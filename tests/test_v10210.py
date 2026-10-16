from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / 'src' / 'workspace'


def test_v10210_workspace_package_boundary_is_lightweight_and_owned():
    assert tuple(map(int, (ROOT / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10, 2, 10)
    boundary = (WORKSPACE / '__init__.py').read_text(encoding='utf-8')
    assert len(boundary.splitlines()) < 80
    tree = ast.parse(boundary)
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body)
    assert 'workspace = Workspace()' in boundary

    expected = {
        'service.py', 'profiles.py', 'memberships.py', 'projects', 'remote_history.py',
        'credentials.py', 'backups', 'erasure.py', 'providers.py', 'filesystem.py',
        'models.py', 'constants.py', 'README.md',
    }
    assert expected <= {path.name for path in WORKSPACE.iterdir()}


def test_v10210_workspace_service_composes_named_owners_without_rehousing_them():
    service = (WORKSPACE / 'service.py').read_text(encoding='utf-8')
    for owner in (
        'ProfileWorkspaceMixin', 'RemoteHistoryWorkspaceMixin', 'CredentialWorkspaceMixin',
        'MembershipWorkspaceMixin', 'ProjectWorkspaceMixin', 'BackupWorkspaceMixin',
        'BackupErasureWorkspaceMixin',
    ):
        assert owner in service
    # service.py owns composition/registry/manifests, not the extracted domains.
    for forbidden in ('def export_project(', 'def sync_remote_history(', 'def add_backup_destination(', 'def erase_known_managed_backup_data('):
        assert forbidden not in service


def test_v10210_production_internal_workspace_imports_target_canonical_owners():
    offenders = []
    for path in (ROOT / 'src').rglob('*.py'):
        if WORKSPACE in path.parents:
            continue
        text = path.read_text(encoding='utf-8')
        for symbol in ('safe_name', 'WorkspaceError', 'APP_SCHEMA_VERSION'):
            if f'from workspace import {symbol}' in text:
                offenders.append(f'{path.relative_to(ROOT)}:{symbol}')
            if 'from workspace import ' in text and symbol in text.split('from workspace import ', 1)[1].split('\n', 1)[0]:
                offenders.append(f'{path.relative_to(ROOT)}:{symbol}')
    assert offenders == []
