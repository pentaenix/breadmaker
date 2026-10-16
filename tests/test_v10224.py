from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CP=ROOT/'src/api/checkpoints'


def test_v10224_checkpoints_are_explicit_domain_package_without_flat_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,24)
    assert CP.is_dir()
    assert not (ROOT/'src/api/checkpoints.py').exists()
    required={'__init__.py','README.md','capture.py','lineage.py','comparison.py','restore_support.py','restore.py','lifecycle.py','apply.py'}
    assert required <= {p.name for p in CP.iterdir()}
    tree=ast.parse((CP/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in (CP/'__init__.py').read_text(encoding='utf-8')


def test_v10224_checkpoint_cross_domain_edges_target_canonical_owners():
    durability=(ROOT/'src/api/durability/history.py').read_text(encoding='utf-8')
    reviews=(ROOT/'src/api/reviews/snapshots.py').read_text(encoding='utf-8')
    transfer=(ROOT/'src/api/import_export/docx_import.py').read_text(encoding='utf-8')
    combined='\n'.join((durability,reviews,transfer))
    assert "feature_call('checkpoints'," not in combined
    assert "feature_call('checkpoints.capture'," in combined
    assert "feature_call('checkpoints.lineage'," in combined
    assert "feature_call('checkpoints.comparison'," in durability


def test_v10224_route_map_records_checkpoint_subdomain_owners():
    manifest=json.loads((ROOT/'src/api/route_manifest.json').read_text(encoding='utf-8'))
    domain=next(d for d in manifest['domains'] if d['module']=='api.checkpoints')
    assert domain['file']=='src/api/checkpoints/__init__.py'
    assert len(domain['routes'])==8
    files={r['file'] for r in domain['routes']}
    assert files=={
        'src/api/checkpoints/lifecycle.py',
        'src/api/checkpoints/comparison.py',
        'src/api/checkpoints/apply.py',
    }


def test_v10224_checkpoint_owners_are_bounded_and_restore_is_separate_from_capture():
    impl=[p for p in CP.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in impl) < 300
    capture=(CP/'capture.py').read_text(encoding='utf-8')
    restore=(CP/'restore.py').read_text(encoding='utf-8')
    assert '_restore_chapter_snapshot' not in capture
    assert '_restore_book_snapshot' not in capture
    assert 'def _restore_chapter_snapshot' in restore
    assert 'def _restore_book_snapshot' in restore
