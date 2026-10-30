from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MANUSCRIPT=ROOT/'src'/'api'/'manuscript'


def test_v10218_manuscript_is_a_real_domain_package_without_compatibility_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,18)
    assert MANUSCRIPT.is_dir()
    assert not (ROOT/'src/api/manuscript.py').exists()
    expected={'navigation.py','document.py','images.py','anchors.py','chapter_structure.py','chapters.py','scenes.py','__init__.py','README.md'}
    assert expected <= {p.name for p in MANUSCRIPT.iterdir()}
    init=(MANUSCRIPT/'__init__.py').read_text(encoding='utf-8')
    tree=ast.parse(init)
    assert not [node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in init


def test_v10218_manuscript_owners_are_bounded_and_cross_feature_edges_are_canonical():
    implementation=[p for p in MANUSCRIPT.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in implementation) < 600
    combined='\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'src/api').rglob('*.py'))
    assert "feature_call('manuscript'," not in combined
    assert "feature_call('manuscript.anchors'," in combined
    assert "feature_call('manuscript.images'," in combined
    assert "feature_call('manuscript.scenes'," in combined


def test_v10218_route_map_records_manuscript_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    manuscript=next(domain for domain in manifest['domains'] if domain['module']=='api.manuscript')
    assert manuscript['file']=='src/api/manuscript/__init__.py'
    narration=[route for route in manuscript['routes'] if '/narration' in route['file']]
    assert len(narration)==8
    assert len(manuscript['routes'])-len(narration)==24
    files={route['file'] for route in manuscript['routes']}
    assert 'src/api/manuscript/document.py' in files
    assert 'src/api/manuscript/images.py' in files
    assert 'src/api/manuscript/chapter_structure.py' in files
    assert 'src/api/manuscript/chapters.py' in files
    assert 'src/api/manuscript/scenes.py' in files
    for owner in ('narration.py','narration_voices.py','narration_pronunciation.py'):
        assert 'src/api/manuscript/'+owner in files
    assert all(path.startswith('src/api/manuscript/') for path in files)


def test_v10218_review_failure_contracts_patch_portable_owner():
    security=(ROOT/'tests/test_v9613.py').read_text(encoding='utf-8')
    ownership=(ROOT/'tests/test_v1012.py').read_text(encoding='utf-8')
    needle="monkeypatch.setattr(portable_reviews,'runtime',fake)"
    assert needle in security
    assert needle in ownership
    assert "monkeypatch.setattr(reviews_api,'runtime',fake)" not in security
