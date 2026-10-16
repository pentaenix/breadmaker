from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_remainder_source, frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1031_release_identity_and_frontend_load_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,1)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    expected_order=[
        '/static/js/core/recovery.js',
        '/static/js/core/boot.js',
        '/static/js/features/projects/menu.js',
        '/static/js/features/manuscript/navigation.js',
        '/static/js/features/manuscript/rendering.js',
        '/static/js/features/manuscript/editing.js',
        '/static/js/features/manuscript/scenes.js',
        '/static/js/features/manuscript/annotations.js',
        '/static/js/features/manuscript/find_replace.js',
    ]
    positions=[loaded.index(path) for path in expected_order]
    assert positions==sorted(positions)
    assert loaded[-1]=='/static/app.js'


def test_v1031_app_js_is_only_the_unextracted_feature_remainder():
    app=frontend_remainder_source(ROOT)
    assert len(app.splitlines()) < 2800
    for heading in (
        '// -------------------- crash journal --------------------',
        '// -------------------- lease / boot --------------------',
        '// -------------------- projects --------------------',
        '// -------------------- documents and chapter navigation --------------------',
        '// -------------------- manuscript rendering --------------------',
        '// -------------------- manuscript editing / selection --------------------',
        '// -------------------- selection actions / scenes --------------------',
        '// -------------------- markers / annotations --------------------',
        '// -------------------- Find / replace: inline navigator --------------------',
    ):
        assert heading not in app
    combined=frontend_source(ROOT)
    for heading in (
        '// -------------------- crash journal --------------------',
        '// -------------------- lease / boot --------------------',
        '// -------------------- projects --------------------',
        '// -------------------- manuscript rendering --------------------',
        '// -------------------- tasks --------------------',
    ):
        assert heading in combined


def test_v1031_frontend_owners_exist_and_are_not_compatibility_facades():
    expected={
        'js/core/recovery.js',
        'js/core/boot.js',
        'js/features/projects/menu.js',
        'js/features/projects/settings.js',
        'js/features/projects/backups.js',
        'js/features/projects/lifecycle.js',
        'js/features/projects/export_recovery.js',
        'js/features/projects/README.md',
        'js/features/manuscript/navigation.js',
        'js/features/manuscript/rendering.js',
        'js/features/manuscript/editing.js',
        'js/features/manuscript/scenes.js',
        'js/features/manuscript/annotations.js',
        'js/features/manuscript/find_replace.js',
        'js/features/manuscript/README.md',
    }
    present={str(path.relative_to(STATIC)).replace('\\','/') for path in STATIC.rglob('*') if path.is_file()}
    assert expected <= present
    assert 'function boot()' in (STATIC/'js/core/boot.js').read_text(encoding='utf-8')
    assert 'function renderEditor()' in (STATIC/'js/features/manuscript/rendering.js').read_text(encoding='utf-8')
    assert 'async function selectChapter(' in (STATIC/'js/features/manuscript/rendering.js').read_text(encoding='utf-8')
    assert 'function makeBlockWrap(' in (STATIC/'js/features/manuscript/rendering.js').read_text(encoding='utf-8')
    assert 'const state = {' not in (STATIC/'js/features/manuscript/rendering.js').read_text(encoding='utf-8')


def test_v1031_frontend_docs_describe_the_current_ownership_tree():
    docs=(STATIC/'js'/'README.md').read_text(encoding='utf-8')
    manuscript=(STATIC/'js/features/manuscript/README.md').read_text(encoding='utf-8')
    assert '`core/recovery.js`' in docs
    assert '`core/boot.js`' in docs
    assert '`features/projects/`' in docs
    assert '`features/manuscript/`' in docs
    assert '2,579' in docs
    assert 'navigation.js' in manuscript and 'annotations.js' in manuscript
