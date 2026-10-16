from __future__ import annotations

import importlib
from pathlib import Path

from api import ROUTER_MODULES
from source_contracts import backend_source, composition_source

ROOT=Path(__file__).resolve().parents[1]


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1011_transitional_bridge_has_been_superseded_by_explicit_dependencies():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,2,0)
    assert not (ROOT/'src/api/_bridge.py').exists()
    source=backend_source(ROOT)
    composition=composition_source(ROOT)
    assert '_install_routers(_platform_app)' in composition
    assert 'app=_platform_app' in composition
    assert 'sys.modules[__name__]' not in composition
    for name in ROUTER_MODULES:
        file=ROOT/'src/api'/f'{name}.py'
        package=ROOT/'src/api'/name
        paths=[file] if file.is_file() else sorted(package.glob('*.py'))
        source='\n'.join(path.read_text(encoding='utf-8') for path in paths)
        assert 'bind_core(' not in source
        assert '__core_exports__' not in source
        assert 'globals().update' not in source


def test_v1011_cross_domain_calls_have_named_canonical_owners(app_module_fixture):
    # Cross-domain compatibility is explicit and cycle-safe: aliases are callable lazy
    # references, and invoking them resolves the canonical function from its owner.
    manuscript_document=importlib.import_module('api.manuscript.document')
    reviews=importlib.import_module('api.reviews.lifecycle')
    assert callable(manuscript_document._review_rows)
    assert manuscript_document._review_rows.__name__=='_review_rows'
    assert reviews._review_rows is not manuscript_document._review_rows


def test_v1011_bootstrap_can_resolve_review_summary_after_decomposition(client):
    response=client.get('/api/bootstrap')
    assert response.status_code==200,response.text
    payload=response.json()
    assert isinstance(payload.get('reviews'),list)
