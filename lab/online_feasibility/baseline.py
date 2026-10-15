"""Pinned, reduced legacy baseline. Missing original coverage remains BLOCKED."""

import contextlib
import hashlib
import io
import json
import os
import runpy
import shutil
import stat
import sys
import tarfile
import unittest
from pathlib import Path, PurePosixPath

from lab.online_feasibility.cases import LegacySink
from lab.online_feasibility.runner import bounded_child, minimal_env
from lab.online_feasibility.storage import checked_path

BASE = '08c344511d4f53ac19ad1cd3bbb445e342ddc942'
CORE = ('test_crm_validation', 'test_crm_stage_transitions', 'test_crm_case_integrity',
        'test_crm_ledger_integrity', 'test_crm_scheduler', 'test_runtime_heartbeat')
EXCLUDED = {'test_auth_hardening': 'optional_node_and_proxy_not_reviewed',
            'test_runtime_api': 'real_dependency_bootstrap_not_reviewed',
            'test_token_privacy': 'secret_prerequisites_and_zero_unittest_collection'}
SOURCES = ('panel.py', 'crm.py', 'jalali.py', 'log_utils.py', 'panel_auth.py', 'app_paths.py', 'estelam_shot.py',
           'lab/scripts/run_panel_tests.py') + tuple('lab/tests/' + n + '.py' for n in CORE)


def permitted(name):
    return (name in SOURCES or
            name.startswith('cloud_sync/') and len(PurePosixPath(name).parts) == 2 and
            name.endswith('.py'))


def candidate_identity(root, repository, scratch):
    from lab.online_feasibility.inventory import FILES
    required = (set(FILES) | set(SOURCES) |
                {'lab/tests/' + name + '.py' for name in EXCLUDED} |
                {'lab/tests/test_v1_2_0_login_gate.py'})
    env = minimal_env(scratch)
    try:
        root = checked_path(root)
        raw_names = bounded_child(['git', '-C', str(repository), 'ls-tree', '-r',
                                   '--name-only', BASE], repository, env, timeout=30, cap=1048576)
        if raw_names is None:
            raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
        names = sorted(name for name in raw_names.decode('utf-8').splitlines()
                       if name in required or permitted(name))
        if not required.issubset(names) or len(names) > 128:
            raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
        raw = bounded_child(['git', '-C', str(repository), 'archive', '--format=tar',
                             BASE, '--', *names], repository, env, timeout=30, cap=32 * 1024 * 1024)
        if raw is None:
            raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
        files = {}
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for member in archive.getmembers():
                if member.isdir() and member.name.rstrip('/') in {'lab', 'lab/tests', 'lab/scripts', 'cloud_sync'}:
                    continue
                if (member.name not in names or member.name in files or not member.isfile() or
                        not 0 <= member.size <= 8 * 1024 * 1024):
                    raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
                path = checked_path(root / member.name)
                if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
                current = path.read_bytes()
                with archive.extractfile(member) as source:
                    pinned = source.read().replace(b'\r\n', b'\n')
                normalized = current.replace(b'\r\n', b'\n')
                if normalized != pinned:
                    raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
                files[member.name] = dict(sha256=hashlib.sha256(current).hexdigest(),
                    normalized_sha256=hashlib.sha256(normalized).hexdigest())
        if set(files) != set(names):
            raise ValueError('CANDIDATE_PREREQUISITE_INVALID')
        return dict(base=BASE, normalization='crlf_to_lf', files=files)
    except (OSError, ValueError, tarfile.TarError):
        raise ValueError('CANDIDATE_PREREQUISITE_INVALID') from None


def extract_snapshot(raw, destination):
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        members = archive.getmembers()
        files, total = [], 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.isdir() and member.name.rstrip('/') in {'lab', 'lab/tests', 'lab/scripts', 'cloud_sync'}:
                continue
            if (not member.isfile() or not parts or '..' in parts or '\\' in member.name or
                    PurePosixPath(member.name).is_absolute() or not permitted(member.name) or
                    member.size > 8 * 1024 * 1024):
                raise ValueError('BASELINE_ARCHIVE_INVALID')
            total += member.size
            files.append(member)
        if total > 32 * 1024 * 1024 or len(files) > 128:
            raise ValueError('BASELINE_ARCHIVE_INVALID')
        for member in files:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as out:
                shutil.copyfileobj(source, out)


