from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_remainder_source, frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1032_release_identity_and_frontend_load_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,2)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    expected_order=[
        '/static/js/features/manuscript/find_replace.js',
        '/static/js/features/tasks.js',
        '/static/js/features/linking.js',
        '/static/js/features/canvas/hierarchy.js',
        '/static/js/features/canvas/nodes.js',
        '/static/js/features/canvas/connectors.js',
    ]
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    positions=[loaded.index(path) for path in expected_order]
    assert positions==sorted(positions)


def test_v1032_app_js_remainder_keeps_shrinking_after_canvas_extraction():
    app=frontend_remainder_source(ROOT)
    assert len(app.splitlines()) < 1600
    for heading in (
        '// -------------------- tasks --------------------',
        '// -------------------- universal linking --------------------',
        '// -------------------- Canvas hierarchy / UI state --------------------',
        '// -------------------- Canvas nodes --------------------',
        '// -------------------- Canvas connectors --------------------',
    ):
        assert heading not in app
        assert heading in frontend_source(ROOT)


def test_v1032_tasks_linking_and_canvas_have_canonical_owners():
    tasks=(STATIC/'js/features/tasks.js').read_text(encoding='utf-8')
    linking=(STATIC/'js/features/linking.js').read_text(encoding='utf-8')
    hierarchy=(STATIC/'js/features/canvas/hierarchy.js').read_text(encoding='utf-8')
    nodes=(STATIC/'js/features/canvas/nodes.js').read_text(encoding='utf-8')
    connectors=(STATIC/'js/features/canvas/connectors.js').read_text(encoding='utf-8')
    assert '// -------------------- tasks --------------------' in tasks
    assert '// -------------------- universal linking --------------------' in linking
    assert '// -------------------- Canvas hierarchy / UI state --------------------' in hierarchy
    assert '// -------------------- Canvas nodes --------------------' in nodes
    assert '// -------------------- Canvas connectors --------------------' in connectors
    assert (STATIC/'js/features/canvas/README.md').is_file()
    assert 'const state = {' not in tasks+linking+hierarchy+nodes+connectors


def test_v1032_frontend_docs_describe_tasks_linking_canvas_and_maps_remainder():
    docs=(STATIC/'js/README.md').read_text(encoding='utf-8')
    assert '`features/tasks.js`' in docs
    assert '`features/linking.js`' in docs
    assert '`features/canvas/`' in docs
    assert '1,477' in docs
    assert 'Maps' in docs
