"""M12S source-only regression contracts for encrypted recovery isolation.

The owner, not the authoring assistant, executes these via ./verify-release.
"""
from __future__ import annotations

import pytest

from test_v10524_private_live_restore import OWNER, DEST, private_store
from hosted.encrypted_recovery import HostedEncryptedRecoveryReader, HostedEncryptedRecoveryWriter
from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer, HostedEncryptedLiveRestoreError
from hosted.packages import HostedProjectPackageService
from hosted.recovery_coverage import RecoveryCoverageError, physical_project_gcs_fingerprint
from hosted.security.envelope import FORMAT
from hosted.storage.memory import MemoryObjectStore


def test_v10531_gcs_inventory_changes_during_private_source_export_are_rejected(monkeypatch):
    docs, live, projects, codec = private_store(OWNER)
    pid = projects.create('Confidential source')['id']
    baseline = physical_project_gcs_fingerprint(live, pid)
    assert baseline
    bucket = MemoryObjectStore()
    original = HostedProjectPackageService._render_package

    def concurrent_object_publication(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        live.write_chunks(
            f'projects/{pid}/private/v1/manuscript_block/b_shadow/op_raced.bme',
            (b'ciphertext-only-fixture',), max_bytes=1000,
            content_type='application/vnd.breadmaker.encrypted+json',
            if_generation_match=0,
            metadata={'breadmaker-private-format': FORMAT},
        )
        return result

    monkeypatch.setattr(HostedProjectPackageService, '_render_package', concurrent_object_publication)
    with pytest.raises(RecoveryCoverageError, match='GCS_CHANGED_DURING_EXPORT'):
        HostedEncryptedRecoveryWriter(projects, bucket, codec).create(
            pid, OWNER, operation='concurrent_gcs_change')
    assert not bucket.list_prefix(f'projects/{pid}/')
    assert physical_project_gcs_fingerprint(live, pid) != baseline


def test_v10531_duplicate_physical_gcs_inventory_is_not_trusted(monkeypatch):
    _docs, live, projects, _codec = private_store(OWNER)
    pid = projects.create('Hidden draft')['id']
    original = live.list_prefix
    monkeypatch.setattr(live, 'list_prefix', lambda prefix: original(prefix) + original(prefix))
    with pytest.raises(RecoveryCoverageError, match='GCS_INVENTORY_INVALID'):
        physical_project_gcs_fingerprint(live, pid)


def _archive_with_participant(payload):
    docs, live, source, codec = private_store(OWNER)
    pid = source.create('Restorable beta')['id']
    source.domain.create('review', {'project_id': pid}, {'name': 'Alpha'},
                         user_id=OWNER, entity_id='review_secure')
    source.domain.create('review_participant',
                         {'project_id': pid, 'review_id': 'review_secure'},
                         payload, user_id=OWNER, entity_id='participant_secure')
    bucket = MemoryObjectStore()
    HostedEncryptedRecoveryWriter(source, bucket, codec).create(
        pid, OWNER, operation='participant_source')
    reader = HostedEncryptedRecoveryReader(bucket, codec,
        authorize=lambda p, u, _sid: p == pid and u == OWNER)
    return pid, reader


def test_v10531_recovered_review_preserves_feedback_but_revokes_old_session_capabilities():
    pid, reader = _archive_with_participant({
        'display_name': 'An unpublished reviewer',
        'notes': 'Carefully annotated chapter four',
        'status': 'in_progress', 'started': True, 'bookmark': {'paragraph': 9},
        'invite_token': 'old-secret', 'session_token': 'old-session-token',
        'reviewer_session_id': 'old-session-id',
    })
    docs, _live, destination, _codec = private_store(DEST)
    new_id = HostedEncryptedLiveRestorer(reader, destination,
        authorize_destination=lambda uid: uid == DEST).restore(
            pid, OWNER, DEST, operation='participant_source',
            restore_operation='reviewer_capabilities_revoked').destination_project_id
    participant = destination.domain.get('review_participant', {
        'project_id': new_id, 'review_id': 'review_secure',
        'participant_id': 'participant_secure'}, user_id=DEST)
    assert participant['display_name'] == 'An unpublished reviewer'
    assert participant['notes'] == 'Carefully annotated chapter four'
    assert participant['bookmark'] == {'paragraph': 9}
    assert participant['status'] == 'revoked' and participant['started'] is False
    assert not any(key in participant for key in ('invite_token', 'session_token', 'reviewer_session_id'))
    raw = docs.get(('projects', new_id, 'reviews', 'review_secure', 'participants', 'participant_secure'))
    assert 'old-secret' not in repr(raw.data)


def test_v10531_unknown_reviewer_authority_schema_cannot_publish_destination():
    pid, reader = _archive_with_participant({
        'name': 'External reviewer', 'external_grant': 'legacy-live-access',
    })
    docs, _live, destination, _codec = private_store(DEST)
    with pytest.raises(HostedEncryptedLiveRestoreError, match='REVIEWER_SCHEMA_UNSUPPORTED'):
        HostedEncryptedLiveRestorer(reader, destination,
            authorize_destination=lambda uid: uid == DEST).restore(
                pid, OWNER, DEST, operation='participant_source',
                restore_operation='unknown_reviewer_capability')
    assert not destination.list(DEST)
    assert docs.get(('hosted_private_restore_operations', 'unknown_reviewer_capability')) is None