def descriptor_resolver():
    if os.name != 'nt':
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_path = kernel.GetFinalPathNameByHandleW
    get_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    get_path.restype = wintypes.DWORD
    get_type = kernel.GetFileType
    get_type.argtypes = (wintypes.HANDLE,)
    get_type.restype = wintypes.DWORD

    def resolve(fd):
        if type(fd) is not int or fd < 0:
            raise ValueError('DESCRIPTOR_INVALID')
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('DESCRIPTOR_INVALID')
        handle = msvcrt.get_osfhandle(fd)
        if get_type(handle) != 1:
            raise ValueError('DESCRIPTOR_INVALID')
        buffer = ctypes.create_unicode_buffer(32768)
        count = get_path(handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer):
            raise ValueError('DESCRIPTOR_INVALID')
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        path = Path(value)
        if not path.is_absolute() or ':' in value[len(path.drive):]:
            raise ValueError('DESCRIPTOR_INVALID')
        path = checked_path(path)
        if not os.path.samestat(info, path.stat()) or not os.path.samestat(info, os.fstat(fd)):
            raise ValueError('DESCRIPTOR_INVALID')
        return path

    return resolve


def install_baseline_audit(workspace):
    workspace = checked_path(workspace)
    resolve_descriptor = descriptor_resolver()
    libraries = (checked_path(Path(sys.base_prefix) / 'Lib'),
                 checked_path(Path(sys.base_prefix) / 'DLLs'))
    violations = []

    def deny(kind):
        if len(violations) < 4096:
            frame = sys._getframe(1)
            file, line = '<outside>', frame.f_lineno
            while frame:
                source = Path(frame.f_code.co_filename)
                if source.is_relative_to(workspace / 'snapshot'):
                    file, line = source.relative_to(workspace / 'snapshot').as_posix(), frame.f_lineno
                    break
                line = frame.f_lineno
                frame = frame.f_back
            violations.append(dict(kind=kind, file=file, line=line))
        raise PermissionError('BASELINE_IO_DENIED')

    def contained(path, writing=False, event='open'):
        try:
            if isinstance(path, int):
                if resolve_descriptor is None:
                    raise ValueError('DESCRIPTOR_INVALID')
                candidate = resolve_descriptor(path)
            else:
                candidate = checked_path(Path(os.fsdecode(path)))
        except (ValueError, TypeError, OSError):
            deny(event)
        if candidate.is_relative_to(workspace):
            return
        if not writing and any(candidate.is_relative_to(p) for p in libraries) and 'site-packages' not in candidate.parts:
            return
        deny(event)

    def audit(event, args):
        if event == 'open':
            path, mode, flags = args
            writing = bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                          os.O_TRUNC | os.O_APPEND))
            contained(path, writing)
        elif event.startswith('socket.') or event in {'subprocess.Popen', 'os.system',
                'os.fork', 'os.posix_spawn', 'os.startfile', 'os.startfile/2'} or event.startswith(('os.exec', 'os.spawn')):
            deny(event)
        elif event in {'os.listdir', 'os.scandir'}:
            contained(args[0] if args else os.curdir)
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime', 'os.truncate', 'os.chdir'}:
            contained(args[0], True, event)
        elif event == 'os.rename':
            contained(args[0], True, event)
            contained(args[1], True, event)
        elif event in {'os.link', 'os.symlink'}:
            deny(event)

    sys.addaudithook(audit)
    return violations


