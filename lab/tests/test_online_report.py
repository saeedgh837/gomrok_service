import json
import tempfile
import unittest
from pathlib import Path

from lab.online_feasibility.report import EVIDENCE_BY_CASE, REQUIRED, build_report
from lab.online_feasibility.runner import bounded_child, minimal_env, validate_hashes, validate_scratch
from lab.online_feasibility.inventory import APPROVED_ROBOT, FILES


class ReportTests(unittest.TestCase):
    def cases(self):
        return [{'id': name, 'status': 'passed', 'evidence_kind': EVIDENCE_BY_CASE[name],
                 'violations': 0} for name in REQUIRED]

    def test_all_lab_cases_never_claim_production_pass(self):
        report = build_report({'gomrok.py': 'same'}, {'gomrok.py': 'same'}, 'passed', self.cases())
        self.assertEqual(report['decision'], 'CONDITIONAL')
        self.assertFalse(report['full_release_ready'])
        self.assertIn('real_dependency_storage', report['unresolved'])
        self.assertEqual(report['missing_cases'], [])

    def test_missing_or_blocked_baseline_blocks(self):
        for baseline, cases in (('blocked', self.cases()), ('passed', [])):
            report = build_report({'gomrok.py': 'same'}, {'gomrok.py': 'same'}, baseline, cases)
            self.assertEqual(report['decision'], 'BLOCKED')

    def test_changed_source_or_swallowed_violation_blocks(self):
        cases = self.cases()
        cases[0]['violations'] = 1
        for after, observed in (({'gomrok.py': 'different'}, self.cases()),
                                ({'gomrok.py': 'same'}, cases)):
            report = build_report({'gomrok.py': 'same'}, after, 'passed', observed)
            self.assertEqual(report['decision'], 'BLOCKED')

    def test_skipped_status_is_blocked_and_not_echoed(self):
        for status in ('skipped', 'fixture-secret', None, []):
            cases = self.cases()
            cases[0]['status'] = status
            report = build_report({'x': 'same'}, {'x': 'same'}, 'fixture-secret', cases)
            self.assertEqual(report['decision'], 'BLOCKED')
            self.assertNotIn('fixture-secret', json.dumps(report))

    def test_unknown_fields_and_bad_counts_are_rejected(self):
        for field, value, error in (('extra', 'fixture-secret', 'CASE_SCHEMA_INVALID'),
                                    ('violations', True, 'VIOLATION_COUNT_INVALID'),
                                    ('violations', -1, 'VIOLATION_COUNT_INVALID'),
                                    ('evidence_kind', 'model', 'EVIDENCE_KIND_INVALID'),
                                    ('id', [], 'CASE_ID_INVALID')):
            cases = self.cases()
            cases[0][field] = value
            with self.assertRaisesRegex(ValueError, '^' + error + '$'):
                build_report({}, {}, 'passed', cases)

    def test_duplicate_and_unknown_cases_are_rejected(self):
        for cases in (self.cases() + [self.cases()[0]],
                      [{'id': 'fixture-secret', 'status': 'passed',
                        'evidence_kind': 'model', 'violations': 0}]):
            with self.assertRaisesRegex(ValueError, '^CASE_ID_INVALID$'):
                build_report({}, {}, 'passed', cases)


