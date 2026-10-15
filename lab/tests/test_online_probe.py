import builtins
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lab.online_feasibility.probe import FixtureDriver, loaded_robot
from lab.online_feasibility.storage import MemoryStore, fixture_png

ROOT = Path(__file__).resolve().parents[2]


def fixture_bridge():
    def claim_profile(profile_id=None):
        return None
    def get_browser_visibility(profile_id):
        return True
    def release_profile(profile_id, runner_id):
        return False
    def record_profile_output(profile_id, runner_id, receipt, payment, link):
        return False
    def mark_profile_unusable(profile_id, error_text):
        return False
    def heartbeat_profile(profile_id, runner_id):
        return False
    def mark_profile_success(title, national, imei, link=''):
        return False
    return SimpleNamespace(**locals())


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        app = root / 'app'
        app.mkdir()
        self.source = app / 'gomrok.py'
        shutil.copyfile(ROOT / 'gomrok.py', self.source)
        self.store = MemoryStore(app, root / 'photos', fixture_png())

    def test_original_log_debug_and_cdp_bodies_use_local_seam(self):
        before = self.source.read_bytes()
        original_open, original_path = builtins.open, list(sys.path)
        original_panel = sys.modules.get('panel')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with loaded_robot(self.source, self.store, fixture_bridge()) as g:
                self.assertIs(g['log_event'].__globals__['open'].__self__, self.store)
                g['log_event']('info', 'fixture event')
                driver = FixtureDriver(self.store, fixture_png())
                g['DebugController'](driver, False)._dump_state('fixture')
                logical = self.store.app_root / 'screenshots' / 'Sample_receipt.png'
                self.assertTrue(g['_full_screenshot'](driver, str(logical)))
                self.assertTrue(self.store.exists(logical))
        self.assertEqual(len(self.store.text), 4)
        self.assertIs(builtins.open, original_open)
        self.assertEqual(sys.path, original_path)
        self.assertIs(sys.modules.get('panel'), original_panel)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.store.violations, [])

    def test_fallback_body_and_both_routes_failed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with loaded_robot(self.source, self.store, fixture_bridge()) as g:
                path = self.store.app_root / 'fallback.png'
                driver = FixtureDriver(self.store, fixture_png(), fail_cdp=True)
                self.assertTrue(g['_full_screenshot'](driver, str(path)))
                self.assertIn('fallback_screenshot', driver.events)
                driver.fail_fallback = True
                failed = self.store.app_root / 'failed.png'
                self.assertFalse(g['_full_screenshot'](driver, str(failed)))
                self.assertFalse(self.store.exists(failed))

    def test_original_repository_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, '^SOURCE_NOT_COPIED$'):
            with loaded_robot(ROOT / 'gomrok.py', self.store, fixture_bridge()):
                pass

    def test_unsupported_fixture_operation_never_synthesizes_success(self):
        driver = FixtureDriver(self.store, fixture_png())
        with self.assertRaisesRegex(RuntimeError, '^FIXTURE_OPERATION_FORBIDDEN$'):
            driver.execute_async_script('not evaluated')

    def guarded_child(self, action, expected):
        code = '''
import os, socket, subprocess, sys
from pathlib import Path
from lab.online_feasibility.probe import install_audit
scratch = Path(sys.argv[1])
attempts = install_audit(scratch / 'photos', (scratch, Path(sys.base_prefix) / 'Lib'))
try:
    ACTION
except PermissionError:
    pass
assert any(a['kind'] == EXPECTED for a in attempts)
assert all(set(a) == {'kind', 'file', 'line'} for a in attempts)
assert all(not a['file'].startswith(str(scratch)) for a in attempts)
assert not (scratch / 'deleted-after-write.txt').exists()
'''.replace('ACTION', action).replace('EXPECTED', repr(expected))
        result = subprocess.run([sys.executable, '-B', '-c', code, self.temp.name],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=20)
        self.assertEqual(result.returncode, 0, expected)

    def test_guarded_original_bodies_have_no_unexpected_attempts(self):
        code = '''
import contextlib, io, sys
from pathlib import Path
from types import SimpleNamespace
from lab.online_feasibility.probe import install_audit, loaded_robot, FixtureDriver
from lab.online_feasibility.storage import MemoryStore, fixture_png
scratch = Path(sys.argv[1])
store = MemoryStore(scratch / 'app', scratch / 'photos', fixture_png())
def inert(*args):
    return False
bridge = SimpleNamespace(**{n: inert for n in (
    'claim_profile', 'get_browser_visibility', 'release_profile',
    'record_profile_output', 'mark_profile_unusable',
    'heartbeat_profile', 'mark_profile_success')})
attempts = install_audit(store.photo_root, (store.app_root, Path(sys.base_prefix) / 'Lib'))
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    with loaded_robot(store.app_root / 'gomrok.py', store, bridge) as g:
        g['log_event']('info', 'fixture event')
        driver = FixtureDriver(store, fixture_png())
        g['DebugController'](driver, False)._dump_state('fixture')
        assert g['_full_screenshot'](driver, str(store.app_root / 'receipt.png'))
assert not attempts, attempts
assert not store.violations
assert len(store.text) == 4
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, self.temp.name],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))

    def test_unjoined_thread_cannot_resolve_real_panel_after_context_failure(self):
        code = '''