def baseline_child(workspace):
    sink = LegacySink()
    recorded = []
    original = unittest.TextTestRunner.run

    def observe(runner, suite):
        result = original(runner, suite)
        recorded.append(result)
        return result

    unittest.TextTestRunner.run = observe
    sys.argv = ['lab/scripts/run_panel_tests.py', *CORE]
    violations = install_baseline_audit(workspace)
    exit_code = 1
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                runpy.run_path(str(workspace / 'snapshot/lab/scripts/run_panel_tests.py'),
                               run_name='__main__')
            except SystemExit as exc:
                exit_code = exc.code
    except Exception:
        pass
    finally:
        unittest.TextTestRunner.run = original
    result = recorded[0] if len(recorded) == 1 else None
    count = result.testsRun if result else 0
    skipped = len(result.skipped) if result else 0
    errors = len(result.errors) if result else 0
    failures = len(result.failures) if result else 0
    unexpected = len(violations) + int(sink.overflow)
    status = ('passed' if exit_code == 0 and count > 0 and not skipped and not unexpected else
              'blocked' if not result or unexpected or skipped else 'failed')
    sink.close()
    return dict(core_status=status, tests_run=count, skipped=skipped, errors=errors,
                failures=failures, violations=unexpected, denied_events=violations[:16])


def valid_observation(observed, names):
    counts = ('tests_run', 'skipped', 'errors', 'failures', 'violations')
    kinds = {'open', 'os.listdir', 'os.scandir', 'os.remove', 'os.rmdir',
             'os.mkdir', 'os.chmod', 'os.utime', 'os.truncate', 'os.chdir',
             'os.rename', 'os.link', 'os.symlink', 'subprocess.Popen',
             'os.system', 'os.fork', 'os.posix_spawn', 'os.startfile', 'os.startfile/2',
             'os.exec', 'os.spawn', 'socket.__new__', 'socket.connect', 'socket.bind',
             'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
             'socket.gethostname', 'socket.getnameinfo', 'socket.sendto'}
    if (not isinstance(observed, dict) or set(observed) !=
            {'core_status', *counts, 'denied_events'} or
            not isinstance(observed['core_status'], str) or
            observed['core_status'] not in {'passed', 'blocked', 'failed'} or
            any(type(observed[k]) is not int or not 0 <= observed[k] <= 1000000
                for k in counts) or not isinstance(observed['denied_events'], list) or
            len(observed['denied_events']) > min(16, observed['violations'])):
        return False
    if observed['core_status'] == 'passed' and (not observed['tests_run'] or
            any(observed[k] for k in counts[1:])):
        return False
    for event in observed['denied_events']:
        if (not isinstance(event, dict) or set(event) != {'kind', 'file', 'line'} or
                not isinstance(event['kind'], str) or event['kind'] not in kinds or
                not isinstance(event['file'], str) or event['file'] not in {'<outside>', *names} or
                type(event['line']) is not int or not 0 < event['line'] <= 1000000):
            return False
    return True


def run_baseline(root, workspace):
    result = dict(status='blocked', base=BASE, selected_modules=list(CORE),
                  excluded_modules=EXCLUDED, core_status='blocked', tests_run=0,
                  skipped=0, errors=0, failures=0, violations=0, denied_events=[])
    env = minimal_env(workspace)
    try:
        names_raw = bounded_child(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', BASE],
                                  root, env, timeout=30, cap=1048576)
        if names_raw is None:
            return result
        names = [n for n in names_raw.decode('utf-8').splitlines() if permitted(n)]
        if not set(SOURCES).issubset(names):
            return result
        raw = bounded_child(['git', '-C', str(root), 'archive', '--format=tar', BASE, '--', *names],
                            root, env, timeout=30, cap=32 * 1024 * 1024)
        if raw is None:
            return result
        snapshot = workspace / 'snapshot'
        snapshot.mkdir()
        extract_snapshot(raw, snapshot)
        (workspace / 'temp').mkdir()
        env = minimal_env(workspace / 'temp')
        script = Path(__file__).resolve().parents[1] / 'scripts/run_online_feasibility.py'
        raw_result = bounded_child([sys.executable, '-I', '-S', '-B', str(script),
            '--baseline-child', str(workspace)], workspace, env, timeout=600)
        if raw_result is None:
            return result
        observed = json.loads(raw_result)
        if not valid_observation(observed, names):
            return result
        result.update(observed)
    except (OSError, ValueError):
        return result
    return result
