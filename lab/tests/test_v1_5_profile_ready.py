# -*- coding: utf-8 -*-
"""Registration profiles are linked at passenger attachment and healed at start.

CRM identity is authoritative for linked unpaid profiles, including the actual
passport. Manual unlinked profiles retain optional passport storage, while a
linked registration attempt must reject incomplete canonical identity before
spawning. Financial history must remain unchanged by failed preparation.
"""
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import crm
import panel

OWNER_FIELDS = {
    "first_name": "رضا", "last_name": "محمدی",
    "national": "0012345679", "birth": "1375/04/29",
    "mobile": "09121234567", "passport": "PP0000001",
}
SERIAL1 = "356938035643809"
SERIAL2 = "356938035643810"


def panel_source():
    with open(panel.__file__, encoding="utf-8") as handle:
        return handle.read()


def fn_body_js(js, name):
    """Naive brace-matched body of `function name(...){...}` ("" if absent)."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\{", js)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(js) and depth:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
        i += 1
    return js[m.start():i]


class ProfileReadyHttpHarness(unittest.TestCase):
    """Boots the real handler with isolated stores (test_crm_api pattern)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = os.path.join(self.tmp.name, "profiles.json")
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": []}, handle)
        self.crm_store = os.path.join(self.tmp.name, "crm_store.json")
        self.estelam_store = os.path.join(self.tmp.name, "estelam_store.json")
        with open(self.estelam_store, "w", encoding="utf-8") as handle:
            json.dump({"person": {}, "devices": []}, handle)
        self._patches = [
            patch.object(panel, "PROFILES_PATH", self.profiles),
            patch.object(panel, "INPUTS_PATH",
                         os.path.join(self.tmp.name, "inputs.json")),
            patch.object(panel, "_STORE_LOCK_PATH", self.profiles + ".lock"),
            patch.object(panel, "ESTELAM_PATH", self.estelam_store),
            patch.dict(os.environ, {"IRICA_CRM_STORE": self.crm_store}),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        crm.reset_cache()
        self.addCleanup(crm.reset_cache)

        from lab.tests import _auth_harness
        self._token = _auth_harness.seed_test_users()
        _auth_harness.install_auth_cookie(self, self._token)

        self.httpd = panel.ThreadingHTTPServer(("127.0.0.1", 0),
                                               panel.Handler)
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        # Stop the accept loop before closing its socket on Windows.
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=5)
        self.assertFalse(self.server_thread.is_alive())

    # -- helpers ---------------------------------------------------------
    def _post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception:
                pass
            return error.code, body

    def _add_device(self, **extra):
        record, errs = crm.add_device({"name": "iPhone 13",
                                       "serial1": SERIAL1,
                                       "serial2": SERIAL2, **extra})
        assert errs == [], errs
        return record

    def _add_owner(self, **override):
        record, errs = crm.add_owner({**OWNER_FIELDS, **override})
        assert errs == [], errs
        return record

    def _patch_device(self, device_id, **fields):
        data = crm.store_read()
        dev = next(d for d in data["devices"] if d["id"] == device_id)
        dev.update(fields)
        crm.store_write(data)
        return dev

    def _read_profiles(self):
        with open(self.profiles, encoding="utf-8") as handle:
            return json.load(handle)["profiles"]


