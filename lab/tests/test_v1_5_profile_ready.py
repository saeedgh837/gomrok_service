# -*- coding: utf-8 -*-
"""v1.5 item 11 — registration profile exists from passenger-connect time.

The registration profile is created/linked at ATTACH time (primary layer,
hooked into /crm-tick attach_owner) with the existing handover/auto-advance
ensure as the backstop. Profile records and the site-1 form gain an
OPTIONAL passport (never required, never blocking; fill-once from the
attached owner, never overwritten). /crm-start-payment self-heals skip-
shaped cases and answers the genuinely-impossible no-owner shape with an
actionable Persian error naming the recovery (no stray profile minted).
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
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

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
                    "imeis": [SERIAL1], "enabled": True}
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

    def test_attach_never_overwrites_existing_passport(self):
        existing = {"id": "p-manual", "title": "old manual entry",
                    "national": OWNER_FIELDS["national"],
                    "birth": "1375/04/29", "mobile": "09121234567",
                    "imeis": [SERIAL1], "passport": "ZZ9999999",
                    "enabled": True}
        with open(self.profiles, "w", encoding="utf-8") as handle:
            json.dump({"active_id": "", "profiles": [existing]}, handle)
        device, owner = self._device_with_owner_at_ready()
        status, body = self._post("/crm-tick", {
            "device_id": device["id"], "action": "attach_owner",
            "owner_id": owner["id"]})
        self.assertEqual(status, 200, body)
        profiles = self._read_profiles()
        self.assertEqual(profiles[0]["passport"], "ZZ9999999")  # untouched
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
        self.assertIn("اتصال مسافر", errors[0])
        self.assertEqual(self._read_profiles(), [])   # nothing minted


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

    def test_missing_passport_key_passes(self):
        """Legacy payloads without the key keep saving (field optional)."""
        status, body = self._post("/save", self._new_profile_payload())
        self.assertEqual(status, 200, body)
        self.assertEqual(self._read_profiles()[0]["passport"], "")

    def test_malformed_passports_rejected_400_persian(self):
        for bad in ("ab1!", "abc", "way-too-long-passport-number-1234"):
            status, body = self._post(
                "/save", self._new_profile_payload(passport=bad))
            self.assertEqual(status, 400, (bad, body))
            self.assertIn("گذرنامه", str(body.get("error")))
            self.assertEqual(self._read_profiles(), [])  # nothing stored


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

    def test_start_payment_contains_self_heal_call(self):
        src = panel_source()
        start = src.index("def _crm_start_payment")
        chunk = src[start:src.index("\n    def ", start + 10)]
        self.assertIn("crm_ensure_registration_profile(device_id)", chunk)
        self.assertIn("اتصال مسافر", chunk)        # actionable recovery

    def test_build_form_has_passport_input(self):
        body = fn_body_js(panel.PAGE_JS, "buildForm")
        self.assertIn('name="passport"', body)
        self.assertIn("class=\"ltr\"", body)
        self.assertIn("اختیاری", body)             # never a required marker

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
        self.assertIn("toast(r.profile_notice,false)", body)

    def test_start_profile_missing_list_untouched(self):
        """Robot side out of scope: passport must not gate the robot start."""
        src = panel_source()
        start = src.index("def start_profile(")
        chunk = src[start:src.index("\ndef ", start + 10)]
        self.assertNotIn("passport", chunk)


if __name__ == "__main__":
    unittest.main()
