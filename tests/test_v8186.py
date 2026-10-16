from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import schema as storage_schema

from pathlib import Path

import scripts.verify_release as verify_release
from workspace import APP_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_v8186_release_identity_and_review_helper_block_are_complete():
    version = tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (8, 18, 6)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION >= 39
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    for function_name in (
        'renderReviewParticipantsPanel',
        'participantNameModal',
        'createReviewParticipantDialog',
        'renameReviewParticipantDialog',
        'deleteReviewParticipant',
        'openReviewParticipantsPopulateMenu',
        'reuseReviewParticipantsDialog',
        'openReviewCyclePopulateModal',
    ):
        assert f'function {function_name}' in js or f'async function {function_name}' in js
    assert js.count('function openReviewParticipantsPanel(')==1
    assert "renderReviewParticipantsPanel();syncReviewToolbar(review)" in js
    assert "action:()=>openReviewCyclePopulateModal()" in js
    assert "$('#editReviewDetailsBtn')?.addEventListener('click'" in js


def test_v8186_core_execution_plan_batches_normal_modules_but_keeps_reliability_isolated():
    exact = [
        verify_release.Job('tests/test_v8110.py::test_a', 'core/API', 'tests/test_v8110.py::test_a', 35),
        verify_release.Job('tests/test_v8110.py::test_b', 'core/API', 'tests/test_v8110.py::test_b', 35),
        verify_release.Job('tests/test_reliability.py::test_r[1]', 'core/API', 'tests/test_reliability.py::test_r[1]', 35),
        verify_release.Job('tests/test_reliability.py::test_r[2]', 'core/API', 'tests/test_reliability.py::test_r[2]', 35),
    ]
    planned = verify_release.plan_core_execution_jobs(exact)
    normal = [job for job in planned if job.target == 'tests/test_v8110.py']
    reliability = [job for job in planned if 'test_reliability.py::' in job.target]
    assert len(normal) == 1 and normal[0].expected_items == 2
    assert len(reliability) == 2 and all(job.expected_items == 1 for job in reliability)


def test_v8186_verifier_preserves_child_failures_and_failure_phase_metadata():
    source = ROOT.joinpath('scripts', 'verify_release.py').read_text(encoding='utf-8')
    conftest = ROOT.joinpath('tests', 'conftest.py').read_text(encoding='utf-8')
    assert '--junitxml' in source
    assert '_parse_junit' in source
    assert '_parse_result_jsonl' in source
    assert 'BREADMAKER_VERIFY_RESULT_JSONL' in source
    assert 'child_failures' in source
    assert 'BREADMAKER_FAILURE_PHASE::' in conftest
    assert '"setup":"setup","call":"test body","teardown":"teardown"' in source
