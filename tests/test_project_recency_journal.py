"""Canonical journal changes maintain existing project modification metadata."""
import pytest
from hosted import action_journal as module
from hosted.action_journal import ActionJournal, bind_action
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas

@pytest.mark.parametrize('direction',['undo','rollback'])
def test_restore_refreshes_timestamp_and_replay_does_not(monkeypatch,direction):
    one,_,docs,pid,_=_replicas(monkeypatch)
    domain=one.projects.domain;journal=ActionJournal(domain.documents)
    token=journal.begin(pid,OWNER,'Synthetic recency')['token']
    with bind_action(token,pid,OWNER):
        domain.create('task',{'project_id':pid},{'title':'Synthetic'},user_id=OWNER)
    if direction=='undo':journal.finish(pid,OWNER,token)
    stamp='2026-11-14T12:00:00+00:00'
    monkeypatch.setattr(module,'_modification_stamp',lambda:stamp)
    journal.restore(pid,OWNER,token,direction)
    assert docs.get(('projects',pid)).data['updated_at']==stamp
    monkeypatch.setattr(module,'_modification_stamp',lambda:'2026-11-14T13:00:00+00:00')
    journal.restore(pid,OWNER,token,direction)
    assert docs.get(('projects',pid)).data['updated_at']==stamp
    if direction=='undo':
        journal.restore(pid,OWNER,token,'redo')
        assert docs.get(('projects',pid)).data['updated_at']=='2026-11-14T13:00:00+00:00'

def test_bound_write_without_root_update_refreshes_timestamp(monkeypatch):
    one,_,docs,pid,_=_replicas(monkeypatch)
    domain=one.projects.domain;journal=ActionJournal(domain.documents)
    token=journal.begin(pid,OWNER,'Synthetic metadata')['token']
    stamp='2026-11-14T12:00:00+00:00'
    monkeypatch.setattr(module,'_modification_stamp',lambda:stamp)
    with bind_action(token,pid,OWNER):
        domain.documents.run_transaction(lambda tx:tx.create(('projects',pid,'tasks','task_synthetic'),
            {'id':'task_synthetic','generation':1}))
    assert docs.get(('projects',pid)).data['updated_at']==stamp
