"""Populate commits, cache authority and background history remain fail-closed."""
import asyncio

import pytest

from hosted.action_journal import ActionJournal, bind_action
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError, ProjectUnavailable
from hosted.population import atomic_population
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas
from tests.test_v10552 import request


def canvas(one, pid):
    return one.projects.domain.create('canvas', {'project_id':pid}, {'name':'Synthetic canvas'}, user_id=OWNER)['id']


def test_populate_is_one_commit_with_cross_replica_replay_and_undo(monkeypatch):
    one,make,docs,pid,_=_replicas(monkeypatch);cid=canvas(one,pid)
    journal=ActionJournal(one.projects.domain.documents)
    token=journal.begin(pid,OWNER,'Synthetic Populate')['token']
    prior=one.projects.get(pid)['generation']
    def populate(domain):
        with atomic_population(domain,pid,OWNER,'op_synthetic_populate') as plan:
            a=plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':'A'})
            b=plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':'B'})
            plan.create('canvas_edge',{'project_id':pid,'canvas_id':cid},
                        {'from_node_id':a['id'],'to_node_id':b['id']})
            assert len(docs.query_collection(('projects',pid,'canvases',cid,'nodes')))==0
    with bind_action(token,pid,OWNER):populate(one.projects.domain)
    assert one.projects.get(pid)['generation']==prior+1
    # Reconstruct the runtime; persisted fingerprints, not a process-local cache,
    # establish retry safety. Recreate the identical plan without an action token.
    with atomic_population(make().projects.domain,pid,OWNER,'op_synthetic_populate') as plan:
        a=plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':'A'})
        b=plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':'B'})
        plan.create('canvas_edge',{'project_id':pid,'canvas_id':cid},{'from_node_id':a['id'],'to_node_id':b['id']})
    assert one.projects.get(pid)['generation']==prior+1
    journal.finish(pid,OWNER,token)
    journal.restore(pid,OWNER,token,'undo')
    assert not docs.query_collection(('projects',pid,'canvases',cid,'nodes'))
    assert not docs.query_collection(('projects',pid,'canvases',cid,'edges'))
    journal.restore(pid,OWNER,token,'redo')
    assert len(docs.query_collection(('projects',pid,'canvases',cid,'nodes')))==2
    assert len(docs.query_collection(('projects',pid,'canvases',cid,'edges')))==1


@pytest.mark.parametrize('failure',['oversize','missing_parent','missing_edge_node','stale'])
def test_invalid_population_never_publishes_partial_records(monkeypatch,failure):
    one,_,docs,pid,_=_replicas(monkeypatch);cid=canvas(one,pid);domain=one.projects.domain
    prior=one.projects.get(pid)['generation']
    with pytest.raises((HostedValidationError,DocumentNotFound,DocumentConflict)):
        with atomic_population(domain,pid,OWNER,'op_failed_populate') as plan:
            node=plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':'No partial publication'})
            if failure=='oversize':
                for index in range(95):plan.create('canvas_node',{'project_id':pid,'canvas_id':cid},{'title':str(index)})
            elif failure=='missing_parent':
                plan.create('map_item',{'project_id':pid,'map_id':'map_missing','line_id':'line_missing'},{'title':'Missing'})
            elif failure=='missing_edge_node':
                plan.create('canvas_edge',{'project_id':pid,'canvas_id':cid},{'from_node_id':node['id'],'to_node_id':'node_missing'})
            else:one.projects.rename(pid,'Concurrent synthetic edit')
    assert not docs.query_collection(('projects',pid,'canvases',cid,'nodes'))
    assert one.projects.get(pid)['generation']==prior+(failure=='stale')


def test_map_population_can_atomically_reference_new_parent_line(monkeypatch):
    one,make,docs,pid,_=_replicas(monkeypatch);domain=one.projects.domain
    mid=domain.create('story_map',{'project_id':pid},{'name':'Synthetic map'},user_id=OWNER)['id']
    with atomic_population(domain,pid,OWNER,'op_map_populate') as plan:
        line=plan.create('map_line',{'project_id':pid,'map_id':mid},{'name':'Synthetic line'})
        item=plan.create('map_item',{'project_id':pid,'map_id':mid,'line_id':line['id']},{'title':'Synthetic stop'})
        assert not docs.query_collection(('projects',pid,'story_maps',mid,'lines'))
    saved=make().projects.domain.get('map_item',{'project_id':pid,'map_id':mid,'line_id':line['id'],'item_id':item['id']},user_id=OWNER)
    assert saved['title']=='Synthetic stop'


