"""Registration attempts use synthetic stores and mocked external boundaries only."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
INSTALL = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"


def _configure_fixture(root):
    os.environ.update({
        "IRICA_DATA_DIR": str(root), "IRICA_CRM_STORE": str(root / "crm.json"),
        "IRICA_ESTELAM_STORE": str(root / "inquiry.json"),
        "IRICA_IMPORT_FROM": str(root / "absent-legacy"),
        "IRICA_CLOUD_WORKER_OFF": "1", "IRICA_CRM_SCHEDULER_OFF": "1",
    })
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import panel
    import crm
    for key, name in {
        "PROFILES_PATH": "profiles.json", "_STORE_LOCK_PATH": "profiles.json.lock",
        "INPUTS_PATH": "inputs.json", "ESTELAM_PATH": "inquiry.json",
        "ROBOT_LOG_PATH": "worker.log", "LOGS_PATH": "logs.jsonl",
    }.items():
        setattr(panel, key, str(root / name))
    crm.reset_cache()
    return panel, crm


def _wait(path, timeout=3):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Fixture worker did not signal readiness")
        time.sleep(0.01)


def _attempt_worker(root, mode, ready, proceed):
    panel, _ = _configure_fixture(Path(root))
    attempt = panel.claim_profile("profile1")
    Path(ready).write_text(json.dumps({
        "runner_id": attempt["runner_id"],
        "fingerprint": attempt["claimed_identity_fingerprint"],
    }), encoding="ascii")
    _wait(Path(proceed))
    if mode == "claim-publish":
        result = panel.publish_profile_result("profile1", attempt["runner_id"],
            attempt["claimed_identity_fingerprint"],
            {"link": "https://fixture.invalid/payment", "identity_verified": True})
    elif mode == "claim-assert":
        try:
            panel.assert_profile_identity_current("profile1", attempt["runner_id"])
            result = {"ok": True}
        except panel.ProfileIdentityStopped:
            result = {"ok": False}
    else:
        raise ValueError("Unknown fixture worker mode")
    print(json.dumps(result), flush=True)
    panel.release_profile("profile1", attempt["runner_id"])


class AttemptFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="registration_attempt_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Set all paths before importing either application module.
        self.panel, self.crm = _configure_fixture(self.root)
        versions_path = patch.object(self.panel, "_cloud_versions_path", return_value=str(self.root / "versions.json"))
        versions_path.start()
        self.addCleanup(versions_path.stop)
        self.owner = {"id": "owner1", "first_name": "Fixture", "last_name": "Passenger",
                      "national": "0012345679", "birth": "1375/04/29",
                      "mobile": "09121234567", "passport": "PP0000001"}
        self.device = {"id": "device1", "name": "Fixture phone", "owner_id": "owner1",
                       "serial1": "356938035640001", "serial2": "356938035640002",
                       "stage": "payment", "profile_id": "profile1"}
        self.profile = {"id": "profile1", "title": "Fixture profile",
                        "national": self.owner["national"], "birth": self.owner["birth"],
                        "mobile": self.owner["mobile"], "passport": self.owner["passport"],
                        "imeis": [self.device["serial1"], self.device["serial2"]], "enabled": True}
        self.seed()
        self.addCleanup(self.crm.reset_cache)

    def seed(self, profiles=None, devices=None, owners=None):
        self.crm.store_write({"owners": copy.deepcopy(owners or [self.owner]),
                              "devices": copy.deepcopy(devices or [self.device]), "settings": {}})
        self.panel.save_store({"active_id": "profile1", "profiles": copy.deepcopy(
            [self.profile] if profiles is None else profiles)})

    def read_profile(self, profile_id="profile1"):
        return next(p for p in self.panel.load_store()["profiles"] if p["id"] == profile_id)

    def change_owner(self, **fields):
        with self.crm.STORE_LOCK:
            data = self.crm.store_read()
            data["owners"][0].update(fields)
            self.crm.store_write(data)

    def claim(self):
        result = self.panel.claim_profile("profile1")
        self.assertIsNotNone(result, "A valid unpaid linked profile must be claimable")
        return result

    def publish(self, attempt, **result):
        method = getattr(self.panel, "publish_profile_result", None)
        self.assertTrue(callable(method), "Attempt-scoped result publication is required")
        return method(attempt["id"], attempt["runner_id"],
                      attempt.get("claimed_identity_fingerprint", ""),
                      {"link": "https://fixture.invalid/payment", "identity_verified": True, **result})

    def correction(self, **fields):
        from cloud_sync import mapper
        owner = dict(self.crm.store_read()["owners"][0], **fields)
        wire = mapper.to_row("owners", owner, source_installation_id=INSTALL)
        self.panel._apply_conflict_server_record(
            {"config": types.SimpleNamespace(source_installation_id=INSTALL)},
            "owners", "owner1", wire)

    def worker(self, mode):
        ready, proceed = self.root / "worker.ready", self.root / "worker.proceed"
        process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                    "--attempt-worker", str(self.root), mode, str(ready), str(proceed)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            proceed.touch()
            try:
                process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=1)
        self.addCleanup(cleanup)
        _wait(ready)
        return process, proceed


class TestRegistrationAttempts(AttemptFixture):
    def test_local_owner_correction_invalidates_current_outputs_on_first_crm_read(self):
        self.profile.update(registration_status="success", payment_link="https://fixture.invalid/old",
                            success_at="2026-01-01 00:00:00", receipt_screenshot="old.png")
        self.seed()
        self.panel.crm_ensure_registration_profile("device1")
        _, errors = self.crm.update_owner("owner1", {"mobile": "09351234567"})
        self.assertEqual(errors, [])
        result = self.panel.crm_state_payload()
        pay = result["devices"][0]["pay"]
        self.assertEqual(pay["registration_status"], "pending")
        self.assertEqual(pay["payment_link"], "")
        self.assertEqual(pay["receipt_screenshot"], "")
        self.assertEqual(result["devices"][0]["stage"], "payment")
        self.assertTrue(any(item["payment_link"] == "https://fixture.invalid/old"
                            for item in self.read_profile()["attempt_history"]))

    def test_current_profile_snapshot_reconciles_before_legacy_ui_render(self):
        self.profile.update(registration_status="success", payment_link="https://fixture.invalid/old")
        self.seed()
        self.change_owner(passport="PP0000002")
        snapshot = getattr(self.panel, "current_profile_store", None)
        self.assertTrue(callable(snapshot), "Profile reads do not reconcile canonical identity")
        profile = snapshot()["profiles"][0]
        self.assertEqual(profile["registration_status"], "pending")
        self.assertEqual(profile["payment_link"], "")
        self.assertEqual(profile["passport"], "PP0000002")

    def test_opaque_cloud_link_collision_never_invalidates_or_exposes_unrelated_manual_success(self):
        self.profile.update(registration_status="success", payment_link="https://fixture.invalid/manual",
                            imeis=["111111111111111"], receipt_screenshot="manual.png")
        self.seed()
        before = (self.root / "profiles.json").read_bytes()
        snapshot = self.panel.current_profile_store()
        manual = snapshot["profiles"][0]
        self.assertEqual(manual["registration_status"], "success")
        self.assertEqual(manual["payment_link"], "https://fixture.invalid/manual")
        self.assertEqual((self.root / "profiles.json").read_bytes(), before)
        pay = self.panel.crm_state_payload()["devices"][0]["pay"]
        self.assertEqual(pay["payment_link"], "")
        self.assertEqual(pay["receipt_screenshot"], "")
        self.assertEqual((self.root / "profiles.json").read_bytes(), before)

    def test_cloud_apply_preserves_unrelated_manual_profiles_with_opaque_link_collisions(self):
        from cloud_sync import mapper
        for existing_collision in (False, True):
            for running in (False, True):
                with self.subTest(existing_collision=existing_collision, running=running):
                    self.profile.update(registration_status="success", payment_link="https://fixture.invalid/manual",
                                        imeis=["111111111111111"], receipt_screenshot="manual.png",
                                        run_status="busy" if running else "idle", runner_id="manual-runner" if running else "")
                    self.device["profile_id"] = "profile1" if existing_collision else "foreign-profile"
                    self.seed()
                    before = (self.root / "profiles.json").read_bytes()
                    if existing_collision:
                        self.correction(first_name="Cloud cosmetic correction")
                    else:
                        device = dict(self.device, profile_id="profile1")
                        wire = mapper.to_row("crm_devices", device, source_installation_id=INSTALL)
                        self.panel._apply_conflict_server_record(
                            {"config": types.SimpleNamespace(source_installation_id=INSTALL)},
                            "crm_devices", "device1", wire)
                    self.assertEqual((self.root / "profiles.json").read_bytes(), before)
                    manual = self.read_profile()
                    self.assertEqual(manual["payment_link"], "https://fixture.invalid/manual")
                    self.assertFalse(manual["attempt_invalidated"])

    def test_backup_recovery_never_revives_superseded_current_evidence(self):
        self.profile.update(registration_status="success", payment_link="https://fixture.invalid/old",
                            receipt_screenshot="old.png")
        self.seed()
        self.change_owner(passport="PP0000002")
        self.panel.crm_ensure_registration_profile("device1")
        current = self.root / "profiles.json"
        current.write_text("{corrupt-current", encoding="utf-8")
        self.panel._LAST_GOOD_STORE.clear()
        recovered = self.panel.load_store()
        self.assertEqual(recovered["profiles"][0]["registration_status"], "pending")
        self.assertEqual(recovered["profiles"][0]["payment_link"], "")
        self.assertEqual(recovered["profiles"][0]["receipt_screenshot"], "")
        self.assertTrue(recovered.get("recovery_required"))
        self.assertTrue(any(row["payment_link"] == "https://fixture.invalid/old"
                            for row in recovered["profiles"][0]["attempt_history"]))
        with self.assertRaises(self.panel.StoreUnreadableError):
            self.panel.save_store(recovered)
        self.assertEqual(current.read_text(encoding="utf-8"), "{corrupt-current")

    def test_crm_payment_retry_is_exposed_only_for_idle_incomplete_unpaid_cases(self):
        for stage, status, expected in (("payment", "pending", True),
                                        ("payment", "success", False),
                                        ("payment_done", "pending", False),
                                        ("done", "pending", False),
                                        ("closed", "pending", False)):
            with self.subTest(stage=stage, status=status):
                self.device["stage"] = stage
                self.profile["registration_status"] = status
                self.seed()
                pay = self.panel.crm_state_payload()["devices"][0]["pay"]
                self.assertIs(pay.get("can_retry"), expected)

    def test_existing_link_refreshes_all_material_fields_and_invalidates_prepayment(self):
        self.profile.update(registration_status="success", success_at="2026-01-01 00:00:00",
                            payment_link="https://fixture.invalid/old", receipt_screenshot="old.png")
        self.seed()
        self.change_owner(national="0084575948", birth="1374/05/21",
                          mobile="09351234567", passport="PP0000002")
        self.assertEqual(self.panel.crm_ensure_registration_profile("device1"), "profile1")
        profile = self.read_profile()
        for key in ("national", "birth", "mobile", "passport"):
            self.assertEqual(profile[key], self.crm.store_read()["owners"][0][key])
        self.assertEqual(profile["registration_status"], "pending")
        self.assertEqual(profile["payment_link"], "")
        self.assertTrue(any(e.get("payment_link") == "https://fixture.invalid/old"
                            for e in profile["attempt_history"]))
        self.assertEqual(self.crm.store_read()["devices"][0]["stage"], "payment")

    def test_paid_cases_never_refresh_or_reopen(self):
        for stage in ("payment_done", "done", "closed"):
            with self.subTest(stage=stage):
                self.device["stage"] = stage
                self.profile.update(registration_status="success", payment_link="old")
                self.seed()
                before = (self.root / "profiles.json").read_bytes()
                self.change_owner(passport="PP0000002")
                self.assertEqual(self.panel.crm_ensure_registration_profile("device1"), "profile1")
                self.assertEqual((self.root / "profiles.json").read_bytes(), before)
                self.assertIsNone(self.panel.claim_profile("profile1"))
                with patch.object(self.panel, "start_profile_process") as spawn:
                    self.assertEqual(self.panel.start_profile("profile1")[0], 409)
                    spawn.assert_not_called()

    def test_cosmetic_correction_keeps_success_and_fingerprint(self):
        self.panel.crm_ensure_registration_profile("device1")
        attempt = self.claim()
        self.assertTrue(self.publish(attempt)["ok"])
        self.panel.release_profile("profile1", attempt["runner_id"])
        original = self.read_profile()["identity_fingerprint"]
        self.change_owner(first_name="Renamed")
        self.panel.crm_ensure_registration_profile("device1")
        profile = self.read_profile()
        self.assertEqual(profile["identity_fingerprint"], original)
        self.assertEqual(profile["registration_status"], "success")

    def test_serial_binding_is_ambiguous_or_partial_fail_closed(self):
        for profiles in ([self.profile, dict(self.profile, id="duplicate")],
                         [dict(self.profile, imeis=[self.device["serial1"], "356938035640009"])]):
            with self.subTest(count=len(profiles)):
                self.device["profile_id"] = ""
                self.seed(profiles=profiles)
                before = (self.root / "profiles.json").read_bytes()
                with self.assertRaises(ValueError):
                    self.panel.crm_ensure_registration_profile("device1")
                self.assertEqual((self.root / "profiles.json").read_bytes(), before)

    def test_cross_pc_opaque_id_never_binds_unrelated_profile(self):
        unrelated = dict(self.profile, national="0084575948", imeis=["356938035640009"])
        self.seed(profiles=[unrelated])
        profile_id = self.panel.crm_ensure_registration_profile("device1")
        self.assertNotEqual(profile_id, "profile1")
        self.assertEqual(self.read_profile("profile1")["imeis"], unrelated["imeis"])
        self.assertEqual(self.panel.crm_ensure_registration_profile("device1"), profile_id)
        self.assertEqual(self.read_profile(profile_id)["crm_device_id"], "device1")

    def test_missing_profile_self_heals_deterministically(self):
        self.seed(profiles=[])
        first = self.panel.crm_ensure_registration_profile("device1")
        self.panel.save_store({"profiles": [], "active_id": ""})
        second = self.panel.crm_ensure_registration_profile("device1")
        self.assertEqual(first, second)

    def test_reverse_only_link_refreshes_before_direct_and_pool_claim(self):
        self.device["profile_id"] = "foreign-opaque"
        self.profile["crm_device_id"] = "device1"
        self.profile["registration_status"] = "success"
        self.seed()
        self.change_owner(passport="PP0000002")
        attempt = self.panel.claim_profile()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt["passport"], "PP0000002")
        self.assertEqual(attempt["claimed_identity_fingerprint"], attempt["identity_fingerprint"])

    def test_invalid_passport_never_starts_or_claims(self):
        for passport in ("", "PP-1", "P" * 21):
            with self.subTest(passport=passport):
                self.seed()
                self.change_owner(passport=passport)
                with patch.object(self.panel, "start_profile_process") as spawn:
                    self.assertEqual(self.panel.start_profile("profile1")[0], 422)
                    spawn.assert_not_called()
                self.assertIsNone(self.panel.claim_profile("profile1"))

    def test_prepaid_success_refreshes_before_both_start_routes(self):
        for starter, spawn_name in ((self.panel.start_profile, "start_profile_process"),
                                    (self.panel.start_profile2, "start_profile2_process")):
            with self.subTest(starter=starter.__name__):
                self.profile["registration_status"] = "success"
                self.seed()
                self.change_owner(passport="PP0000002")
                with patch.object(self.panel, spawn_name, return_value={"pid": 987654321}) as spawn:
                    status, _ = starter("profile1")
                self.assertEqual(status, 200)
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(self.read_profile()["passport"], "PP0000002")

    def test_empty_or_wrong_attempt_cannot_publish_or_write_output(self):
        attempt = self.claim()
        for runner, fingerprint in (("", ""), ("wrong", attempt.get("claimed_identity_fingerprint", "")),
                                    (attempt["runner_id"], "0" * 64)):
            with self.subTest(runner=runner):
                method = getattr(self.panel, "publish_profile_result", None)
                self.assertTrue(callable(method))
                self.assertFalse(method("profile1", runner, fingerprint, {"link": "bad"})["ok"])
        self.assertFalse(self.panel.record_profile_output("profile1", receipt="bad.png"))
        self.assertEqual(self.read_profile()["registration_status"], "pending")

    def test_same_national_profiles_publish_only_exact_claim(self):
        other = dict(self.profile, id="profile2", imeis=["356938035640009"])
        self.seed(profiles=[self.profile, other])
        attempt = self.claim()
        self.assertTrue(self.publish(attempt, receipt="current.png")["ok"])
        self.assertEqual(self.read_profile()["receipt_screenshot"], "current.png")
        self.assertEqual(self.read_profile("profile2")["registration_status"], "pending")
        self.assertFalse(hasattr(self.panel, "mark_profile_success"))
        self.assertEqual(self.read_profile("profile2")["payment_link"], "")

    def test_live_source_change_stops_assert_and_rejects_result(self):
        attempt = self.claim()
        self.change_owner(passport="PP0000002")
        stop = getattr(self.panel, "ProfileIdentityStopped", None)
        self.assertIsNotNone(stop)
        self.assertTrue(issubclass(stop, SystemExit))
        with self.assertRaises(stop):
            self.panel.assert_profile_identity_current("profile1", attempt["runner_id"])
        self.assertFalse(self.publish(attempt)["ok"])
        self.assertNotEqual(self.read_profile()["registration_status"], "success")

    def test_cloud_a_b_a_keeps_old_attempt_invalidated(self):
        attempt = self.claim()
        self.correction(passport="PP0000002")
        self.correction(passport="PP0000001")
        self.assertTrue(self.read_profile()["attempt_invalidated"])
        self.assertFalse(self.publish(attempt)["ok"])

    def test_cloud_cosmetic_change_does_not_invalidate_active_attempt(self):
        attempt = self.claim()
        self.correction(first_name="Renamed")
        self.assertFalse(self.read_profile()["attempt_invalidated"])
        self.panel.assert_profile_identity_current("profile1", attempt["runner_id"])
        self.assertTrue(self.publish(attempt)["ok"])

    def test_corrected_external_prepayment_requires_review_preserves_evidence(self):
        attempt = self.claim()
        result = self.publish(attempt, identity_verified=False, existing_prepayment=True,
                              receipt="unverified.png")
        self.assertFalse(result["ok"])
        profile = self.read_profile()
        self.assertTrue(profile["identity_review_required"])
        self.assertNotEqual(profile["registration_status"], "success")
        self.assertTrue(any(e.get("receipt_screenshot") == "unverified.png"
                            for e in profile["attempt_history"]))

    def test_local_material_edits_blocked_while_busy_and_linked_source_is_authoritative(self):
        self.claim()
        with self.assertRaises(ValueError):
            self.panel.save_profile_update({"id": "profile1", "passport": "PP0000002"})
        self.assertEqual(self.read_profile()["passport"], "PP0000001")
        self.panel.save_profile_update({"id": "profile1", "title": "Cosmetic title"})
        self.assertEqual(self.read_profile()["title"], "Cosmetic title")

    def test_clone_is_unlinked_and_drops_attempt_metadata(self):
        self.claim()
        clone = self.panel.clone_profile("profile1")
        self.assertEqual(clone["crm_device_id"], "")
        self.assertEqual(clone["claimed_identity_fingerprint"], "")
        self.assertEqual(clone["attempt_history"], [])

    def test_atomic_spawn_reservation_blocks_duplicate_start_and_direct_claim(self):
        calls = []
        def spawn(*args, **kwargs):
            calls.append(self.read_profile())
            return {"pid": os.getpid()}
        with patch.object(self.panel, "start_profile_process", side_effect=spawn):
            self.assertEqual(self.panel.start_profile("profile1")[0], 200)
            self.assertEqual(self.panel.start_profile("profile1")[0], 409)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["run_status"], "busy")
        self.assertIsNone(self.panel.claim_profile("profile1"))

    def test_manual_payment_rejects_protected_and_invalid_stages_before_spawn(self):
        for stage in ("new", "ready_owner", "payment_done", "done", "closed"):
            with self.subTest(stage=stage):
                self.device["stage"] = stage
                self.seed()
                handler = object.__new__(self.panel.Handler)
                handler.actor = "fixture"
                responses = []
                handler._json = lambda payload, status=200: responses.append((status, payload))
                with patch.object(self.panel, "start_profile_process") as spawn:
                    handler._crm_start_payment("device1")
                    spawn.assert_not_called()
                self.assertFalse(responses[-1][1]["ok"])

    def test_manual_payment_allows_current_payment_retry_and_surfaces_advance_failure(self):
        handler = object.__new__(self.panel.Handler)
        handler.actor = "fixture"
        responses = []
        handler._json = lambda payload, status=200: responses.append((status, payload))
        with patch.object(self.panel, "start_profile_process", return_value={"pid": 987654321}):
            handler._crm_start_payment("device1")
        self.assertTrue(responses[-1][1]["ok"])
        self.assertEqual(self.crm.store_read()["devices"][0]["stage"], "payment")
        self.device["stage"] = "to_payment"
        self.seed()
        with patch.object(self.crm, "advance", return_value=(None, ["Fixture rejected stage"])), \
                patch.object(self.panel, "start_profile_process") as spawn:
            handler._crm_start_payment("device1")
            spawn.assert_not_called()
        self.assertFalse(responses[-1][1]["ok"])

    def test_corrupt_current_stores_never_authorize_cached_attempt(self):
        for name in ("crm.json", "profiles.json"):
            with self.subTest(name=name):
                self.seed()
                attempt = self.claim()
                path = self.root / name
                path.write_text("{broken-current-store", encoding="ascii")
                before = path.read_bytes()
                with self.assertRaises(self.panel.ProfileIdentityStopped):
                    self.panel.assert_profile_identity_current("profile1", attempt["runner_id"])
                self.assertFalse(self.publish(attempt)["ok"])
                self.assertEqual(path.read_bytes(), before)

    def test_draft_owner_can_be_linked_but_missing_passport_never_claims(self):
        self.device["stage"] = "ready_owner"
        self.owner["passport"] = ""
        self.seed(profiles=[])
        profile_id = self.panel.crm_ensure_registration_profile("device1")
        self.assertTrue(profile_id)
        self.assertEqual(self.read_profile(profile_id)["passport"], "")
        with self.crm.STORE_LOCK:
            data = self.crm.store_read()
            data["devices"][0]["stage"] = "payment"
            self.crm.store_write(data)
        self.assertIsNone(self.panel.claim_profile(profile_id))

    def test_temporary_ensure_errors_retry_without_spawning_stale_inputs(self):
        self.device["stage"] = "to_payment"
        self.seed()
        real = self.panel.crm_ensure_registration_profile
        calls = []
        def temporary(device_id):
            calls.append(device_id)
            if len(calls) < 3:
                raise OSError("Fixture-only storage contention")
            return real(device_id)
        with patch.object(self.panel, "crm_ensure_registration_profile", side_effect=temporary), \
                patch.object(self.panel, "start_profile_process", return_value={"pid": 987654321}) as spawn:
            status, payload = self.panel.start_crm_payment("device1")
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(calls), 3)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.crm.store_read()["devices"][0]["stage"], "payment")

    def test_failed_spawn_releases_reservation_without_false_success(self):
        with patch.object(self.panel, "start_profile_process", side_effect=OSError("Fixture launch failure")):
            status, payload = self.panel.start_profile("profile1")
        self.assertNotEqual(status, 200)
        self.assertFalse(payload.get("ok"))
        profile = self.read_profile()
        self.assertEqual(profile["run_status"], "idle")
        self.assertEqual(profile["runner_id"], "")
        self.assertFalse(profile["attempt_reserved"])

    def test_cloud_pull_fetches_unlocked_and_applies_fresh_versions_and_dirty_state(self):
        from cloud_sync import ids, mapper
        attempt = self.claim()
        calls = []
        owner = mapper.to_row("owners", dict(self.owner, passport="PP0000002"),
                              source_installation_id=INSTALL)
        def network(handler):
            self.assertIsNone(self.crm.STORE_LOCK.path)
            calls.append("fetch")
            handler({"changes": [{"entity": "owners", "id": ids.legacy_to_uuid(INSTALL, "owners", "owner1"),
                                    "seq": 1, "row_version": 1, "record": owner}]})
            self.assertIsNone(self.crm.STORE_LOCK.path)
            return 1
        config = types.SimpleNamespace(api_base="https://fixture.invalid", sync_device_id="fixture",
                                       dataset_epoch=1, source_installation_id=INSTALL)
        client = types.SimpleNamespace(pull_all=network)
        real_versions, real_dirty = self.panel._load_cloud_versions, self.panel._cloud_dirty_set
        def versions(*args):
            self.assertIsNotNone(self.crm.STORE_LOCK.path)
            calls.append("versions")
            return real_versions(*args)
        def dirty(*args):
            self.assertIsNotNone(self.crm.STORE_LOCK.path)
            calls.append("dirty")
            return real_dirty(*args)
        with patch.object(self.panel, "_load_cloud_versions", side_effect=versions), \
                patch.object(self.panel, "_cloud_dirty_set", side_effect=dirty):
            result = self.panel.cloud_pull_step({"config": config, "client": client,
                                                 "lease": None, "outbox": None})
        self.assertEqual(calls, ["fetch", "versions", "dirty"])
        self.assertEqual(result["pulled"], 1)
        self.assertTrue(self.read_profile()["attempt_invalidated"])
        self.assertFalse(self.publish(attempt)["ok"])

    def test_bootstrap_and_incremental_cloud_apply_share_attempt_invalidation(self):
        from cloud_sync import ids, mapper
        self.claim()
        document = self.crm.store_read()
        document["owners"][0]["passport"] = "PP0000002"
        rows = mapper.split_store(document, source_installation_id=INSTALL)
        collections = {}
        for entity in ids.ENTITIES:
            records = [rows[entity]] if entity == "settings" else rows[entity]
            collections[entity] = [{
                "id": ids.legacy_to_uuid(INSTALL, entity,
                                         mapper.SETTINGS_LEGACY_ID if entity == "settings" else record["id"]),
                "tenant_id": TENANT, "row_version": 1, "record": record,
            } for record in records]
        config = types.SimpleNamespace(source_installation_id=INSTALL)
        events = []
        def committed(snapshot):
            self.assertIsNone(self.crm.STORE_LOCK.path)
            events.append("committed")
        client = types.SimpleNamespace(commit_bootstrap=committed)
        self.panel._cloud_apply_snapshot({"config": config, "client": client, "outbox": None},
            {"collections": collections, "cursor": 1, "dataset_epoch": 1, "tenant_id": TENANT})
        self.assertEqual(events, ["committed"])
        self.assertTrue(self.read_profile()["attempt_invalidated"])

    def test_collection_edits_preserve_sticky_cloud_invalidation(self):
        self.claim()
        self.correction(passport="PP0000002")
        for action, fields in (("activate", {"id": "profile1"}),
                               ("toggle", {"id": "profile1", "enabled": False}),
                               ("new", {})):
            self.panel.update_profile_collection(action, fields)
            self.assertTrue(self.read_profile()["attempt_invalidated"])
        with self.assertRaises(self.panel.RegistrationIdentityError):
            self.panel.update_profile_collection("delete", {"id": "profile1"})

    def test_stale_attempt_cannot_block_a_corrected_profile(self):
        attempt = self.claim()
        self.correction(passport="PP0000002")
        self.assertFalse(self.panel.mark_profile_unusable("profile1", "Fixture stale error", attempt["runner_id"]))
        self.assertNotEqual(self.read_profile()["registration_status"], "blocked")

    def test_attempt_history_is_bounded_without_dropping_latest_evidence(self):
        attempt = self.claim()
        for index in range(30):
            self.assertTrue(self.publish(attempt, receipt=f"receipt-{index}.png")["ok"])
        history = self.read_profile()["attempt_history"]
        self.assertEqual(len(history), 20)
        self.assertEqual(history[-1]["receipt_screenshot"], "receipt-29.png")

    def test_cross_process_cloud_correction_cannot_race_publication(self):
        process, proceed = self.worker("claim-publish")
        self.correction(passport="PP0000002")
        proceed.touch()
        stdout, stderr = process.communicate(timeout=6)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertFalse(json.loads(stdout.strip().splitlines()[-1])["ok"])
        self.assertNotEqual(self.read_profile()["registration_status"], "success")

    def test_cross_process_a_b_a_safe_stop_is_sticky(self):
        process, proceed = self.worker("claim-assert")
        self.correction(passport="PP0000002")
        self.correction(passport="PP0000001")
        proceed.touch()
        stdout, stderr = process.communicate(timeout=6)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertFalse(json.loads(stdout.strip().splitlines()[-1])["ok"])


class TestRobotAttemptBoundaries(AttemptFixture):
    def setUp(self):
        super().setUp()
        import gomrok
        import gomrok2
        self.robot = gomrok
        self.robot2 = gomrok2
        self.attempt = self.claim()
        self.patches = [
            patch.object(gomrok, "_CLAIMED_PROFILE_ID", "profile1"),
            patch.object(gomrok, "_CLAIMED_RUNNER_ID", self.attempt["runner_id"]),
            patch.object(gomrok, "_CLAIMED_FINGERPRINT", self.attempt.get("claimed_identity_fingerprint", ""), create=True),
            patch.object(gomrok, "_CLAIMED_IDENTITY_VERIFIED", False, create=True),
            patch.object(gomrok, "_SCREENSHOTS_DIR", str(self.root / "screenshots")),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_linked_cli_overrides_cannot_replace_claimed_identity(self):
        args = types.SimpleNamespace(national="0084575948", birth="1374/05/21",
                                     mobile="09351234567", passport="BADOVERRIDE", imei=["356938035640009"])
        inputs = self.robot._registration_inputs(self.attempt, args)
        for field in ("national", "birth", "mobile", "passport", "imeis"):
            self.assertEqual(inputs[field], self.attempt[field])

    def test_form_verification_requires_exact_national_mobile_and_actual_passport(self):
        values = {self.robot.WP_REG_NATIONAL: self.owner["national"],
                  self.robot.WP_REG_MOBILE: self.owner["mobile"],
                  self.robot.WP_REG_PASSPORT: self.owner["passport"]}
        driver = types.SimpleNamespace(execute_script=lambda script, wp: values.get(wp, ""))
        self.assertTrue(self.robot.verify_registrant_identity(driver, self.attempt))
        for wp in values:
            original = values[wp]
            values[wp] = "different"
            self.assertFalse(self.robot.verify_registrant_identity(driver, self.attempt))
            values[wp] = original

    def test_existing_external_prepayment_never_announces_unverified_linked_success(self):
        with patch.object(self.robot, "_full_screenshot", return_value=False), \
                patch.object(self.robot, "_extract_payment_link", return_value="https://fixture.invalid/old"), \
                patch.object(self.robot, "_telegram_deliver") as telegram:
            with self.assertRaises(self.panel.ProfileIdentityStopped):
                self.robot.handle_already_registered(object())
            telegram.assert_not_called()
        self.assertTrue(self.read_profile()["identity_review_required"])
        self.assertNotEqual(self.read_profile()["registration_status"], "success")

    def test_stale_final_output_is_not_telegram_success(self):
        self.correction(passport="PP0000002")
        with patch.object(self.robot, "_telegram_deliver") as telegram:
            with self.assertRaises(self.panel.ProfileIdentityStopped):
                self.robot._publish_panel_result("receipt.png", "payment.png", "https://fixture.invalid/stale")
            telegram.assert_not_called()
        self.assertEqual(self.read_profile()["payment_link"], "")

    def test_site2_driver_requires_explicit_compatible_driver(self):
        with patch.object(self.robot2.cc, "require_compatible_chromedriver",
                          side_effect=self.robot2.cc.DriverUpdateRequired("Fixture consent required")) as guard, \
                patch("selenium.webdriver.Chrome") as browser:
            with self.assertRaises(self.robot2.cc.DriverUpdateRequired):
                self.robot2._build_desktop_driver()
            with self.assertRaises(self.robot2.cc.DriverUpdateRequired):
                self.robot2._attach_driver("127.0.0.1:9876")
            self.assertEqual(guard.call_count, 2)
            browser.assert_not_called()

    def test_launcher_registers_self_and_unregisters_exact_creation_on_safe_stop(self):
        import robot_launcher
        events = []
        class Gate:
            @contextmanager
            def admit(self):
                events.append("enter")
                try:
                    yield
                finally:
                    events.append("exit")
            def register(self, pid):
                events.append(("register", pid))
                return 12345
            def unregister(self, pid, *, expected_created):
                events.append(("unregister", pid, expected_created))
        with patch("maintenance_gate.default_gate", return_value=Gate()), \
                patch.object(sys, "argv", ["robot_launcher.py", str(ROOT / "gomrok.py")]), \
                patch.object(robot_launcher.system_debug, "configure"), \
                patch.object(robot_launcher.system_debug, "event"), \
                patch.object(robot_launcher.runpy, "run_path", side_effect=self.panel.ProfileIdentityStopped("Fixture stop")):
            with self.assertRaises(self.panel.ProfileIdentityStopped):
                robot_launcher.main()
        self.assertEqual(events, ["enter", ("register", os.getpid()), "exit",
                                  ("unregister", os.getpid(), 12345)])


class TestMaintenanceAdmission(AttemptFixture):
    def test_every_root_spawn_holds_admission_until_pid_registration(self):
        events = []
        class Gate:
            @contextmanager
            def admit(self):
                events.append("enter")
                try:
                    yield
                finally:
                    events.append("exit")
            def register(self, pid):
                self_pid = int(pid)
                events.append(("register", self_pid))
                return None  # A nonexistent fixture PID is not a registered process.
        def spawn(*args, **kwargs):
            self.assertEqual(events[-1], "enter")
            events.append("spawn")
            return types.SimpleNamespace(pid=987654321)
        roots = [lambda: self.panel.start_profile_process("profile1"),
                 lambda: self.panel.start_profile2_process("profile1"),
                 self.panel.start_site2_manual_process,
                 lambda: self.panel.start_profile_device_inquiry("profile1", "gomrok"),
                 lambda: self.panel.start_exitban_process("profile1"),
                 lambda: self.panel.start_estelam_process("device1")]
        for root in roots:
            with self.subTest(root=root):
                events.clear()
                with patch("maintenance_gate.default_gate", return_value=Gate()), \
                        patch.object(self.panel.subprocess, "Popen", side_effect=spawn):
                    root()
                self.assertEqual(events, ["enter", "spawn", ("register", 987654321), "exit"])


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--attempt-worker":
        _attempt_worker(*sys.argv[2:])
    else:
        unittest.main()
