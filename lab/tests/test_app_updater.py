"""Offline behavioral tests for the isolated official-release updater core."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    import app_updater as updater_module
except ModuleNotFoundError as exc:
    if exc.name != "app_updater":
        raise
    updater_module = None


API = "https://api.github.com/repos/saeedgh837/gomrok_service/releases/latest"
BASE = "https://github.com/saeedgh837/gomrok_service/releases/download/v1.19.0/"
NAME = "IRICA-Setup-v1.19.0.exe"
URL = BASE + NAME
PAYLOAD = b"MZ synthetic installer payload, never executed\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class Clock:
    def __init__(self):
        self.value = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class Response:
    def __init__(self, body=b"", status=200, headers=None, on_chunk=None):
        self.body = body
        self.status_code = status
        self.headers = {"Content-Length": str(len(body))}
        if headers is not None:
            self.headers.update(headers)
        self.on_chunk = on_chunk
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            if self.on_chunk:
                self.on_chunk()
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.routes = {}
        self.calls = []
        self.trust_env = True
        self.proxies = {"https": "http://ambient.invalid:1234"}
        self.lock = threading.Lock()

    def get(self, url, **kwargs):
        with self.lock:
            self.calls.append((url, kwargs))
            queue = self.routes.get(url, [])
            if not queue:
                raise AssertionError("Unexpected fixture request: " + url)
            response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else response

    def add(self, url, response):
        self.routes.setdefault(url, []).append(response)



def release():
    return {
        "id": 321,
        "tag_name": "v1.19.0",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/saeedgh837/gomrok_service/releases/tag/v1.19.0",
        "url": "https://api.github.com/repos/saeedgh837/gomrok_service/releases/321",
        "assets": [{
            "id": 654,
            "name": NAME,
            "size": len(PAYLOAD),
            "state": "uploaded",
            "digest": "sha256:" + DIGEST,
            "browser_download_url": URL,
            "url": "https://api.github.com/repos/saeedgh837/gomrok_service/releases/assets/654",
        }],
    }


class AppUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="updater-tests-")
        self.root = Path(self.temp.name)
        self.stage = self.root / "staging"
        self.stage.mkdir()
        self.data = self.root / "data"
        self.data.mkdir()
        self.install = self.root / "install"
        self.install.mkdir()
        self.session = Session()
        self.clock = Clock()
        self.instances = []
        self.callbacks = []

    def tearDown(self):
        for instance in self.instances:
            job = getattr(instance, "_job", None)
            if job is not None and job.thread is not None:
                job.thread.join(3)
        self.temp.cleanup()

    def make(self, **kwargs):
        self.assertIsNotNone(updater_module, "The AppUpdater core is not implemented")
        options = dict(current_version="1.18.3", session=self.session,
                       platform="win32", installable=True, clock=self.clock,
                       installer=self.launched, staging_parent=self.stage,
                       install_dir=self.install, data_dir=self.data)
        options.update(kwargs)
        instance = updater_module.AppUpdater(**options)
        self.instances.append(instance)
        return instance

    def launched(self, payload, offer):
        self.callbacks.append((offer, payload, payload.read_bytes()))
        return {"status": "launched"}

    def metadata(self, document=None, **response_kwargs):
        document = release() if document is None else document
        self.session.add(API, Response(json.dumps(document).encode(), **response_kwargs))

    def available(self, **kwargs):
        self.metadata()
        instance = self.make(**kwargs)
        snapshot = instance.check()
        self.assertEqual(snapshot["state"], "available", snapshot)
        return instance, snapshot["offer"]["offer_id"]

    def wait(self, instance, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = instance.status()
            job = getattr(instance, "_job", None)
            if snapshot["state"] == state and (job is None or job.done.is_set()):
                return snapshot
            threading.Event().wait(0.005)
        self.fail("Updater did not finish in state " + state + ": " + repr(instance.status()))

    def failed_check(self, document, code=None, **response_kwargs):
        self.metadata(document, **response_kwargs)
        instance = self.make()
        result = instance.check()
        self.assertEqual(result["state"], "failed", result)
        self.assertIsNone(result["offer"])
        if code:
            self.assertEqual(result["code"], code)
        return result

    def checksum(self, document, body, name="IRICA-Setup-v1.19.0.sha256", digest=True):
        asset = {"id": 655, "name": name, "size": len(body), "state": "uploaded",
                 "browser_download_url": BASE + name,
                 "url": "https://api.github.com/repos/saeedgh837/gomrok_service/releases/assets/655"}
        if digest:
            asset["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
        document["assets"].append(asset)
        self.session.add(BASE + name, Response(body))
        return asset

    def test_import_and_constructor_do_not_start_network_threads_or_staging(self):
        self.assertIsNotNone(updater_module, "The AppUpdater core is not implemented")
        with patch("requests.Session", side_effect=AssertionError("Unexpected session")), \
                patch("tempfile.mkdtemp", side_effect=AssertionError("Unexpected staging")), \
                patch.object(threading.Thread, "start", side_effect=AssertionError("Unexpected thread")):
            importlib.reload(updater_module)
            instance = updater_module.AppUpdater(current_version="1.18.3")
            self.assertFalse(instance.status()["checked"])

    def test_exact_official_offer_is_immutable_across_browser_snapshots(self):
        instance, offer_id = self.available()
        snapshot = instance.status()
        self.assertEqual(snapshot["offer"], {
            "offer_id": offer_id, "release_id": 321, "asset_id": 654,
            "version": "1.19.0", "name": NAME, "size": len(PAYLOAD),
            "sha256": DIGEST, "url": URL,
        })
        self.assertGreaterEqual(len(offer_id), 32)
        snapshot["offer"]["url"] = "https://attacker.invalid/payload.exe"
        self.assertEqual(instance.status()["offer"]["url"], URL)
        self.assertTrue(instance.status()["checked"])

    def test_equal_release_is_current_but_downgrade_is_a_failure(self):
        document = release()
        document["tag_name"] = "v1.18.3"
        document["html_url"] = document["html_url"].replace("1.19.0", "1.18.3")
        self.metadata(document)
        instance = self.make()
        self.assertEqual(instance.check()["state"], "current")
        self.assertIsNone(instance.status()["offer"])
        self.metadata()
        newer = self.make(current_version="1.20.0")
        self.assertEqual(newer.check()["code"], "downgrade")
        self.assertEqual(newer.status()["state"], "failed")

    def test_semver_comparison_is_numeric(self):
        self.metadata()
        self.assertEqual(self.make(current_version="1.9.99").check()["state"], "available")

    def test_malformed_current_version_is_rejected_without_io(self):
        for version in ("v1.18.3", "01.18.3", "1.18", "1.18.3-dev", " 1.18.3", None):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    self.make(current_version=version)
        self.assertEqual(self.session.calls, [])

    def test_only_exact_stable_numeric_tags_are_accepted(self):
        for tag in ("1.19.0", "v1.19", "v01.19.0", "v1.19.0-rc1", "v1.19.0+build", " v1.19.0", "v1.19.-1"):
            with self.subTest(tag=tag):
                document = release()
                document["tag_name"] = tag
                self.failed_check(document)

    def test_draft_prerelease_or_unclassified_releases_fail_closed(self):
        for field, value in (("draft", True), ("prerelease", True), ("draft", None), ("prerelease", 0)):
            with self.subTest(field=field, value=value):
                document = release()
                document[field] = value
                self.failed_check(document)

    def test_release_metadata_must_agree_with_its_official_tag_and_id(self):
        for field, value in (("id", True), ("id", -1),
                             ("html_url", "https://github.com/other/repo/releases/tag/v1.19.0"),
                             ("html_url", "https://github.com/saeedgh837/gomrok_service/releases/tag/v1.18.3"),
                             ("url", "https://api.github.com/repos/saeedgh837/gomrok_service/releases/999")):
            with self.subTest(field=field):
                document = release()
                document[field] = value
                self.failed_check(document)

    def test_installer_name_must_be_exact_and_unique(self):
        for variant in ("wrong", "missing", "duplicate"):
            with self.subTest(variant=variant):
                document = release()
                if variant == "wrong":
                    document["assets"][0]["name"] = "IRICA-Setup-v1.18.3.exe"
                elif variant == "missing":
                    document["assets"] = []
                else:
                    document["assets"].append(dict(document["assets"][0]))
                self.failed_check(document)

    def test_asset_digest_is_mandatory_exact_sha256(self):
        for digest in (None, "", "sha512:" + DIGEST, "sha256:" + "g" * 64,
                       "sha256:" + DIGEST[:-1], "sha256:" + DIGEST + " "):
            with self.subTest(digest=digest):
                document = release()
                document["assets"][0]["digest"] = digest
                self.failed_check(document)

    def test_asset_size_id_and_state_are_strictly_validated(self):
        for field, value in (("size", 0), ("size", -1), ("size", True), ("size", "42"),
                             ("size", 256 * 1024 * 1024 + 1), ("size", 42.5),
                             ("id", False), ("state", "uploading")):
            with self.subTest(field=field, value=value):
                document = release()
                document["assets"][0][field] = value
                self.failed_check(document)

    def test_initial_asset_urls_cannot_select_another_repo_version_or_transport(self):
        for url in (URL.replace("https:", "http:"), URL.replace("github.com", "github.com.evil.invalid"),
                    URL.replace("github.com", "user@github.com"), URL.replace("github.com", "github.com:443"),
                    URL.replace("saeedgh837/gomrok_service", "other/repo"), URL.replace("v1.19.0/", "v1.18.3/"),
                    URL + "?download=1", URL + "#fragment", URL.replace("github.com", "api.github.com")):
            with self.subTest(url=url):
                document = release()
                document["assets"][0]["browser_download_url"] = url
                self.failed_check(document)
        self.assertTrue(all(url == API for url, _ in self.session.calls))

    def test_supplied_versioned_checksums_are_verified(self):
        for name in ("IRICA-Setup-v1.19.0.sha256", "SHA256SUMS_v1.19.0.txt"):
            with self.subTest(name=name):
                document = release()
                self.checksum(document, (DIGEST + "  " + NAME + "\n").encode(), name)
                self.metadata(document)
                self.assertEqual(self.make().check()["state"], "available")

    def test_conflicting_missing_duplicate_or_malformed_checksum_entries_fail(self):
        bodies = [("0" * 64 + "  " + NAME + "\n").encode(),
                  (DIGEST + "  another.exe\n").encode(),
                  ((DIGEST + "  " + NAME + "\n") * 2).encode(),
                  ("not-a-digest  " + NAME + "\n").encode()]
        for body in bodies:
            with self.subTest(body=body):
                document = release()
                self.checksum(document, body)
                self.failed_check(document)

    def test_checksum_asset_digest_and_declared_size_also_fail_closed(self):
        for variant in ("missing_digest", "wrong_digest", "wrong_size"):
            with self.subTest(variant=variant):
                document = release()
                asset = self.checksum(document, (DIGEST + " *" + NAME + "\n").encode(),
                                      digest=variant != "missing_digest")
                if variant == "wrong_digest":
                    asset["digest"] = "sha256:" + "0" * 64
                if variant == "wrong_size":
                    asset["size"] += 1
                self.failed_check(document)

    def test_automatic_cache_is_900_seconds_and_manual_minimum_is_10(self):
        instance, original_id = self.available()
        self.metadata()
        for force, elapsed in ((False, 0), (True, 9.99)):
            self.clock.advance(elapsed)
            self.assertEqual(instance.check(force)["offer"]["offer_id"], original_id)
        self.assertEqual(len(self.session.calls), 1)
        self.clock.advance(0.01)
        changed = instance.check(force=True)
        self.assertNotEqual(changed["offer"]["offer_id"], original_id)
        self.clock.advance(899.99)
        instance.check()
        self.assertEqual(len(self.session.calls), 2)
        self.metadata()
        self.clock.advance(0.01)
        instance.check()
        self.assertEqual(len(self.session.calls), 3)
        self.assertEqual(instance.confirm(original_id)["code"], "invalid_offer")

    def test_errors_remain_failures_during_cache_and_hide_exception_details(self):
        self.session.add(API, OSError("secret-token and local C:/private/data"))
        instance = self.make()
        first = instance.check()
        self.assertEqual(first["state"], "failed")
        self.assertEqual(instance.check()["state"], "failed")
        self.assertNotIn("secret-token", json.dumps(first))
        self.assertNotIn("private/data", json.dumps(first))
        self.assertEqual(len(self.session.calls), 1)

    def test_http_uses_no_environment_proxy_tls_bypass_or_automatic_redirects(self):
        explicit = {"https": "http://fixture-proxy.invalid:18080"}
        instance, _ = self.available(proxies=explicit)
        explicit["https"] = "http://changed.invalid:19090"
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.proxies, {})
        for _, options in self.session.calls:
            self.assertIs(options["allow_redirects"], False)
            self.assertIs(options["verify"], True)
            self.assertIs(options["stream"], True)
            self.assertEqual(options["proxies"], {"https": "http://fixture-proxy.invalid:18080"})
            self.assertEqual(options["headers"]["Accept-Encoding"], "identity")
            self.assertGreater(options["timeout"][0], 0)
            self.assertLessEqual(options["timeout"][0], 5)
            self.assertLessEqual(options["timeout"][1], 10)
        self.assertEqual(instance.status()["state"], "available")

    def test_proxy_provider_is_lazy_for_constructor_status_invalid_offer_and_cached_check(self):
        samples = []

        def provider():
            samples.append(True)
            return {"https": "http://route-a.invalid:18080"}

        instance = self.make(proxy_provider=provider)
        self.assertFalse(instance.status()["checked"])
        self.assertEqual(instance.confirm("invalid")["code"], "invalid_offer")
        self.assertEqual(samples, [])
        self.assertEqual(self.session.calls, [])
        self.metadata()
        self.assertEqual(instance.check()["state"], "available")
        instance.status()
        instance.check()
        instance.check(force=True)
        self.assertEqual(len(samples), 1)
        self.assertEqual(len(self.session.calls), 1)

    def test_saved_proxy_changes_are_observed_by_each_new_metadata_job(self):
        route = {"https": "http://route-a.invalid:18080"}
        samples = []

        def provider():
            samples.append(True)
            return route

        instance, _ = self.available(proxy_provider=provider)
        route["https"] = "http://route-b.invalid:19090"
        self.clock.advance(10)
        self.metadata()
        self.assertEqual(instance.check(force=True)["state"], "available")
        self.assertEqual(len(samples), 2)
        self.assertEqual([options["proxies"] for _, options in self.session.calls], [
            {"https": "http://route-a.invalid:18080"}, {"https": "http://route-b.invalid:19090"}])

    def test_metadata_job_detaches_proxy_route_across_redirect_and_checksum_requests(self):
        route = {"https": "http://route-a.invalid:18080"}
        samples = []
        entered, finish = threading.Event(), threading.Event()
        destination = "https://api.github.com/repos/saeedgh837/gomrok_service/releases/321"
        document = release()
        self.checksum(document, (DIGEST + "  " + NAME + "\n").encode())

        def provider():
            samples.append(True)
            return route

        def blocked_redirect():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture metadata request was not released")
            return Response(status=302, headers={"Location": destination})

        self.session.add(API, blocked_redirect)
        self.session.add(destination, Response(json.dumps(document).encode()))
        instance = self.make(proxy_provider=provider)
        caller = threading.Thread(target=instance.check)
        caller.start()
        try:
            self.assertTrue(entered.wait(1))
            route["https"] = "http://changed-during-job.invalid:19090"
            self.assertEqual(instance.check(force=True)["state"], "checking")
        finally:
            finish.set()
            caller.join(2)
        self.assertFalse(caller.is_alive())
        self.assertEqual(instance.status()["state"], "available")
        self.assertEqual(len(samples), 1)
        self.assertEqual(len(self.session.calls), 3)
        self.assertTrue(all(options["proxies"] == {"https": "http://route-a.invalid:18080"}
                            for _, options in self.session.calls))

    def test_confirmed_download_observes_new_saved_route_without_replacing_consent(self):
        route = {"https": "http://route-a.invalid:18080"}
        samples = []

        def provider():
            samples.append(True)
            return route

        instance, offer_id = self.available(proxy_provider=provider)
        binding = instance.status()["offer"]
        route["https"] = "http://route-b.invalid:19090"
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        result = self.wait(instance, "installing")
        self.assertEqual(result["offer"], binding)
        self.assertEqual(self.callbacks[0][0].offer_id, offer_id)
        self.assertEqual(self.callbacks[0][2], PAYLOAD)
        self.assertEqual(len(samples), 2)
        self.assertEqual([options["proxies"] for _, options in self.session.calls], [
            {"https": "http://route-a.invalid:18080"}, {"https": "http://route-b.invalid:19090"}])

    def test_download_route_snapshot_survives_redirect_and_retained_payload_retry(self):
        route = {"https": "http://route-a.invalid:18080"}
        samples = []
        entered, finish = threading.Event(), threading.Event()
        destination = "https://release-assets.githubusercontent.com/fixture"
        callbacks = iter(("busy", "launched"))

        def provider():
            samples.append(True)
            return route

        def installer(payload, offer):
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": next(callbacks)}

        def blocked_redirect():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture download request was not released")
            return Response(status=302, headers={"Location": destination})

        instance, offer_id = self.available(proxy_provider=provider, installer=installer)
        route["https"] = "http://route-b.invalid:19090"
        self.session.add(URL, blocked_redirect)
        self.session.add(destination, Response(PAYLOAD))
        instance.confirm(offer_id)
        try:
            self.assertTrue(entered.wait(1))
            route["https"] = "http://changed-during-download.invalid:20000"
            self.assertEqual(instance.confirm(offer_id)["state"], "downloading")
        finally:
            finish.set()
        self.wait(instance, "waiting_for_idle")
        self.assertEqual(len(samples), 2)
        self.assertTrue(all(options["proxies"] == {"https": "http://route-b.invalid:19090"}
                            for url, options in self.session.calls if url != API))
        instance.confirm(offer_id)
        self.wait(instance, "installing")
        self.assertEqual(len(samples), 2)
        self.assertEqual(len(self.session.calls), 3)
        self.assertEqual(len(self.callbacks), 2)

    def test_empty_provider_mapping_remains_direct_and_does_not_fall_back(self):
        instance, _ = self.available(proxies={"https": "http://fallback.invalid:1234"},
                                     proxy_provider=lambda: {})
        self.assertEqual(instance.status()["state"], "available")
        self.assertEqual(self.session.calls[0][1]["proxies"], {})
        self.assertFalse(self.session.trust_env)

    def test_invalid_or_raising_metadata_proxy_provider_fails_without_http_or_leaks(self):
        values = [None, [], "http://private-route.invalid", {"ftp": "private-route"},
                  {"https": None}, {"https": ""}, {"https": 123},
                  OSError("private-provider-token C:/private/provider-settings")]
        for value in values:
            with self.subTest(value=value):
                def provider():
                    if isinstance(value, Exception):
                        raise value
                    return value

                instance = self.make(proxy_provider=provider,
                                     proxies={"https": "http://must-not-fallback.invalid:1234"})
                result = instance.check()
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["code"], "metadata_failed")
                self.assertIsNone(result["offer"])
                self.assertNotIn("private-provider-token", json.dumps(result))
                self.assertNotIn("provider-settings", json.dumps(result))
                self.assertEqual(self.session.calls, [])

    def test_download_proxy_provider_failure_preserves_offer_and_never_downloads(self):
        route = {"https": "http://route-a.invalid:18080"}

        def provider():
            if isinstance(route, Exception):
                raise route
            return route

        instance, offer_id = self.available(proxy_provider=provider)
        binding = instance.status()["offer"]
        route = OSError("private-provider-token")
        instance.confirm(offer_id)
        result = self.wait(instance, "failed")
        self.assertEqual(result["code"], "download_failed")
        self.assertEqual(result["offer"], binding)
        self.assertNotIn("private-provider-token", json.dumps(result))
        self.assertEqual(self.callbacks, [])
        self.assertEqual([url for url, _ in self.session.calls], [API])
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_proxy_provider_deadline_is_rechecked_before_any_transport(self):
        def provider():
            self.clock.advance(21)
            return {"https": "http://too-late.invalid:18080"}

        instance = self.make(proxy_provider=provider)
        result = instance.check()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["code"], "timeout")
        self.assertEqual(self.session.calls, [])

    def test_proxy_provider_cancellation_blocks_late_transport_and_overlapping_jobs(self):
        entered, finish = threading.Event(), threading.Event()
        samples = []

        def provider():
            samples.append(True)
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture route provider was not released")
            return {"https": "http://cancelled-route.invalid:18080"}

        instance = self.make(proxy_provider=provider)
        with patch.object(updater_module, "_META_SECONDS", 0.05):
            try:
                result = instance.check()
                self.assertTrue(entered.is_set())
                self.assertEqual(result["code"], "timeout")
                self.clock.advance(1000)
                self.assertEqual(instance.check(force=True)["code"], "timeout")
                self.assertEqual(len(samples), 1)
                self.assertEqual(self.session.calls, [])
            finally:
                finish.set()
            job = instance._job
            if job is not None:
                job.thread.join(2)
        self.assertEqual(instance.status()["code"], "timeout")
        self.assertEqual(self.session.calls, [])

    def test_metadata_status_encoding_and_declared_lengths_are_enforced(self):
        for status, headers in ((201, {}), (403, {}), (304, {}),
                                (200, {"Content-Encoding": "gzip"}),
                                (200, {"Content-Length": "1"}),
                                (200, {"Content-Length": "-1"}),
                                (200, {"Content-Length": "1, 2"}),
                                (200, {"Content-Length": str(1024 * 1024 + 1)})):
            with self.subTest(status=status, headers=headers):
                self.failed_check(release(), status=status, headers=headers)

    def test_streamed_metadata_is_capped_even_without_content_length(self):
        response = Response(b" " * (1024 * 1024 + 1))
        response.headers.clear()
        self.session.add(API, response)
        instance = self.make()
        self.assertEqual(instance.check()["state"], "failed")
        self.assertTrue(response.closed)

    def test_metadata_accepts_bounded_identity_stream_without_length(self):
        response = Response(json.dumps(release()).encode())
        response.headers.clear()
        self.session.add(API, response)
        self.assertEqual(self.make().check()["state"], "available")

    def test_malformed_json_or_duplicate_keys_cannot_become_current(self):
        for body in (b"null", b"[]", b"{", b'{"id":321,"id":322}', b"{\"id\":NaN}"):
            with self.subTest(body=body):
                self.session.add(API, Response(body))
                self.assertEqual(self.make().check()["state"], "failed")

    def test_metadata_has_a_monotonic_total_budget(self):
        self.metadata(on_chunk=lambda: self.clock.advance(21))
        instance = self.make()
        self.assertEqual(instance.check()["code"], "timeout")
        self.assertEqual(instance.status()["state"], "failed")

    def test_metadata_single_flight_publishes_checking_and_never_overlaps(self):
        entered, release_request = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not release_request.wait(2):
                raise AssertionError("Fixture was not released")
            return Response(json.dumps(release()).encode())

        self.session.add(API, blocked)
        instance = self.make()
        caller = threading.Thread(target=instance.check)
        caller.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(instance.status()["state"], "checking")
            self.assertEqual(instance.check(force=True)["state"], "checking")
            self.assertEqual(instance.confirm("unknown")["code"], "invalid_offer")
            self.assertEqual(instance.status()["state"], "checking")
            self.assertEqual(len(self.session.calls), 1)
        finally:
            release_request.set()
            caller.join(3)
        self.assertEqual(instance.status()["state"], "available")

    def test_metadata_redirect_is_validated_and_closed_before_following(self):
        destination = "https://api.github.com/repos/saeedgh837/gomrok_service/releases/321"
        first = Response(status=302, headers={"Location": destination})
        self.session.add(API, first)
        self.session.add(destination, Response(json.dumps(release()).encode()))
        self.assertEqual(self.make().check()["state"], "available")
        self.assertTrue(first.closed)
        self.assertEqual([url for url, _ in self.session.calls], [API, destination])

    def test_invalid_metadata_redirects_never_reach_the_destination(self):
        for location in ("http://github.com/", "https://github.com.evil.invalid/", "https://user@github.com/",
                         "https://api.github.com:443/repos/saeedgh837/gomrok_service/releases/latest",
                         "https://api.github.com/repos/other/repo/releases/latest", "https://github.com/other/repo"):
            with self.subTest(location=location):
                self.session.add(API, Response(status=302, headers={"Location": location}))
                before = len(self.session.calls)
                self.assertEqual(self.make().check()["state"], "failed")
                self.assertEqual(len(self.session.calls), before + 1)

    def test_non_windows_source_or_missing_callback_requires_manual_install(self):
        for options in ({"platform": "linux"}, {"installable": False}, {"installer": None}):
            with self.subTest(options=options):
                instance, offer_id = self.available(**options)
                self.assertEqual(instance.confirm(offer_id)["code"], "manual_install_required")
                self.assertEqual(instance.confirm(offer_id)["code"], "manual_install_required")
        self.assertEqual(self.callbacks, [])
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assertTrue(all(url == API for url, _ in self.session.calls))

    def test_confirm_accepts_only_the_current_opaque_offer_id(self):
        instance, _ = self.available()
        for supplied in (None, "", "random-id", URL, {"url": URL, "sha256": DIGEST}, [URL]):
            with self.subTest(supplied=supplied):
                self.assertEqual(instance.confirm(supplied)["code"], "invalid_offer")
                self.assertEqual(instance.status()["state"], "available")
        self.assertEqual(len(self.session.calls), 1)

    def test_confirm_is_prompt_single_flight_and_duplicate_launch_is_idempotent(self):
        instance, offer_id = self.available()
        entered, finish = threading.Event(), threading.Event()

        def wait_for_fixture():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture download was not released")

        self.session.add(URL, Response(PAYLOAD, on_chunk=wait_for_fixture))
        started = time.monotonic()
        instance.confirm(offer_id)
        self.assertLess(time.monotonic() - started, 0.5)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(instance.status()["state"], "downloading")
            self.assertEqual(instance.confirm(offer_id)["state"], "downloading")
            self.assertEqual(instance.check(force=True)["state"], "downloading")
        finally:
            finish.set()
        result = self.wait(instance, "installing")
        self.assertEqual(result["code"], "launched")
        self.assertEqual(result["downloaded"], len(PAYLOAD))
        self.assertEqual(instance.confirm(offer_id)["state"], "installing")
        self.assertEqual(len(self.callbacks), 1)
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)
        self.assertEqual(self.callbacks[0][2], PAYLOAD)

    def test_callback_receives_frozen_binding_and_locked_verified_payload(self):
        observations = []

        def installer(payload, offer):
            observations.append(offer)
            self.assertEqual(offer.sha256, DIGEST)
            self.assertEqual(offer.release_id, 321)
            self.assertEqual(payload.read_bytes(), PAYLOAD)
            self.assertEqual(instance.status()["state"], "verifying")
            with self.assertRaises((AttributeError, TypeError)):
                offer.url = "https://attacker.invalid/replacement.exe"
            if os.name == "nt":
                with self.assertRaises(OSError):
                    payload.write_bytes(b"substitution")
                with self.assertRaises(OSError):
                    payload.unlink()
            return {"status": "launched"}

        instance, offer_id = self.available(installer=installer)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "installing")
        self.assertEqual(len(observations), 1)

    def test_download_digest_mismatch_never_calls_installer_and_cleans_staging(self):
        instance, offer_id = self.available()
        self.session.add(URL, Response(b"X" * len(PAYLOAD)))
        instance.confirm(offer_id)
        self.wait(instance, "failed")
        self.assertEqual(self.callbacks, [])
        self.assertEqual(list(self.stage.iterdir()), [])
        instance.confirm(offer_id)
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)

    def test_download_requires_exact_declared_and_streamed_size(self):
        for variant in ("short", "long", "declared_short", "missing_length", "chunked", "gzip", "partial"):
            with self.subTest(variant=variant):
                instance, offer_id = self.available()
                body, headers, status = PAYLOAD, {}, 200
                if variant == "short":
                    body, headers = PAYLOAD[:-1], {"Content-Length": str(len(PAYLOAD))}
                elif variant == "long":
                    body, headers = PAYLOAD + b"X", {"Content-Length": str(len(PAYLOAD))}
                elif variant == "declared_short":
                    headers = {"Content-Length": "1"}
                elif variant == "chunked":
                    headers = {"Transfer-Encoding": "chunked"}
                elif variant == "gzip":
                    headers = {"Content-Encoding": "gzip"}
                elif variant == "partial":
                    status = 206
                response = Response(body, headers=headers, status=status)
                if variant == "missing_length":
                    response.headers.clear()
                self.session.add(URL, response)
                instance.confirm(offer_id)
                self.wait(instance, "failed")
        self.assertEqual(self.callbacks, [])
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_download_total_budget_rejects_a_late_chunk(self):
        instance, offer_id = self.available()
        self.session.add(URL, Response(PAYLOAD, on_chunk=lambda: self.clock.advance(301)))
        instance.confirm(offer_id)
        self.assertEqual(self.wait(instance, "failed")["code"], "timeout")
        self.assertEqual(self.callbacks, [])

    def test_each_download_redirect_is_validated_and_three_hops_are_allowed(self):
        instance, offer_id = self.available()
        destinations = ["https://api.github.com/repos/saeedgh837/gomrok_service/releases/assets/654",
                        "https://release-assets.githubusercontent.com/github-production-release-asset/fixture?sig=fixture",
                        "https://objects.githubusercontent.com/github-production-release-asset/fixture?sig=fixture"]
        redirects = []
        for source, destination in zip([URL] + destinations, destinations):
            response = Response(status=302, headers={"Location": destination})
            redirects.append(response)
            self.session.add(source, response)
        self.session.add(destinations[-1], Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "installing")
        self.assertTrue(all(response.closed for response in redirects))
        self.assertEqual(len(self.callbacks), 1)
        self.assertTrue(all(options["allow_redirects"] is False for _, options in self.session.calls))

    def test_a_fourth_redirect_is_not_followed(self):
        instance, offer_id = self.available()
        sources = [URL] + ["https://release-assets.githubusercontent.com/fixture" + str(n) for n in range(4)]
        for source, destination in zip(sources, sources[1:]):
            self.session.add(source, Response(status=302, headers={"Location": destination}))
        instance.confirm(offer_id)
        self.wait(instance, "failed")
        self.assertNotIn(sources[-1], [url for url, _ in self.session.calls])
        self.assertEqual(self.callbacks, [])

    def test_an_unsafe_redirect_after_a_trusted_hop_is_not_followed(self):
        for destination in ("https://evil.invalid/payload", "http://objects.githubusercontent.com/payload",
                            "https://user@release-assets.githubusercontent.com/payload",
                            "https://objects.githubusercontent.com:444/payload", "https://127.0.0.1/payload"):
            with self.subTest(destination=destination):
                instance, offer_id = self.available()
                trusted = "https://release-assets.githubusercontent.com/fixture"
                self.session.add(URL, Response(status=302, headers={"Location": trusted}))
                self.session.add(trusted, Response(status=302, headers={"Location": destination}))
                instance.confirm(offer_id)
                self.wait(instance, "failed")
                self.assertNotIn(destination, [url for url, _ in self.session.calls])
        self.assertEqual(self.callbacks, [])

    def test_busy_and_blocked_callbacks_retry_without_redownloading(self):
        results = iter(("busy", "blocked", "launched"))

        def installer(payload, offer):
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": next(results)}

        instance, offer_id = self.available(installer=installer)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.assertEqual(self.wait(instance, "waiting_for_idle")["code"], "busy")
        self.assertEqual(instance.check(force=True)["state"], "waiting_for_idle")
        instance.confirm(offer_id)
        self.assertEqual(self.wait(instance, "waiting_for_idle")["code"], "blocked")
        instance.confirm(offer_id)
        self.wait(instance, "installing")
        self.assertEqual(len(self.callbacks), 3)
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)
        self.assertEqual(len({record[1] for record in self.callbacks}), 1)

    def test_waiting_payload_is_rehashed_before_every_callback_retry(self):
        def busy(payload, offer):
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": "busy"}

        instance, offer_id = self.available(installer=busy)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "waiting_for_idle")
        self.callbacks[0][1].write_bytes(b"X" * len(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "failed")
        self.assertEqual(len(self.callbacks), 1)
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_first_handoff_rehash_detects_substitution_after_stream_verification(self):
        instance, offer_id = self.available()
        self.session.add(URL, Response(PAYLOAD))
        original = updater_module._verification_handle

        def substitute(path):
            path.write_bytes(b"X" * len(PAYLOAD))
            return original(path)

        with patch.object(updater_module, "_verification_handle", side_effect=substitute):
            instance.confirm(offer_id)
            self.wait(instance, "failed")
        self.assertEqual(self.callbacks, [])

    def test_callback_failure_or_unknown_result_is_terminal_and_sanitized(self):
        for result in ({"status": "failed", "message": "secret-private-path"},
                       {"status": "success"}, {"status": []}, {"status": {}},
                       None, "launched", OSError("secret-private-path")):
            with self.subTest(result=result):
                def installer(payload, offer):
                    self.callbacks.append((offer, payload, b""))
                    if isinstance(result, Exception):
                        raise result
                    return result

                instance, offer_id = self.available(installer=installer)
                self.session.add(URL, Response(PAYLOAD))
                instance.confirm(offer_id)
                final = self.wait(instance, "failed")
                self.assertEqual(final["code"], "installer_failed")
                self.assertNotIn("secret-private-path", json.dumps(final))
                before = len(self.callbacks)
                instance.confirm(offer_id)
                self.assertEqual(len(self.callbacks), before)
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_staging_under_install_or_data_roots_is_rejected(self):
        for parent in (self.install, self.data, self.data / "nested"):
            parent.mkdir(exist_ok=True)
            with self.subTest(parent=parent):
                instance, offer_id = self.available(staging_parent=parent)
                instance.confirm(offer_id)
                self.assertEqual(self.wait(instance, "failed")["code"], "unsafe_staging")
        self.assertEqual(self.callbacks, [])
        self.assertTrue(all(url == API for url, _ in self.session.calls))

    def test_payload_is_staged_outside_install_and_data_with_no_path_in_status(self):
        instance, offer_id = self.available()
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        final = self.wait(instance, "installing")
        payload_path = self.callbacks[0][1]
        self.assertEqual(payload_path.name, NAME)
        self.assertTrue(payload_path.is_relative_to(self.stage))
        self.assertFalse(payload_path.is_relative_to(self.install))
        self.assertFalse(payload_path.is_relative_to(self.data))
        self.assertNotIn(str(payload_path), json.dumps(final))
        if os.name != "nt":
            self.assertEqual(payload_path.parent.stat().st_mode & 0o777, 0o700)
    def test_non_ascii_confirmation_is_rejected_without_raising(self):
        instance, offer_id = self.available()
        result = instance.confirm("é" * len(offer_id))
        self.assertEqual(result["code"], "invalid_offer")
        self.assertEqual(instance.status()["state"], "available")

    def test_missing_known_install_or_data_root_requires_manual_install(self):
        for options in ({"install_dir": None}, {"data_dir": None}):
            with self.subTest(options=options):
                instance, offer_id = self.available(**options)
                self.assertEqual(instance.confirm(offer_id)["code"], "manual_install_required")
        self.assertTrue(all(url == API for url, _ in self.session.calls))

    def test_redirect_path_traversal_is_rejected_before_request_preparation(self):
        paths = ["https://api.github.com/repos/saeedgh837/gomrok_service/releases/%2e%2e/other",
                 "https://api.github.com/repos/saeedgh837/gomrok_service/releases/../../other",
                 "https://github.com/saeedgh837/gomrok_service/releases/download/v1.19.0/%2fother",
                 "https://github.com/saeedgh837/gomrok_service/releases/download/../../../../other/repo"]
        for destination in paths:
            with self.subTest(destination=destination):
                self.session.add(API, Response(status=302, headers={"Location": destination}))
                before = len(self.session.calls)
                self.assertEqual(self.make().check()["state"], "failed")
                self.assertEqual(len(self.session.calls), before + 1)

    def test_injected_http_session_cannot_reuse_ambient_client_credentials(self):
        self.session.cert = "private-client-cert.pem"
        self.session.auth = ("private-user", "private-token")
        self.session.cookies = {"session": "private-cookie"}
        self.session.params = {"access_token": "private-token"}
        self.available()
        self.assertIsNone(self.session.cert)
        self.assertIsNone(self.session.auth)
        self.assertEqual(self.session.cookies, {})
        self.assertEqual(self.session.params, {})
        headers = self.session.calls[0][1]["headers"]
        self.assertIsNone(headers["Authorization"])
        self.assertIsNone(headers["Cookie"])
        self.assertIsNone(headers["Proxy-Authorization"])

    def test_metadata_watchdog_returns_failure_without_allowing_overlapping_or_late_results(self):
        entered, finish = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture request was not released")
            return Response(json.dumps(release()).encode())

        self.session.add(API, blocked)
        instance = self.make()
        with patch.object(updater_module, "_META_SECONDS", 0.05):
            started = time.monotonic()
            try:
                result = instance.check()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(entered.is_set())
                self.assertEqual(result["code"], "timeout")
                self.clock.advance(1000)
                self.assertEqual(instance.check(force=True)["state"], "failed")
                self.assertEqual(len(self.session.calls), 1)
            finally:
                finish.set()
            job = instance._job
            if job is not None:
                job.thread.join(2)
            self.assertEqual(instance.status()["state"], "failed")
        self.assertIsNone(instance.status()["offer"])

    def test_download_watchdog_fails_closed_and_releases_private_staging(self):
        entered, finish = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture response was not released")

        instance, offer_id = self.available()
        self.session.add(URL, Response(PAYLOAD, on_chunk=blocked))
        with patch.object(updater_module, "_DOWNLOAD_SECONDS", 0.05):
            instance.confirm(offer_id)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.wait(instance, "failed")["code"], "timeout")
                self.assertEqual(instance.confirm(offer_id)["state"], "failed")
            finally:
                finish.set()
            job = instance._job
            if job is not None:
                job.thread.join(2)
        self.assertEqual(self.callbacks, [])
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_response_cleanup_failures_are_not_silently_swallowed(self):
        entered, finish = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Fixture response was not released")

        class UncloseableResponse(Response):
            def close(self):
                raise OSError("private transport cleanup detail")

        self.session.add(API, UncloseableResponse(json.dumps(release()).encode(), on_chunk=blocked))
        instance = self.make()
        with patch.object(updater_module, "_META_SECONDS", 0.05):
            try:
                instance.check()
                self.assertTrue(entered.is_set())
            finally:
                finish.set()
            job = instance._job
            if job is not None:
                job.thread.join(2)
        result = instance.status()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["cleanup_pending"])
        self.assertEqual(result["code"], "cleanup_failed")
        self.assertNotIn("private transport", json.dumps(result))
        self.assertEqual(instance.check(force=True)["state"], "failed")
        self.assertEqual(len(self.session.calls), 1)

    def test_failed_staging_cleanup_prevents_accumulating_new_downloads(self):
        instance, offer_id = self.available()
        self.session.add(URL, Response(b"X" * len(PAYLOAD)))
        with patch.object(updater_module.shutil, "rmtree", side_effect=OSError("private cleanup detail")):
            instance.confirm(offer_id)
            result = self.wait(instance, "failed")
        self.assertEqual(result["code"], "cleanup_failed")
        self.assertTrue(result["cleanup_pending"])
        self.clock.advance(1000)
        instance.check(force=True)
        instance.confirm(offer_id)
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)
        self.assertNotIn("private cleanup", json.dumps(result))
        self.assertEqual(self.callbacks, [])

    def test_caller_winning_metadata_expiry_never_waits_for_blocking_response_close(self):
        reading, finish_read, closing, finish_close, returned = (threading.Event() for _ in range(5))
        close_calls = []

        class CallerWinsTimer:
            def __init__(self, *args, **kwargs):
                self.daemon = True

            def start(self):
                pass

            def cancel(self):
                pass

        def blocked_read():
            reading.set()
            if not finish_read.wait(2):
                raise AssertionError("Fixture read was not released")

        class BlockingCloseResponse(Response):
            def close(self):
                close_calls.append(True)
                if len(close_calls) == 1:
                    closing.set()
                    if not finish_close.wait(2):
                        raise AssertionError("Fixture close was not released")
                self.closed = True

        self.session.add(API, BlockingCloseResponse(json.dumps(release()).encode(), on_chunk=blocked_read))
        instance = self.make()
        result = []

        def check():
            try:
                result.append(instance.check())
            finally:
                returned.set()

        caller = threading.Thread(target=check)
        job = None
        with patch.object(updater_module, "_META_SECONDS", 0.05), \
                patch.object(updater_module.threading, "Timer", CallerWinsTimer):
            caller.start()
            try:
                self.assertTrue(reading.wait(1))
                job = instance._job
                self.assertTrue(returned.wait(0.5), "Caller timeout blocked in transport cleanup")
                self.assertEqual(result[0]["code"], "timeout")
                self.assertTrue(closing.wait(1))
                finish_read.set()
                job.thread.join(1)
                self.assertFalse(job.thread.is_alive())
                self.clock.advance(1000)
                self.assertEqual(instance.check(force=True)["code"], "timeout")
                self.assertEqual(len(self.session.calls), 1)
                self.assertIs(instance._job, job)
            finally:
                finish_read.set()
                finish_close.set()
                caller.join(2)
                if job is not None:
                    job.thread.join(2)
                    closer = getattr(job, "cleanup_thread", None)
                    if closer is not None:
                        closer.join(2)
        self.assertFalse(caller.is_alive())
        self.assertIsNone(instance._job)
        self.assertIsNone(instance.status()["offer"])

    def _create_scratch_junction(self, path, target):
        import ctypes
        import struct
        from ctypes import wintypes

        self.assertTrue(path.is_relative_to(self.root))
        self.assertTrue(target.is_relative_to(self.root))
        path.mkdir()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        display = str(target).encode("utf-16-le")
        names = substitute + b"\0\0" + display + b"\0\0"
        reparse = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(names), 0,
                              0, len(substitute), len(substitute) + 2, len(display)) + names
        buffer = ctypes.create_string_buffer(reparse)
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            returned_bytes = wintypes.DWORD()
            if not kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(reparse),
                                          None, 0, ctypes.byref(returned_bytes), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)

    def test_windows_junction_substituted_waiting_stage_cannot_reach_callback(self):
        if os.name != "nt":
            self.skipTest("Windows native reparse-point behavior")

        def busy(payload, offer):
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": "busy"}

        instance, offer_id = self.available(installer=busy)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "waiting_for_idle")
        stage = self.callbacks[0][1].parent
        retained = stage.with_name("retained-real-stage")
        stage.rename(retained)
        self._create_scratch_junction(stage, retained)
        self.assertTrue(stage.lstat().st_file_attributes & 0x400)
        with self.assertRaises(OSError):
            with updater_module._verification_handle(stage / NAME):
                self.fail("A junction was accepted as the verification directory")
        with patch.object(updater_module.shutil, "rmtree", return_value=None) as recursive_cleanup:
            instance.confirm(offer_id)
            final = self.wait(instance, "failed")
            recursive_cleanup.assert_not_called()
        self.assertEqual(final["code"], "cleanup_failed")
        self.assertTrue(final["cleanup_pending"])
        self.assertEqual(instance._stage, stage)
        self.assertEqual(instance._payload, stage / NAME)
        self.assertEqual(len(self.callbacks), 1)
        self.assertTrue((retained / NAME).is_file())

    def test_replaced_plain_staging_directory_is_quarantined_without_recursive_cleanup(self):
        def busy(payload, offer):
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": "busy"}

        instance, offer_id = self.available(installer=busy)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "waiting_for_idle")
        stage = self.callbacks[0][1].parent
        retained = stage.with_name("retained-original-stage")
        stage.rename(retained)
        stage.mkdir()
        (stage / NAME).write_bytes(PAYLOAD)
        with patch.object(updater_module.shutil, "rmtree", return_value=None) as recursive_cleanup:
            instance.confirm(offer_id)
            final = self.wait(instance, "failed")
            recursive_cleanup.assert_not_called()
        self.assertEqual(final["code"], "cleanup_failed")
        self.assertTrue(final["cleanup_pending"])
        self.assertEqual(instance._stage, stage)
        self.assertEqual(instance._payload, stage / NAME)
        self.assertEqual(len(self.callbacks), 1)
        self.assertEqual((retained / NAME).read_bytes(), PAYLOAD)
        self.assertEqual((stage / NAME).read_bytes(), PAYLOAD)
        self.clock.advance(1000)
        self.assertEqual(instance.check(force=True)["code"], "cleanup_failed")
        self.assertEqual([url for url, _ in self.session.calls].count(URL), 1)

    def test_windows_verification_handle_denies_directory_write_access(self):
        if os.name != "nt":
            self.skipTest("Windows native directory sharing behavior")
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        stage = self.stage / "locked-stage"
        stage.mkdir()
        payload = stage / NAME
        payload.write_bytes(PAYLOAD)
        with updater_module._verification_handle(payload):
            handle = kernel.CreateFileW(str(stage), 0x40000000, 3, None, 3, 0x02200000, None)
            error = ctypes.get_last_error()
            if handle != ctypes.c_void_p(-1).value:
                kernel.CloseHandle(handle)
            self.assertEqual(handle, ctypes.c_void_p(-1).value,
                             "Verified staging directory allowed native write access")
            self.assertEqual(error, 32)

    def test_windows_staging_directory_is_protected_against_callback_replacement(self):
        if os.name != "nt":
            self.skipTest("Windows sharing-mode behavior")

        def installer(payload, offer):
            with self.assertRaises(OSError):
                payload.parent.rename(payload.parent.with_name("replacement-stage"))
            self.callbacks.append((offer, payload, payload.read_bytes()))
            return {"status": "launched"}

        instance, offer_id = self.available(installer=installer)
        self.session.add(URL, Response(PAYLOAD))
        instance.confirm(offer_id)
        self.wait(instance, "installing")
        self.assertEqual(len(self.callbacks), 1)


if __name__ == "__main__":
    unittest.main()
