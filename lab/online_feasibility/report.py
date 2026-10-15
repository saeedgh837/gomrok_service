"""Strict sanitized laboratory aggregation. Never grants release approval."""

REQUIRED = ('non_main_load', 'memory_log_debug', 'cdp_image', 'fallback_image',
            'image_failure', 'disk_negative', 'network_negative', 'process_negative',
            'stale_fallback', 'heartbeat_limit', 'inflight_model', 'receipt_order')
EVIDENCE_BY_CASE = {name: 'unchanged_body' for name in REQUIRED}
EVIDENCE_BY_CASE.update({name: 'negative_control' for name in (
    'disk_negative', 'network_negative', 'process_negative')})
EVIDENCE_BY_CASE['inflight_model'] = 'model'
UNRESOLVED = ('real_dependency_storage', 'production_bootstrap',
              'native_browser_storage', 'main_cancellation',
              'inflight_async_effects', 'external_reconciliation')


def build_report(hashes_before, hashes_after, baseline, cases):
    seen, clean = set(), []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'id', 'status', 'evidence_kind', 'violations'}:
            raise ValueError('CASE_SCHEMA_INVALID')
        if not isinstance(case['id'], str) or case['id'] not in REQUIRED or case['id'] in seen:
            raise ValueError('CASE_ID_INVALID')
        if case['evidence_kind'] != EVIDENCE_BY_CASE[case['id']]:
            raise ValueError('EVIDENCE_KIND_INVALID')
        if type(case['violations']) is not int or not 0 <= case['violations'] <= 16384:
            raise ValueError('VIOLATION_COUNT_INVALID')
        status = case['status']
        if not isinstance(status, str) or status not in {'passed', 'failed', 'blocked'}:
            status = 'blocked'
        seen.add(case['id'])
        clean.append(dict(case, status=status))
    if not isinstance(baseline, str) or baseline not in {'passed', 'failed', 'blocked'}:
        baseline = 'blocked'
    blocked = (baseline != 'passed' or seen != set(REQUIRED) or
               hashes_before != hashes_after or not hashes_before or
               any(c['status'] != 'passed' or c['violations'] for c in clean))
    return {'version': 1, 'decision': 'BLOCKED' if blocked else 'CONDITIONAL',
            'full_release_ready': False, 'baseline': baseline, 'cases': clean,
            'missing_cases': [name for name in REQUIRED if name not in seen],
            'unresolved': list(UNRESOLVED)}
