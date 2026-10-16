from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1010_domain_api_and_agent_navigation_contract():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,1,0)
    app=(ROOT/'src/app.py').read_text(encoding='utf-8')
    assert len(app.splitlines()) < 3000
    assert '_install_routers(_platform_app)' in app and 'app=_platform_app' in app
    assert 'sys.modules[__name__]' not in app
    assert '@app.post("/api/canvases")' not in app
    assert '@app.post("/api/maps")' not in app

    normalized=('annotations','comments','icon_markers','find_replace')
    for name in normalized:
        assert (ROOT/'src/api'/name).is_dir()
        assert not (ROOT/'src/api'/f'{name}.py').exists()
        assert not (ROOT/'src/api'/f'{name}_contracts.py').exists()
    assert (ROOT/'src/api/manuscript').is_dir() and not (ROOT/'src/api/manuscript.py').exists()
    assert (ROOT/'src/api/reviews').is_dir() and not (ROOT/'src/api/reviews.py').exists()
    assert (ROOT/'src/api/canvas').is_dir() and not (ROOT/'src/api/canvas.py').exists()
    assert (ROOT/'src/api/tasks').is_dir() and not (ROOT/'src/api/tasks.py').exists()
    assert (ROOT/'src/api/maps').is_dir() and not (ROOT/'src/api/maps.py').exists()
    assert (ROOT/'src/api/checkpoints').is_dir() and not (ROOT/'src/api/checkpoints.py').exists()
    assert (ROOT/'src/api/durability').is_dir() and not (ROOT/'src/api/history_backups.py').exists()
    assert (ROOT/'src/api/import_export').is_dir() and not (ROOT/'src/api/import_export.py').exists()
    assert (ROOT/'src/api/workspace_tools').is_dir() and not (ROOT/'src/api/workspace_tools.py').exists()
    assert (ROOT/'AGENTS.md').is_file()
    assert (ROOT/'src/api/CODEMAP.md').is_file()

    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    routes=[route for domain in manifest['domains'] for route in domain['routes']]
    assert any(r['path']=='/api/canvases/{cid}/populate' and r['function']=='populate_canvas' for r in routes)
    assert any(r['path']=='/api/maps/{mid}/populate' and r['function']=='populate_map' for r in routes)

    index=json.loads((ROOT/'docs/CODE_INDEX.json').read_text(encoding='utf-8'))
    assert index['route_decorator_count'] >= 303
    assert any(r.get('path')=='/api/book' and r['file']=='src/api/manuscript/document.py' for r in index['routes'])
    assert any(r.get('path')=='/api/reviews' and r['file']=='src/api/reviews/lifecycle.py' for r in index['routes'])


def test_v1010_release_verifier_discovers_10x_contracts():
    verifier=(ROOT/'scripts/verify_release.py').read_text(encoding='utf-8')
    assert 'TESTS.glob("test_v10*.py")' in verifier
    assert 'TESTS.glob("test_v10*_browser.py")' in verifier
    assert '"test_v1010.py"' in verifier


def test_v1010_historical_source_contracts_use_backend_boundary():
    helper=(ROOT/'tests/source_contracts.py').read_text(encoding='utf-8')
    assert 'def backend_source' in helper
    direct=[]
    for path in ROOT.joinpath('tests').glob('test_*.py'):
        if path.name in {'test_v1010.py','test_v8153.py'}:continue
        source=path.read_text(encoding='utf-8')
        if "src','app.py" in source or "src'/'app.py" in source or "src/app.py').read_text" in source:
            direct.append(path.name)
    assert direct==[]
