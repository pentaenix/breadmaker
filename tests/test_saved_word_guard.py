"""Synthetic canonical transaction regression; run through normal CI."""
import pytest
from hosted.security.saved_word_guard import SavedWordCoverageGuard, LEDGER_COLLECTION
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from hosted.action_journal import ActionJournal, bind_action
from hosted.runtime_services import _DEV_USER_ID as OWNER
from runtime.hosted import build_hosted_dev_runtime
from tests.test_v10364 import _replace


def setup(monkeypatch, *, enabled=True):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    raw = MemoryDocumentStore()
    guard = SavedWordCoverageGuard(raw, enabled=enabled)
    app = build_hosted_dev_runtime(documents=guard, objects=MemoryObjectStore())
    pid = app.projects.create('Synthetic word coverage')['id']
    cid = app.projects.domain.list('chapter', {'project_id': pid}, user_id=OWNER)[0]['id']
    return app, raw, guard, pid, cid


def seed(raw, pid):
    value = {'state': 'complete', 'words': 7, 'blocks': 1}
    raw.run_transaction(lambda tx: tx.set((LEDGER_COLLECTION, pid), value))
    return value


def test_disabled_changes_no_ledger(monkeypatch):
    app, raw, _, pid, cid = setup(monkeypatch, enabled=False)
    previous = seed(raw, pid)
    _replace(app, pid, cid, [{'text_content': 'Synthetic'}], 'op_word_disabled')
    assert raw.get((LEDGER_COLLECTION, pid)).data == previous


def test_canonical_save_invalidates_and_replay_does_not_write(monkeypatch):
    app, raw, _, pid, cid = setup(monkeypatch)
    seed(raw, pid)
    revision = app.projects.get(pid)['generation']
    _replace(app, pid, cid, [{'text_content': 'Synthetic'}], 'op_word_save', revision)
    first = raw.get((LEDGER_COLLECTION, pid))
    assert first.data['state'] == 'unknown' and first.data['words'] is None
    _replace(app, pid, cid, [{'text_content': 'Synthetic'}], 'op_word_save', revision)
    assert raw.get((LEDGER_COLLECTION, pid)).revision == first.revision


@pytest.mark.parametrize('direction', ['undo', 'rollback'])
def test_canonical_journal_preserves_guard_and_root_authority(monkeypatch, direction):
    app, raw, _, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER, 'Synthetic word coverage')['token']
    with bind_action(token, pid, OWNER):
        _replace(app, pid, cid, [{'text_content': 'Changed'}], 'op_word_journal')
    if direction == 'undo':
        journal.finish(pid, OWNER, token)
    seed(raw, pid)
    journal.restore(pid, OWNER, token, direction)
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] is None
    if direction == 'undo':
        seed(raw, pid)
        journal.restore(pid, OWNER, token, 'redo')
        assert raw.get((LEDGER_COLLECTION, pid)).data['words'] is None


def test_missing_ledger_not_initialized_by_root_publication(monkeypatch):
    _, raw, guard, pid, _ = setup(monkeypatch)
    def publish(tx):
        row = dict(tx.get(('projects', pid)).data)
        row['generation'] += 1
        tx.set(('projects', pid), row)
    guard.run_transaction(publish)
    assert raw.get((LEDGER_COLLECTION, pid)) is None


def test_failed_transaction_keeps_ledger(monkeypatch):
    _, raw, guard, pid, _ = setup(monkeypatch)
    previous = seed(raw, pid)
    def fail(tx):
        tx.delete(('projects', pid))
        raise RuntimeError('synthetic abort')
    with pytest.raises(RuntimeError):
        guard.run_transaction(fail)
    assert raw.get(('projects', pid)) is not None
    assert raw.get((LEDGER_COLLECTION, pid)).data == previous
