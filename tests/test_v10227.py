from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
API=SRC/'api'


def test_v10227_remaining_api_domains_are_normalized_packages():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,27)
    for name in ('annotations','comments','find_replace','icon_markers'):
        package=API/name
        assert package.is_dir()
        assert {'__init__.py','routes.py','contracts.py','README.md'} <= {p.name for p in package.iterdir()}
        assert not (API/f'{name}.py').exists()
        assert not (API/f'{name}_contracts.py').exists()
        boundary=ast.parse((package/'__init__.py').read_text(encoding='utf-8'))
        assert not [n for n in boundary.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]


def test_v10227_annotation_cross_domain_helpers_target_routes_owner():
    comments=(API/'comments/routes.py').read_text(encoding='utf-8')
    restore=(API/'checkpoints/restore_support.py').read_text(encoding='utf-8')
    assert "feature_call('annotations.routes', '_clean_comment_body')" in comments
    assert "feature_call('annotations.routes', '_comment_payload')" in comments
    assert "feature_call('annotations.routes', '_create_comment_thread')" in comments
    assert "feature_call('annotations.routes', '_clean_comment_body')" in restore
    assert "feature_call('annotations'," not in comments
    assert "feature_call('annotations'," not in restore


def test_v10227_project_json_markup_owns_escape_sentinels():
    markup=(SRC/'formats/project_json/markup.py').read_text(encoding='utf-8')
    validation=(SRC/'formats/project_json/validation.py').read_text(encoding='utf-8')
    assert '_ESCAPE_SENTINELS =' in markup
    assert '_SENTINEL_REVERSE =' in markup
    assert '_ESCAPE_SENTINELS =' not in validation
    assert '_SENTINEL_REVERSE =' not in validation


def test_v10227_api_root_contains_only_shared_composition_files():
    loose={p.name for p in API.glob('*.py')}
    assert loose == {'__init__.py','shared_contracts.py'}
