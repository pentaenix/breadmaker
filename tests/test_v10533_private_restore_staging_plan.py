"""M12U operator-only, NON-DESTRUCTIVE failed-restore staging evidence.

Human-owned ./verify-release only. These tests are not run by the assistant.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hosted.firestore.memory import MemoryDocumentStore
from hosted.private_restore_staging import (HostedPrivateRestoreStagingPlanner,
                                           HostedPrivateStagingError)
from hosted.security.envelope import FORMAT
from hosted.storage.memory import MemoryObjectStore

OWNER = 'usr_' + 'b' * 32
SOURCE = 'usr_' + 'a' * 32
PROJECT = 'c' * 32
ORIGINAL = 'd' * 32
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
OP = 'restore_candidate_one'


def staged(*, days=31, has_timestamp=True):
    documents, objects = MemoryDocumentStore(), MemoryObjectStore()
    receipt = {
        'destination_project_id': PROJECT, 'destination_owner_user_id': OWNER,
        'source_project_id': ORIGINAL, 'source_owner_user_id': SOURCE,
        'source_snapshot_id': 'snapshot_test',
        'source_manifest_ciphertext_sha256': 'a' * 64,
        'restored_entity_count': 1, 'status': 'staging',
    }
    if has_timestamp:
        receipt['staging_started_at'] = (NOW - timedelta(days=days)).isoformat()
    documents.run_transaction(lambda tx: tx.create(
        ('hosted_private_restore_operations', OP), receipt))
    # Real staging has private Firestore docs and ciphertext GCS objects but no
    # project root nor owner membership (published last).
    documents.run_transaction(lambda tx: tx.create(
        ('projects', PROJECT, 'tasks', 'task_staged'),
        {'id': 'task_staged', 'generation': 1, '_bm_private_fields': {'sealed': 'test'}}))
    objects.write_chunks(
        f'projects/{PROJECT}/private/v1/manuscript_block/b_staged/restore_01.bme',
        (b'opaque ciphertext',), max_bytes=1024, if_generation_match=0,
        content_type='application/vnd.breadmaker.encrypted+json',
        metadata={'breadmaker-private-format': FORMAT},
    )
    return documents, objects


def planner(documents, objects, authorize=None):
    return HostedPrivateRestoreStagingPlanner(
        documents, objects, authorize=authorize or (lambda u, op: u == OWNER and op == OP))


def test_v10533_staging_plan_is_read_only_and_eligible_only_after_retention():
    docs, objects = staged()
    before = docs.get(('hosted_private_restore_operations', OP))
    obj_before = objects.list_prefix(f'projects/{PROJECT}/')
    result = planner(docs, objects).plan(OWNER, OP, now=NOW)
    assert result.destination_project_id == PROJECT
    assert (result.document_count, result.object_count, result.ciphertext_bytes) == (1, 1, 17)
    assert result.retention_eligible and result.manual_review_required
    assert result.deletion_enabled is False
    assert docs.get(('hosted_private_restore_operations', OP)) == before
    assert objects.list_prefix(f'projects/{PROJECT}/') == obj_before


def test_v10533_young_staging_and_legacy_unclocked_receipts_cannot_be_gc_eligible():
    for days, timestamp in [(5, True), (31, False)]:
        docs, objects = staged(days=days, has_timestamp=timestamp)
        assert not planner(docs, objects).plan(OWNER, OP, now=NOW).retention_eligible


def test_v10533_unauthorized_caller_denied_before_metadata_access():
    class ForbiddenDocuments:
        def get(self, *args):
            raise AssertionError('storage read happened before authorization')
    class ForbiddenObjects:
        def list_prefix(self, *args):
            raise AssertionError('bucket read happened before authorization')
    plan = HostedPrivateRestoreStagingPlanner(
        ForbiddenDocuments(), ForbiddenObjects(), authorize=lambda _u, _op: None)
    with pytest.raises(PermissionError, match='OPERATOR_DENIED'):
        plan.plan(OWNER, OP, now=NOW)


def test_v10533_never_consider_visible_project_for_staging_cleanup():
    docs, objects = staged()
    docs.run_transaction(lambda tx: tx.create(('projects', PROJECT), {'title': 'published'}))
    with pytest.raises(HostedPrivateStagingError, match='PROJECT_PUBLISHED'):
        planner(docs, objects).plan(OWNER, OP, now=NOW)


def test_v10533_unknown_firestore_or_plaintext_object_fails_closed():
    docs, objects = staged()
    docs.run_transaction(lambda tx: tx.create(
        ('projects', PROJECT, 'unknown_family', 'row'), {'sensitive': 'secret'}))
    with pytest.raises(HostedPrivateStagingError, match='UNKNOWN_DOCUMENT'):
        planner(docs, objects).plan(OWNER, OP, now=NOW)
    docs, objects = staged()
    objects.write_chunks(f'projects/{PROJECT}/legacy/plaintext.txt', [b'secret'],
                         max_bytes=1024, if_generation_match=0, content_type='text/plain')
    with pytest.raises(HostedPrivateStagingError, match='OBJECT_INVALID'):
        planner(docs, objects).plan(OWNER, OP, now=NOW)


def test_v10533_receipt_or_project_change_during_scan_fails_closed():
    docs, objects = staged()
    original = docs.project_document_inventory
    def concurrent_publish(*args, **kwargs):
        result = original(*args, **kwargs)
        docs.run_transaction(lambda tx: tx.create(('projects', PROJECT), {'title': 'published'}))
        return result
    docs.project_document_inventory = concurrent_publish
    with pytest.raises(HostedPrivateStagingError, match='CHANGED_DURING_SCAN'):
        planner(docs, objects).plan(OWNER, OP, now=NOW)


def test_v10533_bad_retention_or_unsupported_live_inventory_is_not_a_pass():
    docs, objects = staged()
    with pytest.raises(HostedPrivateStagingError, match='RETENTION_INVALID'):
        planner(docs, objects).plan(OWNER, OP, now=NOW, retention_days=0)
    docs.project_document_inventory = lambda *a, **kw: (_ for _ in ()).throw(
        ValueError('provider cannot enumerate absent roots'))
    with pytest.raises(HostedPrivateStagingError, match='INVENTORY_UNAVAILABLE'):
        planner(docs, objects).plan(OWNER, OP, now=NOW)
