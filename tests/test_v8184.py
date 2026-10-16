from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import schema as storage_schema

from pathlib import Path

import scripts.verify_release as verify_release
from workspace import APP_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_v8184_verification_hardening_keeps_schema_and_release_identity():
    version = tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (8, 18, 4)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION >= 39


def test_v8184_release_manifest_covers_every_current_core_and_browser_contract():
    core_targets = {job.target for job in verify_release.discover_core_jobs()}
    assert core_targets == verify_release._expected_core_nodeids()

    browser_targets = {job.target for job in verify_release.discover_browser_jobs()}
    assert browser_targets == verify_release._expected_browser_nodeids()
    assert any(target.endswith('/tests/test_e2e.py') or target.endswith('tests/test_e2e.py') for target in browser_targets)
    assert any('test_v8184_browser.py::' in target for target in browser_targets)


def test_v8184_review_toolbar_controls_have_explicit_live_bindings():
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    expected = {
        'reviewParticipantsBtn': 'toggleReviewParticipantsPanel',
        'reviewMarkersBtn': 'toggleReviewMarkersPanel',
        'reviewQuestionsBtn': 'toggleReviewQuestionsPanel',
        'reviewStateBtn': 'toggleReviewStatePanel',
    }
    for control, handler in expected.items():
        assert f"$('#{control}')?.addEventListener('click',{handler})" in js


def test_v8184_verifier_is_parallel_observable_and_process_isolated():
    source = ROOT.joinpath('scripts', 'verify_release.py').read_text(encoding='utf-8')
    launcher = ROOT.joinpath('verify-release').read_text(encoding='utf-8')
    assert 'ThreadPoolExecutor' in source
    assert 'Durable E2E' in source and 'Current browser' in source and 'Core / API' in source
    assert 'PROCESS TIMEOUT' in source
    assert 'oldest' in source and 'active' in source
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD' in source
    assert 'start_new_session=(os.name == "posix")' in source
    assert 'scripts/verify_release.py "$@"' in launcher
