"""Synthetic CRM registration identity and cross-process transaction tests."""
import builtins
import copy
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


def _wait_for_file(path, timeout=3):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Fixture process did not signal readiness")
        time.sleep(0.01)


def _lock_worker(store_path, mode, ready_path, proceed_path):
    # The isolated runner installs its guards before executing this branch.
    # No application import happens before the synthetic store path is set.
    os.environ["IRICA_CRM_STORE"] = store_path
    sys.path.insert(0, str(ROOT))
    import crm
    ready, proceed = Path(ready_path), Path(proceed_path)
    if mode == "hold":
        with crm.STORE_LOCK:
            ready.write_text("locked", encoding="ascii")
            _wait_for_file(proceed)
    elif mode == "increment":
        acquired = crm.STORE_LOCK.acquire(timeout=0.15)
        if acquired:
            crm.STORE_LOCK.release()
        ready.write_text("unprotected" if acquired else "blocked", encoding="ascii")
        _wait_for_file(proceed)
        with crm.STORE_LOCK:
            data = crm.store_read()
            data["settings"]["fixture_counter"] += 1
            crm.store_write(data)
    else:
        raise ValueError("Unknown fixture worker mode")


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="crm_identity_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "crm.json"
        self.environment = patch.dict(os.environ, {
            "IRICA_CRM_STORE": str(self.path), "IRICA_DATA_DIR": str(self.root),
            "IRICA_ESTELAM_STORE": str(self.root / "inquiry.json"),
            "IRICA_IMPORT_FROM": str(self.root / "absent_legacy"),
            "IRICA_CLOUD_WORKER_OFF": "1", "IRICA_CRM_SCHEDULER_OFF": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import crm
        self.crm = crm
        self.panel = types.SimpleNamespace(
            ESTELAM_PATH=str(self.root / "inquiry.json"),
            PROFILES_PATH=str(self.root / "profiles.json"),
            estelam_load=lambda: self.runtime_store("inquiry.json", "devices"),
            load_store=lambda: self.runtime_store("profiles.json", "profiles"),
        )
        modules = patch.dict(sys.modules, {"panel": self.panel})
        modules.start()
        self.addCleanup(modules.stop)
        self.owner = {
            "id": "own1", "first_name": "Fixture", "last_name": "Passenger",
            "national": "0012345679", "birth": "1375/04/29",
            "mobile": "09121234567", "passport": "PP0000001", "card_no": "",
        }
        self.device = {
            "id": "dev1", "name": "Fixture phone", "serial1": "356938035640001",
            "serial2": "356938035640002", "owner_id": "own1",
            "profile_id": "profile1", "stage": "handed",
        }
        self.seed()

    def runtime_store(self, name, collection):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {collection: []}

    def seed(self, owners=None, devices=None):
        self.crm.store_write({
            "owners": copy.deepcopy([self.owner] if owners is None else owners),
            "devices": copy.deepcopy([self.device] if devices is None else devices),
            "settings": {"fixture_counter": 0},
        })

    def unreadable_main(self):
        original_open = builtins.open

        def open_fixture(file, mode="r", *args, **kwargs):
            if os.path.normcase(os.fspath(file)) == os.path.normcase(str(self.path)):
                raise PermissionError("Synthetic current CRM read denied")
            return original_open(file, mode, *args, **kwargs)
        return patch("builtins.open", side_effect=open_fixture)

    def identity(self):
        method = getattr(self.crm, "registration_identity", None)
        self.assertTrue(callable(method), "CRM must expose a canonical registration identity")
        return method("dev1")

    def canonical(self):
        return {
            "device_id": "dev1", "owner_id": "own1", "national": "0012345679",
            "birth": "1375/04/29", "mobile": "09121234567", "passport": "PP0000001",
            "imeis": ["356938035640001", "356938035640002"],
            "title": "Fixture phone — Fixture Passenger", "stage": "handed",
        }

    def fingerprint(self, identity):
        method = getattr(self.crm, "identity_fingerprint", None)
        self.assertTrue(callable(method), "CRM must expose a material identity fingerprint")
        return method(identity)

    def start_worker(self, mode):
        ready = self.root / (mode + ".ready")
        proceed = self.root / (mode + ".proceed")
        process = subprocess.Popen([
            sys.executable, "-I", "-B", str(Path(__file__).resolve()),
            "--crm-lock-worker", str(self.path), mode, str(ready), str(proceed),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def cleanup():
            proceed.touch()
            try:
                process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=1)
        self.addCleanup(cleanup)
        return process, ready, proceed


class TestRegistrationIdentity(FixtureCase):
    def test_exact_canonical_identity_and_normalization(self):
        self.owner.update({
            "national": " " + "".join(chr(0x06F0 + int(digit)) for digit in "0012345679") + " ",
            "birth": "1375/4/29", "mobile": " 09121234567 ",
            "passport": " pp0000001 ",
        })
        self.device["serial1"] = " 356938035640001 "
        self.seed()
        before = self.path.read_bytes()
        identity, errors = self.identity()
        self.assertEqual(errors, [])
        self.assertEqual(identity, self.canonical())
        self.assertEqual(self.path.read_bytes(), before, "Canonical reads must not rewrite source data")

    def test_corrupt_current_identity_never_authorizes_from_old_backup(self):
        self.seed([dict(self.owner, passport="PP0000002")])
        self.path.write_text("{invalid-current", encoding="utf-8")
        before = self.path.read_bytes()
        identity, errors = self.identity()
        self.assertIsNone(identity)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.crm.store_read()["owners"][0]["passport"], "PP0000001",
                         "General recovery reads must remain backwards compatible")

    def test_unreadable_current_identity_never_uses_old_backup(self):
        self.seed([dict(self.owner, passport="PP0000002")])
        before = self.path.read_bytes()
        with self.unreadable_main():
            identity, errors = self.identity()
        self.assertIsNone(identity)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_and_ambiguous_links_fail_without_mutation(self):
        cases = [
            ([], [self.device]),
            ([self.owner], []),
            ([self.owner], [dict(self.device, owner_id="missing")]),
            ([self.owner], [dict(self.device, owner_id="")]),
            ([self.owner, dict(self.owner)], [self.device]),
            ([self.owner], [self.device, dict(self.device)]),
            ([self.owner], [self.device, dict(self.device, id="other")]),
        ]
        for owners, devices in cases:
            with self.subTest(owners=len(owners), devices=len(devices)):
                self.seed(owners, devices)
                before = self.path.read_bytes()
                identity, errors = self.identity()
                self.assertIsNone(identity)
                self.assertTrue(errors)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_submitted_owner_fields_fail_closed(self):
        for key, invalid in (
            ("national", "0012345678"), ("national", ""),
            ("birth", "1375/13/29"), ("birth", "1375/07/31"),
            ("birth", "not-a-date"), ("mobile", "08121234567"),
            ("passport", ""), ("passport", "A-1"),
            ("passport", "A" * 21), ("passport", "PP" + chr(0x06F1)),
        ):
            with self.subTest(key=key, invalid=invalid):
                self.seed([dict(self.owner, **{key: invalid})])
                before = self.path.read_bytes()
                identity, errors = self.identity()
                self.assertIsNone(identity)
                self.assertTrue(errors)
                self.assertTrue(all(error.isascii() for error in errors))
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_imeis_fail_closed(self):
        for serial1, serial2 in (("", ""), ("bad", ""),
                                 ("356938035640001", "bad"),
                                 ("356938035640001", "356938035640001")):
            with self.subTest(serial1=serial1, serial2=serial2):
                self.seed(devices=[dict(self.device, serial1=serial1, serial2=serial2)])
                identity, errors = self.identity()
                self.assertIsNone(identity)
                self.assertTrue(errors)

    def test_unicode_case_expansion_is_not_a_valid_passport(self):
        for passport in (chr(0x00DF), chr(0xFB00)):
            with self.subTest(passport=ascii(passport)):
                self.seed([dict(self.owner, passport=passport)])
                identity, errors = self.identity()
                self.assertIsNone(identity)
                self.assertTrue(errors)

    def test_optional_second_imei_and_actual_single_character_passport(self):
        self.seed([dict(self.owner, passport="Z")], [dict(self.device, serial2="")])
        identity, errors = self.identity()
        self.assertEqual(errors, [])
        self.assertEqual(identity["passport"], "Z")
        self.assertEqual(identity["imeis"], ["356938035640001"])

    def test_cosmetic_draft_fields_are_not_registration_requirements(self):
        self.seed([dict(self.owner, first_name="", last_name="", card_no="draft")])
        identity, errors = self.identity()
        self.assertEqual(errors, [])
        self.assertEqual(identity["national"], "0012345679")

    def test_fingerprint_is_stable_and_ignores_cosmetic_fields(self):
        identity = self.canonical()
        before = copy.deepcopy(identity)
        fingerprint = self.fingerprint(identity)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        cosmetic = dict(reversed(list(identity.items())))
        cosmetic.update(title="Renamed phone", stage="done", first_name="Renamed", card_no="other")
        self.assertEqual(self.fingerprint(cosmetic), fingerprint)
        self.assertEqual(identity, before)

    def test_every_material_field_changes_fingerprint(self):
        identity = self.canonical()
        original = self.fingerprint(identity)
        for key, value in (
            ("device_id", "dev2"), ("owner_id", "own2"),
            ("national", "0012345687"), ("birth", "1375/04/28"),
            ("mobile", "09121234568"), ("passport", "PP0000002"),
            ("imeis", ["356938035640001"]),
            ("imeis", ["356938035640002", "356938035640001"]),
        ):
            with self.subTest(field=key):
                self.assertNotEqual(self.fingerprint(dict(identity, **{key: value})), original)

    def test_fingerprint_rejects_invalid_identity_without_mutation(self):
        for changes in ({"passport": ""}, {"imeis": []}, {"owner_id": ""}):
            candidate = dict(self.canonical(), **changes)
            before = copy.deepcopy(candidate)
            method = getattr(self.crm, "identity_fingerprint", None)
            self.assertTrue(callable(method), "CRM must expose identity_fingerprint")
            with self.assertRaises(ValueError):
                method(candidate)
            self.assertEqual(candidate, before)


class TestOwnerIdentityGuard(FixtureCase):
    def assert_change_blocked(self):
        before = self.path.read_bytes()
        owner, errors = self.crm.update_owner("own1", {"mobile": "09121234568"})
        self.assertIsNone(owner)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_main_cannot_be_overwritten_from_backup_during_owner_edit(self):
        self.seed([dict(self.owner, passport="PP0000002")])
        self.path.write_text("{invalid-current", encoding="utf-8")
        self.assert_change_blocked()

    def test_unreadable_main_cannot_be_overwritten_from_backup_during_owner_edit(self):
        self.seed([dict(self.owner, passport="PP0000002")])
        before = self.path.read_bytes()
        with self.unreadable_main():
            owner, errors = self.crm.update_owner("own1", {"mobile": "09121234568"})
        self.assertIsNone(owner)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)

    def test_material_edits_are_blocked_for_actual_protected_stages(self):
        for stage in ("payment_done", "done", "closed"):
            with self.subTest(stage=stage):
                self.seed(devices=[dict(self.device, stage=stage)])
                self.assert_change_blocked()

    def test_material_edits_are_blocked_for_busy_registration(self):
        (self.root / "profiles.json").write_text(json.dumps({
            "profiles": [{"id": "profile1", "run_status": "busy"}],
        }), encoding="utf-8")
        self.assert_change_blocked()

    def test_reverse_linked_busy_profile_blocks_owner_edit(self):
        self.seed(devices=[dict(self.device, profile_id="")])
        (self.root / "profiles.json").write_text(json.dumps({"profiles": [{
            "id": "reverse_profile", "crm_device_id": "dev1", "run_status": "busy",
        }]}), encoding="utf-8")
        self.assert_change_blocked()

    def test_duplicate_runtime_matches_cannot_hide_an_active_entry(self):
        (self.root / "profiles.json").write_text(json.dumps({"profiles": [
            {"id": "profile1", "run_status": "idle"},
            {"id": "profile1", "run_status": "busy"},
        ]}), encoding="utf-8")
        self.assert_change_blocked()
        (self.root / "profiles.json").unlink()
        (self.root / "inquiry.json").write_text(json.dumps({"devices": [
            {"id": "dev1", "status": "healthy"},
            {"id": "dev1", "status": "running"},
        ]}), encoding="utf-8")
        self.assert_change_blocked()

    def test_material_edit_rejects_ambiguous_owner_links(self):
        for owners, devices in (
            ([self.owner, dict(self.owner)], [self.device]),
            ([self.owner], [self.device, dict(self.device, id="other")]),
        ):
            with self.subTest(owners=len(owners), devices=len(devices)):
                self.seed(owners, devices)
                self.assert_change_blocked()

    def test_material_edits_are_blocked_for_running_inquiries(self):
        for service in ("gomrok", "hamta"):
            with self.subTest(service=service):
                (self.root / "profiles.json").write_text(json.dumps({"profiles": [{
                    "id": "profile1", service + "_inquiry_status": "running",
                }]}), encoding="utf-8")
                self.assert_change_blocked()
        (self.root / "profiles.json").unlink()
        (self.root / "inquiry.json").write_text(json.dumps({
            "devices": [{"id": "dev1", "status": "running"}],
        }), encoding="utf-8")
        self.assert_change_blocked()

    def test_unreadable_runtime_does_not_mean_idle(self):
        for reader in ("estelam_load", "load_store"):
            with self.subTest(reader=reader):
                with patch.object(self.panel, reader, side_effect=OSError("Synthetic unreadable runtime")):
                    self.assert_change_blocked()

    def test_corrupt_runtime_cannot_be_hidden_by_forgiving_reader(self):
        for filename, reader in (("inquiry.json", "estelam_load"),
                                 ("profiles.json", "load_store")):
            with self.subTest(filename=filename):
                runtime = self.root / filename
                runtime.write_text("{invalid", encoding="utf-8")
                with patch.object(self.panel, reader, return_value={"devices": [], "profiles": []}):
                    self.assert_change_blocked()
                runtime.unlink()

    def test_cosmetic_card_and_normalization_only_edits_remain_allowed(self):
        self.seed(devices=[dict(self.device, stage="payment_done")])
        self.panel.load_store = lambda: {"profiles": [{"id": "profile1", "run_status": "busy"}]}
        owner, errors = self.crm.update_owner("own1", {
            "first_name": "Renamed", "card_no": "1234567890123456",
            "passport": " pp0000001 ", "birth": "1375/4/29",
        })
        self.assertEqual(errors, [])
        self.assertEqual(owner["first_name"], "Renamed")
        self.assertEqual(owner["passport"], "PP0000001")
        self.assertEqual(self.crm.store_read()["devices"][0]["stage"], "payment_done")

    def test_idle_unpaid_edit_preserves_links_and_financial_stage(self):
        for stage in ("handed", "to_payment", "payment"):
            with self.subTest(stage=stage):
                self.seed(devices=[dict(self.device, stage=stage)])
                owner, errors = self.crm.update_owner("own1", {"passport": "PP0000002"})
                self.assertEqual(errors, [])
                self.assertEqual(owner["id"], "own1")
                device = self.crm.store_read()["devices"][0]
                self.assertEqual(device["owner_id"], "own1")
                self.assertEqual(device["profile_id"], "profile1")
                self.assertEqual(device["stage"], stage)


class TestDeviceIdentityGuard(FixtureCase):
    def assert_change_blocked(self, fields=None):
        before = self.path.read_bytes()
        backup = Path(str(self.path) + ".bak").read_bytes()
        device, errors = self.crm.update_device(
            "dev1", {"serial2": "356938035640003"} if fields is None else fields)
        self.assertIsNone(device)
        self.assertTrue(errors)
        self.assertTrue(all(error.isascii() for error in errors))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(Path(str(self.path) + ".bak").read_bytes(), backup)

    def test_material_imei_edits_reject_actual_protected_stages(self):
        for stage in ("payment_done", "done", "closed"):
            for serial2 in ("356938035640003", ""):
                with self.subTest(stage=stage, serial2=serial2):
                    self.seed(devices=[dict(self.device, stage=stage)])
                    self.assert_change_blocked({"serial2": serial2})

    def test_material_imei_edits_reject_busy_forward_and_reverse_profiles(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.seed(devices=[dict(self.device, profile_id="" if reverse else "profile1")])
                profile = ({"id": "reverse1", "crm_device_id": "dev1"} if reverse
                           else {"id": "profile1"})
                profile["run_status"] = "busy"
                (self.root / "profiles.json").write_text(
                    json.dumps({"profiles": [profile]}), encoding="utf-8")
                self.assert_change_blocked()

    def test_material_imei_edits_reject_active_profile_and_crm_inquiry(self):
        for service in ("gomrok", "hamta"):
            with self.subTest(service=service):
                (self.root / "profiles.json").write_text(json.dumps({"profiles": [{
                    "id": "profile1", service + "_inquiry_status": "running",
                }]}), encoding="utf-8")
                self.assert_change_blocked()
        (self.root / "profiles.json").unlink()
        (self.root / "inquiry.json").write_text(json.dumps({
            "devices": [{"id": "dev1", "status": "running"}],
        }), encoding="utf-8")
        self.assert_change_blocked()

    def test_material_imei_edits_reject_unverifiable_runtime(self):
        for reader in ("estelam_load", "load_store"):
            with self.subTest(reader=reader):
                with patch.object(self.panel, reader,
                                  side_effect=OSError("Synthetic unreadable runtime")):
                    self.assert_change_blocked()
        (self.root / "profiles.json").write_text("{invalid", encoding="utf-8")
        with patch.object(self.panel, "load_store", return_value={"profiles": []}):
            self.assert_change_blocked()

    def test_duplicate_device_ids_cannot_select_one_identity_for_edit(self):
        self.seed(devices=[self.device, dict(self.device, serial2="356938035640004")])
        self.assert_change_blocked()

    def test_corrupt_current_main_cannot_recover_device_edit_from_backup(self):
        self.seed(devices=[dict(self.device, serial2="356938035640004")])
        self.path.write_text("{invalid-current", encoding="utf-8")
        self.assert_change_blocked()
        self.assertEqual(self.crm.store_read()["devices"][0]["serial2"], "356938035640002")

    def test_unreadable_current_main_cannot_recover_device_edit_from_backup(self):
        self.seed(devices=[dict(self.device, serial2="356938035640004")])
        before = self.path.read_bytes()
        with self.unreadable_main():
            device, errors = self.crm.update_device("dev1", {"serial2": "356938035640003"})
        self.assertIsNone(device)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)

    def test_normalization_and_cosmetic_edits_preserve_identity_when_protected(self):
        digits = "".join(chr(0x06F0 + int(digit)) for digit in self.device["serial2"])
        for stage in ("payment_done", "done", "closed"):
            with self.subTest(stage=stage):
                self.seed(devices=[dict(self.device, stage=stage,
                                       payment_confirmed_at="2026-01-01 12:00:00")])
                before = self.crm.store_read()["devices"][0]
                with patch.object(self.panel, "load_store",
                                  side_effect=OSError("Do not inspect runtime for cosmetics")):
                    device, errors = self.crm.update_device("dev1", {
                        "name": "Renamed phone", "notes": "Cosmetic note",
                        "serial1": " " + self.device["serial1"] + " ",
                        "serial2": " " + digits + " ",
                    })
                self.assertEqual(errors, [])
                self.assertEqual(device["name"], "Renamed phone")
                self.assertEqual(device["notes"], "Cosmetic note")
                for key in ("serial1", "serial2", "stage", "owner_id", "profile_id",
                            "payment_confirmed_at"):
                    self.assertEqual(device[key], before[key])

    def test_idle_unpaid_imei_edits_preserve_ledger_and_case_history(self):
        for stage in ("handed", "to_payment", "payment"):
            with self.subTest(stage=stage):
                self.seed(devices=[dict(self.device, stage=stage,
                                       registered_at="2026-01-01 12:00:00",
                                       skipped_stages=["inquiry"])])
                data = self.crm.store_read()
                data["customers"] = [{"id": "customer1", "transactions": [{
                    "device_id": "dev1", "kind": "registry_fee", "amount": -1200,
                }]}]
                data["devices"][0]["customer_id"] = "customer1"
                self.crm.store_write(data)
                before = copy.deepcopy(data)
                device, errors = self.crm.update_device("dev1", {"serial2": "356938035640003"})
                self.assertEqual(errors, [])
                self.assertEqual(device["serial2"], "356938035640003")
                after = self.crm.store_read()
                self.assertEqual(after["customers"], before["customers"])
                for key, value in before["devices"][0].items():
                    if key not in ("serial2", "updated", "created"):
                        self.assertEqual(after["devices"][0][key], value)

    def test_first_imei_remains_immutable_without_mutating_store(self):
        before = self.path.read_bytes()
        device, errors = self.crm.update_device("dev1", {"serial1": "356938035640003"})
        self.assertIsNone(device)
        self.assertTrue(errors)
        self.assertEqual(self.path.read_bytes(), before)


class TestCRMTransactionGuard(FixtureCase):
    def test_nested_calls_are_reentrant_and_release_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "fixture exception"):
            with self.crm.STORE_LOCK:
                with self.crm.STORE_LOCK:
                    data = self.crm.store_read()
                    data["settings"]["fixture_counter"] = 1
                    self.crm.store_write(data)
                    raise RuntimeError("fixture exception")
        acquired = []

        def check_released():
            ok = self.crm.STORE_LOCK.acquire(timeout=0.2)
            acquired.append(ok)
            if ok:
                self.crm.STORE_LOCK.release()
        thread = threading.Thread(target=check_released)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(acquired, [True])
        self.assertEqual(self.crm.store_read()["settings"]["fixture_counter"], 1)

    def test_concurrent_thread_nonblocking_and_timeout_semantics(self):
        entered, release = threading.Event(), threading.Event()

        def hold():
            with self.crm.STORE_LOCK:
                entered.set()
                release.wait(timeout=2)
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertFalse(self.crm.STORE_LOCK.acquire(blocking=False))
            started = time.monotonic()
            self.assertFalse(self.crm.STORE_LOCK.acquire(timeout=0.05))
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            release.set()
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        with self.assertRaises(RuntimeError):
            self.crm.STORE_LOCK.release()
        with self.assertRaises(ValueError):
            self.crm.STORE_LOCK.acquire(blocking=False, timeout=0.1)

    def test_environment_changes_cannot_redirect_active_transaction(self):
        other = self.root / "other.json"
        with self.crm.STORE_LOCK:
            os.environ["IRICA_CRM_STORE"] = str(other)
            data = self.crm.store_read()
            self.assertEqual(data["settings"].get("fixture_counter"), 0)
            data["settings"]["fixture_counter"] = 7
            self.crm.store_write(data)
            self.assertEqual(os.path.normcase(self.crm._store_path()),
                             os.path.normcase(str(self.path.resolve())))
        self.assertFalse(other.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["settings"]["fixture_counter"], 7)
        self.assertEqual(self.crm.store_read()["owners"], [])

    def test_lock_resolves_store_path_before_nested_relative_reads(self):
        alias = str(self.root / "unused" / ".." / "crm.json")
        os.environ["IRICA_CRM_STORE"] = alias
        with self.crm.STORE_LOCK:
            self.assertEqual(self.crm.store_read()["settings"]["fixture_counter"], 0)
            self.assertEqual(os.path.normcase(self.crm._store_path()),
                             os.path.normcase(str(self.path.resolve())))

    def test_concurrent_threads_preserve_all_rmw_updates(self):
        start = threading.Barrier(4)
        errors = []

        def increment():
            try:
                start.wait(timeout=1)
                for _ in range(5):
                    with self.crm.STORE_LOCK:
                        data = self.crm.store_read()
                        data["settings"]["fixture_counter"] += 1
                        self.crm.store_write(data)
            except Exception as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=increment) for _ in range(3)]
        for thread in threads:
            thread.start()
        start.wait(timeout=1)
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.crm.store_read()["settings"]["fixture_counter"], 15)

    def test_context_acquisition_is_bounded_and_does_not_leak(self):
        factory = getattr(self.crm, "_CRMStoreLock", None)
        self.assertTrue(callable(factory), "CRM transactions need a bounded file-lock context")
        guard = factory(timeout=0.05)
        with self.crm.STORE_LOCK:
            with self.assertRaises(TimeoutError):
                with guard:
                    self.fail("A separate lock handle bypassed the held byte lock")
        with guard:
            pass

    def test_failed_file_acquisition_releases_thread_guard(self):
        acquired = False
        try:
            with patch("builtins.open", side_effect=PermissionError("Fixture lock file unavailable")):
                with self.assertRaises(PermissionError):
                    acquired = self.crm.STORE_LOCK.acquire(timeout=0.05)
        finally:
            if acquired:
                self.crm.STORE_LOCK.release()
        acquired = []

        def acquire_after_error():
            ok = self.crm.STORE_LOCK.acquire(timeout=0.2)
            acquired.append(ok)
            if ok:
                self.crm.STORE_LOCK.release()
        thread = threading.Thread(target=acquire_after_error)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(acquired, [True])

    def test_cross_process_rmw_serializes_tmp_and_backup(self):
        with self.crm.STORE_LOCK:
            process, ready, proceed = self.start_worker("increment")
            _wait_for_file(ready)
            result = ready.read_text(encoding="ascii")
            data = self.crm.store_read()
            data["settings"]["fixture_counter"] += 1
            self.crm.store_write(data)
        proceed.touch()
        stdout, stderr = process.communicate(timeout=4)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(result, "blocked", "Another process must not enter the CRM transaction")
        self.assertEqual(self.crm.store_read()["settings"]["fixture_counter"], 2)
        backup = json.loads(Path(str(self.path) + ".bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["settings"]["fixture_counter"], 1)
        self.assertFalse(Path(str(self.path) + ".tmp").exists())

    def test_cross_process_timeout_leaves_lock_usable(self):
        process, ready, proceed = self.start_worker("hold")
        _wait_for_file(ready)
        acquired = self.crm.STORE_LOCK.acquire(timeout=0.05)
        if acquired:
            self.crm.STORE_LOCK.release()
        proceed.touch()
        stdout, stderr = process.communicate(timeout=4)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertFalse(acquired, "A process-held transaction must honor timeout")
        with self.crm.STORE_LOCK:
            self.assertEqual(self.crm.store_read()["settings"]["fixture_counter"], 0)


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--crm-lock-worker":
        _lock_worker(*sys.argv[2:])
    else:
        unittest.main()
