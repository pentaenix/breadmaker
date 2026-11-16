from copy import deepcopy
import pytest
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.action_journal import ActionJournal, bind_action
from hosted.firestore.contracts import DocumentConflict
from hosted.security import account_word_state as records
from runtime.hosted import build_hosted_dev_runtime
from hosted.chapter_mutations import replace_chapter_blocks

A, B = ['usr_' + c*32 for c in ('a', 'b')]


def _replace(app, pid, cid, rows, operation, revision=None):
    return replace_chapter_blocks(app.projects.domain, project_id=pid, chapter_id=cid,
        user_id=app.projects.user_id_provider(), blocks=rows, operation=operation, expected_revision=revision)


def runtime(monkeypatch, *, uid=A, enabled=True, raw=None, objects=None):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_USER_ID', uid)
    monkeypatch.setenv('BREADMAKER_SAVED_WORDS_ENABLED', 'true' if enabled else 'false')
    raw = raw or MemoryDocumentStore(); objects = objects or MemoryObjectStore()
    app = build_hosted_dev_runtime(documents=AccountLifecycleDocumentStore(raw), objects=objects)
    return app, raw, objects


def new_project(app, uid, text):
    pid = app.projects.create('Synthetic account-owned manuscript')['id']
    cid = app.projects.domain.list('chapter', {'project_id': pid}, user_id=uid)[0]['id']
    _replace(app, pid, cid, [{'id': 'b_words', 'text_content': text}], 'account_seed_' + pid)
    return pid, cid


def test_account_owned_values_maintain_save_replay_undo_redo(monkeypatch):
    app, raw, _ = runtime(monkeypatch)
    pid, cid = new_project(app, A, 'two words')
    assert raw.get(records.path(A)).data['saved_words'] == 2
    journal = ActionJournal(app.projects.documents)
    token = journal.begin(pid, A, 'Synthetic account undo')['token']
    expected = app.projects.get(pid)['generation']
    with bind_action(token, pid, A):
        _replace(app, pid, cid, [{'id': 'b_words', 'text_content': 'three saved words'}], 'account_edit', expected)
    journal.finish(pid, A, token)
    before = raw.get(records.path(A))
    _replace(app, pid, cid, [{'id': 'b_words', 'text_content': 'three saved words'}], 'account_edit', expected)
    assert raw.get(records.path(A)) == before
    for direction, words in [('undo', 2), ('redo', 3)]:
        journal.restore(pid, A, token, direction)
        row = raw.get(records.path(A)).data
        assert row['state'] == 'complete' and row['saved_words'] == words
        assert row['projects'][pid]['canonical_revision'] == app.projects.get(pid)['generation']


def test_shared_project_editor_updates_only_its_verified_owner(monkeypatch):
    one, raw, objects = runtime(monkeypatch)
    first, _ = new_project(one, A, 'two words')
    two, _, _ = runtime(monkeypatch, uid=B, raw=raw, objects=objects)
    second, cid = new_project(two, B, 'three saved words')
    two.projects.authority.add_member(second, B, A, 'editor',
        expected_project_generation=two.projects.get(second)['generation'], operation_id='account_share')
    _replace(one, second, cid, [{'id': 'b_words', 'text_content': 'four saved words now'}], 'account_collaborator_edit')
    a, b = [raw.get(records.path(uid)).data for uid in (A, B)]
    assert a['saved_words'] == 2 and set(a['projects']) == {first}
    assert b['saved_words'] == 4 and set(b['projects']) == {second}


def test_legacy_account_cannot_become_complete_from_one_later_save(monkeypatch):
    old, raw, objects = runtime(monkeypatch, enabled=False)
    pid, cid = new_project(old, A, 'existing saved manuscript')
    assert raw.get(records.path(A)) is None
    current, _, _ = runtime(monkeypatch, raw=raw, objects=objects)
    _replace(current, pid, cid, [{'id': 'b_words', 'text_content': 'new saved text'}], 'account_subset')
    row = raw.get(records.path(A)).data
    assert not row['initialized'] and row['saved_words'] is None and row['state'] == 'unknown'


def test_read_bundle_cache_does_not_invalidate_account_counts(monkeypatch):
    app, raw, _ = runtime(monkeypatch)
    pid, _ = new_project(app, A, 'two words')
    before = raw.get(records.path(A))
    def cache(tx):
        path = ('projects', pid, 'blocks', 'b_words'); row = dict(tx.get(path).data)
        row['_bm_read_bundle'] = {'synthetic': True}; tx.set(path, row)
    app.projects.documents.run_transaction(cache)
    assert raw.get(records.path(A)) == before


def test_account_source_failure_rolls_back_canonical_save(monkeypatch):
    app, raw, _ = runtime(monkeypatch)
    pid, cid = new_project(app, A, 'two words'); before = deepcopy(raw._documents)
    original = raw._write_locked
    def failed(path, data):
        if path == records.path(A): raise RuntimeError('synthetic account commit failure')
        return original(path, data)
    monkeypatch.setattr(raw, '_write_locked', failed)
    with pytest.raises(RuntimeError, match='account commit failure'):
        _replace(app, pid, cid, [{'id': 'b_words', 'text_content': 'three saved words'}], 'account_fail')
    assert raw._documents == before


def test_frozen_account_still_rejects_all_writes_atomically(monkeypatch):
    app, raw, _ = runtime(monkeypatch)
    pid, cid = new_project(app, A, 'two words')
    profile = dict(raw.get(('users', A)).data); profile['status'] = 'erasure_pending'
    raw.run_transaction(lambda tx: tx.set(('users', A), profile)); before = deepcopy(raw._documents)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        _replace(app, pid, cid, [{'id': 'b_words', 'text_content': 'three saved words'}], 'account_frozen')
    assert raw._documents == before


@pytest.mark.parametrize('suffix',[('metrics','saved_words_v1'),('word_block_counts','a'*64),('metric_jobs','word_'+'a'*64)])
def test_normal_canonical_callbacks_cannot_write_derived_account_sources(monkeypatch,suffix):
    app,raw,_=runtime(monkeypatch);before=deepcopy(raw._documents)
    with pytest.raises(DocumentConflict,match='DIRECT_WRITE_FORBIDDEN'):
        app.projects.documents.run_transaction(lambda tx:tx.set(('users',A,*suffix),{'forged':True}))
    assert raw._documents==before