import contextlib, io, sys, threading
from pathlib import Path
from types import SimpleNamespace
from lab.online_feasibility.probe import loaded_robot
from lab.online_feasibility.storage import MemoryStore, fixture_png
scratch = Path(sys.argv[1])
store = MemoryStore(scratch / 'app', scratch / 'photos', fixture_png())
started, release = threading.Event(), threading.Event()
def heartbeat(profile, runner):
    started.set()
    release.wait(5)
    return False
def inert(*args):
    return False
bridge = SimpleNamespace(**{n: inert for n in (
    'claim_profile', 'get_browser_visibility', 'release_profile',
    'record_profile_output', 'mark_profile_unusable', 'mark_profile_success')})
bridge.heartbeat_profile = heartbeat
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        with loaded_robot(store.app_root / 'gomrok.py', store, bridge) as g:
            thread = g['_start_profile_heartbeat']('fixture-profile', 'fixture-runner')
            assert started.wait(1)
except RuntimeError as exc:
    assert str(exc) == 'FIXTURE_THREAD_UNJOINED'
    assert sys.modules['panel'].heartbeat_profile is heartbeat
else:
    raise AssertionError('unjoined thread accepted')
finally:
    release.set()
    thread.join(1)
assert not thread.is_alive()
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, self.temp.name],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))

    def image_guard_child(self, body):
        code = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from lab.online_feasibility.probe import install_audit
from lab.online_feasibility.storage import MemoryStore, fixture_png
scratch = Path(sys.argv[1])
store = MemoryStore(scratch / 'app', scratch / 'photos', fixture_png())
target = store.photo_root / ('a' * 32 + '.png')
BODY
'''.replace('BODY', body)
        from lab.online_feasibility.runner import minimal_env
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c', code, self.temp.name, str(ROOT)],
            cwd=self.temp.name, env=minimal_env(Path(self.temp.name)),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))

    def test_opaque_png_name_does_not_permit_direct_text_write(self):
        self.image_guard_child('''
attempts = install_audit(store.photo_root, (
    store.app_root, Path(sys.argv[2]), Path(sys.base_prefix) / 'Lib'))
try:
    with target.open('w', encoding='utf-8') as out:
        out.write('fixture text, not an image')
    target.unlink()
except PermissionError:
    pass
assert [item['kind'] for item in attempts] == ['write_denied'], (
    'DIRECT_WRITE_ATTEMPTS', tuple(item['kind'] for item in attempts))
assert not target.exists()
''')

    def test_direct_image_overwrite_truncate_delete_attempts_stay_visible(self):
        self.image_guard_child('''
target.write_bytes(fixture_png())
attempts = install_audit(store.photo_root, (
    store.app_root, Path(sys.argv[2]), Path(sys.base_prefix) / 'Lib'))
def overwrite():
    with target.open('wb') as out:
        out.write(b'fixture text, not an image')
for action in (overwrite, lambda: os.truncate(target, 0), target.unlink):
    try:
        action()
    except PermissionError:
        pass
assert [item['kind'] for item in attempts] == [
    'write_denied', 'mutation_denied', 'mutation_denied'], (
        'DIRECT_MUTATION_ATTEMPTS', tuple(item['kind'] for item in attempts))
assert target.read_bytes() == fixture_png()
''')

    def test_trusted_png_open_does_not_grant_later_writes_or_bypass_limits(self):
        self.image_guard_child('''
attempts = install_audit(store.photo_root, (
    store.app_root, Path(sys.argv[2]), Path(sys.base_prefix) / 'Lib'))
logical = store.app_root / 'receipt.png'
for number in range(128):
    with store.open(logical, 'wb') as out:
        out.write(fixture_png())
assert not attempts
physical = store.image_paths[str(logical)]
try:
    with physical.open('ab') as out:
        out.write(b'not an image')
except PermissionError:
    pass
assert [item['kind'] for item in attempts] == ['write_denied'], (
    'DIRECT_WRITE_ATTEMPTS', tuple(item['kind'] for item in attempts))
assert physical.read_bytes() == fixture_png()
try:
    with store.open(logical, 'wb') as out:
        out.write(fixture_png())
except ValueError as exc:
    assert str(exc) == 'OUTPUT_LIMIT'
else:
    raise AssertionError('image output limit bypassed')
assert store.violations == ['OUTPUT_LIMIT']
assert store.output_count == 128
''')

    def test_caught_direct_write_attempt_is_still_observed(self):
        self.guarded_child("open(scratch / 'deleted-after-write.txt', 'w')", 'write_denied')

    def test_flag_based_os_open_attempt_is_observed(self):
        self.guarded_child("os.open(scratch / 'deleted-after-write.txt', os.O_CREAT | os.O_WRONLY)", 'write_denied')

    def test_network_denied_before_loopback_connection(self):
        self.guarded_child("socket.socket().connect(('127.0.0.1', 9))", 'network_denied')

    def test_process_denied_before_spawn(self):
        self.guarded_child("subprocess.Popen([sys.executable, '-c', 'pass'])", 'process_denied')

    def test_read_and_mutation_outside_allowed_roots_are_observed(self):
        self.guarded_child("open(scratch.parent / 'not-permitted', 'r')", 'read_denied')
        self.guarded_child("os.mkdir(scratch.parent / 'not-permitted')", 'mutation_denied')
