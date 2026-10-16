from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DURABILITY=ROOT/'src/api/durability'


def test_v10222_durability_is_real_domain_package_without_mixed_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,22)
    assert DURABILITY.is_dir()
    assert not (ROOT/'src/api/history_backups.py').exists()
    required={
        '__init__.py','README.md','cuts.py','history.py','undo.py','history_baseline.py',
        'recovery.py','backup_destinations.py','recovery_backups.py','backup_inventory.py','backups.py',
    }
    assert required <= {p.name for p in DURABILITY.iterdir()}
    tree=ast.parse((DURABILITY/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in (DURABILITY/'__init__.py').read_text(encoding='utf-8')


def test_v10222_cross_feature_edges_target_durability_canonical_owners():
    combined='\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'src').rglob('*.py'))
    assert "feature_call('history_backups'" not in combined
    assert 'api.history_backups' not in combined
    assert "feature_call('durability.cuts'" in combined
    assert "feature_call('durability.backups'" in combined
    assert "feature_call('durability.backup_destinations'" in combined
    assert not (ROOT/'src/api/history_backups.py').exists()


def test_v10222_route_map_records_durability_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    domain=next(d for d in manifest['domains'] if d['module']=='api.durability')
    assert domain['file']=='src/api/durability/__init__.py'
    assert len(domain['routes'])==46
    files={r['file'] for r in domain['routes']}
    assert files >= {
        'src/api/durability/cuts.py',
        'src/api/durability/history.py',
        'src/api/durability/undo.py',
        'src/api/durability/history_baseline.py',
        'src/api/durability/recovery.py',
        'src/api/durability/backup_destinations.py',
        'src/api/durability/recovery_backups.py',
        'src/api/durability/backup_inventory.py',
        'src/api/durability/backups.py',
    }


def test_v10222_durability_owners_are_bounded():
    impl=[p for p in DURABILITY.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in impl) < 350
