import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from lab.online_feasibility.fencing import FixtureBridge, Guard, Lease
from lab.online_feasibility.probe import FixtureDriver, loaded_robot
from lab.online_feasibility.storage import MemoryStore, fixture_png

ROOT = Path(__file__).resolve().parents[2]


class FencingTests(unittest.TestCase):
    def test_revocation_prevents_dispatch(self):
        guard = Guard(Lease(7, 60), lambda: 10)
        calls = []
        guard.revoke()
        with self.assertRaisesRegex(RuntimeError, '^JOB_FENCED$'):
            guard.dispatch(7, lambda: calls.append('fixture action'))
        self.assertEqual(calls, [])

    def test_expiry_and_old_epoch_are_fenced(self):
        for lease, now, epoch in ((Lease(7, 10), 10, 7), (Lease(8, 60), 10, 7)):
            guard = Guard(lease, lambda: now)
            with self.assertRaisesRegex(RuntimeError, '^JOB_FENCED$'):
                guard.dispatch(epoch, lambda: None)
            self.assertFalse(guard.publish(epoch, 'late result'))

    def test_inflight_effect_is_uncertain_not_undone(self):
        for label in ('element', 'helper', 'script', 'async_script', 'navigation', 'cdp'):
            with self.subTest(label=label):
                guard = Guard(Lease(7, 60), lambda: 10)
                effects = []
                def effect():
                    effects.append('before revoke')
                    guard.revoke()
                    effects.append('after revoke')
                    return 'late result'
                self.assertEqual(guard.dispatch(7, effect), 'late result')
                self.assertEqual(effects, ['before revoke', 'after revoke'])
                self.assertTrue(guard.uncertain)
                self.assertFalse(guard.publish(7, 'late result'))
                self.assertEqual(guard.results, [])

    def test_expiry_during_dispatch_makes_result_uncertain(self):
        now = [10]
        guard = Guard(Lease(7, 60), lambda: now[0])
        def effect():
            now[0] = 60
        guard.dispatch(7, effect)
        self.assertTrue(guard.uncertain)
        self.assertFalse(guard.publish(7, 'late result'))


class OriginalHandlerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        app = root / 'app'
        app.mkdir()
        self.source = app / 'gomrok.py'
        shutil.copyfile(ROOT / 'gomrok.py', self.source)
        self.store = MemoryStore(app, root / 'photos', fixture_png())
        self.guard = Guard(Lease(7, 60), lambda: 10)
        self.bridge = FixtureBridge(self.guard, 7)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def test_original_stale_fallback_is_called_but_cannot_publish(self):
        self.guard.revoke()
        with loaded_robot(self.source, self.store, self.bridge) as g:
            g['_CLAIMED_PROFILE_ID'] = 'fixture-profile'
            g['_CLAIMED_RUNNER_ID'] = 'fixture-runner'
            g['handle_already_registered'](FixtureDriver(self.store, fixture_png()),
                                           title='Sample', national='', imei='')
        self.assertEqual(self.bridge.fallback_attempts, 1)
        self.assertEqual(self.guard.results, [])
        self.assertEqual(self.store.violations, [])

    def test_receipt_notification_precedes_payment_for_current_and_revoked_lease(self):
        for revoked in (False, True):
            with self.subTest(revoked=revoked):
                guard = Guard(Lease(7, 60), lambda: 10)
                if revoked:
                    guard.revoke()
                roles = []
                def deliver(kind, **kwargs):
                    roles.append('receipt_photo' if kwargs.get('photo_path', '').endswith(
                        '_receipt.png') else 'payment_photo')
                with loaded_robot(self.source, self.store, FixtureBridge(guard, 7)) as g:
                    g['_CLAIMED_PROFILE_ID'] = 'fixture-profile'
                    g['_CLAIMED_RUNNER_ID'] = 'fixture-runner'
                    g['_telegram_deliver'] = deliver
                    driver = FixtureDriver(self.store, fixture_png())
                    g['handle_receipt_and_payment'](driver, title='Sample', national='', imei='')
                self.assertEqual(roles, ['receipt_photo', 'payment_photo'])
                self.assertEqual(driver.events, ['cdp_image', 'fixture_click', 'cdp_image'])
                if revoked:
                    self.assertEqual(guard.results, [])
                else:
                    self.assertEqual([r['kind'] for r in guard.results],
                                     ['fixture-success', 'fixture-success', 'fixture-output'])

    def test_heartbeat_exit_does_not_cancel_independent_dispatch(self):
        calls = []
        def heartbeat_false(profile, runner):
            calls.append('heartbeat')
            return False
        self.bridge.heartbeat_profile = heartbeat_false
        with loaded_robot(self.source, self.store, self.bridge) as g:
            thread = g['_start_profile_heartbeat']('fixture-profile', 'fixture-runner')
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.guard.dispatch(7, lambda: calls.append('independent dispatch'))
        self.assertEqual(calls, ['heartbeat', 'independent dispatch'])
        self.assertFalse(self.guard.uncertain)
