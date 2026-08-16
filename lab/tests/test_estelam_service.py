"""Test-haye servis-e estelam (panel + robot). Ejra:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" -m pytest lab/tests/test_estelam_service.py -q
ya bedun-e pytest: python lab/tests/test_estelam_service.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


def _fresh_panel(tmp_store):
    import importlib
    import panel
    importlib.reload(panel)
    panel.ESTELAM_PATH = tmp_store
    return panel


def test_person_validator():
    import panel
    bad = panel.validate_estelam_person({"national": "123", "birth": "x", "mobile": "12"})
    assert len(bad) == 3
    ok = panel.validate_estelam_person(
        {"national": "0012345678", "birth": "1375/04/29", "mobile": "09121234567"})
    assert ok == []
    assert panel.validate_estelam_person(
        {"national": "001234567a", "birth": "1375/04/29", "mobile": "09121234567"})
    assert panel.validate_estelam_person(
        {"national": "0012345678", "birth": "1375/4/29", "mobile": "09121234567"})
    assert panel.validate_estelam_person(
        {"national": "0012345678", "birth": "1375/04/29", "mobile": "08121234567"})


def test_device_validator():
    import panel
    # nam-e dastgah EJBARI shode (Aug 2026)
    assert panel.validate_estelam_device({"serial1": "", "serial2": ""})
    assert panel.validate_estelam_device({"name": "x", "serial1": "123", "serial2": ""})
    assert panel.validate_estelam_device(
        {"name": "x", "serial1": "356938035643809", "serial2": "12345"})
    errs = panel.validate_estelam_device({"serial1": "356938035643809"})
    assert any("نام" in e for e in errs), errs
    assert panel.validate_estelam_device({"name": "y" * 41, "serial1": "356938035643809"})
    assert panel.validate_estelam_device(
        {"name": "آیفون", "serial1": "356938035643809", "serial2": ""}) == []
    assert panel.validate_estelam_device(
        {"name": "آیفون", "serial1": "356938035643809",
         "serial2": "356938035643810"}) == []


def test_store_crud_and_stale_reset():
    with tempfile.TemporaryDirectory() as d:
        store = os.path.join(d, "estelam_store.json")
        import panel
        old = panel.ESTELAM_PATH
        panel.ESTELAM_PATH = store
        try:
            panel.estelam_save_person({"national": "0012345678",
                                       "birth": "1375/04/29",
                                       "mobile": "09121234567"})
            rec = panel.estelam_add_device({"serial1": "356938035643809",
                                            "serial2": "356938035643810"})
            data = panel.estelam_load()
            assert data["person"]["national"] == "0012345678"
            assert len(data["devices"]) == 1
            assert data["devices"][0]["status"] == "pending"
            # zaman-e estelam ta ghabl az natije khali ast
            assert data["devices"][0]["gomrok_at"] == ""
            assert data["devices"][0]["hamta_at"] == ""

            panel.estelam_set_device(rec["id"], status="healthy",
                                     result="یافت نشد", messages=["x"])
            data = panel.estelam_load()
            assert data["devices"][0]["status"] == "healthy"
            assert data["devices"][0]["updated"]
            # residan be vaziat-e nahayi zaman-e estelam-e gomrok ra mohr mikonad
            assert data["devices"][0]["gomrok_at"]
            assert data["devices"][0]["hamta_at"] == ""

            panel.estelam_set_device(rec["id"], status="running")
            data = panel.estelam_load()
            data["run_pid"] = 999999999      # pid-e mordeh
            panel.estelam_save(data)
            panel.estelam_reset_pending_stale()
            assert panel.estelam_load()["devices"][0]["status"] == "pending"

            panel.estelam_delete_device(rec["id"])
            assert panel.estelam_load()["devices"] == []
        finally:
            panel.ESTELAM_PATH = old


def test_start_blocks_on_missing_person():
    with tempfile.TemporaryDirectory() as d:
        store = os.path.join(d, "estelam_store.json")
        import panel
        old = panel.ESTELAM_PATH
        panel.ESTELAM_PATH = store
        try:
            panel.estelam_add_device({"serial1": "356938035643809"})
            res = panel.estelam_start()
            assert res["ok"] is False
            assert "اطلاعات شخص برای استعلام را پر کنید" in res["errors"][0]

            panel.estelam_save_person({"national": "0012345678",
                                       "birth": "1375/04/29",
                                       "mobile": "09121234567"})
            data = panel.estelam_load()
            data["devices"] = []
            panel.estelam_save(data)
            res = panel.estelam_start()
            assert res["ok"] is False
            assert "دستگاهی" in res["errors"][0]
        finally:
            panel.ESTELAM_PATH = old


def test_estelam_page_renders_and_js_valid():
    with tempfile.TemporaryDirectory() as d:
        store = os.path.join(d, "estelam_store.json")
        import panel
        old = panel.ESTELAM_PATH
        panel.ESTELAM_PATH = store
        try:
            html = panel.render_estelam_page().decode("utf-8")
        finally:
            panel.ESTELAM_PATH = old
    assert "__ESTELAM_JSON__" not in html
    assert "__STATUS_JSON__" not in html
    for needle in ("افزودن دستگاه", "افزودن عمده", "ذخیره و استعلام",
                   "ذخیره و بعدی", "پایان و برو به استعلام", "اطلاعات شخص",
                   "مدیریت اجرای استعلام", "توقف و بستن مرورگر",
                   "راه‌اندازی مجدد", "لاگ اجراهای اخیر", "log-box", "logbox"):
        assert needle in html, needle
    js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(js)
        js_path = f.name
    try:
        proc = subprocess.run(["node", "--check", js_path],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    except FileNotFoundError:
        pass
    finally:
        os.unlink(js_path)


def test_estelam_logs_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        import panel
        old = panel.ESTELAM_LOGS_PATH
        panel.ESTELAM_LOGS_PATH = os.path.join(d, "logs.jsonl")
        try:
            assert panel.load_estelam_logs() == []
            panel.estelam_log("healthy", "salem", step="استعلام",
                              serial="356938035643809", device_id="a")
            panel.estelam_log("problem", "eshkal", step="استعلام", serial="111")
            logs = panel.load_estelam_logs()
            assert len(logs) == 2
            assert logs[0]["status"] == "problem"      # jadidtar avval
            assert logs[1]["serial"] == "356938035643809"
            assert logs[1]["ts"] and logs[1]["step"] == "استعلام"
            panel.clear_estelam_logs()
            assert panel.load_estelam_logs() == []
        finally:
            panel.ESTELAM_LOGS_PATH = old


def test_runtime_and_stop_lifecycle():
    """runtime/stop ba yek process-e vaghe'i-ye zende (bedun-e robot)."""
    import subprocess as sp
    import time as t
    with tempfile.TemporaryDirectory() as d:
        import panel
        old_store, old_logs = panel.ESTELAM_PATH, panel.ESTELAM_LOGS_PATH
        panel.ESTELAM_PATH = os.path.join(d, "s.json")
        panel.ESTELAM_LOGS_PATH = os.path.join(d, "l.jsonl")
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            panel.estelam_save_person({"national": "0012345678",
                                       "birth": "1375/04/29",
                                       "mobile": "09121234567"})
            rec = panel.estelam_add_device({"serial1": "356938035643809"})
            data = panel.estelam_load()
            data["run_pid"] = proc.pid
            data["run_started"] = "2026-01-01 00:00:00"
            panel.estelam_save(data)
            panel.estelam_set_device(rec["id"], status="running")

            rt = panel.estelam_runtime()
            assert rt["running"] is True and rt["pid"] == proc.pid
            assert rt["counts"] == {"running": 1}
            assert panel.estelam_is_running() is True

            blocked = panel.estelam_start()
            assert blocked["ok"] is False
            assert "در حال انجام" in blocked["errors"][0]

            res = panel.estelam_stop()
            assert res["stopped"] is True
            assert res["runtime"]["running"] is False
            t.sleep(0.5)
            assert proc.poll() is not None, "process koshte nashod"
            after = panel.estelam_load()
            assert after["devices"][0]["status"] == "pending"
            assert "توقف دستی" in after["devices"][0]["result"]
            assert any("متوقف" in x["message"] for x in panel.load_estelam_logs())
        finally:
            try:
                proc.kill()
            except OSError:
                pass
            panel.ESTELAM_PATH, panel.ESTELAM_LOGS_PATH = old_store, old_logs


