from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v775_per_test_timeout_is_self_contained_and_uses_ten_second_core_and_fifteen_second_browser_budgets():
    conftest=(ROOT/'tests/conftest.py').read_text(encoding='utf-8')
    requirements=(ROOT/'requirements-test.txt').read_text(encoding='utf-8')
    assert 'CORE_API_TEST_TIMEOUT_SECONDS = 10.0' in conftest
    assert 'BROWSER_TEST_TIMEOUT_SECONDS = 15.0' in conftest
    assert '_timeout_seconds_for_item' in conftest
    for hook in ('pytest_runtest_setup','pytest_runtest_call','pytest_runtest_teardown'):
        assert hook in conftest
    assert '_breadmaker_execution_budget' in conftest
    assert 'BREADMAKER_TEST_TIMEOUT' in conftest
    assert 'pytest-timeout' not in requirements


def test_v775_timeout_is_per_item_not_per_suite():
    conftest=(ROOT/'tests/conftest.py').read_text(encoding='utf-8')
    # Each collected item receives its own policy-selected execution budget. The
    # phase wrappers spend that shared budget only while setup/call/teardown code runs.
    assert 'item._breadmaker_execution_budget=item._breadmaker_timeout_seconds' in conftest
    assert 'remaining=_item_budget_remaining(item)' in conftest
    assert 'threading.Event()' in conftest
    assert 'threading.Thread(' in conftest
    assert 'session_timeout' not in conftest


def test_v775_uninterruptible_timeout_has_hard_process_fallback():
    conftest=(ROOT/'tests/conftest.py').read_text(encoding='utf-8')
    import re
    grace=re.search(r'_TIMEOUT_KILL_GRACE_SECONDS\s*=\s*([0-9.]+)',conftest)
    assert grace and float(grace.group(1)) >= 4.0
    assert '_cleanup_yield' in conftest
    assert 'faulthandler.dump_traceback' in conftest
    assert 'os._exit(124)' in conftest
    assert 'os.kill(os.getpid(),signal.SIGALRM)' in conftest


def test_v775_timeout_contract_is_documented_for_release_runs():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    assert 'Core/API tests remain at 10 seconds' in readme
    assert 'browser and durable E2E tests use 15 seconds' in readme
    assert 'budget resets for each test' in readme
    assert 'hard setup + call + teardown budget' in readme
