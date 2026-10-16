"""M10D isolated restore contracts. Human release runner owns execution."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.recovery_drill_cli import (
    DrillSelection, RecoveryDrillConfigurationError, required_environment,
    verify_isolated_restore, main,
)
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.contracts import ObjectIntegrityError
from hosted.storage.memory import MemoryObjectStore
from hosted.firestore.memory import MemoryDocumentStore
from runtime.hosted import build_hosted_dev_runtime

PROJECT = 'breadmaker-drill-staging-1'
ENV = {
    'BREADMAKER_ENV': 'staging',
    'BREADMAKER_OPERATOR_MODE': 'isolated_recovery_drill',
    'BREADMAKER_GCP_PROJECT_ID': PROJECT,
    'BREADMAKER_FIRESTORE_DATABASE': '(default)',
    'BREADMAKER_RECOVERY_BUCKET': f'{PROJECT}-breadmaker-recovery',
}


def world(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, live, archive_objects = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=live)
    pid = runtime.projects.create('Disaster-test manuscript')['id']
    archive = HostedRecoveryArchiveService(runtime.projects, archive_objects).create(
        pid, _DEV_USER_ID, operation='stage_recovery_check')
    selection = DrillSelection(pid, _DEV_USER_ID, 'stage_recovery_check',
                               archive.sha256, archive.object_generation)
    return docs, live, archive_objects, selection


def request(selection):
    return json.dumps({
        'project_id': selection.project_id,
        'owner_user_id': selection.owner_user_id,
        'operation': selection.operation,
        'expected_sha256': selection.expected_sha256,
        'expected_generation': selection.expected_generation,
    })


def test_v1053_isolated_restoration_verifies_bytes_semantics_and_generation(monkeypatch):
    docs, live, archive_objects, selection = world(monkeypatch)
    result = verify_isolated_restore(archive_objects, selection)
    assert result['result'] == 'ISOLATED_RESTORE_VERIFIED'
    assert result['archive_sha256'] == selection.expected_sha256
    assert result['object_generation'] == selection.expected_generation
    assert result['source_firestore_required'] is False
    assert result['production_deletion_authorized'] is False
    assert 0 <= result['restored_entity_groups'] <= 100


def test_v1053_live_source_stores_cannot_be_read_or_mutated_by_drill(monkeypatch):
    docs, live, archive_objects, selection = world(monkeypatch)
    original_documents = dict(docs._documents)
    original_live_objects = list(live.list_prefix(''))
    verify_isolated_restore(archive_objects, selection)
    assert docs._documents == original_documents
    assert live.list_prefix('') == original_live_objects


def test_v1053_archive_drill_survives_source_project_disappearance(monkeypatch):
    docs, live, archive_objects, selection = world(monkeypatch)
    docs._documents.clear()
    live._rows.clear()
    assert verify_isolated_restore(archive_objects, selection)['result'] == 'ISOLATED_RESTORE_VERIFIED'


def test_v1053_fails_if_operator_sha256_not_exact(monkeypatch):
    _, _, archive_objects, selection = world(monkeypatch)
    changed = DrillSelection(selection.project_id, selection.owner_user_id,
                             selection.operation, 'a' * 64, selection.expected_generation)
    with pytest.raises(ObjectIntegrityError, match='PINNED_SHA256_MISMATCH'):
        verify_isolated_restore(archive_objects, changed)


def test_v1053_fails_if_object_generation_changes(monkeypatch):
    _, _, archive_objects, selection = world(monkeypatch)
    changed = DrillSelection(selection.project_id, selection.owner_user_id,
                             selection.operation, selection.expected_sha256,
                             selection.expected_generation + 1)
    with pytest.raises(ObjectIntegrityError, match='PINNED_GENERATION_MISMATCH'):
        verify_isolated_restore(archive_objects, changed)


def test_v1053_cross_owner_or_operation_cannot_resolve_archive(monkeypatch):
    _, _, archive_objects, selection = world(monkeypatch)
    for changed in (
        DrillSelection(selection.project_id, 'usr_' + 'c'*32, selection.operation,
                       selection.expected_sha256, selection.expected_generation),
        DrillSelection(selection.project_id, selection.owner_user_id, 'other_operation',
                       selection.expected_sha256, selection.expected_generation),
    ):
        with pytest.raises(ObjectIntegrityError, match='PINNED_GENERATION_MISMATCH'):
            verify_isolated_restore(archive_objects, changed)


def test_v1053_staging_config_requires_operator_pinned_identity(monkeypatch):
    _, _, _, selection = world(monkeypatch)
    project, bucket, parsed = required_environment({**ENV, 'BREADMAKER_DRILL_REQUEST_JSON': request(selection)})
    assert project == PROJECT and bucket == f'{PROJECT}-breadmaker-recovery'
    assert parsed == selection


def test_v1053_rejects_bad_environment_before_constructing_any_sdk(monkeypatch):
    _, _, _, selection = world(monkeypatch)
    safe = {**ENV, 'BREADMAKER_DRILL_REQUEST_JSON': request(selection)}
    for change in (
        {'BREADMAKER_ENV': 'production'},
        {'BREADMAKER_OPERATOR_MODE': 'recovery_archive_writer'},
        {'BREADMAKER_FIRESTORE_DATABASE': 'other'},
        {'BREADMAKER_GCP_PROJECT_ID': 'other-prod'},
        {'BREADMAKER_RECOVERY_BUCKET': 'other-bucket'},
        {'BREADMAKER_DRILL_REQUEST_JSON': ''},
        {'BREADMAKER_DRILL_REQUEST_JSON': '{}'},
    ):
        with pytest.raises((RecoveryDrillConfigurationError, ValueError)):
            required_environment({**safe, **change})


def test_v1053_rejects_bool_generation_invalid_sha_and_extraneous_fields(monkeypatch):
    _, _, _, selection = world(monkeypatch)
    safe = {**ENV, 'BREADMAKER_DRILL_REQUEST_JSON': request(selection)}
    payload = json.loads(request(selection))
    for key, value in [('expected_generation', True), ('expected_generation', 0),
                       ('expected_sha256', 'ABC'), ('operation', '../other')]:
        changed = {**payload, key: value}
        with pytest.raises(ValueError):
            required_environment({**safe, 'BREADMAKER_DRILL_REQUEST_JSON': json.dumps(changed)})
    with pytest.raises(ValueError):
        required_environment({**safe, 'BREADMAKER_DRILL_REQUEST_JSON': json.dumps({**payload, 'admin': True})})


def test_v1053_cli_redacts_user_input_and_returns_bounded_summary(monkeypatch, capsys):
    _, _, archive_objects, selection = world(monkeypatch)
    environment = {**ENV, 'BREADMAKER_DRILL_REQUEST_JSON': request(selection)}
    def runner(**kw):
        return verify_isolated_restore(archive_objects, kw['selection'])
    assert main(['drill'], env=environment, run=runner) == 0
    text = capsys.readouterr().out
    assert 'ISOLATED_RESTORE_VERIFIED' in text
    assert selection.project_id not in text and selection.owner_user_id not in text
    assert main(['drill'], env={**environment, 'BREADMAKER_ENV': 'production'}, run=runner) == 2
    err = capsys.readouterr().err
    assert selection.project_id not in err and selection.owner_user_id not in err
