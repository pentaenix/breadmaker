from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CANVAS=ROOT/'src'/'api'/'canvas'


def test_v10219_canvas_is_a_real_domain_package_without_compatibility_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,19)
    assert CANVAS.is_dir()
    assert not (ROOT/'src/api/canvas.py').exists()
    expected={'populate.py','developmental.py','registry.py','versions.py','lifecycle.py','nodes.py','edges.py','edge_styles.py','structure.py','__init__.py','README.md'}
    assert expected <= {p.name for p in CANVAS.iterdir()}
    init=(CANVAS/'__init__.py').read_text(encoding='utf-8')
    tree=ast.parse(init)
    assert not [node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in init


def test_v10219_canvas_owners_are_bounded_and_cross_feature_edges_are_canonical():
    implementation=[p for p in CANVAS.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in implementation) < 450
    combined='\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'src').rglob('*.py'))
    assert "feature_call('canvas'," not in combined
    assert "feature_call('canvas', " not in combined
    assert "feature_call('canvas.developmental'" in combined
    assert "feature_call('canvas.populate'" in combined


def test_v10219_route_map_records_canvas_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    canvas=next(domain for domain in manifest['domains'] if domain['module']=='api.canvas')
    assert canvas['file']=='src/api/canvas/__init__.py'
    assert len(canvas['routes'])==27
    files={route['file'] for route in canvas['routes']}
    assert 'src/api/canvas/populate.py' in files
    assert 'src/api/canvas/versions.py' in files
    assert 'src/api/canvas/nodes.py' in files
    assert 'src/api/canvas/edges.py' in files
    assert 'src/api/canvas/structure.py' in files
    assert all(path.startswith('src/api/canvas/') for path in files)


def test_v10219_stale_manuscript_review_contract_targets_document_owner():
    source=(ROOT/'tests/test_v1011.py').read_text(encoding='utf-8')
    assert "import_module('api.manuscript.document')" in source
    assert 'manuscript_document._review_rows' in source
    assert 'manuscript._review_rows' not in source
