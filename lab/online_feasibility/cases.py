"""Fixed fixture cases; no browser, embedded script, solver or main execution."""

import contextlib
import io
import socket
import subprocess
import sys
from pathlib import Path

from lab.online_feasibility.fencing import FixtureBridge, Guard, Lease
from lab.online_feasibility.probe import FixtureDriver, install_audit, loaded_robot
from lab.online_feasibility.report import EVIDENCE_BY_CASE, REQUIRED
from lab.online_feasibility.storage import MemoryStore, fixture_png


class LegacySink(io.StringIO):
    def __init__(self):
        super().__init__()
        self.size, self.overflow = 0, False

    def write(self, text):
        size = len(text.encode('utf-8'))
        if self.size + size > 65536:
            self.overflow = True
            raise RuntimeError('LEGACY_OUTPUT_LIMIT')
        self.size += size
        return super().write(text)


def require(condition):
    if not condition:
        raise RuntimeError('FIXTURE_ASSERTION_FAILED')


def run_case(name, scratch):
    if name not in REQUIRED:
        raise ValueError('CASE_ID_INVALID')
    store = MemoryStore(scratch / 'app', scratch / 'photos', fixture_png())
    guard = Guard(Lease(7, 60), lambda: 10)
    bridge = FixtureBridge(guard, 7)
    sink = LegacySink()
    attempts = install_audit(store.photo_root, (store.app_root,
        Path(__file__).resolve().parents[2], Path(sys.base_prefix) / 'Lib',
        Path(sys.base_prefix) / 'DLLs'))
    expected, status = [], 'passed'
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            if name in {'disk_negative', 'network_negative', 'process_negative'}:
                code = {'disk_negative': 'write_denied', 'network_negative': 'network_denied',
                        'process_negative': 'process_denied'}[name]
                try:
                    if name == 'disk_negative':
                        with open(scratch / 'deleted-after-write.txt', 'w') as out:
                            out.write('fixture')
                    elif name == 'network_negative':
                        with socket.socket() as connection:
                            connection.connect(('127.0.0.1', 9))
                    else:
                        subprocess.Popen([sys.executable, '-c', 'pass'])
                except PermissionError:
                    pass
                require(len(attempts) == 1 and attempts[0]['kind'] == code)
                require(not (scratch / 'deleted-after-write.txt').exists())
                expected = [code]
            elif name == 'inflight_model':
                for label in ('element', 'helper', 'script', 'async_script', 'navigation', 'cdp'):
                    model = Guard(Lease(7, 60), lambda: 10)
                    effects = []
                    def effect():
                        effects.append('before revoke')
                        model.revoke()
                        effects.append('after revoke')
                    model.dispatch(7, effect)
                    require(effects == ['before revoke', 'after revoke'] and model.uncertain)
                    require(not model.publish(7, 'late result'))
            else:
                if name == 'heartbeat_limit':
                    def heartbeat_false(profile, runner):
                        return False
                    bridge.heartbeat_profile = heartbeat_false
                with loaded_robot(store.app_root / 'gomrok.py', store, bridge) as g:
                    driver = FixtureDriver(store, fixture_png())
                    g['_CLAIMED_PROFILE_ID'] = 'fixture-profile'
                    g['_CLAIMED_RUNNER_ID'] = 'fixture-runner'
                    if name == 'non_main_load':
                        require(g['__name__'] == 'irica_lab_fixture')
                        require(g['log_event'].__globals__['open'].__self__ is store)
                    elif name == 'memory_log_debug':
                        g['log_event']('info', 'fixture event')
                        g['DebugController'](driver, False)._dump_state('fixture')
                        require(len(store.text) == 4)
                        require(not (store.app_root / 'logs.jsonl').exists())
                    elif name in {'cdp_image', 'fallback_image', 'image_failure'}:
                        driver.fail_cdp = name != 'cdp_image'
                        driver.fail_fallback = name == 'image_failure'
                        target = store.app_root / 'receipt.png'
                        success = g['_full_screenshot'](driver, str(target))
                        require(success == (name != 'image_failure'))
                        require(store.exists(target) == success)
                        if name == 'fallback_image':
                            require('fallback_screenshot' in driver.events)
                    elif name == 'stale_fallback':
                        guard.revoke()
                        g['handle_already_registered'](driver, title='Sample', national='', imei='')
                        require(bridge.fallback_attempts == 1 and guard.results == [])
                    elif name == 'heartbeat_limit':
                        thread = g['_start_profile_heartbeat']('fixture-profile', 'fixture-runner')
                        thread.join(timeout=1)
                        require(not thread.is_alive())
                        effects = []
                        guard.dispatch(7, lambda: effects.append('still permitted'))
                        require(effects == ['still permitted'])
                    elif name == 'receipt_order':
                        for revoked in (False, True):
                            if revoked:
                                guard.revoke()
                                guard.results.clear()
                            roles = []
                            def deliver(kind, **kwargs):
                                role = 'receipt' if kwargs.get('photo_path', '').endswith(
                                    '_receipt.png') else 'payment'
                                roles.append((role, kind))
                            g['_telegram_deliver'] = deliver
                            g['handle_receipt_and_payment'](driver, title='Sample', national='', imei='')
                            require(roles == [('receipt', 'photo'), ('payment', 'photo')])
                            require(not revoked or guard.results == [])
    except Exception:
        status = 'failed'
    unexpected = len(attempts) - len(expected) + len(store.violations) + int(sink.overflow)
    sink.close()
    return {'id': name, 'status': status, 'evidence_kind': EVIDENCE_BY_CASE[name],
            'violations': unexpected}
