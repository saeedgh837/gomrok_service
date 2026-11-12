import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec("release_notes")
        self.assertIsNotNone(spec, "The release summary and durable acknowledgment are missing")
        import release_notes
        self.notes = release_notes
        self.temp = tempfile.TemporaryDirectory(prefix="irica-release-notes-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = {"mode": "standalone", "user": "fixture_user"}

    def snapshot(self, session=None, version="1.21.0"):
        return self.notes.snapshot(self.root, session or self.session, version)

    def acknowledge(self, snapshot=None, session=None, version="1.21.0"):
        result = snapshot or self.snapshot(session, version)
        return self.notes.acknowledge(
            self.root, session or self.session, version,
            {"version": result["notes"]["version"], "notice_id": result["notice_id"]})

    def cloud_session(self, tenant="fixture-tenant", user="fixture-user", api="https://cloud.example/api"):
        import app_paths
        return {"mode": "cloud", "user": "fixture_user", "tenant_id": tenant,
                "user_id": user, "api_base": api,
                "profile_key": app_paths.account_profile_key(api, tenant, user)}

    def test_first_read_is_unseen_and_does_not_create_state(self):
        result = self.snapshot()
        self.assertTrue(result["ok"])
        self.assertFalse(result["seen"])
        self.assertFalse(result["storage_warning"])
        self.assertEqual(result["notes"]["version"], "1.21.0")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_acknowledgment_survives_another_session_and_repeated_reads(self):
        self.acknowledge()
        for session in (dict(self.session), {**self.session, "csrf": "different-fixture"}):
            self.assertTrue(self.snapshot(session)["seen"])
        self.acknowledge()
        markers = list(self.root.rglob("*.json"))
        self.assertEqual(len(markers), 1)
        self.assertLess(markers[0].stat().st_size, 512)

    def test_curated_copy_is_bound_to_installed_version_and_not_mutable(self):
        content = self.notes.notes_for("1.21.0")
        self.assertTrue(content["intro"].strip())
        self.assertGreaterEqual(len(content["sections"]), 2)
        self.assertTrue(all(section["items"] for section in content["sections"]))
        original = copy.deepcopy(content)
        content["sections"][0]["items"][0]["title"] = "changed fixture"
        self.assertEqual(self.notes.notes_for("1.21.0"), original)
        for version in ("1.19.0", "99.0.0", "../1.21.0", None):
            self.assertIsNone(self.notes.notes_for(version))

    def test_unsupported_release_has_no_automatic_notice(self):
        result = self.snapshot(version="1.19.0")
        self.assertIsNone(result["notes"])
        self.assertFalse(result["notice_id"])
        self.assertFalse(result["seen"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_each_local_account_has_independent_acknowledgment(self):
        self.acknowledge()
        for username in ("other_user", "Fixture_user"):
            other = {**self.session, "user": username}
            self.assertFalse(self.snapshot(other)["seen"])
            self.assertNotEqual(self.snapshot()["notice_id"], self.snapshot(other)["notice_id"])

    def test_cloud_namespace_not_display_name_controls_acknowledgment(self):
        account = self.cloud_session()
        self.acknowledge(session=account)
        self.assertTrue(self.snapshot({**account, "user": "renamed_display"})["seen"])
        for other in (self.cloud_session(tenant="other-tenant"),
                      self.cloud_session(user="other-user"),
                      self.cloud_session(api="https://other.example/api"), self.session):
            self.assertFalse(self.snapshot(other)["seen"])

    def test_cloud_trailing_slash_reuses_the_existing_namespace(self):
        self.acknowledge(session=self.cloud_session())
        self.assertTrue(self.snapshot(self.cloud_session(api="https://cloud.example/api/"))["seen"])

    def test_incomplete_or_inconsistent_identity_fails_closed(self):
        invalid = [None, {}, {"mode": "unknown", "user": "fixture_user"},
                   {"mode": "standalone", "user": "../outside"},
                   {"mode": "standalone", "user": "fixture_user\n"}]
        cloud = self.cloud_session()
        invalid.extend({**cloud, field: ""} for field in ("profile_key", "api_base", "tenant_id", "user_id"))
        invalid.append({**cloud, "profile_key": "0" * 64})
        for session in invalid:
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.notes.snapshot(self.root, session, "1.21.0")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stale_account_acknowledgment_cannot_mark_the_new_account(self):
        first = self.snapshot()
        other = {**self.session, "user": "other_user"}
        with self.assertRaises(ValueError):
            self.acknowledge(first, other)
        self.assertFalse(self.snapshot(other)["seen"])
        self.assertFalse(self.snapshot()["seen"])

    def test_acknowledgment_rejects_unknown_fields_forgery_and_old_versions(self):
        initial = self.snapshot()
        valid = {"version": "1.21.0", "notice_id": initial["notice_id"]}
        invalid = [None, [], {}, {"version": "1.21.0"},
                   {**valid, "user": "other_user"}, {**valid, "path": "../outside"},
                   {**valid, "notice_id": "0" * 64}, {**valid, "notice_id": None},
                   {**valid, "notice_id": "نادرست"},
                   {**valid, "version": "1.19.0"}, {**valid, "version": "../../outside"}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.notes.acknowledge(self.root, self.session, "1.21.0", payload)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_new_release_does_not_inherit_old_acknowledgment(self):
        subject = self.notes.subject_from_session(self.session)
        self.notes.mark_seen(self.root, subject, "1.19.0")
        self.assertFalse(self.snapshot()["seen"])
        self.acknowledge()
        self.assertTrue(self.notes.is_seen(self.root, subject, "1.19.0"))
        self.assertTrue(self.snapshot()["seen"])

    def test_concurrent_accounts_and_versions_do_not_lose_markers(self):
        subjects = [self.notes.subject_from_session({**self.session, "user": "fixture_" + str(i)})
                    for i in range(4)]
        work = [(subject, version) for _ in range(20) for subject in subjects
                for version in ("1.19.0", "1.21.0")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda item: self.notes.mark_seen(self.root, *item), work))
        self.assertEqual(len(list(self.root.rglob("*.json"))), 8)
        self.assertEqual(len(list(self.root.rglob("*.tmp"))), 0)
        for subject, version in work:
            self.assertTrue(self.notes.is_seen(self.root, subject, version))

    def test_markers_contain_no_raw_account_session_or_timestamp(self):
        session = {**self.cloud_session(), "csrf": "fixture-csrf", "profile_dir": "private-fixture-path"}
        self.acknowledge(session=session)
        marker = next(self.root.rglob("*.json"))
        text = marker.read_text(encoding="utf-8")
        self.assertEqual(set(json.loads(text)), {"schema", "version", "notice_id"})
        for private in ("fixture_user", "fixture-tenant", "fixture-user", "cloud.example", "fixture-csrf", "private-fixture-path"):
            self.assertNotIn(private, text + str(marker.relative_to(self.root)))

    def test_corrupt_or_mismatched_marker_is_unseen_with_warning(self):
        self.acknowledge()
        marker = next(self.root.rglob("*.json"))
        for content in ("{broken", "[]", "x" * 2048,
                        '{"schema":1,"version":"1.19.0","notice_id":"wrong"}'):
            marker.write_text(content, encoding="utf-8")
            result = self.snapshot()
            self.assertFalse(result["seen"])
            self.assertTrue(result["storage_warning"])
        self.acknowledge()
        self.assertFalse(self.snapshot()["storage_warning"])
        self.assertTrue(self.snapshot()["seen"])

    def test_unreadable_marker_is_nonfatal_with_a_sanitized_warning(self):
        self.acknowledge()
        with patch.object(Path, "open", side_effect=PermissionError("private-fixture-path")):
            result = self.snapshot()
        self.assertFalse(result["seen"])
        self.assertTrue(result["storage_warning"])
        self.assertNotIn("private-fixture-path", json.dumps(result))

    def test_interrupted_atomic_replace_keeps_existing_marker_and_cleans_temp(self):
        self.acknowledge()
        marker = next(self.root.rglob("*.json"))
        before = marker.read_bytes()
        with patch.object(self.notes.os, "replace", side_effect=OSError("private-fixture-path")):
            with self.assertRaises(self.notes.StorageError) as caught:
                self.acknowledge()
        self.assertNotIn("private-fixture-path", str(caught.exception))
        self.assertEqual(marker.read_bytes(), before)
        self.assertTrue(self.snapshot()["seen"])
        self.assertEqual(len(list(self.root.rglob("*.tmp"))), 0)

    def test_unwritable_state_does_not_touch_other_files(self):
        sentinel = self.root / "business-fixture.json"
        sentinel.write_text("fixture", encoding="ascii")
        with patch.object(self.notes.tempfile, "mkstemp", side_effect=PermissionError("private-fixture-path")):
            with self.assertRaises(self.notes.StorageError):
                self.acknowledge()
        self.assertEqual(sentinel.read_text(encoding="ascii"), "fixture")
        self.assertFalse(self.snapshot()["seen"])

    def test_marker_paths_reject_untrusted_subjects_and_release_identifiers(self):
        subject = self.notes.subject_from_session(self.session)
        for bad_subject, bad_version in (("../outside", "1.21.0"), (subject, "../outside"),
                                         ("A" * 64, "1.21.0"), (subject, "01.21.0")):
            with self.subTest(subject=bad_subject, version=bad_version), self.assertRaises(ValueError):
                self.notes.mark_seen(self.root, bad_subject, bad_version)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
