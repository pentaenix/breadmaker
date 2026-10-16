from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAPS=ROOT/'src/api/maps'
TASKS=ROOT/'src/api/tasks'


def _assert_composition_only(package:Path):
    tree=ast.parse((package/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in (package/'__init__.py').read_text(encoding='utf-8')


def test_v10221_maps_and_tasks_are_real_domain_packages_without_flat_facades():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,21)
    assert MAPS.is_dir() and not (ROOT/'src/api/maps.py').exists()
    assert TASKS.is_dir() and not (ROOT/'src/api/tasks.py').exists()
    assert {'domain.py','anchors.py','lifecycle.py','lines.py','items.py','junctions.py','layout.py','populate.py','__init__.py','README.md'} <= {p.name for p in MAPS.iterdir()}
    assert {'lifecycle.py','milestones.py','duplication.py','__init__.py','README.md'} <= {p.name for p in TASKS.iterdir()}
    _assert_composition_only(MAPS); _assert_composition_only(TASKS)


def test_v10221_maps_and_tasks_owners_are_bounded_and_map_edges_are_canonical():
    map_impl=[p for p in MAPS.glob('*.py') if p.name!='__init__.py']
    task_impl=[p for p in TASKS.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in map_impl) < 300
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in task_impl) < 120
    combined='\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'src/api').rglob('*.py'))
    assert "feature_call('maps', '_detach_map_anchors_for_removed_manuscript')" not in combined
    assert "feature_call('maps.anchors', '_detach_map_anchors_for_removed_manuscript')" in combined


def test_v10221_route_map_records_maps_and_tasks_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    maps=next(d for d in manifest['domains'] if d['module']=='api.maps')
    tasks=next(d for d in manifest['domains'] if d['module']=='api.tasks')
    assert maps['file']=='src/api/maps/__init__.py' and len(maps['routes'])==24
    assert tasks['file']=='src/api/tasks/__init__.py' and len(tasks['routes'])==10
    assert all(r['file'].startswith('src/api/maps/') for r in maps['routes'])
    assert all(r['file'].startswith('src/api/tasks/') for r in tasks['routes'])
    assert {r['file'] for r in maps['routes']} >= {'src/api/maps/lifecycle.py','src/api/maps/lines.py','src/api/maps/items.py','src/api/maps/junctions.py','src/api/maps/layout.py','src/api/maps/populate.py'}
    assert {r['file'] for r in tasks['routes']} == {'src/api/tasks/lifecycle.py','src/api/tasks/milestones.py','src/api/tasks/duplication.py'}
