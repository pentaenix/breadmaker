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


def test_opt_in_save_counts_plaintext_and_journal_restores_metadata(monkeypatch):
    app, raw, _, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    domain.saved_word_projection_enabled = True
    journal = ActionJournal(domain.documents)
    _replace(app, pid, cid, [{'text_content': 'two words'}], 'op_word_initial')
    token = journal.begin(pid, OWNER, 'Synthetic counted save')['token']
    with bind_action(token, pid, OWNER):
        _replace(app, pid, cid, [{'text_content': 'three saved words'}], 'op_word_counted')
    journal.finish(pid, OWNER, token)
    def totals():
        return [row.data['_bm_saved_word_v1']['words'] for row in raw.query_collection(('projects', pid, 'blocks'))]
    assert totals() == [3]
    journal.restore(pid, OWNER, token, 'undo')
    assert totals() == [2]
    journal.restore(pid, OWNER, token, 'redo')
    assert totals() == [3]


def test_default_save_has_no_word_metadata(monkeypatch):
    app, raw, _, pid, cid = setup(monkeypatch, enabled=False)
    _replace(app, pid, cid, [{'text_content': 'Synthetic'}], 'op_word_no_metadata')
    assert all('_bm_saved_word_v1' not in row.data for row in raw.query_collection(('projects', pid, 'blocks')))


def test_import_preparation_recomputes_not_trusts_supplied_count(monkeypatch):
    from hosted.packages import HostedProjectPackageService
    app, _, _, pid, _ = setup(monkeypatch)
    app.projects.domain.saved_word_projection_enabled = True
    service = HostedProjectPackageService(app.projects)
    with pytest.raises(ValueError, match='HOSTED_PACKAGE_PROVIDER_FIELDS_FORBIDDEN'):
        service._prepare_import_entity('manuscript_block', {'project_id': pid},
            {'text_content': 'two words', '_bm_saved_word_v1': {'words': 999}}, entity_id='b_words_import')
    _, stored = service._prepare_import_entity('manuscript_block', {'project_id': pid},
        {'text_content': 'two words'}, entity_id='b_words_import')
    assert stored['_bm_saved_word_v1']['words'] == 2


@pytest.mark.parametrize('enabled', [False, True])
def test_encrypted_restore_counts_existing_authorized_plaintext_only(enabled):
    from tests.test_v10524_private_live_restore import source_archive, private_store, DEST, OWNER as SOURCE_OWNER
    from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer
    source_pid, reader = source_archive()
    docs, _, destination, _ = private_store(DEST)
    destination.domain.saved_word_projection_enabled = enabled
    restorer = HostedEncryptedLiveRestorer(reader, destination,
        authorize_destination=lambda uid: uid == DEST)
    result = restorer.restore(source_pid, SOURCE_OWNER, DEST,
        operation='private_backup', restore_operation='restore_words_synthetic')
    row = docs.get(('projects', result.destination_project_id, 'blocks', 'b_1'))
    if enabled:
        assert row.data['_bm_saved_word_v1']['words'] == 2
    else:
        assert '_bm_saved_word_v1' not in row.data
    assert 'Unpublished secret' not in str(row.data)
    assert docs.get((LEDGER_COLLECTION, result.destination_project_id)) is None


def test_generic_create_update_delete_contributions(monkeypatch):
    app, raw, _, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    domain.saved_word_projection_enabled = True
    domain.create('manuscript_block', {'project_id': pid},
        {'chapter_id': cid, 'text_content': 'two words'},
        user_id=OWNER, entity_id='b_generic_words', operation='op_generic_create')
    path = ('projects', pid, 'blocks', 'b_generic_words')
    assert raw.get(path).data['_bm_saved_word_v1']['words'] == 2
    domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_generic_words'},
        {'text_content': 'three saved words'}, user_id=OWNER, operation='op_generic_update')
    assert raw.get(path).data['_bm_saved_word_v1']['words'] == 3
    seed(raw, pid)
    domain.delete('manuscript_block', {'project_id': pid, 'block_id': 'b_generic_words'}, user_id=OWNER)
    assert raw.get(path) is None
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] is None


def test_default_import_preparation_adds_no_word_metadata(monkeypatch):
    from hosted.packages import HostedProjectPackageService
    app, _, _, pid, _ = setup(monkeypatch, enabled=False)
    _, stored = HostedProjectPackageService(app.projects)._prepare_import_entity(
        'manuscript_block', {'project_id': pid}, {'text_content': 'two words'},
        entity_id='b_words_default_import')
    assert '_bm_saved_word_v1' not in stored


def test_complete_initialization_and_atomic_aggregate_maintenance(monkeypatch):
    import time
    app, raw, guard, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    domain.saved_word_projection_enabled = True
    _replace(app, pid, cid, [{'text_content': 'two words'}], 'op_ledger_seed')
    revision = app.projects.get(pid)['generation']
    result = guard.initialize_metadata(pid, expected_revision=revision, deadline=time.monotonic()+20)
    assert result['state'] == 'complete' and result['words'] == 2
    journal = ActionJournal(domain.documents)
    token = journal.begin(pid, OWNER, 'Counted edit')['token']
    with bind_action(token, pid, OWNER):
        _replace(app, pid, cid, [{'text_content': 'three saved words'}], 'op_ledger_edit')
    journal.finish(pid, OWNER, token)
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] == 3
    journal.restore(pid, OWNER, token, 'undo')
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] == 2
    journal.restore(pid, OWNER, token, 'redo')
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] == 3
    _replace(app, pid, cid, [], 'op_ledger_delete')
    final = raw.get((LEDGER_COLLECTION, pid)).data
    assert final['state'] == 'complete' and final['words'] == 0 and final['blocks'] == 0


