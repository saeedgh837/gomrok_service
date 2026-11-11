"""Pure renderer/accessibility contracts and trusted JavaScript syntax checks.

These tests do not execute the browser controller. Confirmation, authentication,
focus, polling, and health-request behavior still require isolated browser gates.
"""
from __future__ import annotations

from html.parser import HTMLParser
import importlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


class _DOM(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []
        self.scripts = {}
        self.styles = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = {key: value if value is not None else "" for key, value in attrs}
        index = len(self.nodes)
        self.nodes.append({"tag": tag, "attrs": attrs,
                           "parent": self.stack[-1] if self.stack else None, "text": ""})
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(index)

    def handle_startendtag(self, tag, attrs):
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        self.stack = self.stack[:before]

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.nodes[self.stack[index]]["tag"] == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, data):
        if self.stack:
            node = self.nodes[self.stack[-1]]
            node["text"] += data
            if node["tag"] == "script":
                self.scripts[node["attrs"].get("id", "")] = node["text"]
            if node["tag"] == "style":
                self.styles[node["attrs"].get("id", "")] = node["text"]

    def by_id(self, node_id):
        return next(node for node in self.nodes if node["attrs"].get("id") == node_id)

    def descendants(self, parent):
        index = self.nodes.index(parent)
        result = []
        for node in self.nodes:
            owner = node["parent"]
            while owner is not None:
                if owner == index:
                    result.append(node)
                    break
                owner = self.nodes[owner]["parent"]
        return result

    def text(self, node):
        return node["text"] + "".join(child["text"] for child in self.descendants(node))


_PAGE = '<!doctype html><html lang="fa" dir="rtl"><head><title>Fixture</title></head><body><header class="irica-maint-host">__MAINTENANCE_ACTIONS__</header><main id="fixture-grid">Original content</main></body></html>'


