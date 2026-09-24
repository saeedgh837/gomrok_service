"""v1.5 B4+B8 — strict section separation and step-wiring safety.

B4: the main devices grid lists ACTIVE cases only; done/problem/closed
devices appear exclusively in their own tabs. KPI and tab counts stay
global. B8: no skip affordance at payment_done — the continue button
(deliver_code) is the only UI path that can complete a case, and
stage_back can never land on `done`; the only way out of a completed
case is the protected done -> payment_done rollback (done_at cleared).
"""
import re
import unittest

import panel
import crm
import lab.tests.test_crm_api as _crm_api


def panel_src():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def crm_js():
    return panel.CRM_PAGE


def fn_body(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not match:
        return ""
    depth, i = 1, match.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[match.start():i]


class SectionSeparationSourceTests(unittest.TestCase):
    """B4/B8 source markers on CRM_PAGE (static checks, no browser)."""

    def setUp(self):
        self.src = panel_src()
        self.js = crm_js()

    def test_active_case_helper_present_and_used_by_devices_html(self):
        self.assertIn(
            "function isActiveCase(d){return !['done','problem','closed']"
            ".includes(d.stage);}", self.src)
        body = fn_body(self.js, "devicesHtml")
        self.assertTrue(body, "devicesHtml not found")
        # v1.10: the grid takes its base set from the period resolver instead
        # of filtering inline, so this pins the delegation rather than the
        # literal. The default reading is still exactly the in-flight cases,
        # and the base is narrowed BEFORE the sort, so ordering never sees
        # inactive rows.
        self.assertIn("periodBaseDevices().sort(", body)
        self.assertIn("all.filter(isActiveCase)",
                      fn_body(self.js, "periodBaseDevices"))
        self.assertNotIn("isActiveCase", fn_body(self.js, "kpisHtml"))

    def test_section_tabs_keep_their_stage_filters(self):
        self.assertIn("filter(d=>d.stage==='done')",
                      fn_body(self.js, "doneTabHtml"))
        self.assertIn("filter(d=>d.stage==='problem')",
                      fn_body(self.js, "problemTabHtml"))
        self.assertIn("filter(d=>d.stage==='closed')",
                      fn_body(self.js, "closedTabHtml"))

    def test_counts_stay_global(self):
        # KPI strip and the devices tab badge count ALL devices (B4 keeps
        # them global); only the card grid is section-limited.
        kpis = fn_body(self.js, "kpisHtml")
        self.assertIn("var ds=(STATE.devices||[]);", kpis)
        tabs = fn_body(self.js, "renderTabs")
        self.assertIn("devices:devs.length", tabs)
        self.assertIn("done_tab:devs.filter(d=>d.stage==='done').length", tabs)

    def test_empty_active_state_keeps_button_and_names_tabs(self):
        body = fn_body(self.js, "devicesHtml")
        self.assertIn("+ پروندهٔ جدید", body)
        self.assertIn("emptyHtml(", body)
        # when only finished cases exist the message points at their tabs
        for tab_label in ("تکمیل شده‌ها", "دارای اشکال", "مختومه‌ها"):
            self.assertIn(tab_label, body)

    def test_no_skip_affordance_at_payment_done(self):
        self.assertNotIn("payment_done':'done", self.src)
        self.assertNotIn("تحویل کد بدون ثبت ثبت‌نام نهایی", self.src)
        self.assertIn("STAGE_SKIP_WARN", self.src)
        skip_map = re.search(r"var STAGE_SKIP_NEXT = \{([\s\S]*?)\};", self.js)
        self.assertTrue(skip_map, "STAGE_SKIP_NEXT not found")
        # 'payment_done' must not be a KEY (it may stay a skip TARGET of
        # 'payment'); the forbidden pairing is asserted above.
        self.assertNotIn("'payment_done':", skip_map.group(1))
        # the off-mainline problem skip is untouched
        self.assertIn("'problem':'inquiry'", skip_map.group(1))

    def test_deliver_code_literal_appears_once_inside_stage_continue(self):
        self.assertEqual(self.src.count("deliver_code"), 1)
        body = fn_body(self.js, "stageContinue")
        self.assertTrue(body, "stageContinue not found")
        self.assertIn("deliver_code", body)
        # src.index context: the single occurrence sits in stageContinue
        idx = self.src.index("deliver_code")
        self.assertGreater(idx, self.src.index("function stageContinue(d){"))
        self.assertLess(idx, self.src.index("function stageActions(d){"))

    def test_skip_ui_markers_survive(self):
        self.assertIn("'__skip'", self.src)
        self.assertIn("stage_skip", self.src)
        self.assertIn("رد کردن این مرحله", self.src)


class BackendWiringPropertyTests(unittest.TestCase):
    """B8: over the crm stage maps, only deliver_code may reach `done`."""

    def test_allowed_reaches_done_only_via_deliver_code(self):
        for stage, actions in crm.ALLOWED.items():
            for action, next_stage in actions.items():
                if next_stage == "done":
                    self.assertEqual(
                        action, "deliver_code",
                        f"crm.ALLOWED[{stage!r}][{action!r}] -> 'done'")
                    self.assertEqual(stage, "payment_done")

    def test_stage_back_never_targets_done(self):
        for stage, target in crm._STAGE_BACK.items():
            self.assertNotEqual(
                target, "done", f"crm._STAGE_BACK[{stage!r}] -> 'done'")

    def test_stage_skip_never_targets_done(self):
        self.assertNotIn("payment_done", crm._STAGE_SKIP_NEXT)
        for stage, next_stage in crm._STAGE_SKIP_NEXT.items():
            self.assertNotEqual(
                next_stage, "done",
                f"crm._STAGE_SKIP_NEXT[{stage!r}] -> 'done'")


class StageWiringHttpTests(_crm_api.CrmApiHarness):
    """B8 regression over the real /crm-tick route (ported store, ephemeral
    HTTPServer — same harness as lab/tests/test_crm_api.py)."""

    def _walk_to(self, target):
        """Add a device and advance it along the mainline to `target`."""
        device, errors = crm.add_device(
            {"name": "iPhone 15", "serial1": "356938035643809"})
        self.assertEqual(errors, [])
        owner, errors = crm.add_owner({
            "first_name": "رضا", "last_name": "محمدی",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "card_no": "6219861012345678",
            "passport": "AB1234567"})
        self.assertEqual(errors, [])
        self._seed_green_estelam(device["id"])
        steps = [("queue_inquiry", {}), ("mark_ready", {}),
                 ("attach_owner", {"owner_id": owner["id"]}),
                 ("handover", {}), ("to_payment", {}), ("start_payment", {}),
                 ("confirm_payment", {}), ("deliver_code", {})]
        current = device
        for action, payload in steps:
            current, errors = crm.advance(device["id"], action, **payload)
            self.assertEqual(errors, [], f"walk failed at {action}: {errors}")
            if current["stage"] == target:
                return current
        self.fail(f"walk never reached stage {target!r}")

    def test_back_from_to_payment_lands_on_handed_never_done(self):
        # Reported bug: "Back at step 5 marked the case Completed" — stage_back
        # from to_payment (step 5) must return to handed, never complete.
        device = self._walk_to("to_payment")
        status, body = self._post(
            "/crm-tick", {"device_id": device["id"], "action": "stage_back"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["device"]["stage"], "handed")
        self.assertNotEqual(body["device"]["stage"], "done")
        self.assertFalse(body["device"].get("done_at"))

    def test_back_from_payment_lands_on_to_payment(self):
        device = self._walk_to("payment")
        status, body = self._post(
            "/crm-tick", {"device_id": device["id"], "action": "stage_back"})
        self.assertEqual(status, 200)
        self.assertEqual(body["device"]["stage"], "to_payment")

    def test_back_from_done_returns_to_payment_done_and_clears_done_at(self):
        # Protected rollback contract (doneRollback's path): done ->
        # payment_done with done_at wiped so the case can be re-completed.
        device = self._walk_to("done")
        self.assertTrue(device["done_at"])
        status, body = self._post(
            "/crm-tick", {"device_id": device["id"], "action": "stage_back"})
        self.assertEqual(status, 200)
        self.assertEqual(body["device"]["stage"], "payment_done")
        self.assertEqual(body["device"]["done_at"], "")

    def test_skip_from_payment_done_is_rejected(self):
        device = self._walk_to("payment_done")
        status, body = self._post(
            "/crm-tick", {"device_id": device["id"], "action": "stage_skip"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"])
        for error in body["errors"]:
            self.assertTrue(
                any("؀" <= ch <= "ۿ" for ch in error),
                f"error is not Persian: {error!r}")
        # stage unchanged: the skip must not complete the case
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["stage"], "payment_done")
        self.assertFalse(stored.get("done_at"))
        self.assertFalse(stored.get("skipped_stages"))


if __name__ == "__main__":
    unittest.main()
