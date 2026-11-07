"""Inactive novel-sized publication rehearsals against two fenced replicas."""
from copy import deepcopy

import pytest

from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.document_io import parse_document_bytes
from hosted.domain_store import HostedDomainStore
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.manuscript_visibility import FORMAT_FIELD, HEAD_FIELD, ManuscriptDocumentStore
from hosted.revision_staging import ManuscriptRevisionImport, delete_chapter_revision
from tests.test_v10594 import actor, fenced_replicas
from tests.test_v10353 import OWNER


def setup():
    make, raw, objects, pid = fenced_replicas()
    def replica():
        source = make()
        view = ManuscriptDocumentStore(source.documents)
        domain = HostedDomainStore(ActionDocumentStore(view), source.domain.objects)
        return ManuscriptRevisionImport(domain)
    return replica, raw, objects, pid


def parsed(count=150, prefix='Synthetic'):
    return parse_document_bytes('\n'.join(f'{prefix} paragraph {i}' for i in range(count)).encode(), 'Novel.txt')[0]


def stage(make, pid, tabs, operation, **options):
    progress = make().begin(pid, OWNER, tabs, operation=operation, **options)
    while not progress['ready']:
        progress = make().step(pid, OWNER, progress['job_id'], progress['next_batch'])
    return progress['job_id']


def publish(make, pid, job):
    service = make()
    journal = ActionJournal(service.domain.documents)
    token = journal.begin(pid, OWNER)['token']
    with bind_action(token, pid, OWNER):
        result = service.finish(pid, OWNER, job)
        assert service.finish(pid, OWNER, job) == result
    assert journal.finish(pid, OWNER, token)['changed']
    return journal, token, result


def texts(make, pid):
    return [row['text_content'] for row in make().domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)]


def test_novel_replacement_is_hidden_until_single_publication_and_pointer_undo():
    make, raw, objects, pid = setup()
    with actor():
        original = texts(make, pid)
        generation = raw.get(('projects', pid)).data['generation']
        progress = make().begin(pid, OWNER, parsed(350), operation='novel-one', replace_main=True)
        assert progress['total_batches'] > 1
        assert raw.get(('projects', pid)).data[FORMAT_FIELD] == 1
        while not progress['ready']:
            assert texts(make, pid) == original
            index = progress['next_batch']
            progress = make().step(pid, OWNER, progress['job_id'], index)
            assert make().step(pid, OWNER, progress['job_id'], index) == progress
            assert raw.get(('projects', pid)).data['generation'] == generation
        journal, token, result = publish(make, pid, progress['job_id'])
        assert len(texts(make, pid)) == 350
        action = raw.get(('hosted_actions', OWNER, 'tokens', token)).data
        assert len(action['records']) == 1 and action['records'][0]['path'][2] == 'tabs'
        assert journal.restore(pid, OWNER, token, 'undo')['changed']
        assert texts(make, pid) == original
        with bind_action(token, pid, OWNER), pytest.raises(DocumentConflict, match='SUPERSEDED'):
            make().finish(pid, OWNER, progress['job_id'])
        assert journal.restore(pid, OWNER, token, 'redo')['changed']
        assert len(texts(make, pid)) == 350
        assert result['chapters'] == 1


