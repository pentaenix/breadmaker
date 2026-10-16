from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def test_v10214_release_removes_dead_storage_compatibility_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,14)
    assert not (SRC/'storage'/'core.py').exists()
    offenders=[]
    for base in (SRC,ROOT/'tests',ROOT/'scripts'):
        for path in base.rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and (node.module=='storage.core' or (node.module=='storage' and any(a.name=='core' for a in node.names))):
                    offenders.append((path.relative_to(ROOT).as_posix(),node.lineno))
                elif isinstance(node,ast.Import) and any(a.name=='storage.core' for a in node.names):
                    offenders.append((path.relative_to(ROOT).as_posix(),node.lineno))
    assert offenders==[]


def test_v10214_app_is_not_a_backend_service_locator():
    app_source=(SRC/'app.py').read_text(encoding='utf-8')
    core_source=(SRC/'backend'/'core.py').read_text(encoding='utf-8')
    assert 'sys.modules[__name__]' not in app_source
    assert 'app=_platform_app' in app_source
    for dead in ('from storage import core','from workspace import workspace','_activate_project','sync_developmental_canvases=','startup=request_pipeline.startup','index=runtime_routes.index'):
        assert dead not in core_source


def test_v10214_dead_backend_aliases_are_removed():
    activation=(SRC/'backend'/'project_activation.py').read_text(encoding='utf-8')
    history=(SRC/'backend'/'history_runtime.py').read_text(encoding='utf-8')
    local=(SRC/'backend'/'local_history.py').read_text(encoding='utf-8')
    assert '_activate_project=activate_project' not in activation.replace(' ','')
    assert '_queue_remote_history=queue_remote_history' not in history.replace(' ','')
    assert '_queue_remote_history = history_runtime.queue_remote_history' not in local
    assert 'history_runtime.queue_remote_history(' in local
