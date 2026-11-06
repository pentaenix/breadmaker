"""Missing browser pages stay404 without weakening API/auth or SPA routes."""
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from hosted.missing_page import install


@pytest.fixture
def client():
    app=FastAPI();install(app)
    @app.get('/known-missing')
    def known_missing():raise HTTPException(404,'PRIVATE_RESOURCE_NOT_FOUND',headers={'X-Synthetic':'preserved'})
    @app.get('/known-view')
    def known_view():return {'ok':True}
    return TestClient(app)


def test_unmatched_html_is_branded404_without_echoing_path(client):
    result=client.get('/secret-looking-unknown-name',headers={'Accept':'text/html'})
    assert result.status_code==404 and result.headers['content-type'].startswith('text/html')
    assert '<h1>Page not found</h1>' in result.text and 'href="/"' in result.text
    assert '/static/breadmaker_logo.svg' in result.text
    assert 'secret-looking-unknown-name' not in result.text
    assert 'admin' not in result.text.lower()
    assert result.headers['x-robots-tag']=='noindex' and 'no-store' in result.headers['cache-control']


def test_language_head_and_accept_negotiation(client):
    headers={'Accept':'text/html','Accept-Language':'en;q=0,es-MX;q=.9'}
    get=client.get('/missing',headers=headers);head=client.head('/missing',headers=headers)
    assert get.status_code==head.status_code==404
    assert '<html lang="es">' in get.text and 'Página no encontrada' in get.text
    assert not head.content and head.headers['content-length']==get.headers['content-length']
    result=client.get('/missing',headers={'Accept':'text/html;q=0,application/json'})
    assert result.status_code==404 and result.json()=={'detail':'Not Found'}


def test_machine_asset_and_existing_route_errors_remain_unchanged(client):
    for path in ['/api/missing','/static/missing.css','/locales/missing.json']:
        result=client.get(path,headers={'Accept':'text/html'})
        assert result.status_code==404 and result.json()=={'detail':'Not Found'}
    result=client.get('/known-missing',headers={'Accept':'text/html'})
    assert result.status_code==404 and result.json()=={'detail':'PRIVATE_RESOURCE_NOT_FOUND'}
    assert result.headers['x-synthetic']=='preserved'
    assert client.get('/known-view',headers={'Accept':'text/html'}).json()=={'ok':True}
    result=client.post('/known-view',headers={'Accept':'text/html'})
    assert result.status_code==405 and 'GET' in result.headers['allow']
    assert client.post('/missing',headers={'Accept':'text/html'}).json()=={'detail':'Not Found'}
