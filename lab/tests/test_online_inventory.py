import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lab.online_feasibility.inventory import (
    FILES, build_inventory, require_approved_robot,
)

ROOT = Path(__file__).resolve().parents[2]


class InventoryTests(unittest.TestCase):
    def test_actual_robot_boundaries_are_visible(self):
        require_approved_robot(ROOT)
        result = build_inventory(ROOT)
        calls = {s['call'] for s in result['sites'] if s['file'] == 'gomrok.py'}
        for call in ('open', 'driver.execute_async_script', 'driver.save_screenshot',
                     'profile_store.mark_profile_success'):
            self.assertIn(call, calls)
        self.assertTrue(any(s['owner'] == '<module>' and
                            s['call'] == 'system_debug.configure'
                            for s in result['sites']))
        self.assertEqual(set(result['source_hashes']), set(FILES))
        self.assertTrue(all(s['line'] > 0 for s in result['sites']))

    def test_missing_dependency_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                build_inventory(Path(raw))

    def test_changed_source_requires_new_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'gomrok.py').write_text('value = 1\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '^ROBOT_HASH_MISMATCH$'):
                require_approved_robot(root)

    def test_unknown_calls_are_kept_without_literal_arguments(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in FILES:
                (root / name).write_text('custom_writer("private fixture")\n', encoding='utf-8')
            result = build_inventory(root)
            self.assertEqual(len(result['sites']), 14)
            self.assertEqual(result['sites'][0]['call'], 'custom_writer')
            self.assertEqual(result['sites'][0]['kind'], 'unclassified_call')
            self.assertNotIn('private fixture', str(result))

    def test_site_limit_fails_instead_of_truncating(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in FILES:
                (root / name).write_text('unknown()\n' * 4, encoding='utf-8')
            with patch('lab.online_feasibility.inventory.MAX_SITES', 50):
                with self.assertRaisesRegex(ValueError, '^INVENTORY_LIMIT$'):
                    build_inventory(root)
