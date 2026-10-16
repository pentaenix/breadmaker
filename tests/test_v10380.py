"""Durable browser cold-start boundary (source contract; operator runs real E2E)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v10380_durable_prestart_is_opt_in_and_bounded():
    conftest = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    durable = (ROOT / 'tests/test_e2e.py').read_text(encoding='utf-8')
    assert 'BREADMAKER_DURABLE_VERIFY") == "1"' in conftest
    assert 'BREADMAKER_DURABLE_VERIFY") == "1"' in durable
    assert '_DURABLE_BROWSER_STARTUP_SECONDS = 30.0' in conftest
    assert '_DURABLE_BROWSER_HARD_STOP_SECONDS = 40.0' in conftest
    assert 'pytest_sessionstart(session)' in conftest
    assert '_prepare_durable_browser(session.config)' in conftest
    assert '_close_durable_browser(request.config)' in durable
    assert 'BREADMAKER_DURABLE_BROWSER_PRESTART_MISSING' in durable


def test_v10380_original_per_item_budgets_and_direct_pytest_stay_intact():
    conftest = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    durable = (ROOT / 'tests/test_e2e.py').read_text(encoding='utf-8')
    verifier = (ROOT / 'scripts/verify_release.py').read_text(encoding='utf-8')
    assert 'UNIT_TEST_TIMEOUT_SECONDS = 3.0' in conftest
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in conftest
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in conftest
    assert 'item._breadmaker_execution_budget' in conftest
    assert 'pw=sync_playwright().start()' in durable  # direct pytest fallback
    assert 'env["BREADMAKER_DURABLE_VERIFY"] = "1"' in verifier
    assert 'run_stage("Durable E2E", durable_browser_jobs, 1)' in verifier
