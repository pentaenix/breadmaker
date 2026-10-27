"""History bookkeeping cannot invalidate content or hide a changed recovery state."""
import gzip
import json
import pytest
from fastapi import HTTPException
from hosted.runtime_services import _DEV_USER_ID as OWNER
from hosted.firestore.contracts import DocumentConflict
from tests.test_v10364 import _replicas, _replace
from tests.test_v10552 import request


def setup(monkeypatch):
    import hosted.http_api as http
    one,make,docs,pid,cid=_replicas(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    return http,one,make,docs,pid,cid


def test_unchanged_history_skips_snapshot_bodies_across_replicas_and_preserves_save_cas(monkeypatch):
    http,one,make,docs,pid,cid=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        generation=one.projects.get(pid)['generation']
        tag=json.loads(http.hosted_book(request(pid,'/api/book',query='manifest=true')).body)['read_tag']
        first=http._commit_history_snapshot(pid,'First')
        assert one.projects.get(pid)['generation']==generation
        monkeypatch.setattr(http,'_project_snapshot',lambda *a,**kw:pytest.fail('No-op reread snapshot bodies'))
        replica=make();monkeypatch.setattr(http,'runtime',replica)
        with replica.context.bind(pid,user_id=OWNER):
            repeat=http._commit_history_snapshot(pid,'Unchanged')
            assert repeat['revision_id']==first['revision_id'] and repeat['unchanged']
            assert len(replica.history.list_revisions())==1
            assert http.hosted_book(request(pid,'/api/book',query='manifest=true',tag=tag)).status_code==304
            _replace(replica,pid,cid,[{'text_content':'Synthetic acknowledged edit'}],'op_history_cas',generation)
            with pytest.raises(DocumentConflict,match='HOSTED_GENERATION_CONFLICT'):
                _replace(replica,pid,cid,[{'text_content':'stale'}],'op_stale_cas',generation)


def test_all_canonical_changes_bookmark_and_configuration_change_capture_state(monkeypatch):
    http,one,_,_,pid,cid=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        revisions=[http._commit_history_snapshot(pid,'First')['revision_id']]
        for entity,payload in [('canvas',{'name':'Synthetic canvas'}),('task',{'title':'Synthetic task'})]:
            one.projects.domain.create(entity,{'project_id':pid},payload,user_id=OWNER)
            result=http._commit_history_snapshot(pid,entity)
            assert not result.get('unchanged');revisions.append(result['revision_id'])
        generation=one.projects.get(pid)['generation']
        one.projects.put_member_state(pid,{'chapter_id':cid,'block_id':'synthetic_bookmark'})
        bookmarked=http._commit_history_snapshot(pid,'Bookmark');revisions.append(bookmarked['revision_id'])
        _,body=one.history.read_revision(bookmarked['revision_id'])
        assert json.loads(gzip.decompress(body))['snapshot']['bookmark']['chapter_id']==cid
        original=http._canvas_config_raw
        monkeypatch.setattr(http,'_canvas_config_raw',lambda:{**original(),'node_typography':{'content_font_size_pt':17}})
        revised=http._commit_history_snapshot(pid,'Configuration');revisions.append(revised['revision_id'])
        assert len(set(revisions))==5 and one.projects.get(pid)['generation']==generation
        assert http._commit_history_snapshot(pid,'Again')['unchanged']


def test_snapshot_to_publish_race_cannot_label_old_content_with_new_write_generation(monkeypatch):
    http,one,_,_,pid,_=setup(monkeypatch)
    native=http._project_snapshot
    def racing(*a,**kw):
        snapshot=native(*a,**kw);one.projects.rename(pid,'Concurrent change');return snapshot
    monkeypatch.setattr(http,'_project_snapshot',racing)
    with one.context.bind(pid,user_id=OWNER):
        with pytest.raises(HTTPException) as error:http._commit_history_snapshot(pid,'Race')
        assert error.value.status_code==409
        assert one.history.list_revisions()==[]


def test_interrupted_publication_and_concurrent_write_preserve_previous_recovery_point(monkeypatch):
    http,one,_,_,pid,cid=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        first=http._commit_history_snapshot(pid,'First')
        _replace(one,pid,cid,[{'text_content':'Synthetic new body'}],'op_new_body')
        publisher=one.history.publisher.immutable;native=publisher.publish_bytes
        def interrupt(*a,**kw):
            native(*a,**kw);raise RuntimeError('Synthetic interrupted pointer publication')
        monkeypatch.setattr(publisher,'publish_bytes',interrupt)
        with pytest.raises(HTTPException):http._commit_history_snapshot(pid,'Interrupted')
        assert [row['revision_id'] for row in one.history.list_revisions()]==[first['revision_id']]
        monkeypatch.setattr(publisher,'publish_bytes',native)
        latest=http._commit_history_snapshot(pid,'Retry')
        _,body=one.history.read_revision(latest['revision_id'])
        assert any(row.get('text_content')=='Synthetic new body' for row in json.loads(gzip.decompress(body))['snapshot']['blocks'])
        assert one.history.read_revision(first['revision_id'])[1]


def test_deleted_head_and_revocation_cannot_authorize_noop_history(monkeypatch):
    http,one,_,docs,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        first=http._commit_history_snapshot(pid,'First')
        docs.run_transaction(lambda tx:tx.delete(('projects',pid,'revisions',first['revision_id'])))
        replacement=http._commit_history_snapshot(pid,'Deleted recovery point')
        assert replacement['revision_id']!=first['revision_id'] and not replacement.get('unchanged')
        docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)))
        with pytest.raises(HTTPException) as error:http._commit_history_snapshot(pid,'Revoked')
        assert error.value.status_code in {403,404}


