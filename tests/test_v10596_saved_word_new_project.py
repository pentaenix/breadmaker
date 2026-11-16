"""New-root coverage only; no historical initialization or content reads."""
from copy import deepcopy
import pytest
from hosted.firestore.memory import MemoryDocumentStore, _MemoryTransaction
from hosted.firestore.contracts import DocumentConflict
from hosted.security.saved_word_guard import SavedWordCoverageGuard, LEDGER_COLLECTION
from hosted.security.saved_word_projection import DEFINITION
from tests.test_v10596_saved_word_runtime import runtime
from hosted.runtime_services import _DEV_USER_ID as UID

PID = 'a' * 32
ROOT = ('projects', PID)
LEDGER = (LEDGER_COLLECTION, PID)


def block(words=2, generation=1, source_generation=1):
    return {'generation': generation, '_bm_saved_word_v1': {
        'definition': DEFINITION, 'words': words,
        'source_generation': source_generation}}


def publish(tx):
    tx.create(ROOT, {'status': 'active', 'generation': 1})


@pytest.mark.parametrize('lifecycle', [False, True])
def test_runtime_initializes_new_seeded_project_atomically(monkeypatch, lifecycle):
    app, raw, _ = runtime(monkeypatch, True, lifecycle=lifecycle)
    pid = app.projects.create('New synthetic empty project')['id']
    ledger = raw.get((LEDGER_COLLECTION, pid)).data
    assert ledger['state'] == 'complete' and ledger['words'] == 0 and ledger['blocks'] == 1
    assert ledger['canonical_revision'] == app.projects.get(pid)['generation']


def test_flag_and_later_save_never_initialize_existing_project(monkeypatch):
    from runtime.hosted import build_hosted_dev_runtime
    from tests.test_v10364 import _replace
    old, raw, _ = runtime(monkeypatch, False)
    pid = old.projects.create('Existing synthetic manuscript')['id']
    cid = old.projects.domain.list('chapter', {'project_id': pid}, user_id=UID)[0]['id']
    _replace(old, pid, cid, [{'text_content': 'existing saved text'}], 'old_write')
    queries = []
    def forbidden(*args, **kwargs):
        queries.append(True)
        raise AssertionError('historical metadata scan')
    monkeypatch.setattr(_MemoryTransaction, 'saved_word_metadata', forbidden)
    monkeypatch.setenv('BREADMAKER_SAVED_WORDS_ENABLED', 'true')
    current = build_hosted_dev_runtime(documents=raw, objects=old.projects.objects)
    assert raw.get((LEDGER_COLLECTION, pid)) is None
    _replace(current, pid, cid, [{'text_content': 'new authorized save'}], 'new_write')
    assert queries == [] and raw.get((LEDGER_COLLECTION, pid)) is None


def test_staged_generation_bound_metadata_initializes_at_new_root_publication():
    raw = MemoryDocumentStore()
    raw.run_transaction(lambda tx: tx.create(ROOT + ('blocks', 'b_1'), block()))
    guard = SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True)
    guard.run_transaction(publish)
    assert raw.get(LEDGER).data['state'] == 'complete'
    assert raw.get(LEDGER).data['words'] == 2


def test_same_transaction_pending_blocks_are_included():
    raw = MemoryDocumentStore()
    guard = SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True)
    guard.run_transaction(lambda tx: (publish(tx), tx.create(ROOT + ('blocks', 'b_1'), block(3))))
    assert raw.get(LEDGER).data['words'] == 3 and raw.get(LEDGER).data['blocks'] == 1


@pytest.mark.parametrize('data', [{'generation': 1}, block(source_generation=2)])
def test_missing_or_stale_staging_never_becomes_complete(data):
    raw = MemoryDocumentStore()
    raw.run_transaction(lambda tx: tx.create(ROOT + ('blocks', 'b_1'), data))
    SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True).run_transaction(publish)
    assert raw.get(LEDGER).data['state'] == 'unknown' and raw.get(LEDGER).data['words'] is None


def test_overflow_and_query_deadline_are_bounded_and_unknown(monkeypatch):
    raw = MemoryDocumentStore()
    raw.run_transaction(lambda tx: [tx.create(ROOT + ('blocks', f'b_{i}'), block()) for i in range(1001)])
    original = _MemoryTransaction.saved_word_metadata
    calls = []
    def bounded(self, pid, **kw):
        calls.append(kw)
        return original(self, pid, **kw)
    monkeypatch.setattr(_MemoryTransaction, 'saved_word_metadata', bounded)
    SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True).run_transaction(publish)
    assert calls == [{'limit': 1001, 'timeout': 2.0}]
    assert raw.get(LEDGER).data['state'] == 'unknown'


def test_query_failure_does_not_fabricate_empty_coverage(monkeypatch):
    raw = MemoryDocumentStore()
    def failed(*a, **kw): raise TimeoutError('synthetic metadata timeout')
    monkeypatch.setattr(_MemoryTransaction, 'saved_word_metadata', failed)
    SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True).run_transaction(publish)
    assert raw.get(ROOT) is not None and raw.get(LEDGER).data['state'] == 'unknown'


def test_orphan_ledger_collision_preserves_data_and_rejects_root_reuse():
    raw = MemoryDocumentStore(); retained = {'private_existing_state': 7}
    raw.run_transaction(lambda tx: tx.create(LEDGER, retained))
    with pytest.raises(DocumentConflict, match='PROJECT_ID_REUSE'):
        SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True).run_transaction(publish)
    assert raw.get(ROOT) is None and raw.get(LEDGER).data == retained


def test_ledger_write_failure_rolls_back_project_publication(monkeypatch):
    raw = MemoryDocumentStore(); before = deepcopy(raw._documents)
    write = raw._write_locked
    def failed(path, data):
        if path == LEDGER: raise RuntimeError('synthetic ledger commit failure')
        write(path, data)
    monkeypatch.setattr(raw, '_write_locked', failed)
    with pytest.raises(RuntimeError, match='ledger commit failure'):
        SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True).run_transaction(publish)
    assert raw._documents == before
