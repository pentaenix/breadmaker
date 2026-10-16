from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]




def test_v81812_durable_items_share_runtime_but_not_project_state():
    durable = (ROOT / 'tests/test_e2e.py').read_text(encoding='utf-8')
    assert '@pytest.fixture(autouse=True)' in durable
    assert 'def durable_fresh_project(' in durable
    assert 'live_server+"/api/projects"' in durable
    assert 'headers={"X-Breadmaker-Client":"e2e-tab"}' in durable
    assert '_E2E_SHELL_HTML=None' in durable and 'def _browser_assets()' in durable
    assert 'state.lastManuscriptRange=r.cloneRange();positionSelectionToolbar(r)' in durable
    assert "assert first_lane >= 114" in durable
    assert 'state.structuralChain.then(()=>true)' in durable


def test_v81812_any_durable_call_timeout_becomes_a_resume_boundary():
    hooks = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    verifier = (ROOT / 'scripts/verify_release.py').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in hooks
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in hooks
    assert '_timeout_seconds_for_item' in hooks
    assert 'BREADMAKER_DURABLE_VERIFY' in hooks and 'os._exit(124)' in hooks
    assert 'report.when=="call"' in hooks
    assert '"BREADMAKER_TEST_TIMEOUT" in reason' in hooks
    assert 'env["BREADMAKER_DURABLE_VERIFY"] = "1"' in verifier
    assert 'missing = [name for name in names if name not in recorded_names]' in verifier
    assert 'detail="complete"' in verifier
    assert 'Durable accounting: {result.executed_items}/{result.job.expected_items} pytest items recorded.' in verifier
