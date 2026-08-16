"""Test-haye zanjire-ye ersal be telegram (bootstrap-e khodkar + fallback + fanout).

Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/tests/test_telegram_chain.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------- panel side
def test_panel_helpers_exist():
    import panel
    for name in ("ensure_telegram_relay_chain", "_startup_telegram_bootstrap",
                 "_port_listening", "_process_running", "_spawn_detached",
                 "_wait_port", "_wait_tunnel_registered", "TELEGRAM_LOCAL_BASE"):
        assert hasattr(panel, name), name
    assert panel.TELEGRAM_LOCAL_BASE == "http://127.0.0.1:8752"
    assert panel.RELAY_PROXY_PORT == 18081
    assert panel.RELAY_API_PORT == 8752


def test_startup_calls_bootstrap_not_bare_test():
    src = open(os.path.join(ROOT, "panel.py"), encoding="utf-8").read()
    assert "target=_startup_telegram_bootstrap" in src
    assert "target=test_telegram_api" not in src


def test_port_listening_detects_real_socket():
    import socket
    import panel
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert panel._port_listening(port) is True
    assert panel._port_listening(port) is False


def test_process_running_true_for_python_false_for_fake():
    import panel
    assert panel._process_running("python.exe") is True
    assert panel._process_running("definitely-not-a-real-process-xyz.exe") is False


def test_health_falls_back_to_local_relay(monkey=None):
    """Agar api_base-e omoomi bishkanad vali relay-e mohalli javab bedahad,
    natije bayad ok bashad va used_base = relay-e mohalli."""
    import panel
    import requests

    calls = []

    class FakeResponse:
        def __init__(self, payload, ok=True, status=200):
            self._payload = payload
            self.ok = ok
            self.status_code = status

        def json(self):
            return self._payload

    def fake_get(url, timeout=None):
        calls.append(url)
        if url.startswith("https://"):
            raise requests.exceptions.ConnectionError("tunnel down")
        return FakeResponse({"ok": True, "result": {"username": "ir_hermes_bot"}})

    old_get = requests.get
    old_health = panel.TELEGRAM_HEALTH_PATH
    tmp = tempfile.mkdtemp()
    panel.TELEGRAM_HEALTH_PATH = os.path.join(tmp, "h.json")
    requests.get = fake_get
    try:
        result = panel.test_telegram_api(attempts=1)
    finally:
        requests.get = old_get
        panel.TELEGRAM_HEALTH_PATH = old_health
    assert result["ok"] is True, result
    assert result["used_base"] == "http://127.0.0.1:8752", result
    assert "رله محلی" in result["message"], result["message"]
    assert any(u.startswith("https://") for u in calls)
    assert any(u.startswith("http://127.0.0.1:8752") for u in calls)


def test_health_reports_failure_when_both_paths_down():
    import panel
    import requests

    def fake_get(url, timeout=None):
        raise requests.exceptions.ConnectionError("all down")

    old_get, old_health = requests.get, panel.TELEGRAM_HEALTH_PATH
    tmp = tempfile.mkdtemp()
    panel.TELEGRAM_HEALTH_PATH = os.path.join(tmp, "h.json")
    requests.get = fake_get
    try:
        result = panel.test_telegram_api(attempts=1)
    finally:
        requests.get = old_get
        panel.TELEGRAM_HEALTH_PATH = old_health
    assert result["ok"] is False
    assert "ناموفق" in result["message"]
    assert result["used_base"] == ""


def test_ensure_chain_is_idempotent_when_up():
    """Agar hame chiz bala bashad, hich process-e jadid start nemishavad."""
    import panel
    old_port, old_proc = panel._port_listening, panel._process_running
    spawned = []
    panel._port_listening = lambda port, host="127.0.0.1", timeout=0.4: True
    panel._process_running = lambda name: True
    old_spawn = panel._spawn_detached
    panel._spawn_detached = lambda *a, **k: spawned.append(a) or None
    try:
        status = panel.ensure_telegram_relay_chain(wait_tunnel=False)
    finally:
        panel._port_listening, panel._process_running = old_port, old_proc
        panel._spawn_detached = old_spawn
    assert status["started"] == []
    assert spawned == []
    assert status["proxy"] and status["relay"] and status["tunnel"]


def test_ensure_chain_starts_missing_parts():
    import panel
    old_port, old_proc, old_spawn = (panel._port_listening, panel._process_running,
                                     panel._spawn_detached)
    old_wait, old_waitt = panel._wait_port, panel._wait_tunnel_registered
    spawned = []
    panel._port_listening = lambda port, host="127.0.0.1", timeout=0.4: False
    panel._process_running = lambda name: False
    panel._spawn_detached = lambda command, cwd=None: spawned.append(command[-1])
    panel._wait_port = lambda port, timeout=20.0: True
    panel._wait_tunnel_registered = lambda log_path, timeout=30: True
    try:
        status = panel.ensure_telegram_relay_chain(wait_tunnel=False)
    finally:
        (panel._port_listening, panel._process_running, panel._spawn_detached,
         panel._wait_port, panel._wait_tunnel_registered) = (
            old_port, old_proc, old_spawn, old_wait, old_waitt)
    assert any("cloudflare_http_proxy.py" in x for x in spawned), spawned
    assert any("telegram_api_relay.py" in x for x in spawned), spawned
    assert "http_proxy:18081" in status["started"]
    assert "telegram_relay:8752" in status["started"]


# ------------------------------------------------------------ notifier side
def test_notifier_has_local_fallback_base():
    import telegram_notify as tg
    cfg = {"api_base": "https://telegram.techbysaeed.ir", "bot_token": "T"}
    bases = tg._bases(cfg)
    assert bases[0] == "https://telegram.techbysaeed.ir"
    assert "http://127.0.0.1:8752" in bases
    assert tg._api("getMe", cfg, bases[1]) == "http://127.0.0.1:8752/botT/getMe"


def test_permanent_chat_error_detection():
    import telegram_notify as tg
    assert tg._is_permanent_chat_error("Bad Request: chat not found")
    assert tg._is_permanent_chat_error(RuntimeError("Forbidden: bot was blocked by the user"))
    assert not tg._is_permanent_chat_error("Connection timed out")
    assert not tg._is_permanent_chat_error("")


def test_fanout_survives_one_broken_chat():
    """chat-e 'not found' nabayad ersal be chat-e salem ra kharab konad."""
    import telegram_notify as tg
    old_post, old_cfg = tg._post_confirmed, tg.ensure_config
    tg.ensure_config = lambda: {"api_base": "http://x", "bot_token": "T",
                                "chat_ids": ["good", "bad"]}

    def fake_post(method, cfg, data, files_factory=None, attempts=4):
        if data["chat_id"] == "bad":
            raise RuntimeError("Bad Request: chat not found")
        return {"ok": True, "result": {"message_id": 11}}

    tg._post_confirmed = fake_post
    try:
        out = tg.send_message("hi")
    finally:
        tg._post_confirmed, tg.ensure_config = old_post, old_cfg
    assert len(out) == 1
    assert out[0]["result"]["message_id"] == 11


def test_fanout_raises_only_when_all_chats_fail():
    import telegram_notify as tg
    old_post, old_cfg = tg._post_confirmed, tg.ensure_config
    tg.ensure_config = lambda: {"api_base": "http://x", "bot_token": "T",
                                "chat_ids": ["a", "b"]}
    tg._post_confirmed = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    try:
        try:
            tg.send_message("hi")
            raised = False
        except RuntimeError as exc:
            raised = "failed for all chats" in str(exc)
    finally:
        tg._post_confirmed, tg.ensure_config = old_post, old_cfg
    assert raised


def test_post_confirmed_rotates_bases_and_skips_retry_on_permanent():
    import telegram_notify as tg

    class FakeResponse:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"{self.status_code} Client Error")

    urls = []

    import requests
    old_post = requests.post

    def fake_post(url, data=None, files=None, timeout=None):
        urls.append(url)
        if url.startswith("https://"):
            raise requests.exceptions.ConnectionError("tunnel down")
        return FakeResponse({"ok": True, "result": {"message_id": 7}})

    requests.post = fake_post
    try:
        cfg = {"api_base": "https://pub", "bot_token": "T"}
        body = tg._post_confirmed("sendMessage", cfg, {"chat_id": "1", "text": "x"})
    finally:
        requests.post = old_post
    assert body["result"]["message_id"] == 7
    assert urls[0].startswith("https://pub")
    assert urls[1].startswith("http://127.0.0.1:8752")

    # khata-ye permanent -> hich retry
    urls.clear()

    def fake_post_bad(url, data=None, files=None, timeout=None):
        urls.append(url)
        return FakeResponse({"ok": False, "description": "Bad Request: chat not found"}, 400)

    requests.post = fake_post_bad
    try:
        try:
            tg._post_confirmed("sendMessage", {"api_base": "https://pub", "bot_token": "T"},
                               {"chat_id": "1", "text": "x"})
            raised = False
        except RuntimeError as exc:
            raised = "chat not found" in str(exc)
    finally:
        requests.post = old_post
    assert raised
    assert len(urls) == 1, urls


def test_watchdog_bat_uses_literal_findstr():
    """PITFALL: findstr /R \"PAT WITH SPACE\" = do OR-pattern -> hamishe match."""
    bat = open(os.path.join(ROOT, "start_telegram_relay_chain.bat"), encoding="utf-8").read()
    assert '/C:":18081 .*LISTENING"' in bat
    assert '/C:":8752 .*LISTENING"' in bat
    assert 'findstr /R ":18081' not in bat
    assert 'findstr /R ":8752' not in bat
    assert 'tasklist /FI "IMAGENAME eq cloudflared.exe"' in bat


def test_http_proxy_tunnel_stays_blocking():
    """PITFALL: setblocking(False) + sendall roo-ye CONNECT tunnel baraye
    upload-e multipart BlockingIOError midad -> 502. bayad blocking bemanad."""
    lines = open(os.path.join(ROOT, "cloudflare_http_proxy.py"),
                 encoding="utf-8").read().splitlines()
    code = [l for l in lines if not l.strip().startswith("#")]
    assert not any("setblocking(False)" in l for l in code), \
        "setblocking(False) hanooz dar kod-e ejraei hast"
    joined = "\n".join(code)
    assert "self.request.settimeout(None)" in joined
    assert "upstream.settimeout(None)" in joined


def test_relay_retries_more_for_large_bodies():
    src = open(os.path.join(ROOT, "telegram_api_relay.py"), encoding="utf-8").read()
    assert "max_attempts = 8 if (body and len(body) > 4096) else 3" in src
    assert "for _attempt in range(max_attempts):" in src


def test_notifier_upload_timeout_is_generous():
    src = open(os.path.join(ROOT, "telegram_notify.py"), encoding="utf-8").read()
    assert "timeout=150 if files else 20" in src


def _run_all():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'OK' if not failures else 'FAILED'} — {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
