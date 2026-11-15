"""Synthetic retention boundaries only; no production erasure authority."""
from dataclasses import asdict
import pytest
from hosted.security.saved_word_guard import SavedWordCoverageGuard, LEDGER_COLLECTION
from hosted.security.saved_word_retention import POLICY_PATH
from hosted.security.saved_word_identity import WordIdentity
from hosted.firestore.memory import MemoryDocumentStore
from hosted.runtime_services import _DEV_USER_ID as UID


def policy():
    entry = WordIdentity('identity-platform://synthetic', 'external', 1700000000000,
        UID, '2023-11-14T22:13:20Z', 1700000001.0, '2023-11-14T22:13:21Z')
    return {'schema_version': 1, 'revision': 'policy_1', 'entries': [asdict(entry)]}


@pytest.mark.parametrize('enabled', [False, True])
def test_physical_root_deletion_removes_only_its_ledger_when_enabled(enabled):
    raw = MemoryDocumentStore(); pid = 'a'*32
    root = ('projects', pid); ledger = (LEDGER_COLLECTION, pid)
    raw.run_transaction(lambda tx: (tx.set(root, {'generation': 1}),
        tx.set(ledger, {'words': 5}), tx.set(POLICY_PATH, policy()),
        tx.set((LEDGER_COLLECTION, 'b'*32), {'words': 9})))
    SavedWordCoverageGuard(raw, enabled=enabled).run_transaction(lambda tx: tx.delete(root))
    assert raw.get(root) is None
    assert (raw.get(ledger) is None) is enabled
    assert raw.get(POLICY_PATH).data == policy()
    assert raw.get((LEDGER_COLLECTION, 'b'*32)).data == {'words': 9}


def test_failed_delete_rolls_back_ledger_and_missing_root_noop_does_not_claim_cleanup():
    raw = MemoryDocumentStore(); root = ('projects', 'a'*32); ledger = (LEDGER_COLLECTION, 'a'*32)
    raw.run_transaction(lambda tx: (tx.set(root, {'generation': 1}), tx.set(ledger, {'words': 5})))
    guard = SavedWordCoverageGuard(raw, enabled=True)
    def fail(tx):
        tx.delete(root)
        raise RuntimeError('synthetic abort')
    with pytest.raises(RuntimeError): guard.run_transaction(fail)
    assert raw.get(root) is not None and raw.get(ledger).data == {'words': 5}
    raw.run_transaction(lambda tx: tx.delete(root))
    guard.run_transaction(lambda tx: tx.delete(root))
    assert raw.get(ledger).data == {'words': 5}


def test_project_census_reports_external_references_without_deletion_authority(monkeypatch):
    from tests.test_v10374 import _world, _tombstone, _planner
    runtime, docs, _, recovery, pid = _world(monkeypatch)
    stamp = _tombstone(runtime, docs, pid)
    docs.run_transaction(lambda tx: (tx.set((LEDGER_COLLECTION, pid), {'words': 5}),
                                    tx.set(POLICY_PATH, policy())))
    planner = _planner(runtime, docs, recovery, stamp)
    planner.saved_word_retention_enabled = True
    result = planner.preview(pid, UID)
    assert result.saved_word_ledger_paths == ((LEDGER_COLLECTION, pid),)
    assert result.saved_word_policy_reference_paths == (POLICY_PATH,)
    assert 'HOSTED_PURGE_SAVED_WORD_POLICY_REVIEW_REQUIRED' in result.blockers
    assert not result.document_inventory_exhaustive and not result.physical_purge_authorized
    assert POLICY_PATH not in result.firestore_paths
    assert docs.get(POLICY_PATH).data == policy()


