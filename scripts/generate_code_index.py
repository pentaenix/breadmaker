#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
STATIC=ROOT/'static'
OUT=ROOT/'docs'/'CODE_INDEX.json'


def _python_index(path:Path) -> dict:
    text=path.read_text(encoding='utf-8')
    tree=ast.parse(text)
    symbols=[]
    for node in tree.body:
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            continue
        item={'name':node.name,'kind':'class' if isinstance(node,ast.ClassDef) else ('async_function' if isinstance(node,ast.AsyncFunctionDef) else 'function'),'line':node.lineno}
        routes=[]
        for dec in getattr(node,'decorator_list',[]):
            if isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and isinstance(dec.func.value,ast.Name) and dec.func.value.id in {'app','router'} and dec.func.attr in {'get','post','put','patch','delete'}:
                route={'method':dec.func.attr.upper()}
                if dec.args and isinstance(dec.args[0],ast.Constant):route['path']=dec.args[0].value
                routes.append(route)
        if routes:item['routes']=routes
        symbols.append(item)
    return {'file':path.relative_to(ROOT).as_posix(),'lines':len(text.splitlines()),'symbols':symbols}


def _javascript_index(path:Path) -> dict:
    text=path.read_text(encoding='utf-8')
    patterns=[
        re.compile(r'(?m)^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\('),
        re.compile(r'(?m)^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^\n]*?\)\s*=>'),
    ]
    found={}
    for pat in patterns:
        for m in pat.finditer(text):
            found.setdefault(m.group(1),text.count('\n',0,m.start())+1)
    return {'file':path.relative_to(ROOT).as_posix(),'lines':len(text.splitlines()),'symbols':[{'name':n,'kind':'function','line':line} for n,line in sorted(found.items(),key=lambda x:x[1])]}


def main() -> None:
    py=[_python_index(p) for p in sorted(SRC.rglob('*.py')) if '__pycache__' not in p.parts]
    js=[_javascript_index(p) for p in sorted(STATIC.rglob('*.js'))]
    routes=[]
    for path in sorted(SRC.rglob('*.py')):
        if '__pycache__' in path.parts:continue
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            for dec in getattr(node,'decorator_list',[]):
                if isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and isinstance(dec.func.value,ast.Name) and dec.func.value.id in {'app','router'} and dec.func.attr in {'get','post','put','patch','delete'}:
                    item={'method':dec.func.attr.upper(),'function':node.name,'file':path.relative_to(ROOT).as_posix(),'line':node.lineno}
                    if dec.args and isinstance(dec.args[0],ast.Constant):item['path']=dec.args[0].value
                    routes.append(item)
    routes.sort(key=lambda r:(r['file'],r['line'],r['method']))
    payload={
        'schema':1,
        'purpose':'Fast symbol and route navigation for contributors and coding agents.',
        'generated_by':'scripts/generate_code_index.py',
        'python':py,
        'javascript':js,
        'routes':routes,
        'route_decorator_count':len(routes),
    }
    OUT.write_text(json.dumps(payload,indent=2,sort_keys=False)+'\n',encoding='utf-8')
    print(f'wrote {OUT.relative_to(ROOT)} ({len(routes)} route decorators)')

if __name__=='__main__':main()