class MaintenanceRenderTests(unittest.TestCase):
    def ui(self):
        try:
            return importlib.import_module("maintenance_ui")
        except ModuleNotFoundError as exc:
            if exc.name != "maintenance_ui":
                raise
            self.fail("The pure maintenance_ui renderer has not been implemented")

    def rendered(self):
        return self.ui().with_maintenance_ui(_PAGE)

    def test_shared_fragments_are_deterministic_and_replace_one_header_marker(self):
        ui = self.ui()
        rendered = ui.with_maintenance_ui(_PAGE)
        self.assertEqual(ui.actions_html(), ui.actions_html())
        self.assertEqual(ui.dialogs_html(), ui.dialogs_html())
        self.assertNotIn("__MAINTENANCE_ACTIONS__", rendered)
        self.assertIn('<header class="irica-maint-host">' + ui.actions_html() + '</header>', rendered)
        self.assertIn('<main id="fixture-grid">Original content</main>', rendered)
        self.assertEqual(rendered, ui.with_maintenance_ui(_PAGE))

    def test_missing_duplicate_or_misordered_boundaries_fail_closed(self):
        ui = self.ui()
        invalid = [
            _PAGE.replace("__MAINTENANCE_ACTIONS__", ""),
            _PAGE.replace("__MAINTENANCE_ACTIONS__", "__MAINTENANCE_ACTIONS__" * 2),
            _PAGE.replace("<head>", ""), _PAGE.replace("</head>", ""),
            _PAGE.replace("<body>", ""), _PAGE.replace("</body>", ""),
            _PAGE.replace("<head>", "<head><head>"), _PAGE.replace("</head>", "</head></head>"),
            _PAGE.replace("<body>", "<body><body>"), _PAGE.replace("</body>", "</body></body>"),
            _PAGE.replace("<head>", "<body>").replace("</head>", "</body>").replace("<body><header", "<head><header").replace("</main></body>", "</main></head>"),
            _PAGE.replace("__MAINTENANCE_ACTIONS__", "").replace("<title>", "__MAINTENANCE_ACTIONS__<title>"),
        ]
        for page in invalid:
            with self.subTest(page=page), self.assertRaises(RuntimeError):
                ui.with_maintenance_ui(page)

    def test_case_insensitive_normal_head_and_body_attributes_preserve_host_shell(self):
        page = _PAGE.replace("<head>", '<HEAD data-fixture="head">').replace("</head>", "</HEAD>")
        page = page.replace("<body>", '<BODY class="fixture-body">').replace("</body>", "</BODY>")
        result = self.ui().with_maintenance_ui(page)
        dom = _DOM(result)
        self.assertEqual(next(n for n in dom.nodes if n["tag"] == "body")["attrs"], {"class": "fixture-body"})
        self.assertEqual(next(n for n in dom.nodes if n["tag"] == "html")["attrs"], {"lang": "fa", "dir": "rtl"})
        self.assertIn('<title>Fixture</title>', result)

    def test_dialogs_are_direct_body_children_with_no_automatic_open_state(self):
        dom = _DOM(self.rendered())
        dialogs = [node for node in dom.nodes if node["tag"] == "dialog"]
        self.assertEqual({n["attrs"].get("id") for n in dialogs}, {"irica-maint-updates", "irica-maint-health", "irica-maint-release"})
        for node in dialogs:
            self.assertEqual(dom.nodes[node["parent"]]["tag"], "body")
            self.assertEqual(node["attrs"].get("aria-modal"), "true")
            self.assertEqual(node["attrs"].get("lang"), "fa")
            self.assertEqual(node["attrs"].get("dir"), "rtl", "Persian dialogs must declare RTL paragraph direction")
            self.assertEqual(node["attrs"].get("tabindex"), "-1", "Empty-dialog focus fallback must be programmatically focusable")
            self.assertNotIn("open", node["attrs"])
            self.assertIn(node["attrs"]["aria-labelledby"], {n["attrs"].get("id") for n in dom.nodes})
            self.assertIn(node["attrs"]["aria-describedby"], {n["attrs"].get("id") for n in dom.nodes})

    def test_styles_and_controller_are_singletons_in_normal_head_and_body(self):
        dom = _DOM(self.rendered())
        self.assertEqual(set(dom.scripts), {"irica-maint-controller"})
        self.assertEqual(set(dom.styles), {"irica-maint-style"})
        self.assertEqual(dom.nodes[dom.by_id("irica-maint-style")["parent"]]["tag"], "head")
        self.assertEqual(dom.nodes[dom.by_id("irica-maint-controller")["parent"]]["tag"], "body")
        for asset in ("irica-maint-style", "irica-maint-controller", "irica-maint-updates", "irica-maint-health", "irica-maint-release"):
            with self.subTest(asset=asset), self.assertRaises(RuntimeError):
                self.ui().with_maintenance_ui(_PAGE.replace("<title>", '<meta id="' + asset + '"><title>'))

    def test_new_ui_identifiers_are_unique_namespaced_and_copy_is_persian(self):
        ui = self.ui()
        fragment = '<body>' + ui.actions_html() + ui.dialogs_html() + '</body>'
        dom = _DOM(fragment)
        copy = " ".join(node["text"] for node in dom.nodes)
        copy += " ".join(node["attrs"].get("aria-label", "") for node in dom.nodes)
        self.assertRegex(copy, r"[؀-ۿ]", "The maintenance interface must use Persian copy")
        self.assertNotRegex(copy.replace("IRICA", "").replace("ChromeDriver", ""), r"[A-Za-z]", "No English UI copy may remain outside product names")
        self.assertNotRegex(copy, r"[يك٠-٩]", "Use Persian characters and digits, not Arabic variants")
        identifiers = [n["attrs"]["id"] for n in dom.nodes if "id" in n["attrs"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for node in dom.nodes:
            if "id" in node["attrs"]:
                self.assertTrue(node["attrs"]["id"].startswith("irica-maint-"))
            for cls in node["attrs"].get("class", "").split():
                self.assertTrue(cls.startswith("irica-maint-"))
            self.assertFalse(any(key.startswith("on") for key in node["attrs"]))

    def test_header_controls_have_visible_labels_decorative_svg_and_dialog_targets(self):
        dom = _DOM('<body>' + self.ui().actions_html() + self.ui().dialogs_html() + '</body>')
        actions = next(node for node in dom.nodes if node["attrs"].get("class") == "irica-maint-actions")
        self.assertEqual(actions["attrs"].get("lang"), "fa")
        self.assertEqual(actions["attrs"].get("dir"), "rtl")
        for suffix, label, target in (("open-updates", "به‌روزرسانی‌ها", "updates"), ("open-health", "سلامت سرویس‌ها", "health")):
            button = dom.by_id("irica-maint-" + suffix)
            self.assertEqual(button["tag"], "button")
            self.assertEqual(button["attrs"].get("type"), "button")
            self.assertIn(label, dom.text(button))
            self.assertEqual(button["attrs"].get("aria-haspopup"), "dialog")
            self.assertEqual(button["attrs"].get("aria-controls"), "irica-maint-" + target)
            icons = [n for n in dom.descendants(button) if n["tag"] == "svg"]
            self.assertEqual(len(icons), 1)
            self.assertEqual(icons[0]["attrs"].get("aria-hidden"), "true")
        indicator = dom.by_id("irica-maint-indicator")
        self.assertEqual(indicator["attrs"].get("role"), "status")
        self.assertEqual(indicator["attrs"].get("aria-live"), "polite")

    def test_versions_are_separate_ltr_isolates_and_progress_is_labeled(self):
        dom = _DOM(self.rendered())
        for suffix in ("app-current", "app-offer", "driver-current", "driver-offer", "browser-version", "confirm-version"):
            version = dom.by_id("irica-maint-" + suffix)
            self.assertEqual(version["tag"], "bdi")
            self.assertEqual(version["attrs"].get("dir"), "ltr")
        progress = dom.by_id("irica-maint-app-progress")
        self.assertEqual(progress["tag"], "progress")
        self.assertTrue(progress["attrs"].get("aria-label"))
        self.assertIn("hidden", progress["attrs"])

    def test_confirmation_is_hidden_and_cancel_precedes_explicit_install_button(self):
        dom = _DOM(self.rendered())
        confirmation = dom.by_id("irica-maint-confirmation")
        self.assertIn("hidden", confirmation["attrs"])
        buttons = [n for n in dom.descendants(confirmation) if n["tag"] == "button"]
        self.assertEqual([n["attrs"].get("id") for n in buttons], ["irica-maint-confirm-cancel", "irica-maint-confirm-install"])
        self.assertEqual(dom.text(buttons[0]).strip(), "انصراف")
        self.assertIn("disabled", buttons[1]["attrs"])
        for button in [n for n in dom.nodes if n["tag"] == "button"]:
            self.assertEqual(button["attrs"].get("type"), "button", "No implicit form submission may install software")
        self.assertEqual([n for n in dom.nodes if n["tag"] == "form"], [])

    def test_health_has_exact_service_rows_manual_action_and_scope_caveats(self):
        dom = _DOM(self.rendered())
        dialog = dom.by_id("irica-maint-health")
        rows = [n for n in dom.descendants(dialog) if n["attrs"].get("class") == "irica-maint-health-row"]
        self.assertEqual([n["attrs"].get("id") for n in rows], ["irica-maint-health-cloud", "irica-maint-health-telegram", "irica-maint-health-proxy"])
        self.assertIn("آمادگی ارسال پیام", dom.text(dom.by_id("irica-maint-health-telegram-scope")))
        self.assertIn("اختیاری", dom.text(dom.by_id("irica-maint-health-proxy-scope")))
        self.assertEqual(dom.by_id("irica-maint-health-run")["tag"], "button")
        self.assertEqual(dom.text(dom.by_id("irica-maint-health-time")).strip(), "بررسی نشده")
        self.assertIn("پیامی در تلگرام ارسال نمی‌شود", dom.text(dialog))

    def test_sign_in_links_are_fixed_local_and_no_api_supplied_link_slots_exist(self):
        dom = _DOM(self.rendered())
        links = [n for n in dom.nodes if n["tag"] == "a"]
        self.assertEqual(len(links), 2)
        self.assertEqual({n["attrs"].get("href") for n in links}, {"/login"})
        for suffix in ("auth", "health-auth", "app-manual"):
            self.assertIn("hidden", dom.by_id("irica-maint-" + suffix)["attrs"])

    def test_component_styles_are_scoped_and_include_touch_focus_and_motion_rules(self):
        dom = _DOM(self.rendered())
        css = dom.styles["irica-maint-style"]
        selectors = re.findall(r"(?:^|\})\s*([^{}]+)\{", css)
        for selector in selectors:
            if selector.strip().startswith("@"):
                continue
            for part in selector.split(","):
                self.assertTrue(part.strip().startswith((".irica-maint-", "#irica-maint-")), part)
        self.assertRegex(css, r"min-block-size:\s*44px")
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("flex-wrap:wrap", css)
        self.assertIn("overflow-wrap:anywhere", css)

    def test_primary_keyboard_active_state_retains_high_contrast(self):
        css = _DOM(self.rendered()).styles["irica-maint-style"]
        self.assertRegex(css, r"\.irica-maint-primary:active:not\(:disabled\)\s*\{[^}]*background:var\(--brand")
        self.assertRegex(css, r"\.irica-maint-primary:active:not\(:disabled\)\s*\{[^}]*color:var\(--on-brand")

    def test_release_summary_can_be_reread_from_updates_without_an_extra_header_button(self):
        dom = _DOM(self.rendered())
        dialog = dom.by_id("irica-maint-release")
        trigger = dom.by_id("irica-maint-release-open")
        self.assertIn(trigger, dom.descendants(dom.by_id("irica-maint-updates")))
        self.assertEqual(trigger["attrs"].get("aria-controls"), "irica-maint-release")
        self.assertEqual(trigger["attrs"].get("aria-haspopup"), "dialog")
        self.assertEqual(dom.by_id("irica-maint-release-version")["tag"], "bdi")
        self.assertEqual(dom.by_id("irica-maint-release-version")["attrs"].get("dir"), "ltr")
        self.assertIn(dom.by_id("irica-maint-release-ack"), dom.descendants(dialog))
        self.assertIn(dom.by_id("irica-maint-release-close"), dom.descendants(dialog))
        self.assertNotIn("irica-maint-release-open", self.ui().actions_html())

    def test_release_summary_is_absent_from_the_unauthenticated_login(self):
        import panel
        self.assertNotIn('id="irica-maint-release"', panel.render_login_page().decode("utf-8"))

    def test_controller_has_valid_javascript_syntax(self):
        dom = _DOM(self.rendered())
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for this trusted syntax check")
        with tempfile.TemporaryDirectory(prefix="maintenance-ui-syntax-") as directory:
            source = Path(directory) / "maintenance-controller.js"
            source.write_text(dom.scripts["irica-maint-controller"], encoding="utf-8")
            result = subprocess.run([node, "--check", str(source)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


class MaintenanceControllerSourceContractTests(unittest.TestCase):
    """Static guard/call-site contracts only; coordinator owns runtime proof."""

    def controller(self):
        import maintenance_ui
        return _DOM(maintenance_ui.with_maintenance_ui(_PAGE)).scripts["irica-maint-controller"]

    def function(self, name):
        match = re.search(r"(?ms)^  (?:async )?function " + re.escape(name) + r"\([^\n]*\) \{\n.*?^  \}", self.controller())
        self.assertIsNotNone(match, "Missing controller function: " + name)
        return match.group(0)

    def test_both_products_accept_waiting_offers_without_weakening_manual_install_guard(self):
        candidate = self.function("candidate")
        accepted = re.search(r"if \(!\[([^\]]+)\]\.includes\(state\)\) return null;", candidate)
        self.assertIsNotNone(accepted, "Both products need the same reviewable-state guard")
        self.assertEqual(set(re.findall(r"'([^']+)'", accepted.group(1))), {"available", "failed", "waiting_for_idle"})
        self.assertIn("data.checked !== true", candidate)
        self.assertIn("kind === 'app' && data.can_install !== true", candidate)
        self.assertIn("return offerFor(kind);", candidate)

    def test_waiting_retry_is_explicit_and_requires_same_product_exact_approved_offer(self):
        review = self.function("reviewOrRetry")
        retry = re.search(r"if \(stateFor\(kind\) === 'waiting_for_idle' && sameOffer\(approved\[kind\], offer\)\) \{\s*void install\(kind, offer\);\s*return;\s*\}", review)
        self.assertIsNotNone(retry, "Waiting driver retries must use the selected product's approved offer")
        self.assertLess(retry.end(), review.index("pending = {...offer, kind};"))
        self.assertIn("byId('confirm-cancel').focus();", review)
        self.assertIn("authExpired || mutationBusy || readBusy || readError || uncertain[kind] || pending", review)
        controller = self.controller()
        self.assertIn("const approved = {app: null, driver: null};", controller)
        self.assertEqual(controller.count("approved[captured.kind] ="), 1)

    def test_driver_waiting_action_identifies_retry_and_preserves_app_retry_label(self):
        render = self.function("render")
        self.assertIn("state === 'waiting_for_idle' && sameOffer(approved[kind], offer)", render)
        self.assertIn("'تلاش دوباره برای نصب درایور'", render)
        self.assertIn("'تلاش دوباره برای نصب'", render)
        self.assertIn("'بررسی به‌روزرسانی درایور'", render)
        self.assertIn("'بررسی به‌روزرسانی برنامه'", render)

    def test_localized_messages_never_use_raw_backend_copy(self):
        controller = self.controller()
        self.assertNotIn("publicText", controller, "Backend prose must not bypass the fixed Persian copy")
        self.assertNotRegex(controller, r"\b(?:data|step)\.(?:message|scope|readiness)\b")
        self.assertIn("text(kind + '-message', descriptions[state]);", self.function("render"))
        self.assertIn("serviceScopes[id]", self.function("runHealth"))
        self.assertIn("serviceReadiness[id]", self.function("runHealth"))
        self.assertIn("آمادگی برای ارسال پیام بررسی نشده است.", controller)

    def test_display_formatting_localizes_percent_and_date_but_preserves_iso_time(self):
        render = self.function("render")
        self.assertIn("persianDigits(percent)", render)
        self.assertIn("٪", render)
        controller = self.controller()
        self.assertIn("۰۱۲۳۴۵۶۷۸۹", controller)
        self.assertIn("new Intl.DateTimeFormat('fa-IR'", controller)
        self.assertIn("calendar: 'persian'", controller)
        self.assertIn("numberingSystem: 'arabext'", controller)
        self.assertIn("byId('health-time').dateTime = timestamp;", controller)
        self.assertNotIn("text('health-time', timestamp", controller)

    def test_driver_retry_keeps_consent_and_has_no_poll_or_open_install_call_site(self):
        install = self.function("install")
        self.assertIn("{offer_id: offer.offer_id, consent: true}", install)
        self.assertIn("!sameOffer(approved[kind], offer)", install)
        self.assertIn("'/api/update-chromedriver', 'POST', payload, false", install)
        self.assertEqual(len(re.findall(r"(?m)^\s+void install\(", self.controller())), 2)
        for name in ("applySnapshot", "readSnapshot", "schedulePoll", "openDialog", "render"):
            with self.subTest(function=name):
                self.assertNotRegex(self.function(name), r"\binstall\(")


class IntegratedMaintenanceRenderTests(unittest.TestCase):
    def test_crm_container_and_single_column_grid_fit_the_available_width(self):
        import panel
        self.assertTrue(".wrap{width:min(var(--crm-w,1240px),100%);" in panel.CRM_PAGE,
                        "CRM must use container width, not viewport width outside its page padding")
        self.assertTrue("@media (max-width:1100px){.crm-cols{grid-template-columns:minmax(0,1fr)}" in panel.CRM_PAGE,
                        "The single-column CRM grid must allow wide tables to scroll inside their own containers")

    def test_public_health_renderer_separates_transport_and_readiness(self):
        import panel
        source = panel.render_login_page().decode("utf-8")
        health = source.split("function renderHealthContent(d){", 1)[1].split("// Close modals", 1)[0]
        for label in ("فقط بررسی ارتباط", "دسترسی رد شد", "تنظیم نشده", "پایان مهلت", "نامشخص"):
            self.assertIn(label, health)
        self.assertIn("آمادگی برای ارسال پیام بررسی نشده است.", health)
        self.assertIn('<div lang="fa" dir="rtl">', health)
        self.assertNotIn("overallOk", health)

    def test_persian_retry_action_declares_its_language_and_direction(self):
        import panel
        retry = next((line for line in panel.CRM_PAGE.splitlines() if "تلاش دوباره برای ثبت" in line), "")
        self.assertRegex(retry, r'<button[^>]*lang="fa"[^>]*dir="rtl"[^>]*>[^<]*تلاش دوباره برای ثبت</button>')

    def test_three_real_renderers_share_one_controller_and_two_header_actions(self):
        from unittest.mock import patch
        import panel
        fixture = {"profiles": [], "active_id": ""}
        with patch.object(panel, "load_store", return_value=fixture), \
                patch.object(panel, "current_profile_store", return_value=fixture, create=True), \
                patch.object(panel, "load_logs", return_value=[]), \
                patch.object(panel, "public_telegram_config", return_value={}), \
                patch.object(panel, "estelam_load", return_value={"devices": []}), \
                patch.object(panel, "estelam_runtime", return_value={}):
            pages = (panel.render_home_page(), panel.render_page(), panel.render_estelam_page(), panel.render_crm_page("fixture_user"))
        for page in pages:
            with self.subTest(panel=pages.index(page)):
                source = page.decode("utf-8") if isinstance(page, bytes) else page
                dom = _DOM(source)
                self.assertIn("irica-maint-controller", dom.scripts)
                self.assertEqual(source.count('id="irica-maint-open-updates"'), 1)
                self.assertEqual(source.count('id="irica-maint-open-health"'), 1)
                self.assertEqual(source.count('id="irica-maint-updates"'), 1)
                self.assertNotIn("__MAINTENANCE_ACTIONS__", source)
                self.assertIn("irica-maint-host", source)
                self.assertIn("X-CSRF-Token", source)


if __name__ == "__main__":
    unittest.main()
