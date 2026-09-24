"""Telegram delivery chain: panel health wire-up, the notifier fanout/retry
contract, and the relay/proxy scripts that are still packaged.

Run directly:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/tests/test_telegram_chain.py
or through the suite: python lab/scripts/run_panel_tests.py test_telegram_chain
(v1.9.1: the module is registered there; the AsUnittest adapter below is what
makes its function-style tests visible to unittest discovery.)

Scope note: until 2026-09-10 the panel bootstrapped the local relay chain
(proxy 18081 + relay 8752 + cloudflared named tunnel) and telegram_notify fell
back to http://127.0.0.1:8752 whenever the configured public base was down. That
wiring was retired -- panel now only health-checks the configured base, and the
watchdog no longer starts a tunnel. The tests that pinned the retired wiring were
removed with it, because they kept passing on a shape the code no longer had once
this module stopped being collected; what remains pins behavior that is still
live. Deadline budgeting and single-chat targeting are covered by
test_page_probe_and_telegram_timeout and test_v1_5_handoff.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------- panel side
def test_panel_health_helpers_exist():
    import panel
    for name in ("test_telegram_api", "_write_telegram_health",
                 "_startup_telegram_bootstrap", "load_telegram_health",
                 "load_telegram_config", "TELEGRAM_HEALTH_PATH"):
        assert hasattr(panel, name), name


def test_startup_calls_bootstrap_not_bare_test():
    src = open(os.path.join(ROOT, "panel.py"), encoding="utf-8").read()
    assert "target=_startup_telegram_bootstrap" in src
    assert "target=test_telegram_api" not in src


def test_panel_health_uses_configured_base_only():
    """The public base from telegram_config.json is the ONLY endpoint probed.

    PITFALL this replaces: the retired relay-era code also tried
    http://127.0.0.1:8752 on failure, so a dead public base could still report
    ok = True from a local process. Reintroducing that fallback would make the
    operator's connectivity report lie, so the probe must stay single-target.
    """
    import panel
    import requests

    seen = []

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"username": "ir_hermes_bot"}}

    def fake_get(url, timeout=None):
        seen.append(url)
        return FakeResponse()

    old_get = requests.get
    old_cfg = panel.load_telegram_config
    old_health = panel.TELEGRAM_HEALTH_PATH
    panel.TELEGRAM_HEALTH_PATH = os.path.join(tempfile.mkdtemp(), "health.json")
    panel.load_telegram_config = lambda: {"api_base": "https://tg.example",
                                          "bot_token": "T"}
    requests.get = fake_get
    try:
        result = panel.test_telegram_api(attempts=1)
    finally:
        requests.get = old_get
        panel.load_telegram_config = old_cfg
        panel.TELEGRAM_HEALTH_PATH = old_health
    assert result["ok"] is True, result
    assert result["used_base"] == "https://tg.example", result
    assert seen == ["https://tg.example/botT/getMe"], seen


def test_panel_health_reports_failure_when_base_is_down():
    import panel
    import requests

    def fake_get(url, timeout=None):
        raise requests.exceptions.ConnectionError("all down")

    old_get = requests.get
    old_cfg = panel.load_telegram_config
    old_health = panel.TELEGRAM_HEALTH_PATH
    panel.TELEGRAM_HEALTH_PATH = os.path.join(tempfile.mkdtemp(), "health.json")
    panel.load_telegram_config = lambda: {"api_base": "https://tg.example",
                                          "bot_token": "T"}
    requests.get = fake_get
    try:
        result = panel.test_telegram_api(attempts=1)
    finally:
        requests.get = old_get
        panel.load_telegram_config = old_cfg
        panel.TELEGRAM_HEALTH_PATH = old_health
    assert result["ok"] is False, result
    assert result["used_base"] == ""
    assert "ناموفق" in result["message"], result["message"]


def test_panel_health_requires_a_complete_config():
    import panel

    old_cfg = panel.load_telegram_config
    old_health = panel.TELEGRAM_HEALTH_PATH
    panel.TELEGRAM_HEALTH_PATH = os.path.join(tempfile.mkdtemp(), "health.json")
    panel.load_telegram_config = lambda: {"api_base": "", "bot_token": ""}
    try:
        result = panel.test_telegram_api(attempts=1)
    finally:
        panel.load_telegram_config = old_cfg
        panel.TELEGRAM_HEALTH_PATH = old_health
    assert result["ok"] is False, result
    assert "کامل نیست" in result["message"], result["message"]


# ------------------------------------------------------------ notifier side
def test_notifier_bases_is_only_the_configured_base():
    """No endpoint is invented: an empty api_base means no base at all.

    Known gap (follow-up, needs a source change and a re-release): a
    whitespace-only api_base is NOT normalized away -- ``_bases`` returns
    ``["   "]`` and every send burns its attempts on a garbage URL. Only the
    empty case is asserted here, because that is the contract today.
    """
    import telegram_notify as tg
    cfg = {"api_base": "https://tg.example/", "bot_token": "T"}
    assert tg._bases(cfg) == ["https://tg.example"]      # trailing slash trimmed
    assert tg._bases({}) == []
    assert tg._api("getMe", cfg, tg._bases(cfg)[0]) == "https://tg.example/botT/getMe"


def test_permanent_chat_error_detection():
    import telegram_notify as tg
    assert tg._is_permanent_chat_error("Bad Request: chat not found")
    assert tg._is_permanent_chat_error(RuntimeError("Forbidden: bot was blocked by the user"))
    assert not tg._is_permanent_chat_error("Connection timed out")
    assert not tg._is_permanent_chat_error("")


def test_fanout_survives_one_broken_chat():
    """A 'chat not found' destination must not break delivery to a healthy one."""
    import telegram_notify as tg
    old_post, old_cfg = tg._post_confirmed, tg.ensure_config
    tg.ensure_config = lambda: {"api_base": "http://x", "bot_token": "T",
                                "chat_ids": ["good", "bad"]}

    def fake_post(method, cfg, data, files_factory=None, attempts=4, deadline=None):
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


def test_post_confirmed_does_not_retry_a_permanent_chat_error():
    """Retrying a dead / blocked chat only wastes the send budget."""
    import telegram_notify as tg
    import requests

    class FakeResponse:
        status_code = 400

        def json(self):
            return {"ok": False, "description": "Bad Request: chat not found"}

        def raise_for_status(self):
            raise RuntimeError("400 Client Error")

    urls = []

    def fake_post(url, data=None, files=None, timeout=None):
        urls.append(url)
        return FakeResponse()

    old_post = requests.post
    requests.post = fake_post
    try:
        try:
            tg._post_confirmed("sendMessage", {"api_base": "https://tg",
                                               "bot_token": "T"},
                               {"chat_id": "1", "text": "x"})
            raised = False
        except RuntimeError as exc:
            raised = "chat not found" in str(exc)
    finally:
        requests.post = old_post
    assert raised
    assert len(urls) == 1, urls


def test_post_confirmed_retries_the_configured_base():
    import telegram_notify as tg
    import requests

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

        def raise_for_status(self):
            return None

    urls = []

    def fake_post(url, data=None, files=None, timeout=None):
        urls.append(url)
        if len(urls) == 1:
            raise requests.exceptions.ConnectionError("tunnel down")
        return FakeResponse()

    old_post = requests.post
    requests.post = fake_post
    try:
        body = tg._post_confirmed("sendMessage",
                                  {"api_base": "https://tg.example", "bot_token": "T"},
                                  {"chat_id": "1", "text": "x"}, attempts=2)
    finally:
        requests.post = old_post
    assert body["result"]["message_id"] == 7
    assert set(urls) == {"https://tg.example/botT/sendMessage"}, urls


def test_notifier_upload_timeout_is_generous():
    src = open(os.path.join(ROOT, "telegram_notify.py"), encoding="utf-8").read()
    assert "request_timeout = 150 if files_factory else 20" in src
    # a single request must never outlive the remaining budget
    assert "request_timeout = min(request_timeout, remaining)" in src


# ------------------------------------------- relay / proxy scripts (packaged)
def test_watchdog_bat_uses_literal_findstr():
    """PITFALL: findstr /R "PAT WITH SPACE" = do OR-pattern -> hamishe match."""
    bat = open(os.path.join(ROOT, "start_telegram_relay_chain.bat"),
               encoding="utf-8").read()
    assert '/C:":18081 .*LISTENING"' in bat
    assert '/C:":8752 .*LISTENING"' in bat
    assert 'findstr /R ":18081' not in bat
    assert 'findstr /R ":8752' not in bat
    assert "cloudflare_http_proxy.py" in bat
    assert "telegram_api_relay.py" in bat
    # the cloudflared named tunnel was retired on 2026-09-10; the watchdog must
    # not try to start it again.
    assert "cloudflared.exe" not in bat


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


class AsUnittest(unittest.TestCase):
    """Expose the function-style tests above to unittest discovery.

    Every test in this module is a plain function that ``_run_all()`` calls in a
    loop, and ``loadTestsFromName`` collects nothing from that shape. The module
    is listed in lab/scripts/run_panel_tests.py, so before this class existed it
    contributed zero coverage while the suite still reported green. Each
    ``test_*`` function is attached below as a real method.
    """


def _attach_as_test(name, fn):
    def method(self):
        fn()

    method.__name__ = name
    method.__doc__ = fn.__doc__
    setattr(AsUnittest, name, method)


for _test_name in sorted(n for n in list(globals()) if n.startswith("test_")):
    _attach_as_test(_test_name, globals()[_test_name])


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
