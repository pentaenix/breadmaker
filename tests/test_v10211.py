from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / 'src' / 'workspace'


def test_v10211_workspace_source_contracts_follow_canonical_owners():
    assert tuple(map(int, (ROOT / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10, 2, 11)
    constants = (WORKSPACE / 'constants.py').read_text(encoding='utf-8')
    providers = (WORKSPACE / 'providers.py').read_text(encoding='utf-8')
    backups = '\n'.join(path.read_text(encoding='utf-8') for path in sorted((WORKSPACE / 'backups').glob('*.py')))
    boundary = (WORKSPACE / '__init__.py').read_text(encoding='utf-8')

    assert 'APP_SCHEMA_VERSION = 42' in constants
    assert 'from backups.providers.webdav import' in providers
    assert 'from backups.providers.google_drive import' in providers
    assert 'from backups.retention import' in backups
    assert 'product_config' not in boundary


def test_v10211_backup_volume_metadata_is_resolved_through_filesystem_owner():
    path = WORKSPACE / 'backups' / 'destinations.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    direct_imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'filesystem':
            direct_imports.extend(alias.name for alias in node.names)
    assert '_volume_metadata' not in direct_imports

    source = path.read_text(encoding='utf-8')
    assert 'from workspace import filesystem as workspace_fs' in source
    assert 'workspace_fs._volume_metadata(' in source
