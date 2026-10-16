from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PM=ROOT/'src/api/project_management'


def test_v10223_project_management_is_explicit_api_package_without_backend_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,23)
    assert PM.is_dir()
    assert not (ROOT/'src/backend/project_routes.py').exists()
    required={'__init__.py','README.md','profiles.py','workspace.py','metadata.py','destructive_actions.py','projects.py','members.py','packages.py','json_transfer.py','assets.py','health.py'}
    assert required <= {p.name for p in PM.iterdir()}
    tree=ast.parse((PM/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in (PM/'__init__.py').read_text(encoding='utf-8')


def test_v10223_platform_composition_and_runtime_call_canonical_project_management_owner():
    core=(ROOT/'src/backend/core.py').read_text(encoding='utf-8')
    runtime=(ROOT/'src/backend/runtime_routes.py').read_text(encoding='utf-8')
    assert 'from api import project_management' in core
    assert 'app.include_router(project_management.router)' in core
    assert 'project_routes' not in core
    assert 'from api.project_management import profiles' in runtime
    assert 'profiles._public_local_profile' in runtime
    assert 'project_routes' not in runtime


def test_v10223_route_map_records_platform_project_management_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    domain=next(d for d in manifest['platform_domains'] if d['module']=='api.project_management')
    assert domain['file']=='src/api/project_management/__init__.py'
    assert len(domain['routes'])==30
    files={r['file'] for r in domain['routes']}
    assert files >= {f'src/api/project_management/{name}.py' for name in ('profiles','workspace','metadata','destructive_actions','projects','members','packages','json_transfer','assets','health')}


def test_v10223_project_management_owners_are_bounded():
    impl=[p for p in PM.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in impl) < 260
