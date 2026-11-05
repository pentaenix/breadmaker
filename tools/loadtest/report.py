#!/usr/bin/env python3
"""Summarize sanitized k6 evidence; optionally reject a latency regression."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

OPERATIONS = {'chapter_read', 'project_navigation', 'maps_read', 'save_preread',
              'save_verify', 'chapter_save', 'map_create', 'map_delete'}

def metric(summary, name, statistic, default=0):
    return summary.get('metrics', {}).get(name, {}).get('values', {}).get(statistic, default)


def read_experiment(directory):
    directory = Path(directory)
    experiment = json.loads((directory / 'experiment.json').read_text())
    required_operations = ({'chapter_read', 'project_navigation', 'maps_read'}
        if experiment.get('workload') == 'read-only' else OPERATIONS)
    runs = []
    for vus in experiment['vus']:
        root = directory / f'vus-{vus}'
        status = json.loads((root / 'status.json').read_text())
        if not (root / 'k6-summary.json').exists():
            runs.append(dict(vus=vus, passed=False, error=status.get('error') or 'k6 summary missing'))
            continue
        summary = json.loads((root / 'k6-summary.json').read_text())
        observations = (json.loads((root / 'server-observations.json').read_text())
                        if (root / 'server-observations.json').exists() else {})
        emulator_cleanup = status.get('emulator_cleanup') or {}
        cleanup_valid = experiment.get('adapter') != 'firestore-emulator' or (
            all(emulator_cleanup.get(key) is True for key in ('data_cleared', 'empty_verified', 'endpoint_closed'))
            and emulator_cleanup.get('error') is None)
        checks = {f'{name}: {condition}': value['ok']
                  for name, item in summary['metrics'].items()
                  for condition, value in item.get('thresholds', {}).items()}
        endpoints = {}
        for name in summary['metrics']:
            if name.startswith('http_req_duration{op:'):
                op = name.split('op:')[1].rstrip('}')
                endpoints[op] = dict(
                    count=metric(summary, f'http_reqs{{op:{op}}}', 'count'),
                    p95_ms=metric(summary, name, 'p(95)'), p99_ms=metric(summary, name, 'p(99)'))
        runs.append(dict(vus=vus,
            passed=status['k6_exit_code'] == 0 and not status.get('error') and
                   status.get('temporary_data_removed') is True and cleanup_valid and bool(observations) and
                   bool(checks) and all(checks.values()) and required_operations.issubset(endpoints) and
                   all(item['count'] > 0 for item in endpoints.values()),
            error=status.get('error'),
            requests=metric(summary, 'http_reqs', 'count'), rps=metric(summary, 'http_reqs', 'rate'),
            p95_ms=metric(summary, 'http_req_duration', 'p(95)'),
            p99_ms=metric(summary, 'http_req_duration', 'p(99)'),
            error_rate=metric(summary, 'application_errors', 'rate'),
            throttles=metric(summary, 'throttled_requests', 'count'),
            conflicts=metric(summary, 'conflict_requests', 'count'),
            dropped_iterations=metric(summary, 'dropped_iterations', 'count'),
            completed_actions=metric(summary, 'completed_actions', 'count'),
            endpoints=endpoints, failed_thresholds=[name for name, ok in checks.items() if not ok],
            observations=observations))
    return dict(experiment=experiment, runs=runs)


def regressions(current, baseline, relative=.20, absolute_ms=20):
    # Compare like workloads. Changing population or payload is an experiment,
    # not a code-regression comparison. Both must be independently passing.
    for field in ('dataset', 'profile', 'seconds', 'arrival_actions_per_second', 'k6_version', 'platform'):
        if current['experiment'].get(field) != baseline['experiment'].get(field):
            raise ValueError(f'Baseline mismatch: {field}')
    for field, default in (('adapter', 'memory'), ('account_lifecycle_fence', False), ('workload', 'mixed')):
        if current['experiment'].get(field, default) != baseline['experiment'].get(field, default):
            raise ValueError(f'Baseline mismatch: {field}')
    if current['experiment'].get('emulator') != baseline['experiment'].get('emulator'):
        raise ValueError('Baseline mismatch: emulator version or runtime')
    previous = {row['vus']: row for row in baseline['runs']}
    failures = []
    for row in current['runs']:
        before = previous.get(row['vus'])
        if not before or not row['passed'] or not before['passed']:
            raise ValueError('Comparison requires matching successful VU runs')
        for op, values in row['endpoints'].items():
            if op not in before['endpoints']:
                raise ValueError(f'Baseline missing operation {op}')
            old = before['endpoints'][op]['p95_ms']
            new = values['p95_ms']
            if new - old > absolute_ms and new > old * (1 + relative):
                failures.append(dict(vus=row['vus'], operation=op, before_ms=old, after_ms=new))
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    report = read_experiment(args.directory)
    if args.compare:
        report['regressions'] = regressions(report, read_experiment(args.compare))
    print('| VUs | HTTP requests | req/s | p95 ms | p99 ms | errors | result |')
    print('| --- | --- | --- | --- | --- | --- | --- |')
    for row in report['runs']:
        if 'requests' not in row:
            print(f"| {row['vus']} | — | — | — | — | — | FAIL: {row['error']} |")
        else:
            print(f"| {row['vus']} | {row['requests']} | {row['rps']:.1f} | {row['p95_ms']:.1f} | {row['p99_ms']:.1f} | {row['error_rate']:.1%} | {'PASS' if row['passed'] else 'FAIL'} |")
    (args.directory / 'report.json').write_text(json.dumps(report, indent=2))
    if report.get('regressions'):
        print('Latency regressions:', json.dumps(report['regressions']))
    print(f"Local adapter: {report['experiment'].get('adapter', 'memory')}; synthetic sessions/objects, not production capacity.")
    print('Use endpoint sample counts; p99 with fewer than 100 samples is exploratory.')
    return 1 if any(not row['passed'] for row in report['runs']) or report.get('regressions') else 0


if __name__ == '__main__':
    raise SystemExit(main())
