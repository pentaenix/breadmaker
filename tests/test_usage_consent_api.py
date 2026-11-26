"""HTTP consent boundary belongs in Core/API, outside the pure unit layer."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hosted.usage_consent import FIELD, router_for
from tests.unit.test_usage_consent import setup, body, UID, OTHER

def test_http_uses_only_bound_account_and_bounds_json(setup):
    store, fenced, _ = setup
    runtime = SimpleNamespace(mode='hosted', sessions=SimpleNamespace(metadata=lambda: {'auth_mode': 'identity_platform'}),
                              identity=SimpleNamespace(documents=fenced, request_user_id=lambda: UID))
    app = FastAPI(); app.include_router(router_for(runtime))
    with TestClient(app) as client:
        path = '/api/account/usage-consent'
        result = client.put(path, json=body(False), headers={'X-Breadmaker-User': OTHER})
        assert result.status_code == 200 and result.json()['choice'] is False
        assert FIELD not in store.get(('users', OTHER)).data
        assert client.get(path).json() == result.json()
        assert client.put(path, content='{"choice":true,"choice":false}').status_code == 400
        assert client.put(path, content=b'x' * 513).status_code == 413
        runtime.mode = 'local'
        assert client.get(path).status_code == 404
