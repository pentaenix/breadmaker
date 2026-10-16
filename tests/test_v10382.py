"""M9 operator-facing production preflight and recovery planning contracts.

Source-only tests. The assistant must NOT invoke pytest or the full verifier.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import identity_binding_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.operator_identity_review import HostedIdentityClosureInspector
from hosted.operator_project_preflight import HostedGooglePurgePreflight
from hosted.operator_recovery_drills import HostedRecoveryDrillCoordinator, OfflineDrillRequest
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner
from hosted.purge_production_gate import HostedProductionPurgeGate, REQUIRED_PROOFS

UID = 'usr_' + 'a' * 32
PID = 'b' * 32
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _records(*, count=1, due=True, enabled=True):
    docs = MemoryDocumentStore()
    for i in range(count):
        pid = f'{i + 1:032x}'
        def put(tx, pid=pid):
            tx.create(('projects', pid), {'generation': 7, 'status': 'active'})
            tx.create(('projects', pid, 'members', UID), {'role': 'owner'})
            tx.create(('recovery_schedules', pid), {
                'project_id': pid, 'owner_user_id': UID,
                'enabled': enabled, 'cadence_hours': 24, 'retention_days': 30,
                'next_due_epoch': int((NOW + timedelta(days=1 if not due else -1)).timestamp()),
            })
        docs.run_transaction(put)
    return docs


def test_v10382_operator_plan_is_read_only_and_due_filtered():
    docs = _records()
    before = dict(docs._documents)
    plan = HostedRecoveryJobPlanner(docs).inspect(now=NOW)
    assert plan.checked_count == 1 and len(plan.due_items) == 1
    assert not plan.execution_authorized
    assert 'owner_user_id' not in str(plan.public())
    assert docs._documents == before
    assert HostedRecoveryJobPlanner(docs).revalidate(plan, now=NOW) == plan


def test_v10382_schedule_not_due_and_disabled_never_runs():
    not_due = HostedRecoveryJobPlanner(_records(due=False)).inspect(now=NOW)
    disabled = HostedRecoveryJobPlanner(_records(enabled=False)).inspect(now=NOW)
    assert not not_due.due_items and not_due.not_yet_due_count == 1
    assert not disabled.due_items and disabled.disabled_count == 1


def test_v10382_schedule_root_and_membership_drifts_fail_closed():
    docs = _records()
    plan = HostedRecoveryJobPlanner(docs).inspect(now=NOW)
    pid = plan.due_items[0].project_id
    docs.run_transaction(lambda tx: tx.set(('projects', pid, 'members', UID), {'role': 'editor'}))
    with pytest.raises(DocumentConflict, match='OWNER_OR_PROJECT_CHANGED'):
        HostedRecoveryJobPlanner(docs).revalidate(plan, now=NOW)


def test_v10382_schedule_due_change_invalidates_original_plan():
    docs = _records()
    plan = HostedRecoveryJobPlanner(docs).inspect(now=NOW)
    pid = plan.due_items[0].project_id
    def change(tx):
        path = ('recovery_schedules', pid)
        row = tx.get(path)
        tx.set(path, {**row.data, 'next_due_epoch': row.data['next_due_epoch'] + 50})
    docs.run_transaction(change)
    with pytest.raises(DocumentConflict, match='SCHEDULE_DRIFT'):
        HostedRecoveryJobPlanner(docs).revalidate(plan, now=NOW)


def test_v10382_operator_scheduler_enforces_bounded_scan():
    docs = _records(count=3)
    with pytest.raises(DocumentConflict, match='SCAN_TOO_LARGE'):
        HostedRecoveryJobPlanner(docs, max_records=2).inspect(now=NOW)


def test_v10382_schedule_rejects_malformed_or_short_retention():
    docs = _records()
    pid = HostedRecoveryJobPlanner(docs).inspect(now=NOW).due_items[0].project_id
    def change(tx):
        path = ('recovery_schedules', pid)
        row = tx.get(path)
        tx.set(path, {**row.data, 'retention_days': 4})
    docs.run_transaction(change)
    with pytest.raises(DocumentConflict, match='RETENTION_FLOOR'):
        HostedRecoveryJobPlanner(docs).inspect(now=NOW)


def test_v10382_naive_time_rejected_for_operator_plan():
    with pytest.raises(ValueError, match='UTC_CLOCK_REQUIRED'):
        HostedRecoveryJobPlanner(_records()).inspect(now=NOW.replace(tzinfo=None))


def test_v10382_identity_review_requires_exact_hashed_binding_and_preserves_status():
    docs = MemoryDocumentStore()
    issuer, subject = 'https://issuer.example', 'external-sub-1'
    bid = identity_binding_id(issuer + '\n' + subject)
    docs.run_transaction(lambda tx: tx.create(('identity_bindings', bid), {
        'user_id': UID, 'issuer': issuer, 'subject': subject,
    }))
    review = HostedIdentityClosureInspector(docs).inspect(UID)
    assert review.bindings == ((issuer, subject, bid),)
    assert not review.identity_revocation_complete
    assert not review.physical_deletion_authorized
    assert 'subject' not in str(review.public())


def test_v10382_identity_review_rejects_wrong_path_and_never_claims_provider_revocation():
    docs = MemoryDocumentStore()
    docs.run_transaction(lambda tx: tx.create(('identity_bindings', 'wrong'), {
        'user_id': UID, 'issuer': 'issuer', 'subject': 'sub',
    }))
    with pytest.raises(DocumentConflict, match='BINDING_MISMATCH'):
        HostedIdentityClosureInspector(docs).inspect(UID)
    assert not hasattr(HostedIdentityClosureInspector, 'revoke')
    assert not hasattr(HostedIdentityClosureInspector, 'delete')


def test_v10382_identity_review_rejects_incomplete_data():
    docs = MemoryDocumentStore()
    docs.run_transaction(lambda tx: tx.create(('identity_bindings', 'broken'), {
        'user_id': UID, 'issuer': '', 'subject': 'sub',
    }))
    with pytest.raises(DocumentConflict, match='DATA_INCOMPLETE'):
        HostedIdentityClosureInspector(docs).inspect(UID)


def test_v10382_real_google_admin_preflight_refuses_memory():
    with pytest.raises(ValueError, match='GOOGLE_ADAPTERS_REQUIRED'):
        HostedGooglePurgePreflight(
            type('P', (), {'documents': MemoryDocumentStore(), 'objects': object()})(),
            type('R', (), {'recovery_objects': object()})(),
        )


def test_v10382_google_preflight_has_no_delete_surface_or_http_registration():
    assert not hasattr(HostedGooglePurgePreflight, 'delete')
    assert not hasattr(HostedGooglePurgePreflight, 'execute')
    assert not HostedProductionPurgeGate.assess(recorded_proofs=frozenset(REQUIRED_PROOFS)).production_deletion_authorized
    api = (Path(__file__).resolve().parents[1] / 'src/hosted/http_api.py').read_text()
    assert 'HostedGooglePurgePreflight' not in api
    assert 'HostedIdentityClosureInspector' not in api


def test_v10382_drill_coordinator_is_bounded_and_rejects_duplicates():
    # Instantiate without invoking its constructor to assert input validation
    # runs before attempting to access an independent recovery provider.
    coordinator = object.__new__(HostedRecoveryDrillCoordinator)
    with pytest.raises(ValueError, match='BATCH_INVALID'):
        coordinator.execute_isolated(())
    request = OfflineDrillRequest(PID, UID, 'archive_1')
    with pytest.raises(ValueError, match='DUPLICATE'):
        coordinator.execute_isolated((request, request))
    with pytest.raises(ValueError, match='BATCH_INVALID'):
        coordinator.execute_isolated(tuple(request for _ in range(13)))


def test_v10382_operator_boundaries_stay_outside_local_runtime():
    base = Path(__file__).resolve().parents[1]
    local = (base / 'src/runtime/local.py').read_text()
    assert 'operator_project_preflight' not in local
    assert 'operator_recovery_plan' not in local
    assert 'operator_identity_review' not in local