def test_robot_writes_estelam_logs():
    """_elog-e robot bayad be log-e panel beneveshte shavad."""
    with tempfile.TemporaryDirectory() as d:
        import estelam_gomrok as E
        import panel
        old = panel.ESTELAM_LOGS_PATH
        panel.ESTELAM_LOGS_PATH = os.path.join(d, "l.jsonl")
        try:
            E._elog("healthy", "dastgah salem", serial="356938035643809",
                    device_id="a")
            logs = panel.load_estelam_logs()
            assert len(logs) == 1
            assert logs[0]["serial"] == "356938035643809"
            assert logs[0]["status"] == "healthy"
            assert logs[0]["step"] == "استعلام"
        finally:
            panel.ESTELAM_LOGS_PATH = old


def test_robot_healthy_classifier():
    import estelam_gomrok as E
    assert E.is_healthy_result(
        ["شناسه 356938035643809 دریافت شده از گمرک ورودی یافت نشد."])
    assert not E.is_healthy_result(
        ["این شناسه قبلا در شبکه داخلی کشور ثبت و فعال شده است"])
    assert not E.is_healthy_result([])
    assert not E.is_healthy_result(["خطا در استعلام IMEI از سامانه همتا"])


def test_robot_reuses_gomrok_retry_logic():
    import estelam_gomrok as E
    import gomrok as G
    assert E.G is G
    assert G.is_imei_retryable_error(["خطا در استعلام IMEI از سامانه همتا"])
    assert G.is_imei_retryable_error(["خطای دریافتی : خطا در تبدیل اطلاعات"])
    assert G.is_imei_retryable_error(["کد امنیتی صحیح نمی باشد"])
    # javab-e KHUB nabayad retryable bashad (fatal marker: یافت نشد)
    assert not G.is_imei_retryable_error(
        ["شناسه 356938035643809 دریافت شده از گمرک ورودی یافت نشد."])


