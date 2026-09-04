"""Configuration, state and logs must survive a version upgrade.

The release is a folder you replace wholesale. Everything the operator had
configured -- the Telegram bot token above all -- used to live INSIDE that
folder, so every update meant hand-copying files across, and forgetting one
silently disabled notifications or lost the profile list.

Contract:
  * config/state/logs resolve to a per-user data directory OUTSIDE the release;
  * `IRICA_DATA_DIR` overrides it (tests, portable installs, a shared path);
  * files still sitting next to the app are migrated in on first run, so
    upgrading from a version that kept them locally needs no manual action;
  * migration never overwrites a file that already exists in the data directory;
  * secrets never enter the repo or the release archive -- only *.example.json
    ship, and the release verifier already fails if a real config appears.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/run_panel_tests.py test_app_paths
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import app_paths


class DataDirResolutionTests(unittest.TestCase):
    def test_override_wins_and_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "custom", "irica")
            with patch.dict(os.environ, {"IRICA_DATA_DIR": target}):
                app_paths.reset_cache()
                self.assertEqual(app_paths.data_dir(), os.path.abspath(target))
                self.assertTrue(os.path.isdir(target))
        app_paths.reset_cache()

    def test_override_is_ignored_when_blank(self):
        with patch.dict(os.environ, {"IRICA_DATA_DIR": "   "}):
            app_paths.reset_cache()
            self.assertNotEqual(app_paths.data_dir().strip(), "")
        app_paths.reset_cache()

    def test_default_is_outside_the_app_directory(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IRICA_DATA_DIR", None)
            app_paths.reset_cache()
            resolved = os.path.normcase(app_paths.data_dir())
            app = os.path.normcase(app_paths.APP_DIR)
            self.assertFalse(resolved.startswith(app + os.sep))
            self.assertNotEqual(resolved, app)
            self.assertIn("irica", resolved)
        app_paths.reset_cache()

    def test_default_uses_localappdata_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
            os.environ.pop("IRICA_DATA_DIR", None)
            with patch.object(os, "name", "nt"):
                app_paths.reset_cache()
                self.assertEqual(app_paths.data_dir(),
                                 os.path.join(tmp, "irica"))
        app_paths.reset_cache()

    def test_data_path_joins_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"IRICA_DATA_DIR": tmp}):
            app_paths.reset_cache()
            self.assertEqual(app_paths.data_path("telegram_config.json"),
                             os.path.join(os.path.abspath(tmp),
                                          "telegram_config.json"))
            for bad in ("../escape.json", "sub/dir.json", "a\\b.json", ""):
                with self.subTest(name=bad), self.assertRaises(ValueError):
                    app_paths.data_path(bad)
        app_paths.reset_cache()

    def test_data_subdir_is_created(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"IRICA_DATA_DIR": tmp}):
            app_paths.reset_cache()
            path = app_paths.data_subdir("logs", "system")
            self.assertEqual(path, os.path.join(os.path.abspath(tmp),
                                                "logs", "system"))
            self.assertTrue(os.path.isdir(path))
        app_paths.reset_cache()


class MigrationTests(unittest.TestCase):
    def _legacy(self, app_dir, name, payload):
        with open(os.path.join(app_dir, name), "w", encoding="utf-8") as handle:
            handle.write(payload)

    def test_legacy_files_move_into_the_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "irica-v1.0.6-win64")
            data_dir = os.path.join(tmp, "data")
            os.makedirs(app_dir)
            self._legacy(app_dir, "telegram_config.json",
                         '{"bot_token": "123:abc", "chat_ids": ["1"]}')
            self._legacy(app_dir, "profiles.json", '{"profiles": []}')
            moved = app_paths.migrate_legacy(app_dir, data_dir)
            self.assertIn("telegram_config.json", moved)
            self.assertIn("profiles.json", moved)
            with open(os.path.join(data_dir, "telegram_config.json"),
                      encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["bot_token"], "123:abc")

    def test_migration_keeps_a_copy_in_place_for_rollback(self):
        """The old version must keep working if the operator rolls back."""
        with tempfile.TemporaryDirectory() as tmp:
            app_dir, data_dir = os.path.join(tmp, "app"), os.path.join(tmp, "data")
            os.makedirs(app_dir)
            self._legacy(app_dir, "telegram_config.json", '{"bot_token": "t"}')
            app_paths.migrate_legacy(app_dir, data_dir)
            self.assertTrue(os.path.exists(os.path.join(app_dir,
                                                        "telegram_config.json")))

    def test_migration_never_overwrites_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir, data_dir = os.path.join(tmp, "app"), os.path.join(tmp, "data")
            os.makedirs(app_dir)
            os.makedirs(data_dir)
            self._legacy(app_dir, "telegram_config.json", '{"bot_token": "OLD"}')
            with open(os.path.join(data_dir, "telegram_config.json"), "w",
                      encoding="utf-8") as handle:
                handle.write('{"bot_token": "NEW"}')
            moved = app_paths.migrate_legacy(app_dir, data_dir)
            self.assertNotIn("telegram_config.json", moved)
            with open(os.path.join(data_dir, "telegram_config.json"),
                      encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["bot_token"], "NEW")

    def test_example_templates_are_never_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir, data_dir = os.path.join(tmp, "app"), os.path.join(tmp, "data")
            os.makedirs(app_dir)
            self._legacy(app_dir, "telegram_config.example.json", "{}")
            self.assertEqual(app_paths.migrate_legacy(app_dir, data_dir), [])
            self.assertFalse(os.path.exists(
                os.path.join(data_dir, "telegram_config.example.json")))

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir, data_dir = os.path.join(tmp, "app"), os.path.join(tmp, "data")
            os.makedirs(app_dir)
            self._legacy(app_dir, "inputs.json", "{}")
            first = app_paths.migrate_legacy(app_dir, data_dir)
            second = app_paths.migrate_legacy(app_dir, data_dir)
            self.assertEqual(first, ["inputs.json"])
            self.assertEqual(second, [])

    def test_migration_survives_an_unreadable_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir, data_dir = os.path.join(tmp, "app"), os.path.join(tmp, "data")
            os.makedirs(app_dir)
            self._legacy(app_dir, "inputs.json", "{}")
            with patch.object(shutil, "copy2", side_effect=OSError("locked")):
                self.assertEqual(app_paths.migrate_legacy(app_dir, data_dir), [])

    def test_migration_covers_every_config_and_state_file(self):
        """Any file the app persists must be in the migration list."""
        for name in ("telegram_config.json", "upstream_proxy.json",
                     "relay_config.json", "profiles.json", "profiles.json.bak",
                     "inputs.json", "estelam_store.json", "logs.jsonl",
                     "estelam_logs.jsonl", "hamta_logs.jsonl",
                     "site2_notif.json", "telegram_health.json",
                     "exitban_errors.jsonl", "system_debug.json"):
            with self.subTest(name=name):
                self.assertIn(name, app_paths.MIGRATED_FILES)


class ModuleWiringTests(unittest.TestCase):
    """Every module that persists something must use the data directory."""

    def test_panel_paths_live_in_the_data_dir(self):
        import panel
        expected = os.path.normcase(app_paths.data_dir())
        for attribute in ("INPUTS_PATH", "PROFILES_PATH", "LOGS_PATH",
                          "SITE2_NOTIF_PATH", "TELEGRAM_PATH",
                          "TELEGRAM_HEALTH_PATH", "EXITBAN_ERRORS_PATH",
                          "SYSTEM_DEBUG_CONFIG_PATH", "ESTELAM_PATH",
                          "ESTELAM_LOGS_PATH", "ROBOT_LOG_PATH"):
            with self.subTest(attribute=attribute):
                value = os.path.normcase(getattr(panel, attribute))
                self.assertTrue(value.startswith(expected + os.sep), value)

    def test_panel_system_log_dir_lives_in_the_data_dir(self):
        import panel
        self.assertTrue(os.path.normcase(panel.SYSTEM_LOG_DIR).startswith(
            os.path.normcase(app_paths.data_dir()) + os.sep))

    def test_robot_modules_read_the_shared_config(self):
        import estelam_gomrok
        import estelam_hamta
        import gomrok
        import telegram_notify
        expected = os.path.normcase(app_paths.data_dir())
        for module, attribute in ((telegram_notify, "_CFG_PATH"),
                                  (gomrok, "_INPUTS_PATH"),
                                  (gomrok, "_LOGS_PATH"),
                                  (estelam_hamta, "_LOG_PATH"),
                                  (estelam_hamta, "_STORE_PATH"),
                                  (estelam_gomrok, "_STORE_PATH")):
            with self.subTest(module=module.__name__, attribute=attribute):
                value = os.path.normcase(getattr(module, attribute))
                self.assertTrue(value.startswith(expected + os.sep), value)

    def test_estelam_store_env_override_still_wins(self):
        """profile_inquiry.py hands the robots a temporary store."""
        import importlib
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "temp-store.json")
            with patch.dict(os.environ, {"IRICA_ESTELAM_STORE": store}):
                import estelam_hamta
                importlib.reload(estelam_hamta)
                self.assertEqual(estelam_hamta._STORE_PATH, store)
            importlib.reload(estelam_hamta)

    def test_relay_modules_read_the_shared_config(self):
        import cloudflare_auth_relay
        import cloudflare_http_proxy
        import telegram_api_relay
        expected = os.path.normcase(app_paths.data_dir())
        for module, attribute in ((cloudflare_auth_relay, "CONFIG_PATH"),
                                  (telegram_api_relay, "CONFIG_PATH"),
                                  (cloudflare_http_proxy, "LOCAL_UPSTREAM_CONFIG")):
            with self.subTest(module=module.__name__):
                value = os.path.normcase(getattr(module, attribute))
                self.assertTrue(value.startswith(expected + os.sep), value)


class TelegramConfigTests(unittest.TestCase):
    """The setting that hurt: it must be readable and writable in the data dir."""

    def test_panel_saves_and_reads_back_from_the_data_dir(self):
        import panel
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "telegram_config.json")
            with patch.object(panel, "TELEGRAM_PATH", path):
                panel.save_telegram_config({"bot_token": "111:AAA",
                                            "chat_ids": ["12", "34"]})
                self.assertTrue(os.path.exists(path))
                cfg = panel.load_telegram_config()
                self.assertEqual(cfg["bot_token"], "111:AAA")
                self.assertEqual(cfg["chat_ids"], ["12", "34"])

    def test_public_config_does_not_leak_the_token(self):
        import panel
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "telegram_config.json")
            with patch.object(panel, "TELEGRAM_PATH", path):
                panel.save_telegram_config({"bot_token": "111:AAAAAAAAAAAA",
                                            "chat_ids": ["12"]})
                public = json.dumps(panel.public_telegram_config(),
                                    ensure_ascii=False)
                self.assertNotIn("111:AAAAAAAAAAAA", public)

    def test_telegram_notify_reads_the_same_file_as_the_panel(self):
        import panel
        import telegram_notify
        self.assertEqual(os.path.normcase(panel.TELEGRAM_PATH),
                         os.path.normcase(telegram_notify._CFG_PATH))


if __name__ == "__main__":
    unittest.main()
