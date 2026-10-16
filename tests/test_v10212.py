from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / 'src' / 'workspace'


def test_v10212_backup_platform_discovery_uses_filesystem_owner_at_call_time():
    path = WORKSPACE / 'backups' / 'destinations.py'
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)

    direct_imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'filesystem':
            direct_imports.extend(alias.name for alias in node.names)

    assert '_volume_metadata' not in direct_imports
    assert '_mounted_volume_roots' not in direct_imports
    assert 'from workspace import filesystem as workspace_fs' in source
    assert 'workspace_fs._volume_metadata(' in source
    assert 'workspace_fs._mounted_volume_roots()' in source


def test_v10212_historical_removable_volume_tests_patch_canonical_owner():
    for name in ('test_v788.py', 'test_v789.py'):
        source = (ROOT / 'tests' / name).read_text(encoding='utf-8')
        assert "from workspace import filesystem as workspace_fs" in source
        assert "workspace_backups,'_volume_metadata'" not in source
    source = (ROOT / 'tests' / 'test_v788.py').read_text(encoding='utf-8')
    assert "workspace_backups,'_mounted_volume_roots'" not in source
