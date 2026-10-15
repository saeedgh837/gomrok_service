"""Non-main fixture seams and child-lifetime attempted-I/O observation."""

import base64
import contextlib
import json
import os
import pkgutil
import random
import re
import runpy
import socket
import subprocess
import sys
import threading
import types
from pathlib import Path

from lab.online_feasibility.inventory import APPROVED_ROBOT, source_hash
from lab.online_feasibility.storage import _consume_image_open, checked_path


class FixtureForbidden(RuntimeError):
    pass


def forbidden(*args, **kwargs):
    raise FixtureForbidden('FIXTURE_OPERATION_FORBIDDEN')


@contextlib.contextmanager
def loaded_robot(source: Path, store, bridge):
    if checked_path(source).parent != store.app_root:
        raise ValueError('SOURCE_NOT_COPIED')
    if source_hash(source) != APPROVED_ROBOT:
        raise ValueError('ROBOT_HASH_MISMATCH')
    modules = {}

    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        value.__path__ = []
        value.__file__ = '<fixture>'
        value.__getattr__ = forbidden
        modules[name] = value
        return value

    module('app_paths', data_path=lambda name: str(store.app_root / name),
           data_subdir=lambda name: str(store.app_root / name))
    module('system_debug', configure=lambda service: None, event=lambda message: None)
    module('browser_visibility')
    module('captcha_collector', build_driver=forbidden,
           fire_refresh_events=lambda driver, element: element.click())
    module('live_solve', load_solver=forbidden)
    module('panel', **{name: getattr(bridge, name) for name in (
        'claim_profile', 'get_browser_visibility', 'release_profile',
        'record_profile_output', 'mark_profile_unusable',
        'heartbeat_profile', 'mark_profile_success')})
    for name in ('selenium', 'selenium.webdriver', 'selenium.webdriver.common',
                 'selenium.webdriver.support', 'selenium.common'):
        module(name)
    module('selenium.webdriver.common.by', By=types.SimpleNamespace(
        CSS_SELECTOR='css selector', XPATH='xpath', TAG_NAME='tag name'))
    module('selenium.webdriver.support.ui', WebDriverWait=forbidden)
    module('selenium.webdriver.support.expected_conditions')
    module('selenium.common.exceptions', StaleElementReferenceException=type(
        'StaleElementReferenceException', (Exception,), {}))
    for name, value in modules.items():
        parent, _, child = name.rpartition('.')
        if parent in modules:
            setattr(modules[parent], child, value)
    saved = {name: sys.modules.get(name) for name in modules}
    saved_path = list(sys.path)
    saved_env = {name: os.environ.get(name) for name in ('PYTHONIOENCODING', 'PYTHONUTF8')}
    before_threads = set(threading.enumerate())
    sys.modules.update(modules)
    try:
        ns = runpy.run_path(str(source), init_globals={'open': store.open},
                            run_name='irica_lab_fixture')
        g = ns['log_event'].__globals__
        path_facade = types.SimpleNamespace(**{name: getattr(os.path, name)
            for name in ('join', 'dirname', 'abspath', 'basename', 'splitext')})
        path_facade.exists = store.exists
        g['os'] = types.SimpleNamespace(path=path_facade,
                                       makedirs=lambda path, exist_ok=False: store.key(path))
        g['time'] = types.SimpleNamespace(sleep=lambda seconds: None,
                                         strftime=lambda pattern: '2000-01-01 00:00:00')
        g['_telegram_deliver'] = lambda kind, **kwargs: None
        g['_extract_payment_link'] = lambda driver: 'https://fixture.invalid/payment'
        yield g
    finally:
        live = [t for t in threading.enumerate() if t not in before_threads and
                t.name == 'profile-heartbeat' and t.is_alive()]
        if live:
            # A failed disposable child must not expose real imports to a surviving thread.
            raise FixtureForbidden('FIXTURE_THREAD_UNJOINED')
        sys.path[:] = saved_path
        for name, prior in saved.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
        for name, prior in saved_env.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


