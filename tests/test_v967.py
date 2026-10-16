from source_contracts import backend_source,frontend_styles_source_from_styles_path
from pathlib import Path

from backend import request_pipeline

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text().strip()
APP=backend_source(ROOT)
HTML=(ROOT/'static/index.html').read_text()
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))




def test_v967_runtime_bootstrap_precedes_project_and_actor_binding():
    start=APP.index('async def local_origin_guard(')
    end=APP.index('\n\ndef startup():',start)
    middleware=APP[start:end]
    fast=middleware.index("path=='/api/runtime'")
    project=middleware.index('_request_project_identity(request)')
    actor=middleware.index('_request_user_identity(request)')
    assert fast < project
    assert fast < actor
    assert 'return JSONResponse(_runtime_bootstrap_payload()' in middleware


def test_v967_runtime_http_bootstrap_does_not_consult_project_identity(client,monkeypatch,app_module_fixture):
    def forbidden(*args,**kwargs):
        raise AssertionError('runtime bootstrap touched project/profile request binding')
    monkeypatch.setattr(request_pipeline,'_request_project_identity',forbidden)
    monkeypatch.setattr(request_pipeline,'_request_user_identity',forbidden)
    response=client.get('/api/runtime')
    assert response.status_code==200,response.text
    payload=response.json()
    assert payload['mode'] in {'local','hosted_dev'}
    assert payload['project_header']=='X-Breadmaker-Project'
    assert response.headers.get('cache-control')=='no-store, max-age=0'
