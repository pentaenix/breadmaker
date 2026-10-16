from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_remainder_source, frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1033_release_identity_and_maps_load_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,3)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    expected_order=[
        '/static/js/features/canvas/connectors.js',
        '/static/js/features/maps/workspace.js',
        '/static/js/features/maps/metro.js',
    ]
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    positions=[loaded.index(path) for path in expected_order]
    assert positions==sorted(positions)
    assert loaded[-1]=='/static/app.js'


def test_v1033_maps_and_metro_have_canonical_frontend_owners():
    workspace=(STATIC/'js/features/maps/workspace.js').read_text(encoding='utf-8')
    metro=(STATIC/'js/features/maps/metro.js').read_text(encoding='utf-8')
    remainder=frontend_remainder_source(ROOT)
    combined=frontend_source(ROOT)
    assert workspace.startswith('// -------------------- Maps: narrative transit maps --------------------')
    assert metro.startswith('// -------------------- Metro View: printable derived poster --------------------')
    assert '// -------------------- Maps: narrative transit maps --------------------' not in remainder
    assert '// -------------------- Metro View: printable derived poster --------------------' not in remainder
    assert '// -------------------- Maps: narrative transit maps --------------------' in combined
    assert '// -------------------- Metro View: printable derived poster --------------------' in combined
    assert len(remainder.splitlines()) < 800
    assert '// -------------------- Maps: narrative transit maps --------------------' not in remainder


def test_v1033_maps_docs_describe_the_frontend_ownership_boundary():
    docs=(STATIC/'js/README.md').read_text(encoding='utf-8')
    maps=(STATIC/'js/features/maps/README.md').read_text(encoding='utf-8')
    assert '`features/maps/`' in docs
    assert '721' in docs
    assert 'workspace.js' in maps and 'metro.js' in maps
