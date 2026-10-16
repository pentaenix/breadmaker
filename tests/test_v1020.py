from __future__ import annotations

import json
from pathlib import Path

from source_contracts import composition_source

ROOT=Path(__file__).resolve().parents[1]


def test_v1020_release_uses_explicit_backend_dependencies_without_namespace_bridge():
    assert (ROOT/'VERSION').read_text(encoding='utf-8').strip()>='10.2.0'
    assert not (ROOT/'src/api/_bridge.py').exists()
    deps=(ROOT/'src/backend/dependencies.py').read_text(encoding='utf-8')
    assert 'def feature_call' in deps
    assert 'require_dependency' not in deps
    assert 'namespace.setdefault' not in deps and 'globals().update' not in deps
    app=composition_source(ROOT)
    assert len(app.splitlines()) < 40
    assert 'sys.modules[__name__]' not in app
    assert '_install_routers(_platform_app)' in app and 'app=_platform_app' in app
    for path in (ROOT/'src/api').rglob('*.py'):
        source=path.read_text(encoding='utf-8')
        assert 'bind_core(' not in source
        assert '__core_exports__' not in source


def test_v1020_dependency_manifest_matches_generated_source_declarations():
    import importlib.util
    path=ROOT/'scripts/generate_backend_dependency_map.py'
    spec=importlib.util.spec_from_file_location('breadmaker_dependency_map',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    expected=json.loads((ROOT/'src/api/dependency_manifest.json').read_text(encoding='utf-8'))
    assert module.build()==expected
    feature_edges=[edge for domain in expected['domains'] for edge in domain['feature_dependencies']]
    assert feature_edges


def test_v1020_route_modules_register_without_transitional_exports(app_module_fixture):
    # FastAPI >=0.137 keeps included routers as live route-tree nodes instead of
    # flattening every child into app.routes. OpenAPI is the supported effective
    # path-operation surface and remains stable across that internal change.
    paths=app_module_fixture.app.openapi()['paths']
    assert 'get' in paths['/api/bootstrap']
    assert 'get' in paths['/api/reviews']
    assert 'post' in paths['/api/canvases/{cid}/populate']
