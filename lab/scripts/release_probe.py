"""Bounded offline checks of a fresh copied release, never an installed app.

Run the copied bundled interpreter with -I -B and --stage ABS --scratch NEW_ABS.
The stage must be outside the checkout and contain no runtime state. Existing
IsolationPolicy guards are installed before staged imports. The sole stage write
is a fresh, generated launcher probe which is removed in finally. Everything
else is scratch-only. This is a cooperative audit guard, not a native-code or OS
sandbox; only the coordinator's trusted source/runtime/model copies are allowed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from html.parser import HTMLParser
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import stat
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[2]
_HARNESS = None
_DRIVER_BINDING = "_irica_release_probe_driver"
_FORBIDDEN = frozenset({
    ".git", "secrets", "telegram_config.json", "upstream_proxy.json", "relay_config.json",
    "telegram_health.json", "profiles.json", "profiles.json.bak", "profiles.json.lock",
    "inputs.json", "estelam_store.json", "crm_store.json", "panel_users.json",
    "logs.jsonl", "estelam_logs.jsonl", "hamta_logs.jsonl", "site2_notif.json",
    "exitban_errors.jsonl", "cloudflare_public_url.txt", "system_debug.json",
    "outbox.jsonl", "outbox.compacted.jsonl", "sync_state.json", "sync_lease.json",
    "device_token.json",
})
_ROOT_MODULES = (
    "app_paths", "panel", "gomrok", "gomrok2", "estelam_gomrok", "estelam_hamta",
    "estelam_all", "profile_inquiry", "robot_launcher", "system_debug", "exitban",
    "telegram_notify", "browser_window", "browser_visibility", "estelam_shot", "crm",
    "panel_auth", "log_utils", "chromedriver_updater", "app_updater", "maintenance_gate",
    "installer_handoff", "maintenance_service", "maintenance_ui",
)
_DEPENDENCIES = ("selenium", "cv2", "numpy", "PIL", "onnxruntime", "requests", "urllib3",
                 "certifi", "charset_normalizer", "idna")
# Fixed existing Persian labels, represented as code points in this English harness.
_UPDATE_LABEL = "".join(map(chr, (0x0628, 0x0647, 0x200C, 0x0631, 0x0648, 0x0632, 0x0631,
                                 0x0633, 0x0627, 0x0646, 0x06CC, 0x200C, 0x0647, 0x0627)))
_HEALTH_LABEL = "".join(map(chr, (0x0633, 0x0644, 0x0627, 0x0645, 0x062A, 0x0020, 0x0633,
                                 0x0631, 0x0648, 0x06CC, 0x0633, 0x200C, 0x0647, 0x0627)))


class ProbeFailure(RuntimeError):
    """Only fixed, non-sensitive failure codes may escape to the summary."""


def _require(condition, code):
    if not condition:
        raise ProbeFailure(code)


def _within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _harness():
    global _HARNESS
    if _HARNESS is None:
        spec = importlib.util.spec_from_file_location(
            "_irica_release_isolation", Path(__file__).with_name("run_isolated_tests.py"))
        _require(spec is not None and spec.loader is not None, "missing_isolation_harness")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _HARNESS = module
    return _HARNESS


def _local_ancestors(path):
    _require(not str(path).startswith("\\\\") and ".." not in path.parts, "network_or_alias_path_refused")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel.GetDriveTypeW.restype = wintypes.UINT
        _require(kernel.GetDriveTypeW(path.anchor) in (2, 3, 6), "nonlocal_volume_refused")
    ancestor = Path(path.anchor)
    for component in path.parts[1:]:
        ancestor /= component
        try:
            information = ancestor.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise ProbeFailure("unreadable_path_ancestor") from None
        _require(not getattr(information, "st_file_attributes", 0) & 0x400
                 and not stat.S_ISLNK(information.st_mode), "path_reparse_refused")


def validate_paths(stage, scratch):
    stage, scratch = Path(stage), Path(scratch)
    _require(stage.is_absolute() and scratch.is_absolute(), "absolute_paths_required")
    _require(not str(stage).startswith("\\\\") and not str(scratch).startswith("\\\\"),
             "network_filesystem_refused")
    _local_ancestors(stage)
    _local_ancestors(scratch)
    _require(stage.is_dir() and not stage.is_symlink(), "fresh_stage_required")
    _require(stage == stage.resolve() and scratch == scratch.resolve(), "path_alias_refused")
    stage, scratch = stage.resolve(), scratch.resolve()
    _require(not _within(stage, ROOT) and not _within(scratch, ROOT), "checkout_target_refused")
    _require(not _within(stage, scratch) and not _within(scratch, stage), "overlapping_roots")
    _require(not scratch.exists() and scratch.parent.is_dir(), "new_scratch_required")
    _require((stage / "python" / "python.exe").is_file(), "bundled_runtime_required")
    # Inspect names/attributes only: never read a discovered private store.
    count, pending = 0, [stage]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            count += 1
            _require(count <= 200000, "stage_inventory_limit")
            lower = path.name.lower()
            _require(lower not in _FORBIDDEN and not re.fullmatch(r"unins[0-9]{3}\.exe", lower)
                     and not lower.endswith((".log", ".jsonl", ".pt"))
                     and not lower.endswith("-chrome-profile"), "state_or_installed_stage_refused")
            information = path.lstat()
            _require(not stat.S_ISLNK(information.st_mode)
                     and not getattr(information, "st_file_attributes", 0) & 0x400,
                     "stage_reparse_refused")
            if stat.S_ISDIR(information.st_mode):
                pending.append(path)
    return stage, scratch


def configure_environment(scratch):
    _harness()._environment(Path(scratch))
    os.environ.update({"OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"})


def make_policy(stage, scratch, probe):
    stage, scratch, probe = Path(stage).resolve(), Path(scratch).resolve(), Path(probe).resolve()
    _require(not _within(stage, scratch) and not _within(scratch, stage), "overlapping_roots")
    _require(probe.parent == stage and re.fullmatch(r"_irica_release_probe_[A-Za-z0-9_]+\.py",
                                                   probe.name) is not None,
             "invalid_owned_probe")
    base = _harness().IsolationPolicy
    permitted_anchors = {path.anchor.lower() for path in (stage, scratch, ROOT,
                                                         Path(sys.base_prefix).absolute())}

    class ReleasePolicy(base):
        def path(self, value, write=False):
            if not isinstance(value, int) and value is not None:
                lexical = Path(os.fsdecode(value)).absolute()
                if str(lexical).startswith("\\\\") or lexical.anchor.lower() not in permitted_anchors:
                    self.deny("release-network-filesystem")
                candidate = lexical.resolve()
                if _within(candidate, stage):
                    if not write or candidate == probe:
                        return
                    self.deny("release-stage-write")
            return super().path(value, write)

        def trusted_worker(self, command):
            return None

        def audit(self, event, args):
            if (event == "subprocess.Popen" or event.startswith(("os.system", "os.spawn",
                    "os.exec", "os.posix_spawn", "os.startfile"))):
                self.deny("release-child-executable")
            return super().audit(event, args)

    return ReleasePolicy(scratch)


@contextmanager
def staged_app_paths(stage):
    sentinel = object()
    previous = sys.modules.get("app_paths", sentinel)
    spec = importlib.util.spec_from_file_location("app_paths", Path(stage) / "app_paths.py")
    _require(spec is not None and spec.loader is not None, "missing_staged_app_paths")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app_paths"] = module
    try:
        spec.loader.exec_module(module)
        module.bootstrap = lambda: []
        yield module
    finally:
        if previous is sentinel:
            sys.modules.pop("app_paths", None)
        else:
            sys.modules["app_paths"] = previous


def assert_launcher_isolation(stage):
    stage = Path(stage).resolve()
    _require(all(Path(entry or os.getcwd()).resolve() != stage for entry in sys.path),
             "launcher_import_path_was_prepopulated")


class _Controls(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.buttons = {}
        self.valid_container = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "irica-maint-actions" in attrs.get("class", "").split():
            self.valid_container = (attrs.get("lang") == "fa" and attrs.get("dir") == "rtl"
                                    and any(parent[2] for parent in self.stack))
        button = attrs.get("id")
        if button in {"irica-maint-open-updates", "irica-maint-open-health"}:
            _require(button not in self.buttons and tag == "button", "maintenance_control_duplicate")
            self.buttons[button] = ""
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                       "meta", "param", "source", "track", "wbr", "path"}:
            self.stack.append((tag, button, tag == "header"
                               or "irica-maint-host" in attrs.get("class", "").split()))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        for _, button, _host in self.stack:
            if button in self.buttons:
                self.buttons[button] += data


def verify_markup(page):
    parser = _Controls()
    parser.feed(page)
    _require(parser.valid_container, "maintenance_header_rtl_missing")
    _require(parser.buttons == {"irica-maint-open-updates": _UPDATE_LABEL,
                                "irica-maint-open-health": _HEALTH_LABEL},
             "maintenance_persian_controls_missing")


def verify_http(panel, panel_auth):
    """Real Handler/session/CSRF gates; no login, service checks or confirmations."""
    class QuietHandler(panel.Handler):
        def log_message(self, *_):
            pass

    class BoundedServer(panel.PanelHTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            return connection, address

        def handle_error(self, *_):
            # Never emit a request, cookie or traceback to the release report.
            self.release_error = True

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    server = BoundedServer(("127.0.0.1", 0), QuietHandler)
    server.release_error = False
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.02}, name="release-http-fixture", daemon=True)
    token = panel_auth.create_session("release_fixture", ttl_seconds=60)
    csrf = panel_auth.session_csrf(token)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    requests = 0

    def fetch(route, *, authenticated=False, body=None, use_csrf=False):
        nonlocal requests
        headers = {"Accept": "text/html", "Connection": "close"}
        if authenticated:
            headers["Cookie"] = "irica_session=" + token
        if use_csrf:
            headers[panel.CSRF_HEADER] = csrf
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request("http://127.0.0.1:" + str(server.server_port) + route,
                          data=body, headers=headers)
        requests += 1
        try:
            response = opener.open(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(4 * 1024 * 1024 + 1)
            _require(len(raw) <= 4 * 1024 * 1024, "http_response_limit")
            return response.code, raw.decode("utf-8", errors="strict")

    thread.start()
    closed = False
    try:
        for route in ("/site-1", "/site-2", "/crm"):
            status, _ = fetch(route)
            _require(status in (302, 401), "unauthenticated_page_not_gated")
            status, page = fetch(route, authenticated=True)
            _require(status == 200, "authenticated_page_failed")
            verify_markup(page)
        status, _ = fetch("/api/update-app", authenticated=True, body=b"{}")
        _require(status == 403, "csrf_gate_missing")
        status, _ = fetch("/api/update-app", authenticated=True, body=b"{}", use_csrf=True)
        _require(status == 400, "csrf_valid_request_did_not_reach_schema_gate")
    finally:
        panel_auth.destroy_session(token)
        server.shutdown()
        thread.join(timeout=3)
        try:
            _require(not thread.is_alive() and server.begin_maintenance(timeout=3),
                     "http_threads_did_not_drain")
        finally:
            server.server_close()
            closed = True
    _require(not server.release_error, "http_handler_failed")
    return {"pages": 3, "unauthenticated": 3, "csrf": 2,
            "requests": requests, "server_closed": closed}


def summarize(checks, policy, *, thread_errors=(), live_threads=(), failed=False):
    denials = sum(policy.denials.values()) + sum(policy.expected_denials.values()) if policy else 0
    return {"ok": not (failed or denials or thread_errors or live_threads),
            "checks": len(checks), "denials": denials,
            "thread_errors": len(thread_errors), "unclosed_threads": len(live_threads)}


class _DiscardOutput(io.TextIOBase):
    def write(self, value):
        return len(value)

    def flush(self):
        pass


@contextmanager
def onnx_import_system():
    """Supply native Windows client identity only during ONNX dependency import."""
    import platform
    previous_system, previous_release = platform.system, platform.release
    try:
        if sys.platform == "win32" and os.name == "nt":
            native = sys.getwindowsversion()
            major, minor, build = native.platform_version
            _require(native.product_type == 1 and (major, minor) == (10, 0)
                     and build >= 10240, "unsupported_windows_release_metadata")
            # Windows 11 clients retain NT 10.0; their first native build is 22000.
            release = "11" if build >= 22000 else "10"
            platform.system = lambda: "Windows"
            platform.release = lambda: release
        yield
    finally:
        platform.system, platform.release = previous_system, previous_release


def import_dependency(name):
    if name == "onnxruntime":
        with onnx_import_system():
            module = importlib.import_module(name)
        module.disable_telemetry_events()
        return module
    return importlib.import_module(name)


def _exercise(stage, scratch, checks):
    for name in _ROOT_MODULES:
        module = importlib.import_module(name)
        _require(_within(Path(module.__file__).resolve(), stage), "project_import_outside_stage")
    for name in _DEPENDENCIES:
        module = import_dependency(name)
        _require(_within(Path(module.__file__).resolve(), stage / "python"), "dependency_outside_runtime")
    _require("torch" not in sys.modules, "unexpected_torch_backend")
    checks.append("staged_imports")
    panel = sys.modules["panel"]
    _require(panel.ROBOT_PYTHON == sys.executable, "foreign_robot_interpreter")
    command = panel._robot_command(panel.ROBOT_PYTHON, "gomrok.py")
    _require(command[0] == sys.executable and Path(command[1]).name == "robot_launcher.py",
             "robot_launcher_not_used")
    for name in ("TELEGRAM_PATH", "PROFILES_PATH", "INPUTS_PATH", "ESTELAM_PATH", "LOGS_PATH",
                 "SYSTEM_LOG_DIR"):
        _require(_within(Path(getattr(panel, name)).resolve(), scratch), "state_outside_scratch")
    checks.append("launcher_and_state_paths")
    import numpy as np
    solver = sys.modules["gomrok"].load_solver(device="cpu")
    _require(type(solver).__name__ == "OnnxSolver", "gomrok_solver_backend")
    result = solver.read_array(np.zeros((48, 192, 3), np.uint8))
    _require(isinstance(result, str) and len(result) == 5, "gomrok_zero_input_result")
    hamta = sys.modules["estelam_hamta"].load_solver()
    _require(type(hamta).__name__ == "OnnxHamtaSolver", "hamta_solver_backend")
    result = hamta.read_bgr(np.zeros((90, 380, 3), np.uint8))
    _require(set(result) == {"number", "text", "confidence", "snap_distance"}, "hamta_zero_input_result")
    _require("torch" not in sys.modules, "unexpected_torch_backend")
    checks.append("onnx_zero_inputs")
    verify_http(panel, sys.modules["panel_auth"])
    checks.append("http_auth_csrf_and_maintenance")


def cleanup_probe(path, *, owned):
    if owned:
        Path(path).unlink(missing_ok=True)


def run_probe(stage, scratch, *, timeout=120):
    checks, policy, errors, live = [], None, [], []
    failed = False
    phase = "paths"
    probe = None
    probe_owned = False
    watchdog = None
    baseline = set(threading.enumerate())
    saved_thread_hook = threading.excepthook
    saved_unraisable_hook = sys.unraisablehook
    previous_driver = sys.modules.get(_DRIVER_BINDING)
    output = sys.stdout
    try:
        _require(type(timeout) is int and 1 <= timeout <= 180, "invalid_timeout")

        def expired():
            report = summarize(checks, policy, thread_errors=errors, live_threads=["timeout"], failed=True)
            report["phase"] = "timeout"
            output.write(json.dumps(report, sort_keys=True) + "\n")
            output.flush()
            os._exit(2)

        watchdog = threading.Timer(timeout, expired)
        watchdog.name = "release-probe-deadline"
        watchdog.daemon = True
        watchdog.start()
        stage, scratch = validate_paths(stage, scratch)
        _require(sys.flags.isolated and sys.dont_write_bytecode, "isolated_no_bytecode_required")
        _require(Path(sys.executable).resolve() == stage / "python" / "python.exe",
                 "copied_bundled_interpreter_required")
        _require(not any(name in sys.modules for name in _ROOT_MODULES), "product_preloaded")
        scratch.mkdir(mode=0o700)
        configure_environment(scratch)
        probe = stage / ("_irica_release_probe_" + secrets.token_hex(16) + ".py")
        policy = make_policy(stage, scratch, probe)
        policy.current_test = "release_probe"
        policy.install()
        checks.append("isolation_installed")
        baseline = set(threading.enumerate())
        threading.excepthook = lambda _args: errors.append("thread")
        sys.unraisablehook = lambda _args: errors.append("unraisable")
        phase = "launcher"
        with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
            with staged_app_paths(stage):
                assert_launcher_isolation(stage)
                # App paths was preloaded by explicit spec, not import-path repair.
                sys.modules[_DRIVER_BINDING] = sys.modules[__name__]
                content = ("import sys\n"
                           "sys.modules['_irica_release_probe_driver']._exercise(\n"
                           "    sys.modules['_irica_release_probe_driver']._active_stage,\n"
                           "    sys.modules['_irica_release_probe_driver']._active_scratch,\n"
                           "    sys.modules['_irica_release_probe_driver']._active_checks)\n")
                with probe.open("x", encoding="ascii") as stream:
                    probe_owned = True
                    stream.write(content)
                driver = sys.modules[__name__]
                driver._active_stage, driver._active_scratch, driver._active_checks = stage, scratch, checks
                previous_argv = sys.argv
                try:
                    sys.argv = [str(stage / "robot_launcher.py"), str(probe)]
                    runpy.run_path(str(stage / "robot_launcher.py"), run_name="__main__")
                finally:
                    sys.argv = previous_argv
                    for name in ("_active_stage", "_active_scratch", "_active_checks"):
                        driver.__dict__.pop(name, None)
        _require(len(checks) == 5, "incomplete_probe")
        phase = "complete"
    except BaseException:
        failed = True
    finally:
        deadline = time.monotonic() + 3
        for thread in threading.enumerate():
            if thread not in baseline and thread is not watchdog:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        live = [thread for thread in threading.enumerate() if thread not in baseline and thread is not watchdog]
        if watchdog is not None:
            watchdog.cancel()
            watchdog.join(timeout=1)
            if watchdog.is_alive():
                live.append(watchdog)
        if probe is not None:
            try:
                cleanup_probe(probe, owned=probe_owned)
            except OSError:
                failed = True
                phase = "probe_cleanup"
        threading.excepthook = saved_thread_hook
        sys.unraisablehook = saved_unraisable_hook
        if previous_driver is None:
            sys.modules.pop(_DRIVER_BINDING, None)
        else:
            sys.modules[_DRIVER_BINDING] = previous_driver
    summary = summarize(checks, policy, thread_errors=errors, live_threads=live, failed=failed)
    summary["phase"] = phase
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    arguments = parser.parse_args(argv)
    summary = run_probe(arguments.stage, arguments.scratch, timeout=arguments.timeout)
    print(json.dumps(summary, sort_keys=True), flush=True)
    if summary.get("unclosed_threads"):
        os._exit(2)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