def test_robot_serials_and_store_reader():
    import estelam_gomrok as E
    assert E._serials({"serial1": " 111 ", "serial2": ""}) == ["111"]
    assert E._serials({"serial1": "111", "serial2": "222"}) == ["111", "222"]
    assert E._serials({}) == []
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"person": {"national": "1"}, "devices": [{"id": "a"}]}, f)
        old = E._STORE_PATH
        E._STORE_PATH = p
        try:
            data = E._store_read()
            assert data["person"]["national"] == "1"
            assert data["devices"][0]["id"] == "a"
        finally:
            E._STORE_PATH = old


def test_gomrok_untouched_api_surface():
    """estelam bayad az gomrok.py estefade konad, na copy — pas API-e lazem hast."""
    import gomrok as G
    for name in ("WP_BIRTH", "WP_NATIONAL", "WP_CAPTCHA", "WP_CONTINUE",
                 "WP_CONFIRM", "WP_HAMTA_CONTINUE", "find", "fill_imeis",
                 "verify_imeis", "type_field", "set_field_js", "type_and_verify",
                 "ensure_checked", "cleanup_extra_empty_imei_rows",
                 "ensure_page_ready", "retry_imei_captcha_in_place",
                 "is_imei_retryable_error", "log_event", "install_profile_banner"):
        assert hasattr(G, name), name


class _FakeDriver:
    """DOM-e mock: sequence-e payam-haye khata + hozur/adam-hozur-e dokme-ha."""

    def __init__(self, error_sequence, advance_after=None):
        self.error_sequence = list(error_sequence)
        self.advance_after = advance_after   # ba'd az N click -> hamtaStep
        self.clicks = 0
        self.refreshes = 0
        self.current_url = "https://epl.irica.gov.ir/ImeiRegister?1"

    def _errors_now(self):
        if not self.error_sequence:
            return []
        return self.error_sequence[0]

    def _advanced(self):
        return (self.advance_after is not None and
                self.clicks >= self.advance_after)

    def execute_script(self, script, *args):
        if "scrollIntoView" in script:
            return None
        if "feedBack_container_error" in script and "textContent=''" in script:
            if self.error_sequence:
                self.error_sequence.pop(0)
            return None
        if "feedBack_container_error" in script and "msgs" in script:
            return list(self._errors_now())
        if "wicket-ajax-indicator" in script:
            return False
        if "querySelector" in script:
            return bool(self._advanced()) or bool(self._errors_now())
        return None

    def find_elements(self, by, css):
        if "hamtaStepContinue" in css:
            return ["hamta"] if self._advanced() else []
        if "continue1" in css:
            return [] if self._advanced() else ["btn"]
        if "captchaImage" in css:
            return ["cap"]
        return []

    def find_element(self, by, css):
        found = self.find_elements(by, css)
        if not found:
            raise RuntimeError(f"not found: {css}")
        return found[0]

    def refresh(self):
        self.refreshes += 1

    def save_screenshot(self, path):
        return True

    @property
    def page_source(self):
        return "<html></html>"

    def quit(self):
        return None


