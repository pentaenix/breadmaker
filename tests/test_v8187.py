from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v8187_release_identity_and_review_details_binding():
    version=tuple(map(int,ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (8,18,7)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert "$('#editReviewDetailsBtn')?.addEventListener('click'" in js
    assert 'openReviewDetailsDialog(state.currentReviewId)' in js


def test_v8187_watchdog_excludes_pytest_failure_formatting_but_keeps_per_item_budget_policy():
    source=ROOT.joinpath('tests','conftest.py').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in source
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in source
    assert '_timeout_seconds_for_item' in source
    assert 'pytest_runtest_setup' in source and 'pytest_runtest_call' in source and 'pytest_runtest_teardown' in source
    assert '_breadmaker_execution_budget' in source
    assert 'BREADMAKER_VERIFY_RESULT_JSONL' in source
    assert 'BREADMAKER_FAILURE_REASON::' in source


def test_v8187_verifier_has_incremental_child_accounting_and_mismatch_guard():
    source=ROOT.joinpath('scripts','verify_release.py').read_text(encoding='utf-8')
    assert '_parse_result_jsonl' in source
    assert 'BREADMAKER_ITEM_ACCOUNTING_MISMATCH' in source
    assert 'expected_items' in source