class TestAttachCreatesProfile(ProfileReadyHttpHarness):
    """Layer 1 — the /crm-tick attach hook creates/links the profile."""

    def _device_with_owner_at_ready(self):
        device = self._add_device()
        owner = self._add_owner()
        self._patch_device(device["id"], stage="ready_owner")
        return device, owner

    def test_attach_creates_profile_with_owner_data_and_passport(self):
        device, owner = self._device_with_owner_at_ready()
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["national"], OWNER_FIELDS["national"])
        self.assertEqual(profile["birth"], OWNER_FIELDS["birth"])
        self.assertEqual(profile["mobile"], OWNER_FIELDS["mobile"])
        self.assertIn(SERIAL1, profile["imeis"])
        self.assertIn(SERIAL2, profile["imeis"])
        self.assertEqual(profile["passport"], "PP0000001")
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["profile_id"], profile["id"])
        self.assertEqual(stored["owner_id"], owner["id"])
        self.assertEqual(stored["stage"], "ready_owner")

    def test_attach_is_idempotent_no_duplicate_profile(self):
        device, owner = self._device_with_owner_at_ready()
        first = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(first[0], 200, first[1])
        profile_id = next(d for d in crm.store_read()["devices"]
                          if d["id"] == device["id"])["profile_id"]
        second = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(second[0], 200, second[1])
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)   # no duplicate minted
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["profile_id"], profile_id)

    def test_attach_links_existing_profile_by_serial_and_fills_passport(self):
        existing = {"id": "p-manual", "title": "old manual entry",
                    "national": OWNER_FIELDS["national"],
                    "birth": "1375/04/29", "mobile": "09121234567",
                    "imeis": [SERIAL1, SERIAL2], "enabled": True}
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": [existing]}, handle)
        device, owner = self._device_with_owner_at_ready()
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(status, 200, body)
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)               # linked, not minted
        self.assertEqual(profiles[0]["id"], "p-manual")
        self.assertEqual(profiles[0]["passport"], "PP0000001")  # filled once
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["profile_id"], "p-manual")

    def test_attach_reconciles_existing_passport_from_authoritative_crm_owner(self):
        existing = {"id": "p-manual", "title": "old manual entry",
                    "national": OWNER_FIELDS["national"],
                    "birth": "1375/04/29", "mobile": "09121234567",
                    "imeis": [SERIAL1, SERIAL2], "passport": "ZZ9999999",
                    "enabled": True}
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": [existing]}, handle)
        device, owner = self._device_with_owner_at_ready()
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(status, 200, body)
        profiles = self._read_profiles()
        self.assertEqual(profiles[0]["passport"], OWNER_FIELDS["passport"])
        self.assertEqual(profiles[0]["id"], "p-manual")
        self.assertEqual(profiles[0]["crm_device_id"], device["id"])
        self.assertEqual(len(profiles), 1)


