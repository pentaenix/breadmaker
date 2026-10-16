from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_remainder_source, frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1034_release_identity_and_final_javascript_load_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,4)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    expected_tail=[
        '/static/js/features/maps/metro.js',
        '/static/js/features/split_view.js',
        '/static/js/features/manuscript/imports.js',
        '/static/js/features/checkpoints/workspace.js',
        '/static/js/features/durability/history.js',
        '/static/js/features/notes/comments.js',
        '/static/js/core/quick_open.js',
        '/static/app.js',
    ]
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    assert loaded[-len(expected_tail):]==expected_tail


def test_v1034_app_js_is_startup_only_not_a_feature_remainder():
    app=frontend_remainder_source(ROOT)
    assert len(app.splitlines()) <= 20
    assert 'window.__breadmakerBootPromise=boot()' in app
    assert 'installBreadReviewDropTarget()' in app
    for heading in (
        '// -------------------- split view --------------------',
        '// -------------------- append document tabs --------------------',
        '// -------------------- import manuscript --------------------',
        '// -------------------- checkpoints --------------------',
        '// -------------------- history / cuts / principles --------------------',
        '// -------------------- Notes 4.15.4 --------------------',
        '// -------------------- quick open --------------------',
    ):
        assert heading not in app
        assert heading in frontend_source(ROOT)


def test_v1034_remaining_frontend_domains_have_canonical_owners():
    expected={
        'js/features/split_view.js',
        'js/features/manuscript/imports.js',
        'js/features/checkpoints/workspace.js',
        'js/features/checkpoints/README.md',
        'js/features/durability/history.js',
        'js/features/durability/README.md',
        'js/features/notes/comments.js',
        'js/features/notes/README.md',
        'js/core/quick_open.js',
    }
    present={str(path.relative_to(STATIC)).replace('\\','/') for path in STATIC.rglob('*') if path.is_file()}
    assert expected <= present
    assert (STATIC/'js/features/split_view.js').read_text(encoding='utf-8').startswith('// -------------------- split view --------------------')
    assert '// -------------------- checkpoints --------------------' in (STATIC/'js/features/checkpoints/workspace.js').read_text(encoding='utf-8')
    assert '// -------------------- quick open --------------------' in (STATIC/'js/core/quick_open.js').read_text(encoding='utf-8')


def test_v1034_docs_freeze_javascript_ownership_before_css_refactor():
    docs=(STATIC/'js/README.md').read_text(encoding='utf-8')
    agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
    assert 'startup shim only' in docs
    assert 'features/split_view.js' in docs
    assert 'features/checkpoints/workspace.js' in docs
    assert 'features/durability/history.js' in docs
    assert 'features/notes/comments.js' in docs
    assert 'core/quick_open.js' in docs
    assert 'CSS remains intentionally monolithic' in docs
    assert 'startup shim only' in agents
