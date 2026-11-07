"""Existing-project imports publish once, retain authority and remain undoable."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend.danger_challenges import _new_danger_challenge, _consume_danger_challenge
from hosted.action_journal import ActionJournal, bind_action
from hosted.document_io import parse_document_bytes
from hosted.document_mutations import install_existing_document
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from tests.test_v10594 import actor, fenced_replicas
from tests.test_v10353 import OWNER


def service_and_project():
    make, raw, objects, pid = fenced_replicas()
    # The production runtime composes the journal around the lifecycle fence.
    from hosted.action_journal import ActionDocumentStore
    service = make()
    service.domain.documents = ActionDocumentStore(service.domain.documents)
    service.domain.entities.documents = service.domain.documents
    return service, raw, objects, pid


def parsed(text):
    return parse_document_bytes(text.encode(), 'Synthetic.txt')[0]


def install(service, pid, text, **options):
    return install_existing_document(service.domain, pid, OWNER, parsed(text), **options)


def test_project_codes_have_five_characters_but_global_erasure_keeps_ten():
    for action, length in [('delete_project', 5), ('delete_all_data', 10), ('delete_external_backups', 10)]:
        challenge = _new_danger_challenge(action, actor_id='synthetic', target_id='project')
        assert len(challenge['code']) == length
        _consume_danger_challenge(challenge['challenge_id'], challenge['code'], action,
                                  actor_id='synthetic', target_id='project')
        with pytest.raises(HTTPException):
            _consume_danger_challenge(challenge['challenge_id'], challenge['code'], action,
                                      actor_id='synthetic', target_id='project')


def test_replace_and_append_commit_atomically_and_undo_redo_preserve_other_tabs():
    service, raw, objects, pid = service_and_project()
    with actor():
        journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        with bind_action(token, pid, OWNER):
            result = install(service, pid, 'Original prose', operation='first-replace', replace_main=True)
        assert journal.finish(pid, OWNER, token)['changed']
        original = raw.query_collection(('projects', pid, 'blocks'))[0]
        with pytest.raises(DocumentConflict, match='Confirm replacement'):
            install(service, pid, 'Accidental replacement', operation='not-confirmed', replace_main=True)
        appended = install(service, pid, 'Research notes', operation='append-notes', after_document_id=result['first_document_id'])
        token = journal.begin(pid, OWNER)['token']
        with bind_action(token, pid, OWNER):
            replacement = install(service, pid, 'Replacement prose', operation='confirmed-replace', replace_main=True, confirmed=True)
            assert install(service, pid, 'Replacement prose', operation='confirmed-replace', replace_main=True, confirmed=True) == replacement
        assert journal.finish(pid, OWNER, token)['changed']
        blocks = service.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)
        assert sorted(row['text_content'] for row in blocks) == ['Replacement prose', 'Research notes']
        journal.restore(pid, OWNER, token, 'undo')
        blocks = service.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)
        assert sorted(row['text_content'] for row in blocks) == ['Original prose', 'Research notes']
        assert raw.get(original.path) is not None
        journal.restore(pid, OWNER, token, 'redo')
        blocks = service.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)
        assert sorted(row['text_content'] for row in blocks) == ['Replacement prose', 'Research notes']
        assert raw.get(('projects', pid, 'tabs', appended['first_document_id'])) is not None


def test_oversized_or_failed_staging_never_changes_existing_content(monkeypatch):
    service, raw, objects, pid = service_and_project()
    with actor():
        before = deepcopy(raw.query_collection(('projects', pid, 'blocks')))
        root = deepcopy(raw.get(('projects', pid)).data)
        with pytest.raises(HostedValidationError, match='too large'):
            install(service, pid, '\n'.join(str(i) for i in range(100)), operation='too-large', replace_main=True)
        def fail(*args, **kwargs):raise RuntimeError('synthetic staging interruption')
        monkeypatch.setattr(service.domain, '_publish_hybrid', fail)
        with pytest.raises(RuntimeError, match='staging interruption'):
            install(service, pid, 'Never published', operation='interrupted', replace_main=True)
        assert raw.query_collection(('projects', pid, 'blocks')) == before
        assert raw.get(('projects', pid)).data == root


def test_chapter_delete_is_one_commit_and_undo_restores_content_without_object_reads(monkeypatch):
    from hosted.chapter_deletion import delete_chapter_atomic
    service, raw, objects, pid = service_and_project()
    with actor():
        install(service, pid, 'Chapter prose\nSecond paragraph', operation='seed-delete', replace_main=True)
        cid = raw.query_collection(('projects', pid, 'chapters'))[0].path[-1]
        journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        generation = raw.get(('projects', pid)).data['generation']
        def unread(*args, **kwargs):raise AssertionError('Deletion must not download manuscript objects')
        with monkeypatch.context() as patch:
            patch.setattr(objects, 'read_chunks', unread)
            with bind_action(token, pid, OWNER):
                delete_chapter_atomic(service.domain, pid, cid, OWNER)
        assert raw.get(('projects', pid)).data['generation'] == generation + 1
        assert raw.query_collection(('projects', pid, 'blocks')) == []
        assert journal.finish(pid, OWNER, token)['changed']
        journal.restore(pid, OWNER, token, 'undo')
        assert sorted(row['text_content'] for row in service.domain.list('manuscript_block', {'project_id': pid}, user_id=OWNER)) == ['Chapter prose', 'Second paragraph']


def test_append_after_tab_with_position_gaps_and_replace_removes_only_main_scenes():
    service, raw, objects, pid = service_and_project()
    with actor():
        main = raw.query_collection(('projects', pid, 'tabs'))[0].path[-1]
        third = service.domain.create('document_tab', {'project_id': pid}, {'title': 'C', 'position': 3}, user_id=OWNER)
        fourth = service.domain.create('document_tab', {'project_id': pid}, {'title': 'D', 'position': 4}, user_id=OWNER)
        imported = install(service, pid, 'Appended', operation='after-gap', after_document_id=fourth['id'])
        tabs = sorted(raw.query_collection(('projects', pid, 'tabs')), key=lambda row: row.data['position'])
        assert [row.path[-1] for row in tabs] == [main, third['id'], fourth['id'], imported['first_document_id']]
        old_scene = service.domain.create('scene', {'project_id': pid}, {'title': 'Old main scene', 'tab_id': main}, user_id=OWNER)
        keep_scene = service.domain.create('scene', {'project_id': pid}, {'title': 'Keep other scene', 'tab_id': third['id']}, user_id=OWNER)
        journal = ActionJournal(service.domain.documents)
        token = journal.begin(pid, OWNER)['token']
        with bind_action(token, pid, OWNER):
            install(service, pid, 'New main', operation='replace-scenes', replace_main=True, confirmed=True)
        journal.finish(pid, OWNER, token)
        assert raw.get(('projects', pid, 'scenes', old_scene['id'])) is None
        assert raw.get(('projects', pid, 'scenes', keep_scene['id'])) is not None
        journal.restore(pid, OWNER, token, 'undo')
        assert raw.get(('projects', pid, 'scenes', old_scene['id'])) is not None


def test_readiness_does_not_download_the_whole_manuscript(monkeypatch):
    from hosted.document_io import manuscript_is_default_blank
    service, raw, objects, pid = service_and_project()
    with actor():
        assert manuscript_is_default_blank(service.domain, pid, OWNER)
        install(service, pid, '\n'.join('Paragraph '+str(i) for i in range(30)), operation='readiness-prose', replace_main=True)
        reads = []
        original = objects.read_chunks
        def read(*args, **kwargs):
            reads.append(args[0])
            return original(*args, **kwargs)
        monkeypatch.setattr(objects, 'read_chunks', read)
        assert not manuscript_is_default_blank(service.domain, pid, OWNER)
        assert len(reads) <= 2