def test_disabled_writer_invalidates_stale_contribution(monkeypatch):
    import time
    app, raw, guard, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    domain.saved_word_projection_enabled = True
    _replace(app, pid, cid, [{'id': 'b_binding', 'text_content': 'two words'}], 'op_binding_seed')
    guard.initialize_metadata(pid, expected_revision=app.projects.get(pid)['generation'],
                              deadline=time.monotonic()+20)
    domain.saved_word_projection_enabled = False
    domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_binding'},
        {'text_content': 'now three words'}, user_id=OWNER, operation='op_disabled_change')
    assert raw.get((LEDGER_COLLECTION, pid)).data['state'] == 'unknown'
    result = guard.initialize_metadata(pid, expected_revision=app.projects.get(pid)['generation'],
                                       deadline=time.monotonic()+20)
    assert result['state'] == 'unknown' and result['words'] is None


def test_initializer_missing_metadata_deadline_revision_and_off(monkeypatch):
    import time
    app, raw, guard, pid, _ = setup(monkeypatch)
    rev = app.projects.get(pid)['generation']
    result = guard.initialize_metadata(pid, expected_revision=rev, deadline=time.monotonic()+20)
    assert result['state'] == 'unknown'
    previous = raw.get((LEDGER_COLLECTION, pid)).revision
    for revision, deadline in [(rev+1, time.monotonic()+20), (rev, time.monotonic()-1)]:
        with pytest.raises(ValueError):
            guard.initialize_metadata(pid, expected_revision=revision, deadline=deadline)
    assert raw.get((LEDGER_COLLECTION, pid)).revision == previous
    guard.enabled = False
    assert guard.initialize_metadata(pid, expected_revision=rev, deadline=0) is None


def test_initializer_overflow_never_publishes_partial_total(monkeypatch):
    import time
    app, raw, guard, pid, _ = setup(monkeypatch)
    metadata = {'definition': 'saved_text_unicode_words_v1', 'words': 1, 'source_generation': 1}
    def seed_many(tx):
        for i in range(1001):
            tx.set(('projects', pid, 'blocks', 'b_limit_' + str(i)),
                   {'generation': 1, '_bm_saved_word_v1': metadata})
    raw.run_transaction(seed_many)
    result = guard.initialize_metadata(pid, expected_revision=app.projects.get(pid)['generation'],
                                       deadline=time.monotonic()+20)
    assert result['state'] == 'unknown' and result['words'] is None


@pytest.mark.parametrize('explicit_revision', [False, True])
def test_generic_update_replay_keeps_bound_count_and_revision(monkeypatch, explicit_revision):
    import time
    app, raw, guard, pid, cid = setup(monkeypatch)
    domain = app.projects.domain
    domain.saved_word_projection_enabled = True
    _replace(app, pid, cid, [{'id': 'b_replay', 'text_content': 'two words'}], 'op_replay_seed')
    revision = app.projects.get(pid)['generation']
    guard.initialize_metadata(pid, expected_revision=revision, deadline=time.monotonic()+20)
    options = {'expected_project_revision': revision} if explicit_revision else {}
    for _ in range(2):
        domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_replay'},
            {'text_content': 'three saved words'}, user_id=OWNER,
            operation='op_binding_replay', **options)
    row = raw.get(('projects', pid, 'blocks', 'b_replay')).data
    assert app.projects.get(pid)['generation'] == revision + 1
    assert row['generation'] == row['_bm_saved_word_v1']['source_generation'] == 2
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] == 3


def test_encrypted_chapter_replay_ignores_only_derived_binding():
    from tests.test_v10524_private_live_restore import private_store, OWNER as PRIVATE_OWNER
    from hosted.chapter_mutations import replace_chapter_blocks
    docs, _, projects, _ = private_store(PRIVATE_OWNER)
    projects.domain.saved_word_projection_enabled = True
    pid = projects.create('Synthetic encrypted replay')['id']
    cid = projects.domain.list('chapter', {'project_id': pid}, user_id=PRIVATE_OWNER)[0]['id']
    revision = projects.get(pid)['generation']
    options = dict(project_id=pid, chapter_id=cid, user_id=PRIVATE_OWNER,
        blocks=[{'id': 'b_1', 'text_content': 'two words'}],
        operation='op_encrypted_binding', expected_revision=revision)
    first = replace_chapter_blocks(projects.domain, **options)
    second = replace_chapter_blocks(projects.domain, **options)
    assert first['generation'] == second['generation'] == revision + 1
    row = docs.get(('projects', pid, 'blocks', 'b_1')).data
    assert row['_bm_saved_word_v1']['source_generation'] == row['generation']
