"""Run the fixture-only Phase A gate. A successful lab is not a release approval."""

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT))

from lab.online_feasibility.baseline import baseline_child, candidate_identity, run_baseline
from lab.online_feasibility.cases import run_case
from lab.online_feasibility.inventory import build_inventory, require_approved_robot, source_hash
from lab.online_feasibility.report import EVIDENCE_BY_CASE, REQUIRED, build_report
from lab.online_feasibility.runner import bounded_child, minimal_env, validate_hashes, validate_scratch
from lab.online_feasibility.storage import checked_path

HARNESS = tuple('lab/online_feasibility/' + name for name in (
    '__init__.py', 'inventory.py', 'storage.py', 'probe.py', 'fencing.py',
    'cases.py', 'report.py', 'runner.py', 'baseline.py')) + ('lab/scripts/run_online_feasibility.py',)
EXERCISED = ('log_event', 'DebugController._dump_state', '_full_screenshot',
             'handle_already_registered', 'handle_receipt_and_payment',
             '_start_profile_heartbeat')
# Which fixture cases actually exercise each selected owner. Call lines below
# are static lexical candidates only; runtime_call_coverage_measured stays
# false because no line-level execution trace is collected.
FIXTURE_BRANCHES = {
    'log_event': (('memory_log_debug',),
                  ('bounded_ram_log_record_without_physical_log_file',)),
    'DebugController._dump_state': (('memory_log_debug',),
                                    ('bounded_ram_debug_dump_without_physical_log_file',)),
    '_full_screenshot': (('cdp_image', 'fallback_image', 'image_failure'),
                         ('primary_image', 'fallback_image', 'failed_image_rejected')),
    'handle_already_registered': (('stale_fallback',),
                                  ('revoked_dispatch_rejected',)),
    'handle_receipt_and_payment': (('receipt_order',),
                                   ('receipt_before_payment', 'revoked_result_not_published')),
    '_start_profile_heartbeat': (('heartbeat_limit',),
                                 ('unjoined_heartbeat_keeps_guarded_fakes',)),
}


def execute(root, scratch):
    root = checked_path(root)
    scratch = validate_scratch(scratch, root)
    require_approved_robot(root)
    candidate_before = candidate_identity(root, CODE_ROOT, scratch)
    inventory = build_inventory(root)
    before = inventory['source_hashes']
    validate_hashes(before)
    scratch.mkdir(parents=True, exist_ok=True)
    tooling = scratch / 'tooling'
    for name in HARNESS:
        target = tooling / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CODE_ROOT / name, target)
        if source_hash(target) != source_hash(CODE_ROOT / name):
            raise ValueError('HARNESS_COPY_MISMATCH')
    harness_hashes = {name: source_hash(tooling / name) for name in HARNESS}
    script = tooling / 'lab/scripts/run_online_feasibility.py'
    cases = []
    for name in REQUIRED:
        case_root = scratch / name
        (case_root / 'app').mkdir(parents=True)
        shutil.copyfile(root / 'gomrok.py', case_root / 'app/gomrok.py')
        require_approved_robot(case_root / 'app')
        case = dict(id=name, status='failed', evidence_kind=EVIDENCE_BY_CASE[name], violations=0)
        raw = bounded_child([sys.executable, '-I', '-S', '-B', str(script),
                             '--case', name, '--scratch', str(case_root)],
                            case_root, minimal_env(case_root))
        if raw is not None:
            try:
                observed = json.loads(raw)
                build_report(before, before, 'blocked', [observed])
                if observed['id'] == name:
                    case = observed
            except (ValueError, TypeError):
                pass
        cases.append(case)
    baseline_root = scratch / 'baseline'
    baseline_root.mkdir()
    baseline = run_baseline(CODE_ROOT, baseline_root)
    after = build_inventory(root)['source_hashes']
    validate_hashes(after)
    candidate_after = candidate_identity(root, CODE_ROOT, scratch)
    candidate_stable = candidate_before == candidate_after
    report = build_report(before, after, baseline['status'] if candidate_stable else 'blocked', cases)
    report.update(hashes_before=before, hashes_after=after, harness_hashes=harness_hashes,
                  candidate_identity_before=candidate_before, candidate_identity_after=candidate_after,
                  candidate_identity_stable=candidate_stable,
                  baseline_evidence=baseline,
                  inventory_counts=dict(files=len(before), calls=len(inventory['sites']),
                                        imports=len(inventory['imports']),
                                        kinds=dict(Counter(s['kind'] for s in inventory['sites']))),
                  runtime_call_coverage_measured=False,
                  coverage=[dict(file='gomrok.py', owner=owner,
                      line_evidence='static_lexical_candidates',
                      static_candidate_call_lines=[s['line'] for s in inventory['sites']
                                  if s['file'] == 'gomrok.py' and s['owner'] == owner],
                      fixture_case_ids=list(FIXTURE_BRANCHES[owner][0]),
                      fixture_branch_evidence='declared_case_mapping_not_traced',
                      fixture_branches=list(FIXTURE_BRANCHES[owner][1]),
                      scope='selected_branches_with_inert_dependencies')
                      for owner in EXERCISED],
                  fake_dependencies=['panel', 'app_paths', 'system_debug', 'selenium',
                     'browser_visibility', 'captcha_collector', 'live_solve',
                     'driver', 'telegram_delivery', 'payment_link_extraction', 'time', 'os_facade'],
                  not_run=['host_schema', 'host_api', 'real_browser', 'solver', 'robot_main',
                           'production', 'release', 'install'],
                  commands=['python -I -S -B lab/scripts/run_online_feasibility.py --root <source> --scratch <private>',
                            'python -I -S -B lab/scripts/run_panel_tests.py ' + ' '.join(baseline['selected_modules'])],
                  audit_limitations=['python_audit_not_os_sandbox', 'native_io_not_monitored',
                                     'startup_imports_precede_audit', 'same_user_path_races_not_prevented'])
    (scratch / 'inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    (scratch / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--case', choices=REQUIRED, help=argparse.SUPPRESS)
    parser.add_argument('--baseline-child', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.baseline_child:
        report = baseline_child(args.baseline_child)
    elif args.case and args.scratch:
        report = run_case(args.case, args.scratch)
    elif args.root and args.scratch:
        try:
            report = execute(args.root, args.scratch)
        except (ValueError, OSError, SyntaxError):
            report = build_report({}, {}, 'blocked', [])
            report['reason'] = 'SOURCE_OR_SCRATCH_PREREQUISITE_INVALID'
        except Exception:
            print(json.dumps({'decision': 'BLOCKED', 'full_release_ready': False,
                              'reason': 'HARNESS_DEFECT'}))
            return 1
    else:
        parser.error('--root and --scratch are required')
    print(json.dumps(report, ensure_ascii=True, separators=(',', ':')))
    return 2 if report.get('decision') == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
