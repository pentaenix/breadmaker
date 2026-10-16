from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
API=ROOT/'src/api'
PLATFORM_ORDER=('project_management',)
ORDER=(
    'manuscript','annotations','comments','reviews','tasks','canvas','icon_markers','maps',
    'workspace_tools','find_replace','checkpoints','durability','import_export',
)
_INTERNAL_PREFIXES=('backend','storage','workspace','runtime','auth','security','formats','backups','revision_store','reliability')


def module_paths(module:str) -> list[Path]:
    path=API/f'{module}.py'
    if path.is_file():return [path]
    package=API/module
    if package.is_dir():return [p for p in sorted(package.glob('*.py')) if p.name!='__init__.py']
    raise FileNotFoundError(module)


def declarations(path:Path) -> tuple[list[dict[str,str]],list[dict[str,str]]]:
    tree=ast.parse(path.read_text(encoding='utf-8'))
    platform=[];features=[]
    for node in tree.body:
        if isinstance(node,ast.ImportFrom):
            module=str(node.module or '')
            if module and (module in _INTERNAL_PREFIXES or module.startswith(tuple(prefix+'.' for prefix in _INTERNAL_PREFIXES))):
                for alias in node.names:platform.append({'module':module,'symbol':alias.name,'local':alias.asname or alias.name})
            continue
        if isinstance(node,ast.Import):
            for alias in node.names:
                module=str(alias.name or '')
                if module and (module in _INTERNAL_PREFIXES or module.startswith(tuple(prefix+'.' for prefix in _INTERNAL_PREFIXES))):
                    platform.append({'module':module,'symbol':'','local':alias.asname or module.split('.')[0]})
            continue
        if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name):continue
        value=node.value;target=node.targets[0].id
        if isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id=='feature_call' and len(value.args)==2 and all(isinstance(x,ast.Constant) for x in value.args):
            assert target==value.args[1].value
            features.append({'symbol':target,'owner':str(value.args[0].value)})
    return platform,features


def _domain(module:str) -> dict:
    platform=[];features=[]
    for path in module_paths(module):
        p,f=declarations(path);platform.extend(p);features.extend(f)
    platform.sort(key=lambda row:(row['module'],row['symbol'],row['local']))
    features.sort(key=lambda row:(row['symbol'],row['owner']))
    return {'module':module,'platform_dependencies':platform,'feature_dependencies':features}

def build() -> dict:
    platform_domains=[_domain(module) for module in PLATFORM_ORDER]
    domains=[]
    for module in ORDER:
        domains.append(_domain(module))
    return {'version':5,'description':'Explicit backend dependencies. Platform and feature package domains aggregate focused owners; platform dependencies point to canonical modules and feature dependencies are lazy calls to canonical feature owners.','platform_domains':platform_domains,'domains':domains}

if __name__=='__main__':
    target=API/'dependency_manifest.json';target.write_text(json.dumps(build(),indent=2)+'\n',encoding='utf-8')
