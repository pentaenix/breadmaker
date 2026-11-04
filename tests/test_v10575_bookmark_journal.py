"""Member bookmarks are undoable product state, never membership authority."""
from copy import deepcopy

import pytest

from hosted.action_journal import ActionJournal, bind_action
from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas


def change(instance, journal, pid, payload):
    token = journal.begin(pid, OWNER)['token']
    with bind_action(token, pid, OWNER):
        if payload is None:
            instance.projects.delete_member_state(pid)
        else:
            instance.projects.put_member_state(pid, payload)
    journal.finish(pid, OWNER, token)
    return token


def test_bookmark_create_move_delete_and_history_across_replicas(monkeypatch):
    one, make, docs, pid, cid = _replicas(monkeypatch)
    journal = ActionJournal(docs)
    first = change(one, journal, pid, {'target_type':'chapter', 'chapter_id':cid})
    original = make().projects.member_state(pid)
    second = change(make(), journal, pid, {'target_type':'range', 'start_block_id':'b_synthetic'})
    moved = make().projects.member_state(pid)
    third = change(make(), journal, pid, None)
    assert make().projects.member_state(pid) is None
    for token, expected in ((third, moved), (second, original), (first, None)):
        ActionJournal(make().projects.documents).restore(pid, OWNER, token, 'undo')
        actual = make().projects.member_state(pid)
        assert (actual or {}).get('target_type') == (expected or {}).get('target_type')
        if expected:
            assert actual['generation'] > expected['generation']
    for token in (first, second, third):
        journal.restore(pid, OWNER, token, 'redo')
    assert make().projects.member_state(pid) is None


@pytest.mark.parametrize('suffix', [(), ('state','other'),
    ('ui_state','bookmark'), ('state','bookmark','sessions','child')])
def test_bookmark_exception_does_not_allow_member_authority_or_siblings(monkeypatch, suffix):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    token = ActionJournal(docs).begin(pid, OWNER)['token']
    path = ('projects',pid,'members',OWNER) + suffix
    before = docs.get(path)
    with bind_action(token,pid,OWNER), pytest.raises(HostedValidationError,match='UNSUPPORTED'):
        one.projects.documents.run_transaction(lambda tx:tx.set(path,{'role':'owner'}))
    assert docs.get(path) == before


def test_other_members_bookmark_and_restore_of_authority_are_denied(monkeypatch):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    journal = ActionJournal(docs)
    token = journal.begin(pid, OWNER)['token']
    path = ('projects',pid,'members','usr_'+'f'*32,'state','bookmark')
    with bind_action(token,pid,OWNER), pytest.raises(HostedValidationError,match='UNSUPPORTED'):
        one.projects.documents.run_transaction(lambda tx:tx.set(path,{'target_type':'chapter'}))
    # Even a malformed server journal must never restore permission records.
    member = ('projects',pid,'members',OWNER)
    before = deepcopy(docs.get(member).data)
    def corrupt(tx):
        key = ('hosted_actions',OWNER,'tokens',token)
        row = dict(tx.get(key).data)
        row['records'] = [{'path':list(member),'before':{'role':'owner'},'after':before}]
        tx.set(key,row)
    docs.run_transaction(corrupt)
    with pytest.raises(HostedValidationError,match='UNSUPPORTED'):
        journal.restore(pid,OWNER,token,'rollback')
    assert docs.get(member).data == before


def test_bookmark_conflicts_revocation_and_failed_action_rollback(monkeypatch):
    one, _, docs, pid, cid = _replicas(monkeypatch)
    journal = ActionJournal(docs)
    token = journal.begin(pid,OWNER)['token']
    with bind_action(token,pid,OWNER):
        one.projects.put_member_state(pid,{'target_type':'chapter','chapter_id':cid})
    journal.restore(pid,OWNER,token,'rollback')
    assert one.projects.member_state(pid) is None
    token = change(one,journal,pid,{'target_type':'chapter','chapter_id':cid})
    one.projects.put_member_state(pid,{'chapter_id':'different'})
    with pytest.raises(DocumentConflict,match='RECORD_CHANGED'):
        journal.restore(pid,OWNER,token,'undo')
    docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)))
    with pytest.raises(ProjectUnavailable):
        journal.restore(pid,OWNER,token,'undo')