def test_append_and_large_chapter_delete_keep_old_tab_and_undo_without_prose_reads(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        original = texts(make, pid)
        job = stage(make, pid, parsed(220), 'large-append')
        journal, token, result = publish(make, pid, job)
        assert len(texts(make, pid)) == len(original)+220
        chapter = next(row for row in make().domain.list('chapter', {'project_id': pid}, user_id=OWNER)
            if row['tab_id'] == result['first_document_id'])
        delete_token = journal.begin(pid, OWNER)['token']
        def unread(*args, **kwargs):
            raise AssertionError('Chapter deletion must not read prose objects')
        with monkeypatch.context() as patch:
            patch.setattr(objects, 'read_chunks', unread)
            with bind_action(delete_token, pid, OWNER):
                delete_chapter_revision(make().domain, pid, chapter['id'], OWNER)
        assert journal.finish(pid, OWNER, delete_token)['changed']
        assert texts(make, pid) == original
        assert len(raw.query_collection(('projects', pid, 'blocks'))) == len(original)+220
        assert len(raw.get(('hosted_actions', OWNER, 'tokens', delete_token)).data['records']) == 1
        journal.restore(pid, OWNER, delete_token, 'undo')
        assert len(texts(make, pid)) == len(original)+220
        # Consecutive earlier append action remains undoable after delete Undo.
        journal.restore(pid, OWNER, token, 'undo')
        assert texts(make, pid) == original


def test_interruption_and_project_conflict_never_publish_staged_rows(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        progress = make().begin(pid, OWNER, parsed(160), operation='interrupted', replace_main=True)
        service = make()
        original = texts(make, pid)
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic upload interruption')
        with monkeypatch.context() as patch:
            patch.setattr(service.domain, '_publish_hybrid', fail)
            with pytest.raises(RuntimeError, match='interruption'):
                service.step(pid, OWNER, progress['job_id'], 0)
        assert texts(make, pid) == original
        assert make().status(pid, OWNER, progress['job_id'])['next_batch'] == 0
        progress = make().step(pid, OWNER, progress['job_id'], 0)
        root = raw.get(('projects', pid))
        raw.run_transaction(lambda tx: tx.set(root.path, {**root.data, 'generation': root.data['generation']+1}))
        with pytest.raises(DocumentConflict, match='GENERATION'):
            make().step(pid, OWNER, progress['job_id'], progress['next_batch'])
        assert texts(make, pid) == original


def test_confirmation_images_and_action_requirement_fail_before_publication():
    make, raw, objects, pid = setup()
    with actor():
        job = stage(make, pid, parsed(2), 'first', replace_main=True)
        with pytest.raises(HostedValidationError, match='UNDO_ACTION_REQUIRED'):
            make().finish(pid, OWNER, job)
        publish(make, pid, job)
        with pytest.raises(DocumentConflict, match='Confirm replacement'):
            make().begin(pid, OWNER, parsed(160), operation='unconfirmed', replace_main=True)
        before = deepcopy(raw.get(('projects', pid)))
        image_tabs = [{'chapters': [{'blocks': [{'kind': 'image', 'image_path': '/api/assets/img.png'}]}]}]
        with pytest.raises(HostedValidationError, match='IMAGE_STAGING'):
            make().begin(pid, OWNER, image_tabs, operation='images', confirmed=True, replace_main=True)
        assert raw.get(('projects', pid)) == before


def test_revocation_and_erasure_between_steps_remain_fenced():
    make, raw, objects, pid = setup()
    with actor():
        progress = make().begin(pid, OWNER, parsed(160), operation='revocation', replace_main=True)
        member_path = ('projects', pid, 'members', OWNER)
        member = raw.get(member_path)
        raw.run_transaction(lambda tx: tx.delete(member_path))
        with pytest.raises(ProjectUnavailable):
            make().step(pid, OWNER, progress['job_id'], 0)
        raw.run_transaction(lambda tx: tx.create(member_path, member.data))
        raw.run_transaction(lambda tx: tx.set(('users', OWNER), {**tx.get(('users', OWNER)).data, 'status': 'erasure_pending'}))
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            make().step(pid, OWNER, progress['job_id'], 0)
        assert make().retained.get(('projects', pid, 'manuscript_import_jobs', progress['job_id'])).data['next_batch'] == 0


def test_one_hundred_thousand_word_manuscript_keeps_a_single_pointer_journal():
    make, raw, objects, pid = setup()
    text = '\n'.join(' '.join(['synthetic']*200) for _ in range(500))
    assert len(text.split()) == 100_000
    tabs = parse_document_bytes(text.encode(), 'Novel.txt')[0]
    with actor():
        original = texts(make, pid)
        job = stage(make, pid, tabs, 'hundred-thousand-words', replace_main=True)
        journal, token, result = publish(make, pid, job)
        assert sum(len(value.split()) for value in texts(make, pid)) == 100_000
        entry = raw.get(('hosted_actions', OWNER, 'tokens', token)).data
        assert len(entry['records']) == 1
        journal.restore(pid, OWNER, token, 'undo')
        assert texts(make, pid) == original


def test_cancelled_job_cannot_resume_or_publish_and_late_cancel_requires_undo():
    make, raw, objects, pid = setup()
    with actor():
        original = texts(make, pid)
        progress = make().begin(pid, OWNER, parsed(), operation='cancel-before-publication', replace_main=True)
        make().step(pid, OWNER, progress['job_id'], 0)
        assert make().cancel(pid, OWNER, progress['job_id']) == make().cancel(pid, OWNER, progress['job_id'])
        with pytest.raises(DocumentConflict, match='CANCELLED'):
            make().step(pid, OWNER, progress['job_id'], 1)
        journal = ActionJournal(make().domain.documents)
        token = journal.begin(pid, OWNER)['token']
        with bind_action(token, pid, OWNER), pytest.raises(DocumentConflict, match='CANCELLED'):
            make().finish(pid, OWNER, progress['job_id'])
        assert texts(make, pid) == original
        job = stage(make, pid, parsed(), 'cancel-too-late', replace_main=True)
        publish(make, pid, job)
        with pytest.raises(DocumentConflict, match='USE_UNDO'):
            make().cancel(pid, OWNER, job)
        assert len(texts(make, pid)) == 150


@pytest.mark.parametrize('corruption', ['head', 'orphan'])
def test_legacy_upgrade_repair_cases_leave_root_content_and_objects_unchanged(corruption):
    make, raw, objects, pid = setup()
    with actor():
        if corruption == 'head':
            tab = raw.query_collection(('projects', pid, 'tabs'))[0]
            raw.run_transaction(lambda tx: tx.set(tab.path, {**tab.data, HEAD_FIELD: 'unexpected'}))
        else:
            block = raw.query_collection(('projects', pid, 'blocks'))[0]
            raw.run_transaction(lambda tx: tx.set(block.path, {**block.data, 'chapter_id': 'chapter_missing'}))
        root = deepcopy(raw.get(('projects', pid)))
        blocks = deepcopy(raw.query_collection(('projects', pid, 'blocks')))
        object_names = [row.name for row in objects.list_prefix(f'projects/{pid}/')]
        with pytest.raises(HostedValidationError, match='REPAIR_REQUIRED'):
            make().begin(pid, OWNER, parsed(), operation='needs-repair', replace_main=True, confirmed=True)
        assert raw.get(('projects', pid)) == root
        assert raw.query_collection(('projects', pid, 'blocks')) == blocks
        assert [row.name for row in objects.list_prefix(f'projects/{pid}/')] == object_names


def test_cancelled_begin_retry_does_not_upload_more_objects(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        tabs = parsed()
        progress = service.begin(pid, OWNER, tabs, operation='cancelled-begin', replace_main=True)
        service.cancel(pid, OWNER, progress['job_id'])
        def unexpected(*args, **kwargs):
            raise AssertionError('Cancelled retry must not upload')
        monkeypatch.setattr(service.domain, '_publish_hybrid', unexpected)
        with pytest.raises(DocumentConflict, match='CANCELLED'):
            service.begin(pid, OWNER, tabs, operation='cancelled-begin', replace_main=True)


@pytest.mark.parametrize('corruption', ['foreign_membership', 'missing_entry', 'extra_field', 'revision', 'parent', 'identity'])
def test_correctly_hashed_malformed_plan_cannot_gain_retained_write_authority(monkeypatch, corruption):
    import gzip
    import json
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        progress = service.begin(pid, OWNER, parsed(3), operation='malformed-'+corruption, replace_main=True)
        job_path = service._path(pid, progress['job_id'])
        data = raw.get(job_path).data
        pointer = data['plans'][0]
        encoded = b''.join(objects.read_chunks(pointer['object_name'], max_bytes=4*1024*1024))
        entries = json.loads(gzip.decompress(encoded))
        foreign = 'f'*32
        if corruption == 'foreign_membership':
            entries[0]['path'] = ['projects', foreign, 'members', OWNER]
        elif corruption == 'missing_entry':
            entries.pop()
        elif corruption == 'extra_field':
            entries[0]['data']['role'] = 'owner'
        elif corruption == 'revision':
            entries[0]['data']['_bm_manuscript_revision'] = 'other'
        elif corruption == 'parent':
            entries[1]['data']['chapter_id'] = 'chapter_foreign'
        else:
            entries[0]['ids']['chapter_id'] = 'chapter_foreign'
        forged = service.domain._publish_hybrid('manuscript_block',
            {'project_id': pid, 'block_id': f'b_{progress["job_id"]}_plan_0'}, gzip.compress(json.dumps(entries).encode(), mtime=0),
            user_id=OWNER, operation='forged-plan-fixture')
        raw.run_transaction(lambda tx: tx.set(job_path, {**data, 'plans': [forged]}))
        before = deepcopy(raw.query_collection(('projects', pid, 'blocks')))
        def unexpected(*args, **kwargs):
            raise AssertionError('Malformed plan must fail before payload upload')
        monkeypatch.setattr(service.domain, '_publish_hybrid', unexpected)
        with pytest.raises(HostedValidationError, match='PLAN_SCHEMA_INVALID'):
            service.step(pid, OWNER, progress['job_id'], 0)
        assert raw.get(('projects', foreign, 'members', OWNER)) is None
        assert raw.query_collection(('projects', pid, 'blocks')) == before
        assert raw.get(job_path).data['next_batch'] == 0


def test_resume_requires_matching_actor_even_before_account_freeze(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        progress = make().begin(pid, OWNER, parsed(), operation='actor-bound-resume', replace_main=True)
    def unexpected(*args, **kwargs):
        raise AssertionError('Missing actor must fail before object reads')
    monkeypatch.setattr(objects, 'read_chunks', unexpected)
    with pytest.raises(HostedValidationError, match='AUTHENTICATED_ACTOR_REQUIRED'):
        make().step(pid, OWNER, progress['job_id'], 0)
    raw.run_transaction(lambda tx: tx.set(('users', OWNER), {**tx.get(('users', OWNER)).data, 'status': 'erasure_pending'}))
    with pytest.raises(HostedValidationError, match='AUTHENTICATED_ACTOR_REQUIRED'):
        make().step(pid, OWNER, progress['job_id'], 0)
    with actor(), pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        make().step(pid, OWNER, progress['job_id'], 0)
    assert raw.get(make()._path(pid, progress['job_id'])).data['next_batch'] == 0


def test_lock_only_actor_and_owner_freeze_block_resume_before_object_read(monkeypatch):
    from hosted.runtime_services import ensure_hosted_user
    editor = 'usr_'+'e'*32
    make, raw, objects, pid = setup()
    with actor():
        ensure_hosted_user(make().retained, user_id=editor, display_name='Synthetic editor')
        raw.run_transaction(lambda tx: tx.create(('projects', pid, 'members', editor), {'user_id': editor, 'role': 'editor'}))
    with actor(editor):
        progress = make().begin(pid, editor, parsed(), operation='owner-freeze', replace_main=True)
        raw.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'request_id': 'synthetic-lock'}))
        def unexpected(*args, **kwargs):
            raise AssertionError('Owner lock must fail before reading source bytes')
        monkeypatch.setattr(objects, 'read_chunks', unexpected)
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            make().step(pid, editor, progress['job_id'], 0)
        assert raw.get(make()._path(pid, progress['job_id'])).data['next_batch'] == 0


@pytest.mark.parametrize('corruption', ['project', 'batch', 'generation_type', 'extra'])
def test_plan_object_scope_is_checked_before_any_object_read(monkeypatch, corruption):
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        progress = service.begin(pid, OWNER, parsed(), operation='pointer-'+corruption, replace_main=True)
        path = service._path(pid, progress['job_id'])
        data = deepcopy(dict(raw.get(path).data))
        pointer = data['plans'][0]
        if corruption == 'project':
            pointer['object_name'] = pointer['object_name'].replace(pid, 'f'*32)
        elif corruption == 'batch':
            pointer['object_name'] = pointer['object_name'].replace('_plan_0', '_plan_1')
        elif corruption == 'generation_type':
            pointer['object_generation'] = True
        else:
            pointer['extra'] = 'not accepted'
        raw.run_transaction(lambda tx: tx.set(path, data))
        def unexpected(*args, **kwargs):
            raise AssertionError('Invalid pointer must fail before storage read')
        monkeypatch.setattr(objects, 'read_chunks', unexpected)
        with pytest.raises(HostedValidationError, match='PLAN_SCOPE_INVALID'):
            service.step(pid, OWNER, progress['job_id'], 0)


def test_job_plan_change_during_upload_cannot_advance_cursor(monkeypatch):
    from threading import Lock
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        progress = service.begin(pid, OWNER, parsed(3), operation='plan-race', replace_main=True)
        path = service._path(pid, progress['job_id'])
        original = service.domain._publish_hybrid
        lock, changed = Lock(), [False]
        def mutate(*args, **kwargs):
            with lock:
                if not changed[0]:
                    raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, 'digest': 'changed'}))
                    changed[0] = True
            return original(*args, **kwargs)
        monkeypatch.setattr(service.domain, '_publish_hybrid', mutate)
        with pytest.raises(DocumentConflict, match='PLAN_CHANGED'):
            service.step(pid, OWNER, progress['job_id'], 0)
        assert raw.get(path).data['next_batch'] == 0