class TestStartPaymentSelfHeal(ProfileReadyHttpHarness):
    """The traced bug: skip-shaped cases reach payment with no profile."""

    def test_skip_shape_with_owner_self_heals(self):
        device = self._add_device()
        owner = self._add_owner()
        # The skip shape: stage jumped past handover, owner bound, no link.
        self._patch_device(device["id"], stage="to_payment",
                           owner_id=owner["id"], profile_id="")
        with patch.object(panel, "start_profile_process",
                          return_value={"pid": 4321, "profile_id": "x"}):
            status, body = self._post("/crm-start-payment",
                                      {"device_id": device["id"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["passport"], "PP0000001")
        self.assertEqual(profiles[0]["national"], OWNER_FIELDS["national"])
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["profile_id"], profiles[0]["id"])
        self.assertEqual(stored["stage"], "payment")

    def test_impossible_no_owner_shape_400_actionable_no_stray_profile(self):
        device = self._add_device()
        self._patch_device(device["id"], stage="to_payment", profile_id="")
        status, body = self._post("/crm-start-payment",
                                  {"device_id": device["id"]})
        self.assertEqual(status, 400, body)
        errors = body.get("errors") or []
        self.assertTrue(errors, body)
        self.assertIn("attach", errors[0].lower())
        self.assertIn("passenger", errors[0].lower())
        self.assertEqual(self._read_profiles(), [])

    def test_linked_draft_without_actual_passport_cannot_start_registration(self):
        device = self._add_device()
        owner = self._add_owner(passport="")
        self._patch_device(device["id"], stage="ready_owner")
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner", "owner_id": owner["id"]})
        self.assertEqual(status, 200, body)
        stored = next(d for d in crm.store_read()["devices"] if d["id"] == device["id"])
        self.assertEqual(stored["owner_id"], owner["id"])
        self._patch_device(device["id"], stage="to_payment")
        before = crm.store_read()
        with patch.object(panel, "start_profile_process") as spawn:
            status, body = self._post("/crm-start-payment", {"device_id": device["id"]})
        self.assertEqual(status, 422, body)
        self.assertIn("passport", str(body).lower())
        spawn.assert_not_called()
        after = crm.store_read()
        stored = next(d for d in after["devices"] if d["id"] == device["id"])
        self.assertEqual(stored["stage"], "to_payment")
        self.assertFalse(stored.get("payment_confirmed_at"))
        self.assertEqual(after["customers"], before["customers"])


class TestAttachEnsureFailureHandling(ProfileReadyHttpHarness):
    """A failing ensure must never 500 the attach or drop the binding."""

    def _attach_with_broken_ensure(self, ensure_behavior):
        """ensure_behavior: "" -> the mock RETURNS empty; an exception
        instance -> the mock RAISES (both must keep the route at 200)."""
        device = self._add_device()
        owner = self._add_owner()
        self._patch_device(device["id"], stage="ready_owner")
        log_file = os.path.join(self.tmp.name, "crm_events.jsonl")
        if ensure_behavior == "":
            ensure_patcher = patch.object(
                panel, "crm_ensure_registration_profile", return_value="")
        else:
            ensure_patcher = patch.object(
                panel, "crm_ensure_registration_profile",
                side_effect=ensure_behavior)
        with ensure_patcher, \
                patch.object(crm, "log_path", return_value=log_file):
            status, body = self._post("/crm-tick", {
                "device_id": device["id"], "action": "attach_owner",
                "owner_id": owner["id"]})
        return device, owner, status, body, log_file

    def test_ensure_returns_empty_binding_kept_notice_and_clean_log(self):
        device, owner, status, body, log_file = \
            self._attach_with_broken_ensure("")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body.get("profile_notice"), body)
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["owner_id"], owner["id"])   # binding kept
        self.assertEqual(stored["stage"], "ready_owner")
        self.assertEqual(self._read_profiles(), [])
        self.assertRegex(body["profile_notice"], r"[؀-ۿ]")
        self.assertIn("تلاش", body["profile_notice"])
        self._assert_log_hygiene(log_file, device["id"])

    def test_ensure_raises_binding_kept_notice_and_clean_log(self):
        device, owner, status, body, log_file = \
            self._attach_with_broken_ensure(RuntimeError("store io boom"))
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"], body)
        self.assertTrue(body.get("profile_notice"), body)
        stored = next(d for d in crm.store_read()["devices"]
                      if d["id"] == device["id"])
        self.assertEqual(stored["owner_id"], owner["id"])   # binding kept
        self._assert_log_hygiene(log_file, device["id"])

    def _assert_log_hygiene(self, log_file, device_id):
        with open(log_file, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        ensure_failed = [e for e in lines
                         if e.get("event") == "profile_ensure_failed"]
        self.assertEqual(len(ensure_failed), 1)
        entry = ensure_failed[0]
        self.assertEqual(entry.get("device_id"), device_id)
        self.assertEqual(entry.get("phase"), "attach")
        # No personal data anywhere in the log: owner names/national/passport
        # and the device serials must never appear.
        blob = json.dumps(lines, ensure_ascii=False)
        for secret in (OWNER_FIELDS["national"], OWNER_FIELDS["passport"],
                       OWNER_FIELDS["first_name"], OWNER_FIELDS["last_name"],
                       OWNER_FIELDS["mobile"], SERIAL1, SERIAL2):
            self.assertNotIn(secret, blob)


class TestSavePassportValidation(ProfileReadyHttpHarness):
    """/save route: optional passport, validated only at the route level."""

    def _new_profile_payload(self, **override):
        # NOTE: ASCII title on purpose — the /save route prints the title to
        # stdout, and a Persian title crashes the handler thread under a
        # cp1252 console (pre-existing print, out of scope here).
        return {"title": "Passport Roundtrip", "national": "0012345679",
                "birth": "1375/04/29", "mobile": "09121234567",
                "first_name": "Reza", "last_name": "Mohammadi",
                "father_name": "Mohsen", "birth_certificate_no": "1234",
                "imeis": [SERIAL1], **override}

    def test_valid_passport_stored_uppercased(self):
        status, body = self._post(
            "/save", self._new_profile_payload(passport="ab1234567"))
        self.assertEqual(status, 200, body)
        profile = self._read_profiles()[0]
        self.assertEqual(profile["passport"], "AB1234567")

    def test_empty_passport_passes(self):
        status, body = self._post(
            "/save", self._new_profile_payload(passport=""))
        self.assertEqual(status, 200, body)
        self.assertEqual(self._read_profiles()[0]["passport"], "")

    def test_missing_passport_on_new_profile_uses_creation_default(self):
        status, body = self._post("/save", self._new_profile_payload())
        self.assertEqual(status, 200, body)
        self.assertEqual(self._read_profiles()[0]["passport"], "A")

    def test_new_profile_defaults_without_backfilling_existing_blank(self):
        status, body = self._post("/save", self._new_profile_payload(passport=""))
        self.assertEqual(status, 200, body)
        existing_id = self._read_profiles()[0]["id"]
        status, body = self._post("/new", {})
        self.assertEqual(status, 200, body)
        profiles = {profile["id"]: profile for profile in self._read_profiles()}
        self.assertEqual(profiles[existing_id]["passport"], "")
        self.assertNotEqual(body["active_id"], existing_id)
        self.assertEqual(profiles[body["active_id"]]["passport"], "A")

    def test_last_profile_replacement_gets_creation_default(self):
        status, body = self._post("/save", self._new_profile_payload(passport="PP0000009"))
        self.assertEqual(status, 200, body)
        old_id = self._read_profiles()[0]["id"]
        status, body = self._post("/delete", {"id": old_id})
        self.assertEqual(status, 200, body)
        profiles = self._read_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertNotEqual(profiles[0]["id"], old_id)
        self.assertEqual(profiles[0]["passport"], "A")

    def test_first_launch_gets_creation_default(self):
        os.unlink(self.profiles)
        store = panel.load_store()
        self.assertEqual(store["profiles"][0]["passport"], "A")
        self.assertEqual(self._read_profiles()[0]["passport"], "A")

    def test_legacy_input_migration_does_not_invent_a_passport(self):
        os.unlink(self.profiles)
        with open(panel.INPUTS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"title": "Legacy manual profile"}, handle)
        self.assertEqual(panel.load_store()["profiles"][0]["passport"], "")
        self.assertEqual(self._read_profiles()[0]["passport"], "")

    def test_existing_omitted_passports_preserve_blank_and_real_values(self):
        for fields, expected in (({}, ""), ({"passport": None}, ""),
                                 ({"passport": ""}, ""), ({"passport": "  "}, ""),
                                 ({"passport": " pp0000009 "}, "PP0000009")):
            with self.subTest(fields=fields):
                with open(self.profiles, "w", encoding="utf-8") as handle:
                    json.dump({"active_id": "legacy", "profiles": [
                        {"id": "legacy", "title": "Legacy profile", **fields}]}, handle)
                status, body = self._post("/save", {"id": "legacy", "title": "Renamed profile"})
                self.assertEqual(status, 200, body)
                self.assertEqual(self._read_profiles()[0]["passport"], expected)

    def test_malformed_passports_rejected_400_with_english_diagnostic(self):
        for bad in ("ab1!", "A B", "way-too-long-passport-number-1234", "K1"):
            status, body = self._post(
                "/save", self._new_profile_payload(passport=bad))
            self.assertEqual(status, 400, (bad, body))
            self.assertIn("passport", str(body.get("error")).lower())
            self.assertEqual(self._read_profiles(), [])


