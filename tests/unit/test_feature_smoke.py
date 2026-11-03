"""Smoke selection, accounting and failure propagation; no browser processes."""
import json

import pytest
from scripts import verify_feature_smoke as smoke
from scripts import verify_release as verifier


def _result(*, code=0, passed=1, failed=0, skipped=0):
    job = verifier.Job('synthetic browser', 'current browser', 'synthetic.py::test_ui', 45)
    return verifier.Result(job, code, 'synthetic output', .1, executed_items=1,
                           passed_items=passed, failed_items=failed, skipped_items=skipped)


def test_smoke_cases_are_real_distinct_exact_items_in_standard_full_discovery():
    local, hosted = smoke.select_jobs('local'), smoke.select_jobs('hosted')
    both = smoke.select_jobs('both')
    assert len(local) == 9 and len(hosted) == 12 and len(both) == 21
    assert {j.name for j in both} == {j.name for j in local+hosted}
    assert all(j.expected_items == 1 and j.category == 'current browser' for j in both)


def test_startup_modal_interaction_is_selected_once_in_hosted_smoke():
    target = 'test_v10549_browser.py::test_startup_modal_is_interactive_without_revealing_editor'
    hosted = smoke.select_jobs('hosted')
    assert sum(job.name == target for job in hosted) == 1
    assert target not in {job.name for job in smoke.select_jobs('local')}


def test_missing_or_duplicated_discovery_is_a_hard_failure(monkeypatch):
    jobs = verifier.discover_browser_jobs()
    target = smoke.CASES['hosted'][0]
    monkeypatch.setattr(verifier, 'discover_browser_jobs', lambda: [j for j in jobs if j.name != target])
    with pytest.raises(ValueError, match='discovery/accounting'):
        smoke.select_jobs('both')
    one = next(j for j in jobs if j.name == target)
    monkeypatch.setattr(verifier, 'discover_browser_jobs', lambda: jobs+[one])
    with pytest.raises(ValueError, match='discovery/accounting'):
        smoke.select_jobs('both')


def test_empty_requested_manifest_fails_before_any_browser_discovery(monkeypatch):
    monkeypatch.setattr(smoke, 'CASES', {'local': (), 'hosted': ()})
    def forbidden():
        raise AssertionError('An empty scope must fail before discovery or browser setup')
    monkeypatch.setattr(verifier, 'discover_browser_jobs', forbidden)
    with pytest.raises(ValueError, match='requested scope is empty'):
        smoke.select_jobs('both')


def test_empty_expected_and_executed_sets_cannot_report_success():
    report = smoke.result_report([], [], mode='both', workers=1, elapsed=.1)
    assert report['expected'] == report['executed'] == 0
    assert report['ok'] is False and report['accounting_complete'] is False


@pytest.mark.parametrize('change', ['missing', 'skip', 'process_failure', 'failed', 'incorrect_count'])
def test_report_cannot_turn_incomplete_skipped_or_failed_work_green(change):
    result = _result()
    results = [result]
    if change == 'missing':
        results = []
    elif change == 'skip':
        result.passed_items, result.skipped_items = 0, 1
    elif change == 'process_failure':
        result.returncode = 124
    elif change == 'failed':
        result.passed_items, result.failed_items = 0, 1
    else:
        result.executed_items = 2
    report = smoke.result_report([result.job], results, mode='local', workers=1, elapsed=.1)
    assert report['ok'] is False
    if results:
        assert report['failed_jobs'] == 1 and report['results'][0]['ok'] is False


def test_recovered_retry_retains_original_failure_evidence():
    result = _result()
    result.retry_attempted = result.retry_recovered = True
    result.initial_output = 'Initial browser failure'
    result.initial_child_failures = [('synthetic.py::test_ui', 'Original error', 'call')]
    result.retry_duration = .2
    report = smoke.result_report([result.job], [result], mode='local', workers=2, elapsed=.3)
    assert report['ok'] and report['accounting_complete']
    row = report['results'][0]
    assert row['retry_recovered'] and row['initial_output'] == 'Initial browser failure'
    assert row['initial_failures'] == result.initial_child_failures
    assert row['retry_seconds'] == .2


def test_list_never_starts_tests_and_worker_bound_is_enforced(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('list must not launch a browser')
    monkeypatch.setattr(verifier, '_browser_runtime_check', forbidden)
    monkeypatch.setattr(verifier, 'run_stage', forbidden)
    assert smoke.main(['--list']) == 0
    with pytest.raises(SystemExit) as error:
        smoke.main(['--workers', '3'])
    assert error.value.code == 2


def test_failed_case_exits_nonzero_and_saves_failure_and_retry(monkeypatch, tmp_path, capsys):
    result = _result(code=1, passed=0, failed=1)
    result.child_failures = [('synthetic.py::test_ui', 'HOSTED_ACTION_ENTITY_UNSUPPORTED', 'call')]
    monkeypatch.setattr(smoke, 'select_jobs', lambda mode: [result.job])
    for name in ('_assert_release_identity', '_assert_protected_integrity', '_browser_runtime_check'):
        monkeypatch.setattr(verifier, name, lambda: None)
    monkeypatch.setattr(verifier, 'run_stage', lambda *args: [result])
    retried = []
    monkeypatch.setattr(verifier, 'retry_failed_current_browser', lambda *args: retried.append(args))
    report = tmp_path/'report.json'
    assert smoke.main(['--report', str(report)]) == 1
    assert len(retried) == 1
    saved = json.loads(report.read_text())
    assert saved['ok'] is False and saved['failed'] == 1
    assert saved['results'][0]['failures'] == [list(result.child_failures[0])]
    assert 'FAIL synthetic browser' in capsys.readouterr().out
