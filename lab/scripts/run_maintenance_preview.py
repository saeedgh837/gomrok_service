"""Synthetic loopback preview; external requests and executable launches are denied."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


class PreviewMaintenance:
    scenarios = {"available", "current", "offline", "busy", "manual", "health_error"}

    def __init__(self):
        self._lock = threading.RLock()
        self._counts = {"checks": 0, "app_confirmations": 0,
                        "driver_confirmations": 0, "health_checks": 0}
        self.set_scenario("available")

    def set_scenario(self, name):
        if name not in self.scenarios:
            raise ValueError("Unknown preview scenario")
        with self._lock:
            self.scenario = name
            self._checked = False
            self._app_started = self._driver_started = None
            self._app_waiting = self._driver_waiting = False
            self._busy_released = False

    def audit(self):
        with self._lock:
            return dict(self._counts, scenario=self.scenario)

    def status(self):
        with self._lock:
            available = self._checked and self.scenario != "current"
            app = {"state": "available" if available else "current", "code": "available" if available else "current",
                   "message": "Synthetic official-release offer; no installer can run in this preview.",
                   "current_version": "1.18.3", "checked": self._checked,
                   "can_install": self.scenario != "manual", "cleanup_pending": False,
                   "offer": {"offer_id": "preview-app-offer", "version": "99.0.0"} if available else None,
                   "downloaded": 0, "total": 100}
            driver = {"state": "available" if available else "current", "checked": self._checked,
                      "message": "Synthetic ChromeDriver offer; no executable can change.",
                      "installed_version": "154.0.1.1", "browser_version": "154.0.1.2",
                      "offer": {"offer_id": "preview-driver-offer", "version": "154.0.1.2"} if available else None}
            if self._app_waiting:
                app.update(state="waiting_for_idle", message="Finish active work, then retry the confirmed update.")
            if self._app_started is not None:
                elapsed = time.monotonic() - self._app_started
                app["state"] = "downloading" if elapsed < 4 else "verifying" if elapsed < 6 else "installing" if elapsed < 9 else "current"
                app["downloaded"] = min(100, int(elapsed * 25))
                if elapsed >= 9:
                    app.update(current_version="99.0.0", offer=None, message="Synthetic update completed; no files were changed.")
            if self._driver_waiting:
                driver.update(state="waiting_for_idle", message="Finish active work, then retry the confirmed driver update.")
            if self._driver_started is not None:
                done = time.monotonic() - self._driver_started >= 3
                driver.update(state="current" if done else "installing")
                if done:
                    driver.update(installed_version="154.0.1.2", offer=None)
            return {"ok": True, "app": app, "driver": driver, "checking": False}

    def check(self, force=False):
        with self._lock:
            self._counts["checks"] += 1
            self._checked = True
            return self.status()

    def confirm_app(self, offer_id):
        with self._lock:
            offer = self.status()["app"]["offer"]
            if not offer or offer_id != offer["offer_id"] or self.scenario == "manual":
                return {"ok": False, "errors": ["Review the current synthetic offer."], "app": self.status()["app"]}
            self._counts["app_confirmations"] += 1
            if self.scenario == "busy" and not self._busy_released:
                self._app_waiting = self._busy_released = True
            else:
                self._app_waiting = False
                self._app_started = time.monotonic()
            return {"ok": True, "app": self.status()["app"]}

    def confirm_driver(self, offer_id, *, consent=False):
        with self._lock:
            offer = self.status()["driver"]["offer"]
            if consent is not True or not offer or offer_id != offer["offer_id"]:
                return {"ok": False, "errors": ["Confirm the current synthetic driver offer."], "driver": self.status()["driver"]}
            self._counts["driver_confirmations"] += 1
            if self.scenario == "busy" and not self._busy_released:
                self._driver_waiting = self._busy_released = True
            else:
                self._driver_waiting = False
                self._driver_started = time.monotonic()
            return {"ok": True, "driver": self.status()["driver"]}

    def health(self):
        with self._lock:
            self._counts["health_checks"] += 1
            return {"ok": True, "overall": "error" if self.scenario == "health_error" else "warning",
                    "checked_at": "2026-11-09 12:00:00", "steps": [
                        {"id": "cloud", "name": "Cloud", "status": "ok", "readiness": "unknown",
                         "message": "Synthetic HTTP liveness succeeded; authentication and database readiness were not tested.",
                         "scope": "HTTP liveness only.", "latency_ms": 24},
                        {"id": "telegram", "name": "Telegram transport",
                         "status": "auth_denied" if self.scenario == "health_error" else "reachable", "readiness": "unknown",
                         "message": "Synthetic transport result; no token, secret or message was sent.",
                         "scope": "Unauthenticated transport only; delivery was not tested.", "latency_ms": 38},
                        {"id": "proxy", "name": "Optional proxy", "status": "not_configured", "readiness": "unknown",
                         "message": "The optional synthetic proxy is not configured.", "scope": "Configured proxy transport only."}]}


class PreviewReleaseNotes:
    scenarios = {"unseen", "seen", "new_version", "account_b", "storage_error", "blocked", "editing"}

    def __init__(self, root):
        self.root = root
        self.counter = 0
        self.set_scenario("unseen")

    def set_scenario(self, name):
        if name not in self.scenarios:
            raise ValueError("Unknown release-summary preview scenario")
        import release_notes
        self.counter += 1
        self.scenario = name
        self.user = "preview_other" if name == "account_b" else "preview_user_" + str(self.counter)
        self.session = {"mode": "standalone", "user": self.user}
        if name in {"seen", "new_version"}:
            release_notes.mark_seen(self.root, release_notes.subject_from_session(self.session),
                                    "1.20.0" if name == "seen" else "1.19.0")

    def snapshot(self):
        import release_notes
        return release_notes.snapshot(self.root, self.session, "1.20.0")

    def acknowledge(self, payload):
        import release_notes
        if self.scenario == "storage_error":
            raise release_notes.StorageError("Synthetic acknowledgment storage failure")
        release_notes.acknowledge(self.root, self.session, "1.20.0", payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "lab" / "scripts"))
    from run_isolated_tests import IsolationPolicy, _environment
    scratch = Path(tempfile.mkdtemp(prefix="irica-maintenance-preview-"))
    _environment(scratch)

    class PreviewPolicy(IsolationPolicy):
        def path(self, value, write=False):
            if not write and isinstance(value, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(value)).resolve()
                if candidate in {root / "fonts/Vazirmatn-var.woff2", root / "fonts/Vazirmatn-Bold.woff2"}:
                    return
            return super().path(value, write)

        def audit(self, event, args):
            if event == "subprocess.Popen":
                self.deny("preview-executable-launch")
            return super().audit(event, args)

    policy = PreviewPolicy(scratch)
    policy.install()
    sys.path.insert(0, str(root))
    import app_paths
    app_paths.bootstrap = lambda: []
    import panel
    import panel_auth

    data = Path(app_paths.data_dir())
    owner = {"id": "fixture-owner", "first_name": "Sample", "last_name": "Passenger",
             "national": "0012345679", "birth": "1375/04/29", "mobile": "09121234567", "passport": "PP0000001"}
    device = {"id": "fixture-device", "name": "Sample phone", "owner_id": owner["id"],
              "customer_id": "fixture-customer", "serial1": "356938035640001", "serial2": "356938035640002",
              "stage": "payment", "profile_id": "fixture-profile", "period_id": "fixture-period"}
    profile = {"id": "fixture-profile", "title": "Sample registration", "crm_device_id": device["id"],
               **{key: owner[key] for key in ("national", "birth", "mobile", "passport")},
               "imeis": [device["serial1"], device["serial2"]]}
    documents = {
        "crm_store.json": {"version": 1, "owners": [owner], "devices": [device], "identities": [], "clerks": [],
                           "customers": [{"id": "fixture-customer", "name": "Sample customer", "transactions": []}],
                           "periods": [{"id": "fixture-period", "name": "Preview period", "active": True}],
                           "settings": {"database_mode": "local", "scheduler_enabled": False}},
        "profiles.json": {"active_id": profile["id"], "profiles": [profile]},
        "estelam_store.json": {"devices": []},
        "telegram_config.json": {"mode": "host", "relay_url": "https://relay.example/telegram.php"},
    }
    for name, document in documents.items():
        (data / name).write_text(json.dumps(document), encoding="utf-8")
    fixture = PreviewMaintenance()
    notes_fixture = PreviewReleaseNotes(data)
    panel._MAINTENANCE_SERVICE = fixture
    panel.check_services_health = fixture.health
    panel.check_system_updates = lambda **_: {"ok": True, "notice": "Sign in to review synthetic updates.",
                                               "chrome": {"installed_version": "154.0.1.2"},
                                               "chromedriver": {"installed_version": "154.0.1.1", "matched_version": "154.0.1.2"}}
    tokens = []

    class PreviewHandler(panel.Handler):
        def log_message(self, *_):
            pass

        def _gate(self, route):
            if self.command == "POST" and route == "/login":
                self._json({"ok": False, "errors": ["Use the synthetic preview entry point; real credentials are not accepted."]}, 403)
                return False
            return super()._gate(route)

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            if ctype.startswith("text/html") and isinstance(body, bytes):
                marker = b'<script id="irica-maint-controller">'
                blocker = b""
                if notes_fixture.scenario == "blocked":
                    blocker = (b'<dialog id="preview-existing-dialog" class="irica-maint-dialog" open>'
                               b'<form method="dialog"><p>Synthetic existing dialog</p>'
                               b'<button id="preview-existing-close">Close fixture</button></form></dialog>')
                elif notes_fixture.scenario == "editing":
                    blocker = (b'<label for="preview-editing">Synthetic editing fixture</label>'
                               b'<input id="preview-editing" value="Unsaved fixture">'
                               b'<script>document.getElementById("preview-editing").focus();</script>')
                body = body.replace(marker, blocker + marker, 1)
            return super()._send(code, body, ctype)

        def _preview_session(self, *, redirect=False):
            token = panel_auth.create_session(notes_fixture.user, mode="standalone")
            tokens.append(token)
            self.send_response(302 if redirect else 200)
            self.send_header("Set-Cookie", "irica_session=" + token + "; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Set-Cookie", "irica_csrf=" + panel_auth.session_csrf(token) + "; Path=/; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            if redirect:
                self.send_header("Location", "/index.html")
                body = b""
            else:
                self.send_header("Content-Type", "application/json; charset=utf-8")
                body = json.dumps({"ok": True, "scenario": notes_fixture.scenario}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _release_notes(self, *, acknowledge=False):
            if acknowledge and notes_fixture.scenario == "storage_error":
                self._json({"ok": False, "errors": ["Synthetic acknowledgment storage failure."]}, 503)
                return
            return super()._release_notes(acknowledge=acknowledge)

        def _do_get_body(self):
            route = urlsplit(self.path).path
            if route == "/__preview/start" or (route == "/" and panel_auth.session_user(panel._cookie_token(self.headers)) is None):
                self._preview_session(redirect=True)
                return
            if route == "/__preview/state":
                if self._gate(route):
                    self._json(fixture.audit())
                return
            if route.startswith("/cloud-") or route == "/telegram-health":
                if self._gate(route):
                    self._json({"ok": True, "configured": False, "enabled": False,
                                "state": "disabled", "conflicts": 0, "pending": 0, "ts": ""})
                return
            if route == "/api/maintenance" and fixture.scenario == "offline":
                if self._gate(route):
                    self._json({"ok": False, "errors": ["Synthetic offline state."]}, 503)
                return
            super()._do_get_body()

        def _do_post_body(self):
            route = urlsplit(self.path).path
            if route == "/__preview/scenario":
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"scenario"} or payload["scenario"] not in fixture.scenarios:
                    self._json({"ok": False}, 400)
                    return
                fixture.set_scenario(payload["scenario"])
                self._json({"ok": True, **fixture.audit()})
            elif route == "/__preview/release":
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"scenario"} or payload["scenario"] not in notes_fixture.scenarios:
                    self._json({"ok": False}, 400)
                    return
                notes_fixture.set_scenario(payload["scenario"])
                self._preview_session()
            elif route == "/__preview/expire":
                for token in tokens:
                    panel_auth.destroy_session(token)
                self._json({"ok": True})
            elif route in {"/api/maintenance/check", "/api/update-app", "/api/update-chromedriver", "/api/release-notes/seen"}:
                super()._do_post_body()
            else:
                self._json({"ok": False, "errors": ["Only synthetic maintenance actions are enabled in this preview."]}, 403)

    server = panel.PanelHTTPServer(("127.0.0.1", arguments.port), PreviewHandler)
    print("Synthetic maintenance preview ready; no external requests or executable launches are permitted.", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if policy.denials:
            print("Preview safety denials: " + str(len(policy.denials)), flush=True)


if __name__ == "__main__":
    main()
