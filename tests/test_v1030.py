from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1030_release_identity_and_modular_frontend_runtime():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,3,0)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    required=[
        '/static/js/core/i18n.js',
        '/static/js/core/runtime.js',
        '/static/js/core/state.js',
        '/static/js/core/ui.js',
        '/static/js/core/routing.js',
        '/static/js/features/reviews/reader.js',
    ]
    positions=[loaded.index(path) for path in required]
    assert positions==sorted(positions)
    assert loaded[-1]=='/static/app.js'
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()


def test_v1030_app_js_is_a_shrinking_remainder_not_frontend_source_boundary():
    app=(STATIC/'app.js').read_text(encoding='utf-8')
    assert len(app.splitlines()) < 5500
    assert '// -------------------- reviews --------------------' not in app
    assert 'function ensureI18n(' not in app
    assert 'const state = {' not in app
    assert '// -------------------- reviews --------------------' not in app
    combined=frontend_source(ROOT)
    assert 'function ensureI18n(' in combined
    assert 'const state = {' in combined
    assert '// -------------------- reviews --------------------' in combined
    assert '// -------------------- crash journal --------------------' in combined


def test_v1030_frontend_source_contracts_do_not_require_the_monolith():
    import ast
    contracts=(ROOT/'tests'/'source_contracts.py').read_text(encoding='utf-8')
    assert 'def frontend_source(' in contracts
    assert 'index.html' in contracts
    direct=[]
    for path in (ROOT/'tests').glob('test_*.py'):
        if path.name=='test_v1030.py':
            continue
        source=path.read_text(encoding='utf-8')
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute):
                continue
            if node.func.attr not in {'read_text','read'}:
                continue
            target=ast.get_source_segment(source,node.func.value) or ''
            if 'app.js' in target and 'frontend_source_from_app_path' not in target:
                direct.append(path.name)
                break
        if "['static/index.html','static/app.js','static/styles.css']" in source:
            direct.append(path.name)
    assert sorted(set(direct))==[]


def test_v1030_frontend_ownership_docs_exist():
    docs=(STATIC/'js'/'README.md').read_text(encoding='utf-8')
    assert 'build-step-free' in docs
    assert '`core/i18n.js`' in docs
    assert '`features/reviews/`' in docs
    assert '`../app.js`' in docs and 'startup shim' in docs
