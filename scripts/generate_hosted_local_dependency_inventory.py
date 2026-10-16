from __future__ import annotations

"""Freeze direct local-persistence coupling during the serverless rewrite.

This is an inventory, not an endorsement.  Milestones 2-5 are expected to shrink the
`local_engine_dependencies` set as feature domains move behind hosted adapters.
"""

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCAN_ROOTS=(ROOT/'src'/'api',ROOT/'src'/'backend')

# Pure helpers/exceptions are intentionally not persistence authority.
NON_PERSISTENCE_IMPORTS={
    'storage.values',
    'workspace.filesystem',
    'workspace.models',
    'workspace.constants',
}


def _module_name(node: ast.AST) -> str | None:
    if isinstance(node,ast.ImportFrom):return node.module
    if isinstance(node,ast.Import) and node.names:return node.names[0].name
    return None


def _is_local_engine(module:str) -> bool:
    if module in NON_PERSISTENCE_IMPORTS:return False
    return module=='storage' or module.startswith('storage.') or module=='revision_store' or module.startswith('revision_store.') or module=='workspace'


def collect() -> dict:
    local_engine=[];helper_only=[]
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob('*.py')):
            rel=path.relative_to(ROOT).as_posix();tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
            engine=[];helpers=[]
            for node in ast.walk(tree):
                if not isinstance(node,(ast.Import,ast.ImportFrom)):continue
                module=_module_name(node)
                if not module:continue
                if module in NON_PERSISTENCE_IMPORTS:
                    helpers.append(module)
                elif _is_local_engine(module):
                    engine.append(module)
            if engine:local_engine.append({'file':rel,'modules':sorted(set(engine))})
            elif helpers:helper_only.append({'file':rel,'modules':sorted(set(helpers))})
    return {
        'format':1,
        'purpose':'Milestone-1 freeze of direct API/backend dependencies on local SQLite/filesystem/revision implementations.',
        'local_engine_dependencies':local_engine,
        'helper_only_imports':helper_only,
        'allowed_runtime_native_bridges':['native_projects','native_assets','native_history','native_backups'],
        'rule':'New local_engine_dependencies are forbidden. Existing entries are migration debt and may only shrink as hosted adapters replace them.',
    }


def main() -> None:
    target=ROOT/'docs'/'HOSTED_LOCAL_DEPENDENCY_INVENTORY.json'
    target.write_text(json.dumps(collect(),indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(target)


if __name__=='__main__':main()