def _patch_robot(monkey_targets):
    """Stub-haye lazem baraye ejra-ye inquire_device bedun-e Chrome/model."""
    import captcha_collector as cc
    import estelam_gomrok as E
    import gomrok as G
    saved = {}

    def save(module, name, value):
        saved[(module, name)] = getattr(module, name)
        setattr(module, name, value)

    save(G, "ensure_page_ready", lambda *a, **k: True)
    save(G, "set_field_js", lambda *a, **k: None)
    save(G, "type_field", lambda *a, **k: None)
    save(G, "fill_imeis", lambda *a, **k: None)
    save(G, "verify_imeis", lambda d, s: (True, list(s)))
    save(G, "ensure_checked", lambda *a, **k: None)
    save(G, "cleanup_extra_empty_imei_rows", lambda *a, **k: None)
    save(G, "type_and_verify", lambda *a, **k: True)
    save(G, "find", lambda d, wp: "el")
    save(G, "log_event", lambda *a, **k: None)
    save(cc, "click_refresh", lambda *a, **k: True)
    save(cc, "prepare_captcha_for_shot", lambda *a, **k: True)
    save(cc, "crop_captcha_with_padding", lambda *a, **k: "img")
    save(cc, "snapshot_captcha", lambda *a, **k: "ref")
    save(cc, "fire_refresh_events", lambda d, btn: setattr(d, "clicks", d.clicks + 1))
    save(cc, "wait_for_new_captcha", lambda *a, **k: True)
    save(cc, "is_captcha_empty", lambda *a, **k: False)
    save(E, "_telegram", lambda text: monkey_targets.setdefault("telegram", []).append(text))
    save(E, "SUBMIT_DEADLINE_SEC", 3)
    return saved


def _unpatch(saved):
    for (module, name), value in saved.items():
        setattr(module, name, value)


class _FakeSolver:
    def read_pil(self, img):
        return "1234"


def test_inquire_device_healthy():
    import estelam_gomrok as E
    sent = {}
    saved = _patch_robot(sent)
    try:
        driver = _FakeDriver([["شناسه 356938035643809 دریافت شده از گمرک ورودی یافت نشد."]])
        out = E.inquire_device(driver, None, _FakeSolver(),
                               {"national": "0012345678", "birth": "1375/04/29"},
                               {"id": "a", "serial1": "356938035643809", "serial2": "356938035643817"})
        assert out["status"] == "healthy", out
        assert "یافت نشد" in out["result"]
        # natije-e HAR estelam be telegram miravad (serial-e aval/dovom + zaman)
        msg = sent["telegram"][0]
        assert "نتیجه استعلام" in msg
        assert "356938035643809" in msg and "356938035643817" in msg
        assert "سریال اول" in msg and "سریال دوم" in msg and "زمان استعلام" in msg
    finally:
        _unpatch(saved)


def test_inquire_device_transient_then_healthy():
    """khata-ye movaghat -> captcha-ye taze + click-e dobare (HAMUN morurgar)."""
    import estelam_gomrok as E
    sent = {}
    saved = _patch_robot(sent)
    try:
        driver = _FakeDriver([
            ["خطا در استعلام IMEI از سامانه همتا"],
            ["شناسه 356938035643809 دریافت شده از گمرک ورودی یافت نشد."],
        ])
        out = E.inquire_device(driver, None, _FakeSolver(),
                               {"national": "0012345678", "birth": "1375/04/29"},
                               {"id": "a", "serial1": "356938035643809"})
        assert out["status"] == "healthy", out
        assert driver.clicks >= 2, driver.clicks   # click-e dobare zade shod
    finally:
        _unpatch(saved)


