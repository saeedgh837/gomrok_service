"""Test-haye mohafezat-e token-e bot + self-contained boodan-e file-haye telegram.

Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" lab/tests/test_token_privacy.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

FAKE_TOKEN = "1234567890:AAAAtestTOKENshouldNeverLeak_xyz"


def _with_temp_config(fn):
    import panel
    old = panel.TELEGRAM_PATH
    tmp = tempfile.mkdtemp()
    panel.TELEGRAM_PATH = os.path.join(tmp, "telegram_config.json")
    with open(panel.TELEGRAM_PATH, "w", encoding="utf-8") as handle:
        json.dump({"api_base": "https://telegram.example.ir",
                   "bot_token": FAKE_TOKEN,
                   "chat_ids": ["111", "222"]}, handle)
    try:
        return fn(panel)
    finally:
        panel.TELEGRAM_PATH = old


def test_public_config_hides_token():
    def check(panel):
        pub = panel.public_telegram_config()
        assert "bot_token" not in pub, pub
        assert pub["token_set"] is True
        assert pub["api_base"] == "https://telegram.example.ir"
        assert pub["chat_ids"] == ["111", "222"]
        return True
    assert _with_temp_config(check)


def test_public_config_flags_missing_token():
    import panel
    old = panel.TELEGRAM_PATH
    tmp = tempfile.mkdtemp()
    panel.TELEGRAM_PATH = os.path.join(tmp, "t.json")
    with open(panel.TELEGRAM_PATH, "w", encoding="utf-8") as handle:
        json.dump({"api_base": "https://x", "bot_token": "", "chat_ids": []}, handle)
    try:
        assert panel.public_telegram_config()["token_set"] is False
    finally:
        panel.TELEGRAM_PATH = old


def test_empty_token_on_save_keeps_existing():
    def check(panel):
        out = panel.save_telegram_config({"api_base": "https://telegram.example.ir",
                                          "bot_token": "",
                                          "chat_ids": "999"})
        assert "bot_token" not in out
        assert out["token_set"] is True
        stored = json.load(open(panel.TELEGRAM_PATH, encoding="utf-8"))
        assert stored["bot_token"] == FAKE_TOKEN, "token pak shod!"
        assert stored["chat_ids"] == ["999"]
        return True
    assert _with_temp_config(check)


def test_new_token_on_save_replaces():
    def check(panel):
        panel.save_telegram_config({"api_base": "https://telegram.example.ir",
                                    "bot_token": "999:NEWTOKEN",
                                    "chat_ids": "111"})
        stored = json.load(open(panel.TELEGRAM_PATH, encoding="utf-8"))
        assert stored["bot_token"] == "999:NEWTOKEN"
        return True
    assert _with_temp_config(check)


def test_rendered_page_never_contains_token():
    def check(panel):
        html = panel.render_page().decode("utf-8")
        assert FAKE_TOKEN not in html, "token dar HTML leak shod!"
        assert "token_set" in html
        assert 'id="tg_token" type="password"' in html
        assert "-webkit-text-security:disc" in html
        assert "user-select:none" in html
        return True
    assert _with_temp_config(check)


def test_token_input_has_no_value_attribute():
    """input-e token nabayad value-ye pish-far dashte bashad (copy-nashodani)."""
    src = open(os.path.join(ROOT, "panel.py"), encoding="utf-8").read()
    assert "TELEGRAM.bot_token" not in src, "UI hanooz token-ra mikhanad"
    assert 'id=\\"tg_token\\" type=\\"password\\"' in src or \
           'id="tg_token" type="password"' in src


def test_telegram_files_are_inside_project():
    """Hame-ye file-haye lazem baraye ersal-e telegram bayad dar mas'ir-e proje bashand."""
    needed = ("telegram_notify.py", "telegram_api_relay.py", "cloudflare_http_proxy.py",
              "cloudflare_named_tunnel.yml", "telegram_config.json", "upstream_proxy.json",
              "start_telegram_relay_chain.bat", ".tools/cloudflared.exe",
              "secrets/cert.pem")
    for rel in needed:
        assert os.path.exists(os.path.join(ROOT, rel)), rel


def test_example_configs_exist_for_secrets():
    """Har file-e secret bayad hamta-ye .example dashte bashad (baraye git)."""
    for rel in ("telegram_config.example.json", "upstream_proxy.example.json",
                "relay_config.example.json"):
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), rel
        data = json.load(open(path, encoding="utf-8"))
        assert data, rel


def test_gitignore_covers_every_secret():
    """.gitignore bayad hame-ye file-haye hassas ra pooshesh dahad.
    PITFALL: .gitignore comment-e HAM-KHAT nadarad ('x.json  # note' kar nemikonad)."""
    path = os.path.join(ROOT, ".gitignore")
    assert os.path.isfile(path), ".gitignore nist"
    lines = [l.strip() for l in open(path, encoding="utf-8").read().splitlines()]
    patterns = {l for l in lines if l and not l.startswith("#")}
    for needed in ("telegram_config.json", "upstream_proxy.json", "relay_config.json",
                   "secrets/", "telegram_health.json", ".tools/", "*.pt",
                   "solver/checkpoints/", ".exitban-chrome-profile/"):
        assert needed in patterns, f"'{needed}' dar .gitignore nist"
    for pattern in patterns:
        assert "#" not in pattern, f"comment-e ham-khat dar .gitignore: {pattern}"
    # example-ha NABAYAD ignore shavand
    for keep in ("telegram_config.example.json", "upstream_proxy.example.json",
                 "relay_config.example.json"):
        assert keep not in patterns, keep


def test_upstream_proxy_read_from_project_file():
    import importlib
    import cloudflare_http_proxy as proxy
    importlib.reload(proxy)
    assert os.path.basename(proxy.LOCAL_UPSTREAM_CONFIG) == "upstream_proxy.json"
    host, port, user, password = proxy.load_upstream_config()
    local = json.load(open(os.path.join(ROOT, "upstream_proxy.json"), encoding="utf-8"))
    assert host == local["host"] and port == int(local["port"])
    assert user == local["username"] and password == local["password"]


def test_tunnel_config_credentials_inside_project():
    text = open(os.path.join(ROOT, "cloudflare_named_tunnel.yml"), encoding="utf-8").read()
    assert "irica\\secrets\\" in text or "irica/secrets/" in text, text
    assert ".cloudflared" not in text
    creds_line = [l for l in text.splitlines() if l.startswith("credentials-file:")][0]
    creds_path = creds_line.split(":", 1)[1].strip()
    assert os.path.isfile(creds_path), creds_path


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