class FixtureButton:
    def __init__(self, events):
        self.events = events

    def get_attribute(self, name):
        if name != 'onclick':
            forbidden()
        return "window.location.href = 'https://fixture.invalid/payment'"

    def click(self):
        self.events.append('fixture_click')

    __getattr__ = forbidden


class FixtureDriver:
    page_source = '<html>fixture</html>'
    current_url = 'https://fixture.invalid/payment'

    def __init__(self, store, png, fail_cdp=False, payment_button=True):
        self.store, self.png = store, png
        self.fail_cdp, self.fail_fallback = fail_cdp, False
        self.payment_button, self.events = payment_button, []

    def execute_script(self, script, *args):
        return {'errors': [], 'imei_rows': [], 'continue_visible': False,
                'captcha_loaded': False}

    def execute_cdp_cmd(self, command, options):
        if command != 'Page.captureScreenshot':
            forbidden()
        if self.fail_cdp:
            raise FixtureForbidden('FIXTURE_CDP_FAILURE')
        self.events.append('cdp_image')
        return {'data': base64.b64encode(self.png).decode('ascii')}

    def save_screenshot(self, path):
        self.events.append('fallback_screenshot')
        if self.fail_fallback:
            raise FixtureForbidden('FIXTURE_SCREENSHOT_FAILURE')
        with self.store.open(path, 'wb') as out:
            out.write(self.png)
        return True

    def find_elements(self, by, selector):
        if by != 'css selector':
            forbidden()
        return [FixtureButton(self.events)] if self.payment_button else []

    __getattr__ = forbidden


def install_audit(photo_root: Path, read_roots: tuple) -> list:
    """Install once in a disposable child. This is not a native/hostile-code sandbox."""
    photo_root = checked_path(photo_root)
    roots = tuple(checked_path(root) for root in read_roots)
    attempts = []

    def deny(kind):
        if len(attempts) < 4095:
            frame = sys._getframe(2)
            name = Path(frame.f_code.co_filename).name
            file = ('gomrok.py' if name == 'gomrok.py' else
                    'lab/online_feasibility/' + name if name in
                    {'probe.py', 'storage.py', 'fencing.py'} else '<outside>')
            attempts.append({'kind': kind, 'file': file, 'line': frame.f_lineno})
        elif len(attempts) == 4095:
            attempts.append({'kind': 'audit_limit', 'file': '<outside>', 'line': 0})
        raise PermissionError(kind)

    def resolve(path):
        if isinstance(path, int):
            deny('descriptor_write_denied')
        try:
            return checked_path(Path(os.fsdecode(path)))
        except (ValueError, TypeError, OSError):
            deny('path_denied')

    def readable(path):
        return (any(path.is_relative_to(root) for root in roots) and
                'site-packages' not in path.parts)

    def image(path):
        return (path.parent == photo_root and
                re.fullmatch(r'[a-f0-9]{32}\.png', path.name) is not None)

    def audit(event, args):
        if event == 'open':
            path, mode, flags = args
            writing = (bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                           os.O_TRUNC | os.O_APPEND)) or
                       bool(mode and any(c in mode for c in 'wax+')))
            if isinstance(path, int):
                deny('descriptor_write_denied' if writing else 'read_denied')
            target = resolve(path)
            if writing and not (image(target) and _consume_image_open(target, flags or 0)):
                deny('write_denied')
            if not writing and not (readable(target) or image(target)):
                deny('read_denied')
        elif event.startswith('socket.') and event in {
                'socket.connect', 'socket.getaddrinfo', 'socket.bind',
                'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto'}:
            deny('network_denied')
        elif (event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork',
                        'os.startfile', 'os.startfile/2'} or
              event.startswith(('os.exec', 'os.spawn'))):
            deny('process_denied')
        elif event in {'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir',
                       'os.link', 'os.symlink', 'os.truncate', 'os.chmod',
                       'os.chdir', 'os.utime'}:
            deny('mutation_denied')
        elif event in {'os.listdir', 'os.scandir'}:
            if not readable(resolve(args[0] if args else os.curdir)):
                deny('read_denied')

    sys.addaudithook(audit)
    return attempts