def test_chapter_cache_revalidation_requires_current_membership_and_revision(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,cid=_replicas(monkeypatch);monkeypatch.setattr(http,'runtime',one)
    first=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid))
    tag=first['read_tag']
    def no_body(*args,**kwargs):raise AssertionError('A 304 must not hydrate manuscript bodies')
    native=http._list;monkeypatch.setattr(http,'_list',no_body)
    unchanged=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,tag=tag))
    assert unchanged.status_code==304 and unchanged.headers['cache-control']=='private, no-store'
    monkeypatch.setattr(http,'_uid',lambda:'usr_'+'f'*32)
    with pytest.raises(ProjectUnavailable):http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,tag=tag))
    monkeypatch.setattr(http,'_uid',lambda:OWNER);monkeypatch.setattr(http,'_list',native)
    one.projects.rename(pid,'New revision')
    changed=http.hosted_chapter_get(cid,request(pid,'/api/chapters/'+cid,tag=tag))
    assert changed['read_tag']!=tag and changed['blocks']==first['blocks']


def test_empty_body_autocommit_keeps_request_context_in_worker(monkeypatch):
    import hosted.http_api as http
    from starlette.requests import Request
    one,_,_,pid,_=_replicas(monkeypatch);monkeypatch.setattr(http,'runtime',one)
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    req=Request({'type':'http','method':'POST','path':'/api/autocommit',
        'query_string':b'label=Synthetic+history&source=continuous',
        'headers':[(b'x-breadmaker-project',pid.encode())]},receive)
    with one.context.bind(pid,user_id=OWNER):
        result=asyncio.run(http.hosted_autocommit(req))
        history=one.history.list_revisions()
        assert len(history)==1 and history[0]==result
        _,envelope=one.history.read_revision(result['revision_id'])
    import gzip
    import json
    snapshot=json.loads(gzip.decompress(envelope))['snapshot']
    assert snapshot['project']['id']==pid


def test_async_identity_checks_remain_revocable_and_task_local(monkeypatch):
    import threading
    from auth.identity_platform import IdentityPlatformError
    from tests.test_v10358 import _service,_seed_invite
    docs,_,admin,service=_service();_seed_invite(docs,'synthetic-async-invite')
    exchange=service.exchange_id_token('synthetic-token',invitation_token='synthetic-async-invite')
    main_thread=threading.get_ident();native=admin.verify_session_cookie
    def checked(cookie,check_revoked=False):
        assert threading.get_ident()!=main_thread,'Provider I/O blocked the ASGI thread'
        assert check_revoked is True
        return native(cookie,check_revoked=check_revoked)
    monkeypatch.setattr(admin,'verify_session_cookie',checked)
    async def exercise():
        async def bound(token):
            async with service.bind_async(token) as principal:
                await asyncio.sleep(.001)
                assert service.current()==principal
                return principal
        signed,anonymous=await asyncio.gather(bound(exchange.cookie),bound(None))
        assert signed.user_id==exchange.principal.user_id and anonymous is None
        assert service.current() is None
        def revoked(*args,**kwargs):raise RuntimeError('Synthetic revoked cookie')
        monkeypatch.setattr(admin,'verify_session_cookie',revoked)
        with pytest.raises(IdentityPlatformError):
            async with service.bind_async(exchange.cookie):pytest.fail('Revoked session admitted')
        assert service.current() is None
    asyncio.run(exercise())


def test_async_project_binding_is_off_loop_and_does_not_leak_projects(monkeypatch):
    import threading
    from auth.identity import AuthorizationError
    one,_,_,pid,_=_replicas(monkeypatch);other=one.projects.create('Second synthetic project')['id']
    native=one.context.projects.get_for_user;main_thread=threading.get_ident()
    def checked(*args,**kwargs):
        assert threading.get_ident()!=main_thread,'Membership I/O blocked the ASGI thread'
        return native(*args,**kwargs)
    monkeypatch.setattr(one.context.projects,'get_for_user',checked)
    async def exercise():
        async def bound(target):
            async with one.context.bind_async(target,user_id=OWNER) as context:
                await asyncio.sleep(.001)
                assert one.context.current()==context and context.project_id==target
        await asyncio.gather(bound(pid),bound(other))
        assert one.context.current() is None
        with pytest.raises(AuthorizationError,match='HOSTED_IDENTITY_MISMATCH'):
            async with one.context.bind_async(pid,user_id='usr_'+'f'*32):pytest.fail('Impersonation admitted')
    asyncio.run(exercise())