def test_inquire_device_problem_is_not_retried():
    import estelam_gomrok as E
    sent = {}
    saved = _patch_robot(sent)
    try:
        driver = _FakeDriver([["این شناسه قبلا در شبکه داخلی کشور ثبت و فعال شده است"]])
        out = E.inquire_device(driver, None, _FakeSolver(),
                               {"national": "0012345678", "birth": "1375/04/29"},
                               {"id": "a", "serial1": "356938035643809"})
        assert out["status"] == "problem", out
        assert driver.clicks == 1, driver.clicks
    finally:
        _unpatch(saved)


def test_inquire_device_advanced_alerts_telegram():
    import estelam_gomrok as E
    sent = {}
    saved = _patch_robot(sent)
    try:
        driver = _FakeDriver([[]], advance_after=1)
        out = E.inquire_device(driver, None, _FakeSolver(),
                               {"national": "0012345678", "birth": "1375/04/29"},
                               {"id": "a", "serial1": "356938035643809"})
        assert out["status"] == "advanced", out
        assert sent.get("telegram"), "telegram alert nafrestad"
        msg = sent["telegram"][0]
        assert "نتیجه استعلام" in msg and "356938035643809" in msg
        assert "زمان استعلام" in msg
    finally:
        _unpatch(saved)


def test_telegram_result_message_shape():
    """Payam-e natije bayad serial-e aval/dovom + zaman + vaziat dashte bashad."""
    import estelam_gomrok as E
    sent = []
    old = E._telegram
    E._telegram = lambda text: sent.append(text)
    try:
        E._telegram_result({"serial1": "356938035643809", "serial2": "356938035643817"},
                           {"national": "0012345678"}, "healthy", "یافت نشد.")
        E._telegram_result({"serial1": "111222333444555", "serial2": ""},
                           {}, "problem", "قبلا ثبت شده")
    finally:
        E._telegram = old
    first, second = sent
    assert "سریال اول: 356938035643809" in first
    assert "سریال دوم: 356938035643817" in first
    assert "کد ملی: 0012345678" in first
    assert "سالم" in first and "زمان استعلام:" in first
    # dastgah-e tak-serial nabayad khat-e 'serial-e dovom' dashte bashad
    assert "سریال دوم" not in second
    assert "دارای اشکال" in second and "زمان استعلام:" in second


def test_estelam_page_serials_stacked_in_one_column():
    """Sotoon-e serial: serial1/serial2 zir-e ham (na do sotoon-e joda)."""
    import panel
    html = panel.render_estelam_page().decode("utf-8")
    # header ba ezafe shodan-e nam-e dastgah shode «نام / سریال‌ها»
    assert "نام / سریال" in html
    assert "<th>سریال اول</th><th>سریال دوم</th>" not in html
    assert 'class="mono sn1"' in html and 'class="mono sn2"' in html
    assert ".mono.sn2{" in html
    assert 'class="dev-name"' in html


def test_panels_use_normal_zoom():
    """Panel-e khodemoon NABAYAD zoom-e 0.75 dashte bashad (zoom faghat roo-ye
    site-e gomrok dar captcha_collector e'mal mishavad)."""
    import panel
    for name, html in (("site-1", panel.render_page().decode("utf-8")),
                       ("estelam", panel.render_estelam_page().decode("utf-8"))):
        assert "zoom:0.75" not in html, name


def test_site_zoom_lives_in_captcha_collector():
    """Zoom-e 75%-i-ye site + captcha-ye zoom-e ma'koos dar captcha_collector ast."""
    import captcha_collector as cc
    assert cc.SITE_ZOOM == 0.75
    js = cc.zoom_bootstrap_js()
    assert "html{zoom:" in js and "0.75" in js
    assert "img.captchaImage{zoom:" in js  # captcha andaze-ash hefz shavad
    assert hasattr(cc, "install_site_zoom")
    src = open(os.path.join(ROOT, "captcha_collector.py"), encoding="utf-8").read()
    assert src.count("install_site_zoom(build_") >= 2  # roo-ye Chrome va Edge


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
