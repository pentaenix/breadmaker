"""Revision read/title/write contract; stale writers must still fail closed."""
import pytest

from hosted.firestore.contracts import DocumentConflict
from tests.test_v10364 import _replicas, _replace
from hosted.runtime_services import _DEV_USER_ID


def test_title_commit_returns_exact_revision_for_following_block_save(monkeypatch):
    one,make,_,pid,cid=_replicas(monkeypatch)
    generation=one.projects.domain.project_generation(pid,_DEV_USER_ID)
    updated=one.projects.domain.update('chapter',{'project_id':pid,'chapter_id':cid},
        {'title':'Renamed'},user_id=_DEV_USER_ID,expected_project_revision=generation,
        operation='test_title_commit')
    assert updated['revision']==str(generation+1)
    assert 'revision' not in one.projects.domain.entities.get('chapter',
        {'project_id':pid,'chapter_id':cid},user_id=_DEV_USER_ID)
    saved=_replace(make(),pid,cid,[{'text_content':'New content'}],'test_following_save',updated['revision'])
    assert saved['revision']==str(generation+2)


def test_stale_title_and_superseded_retry_cannot_authorize_overwrite(monkeypatch):
    one,make,_,pid,cid=_replicas(monkeypatch)
    generation=one.projects.domain.project_generation(pid,_DEV_USER_ID)
    args=('chapter',{'project_id':pid,'chapter_id':cid},{'title':'First'})
    one.projects.domain.update(*args,user_id=_DEV_USER_ID,
        expected_project_revision=generation,operation='test_title_retry')
    _replace(make(),pid,cid,[{'text_content':'Concurrent manuscript'}],'test_concurrent',generation+1)
    with pytest.raises(DocumentConflict,match='HOSTED_GENERATION_CONFLICT'):
        make().projects.domain.update('chapter',{'project_id':pid,'chapter_id':cid},{'title':'Stale'},
            user_id=_DEV_USER_ID,expected_project_revision=generation,operation='test_stale_title')
    with pytest.raises(DocumentConflict,match='HOSTED_REPLAY_SUPERSEDED'):
        make().projects.domain.update(*args,user_id=_DEV_USER_ID,
            expected_project_revision=generation,operation='test_title_retry')
