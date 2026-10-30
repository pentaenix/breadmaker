"""Signed-out page navigation redirects; API authority still fails closed.

Real hosted middleware/router over HTTP with deterministic session authority.
No provider calls, production credentials or manuscript writes.
"""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import pytest

from auth.identity import AuthorizationError
from auth.sessions import AuthenticationError

PID='a'*32
COOKIE='__Host-breadmaker_session'
PAGES=['/','/canvas','/maps','/tasks','/reviews']+[
    f'/p/{PID}'+suffix for suffix in ('','/canvas','/maps','/tasks','/reviews')]


@pytest.fixture
def hosted_http(monkeypatch):
    import hosted.http_api as http
    principal=ContextVar('redirect_test_principal',default=None)
    memberships=[]
    class Sessions:
        requires_authentication=True
        cookie_name=COOKIE
        cookie_secure=True
        def current(self):return principal.get()
        @contextmanager
        def bind(self,token):
            if token and token!='valid':raise AuthenticationError('Synthetic expired/revoked cookie')
            handle=principal.set(SimpleNamespace(user_id='usr_'+'b'*32) if token else None)
            try:yield principal.get()
            finally:principal.reset(handle)
    class Identity:
        def request_user_id(self,_):return principal.get().user_id
        def current_actor(self,project_id,*,require_membership):
            assert principal.get() is not None and require_membership
            memberships.append(project_id)
            if project_id!=PID:raise AuthorizationError('Synthetic non-member')
    monkeypatch.setattr(http,'runtime',SimpleNamespace(sessions=Sessions(),identity=Identity()))
    monkeypatch.setenv('BREADMAKER_PUBLIC_ORIGIN','https://example.invalid')
    app=FastAPI()
    http.install_request_boundary(app)
    app.include_router(http.platform_router)
    @app.get('/auth/login')
    def login():return HTMLResponse('<h1>Login</h1>')
    with TestClient(app,base_url='https://example.invalid',follow_redirects=False) as client:
        yield client,memberships


@pytest.mark.parametrize('path',PAGES)
@pytest.mark.parametrize('cookie',[None,'expired'])
def test_signed_out_pages_redirect_to_login_with_exact_local_destination(hosted_http,path,cookie):
    client,memberships=hosted_http
    if cookie:client.cookies.set(COOKIE,cookie,domain='example.invalid',path='/')
    response=client.get(path+'?view=outline&filter=one%20two')
    assert response.status_code==303
    location=urlsplit(response.headers['location'])
    assert not location.netloc and location.path=='/auth/login'
    assert parse_qs(location.query)=={'return_to':[path+'?view=outline&filter=one%20two']}
    assert 'no-store' in response.headers['cache-control']
    assert not memberships
    if cookie:
        cleared=response.headers['set-cookie']
        assert COOKIE+'=' in cleared and 'Max-Age=0' in cleared
        assert 'Secure' in cleared and 'HttpOnly' in cleared and 'SameSite=lax' in cleared
    # Following the redirect must reach login, not another authentication error.
    assert client.get(response.headers['location'],follow_redirects=True).text=='<h1>Login</h1>'


def test_direct_login_with_bad_cookie_clears_it_without_redirect_loop(hosted_http):
    client,memberships=hosted_http
    client.cookies.set(COOKIE,'revoked',domain='example.invalid',path='/')
    target='/auth/login?return_to=/p/'+PID+'/maps'
    response=client.get(target,follow_redirects=True)
    assert response.status_code==200 and response.text=='<h1>Login</h1>'
    assert len(response.history)==1 and response.history[0].headers['location']==target
    assert not memberships and COOKIE not in client.cookies


def test_async_session_rejection_also_redirects_and_recovers_login(hosted_http,monkeypatch):
    import hosted.http_api as http
    client,memberships=hosted_http
    original=http.runtime.sessions.bind
    @asynccontextmanager
    async def bind_async(token):
        with original(token) as principal:yield principal
    monkeypatch.setattr(http.runtime.sessions,'bind_async',bind_async,raising=False)
    client.cookies.set(COOKIE,'revoked',domain='example.invalid',path='/')
    response=client.get(f'/p/{PID}/reviews',follow_redirects=True)
    assert response.status_code==200 and response.text=='<h1>Login</h1>'
    assert len(response.history)==1 and response.history[0].status_code==303
    assert not memberships and COOKIE not in client.cookies


@pytest.mark.parametrize('method,path',[
    ('GET','/api/projects'),('GET','/api/project/health'),
    ('GET','/api/book'),('GET','/api/assets/private.png'),
    ('POST','/api/projects'),('POST','/api/auth/session/exchange'),
    ('POST','/tasks'),('GET','/p/'+PID+'/not-a-view'),
])
def test_bad_cookie_api_and_non_navigation_requests_keep_json_denial(hosted_http,method,path):
    client,memberships=hosted_http
    client.cookies.set(COOKIE,'invalid',domain='example.invalid',path='/')
    response=client.request(method,path,headers={'Origin':'https://example.invalid'})
    assert response.status_code==401 and response.json()=={'detail':'AUTHENTICATION_REQUIRED'}
    assert 'location' not in response.headers and 'no-store' in response.headers['cache-control']
    assert not memberships


def test_redirect_ignores_host_and_keeps_untrusted_return_parameter_in_query(hosted_http):
    client,_=hosted_http
    client.cookies.set(COOKIE,'invalid',domain='example.invalid',path='/')
    response=client.get('/tasks?return_to=//attacker.invalid',headers={'Host':'attacker.invalid'})
    location=urlsplit(response.headers['location'])
    assert location.path=='/auth/login' and not location.netloc and not location.scheme
    assert parse_qs(location.query)=={'return_to':['/tasks?return_to=//attacker.invalid']}


def test_valid_session_still_requires_membership_and_never_loses_cookie(hosted_http):
    client,memberships=hosted_http
    client.cookies.set(COOKIE,'valid',domain='example.invalid',path='/')
    allowed=client.get(f'/p/{PID}/canvas')
    assert allowed.status_code==200 and 'text/html' in allowed.headers['content-type']
    denied=client.get('/p/'+'c'*32+'/canvas')
    assert denied.status_code==303 and denied.headers['location']=='/?new_project=1'
    assert 'no-store' in denied.headers['cache-control']
    assert memberships==[PID,'c'*32]
    assert client.cookies.get(COOKIE)=='valid'


def test_local_accountless_routes_keep_original_behavior(client,fresh_project):
    response=client.get('/')
    assert response.status_code==200 and 'text/html' in response.headers['content-type']
    assert 'location' not in response.headers
