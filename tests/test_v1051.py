"""M10B: read-only recovery plan worker; assistant does not run these tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hosted.backup_job_cli import _required_environment, build_plan, main, RecoveryPlanJobConfigurationError
from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.memory import MemoryDocumentStore

UID = 'usr_' + 'a' * 32
PID = 'b' * 32
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
ENV = {
    'BREADMAKER_ENV': 'staging',
    'BREADMAKER_OPERATOR_MODE': 'recovery_plan_read_only',
    'BREADMAKER_GCP_PROJECT_ID': 'breadmaker-stage-123',
    'BREADMAKER_FIRESTORE_DATABASE': '(default)',
}


def _world():
    documents = MemoryDocumentStore()
    def seed(tx):
        tx.create(('projects', PID), {'generation': 4, 'status': 'active'})
        tx.create(('projects', PID, 'members', UID), {'role': 'owner'})
        tx.create(('recovery_schedules', PID), {
            'project_id': PID, 'owner_user_id': UID, 'enabled': True,
            'cadence_hours': 24, 'retention_days': 30, 'next_due_epoch': 1,
        })
    documents.run_transaction(seed)
    return documents


def test_v1051_plan_is_pure_and_redacts_user_identity():
    docs = _world()
    before = dict(docs._documents)
    report = build_plan(docs, now=NOW)
    assert report['due_count'] == 1
    assert report['execution_authorized'] is False
    assert UID not in json.dumps(report) and PID not in json.dumps(report)
    assert before == docs._documents


def test_v1051_cli_rejects_non_staging_and_write_modes_before_provider():
    for mode in ('production', 'local', ''):
        env = {**ENV, 'BREADMAKER_ENV': mode}
        with pytest.raises(RecoveryPlanJobConfigurationError, match='STAGING_READ_ONLY'):
            _required_environment(env)
    env = {**ENV, 'BREADMAKER_OPERATOR_MODE': 'recovery_write'}
    with pytest.raises(RecoveryPlanJobConfigurationError, match='STAGING_READ_ONLY'):
        _required_environment(env)


def test_v1051_cli_rejects_missing_project_and_nondefault_database():
    for env in ({**ENV, 'BREADMAKER_GCP_PROJECT_ID': ''},
                {**ENV, 'BREADMAKER_FIRESTORE_DATABASE': 'test-db'}):
        with pytest.raises(RecoveryPlanJobConfigurationError):
            _required_environment(env)


def test_v1051_cli_reports_only_readonly_counts_and_no_credential_dependency(capsys):
    docs = _world()
    calls = []
    def factory(*, project, database):
        calls.append((project, database))
        return docs
    code = main(['plan'], env=ENV, store_factory=factory, now=NOW)
    assert code == 0 and calls == [('breadmaker-stage-123', '(default)')]
    result = json.loads(capsys.readouterr().out)
    assert result['result'] == 'READ_ONLY'
    assert result['due_count'] == 1 and result['execution_authorized'] is False
    assert PID not in json.dumps(result) and UID not in json.dumps(result)


def test_v1051_cli_fails_closed_if_owner_record_changes(capsys):
    docs = _world()
    docs.run_transaction(lambda tx: tx.set(('projects', PID, 'members', UID), {'role': 'viewer'}))
    code = main(['plan'], env=ENV, store_factory=lambda **kwargs: docs, now=NOW)
    assert code == 2
    err = capsys.readouterr()
    assert not err.out and 'OWNER_OR_PROJECT_CHANGED' in err.err


def test_v1051_cli_does_not_import_client_before_env_gate(capsys):
    assert main(['plan'], env={}) == 2
    assert 'STAGING_READ_ONLY' in capsys.readouterr().err


def test_v1051_operator_dockerfile_is_nonroot_no_server_and_allowlisted():
    dockerfile = (Path(__file__).resolve().parents[1] / 'deploy/Dockerfile.hosted-recovery-plan').read_text()
    assert 'USER 10001:10001' in dockerfile
    assert 'COPY src ./src' in dockerfile
    assert 'COPY . ' not in dockerfile
    assert 'hosted.backup_job_cli' in dockerfile
    assert 'uvicorn' not in dockerfile
