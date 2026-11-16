"""Production preflight composition with synthetic Google-shaped adapters."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
from hosted.firestore.contracts import DocumentConflict
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.memory import MemoryObjectStore
from hosted.purge_discovery import MemoryProjectTreeDiscovery
from hosted import operator_project_preflight as operator
from hosted.security.saved_word_guard import LEDGER_COLLECTION
from hosted.security.saved_word_retention import POLICY_PATH
from tests.test_v10596_saved_word_retention import policy, UID

PID = 'a' * 32
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def preflight(monkeypatch, *, ledger=True, value=None):
    raw = MemoryDocumentStore()
    raw.run_transaction(lambda tx: (
        tx.create(('projects', PID), {'status': 'deleted', 'generation': 7,
            'deleted_at': (NOW - timedelta(days=31)).isoformat()}),
        tx.create(('projects', PID, 'members', UID), {'role': 'owner'})))
    if ledger:
        raw.run_transaction(lambda tx: tx.create((LEDGER_COLLECTION, PID), {'words': 5}))
    if value is not None:
        raw.run_transaction(lambda tx: tx.create(POLICY_PATH, value))
    # Exact adapter types retain the production constructor boundary; their IO
    # is injected locally so these checks cannot contact a provider or erase data.
    docs = object.__new__(GoogleFirestoreDocumentStore)
    docs.get = raw.get
    live, archive = object.__new__(GoogleCloudObjectStore), object.__new__(GoogleCloudObjectStore)
    live._bucket, archive._bucket = SimpleNamespace(name='live'), SimpleNamespace(name='independent')
    live.list_prefix = MemoryObjectStore().list_prefix
    archive.list_prefix = MemoryObjectStore().list_prefix
    projects = SimpleNamespace(documents=docs, objects=live)
    recovery = SimpleNamespace(projects=projects, recovery_objects=archive)
    monkeypatch.setattr(operator, 'GoogleProjectTreeDiscovery', lambda _docs: MemoryProjectTreeDiscovery(raw))
    return operator.HostedGooglePurgePreflight(projects, recovery, now=lambda: NOW), raw


def test_operator_always_includes_census_and_blocks_sidecars_without_archive_read(monkeypatch):
    check, raw = preflight(monkeypatch, value=policy())
    before = deepcopy(raw._documents)
    assert check.planner.saved_word_retention_enabled is True
    result = check.inspect(PID, UID, archive_operation='not_read')
    assert result.saved_word_ledger_count == result.saved_word_policy_reference_count == 1
    assert 'HOSTED_PURGE_SAVED_WORD_LEDGER_CLEANUP_REQUIRED' in result.blockers
    assert 'HOSTED_PURGE_SAVED_WORD_POLICY_REVIEW_REQUIRED' in result.blockers
    assert result.public()['archive_verified'] is False
    assert result.public()['production_deletion_authorized'] is False
    assert raw._documents == before
    assert not hasattr(check, 'execute') and not hasattr(check, 'delete')


def test_unverified_policy_is_reported_even_without_project_ledger(monkeypatch):
    check, raw = preflight(monkeypatch, ledger=False, value={'entries': 'invalid'})
    result = check.inspect(PID, UID, archive_operation='not_read')
    assert result.saved_word_ledger_count == 0 and result.saved_word_policy_reference_count == 1
    assert 'HOSTED_PURGE_SAVED_WORD_POLICY_UNVERIFIED' in result.blockers
    assert raw.get(POLICY_PATH).data == {'entries': 'invalid'}


def test_sidecar_change_between_operator_censuses_fails_closed(monkeypatch):
    check, raw = preflight(monkeypatch)
    original = check.planner.preview
    calls = []
    def changing(*args):
        if calls:
            raw.run_transaction(lambda tx: tx.set((LEDGER_COLLECTION, PID), {'words': 9}))
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(check.planner, 'preview', changing)
    with pytest.raises(DocumentConflict, match='CROSS_PROVIDER_DRIFT'):
        check.inspect(PID, UID, archive_operation='not_read')


def test_retention_floor_is_still_required_before_word_diagnostics(monkeypatch):
    check, raw = preflight(monkeypatch)
    raw.run_transaction(lambda tx: tx.set(('projects', PID), {'status': 'deleted',
        'generation': 8, 'deleted_at': NOW.isoformat()}))
    with pytest.raises(DocumentConflict, match='RETENTION_INCOMPLETE'):
        check.inspect(PID, UID, archive_operation='not_read')
