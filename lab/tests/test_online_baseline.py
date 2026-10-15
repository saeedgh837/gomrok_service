import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import json
import os
import sys

from lab.online_feasibility.baseline import BASE, CORE, SOURCES, extract_snapshot, run_baseline
from lab.online_feasibility.runner import bounded_child, minimal_env


def descriptor_child(setup, operation):
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as temp:
        workspace = Path(temp) / 'private'
        workspace.mkdir()
        code = '''
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lab.online_feasibility.baseline import install_baseline_audit
workspace = Path(sys.argv[2])
''' + setup + '''
violations = install_baseline_audit(workspace)
denied = False
try:
''' + ''.join('    ' + line + '\n' for line in operation.splitlines()) + '''
except PermissionError:
    denied = True
print(json.dumps(dict(denied=denied, attempts=list(violations))))
'''
        raw = bounded_child([sys.executable, '-I', '-S', '-B', '-c', code,
                             str(root), str(workspace)], workspace, minimal_env(workspace))
        if raw is None:
            raise AssertionError('DESCRIPTOR_CHILD_FAILED')
        result = json.loads(raw)
        result['inside'] = ((workspace / 'allowed.bin').read_bytes()
                            if (workspace / 'allowed.bin').exists() else None)
        result['outside'] = ((workspace.parent / 'denied.bin').read_bytes()
                             if (workspace.parent / 'denied.bin').exists() else None)
        return result


class BaselineTests(unittest.TestCase):
    def test_verified_private_descriptor_preserves_atomic_write(self):
        result = descriptor_child(
            "fd, path = tempfile.mkstemp(dir=workspace)",
            "with os.fdopen(fd, 'w', encoding='utf-8') as out:\n"
            "    out.write('fixture')\n"
            "    out.flush()\n"
            "    os.fsync(out.fileno())\n"
            "os.replace(path, workspace / 'allowed.bin')")
        self.assertFalse(result['denied'], result['attempts'])
        self.assertEqual(result['attempts'], [])
        self.assertEqual(result['inside'], b'fixture')

    def test_descriptor_validation_rejects_outside_closed_pipe_and_reused_handles(self):
        outside = "outside = workspace.parent / 'denied.bin'\noutside.write_bytes(b'preserved')\n"
        for setup in (
                outside + "fd = os.open(outside, os.O_RDWR)",
                "fd = os.open(workspace / 'allowed.bin', os.O_RDWR | os.O_CREAT)\nos.close(fd)",
                "read_fd, fd = os.pipe()",
                outside + "fd = os.open(workspace / 'allowed.bin', os.O_RDWR | os.O_CREAT)\n"
                "other = os.open(outside, os.O_RDWR)\nos.dup2(other, fd)",
                outside + "os.link(outside, workspace / 'allowed.bin')\n"
                "fd = os.open(workspace / 'allowed.bin', os.O_RDWR)"):
            with self.subTest(setup=setup):
                result = descriptor_child(setup,
                    "with os.fdopen(fd, 'w', encoding='utf-8') as out:\n"
                    "    out.write('corrupted')")
                self.assertTrue(result['denied'])
                self.assertEqual(len(result['attempts']), 1)
                self.assertEqual(result['attempts'][0]['kind'], 'open')
                if result['outside'] is not None:
                    self.assertEqual(result['outside'], b'preserved')

    def test_descriptor_truncation_checks_current_os_target(self):
        result = descriptor_child(
            "outside = workspace.parent / 'denied.bin'\n"
            "outside.write_bytes(b'preserved')\nfd = os.open(outside, os.O_RDWR)",
            "os.truncate(fd, 0)")
        self.assertTrue(result['denied'])
        self.assertEqual(result['attempts'][0]['kind'], 'os.truncate')
        self.assertEqual(result['outside'], b'preserved')

    def test_archive_rejects_traversal_links_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, kind in (('../escape.py', tarfile.REGTYPE),
                               ('panel.py', tarfile.SYMTYPE),
                               ('upstream_proxy.json', tarfile.REGTYPE)):
                raw = io.BytesIO()
                with tarfile.open(fileobj=raw, mode='w') as archive:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    archive.addfile(info)
                with self.assertRaisesRegex(ValueError, '^BASELINE_ARCHIVE_INVALID$'):
                    extract_snapshot(raw.getvalue(), Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_audit_denials_keep_sanitized_event_and_callsite(self):
        import json
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            code = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lab.online_feasibility.baseline import install_baseline_audit
root = Path(sys.argv[2])
violations = install_baseline_audit(root)
try:
    open(root.parent / 'forbidden-fixture', 'r')
except PermissionError:
    pass
print(json.dumps(violations))
'''
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
                                     str(root), temp], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(result.returncode, 0)
            attempts = json.loads(result.stdout)
            self.assertEqual(attempts[0]['kind'], 'open')
            self.assertEqual(attempts[0]['file'], '<outside>')
            self.assertGreater(attempts[0]['line'], 0)
            self.assertNotIn(temp, result.stdout.decode())

    def test_child_evidence_rejects_unsanitized_denial_records(self):
        import json
        from unittest.mock import patch
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as archive:
            for name in SOURCES:
                member = tarfile.TarInfo(name)
                archive.addfile(member)
        observed = dict(core_status='blocked', tests_run=1, skipped=0, errors=0,
                        failures=0, violations=1,
                        denied_events=[dict(kind='open', file='fixture-secret', line=1)])
        with tempfile.TemporaryDirectory() as temp, patch(
                'lab.online_feasibility.baseline.bounded_child', side_effect=[
                    '\n'.join(SOURCES).encode(), raw.getvalue(), json.dumps(observed).encode()]):
            result = run_baseline(Path.cwd(), Path(temp))
        self.assertEqual(result['denied_events'], [])
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_snapshot_includes_reviewed_transitive_dependencies(self):
        self.assertIn('jalali.py', SOURCES)
        self.assertIn('log_utils.py', SOURCES)

    def test_pinned_snapshot_executes_reviewed_core_without_full_pass_claim(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            result = run_baseline(root, Path(temp))
            self.assertEqual(result['base'], BASE)
            self.assertEqual(result['selected_modules'], list(CORE))
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(len(result['excluded_modules']), 3)
            self.assertEqual(result['core_status'], 'passed')
            self.assertGreater(result['tests_run'], 0)
            for field in ('errors', 'failures', 'skipped', 'violations'):
                self.assertEqual(result[field], 0, result['denied_events'])
