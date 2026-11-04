#!/usr/bin/env python3
"""Bounded synthetic feature smoke; never a substitute for the release gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import verify_release as verifier

PREFIX = 'test_v10575_feature_smoke_browser.py::test_feature_smoke_'
MOBILE = 'test_v10575_mobile_blockers_browser.py::'
RAILS = 'test_v10575_mobile_rails_browser.py::'
CASES = {
    'local': (
        PREFIX+'local_bookmark_create_remove_and_reopen',
        PREFIX+'local_marker_validation_save_remove_and_reopen',
        PREFIX+'local_voice_selection_generation_stop_and_reopen',
        MOBILE+'test_local_masked_password_and_text_dialogs',
        MOBILE+'test_local_modal_keyboard_nested_busy_and_portal',
        MOBILE+'test_local_project_menu_touch_hover_and_keyboard',
        MOBILE+'test_local_voice_pack_popup_keyboard_scope',
        RAILS+'test_local_phone_rails_creation_selection_focus_and_view_switch',
        RAILS+'test_local_landscape_narrow_and_normal_desktop_panels',
    ),
    'hosted': (
        'test_v10549_browser.py::test_startup_modal_is_interactive_without_revealing_editor',
        PREFIX+'hosted_bookmark_create_remove_and_reopen',
        PREFIX+'hosted_marker_validation_save_remove_and_reopen',
        'test_v10575_browser.py::test_hosted_uncached_visible_voice_can_be_selected_and_used_without_server_install',
        'test_v10575_browser.py::test_chapter_preparation_shows_progress_and_stop_prevents_late_generation',
        'test_v10569_narration_browser.py::test_hosted_narration_denial_clears_private_audio_worker_and_late_resume',
        MOBILE+'test_hosted_masked_password_and_text_dialogs',
        MOBILE+'test_hosted_modal_keyboard_nested_busy_and_portal',
        MOBILE+'test_hosted_project_menu_touch_hover_and_keyboard',
        MOBILE+'test_hosted_voice_pack_popup_keyboard_scope',
        RAILS+'test_hosted_phone_rails_creation_selection_focus_and_view_switch',
        RAILS+'test_hosted_landscape_narrow_and_normal_desktop_panels',
    ),
}


def select_jobs(mode):
    if mode not in ('both', *CASES):
        raise ValueError('Unknown feature smoke mode')
    names = [name for scope in CASES if mode in ('both', scope) for name in CASES[scope]]
    if not names:
        raise ValueError('Feature smoke discovery/accounting mismatch; requested scope is empty')
    discovered = verifier.discover_browser_jobs()
    jobs = [job for name in names for job in discovered if job.name == name]
    if (len(set(names)) != len(names) or len(jobs) != len(names)
            or {job.name for job in jobs} != set(names)
            or any(job.category != 'current browser' or job.expected_items != 1 for job in jobs)):
        raise ValueError('Feature smoke discovery/accounting mismatch; no tests may be silently omitted')
    return jobs


def result_report(jobs, results, *, mode, workers, elapsed):
    complete = (bool(jobs) and len(results) == len(jobs) and {r.job.name for r in results} == {j.name for j in jobs}
                and all(r.executed_items == 1 and r.passed_items+r.failed_items+r.skipped_items == 1
                        and r.skipped_items == 0 for r in results))
    return {
        'scope': mode, 'version': (ROOT/'VERSION').read_text().strip(),
        'release_gate': False, 'workers': workers, 'seconds': round(elapsed, 3),
        'expected': len(jobs), 'executed': sum(r.executed_items for r in results),
        'passed': sum(r.passed_items for r in results), 'failed': sum(r.failed_items for r in results),
        'skipped': sum(r.skipped_items for r in results), 'accounting_complete': complete,
        'failed_jobs': sum(not r.ok or r.executed_items != 1 or r.passed_items != 1 for r in results),
        'ok': complete and all(r.ok for r in results),
        'results': [dict(name=r.job.name, ok=r.ok and r.executed_items == 1 and r.passed_items == 1
                         and r.failed_items == 0 and r.skipped_items == 0, seconds=round(r.duration, 3),
                         retry_attempted=r.retry_attempted, retry_recovered=r.retry_recovered,
                         retry_seconds=round(r.retry_duration, 3), failures=r.child_failures,
                         initial_failures=r.initial_child_failures,
                         output=r.output, initial_output=r.initial_output) for r in results],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('both', 'local', 'hosted'), default='both')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    parser.add_argument('--list', action='store_true', help='List exact cases without launching a browser')
    parser.add_argument('--report', type=Path, help='Optional local JSON report including original/retry failures')
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = None
    try:
        jobs = select_jobs(args.mode)
        if args.list:
            print('\n'.join(job.name for job in jobs))
            print(f'{len(jobs)} exact browser items; scope={args.mode}; release gate unchanged')
            return 0
        verifier._assert_release_identity()
        verifier._assert_protected_integrity()
        verifier._browser_runtime_check()
        results = verifier.run_stage('Feature smoke', jobs, args.workers)
        verifier.retry_failed_current_browser(results, args.workers)
        report = result_report(jobs, results, mode=args.mode, workers=args.workers,
                               elapsed=time.monotonic()-started)
        for row in report['results']:
            print(f"{'PASS' if row['ok'] else 'FAIL'} {row['name']} ({row['seconds']}s)"
                  + (' [fresh-process retry recovered]' if row['retry_recovered'] else ''))
            if row['retry_attempted']:
                print('Initial attempt retained:\n'+row['initial_output'])
            if not row['ok']:
                print(row['output'])
        print(f"Feature smoke {'PASS' if report['ok'] else 'FAIL'}: {report['passed']} passed / {report['failed']} failed / "
              f"{report['skipped']} skipped; {report['executed']}/{report['expected']} items; "
              f"{report['failed_jobs']} failed jobs; accounting="
              f"{'complete' if report['accounting_complete'] else 'FAILED'}; {report['seconds']}s")
        return 0 if report['ok'] else 1
    except Exception as error:
        report = {'ok': False, 'scope': args.mode, 'release_gate': False, 'fatal': str(error)}
        print(f'Feature smoke FAILED: {error}', file=sys.stderr)
        return 2
    finally:
        if args.report and report is not None:
            args.report.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    raise SystemExit(main())