@pytest.mark.parametrize('malformed', [False, True])
def test_account_census_blocks_shared_policy_without_making_it_deletable(monkeypatch, malformed):
    from tests.test_v10379 import _fixture
    _, docs, _, _, pid, planner = _fixture(monkeypatch)
    value = {'unexpected': UID} if malformed else policy()
    docs.run_transaction(lambda tx: (tx.set((LEDGER_COLLECTION, pid), {'words': 5}),
                                    tx.set(POLICY_PATH, value)))
    planner.saved_word_retention_enabled = True
    result = planner.preview(UID)
    assert result.saved_word_ledger_paths == ((LEDGER_COLLECTION, pid),)
    assert result.saved_word_policy_reference_paths == (POLICY_PATH,)
    assert 'HOSTED_ACCOUNT_SAVED_WORD_POLICY_REVIEW_REQUIRED' in result.blockers
    assert POLICY_PATH not in result.account_document_paths
    assert not result.physical_deletion_authorized
    if malformed: assert 'HOSTED_ACCOUNT_SAVED_WORD_POLICY_UNVERIFIED' in result.blockers
    assert docs.get(POLICY_PATH).data == value


def test_account_census_marks_unattributed_orphan_ledger_unresolved(monkeypatch):
    from tests.test_v10379 import _fixture
    _, docs, _, _, _, planner = _fixture(monkeypatch)
    path = (LEDGER_COLLECTION, 'f'*32)
    docs.run_transaction(lambda tx: tx.set(path, {'words': 5}))
    planner.saved_word_retention_enabled = True
    result = planner.preview(UID)
    assert path in result.saved_word_ledger_paths
    assert path not in result.account_document_paths
    assert 'HOSTED_ACCOUNT_SAVED_WORD_ORPHAN_LEDGER_UNATTRIBUTED' in result.blockers
    assert docs.get(path) is not None


def test_default_project_planner_never_reads_word_sidecars(monkeypatch):
    from tests.test_v10374 import _world, _tombstone, _planner
    runtime, docs, _, recovery, pid = _world(monkeypatch)
    stamp = _tombstone(runtime, docs, pid)
    planner = _planner(runtime, docs, recovery, stamp)
    before = planner.preview(pid, UID)
    docs.run_transaction(lambda tx: (tx.set((LEDGER_COLLECTION, pid), {'words': 5}),
                                    tx.set(POLICY_PATH, policy())))
    get = docs.get
    def forbid(path):
        if tuple(path) == POLICY_PATH or tuple(path)[0] == LEDGER_COLLECTION:
            raise AssertionError('default path expanded saved-word reads')
        return get(path)
    monkeypatch.setattr(docs, 'get', forbid)
    result = planner.preview(pid, UID)
    assert result.fingerprint == before.fingerprint
    assert result.saved_word_ledger_paths == result.saved_word_policy_reference_paths == ()


def test_malformed_policy_blocks_opt_in_project_census(monkeypatch):
    from tests.test_v10374 import _world, _tombstone, _planner
    runtime, docs, _, recovery, pid = _world(monkeypatch)
    stamp = _tombstone(runtime, docs, pid)
    docs.run_transaction(lambda tx: tx.set(POLICY_PATH, {'entries': 'unverified'}))
    planner = _planner(runtime, docs, recovery, stamp)
    planner.saved_word_retention_enabled = True
    result = planner.preview(pid, UID)
    assert not result.document_inventory_exhaustive
    assert 'HOSTED_PURGE_SAVED_WORD_POLICY_UNVERIFIED' in result.blockers


@pytest.mark.parametrize('after_arm', [False, True])
def test_account_sandbox_cannot_ignore_policy_before_or_after_preflight(monkeypatch, after_arm):
    from tests.test_v10381 import _fixture, _closure, _step, KEY
    from hosted.firestore.contracts import DocumentConflict
    docs, _, _, planner, _, _ = _fixture(monkeypatch)
    planner.saved_word_retention_enabled = True
    closure = _closure(planner)
    if after_arm:
        closure.arm(UID, operation_key=KEY)
    docs.run_transaction(lambda tx: tx.set(POLICY_PATH, policy()))
    with pytest.raises(DocumentConflict):
        if after_arm:
            _step(closure)
        else:
            closure.arm(UID, operation_key=KEY)
    assert docs.get(('users', UID)) is not None
    assert docs.get(POLICY_PATH).data == policy()