class RunnerBoundaryTests(unittest.TestCase):
    def test_hash_boundary_rejects_missing_and_unapproved_sources(self):
        hashes = {name: 'a' * 64 for name in FILES}
        hashes['gomrok.py'] = APPROVED_ROBOT
        validate_hashes(hashes)
        for changed in ({}, dict(hashes, extra='b' * 64),
                        dict(hashes, **{'gomrok.py': 'b' * 64}),
                        dict(hashes, **{'crm.py': 'SAME'})):
            with self.assertRaisesRegex(ValueError, '^SOURCE_HASH_INVALID$'):
                validate_hashes(changed)

    def test_scratch_rejects_nonempty_and_source_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            with self.assertRaisesRegex(ValueError, '^SCRATCH_INVALID$'):
                validate_scratch(source / 'nested', source)
            scratch = root / 'scratch'
            scratch.mkdir()
            (scratch / 'existing.txt').write_text('fixture')
            with self.assertRaisesRegex(ValueError, '^SCRATCH_INVALID$'):
                validate_scratch(scratch, source)

    def test_child_timeout_and_output_overflow_fail_closed(self):
        import sys
        with tempfile.TemporaryDirectory() as temp:
            cwd = Path(temp)
            for code, timeout in (("import time; time.sleep(30)", 0.2),
                                  ("print('x' * 20000)", 5)):
                result = bounded_child([sys.executable, '-I', '-S', '-B', '-c', code],
                                       cwd, minimal_env(cwd), timeout=timeout)
                self.assertIsNone(result)

    def test_candidate_identity_rejects_missing_or_modified_legacy_prerequisites(self):
        import shutil
        from lab.online_feasibility import baseline
        self.assertTrue(callable(getattr(baseline, 'candidate_identity', None)))
        root = Path(__file__).resolve().parents[2]
        names = (set(FILES) | set(baseline.SOURCES) |
                 {'lab/tests/' + name + '.py' for name in baseline.EXCLUDED} |
                 {'lab/tests/test_v1_2_0_login_gate.py'})
        names.update(p.relative_to(root).as_posix() for p in (root / 'cloud_sync').glob('*.py'))
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            for name in names:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
            identity = baseline.candidate_identity(source, root, Path(temp) / 'scratch')
            self.assertEqual(identity['base'], baseline.BASE)
            self.assertEqual(set(identity['files']), names)
            for name in ('lab/tests/test_crm_validation.py', 'log_utils.py',
                         'lab/tests/test_auth_hardening.py', 'cloud_sync/__init__.py'):
                path = source / name
                original = path.read_bytes()
                for contents in (None, original + b'\n# changed synthetic candidate\n'):
                    with self.subTest(name=name, missing=contents is None):
                        if contents is None:
                            path.unlink()
                        else:
                            path.write_bytes(contents)
                        with self.assertRaisesRegex(ValueError, '^CANDIDATE_PREREQUISITE_INVALID$'):
                            baseline.candidate_identity(source, root, Path(temp) / 'scratch')
                        path.write_bytes(original)
            path = source / 'log_utils.py'
            path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
            normalized = baseline.candidate_identity(source, root, Path(temp) / 'scratch')
            self.assertEqual(normalized['files']['log_utils.py']['normalized_sha256'],
                             identity['files']['log_utils.py']['normalized_sha256'])
            self.assertFalse((Path(temp) / 'scratch').exists())

    def test_cli_modified_dependency_is_blocked_before_fixture_execution(self):
        import shutil
        import subprocess
        import sys
        from lab.online_feasibility import baseline
        root = Path(__file__).resolve().parents[2]
        names = (set(FILES) | set(baseline.SOURCES) |
                 {'lab/tests/' + name + '.py' for name in baseline.EXCLUDED} |
                 {'lab/tests/test_v1_2_0_login_gate.py'})
        names.update(p.relative_to(root).as_posix() for p in (root / 'cloud_sync').glob('*.py'))
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            for name in names:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
            with (source / 'log_utils.py').open('ab') as out:
                out.write(b'\n# changed synthetic dependency\n')
            scratch = Path(temp) / 'run'
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(root / 'lab/scripts/run_online_feasibility.py'),
                '--root', str(source), '--scratch', str(scratch)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['decision'], 'BLOCKED')
            self.assertFalse(scratch.exists())

    def test_cli_executes_all_twelve_cases_and_preserves_blocked_baseline(self):
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp) / 'run'
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(root / 'lab/scripts/run_online_feasibility.py'),
                '--root', str(root), '--scratch', str(scratch)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90)
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertEqual(report['decision'], 'BLOCKED')
            self.assertEqual(report['baseline'], 'blocked')
            self.assertFalse(report['full_release_ready'])
            self.assertEqual({c['id'] for c in report['cases']}, set(REQUIRED))
            self.assertTrue(all(c['status'] == 'passed' and c['violations'] == 0
                                for c in report['cases']), report['cases'])
            self.assertEqual(json.loads((scratch / 'report.json').read_text()), report)
            self.assertNotIn(str(root), result.stdout.decode())
            self.assertEqual(report['hashes_before'], report['hashes_after'])
            self.assertEqual(report['candidate_identity_before'], report['candidate_identity_after'])
            self.assertTrue(report['candidate_identity_stable'])
            self.assertFalse(report['runtime_call_coverage_measured'])
            for probe in report['coverage']:
                self.assertNotIn('call_lines', probe)
                self.assertEqual(probe['line_evidence'], 'static_lexical_candidates')
                self.assertTrue(probe['static_candidate_call_lines'])
                self.assertTrue(set(probe['fixture_case_ids']).issubset(REQUIRED))
                self.assertTrue(probe['fixture_case_ids'])
                self.assertEqual(probe['fixture_branch_evidence'],
                                 'declared_case_mapping_not_traced')
                self.assertTrue(probe['fixture_branches'])
            self.assertEqual(report['baseline_evidence']['core_status'], 'passed')
            self.assertEqual(report['baseline_evidence']['violations'], 0)

    def test_cli_changed_robot_is_blocked_before_scratch_creation(self):
        import shutil
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            source.mkdir()
            for name in FILES:
                shutil.copyfile(root / name, source / name)
            with (source / 'gomrok.py').open('ab') as out:
                out.write(b'\n# changed fixture\n')
            scratch = Path(temp) / 'run'
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(root / 'lab/scripts/run_online_feasibility.py'),
                '--root', str(source), '--scratch', str(scratch)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['decision'], 'BLOCKED')
            self.assertFalse(scratch.exists())

    def test_bounded_protocol_preserves_valid_small_result(self):
        import sys
        with tempfile.TemporaryDirectory() as temp:
            result = bounded_child([sys.executable, '-I', '-S', '-B', '-c',
                                    'print("{\\"ok\\":true}")'], Path(temp), minimal_env(Path(temp)), timeout=5)
            self.assertEqual(json.loads(result), {'ok': True})
