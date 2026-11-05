"""Safety bounds, failure reporting and regression detection for local k6."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools.loadtest.run import clean_environment, parser, plan, stop
from tools.loadtest.report import OPERATIONS, read_experiment, regressions
from tools.loadtest.fixture import Observations, make_store
from tools.loadtest.emulator import check_installation, create_client, validate_target


def test_production_target_and_unbounded_load_are_not_accepted():
    for arguments in (['--base-url', 'https://app.breadmaker.studio'], ['--vus', '21'],
                      ['--seconds', '121'], ['--rate', '11'], ['--users', '101']):
        with pytest.raises(SystemExit):
            parser().parse_args(arguments)
    with pytest.raises(ValueError, match='distinct account'):
        plan(parser().parse_args(['--vus', '20', '--users', '2']))
    with pytest.raises(ValueError, match='20,000'):
        plan(parser().parse_args(['--users', '100', '--chapters', '8', '--blocks', '128']))
    with pytest.raises(ValueError, match='five distinct'):
        plan(parser().parse_args(['--vus', '1', '1']))


def test_child_environment_excludes_credentials_outputs_and_proxies(monkeypatch):
    for key in ('GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CLOUD_PROJECT', 'BREADMAKER_RUNTIME',
                'BREADMAKER_STORAGE_BUCKET', 'K6_OUT', 'K6_CLOUD_TOKEN', 'HTTPS_PROXY', 'HTTP_PROXY',
                'FIRESTORE_EMULATOR_HOST', 'DATASTORE_EMULATOR_HOST', 'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS'):
        monkeypatch.setenv(key, 'must-not-propagate')
    child = clean_environment()
    assert child['NO_PROXY'] == '127.0.0.1'
    assert not any(value == 'must-not-propagate' for value in child.values())


@pytest.mark.skipif(shutil.which('k6') is None, reason='Optional installed OSS k6 is required')
def test_k6_rejects_remote_manifest_before_sending_requests(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(fixture='local-memory-v1', base_url='https://app.breadmaker.studio')))
    result = subprocess.run(['k6', 'inspect', '--include-system-env-vars', str(Path(__file__).resolve().parents[1] / 'tools/loadtest/workload.js')],
        env={**clean_environment(), 'BREADMAKER_LOAD_MANIFEST': str(manifest)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert 'Only the disposable loopback fixture' in result.stderr


def experiment():
    return dict(experiment=dict(dataset=dict(users=20), profile='concurrency', seconds=30),
        runs=[dict(vus=5, passed=True, endpoints=dict(chapter_read=dict(p95_ms=100, p99_ms=150)))])


def test_regression_requires_both_material_relative_and_absolute_change():
    before = experiment()
    after = deepcopy(before)
    after['runs'][0]['endpoints']['chapter_read']['p95_ms'] = 130
    assert regressions(after, before)[0]['operation'] == 'chapter_read'
    after['runs'][0]['endpoints']['chapter_read']['p95_ms'] = 115
    assert regressions(after, before) == []
    before['runs'][0]['endpoints']['chapter_read']['p95_ms'] = 1
    after['runs'][0]['endpoints']['chapter_read']['p95_ms'] = 2
    assert regressions(after, before) == []
    after['experiment']['dataset']['users'] = 100
    with pytest.raises(ValueError, match='dataset'):
        regressions(after, before)


def test_report_preserves_failed_run_without_summary(tmp_path):
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(vus=[1])))
    run = tmp_path / 'vus-1'
    run.mkdir()
    (run / 'status.json').write_text(json.dumps(dict(error='controlled startup failure')))
    report = read_experiment(tmp_path)
    assert report['runs'][0]['passed'] is False
    assert report['runs'][0]['error'] == 'controlled startup failure'


def test_report_does_not_accept_empty_k6_metrics_as_a_pass(tmp_path):
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(vus=[1])))
    run = tmp_path / 'vus-1'
    run.mkdir()
    (run / 'status.json').write_text(json.dumps(dict(k6_exit_code=0, error=None, temporary_data_removed=True)))
    (run / 'k6-summary.json').write_text(json.dumps(dict(metrics={})))
    (run / 'server-observations.json').write_text(json.dumps(dict(adapter='MemoryDocumentStore')))
    assert read_experiment(tmp_path)['runs'][0]['passed'] is False


def test_stop_reaps_owned_process():
    import sys
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    stop(child)
    assert child.poll() is not None


def test_timed_store_keeps_normal_rollback_after_disposable_seed():
    store = make_store(Observations())
    store.run_transaction(lambda tx: tx.create(('synthetic', 'one'), {'value': 'seed'}))
    store.seeding = False
    def failing_write(tx):
        tx.set(('synthetic', 'one'), {'value': 'must roll back'})
        raise ValueError('controlled failure')
    with pytest.raises(ValueError, match='controlled failure'):
        store.run_transaction(failing_write)
    assert store.get(('synthetic', 'one')).data == {'value': 'seed'}


@pytest.mark.parametrize('endpoint,project', [
    ('firestore.googleapis.com:443', 'demo-bm-k6-' + 'a' * 16),
    ('127.0.0.1:8080', 'production-project'),
    ('http://127.0.0.1:8080', 'demo-bm-k6-' + 'a' * 16),
    ('127.0.0.1:0', 'demo-bm-k6-' + 'a' * 16),
    ('127.0.0.1:65536', 'demo-bm-k6-' + 'a' * 16),
    ('localhost:8080', 'demo-bm-k6-' + 'a' * 16),
])
def test_emulator_rejects_unowned_or_cloud_targets(endpoint, project):
    with pytest.raises(ValueError):
        validate_target(endpoint, project)


def test_emulator_client_uses_anonymous_credentials_and_loopback(monkeypatch):
    import google.auth
    from google.auth.credentials import AnonymousCredentials
    monkeypatch.setattr(google.auth, 'default', lambda *a, **kw: pytest.fail('ADC must never be consulted'))
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST', '127.0.0.1:54321')
    client = create_client('127.0.0.1:54321', 'demo-bm-k6-' + 'a' * 16)
    assert isinstance(client._credentials, AnonymousCredentials)
    assert client._emulator_host == client._target == '127.0.0.1:54321'
    with pytest.raises(ValueError, match='must agree'):
        create_client('127.0.0.1:54322', 'demo-bm-k6-' + 'a' * 16)


def test_unverified_jar_is_not_executed(tmp_path, monkeypatch):
    jar = tmp_path / 'unverified.jar'
    jar.write_bytes(b'Unverified executable')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('Unverified JAR must not execute'))
    with pytest.raises(ValueError, match='SHA-256'):
        check_installation(jar, 'java')


def test_emulator_plan_requires_explicit_jar_and_tags_adapter():
    with pytest.raises(ValueError, match='emulator-jar'):
        plan(parser().parse_args(['--adapter', 'firestore-emulator']))
    value = plan(parser().parse_args(['--adapter', 'firestore-emulator', '--emulator-jar', '/explicit/emulator.jar']))
    assert value['adapter'] == 'firestore-emulator'
    assert value['account_lifecycle_fence'] is True


def test_regression_comparison_rejects_different_adapters_and_fences():
    before, after = experiment(), experiment()
    after['experiment']['adapter'] = 'firestore-emulator'
    with pytest.raises(ValueError, match='adapter'):
        regressions(after, before)
    after['experiment']['adapter'] = 'memory'
    after['experiment']['account_lifecycle_fence'] = True
    with pytest.raises(ValueError, match='account_lifecycle_fence'):
        regressions(after, before)
    after['experiment']['account_lifecycle_fence'] = False
    after['experiment']['workload'] = 'read-only'
    with pytest.raises(ValueError, match='workload'):
        regressions(after, before)


def test_emulator_report_requires_positive_data_and_process_cleanup(tmp_path):
    (tmp_path / 'experiment.json').write_text(json.dumps(dict(vus=[1], adapter='firestore-emulator')))
    root = tmp_path / 'vus-1'
    root.mkdir()
    status = dict(k6_exit_code=0, error=None, temporary_data_removed=True)
    (root / 'status.json').write_text(json.dumps(status))
    metrics = {'checks': {'values': {'rate': 1}, 'thresholds': {'rate==1': {'ok': True}}}}
    for op in OPERATIONS:
        metrics[f'http_req_duration{{op:{op}}}'] = {'values': {'p(95)': 10, 'p(99)': 20}}
        metrics[f'http_reqs{{op:{op}}}'] = {'values': {'count': 1}}
    (root / 'k6-summary.json').write_text(json.dumps(dict(metrics=metrics)))
    (root / 'server-observations.json').write_text(json.dumps(dict(adapter='GoogleFirestoreDocumentStore/emulator')))
    assert read_experiment(tmp_path)['runs'][0]['passed'] is False
    status['emulator_cleanup'] = dict(data_cleared=True, empty_verified=True, endpoint_closed=True, error=None)
    (root / 'status.json').write_text(json.dumps(status))
    assert read_experiment(tmp_path)['runs'][0]['passed'] is True
