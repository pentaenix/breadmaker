"""Project-page recovery never makes a missing private API readable."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from auth.identity import AuthorizationError
from hosted.firestore.contracts import HostedAuthorizationError,ProjectUnavailable
from tests.test_v10566 import hosted_http,COOKIE,PID


@pytest.mark.parametrize('error',[AuthorizationError,HostedAuthorizationError,ProjectUnavailable])
def test_deleted_and_nonmember_pages_have_identical_fresh_workspace_redirect(hosted_http,monkeypatch,error):
    import hosted.http_api as http
    client,_=hosted_http
    client.cookies.set(COOKIE,'valid',domain='example.invalid',path='/')
    def deny(*args,**kwargs):raise error('Synthetic unavailable project')
    monkeypatch.setattr(http.runtime.identity,'current_actor',deny)
    response=client.get(f'/p/{PID}/maps')
    assert response.status_code==303 and response.headers['location']=='/?new_project=1'
    assert 'no-store' in response.headers['cache-control']
    assert client.cookies.get(COOKIE)=='valid'


def test_unavailable_project_api_still_returns_private_json404(hosted_http,monkeypatch):
    import hosted.http_api as http
    client,_=hosted_http
    client.cookies.set(COOKIE,'valid',domain='example.invalid',path='/')
    @contextmanager
    def deny(*args,**kwargs):
        raise ProjectUnavailable('PROJECT_NOT_FOUND')
        yield
    monkeypatch.setattr(http.runtime,'context',SimpleNamespace(bind=deny),raising=False)
    response=client.get(f'/api/projects/{PID}/members')
    assert response.status_code==404 and response.json()=={'detail':'PROJECT_NOT_FOUND'}
    assert 'location' not in response.headers and 'no-store' in response.headers['cache-control']


def test_authority_outage_is_not_misreported_as_a_missing_project(hosted_http,monkeypatch):
    import hosted.http_api as http
    client,_=hosted_http
    client.cookies.set(COOKIE,'valid',domain='example.invalid',path='/')
    def unavailable(*args,**kwargs):raise OSError('Synthetic provider unavailable')
    monkeypatch.setattr(http.runtime.identity,'current_actor',unavailable)
    with pytest.raises(OSError,match='Synthetic provider unavailable'):
        client.get(f'/p/{PID}')
