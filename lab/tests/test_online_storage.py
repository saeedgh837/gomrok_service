import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from lab.online_feasibility.storage import MemoryStore


def fixture_png():
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data +
                struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
    header = struct.pack('!IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) +
            chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\xff')) + chunk(b'IEND', b''))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MemoryStore(self.root / 'app', self.root / 'photos', fixture_png())

    def test_text_stays_in_ram_and_image_name_is_opaque(self):
        logical = self.store.app_root / 'Sample_receipt.png'
        with self.store.open(self.store.app_root / 'payment-link-Sample.txt', 'w') as out:
            out.write('fixture link')
        with self.store.open(logical, 'wb') as out:
            out.write(fixture_png())
        self.assertFalse((self.store.app_root / 'payment-link-Sample.txt').exists())
        physical = self.store.image_paths[str(logical)]
        self.assertRegex(physical.name, r'^[a-f0-9]{32}\.png$')
        self.assertEqual(physical.read_bytes(), fixture_png())
        self.assertTrue(self.store.exists(logical))

    def test_escape_and_fake_png_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '^PATH_ESCAPE$'):
            self.store.open(self.root / 'outside.txt', 'w')
        with self.assertRaisesRegex(ValueError, '^FIXTURE_PNG_INVALID$'):
            with self.store.open(self.store.app_root / 'bad.png', 'wb') as out:
                out.write(b'not a png')
        self.assertEqual(len(self.store.violations), 2)

    def test_constructor_cannot_whitelist_arbitrary_binary_bytes(self):
        with self.assertRaisesRegex(ValueError, '^FIXTURE_PNG_INVALID$'):
            MemoryStore(self.root / 'app', self.root / 'other-photos', b'not a png')

    def test_swallowed_overflow_remains_a_violation(self):
        store = MemoryStore(self.root / 'app', self.root / 'photos', fixture_png(), limit=4)
        with self.assertRaisesRegex(ValueError, '^BUFFER_LIMIT$'):
            with store.open(store.app_root / 'logs.jsonl', 'w') as out:
                out.write('too long')
        self.assertIn('BUFFER_LIMIT', store.violations)
        self.assertFalse(store.exists(store.app_root / 'logs.jsonl'))

    def test_utf8_bytes_not_character_count_are_bounded(self):
        store = MemoryStore(self.root / 'app', self.root / 'photos', fixture_png(), limit=3)
        with self.assertRaisesRegex(ValueError, '^BUFFER_LIMIT$'):
            with store.open(store.app_root / 'utf8.txt', 'w') as out:
                out.write('éé')

    def test_output_count_cannot_grow_unbounded_even_on_rewrites(self):
        for number in range(128):
            with self.store.open(self.store.app_root / 'same.txt', 'w') as out:
                out.write('x')
        with self.assertRaisesRegex(ValueError, '^OUTPUT_LIMIT$'):
            with self.store.open(self.store.app_root / 'same.txt', 'w') as out:
                out.write('x')
        self.assertIn('OUTPUT_LIMIT', self.store.violations)

    def test_total_retained_bytes_are_bounded(self):
        for number in range(8):
            with self.store.open(self.store.app_root / f'{number}.txt', 'w') as out:
                out.write('x' * (1024 * 1024))
        with self.assertRaisesRegex(ValueError, '^TOTAL_BUFFER_LIMIT$'):
            with self.store.open(self.store.app_root / 'ninth.txt', 'w') as out:
                out.write('x')

    def test_aborted_or_empty_image_does_not_claim_an_image(self):
        logical = self.store.app_root / 'receipt.png'
        with self.assertRaisesRegex(RuntimeError, 'fixture abort'):
            with self.store.open(logical, 'wb') as out:
                out.write(fixture_png())
                raise RuntimeError('fixture abort')
        with self.assertRaisesRegex(ValueError, '^FIXTURE_PNG_INVALID$'):
            with self.store.open(logical, 'wb'):
                pass
        self.assertFalse(self.store.exists(logical))
        self.assertEqual(list(self.store.photo_root.iterdir()), [])

    def test_append_and_read_use_memory_and_unsupported_modes_fail(self):
        logical = self.store.app_root / 'logs.jsonl'
        for value in ('first', 'second'):
            with self.store.open(logical, 'a') as out:
                out.write(value)
        with self.store.open(logical) as stream:
            self.assertEqual(stream.read(), 'firstsecond')
        with self.assertRaisesRegex(ValueError, '^MODE_UNSUPPORTED$'):
            self.store.open(logical, 'w+')
