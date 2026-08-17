"""Ad-hoc test runner for the panel/robot unit tests (no pytest needed).

Lives under lab/scripts/ because panel.clean_project_root() deletes any
`_verify_*.py` / `hermes-verify-*.py` left in the project root.

    python lab/scripts/run_panel_tests.py [test_module ...]
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TESTS = os.path.join(ROOT, "lab", "tests")
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

DEFAULT = [
    "test_profile_pool", "test_runtime_heartbeat", "test_profile_success",
    "test_clone_profile", "test_unusable_profile", "test_runtime_api",
    "test_identity_sections", "test_store_durability", "test_browser_visibility",
    "test_page_probe_and_telegram_timeout", "test_already_registered_profile",
    "test_telegram_delivery", "test_telegram_chain",
]

names = sys.argv[1:] or DEFAULT
loader = unittest.TestLoader()
suite = unittest.TestSuite()
missing = []
for name in names:
    if not os.path.exists(os.path.join(TESTS, name + ".py")):
        missing.append(name)
        continue
    suite.addTests(loader.loadTestsFromName(name))
if missing:
    print("SKIPPED (not present):", ", ".join(missing))
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
