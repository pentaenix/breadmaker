"""Bounded performance fixes must not bypass private-data authorization or CAS."""
import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

from auth.sessions import AuthenticationError
from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas


@pytest.mark.parametrize('method,path,public',[
    ('GET','/static/js/core/boot.js',True),
    ('HEAD','/static/breadmaker_logo.svg',True),
    ('GET','/locales/en.json',True),
    ('GET','/api/assets/img_private.png',False),
    ('GET','/api/book',False),
    ('GET','/api/bootstrap',False),
    ('GET','/locales/../api/bootstrap',False),
    ('POST','/static/js/core/boot.js',False),
])
def test_only_public_file_reads_avoid_provider_session_work(monkeypatch,method,path,public):
    import hosted.http_api as http
    calls=[]
    class Sessions:
        cookie_name='session'
        requires_authentication=True
        def bind(self,token):
            calls.append('authenticate')
            raise AuthenticationError('AUTHENTICATION_REQUIRED')
    monkeypatch.setattr(http,'runtime',SimpleNamespace(sessions=Sessions()))
    async def next_handler(request):
        calls.append('serve')
        return Response(status_code=200)
    request=Request({'type':'http','method':method,'path':path,'query_string':b'',
        'scheme':'https','server':('unit.invalid',443),'headers':[(b'cookie',b'session=invalid')]})
    response=asyncio.run(http.hosted_request_boundary(request,next_handler))
    if public:
        assert response.status_code==200 and calls==['serve']
    else:
        assert response.status_code in {401,403} and 'serve' not in calls


def test_full_text_reads_only_manuscript_entities_and_keeps_exact_revision(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=_replicas(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    original=one.projects.domain.list
    reads=[]
    def counted(entity,*args,**kwargs):
        reads.append(entity)
        return original(entity,*args,**kwargs)
    monkeypatch.setattr(one.projects.domain,'list',counted)
    result=http._project_snapshot(pid,manuscript_only=True)
    assert reads==['document_tab','chapter','scene','manuscript_block']
    assert result['blocks'] and result['chapters']
    assert all(ch['revision']==str(result['project']['generation']) for ch in result['chapters'])
    assert not {'canvases','maps','reviews'} & result.keys()


def test_full_text_rejects_concurrent_mutation_and_foreign_actor(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=_replicas(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    original=one.projects.domain.list
    def intervening(entity,*args,**kwargs):
        rows=original(entity,*args,**kwargs)
        if entity=='manuscript_block':one.projects.rename(pid,'Concurrent synthetic rename')
        return rows
    monkeypatch.setattr(one.projects.domain,'list',intervening)
    with pytest.raises(DocumentConflict,match='SNAPSHOT_GENERATION_CHANGED'):
        http._project_snapshot(pid,manuscript_only=True)
    monkeypatch.setattr(one.identity,'active_profile_id',lambda:'usr_'+'f'*32)
    with pytest.raises(ProjectUnavailable):http._project_snapshot(pid,manuscript_only=True)


def test_browser_icon_reuses_existing_breadmaker_mark(client):
    page=client.get('/')
    from bs4 import BeautifulSoup
    icon=BeautifulSoup(page.text,'html.parser').find('link',rel='icon')
    assert icon and icon['type']=='image/svg+xml'
    assert icon['href'].split('?')[0]=='/static/breadmaker_logo.svg'
    image=client.get(icon['href'])
    assert image.status_code==200 and 'bread.shape' in image.text and 'letter.B' in image.text