class TestProfileReadySourceMarkers(unittest.TestCase):
    """Structural markers: two-layer invariant, self-heal, form wiring."""

    def test_profile_defaults_have_optional_passport(self):
        self.assertEqual(panel.PROFILE_DEFAULTS.get("passport"), "")

    def test_clean_profile_normalizes_passport_never_rejects(self):
        cleaned = panel._clean_profile({"passport": " ab1234567 "})
        self.assertEqual(cleaned["passport"], "AB1234567")
        # legacy row without the key stays valid and defaults to ""
        self.assertEqual(panel._clean_profile({"title": "old"})["passport"], "")

    def test_crm_tick_has_two_layer_invariant_comment_and_hook(self):
        src = panel_source()
        start = src.index('self.path == "/crm-tick"')
        chunk = src[start:src.index('self.path == "/crm-start-payment"')]
        self.assertIn("TWO-LAYER", chunk)          # invariant documented
        self.assertIn("attach_owner", chunk)
        self.assertIn("profile_ensure_failed", chunk)
        self.assertIn("profile_notice", chunk)
        self.assertIn("crm_ensure_registration_profile", chunk)

    def test_start_payment_delegates_to_the_runtime_tested_self_heal_entrypoint(self):
        # TestStartPaymentSelfHeal exercises real profile creation and payment
        # state; this structural assertion only pins the HTTP delegation.
        src = panel_source()
        start = src.index("def _crm_start_payment")
        chunk = src[start:src.index("\n    def ", start + 10)]
        self.assertIn("start_crm_payment(device_id", chunk)

    def test_build_form_has_one_labeled_passport_in_base(self):
        body = fn_body_js(panel.PAGE_JS, "buildForm")
        self.assertEqual(body.count('name="passport"'), 1)
        self.assertIn('id="profile-passport"', body)
        self.assertIn('for="profile-passport"', body)
        self.assertIn('dir="ltr"', body)
        self.assertIn('aria-describedby="profile-passport-hint"', body)
        self.assertLess(body.index('name="passport"'), body.index('id="view-identity"'))
        self.assertNotIn("اختیاری", body)

    def test_hidden_tab_passport_defers_validation_until_server_normalization(self):
        body = fn_body_js(panel.PAGE_JS, "buildForm")
        passport_input = body.split("var passportInput=", 1)[1].split(";", 1)[0]
        self.assertNotIn("pattern=", passport_input)
        self.assertNotIn("maxlength=", passport_input)

    def test_profile_overview_includes_the_saved_passport(self):
        body = fn_body_js(panel.PAGE_JS, "buildProfileOverview")
        self.assertIn("p.passport", body)
        self.assertLess(body.index("p.passport"), body.index('id="view-identity"'))

    def test_save_profile_posts_passport_and_checks_r_ok(self):
        body = fn_body_js(panel.PAGE_JS, "saveProfile")
        self.assertIn("passport:f.passport.value.trim()", body)
        # the fixed .then: HTTP status checked, error surfaced on the toast
        # (red) instead of assigning the error body to STORE
        self.assertIn("if(!res.ok){", body)
        self.assertIn("res.data.error", body)
        self.assertIn("var(--danger)", body)
        self.assertIn("STORE=res.data", body)      # success path only

    def test_do_attach_surfaces_profile_notice(self):
        body = fn_body_js(panel.CRM_PAGE, "doAttach")
        self.assertIn("if(r.profile_notice)", body)
        # v1.3.1: the notice is named with the passenger (toastCtx)
        self.assertIn("toastCtx(oname,r.profile_notice,false)", body)


if __name__ == "__main__":
    unittest.main()
