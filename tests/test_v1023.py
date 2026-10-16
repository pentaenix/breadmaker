from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1023_release_identity_and_src_root_contains_only_entrypoints():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,2,3)
    assert {p.name for p in SRC.glob('*.py')} == {'__init__.py','app.py','server.py'}
    for package in ('api','auth','backend','backups','formats','reliability','revision_store','runtime','security','storage','workspace'):
        assert (SRC/package/'__init__.py').is_file(), package


def test_v1023_backend_support_modules_have_explicit_owners():
    expected={
        'auth/identity.py','auth/sessions.py',
        'backend/diagnostics.py',
        'formats/breadreview.py','formats/fragment_json.py','formats/project_json/validation.py',
        'security/content_security.py','security/hosted_guardrails.py',
        'storage/schema.py',
    }
    present={p.relative_to(SRC).as_posix() for p in SRC.rglob('*.py')}
    assert expected <= present
    retired={
        'identity.py','sessions.py','diagnostics.py','breadreview.py','fragment_json.py',
        'project_json.py','content_security.py','hosted_guardrails.py','storage.py',
        'storage_schema.py','workspace.py',
    }
    assert not (retired & present)


def test_v1023_moved_storage_keeps_repository_root_semantics():
    source=(SRC/'storage'/'context.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    assigns={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            assigns[node.targets[0].id]=ast.unparse(node.value)
    assert 'parents[1]' in assigns['SOURCE_ROOT']
    assert assigns['ROOT']=='SOURCE_ROOT.parent'
