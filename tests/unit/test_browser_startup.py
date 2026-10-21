"""Bounded startup/cleanup and early failure paths; no real browser or app."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from scripts import browser_runtime as browser
from scripts import verify_release as verifier


def test_startup_pins_managed_binary_and_uses_native_timeout(monkeypatch):
    monkeypatch.delenv('BREADMAKER_TEST_BROWSER', raising=False)
    calls = []
    instance = object()
    def launch(**kwargs):
        calls.append(kwargs)
        return instance
    driver = SimpleNamespace(chromium=SimpleNamespace(executable_path=sys.executable, launch=launch))
    factory = lambda: SimpleNamespace(start=lambda: driver)
    assert browser.start_browser(driver_factory=factory) == (driver, instance, sys.executable)
    assert calls == [{'executable_path': sys.executable, 'headless': True,
                      'args': ['--no-sandbox'], 'timeout': 30000.0}]


def test_launch_failure_stops_driver_and_remains_failure(monkeypatch):
    monkeypatch.delenv('BREADMAKER_TEST_BROWSER', raising=False)
    calls = []
    def launch(**_kwargs):
        raise RuntimeError('launch failed')
    driver = SimpleNamespace(chromium=SimpleNamespace(executable_path=sys.executable, launch=launch),
                             stop=lambda: calls.append('stop'))
    with pytest.raises(RuntimeError, match='launch failed'):
        browser.start_browser(driver_factory=lambda: SimpleNamespace(start=lambda: driver))
    assert calls == ['stop']


def test_close_failure_still_stops_driver():
    calls = []
    def close():
        raise RuntimeError('close failed')
    with pytest.raises(RuntimeError, match='close failed'):
        browser.close_browser(SimpleNamespace(stop=lambda: calls.append('stop')),
                              SimpleNamespace(close=close))
    assert calls == ['stop']


@pytest.mark.parametrize('hang', ['driver', 'failure-cleanup', 'close'])
def test_hanging_lifecycle_is_killed_with_named_phase(hang):
    code = '''
import sys, time, os
from types import SimpleNamespace as S
from scripts.browser_runtime import start_browser, close_browser
os.environ.pop('BREADMAKER_TEST_BROWSER', None)
def stall(): time.sleep(10)
def failed_launch(**kwargs): raise RuntimeError('launch failure')
mode = sys.argv[1]
if mode == 'close':
    close_browser(S(stop=lambda: None), S(close=stall), hard_seconds=.15)
else:
    driver = S(chromium=S(executable_path=sys.executable, launch=failed_launch), stop=stall)
    start = stall if mode == 'driver' else lambda: driver
    start_browser(driver_factory=lambda: S(start=start), hard_seconds=.15)
'''
    result = subprocess.run([sys.executable, '-c', code, hang], cwd=verifier.ROOT,
                            capture_output=True, text=True, timeout=2)
    assert result.returncode == 124
    expected = {'driver': 'driver-start', 'failure-cleanup': 'failed-start-driver-stop',
                'close': 'chromium-close'}[hang]
    assert f'BREADMAKER_BROWSER_HARD_TIMEOUT: {expected}' in result.stderr


def test_preflight_rejects_nonzero_probe_even_with_executable_marker(monkeypatch):
    class Process:
        returncode = 124
        def communicate(self, timeout):
            assert timeout == 45
            return f'BREADMAKER_BROWSER_EXECUTABLE::{sys.executable}\n', None
    process = Process()
    cleaned = []
    monkeypatch.setattr(verifier.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(verifier, '_cleanup_abandoned_process_group', lambda p: cleaned.append(p))
    with pytest.raises(AssertionError, match='child exit 124'):
        verifier._browser_runtime_check()
    assert cleaned == [process]


def test_preflight_parent_timeout_kills_and_cleans_child(monkeypatch):
    class Process:
        def communicate(self, timeout):
            raise subprocess.TimeoutExpired('probe', timeout)
    process = Process()
    calls = []
    monkeypatch.setattr(verifier.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(verifier, '_kill_process_tree', lambda p: calls.append(('kill', p)))
    monkeypatch.setattr(verifier, '_cleanup_abandoned_process_group', lambda p: calls.append(('cleanup', p)))
    with pytest.raises(AssertionError, match='BROWSER_PREFLIGHT_TIMEOUT'):
        verifier._browser_runtime_check()
    assert calls == [('kill', process), ('cleanup', process)]


def test_preflight_pins_successful_probe_for_later_jobs(monkeypatch):
    class Process:
        returncode = 0
        def communicate(self, timeout):
            return f'BREADMAKER_BROWSER_EXECUTABLE::{sys.executable}\n', None
    monkeypatch.delenv('BREADMAKER_TEST_BROWSER', raising=False)
    monkeypatch.setattr(verifier.subprocess, 'Popen', lambda *a, **kw: Process())
    monkeypatch.setattr(verifier, '_cleanup_abandoned_process_group', lambda p: None)
    verifier._browser_runtime_check()
    import os
    assert os.environ['BREADMAKER_TEST_BROWSER'] == sys.executable


@pytest.mark.parametrize('failed_stage', ['Unit', 'Durable E2E'])
def test_failed_early_stage_never_launches_core_or_marks_success(monkeypatch, tmp_path, failed_stage):
    monkeypatch.setattr(verifier, '_parse_args', lambda: SimpleNamespace(
        quick=False, list=False, core_workers=1, browser_workers=1))
    monkeypatch.setattr(verifier, 'discover_unit_jobs', lambda: [])
    monkeypatch.setattr(verifier, 'discover_core_jobs', lambda **kw: [])
    monkeypatch.setattr(verifier, 'discover_browser_jobs', lambda **kw: [])
    monkeypatch.setattr(verifier, 'run_preflight', lambda: None)
    monkeypatch.setattr(verifier, 'collect_exact_unit_job', lambda: SimpleNamespace(expected_items=1))
    stages, reports = [], []
    def stage(name, jobs, workers):
        stages.append(name)
        return [SimpleNamespace(ok=name != failed_stage)]
    monkeypatch.setattr(verifier, 'run_stage', stage)
    monkeypatch.setattr(verifier, 'collect_exact_core_jobs', lambda **kw: pytest.fail('core must not start'))
    monkeypatch.setattr(verifier, '_print_stage_accounting', lambda results: None)
    monkeypatch.setattr(verifier, '_print_failure_digest', lambda results: None)
    monkeypatch.setattr(verifier, '_write_report', lambda *a, **kw: reports.append((a, kw)))
    assert verifier.main() == 1
    assert stages == (['Unit'] if failed_stage == 'Unit' else ['Unit', 'Durable E2E'])
    assert reports


@pytest.mark.parametrize('parent_timeout', [False, True])
def test_durable_startup_timeout_is_named_and_missing_tests_stay_unverified(monkeypatch, tmp_path, parent_timeout):
    import time
    class Process:
        returncode = 124
        def poll(self):
            return self.returncode
        def communicate(self, timeout):
            return 'BREADMAKER_BROWSER_HARD_TIMEOUT: driver-start\n', None
    monkeypatch.setattr(verifier, '_test_functions', lambda path: ['test_a', 'test_b'])
    monkeypatch.setattr(verifier.subprocess, 'Popen', lambda *a, **kw: Process())
    monkeypatch.setattr(verifier, '_cleanup_abandoned_process_group', lambda proc: None)
    killed = []
    if parent_timeout:
        monkeypatch.setattr(verifier, '_await_durable_startup', lambda *a: False)
        monkeypatch.setattr(verifier, '_kill_process_tree', lambda proc: killed.append(proc))
    result = verifier._run_durable_job(
        verifier.Job('durable', 'durable E2E', 'test_e2e.py', 60, expected_items=2),
        time.monotonic(), tmp_path / 'junit.xml', tmp_path / 'results.jsonl')
    assert not result.ok and result.timed_out and result.returncode == 124
    assert result.executed_items == result.passed_items == result.skipped_items == 0
    assert any(phase == 'startup' and 'driver-start' in message for _, message, phase in result.child_failures)
    assert any(phase == 'accounting' and 'recorded 0' in message for _, message, phase in result.child_failures)
    assert 'durable attempt 2' not in result.output  # Never blindly retry startup.
    assert bool(killed) is parent_timeout


def test_durable_parent_deadline_cannot_wait_for_wedged_child(tmp_path):
    process = SimpleNamespace(poll=lambda: None)
    assert not verifier._await_durable_startup(process, tmp_path / 'not-ready', timeout=0)
    ready = tmp_path / 'ready'
    ready.write_text('ready')
    assert verifier._await_durable_startup(process, ready, timeout=0)


def test_successful_gate_keeps_every_stage_and_runs_durable_only_once(monkeypatch):
    args = SimpleNamespace(quick=False, list=False, core_workers=2, browser_workers=1, verbose_failures=False)
    monkeypatch.setattr(verifier, '_parse_args', lambda: args)
    def job(name, category):
        return verifier.Job(name, category, str(verifier.TESTS / name), 30)
    unit = job('unit.py', 'unit')
    core = [job('test_scale.py', 'core/API'), job('serial.py', 'core/API'), job('ordinary.py', 'core/API')]
    durable, current = job('test_e2e.py', 'durable E2E'), job('browser.py', 'current browser')
    monkeypatch.setattr(verifier, 'discover_unit_jobs', lambda: [unit])
    monkeypatch.setattr(verifier, 'discover_core_jobs', lambda **kw: core)
    monkeypatch.setattr(verifier, 'discover_browser_jobs', lambda **kw: [durable, current])
    monkeypatch.setattr(verifier, 'run_preflight', lambda: None)
    monkeypatch.setattr(verifier, 'collect_exact_unit_job', lambda: unit)
    monkeypatch.setattr(verifier, 'collect_exact_core_jobs', lambda **kw: core)
    monkeypatch.setattr(verifier, 'plan_core_execution_jobs', lambda jobs: jobs)
    monkeypatch.setattr(verifier, 'NO_CONTENTION_CORE_MODULES', {'serial.py'})
    calls, reports = [], []
    def stage(name, jobs, workers):
        calls.append((name, [j.name for j in jobs]))
        return [verifier.Result(j, 0, '', 0, executed_items=1, passed_items=1) for j in jobs]
    monkeypatch.setattr(verifier, 'run_stage', stage)
    monkeypatch.setattr(verifier, 'retry_failed_current_browser', lambda *a: None)
    monkeypatch.setattr(verifier, '_write_report', lambda *a, **kw: reports.append(a))
    assert verifier.main() == 0
    assert [name for name, jobs in calls] == [
        'Unit', 'Durable E2E', 'Core / API scale', 'Core / API serial', 'Core / API', 'Current browser']
    assert [name for _, jobs in calls for name in jobs] == [
        unit.name, durable.name, *(j.name for j in core), current.name]
    assert len(reports) == 1 and reports[0][3] is True