def test_frozen_begin_rejects_before_blank_manuscript_object_read(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        raw.run_transaction(lambda tx: tx.set(('users', OWNER), {**tx.get(('users', OWNER)).data, 'status': 'erasure_pending'}))
        def unexpected(*args, **kwargs):
            raise AssertionError('Frozen begin must not inspect private prose')
        monkeypatch.setattr(objects, 'read_chunks', unexpected)
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            make().begin(pid, OWNER, parsed(), operation='frozen-before-blank-check', replace_main=True)


def test_empty_rollback_preserves_resume_job_and_existing_structural_head():
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        journal = ActionJournal(service.domain.documents)
        first = journal.begin(pid, OWNER)['token']
        with bind_action(first, pid, OWNER):
            progress = service.begin(pid, OWNER, parsed(3), operation='resume-empty-rollback', replace_main=True)
            service.step(pid, OWNER, progress['job_id'], 0)
        root = deepcopy(raw.get(('projects', pid)))
        assert journal.restore(pid, OWNER, first, 'rollback') == {'ok': True, 'changed': False}
        assert raw.get(('projects', pid)) == root
        assert service.status(pid, OWNER, progress['job_id'])['ready']
        second_journal, second, result = publish(make, pid, progress['job_id'])
        assert result['undo_token'] == second
        published_root = deepcopy(raw.get(('projects', pid)))
        assert journal.restore(pid, OWNER, first, 'rollback')['changed'] is False
        assert raw.get(('projects', pid)) == published_root
        second_journal.restore(pid, OWNER, second, 'undo')
        with pytest.raises(DocumentConflict, match='SUPERSEDED'):
            service.status(pid, OWNER, progress['job_id'])


def test_lost_finish_response_can_recover_original_undo_token_without_republishing():
    make, raw, objects, pid = setup()
    with actor():
        service = make()
        job = stage(make, pid, parsed(3), 'lost-finish', replace_main=True)
        journal = ActionJournal(service.domain.documents)
        original = journal.begin(pid, OWNER)['token']
        with bind_action(original, pid, OWNER):
            result = service.finish(pid, OWNER, job)
        # Simulate page reload after publication but before finishStructuralUndo.
        recovered = make().status(pid, OWNER, job)
        assert recovered['completed'] and recovered['result'] == result
        assert recovered['result']['undo_token'] == original
        generation = raw.get(('projects', pid)).data['generation']
        replacement_token = journal.begin(pid, OWNER)['token']
        with bind_action(replacement_token, pid, OWNER):
            assert make().finish(pid, OWNER, job) == result
        assert journal.finish(pid, OWNER, original)['changed']
        assert not journal.finish(pid, OWNER, replacement_token)['changed']
        assert raw.get(('projects', pid)).data['generation'] == generation
        journal.restore(pid, OWNER, original, 'undo')
        assert len(texts(make, pid)) == 1


def test_private_root_marker_refuses_plaintext_import_before_object_io(monkeypatch):
    make, raw, objects, pid = setup()
    with actor():
        raw.run_transaction(lambda tx: tx.set(('projects', pid), {**tx.get(('projects', pid)).data, '_bm_private_title': {'sealed': 'fixture'}}))
        root = deepcopy(raw.get(('projects', pid)))
        def unexpected(*args, **kwargs):
            raise AssertionError('Private root must not cause plaintext object I/O')
        monkeypatch.setattr(objects, 'read_chunks', unexpected)
        with pytest.raises(HostedValidationError, match='PRIVATE_TITLE_CODEC_REQUIRED'):
            make().begin(pid, OWNER, parsed(), operation='private-root', replace_main=True)
        assert raw.get(('projects', pid)) == root


def test_completion_remains_truthful_when_the_original_undo_token_is_gone():
    make, raw, objects, pid = setup()
    with actor():
        job = stage(make, pid, parsed(3), 'completion-without-undo', replace_main=True)
        journal, token, result = publish(make, pid, job)
        assert result['undo_available']
        raw.run_transaction(lambda tx: tx.delete(('hosted_actions', OWNER, 'tokens', token)))
        root = deepcopy(raw.get(('projects', pid)))
        status = make().status(pid, OWNER, job)
        assert status['completed'] and not status['result']['undo_available']
        assert status['result']['undo_token'] == token
        assert raw.get(('projects', pid)) == root