def test_concurrent_equal_captures_publish_only_one_recovery_pointer(monkeypatch):
    http,one,_,_,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        publisher=one.history.publisher.immutable;native=publisher.publish_bytes;other=[]
        def interleave(*a,**kw):
            result=native(*a,**kw)
            monkeypatch.setattr(publisher,'publish_bytes',native)
            other.append(http._commit_history_snapshot(pid,'Concurrent capture'))
            return result
        monkeypatch.setattr(publisher,'publish_bytes',interleave)
        result=http._commit_history_snapshot(pid,'Outer capture')
        assert result['unchanged'] and result['revision_id']==other[0]['revision_id']
        assert len(one.history.list_revisions())==1
        assert one.history.read_revision(result['revision_id'])[1]


def test_membership_revocation_between_object_and_pointer_commit_rejects_publication(monkeypatch):
    http,one,_,docs,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        publisher=one.history.publisher.immutable;native=publisher.publish_bytes
        def revoke(*a,**kw):
            result=native(*a,**kw)
            docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)))
            return result
        monkeypatch.setattr(publisher,'publish_bytes',revoke)
        with pytest.raises(HTTPException) as error:http._commit_history_snapshot(pid,'Revoked race')
        assert error.value.status_code in {403,404}
        assert docs.query_collection(('projects',pid,'revisions'))==[]


def test_structural_write_between_object_and_pointer_commit_keeps_previous_history(monkeypatch):
    http,one,_,_,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        first=http._commit_history_snapshot(pid,'First')
        one.projects.rename(pid,'Changed before capture')
        publisher=one.history.publisher.immutable;native=publisher.publish_bytes
        def change(*a,**kw):
            result=native(*a,**kw);one.projects.rename(pid,'Changed during publication');return result
        monkeypatch.setattr(publisher,'publish_bytes',change)
        with pytest.raises(HTTPException) as error:http._commit_history_snapshot(pid,'Structural race')
        assert error.value.status_code==409
        assert one.history.list_revisions()[0]['revision_id']==first['revision_id']


def test_missing_recovery_object_fails_closed_instead_of_reporting_successful_noop(monkeypatch):
    http,one,_,_,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        first=http._commit_history_snapshot(pid,'First')
        one.history.objects.delete(first['object_name'],if_generation_match=first['object_generation'])
        with pytest.raises(HTTPException):http._commit_history_snapshot(pid,'Missing object')


def test_project_deletion_during_publication_cannot_commit_history(monkeypatch):
    http,one,_,docs,pid,_=setup(monkeypatch)
    with one.context.bind(pid,user_id=OWNER):
        publisher=one.history.publisher.immutable;native=publisher.publish_bytes
        def delete_project(*a,**kw):
            result=native(*a,**kw)
            def remove(tx):
                path=('projects',pid);row=dict(tx.get(path).data);row['status']='deleted';tx.set(path,row)
            docs.run_transaction(remove)
            return result
        monkeypatch.setattr(publisher,'publish_bytes',delete_project)
        with pytest.raises(HTTPException) as error:http._commit_history_snapshot(pid,'Deletion race')
        assert error.value.status_code==404
        assert docs.query_collection(('projects',pid,'revisions'))==[]
