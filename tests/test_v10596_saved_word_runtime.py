"""Explicit runtime contribution/guard composition; synthetic data only."""
import pytest
from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from hosted.runtime_services import _DEV_USER_ID as UID
from hosted.security.saved_word_guard import SavedWordCoverageGuard, LEDGER_COLLECTION
from hosted.security.account_word_guard import AccountWordGuard
from runtime.hosted import build_hosted_dev_runtime


def runtime(monkeypatch, enabled, *, lifecycle=False):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv('BREADMAKER_SAVED_WORDS_ENABLED', 'true' if enabled else 'false')
    raw = MemoryDocumentStore()
    documents = AccountLifecycleDocumentStore(raw) if lifecycle else raw
    app = build_hosted_dev_runtime(documents=documents, objects=MemoryObjectStore())
    return app, raw, documents


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('lifecycle', [False, True])
def test_exact_runtime_chain_and_new_project_coverage_save_undo_replay(monkeypatch, enabled, lifecycle):
    from tests.test_v10364 import _replace
    app, raw, source = runtime(monkeypatch, enabled, lifecycle=lifecycle)
    wrapped = app.projects.domain.documents
    assert type(wrapped) is ActionDocumentStore
    if enabled:
        assert type(wrapped.store) is SavedWordCoverageGuard
        assert type(wrapped.store.store) is AccountWordGuard
        assert wrapped.store.store.store is source
        assert wrapped.store.initialize_new_projects is True
    else:
        assert wrapped.store is source
    assert app.projects.domain.saved_word_projection_enabled is enabled
    assert app.assets.domain.saved_word_projection_enabled is enabled
    pid = app.projects.create('Synthetic contribution runtime')['id']
    cid = app.projects.domain.list('chapter', {'project_id': pid}, user_id=UID)[0]['id']
    _replace(app, pid, cid, [{'id': 'b_runtime', 'text_content': 'two words'}], 'op_runtime_first')
    journal = ActionJournal(wrapped)
    token = journal.begin(pid, UID, 'Synthetic runtime undo')['token']
    revision = app.projects.get(pid)['generation']
    with bind_action(token, pid, UID):
        _replace(app, pid, cid, [{'id': 'b_runtime', 'text_content': 'three saved words'}],
                 'op_runtime_second', revision)
    journal.finish(pid, UID, token)
    _replace(app, pid, cid, [{'id': 'b_runtime', 'text_content': 'three saved words'}],
             'op_runtime_second', revision)
    for direction, words in [('undo', 2), ('redo', 3)]:
        journal.restore(pid, UID, token, direction)
        row = raw.get(('projects', pid, 'blocks', 'b_runtime')).data
        if enabled:
            assert row['_bm_saved_word_v1']['words'] == words
            assert row['_bm_saved_word_v1']['source_generation'] == row['generation']
        else:
            assert '_bm_saved_word_v1' not in row
    ledger = raw.get((LEDGER_COLLECTION, pid))
    if enabled:
        assert ledger.data['state'] == 'complete' and ledger.data['words'] == 3
        assert ledger.data['canonical_revision'] == app.projects.get(pid)['generation']
    else:
        assert ledger is None


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('lifecycle', [False, True])
def test_actual_package_import_inherits_runtime_flag(monkeypatch, enabled, lifecycle):
    from hosted.packages import HostedProjectPackageService
    from tests.test_v10364 import _replace
    app, raw, _ = runtime(monkeypatch, enabled, lifecycle=lifecycle)
    pid = app.projects.create('Synthetic import source')['id']
    cid = app.projects.domain.list('chapter', {'project_id': pid}, user_id=UID)[0]['id']
    _replace(app, pid, cid, [{'text_content': 'two words'}], 'op_runtime_export')
    packages = HostedProjectPackageService(app.projects)
    restored = packages.import_bytes(packages.export_bytes(pid, UID), UID, operation='op_runtime_import')
    target = restored['id']
    blocks = raw.query_collection(('projects', target, 'blocks'))
    assert blocks
    assert all(('_bm_saved_word_v1' in row.data) is enabled for row in blocks)
    if enabled: assert sum(row.data['_bm_saved_word_v1']['words'] for row in blocks) == 2
    ledger = raw.get((LEDGER_COLLECTION, target))
    if enabled:
        assert ledger.data['state'] == 'complete' and ledger.data['words'] == 2
    else:
        assert ledger is None


@pytest.mark.parametrize('enabled', [False, True])
def test_document_import_publishes_coverage_only_at_fenced_root_commit(monkeypatch, enabled):
    from hosted.document_import import DocumentImport
    app, raw, _ = runtime(monkeypatch, enabled, lifecycle=True)
    service = DocumentImport(app.projects)
    progress = service.begin(b'First paragraph\nSecond paragraph', 'Synthetic.txt',
        'heading1', 'Synthetic document', UID, 'guarded_document_import')
    pid = service._load(UID, progress['job_id'])['project_id']
    while not progress['ready']:
        assert raw.get(('projects', pid)) is None and raw.get((LEDGER_COLLECTION, pid)) is None
        progress = service.step(UID, progress['job_id'], progress['next_batch'])
    assert raw.get(('projects', pid)) is None and raw.get((LEDGER_COLLECTION, pid)) is None
    result = service.finish(UID, progress['job_id'])
    assert result['project']['id'] == pid
    ledger = raw.get((LEDGER_COLLECTION, pid))
    if enabled:
        assert ledger.data['state'] == 'complete' and ledger.data['words'] == 4
    else:
        assert ledger is None


