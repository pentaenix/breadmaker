from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
API=ROOT/'src'/'api'

# These are the only feature -> backend.core edges intentionally left in 10.2.5.
# They are stateful lifecycle/activation boundaries and will be moved in a later
# backend-only checkpoint rather than copied across modules as scalars.
_ALLOWED_CORE_EDGES={
    '_BACKUP_DESTINATION_RECONCILE',
    '_BACKUP_DESTINATION_RECONCILE_LOCK',
    '_BACKUP_DESTINATION_RECONCILE_PROJECTS',
    '_BACKUP_DESTINATION_WAKE',
    '_PORTABLE_REVIEW_PASSPHRASE',
    '_TEMP_PORTABLE_REVIEWS',
    '_TEMP_PORTABLE_REVIEW_LOCK',
    '_TemporaryPortableReviewState',
    '_activate_project',
    '_current_portable_review_path',
    '_current_recovery_mode',
    '_ensure_recovery_scheduler',
    '_queue_remote_history',
    'commit',
}


def test_v1025_release_and_clean_home_script_use_canonical_storage_owner():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,5)
    source=(ROOT/'tests'/'test_v781.py').read_text(encoding='utf-8')
    assert 'import app, storage' not in source
    assert 'from storage import core as storage' not in source
    assert 'from storage import context as storage_context' in source


def test_v1025_feature_modules_import_platform_symbols_from_canonical_owners():
    offenders=[]
    for path in API.glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        for node in tree.body:
            if isinstance(node,ast.ImportFrom) and node.module=='backend.core':
                for alias in node.names:
                    if alias.name not in _ALLOWED_CORE_EDGES:
                        offenders.append((path.name,node.lineno,alias.name))
    assert offenders==[]


def test_v1025_platform_modules_are_independently_importable_and_api_neutral():
    modules=(
        'backend.environment',
        'backend.configuration',
        'backend.content',
        'backend.data_access',
        'backend.request_context',
    )
    for name in modules:
        module=importlib.import_module(name)
        assert module.__name__==name
        source=Path(module.__file__).read_text(encoding='utf-8')
        assert 'from api.' not in source and 'import api.' not in source