@pytest.mark.parametrize('enabled', [False, True])
def test_encrypted_restore_uses_explicit_repository_constructor_flag(enabled):
    from tests.test_v10524_private_live_restore import source_archive, private_store, DEST, OWNER
    from hosted.runtime_services import HostedCloudProjectRepository, HostedHistoryStore, HostedCommittedAssetStore
    from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer
    source_pid, reader = source_archive()
    docs, objects, original, _ = private_store(DEST)
    options = dict(encrypted_fields=original.domain.encrypted_fields,
                   encrypted_objects=original.domain.encrypted_objects,
                   saved_word_projection_enabled=enabled)
    destination = HostedCloudProjectRepository(docs, objects, original.authority,
        original.users, lambda: DEST, **options)
    history = HostedHistoryStore(docs, objects, lambda: DEST, lambda: source_pid, **options)
    assets = HostedCommittedAssetStore(docs, objects, lambda: DEST, lambda: source_pid, **options)
    assert history.domain.saved_word_projection_enabled is enabled
    assert assets.domain.saved_word_projection_enabled is enabled
    result = HostedEncryptedLiveRestorer(reader, destination, authorize_destination=lambda uid: uid == DEST).restore(
        source_pid, OWNER, DEST, operation='private_backup', restore_operation='restore_runtime_option')
    row = docs.get(('projects', result.destination_project_id, 'blocks', 'b_1')).data
    assert ('_bm_saved_word_v1' in row) is enabled
    assert docs.get((LEDGER_COLLECTION, result.destination_project_id)) is None


def test_privileged_encrypted_restore_publication_initializes_through_guard():
    from tests.test_v10524_private_live_restore import source_archive, private_store, DEST, OWNER
    from hosted.runtime_services import HostedCloudProjectRepository
    from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer
    from hosted.firestore.repositories import HostedProjectRepository
    source_pid, reader = source_archive()
    raw, objects, original, _ = private_store(DEST)
    # The existing privileged restorer does not carry ordinary import staging
    # authority through AccountLifecycleDocumentStore. This verifies its explicit
    # destination guard seam without broadening that separate write fence.
    docs = ActionDocumentStore(SavedWordCoverageGuard(raw, enabled=True, initialize_new_projects=True))
    destination = HostedCloudProjectRepository(docs, objects, HostedProjectRepository(docs),
        original.users, lambda: DEST, encrypted_fields=original.domain.encrypted_fields,
        encrypted_objects=original.domain.encrypted_objects, saved_word_projection_enabled=True)
    result = HostedEncryptedLiveRestorer(reader, destination, authorize_destination=lambda uid: uid == DEST).restore(
        source_pid, OWNER, DEST, operation='private_backup', restore_operation='guarded_restore')
    ledger = raw.get((LEDGER_COLLECTION, result.destination_project_id)).data
    assert ledger['state'] == 'complete' and ledger['words'] == 2 and ledger['blocks'] == 1
    assert ledger['canonical_revision'] == destination.get(result.destination_project_id)['generation']


@pytest.mark.parametrize('flag', ['TRUE', '1', 'yes'])
def test_invalid_runtime_flag_fails_before_store_construction(monkeypatch, flag):
    runtime(monkeypatch, False)
    monkeypatch.setenv('BREADMAKER_SAVED_WORDS_ENABLED', flag)
    with pytest.raises(RuntimeError, match='SAVED_WORD_CONFIGURATION_INVALID'):
        build_hosted_dev_runtime()


@pytest.mark.parametrize('flag', [None, '', '0', 'false'])
def test_disabled_flags_preserve_unwrapped_store(monkeypatch, flag):
    runtime(monkeypatch, False)
    if flag is None:
        monkeypatch.delenv('BREADMAKER_SAVED_WORDS_ENABLED', raising=False)
    else:
        monkeypatch.setenv('BREADMAKER_SAVED_WORDS_ENABLED', flag)
    raw = MemoryDocumentStore()
    app = build_hosted_dev_runtime(documents=raw, objects=MemoryObjectStore())
    assert app.projects.documents.store is raw
    assert app.projects.domain.saved_word_projection_enabled is False


def test_runtime_guard_does_not_bypass_account_erasure_fence(monkeypatch):
    from hosted.firestore.contracts import DocumentConflict
    app, raw, _ = runtime(monkeypatch, True, lifecycle=True)
    pid = app.projects.create('Synthetic frozen owner')['id']
    before = raw.get(('projects', pid, 'blocks', 'b_1'))
    ledger_before = raw.get((LEDGER_COLLECTION, pid))
    def freeze(tx):
        profile = dict(tx.get(('users', UID)).data)
        profile['status'] = 'erasure_pending'
        tx.set(('users', UID), profile)
    raw.run_transaction(freeze)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        app.projects.domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_1'},
            {'text_content': 'blocked change'}, user_id=UID, operation='op_runtime_frozen')
    assert raw.get(('projects', pid, 'blocks', 'b_1')) == before
    assert raw.get((LEDGER_COLLECTION, pid)) == ledger_before
