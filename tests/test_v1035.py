from __future__ import annotations

import re
from pathlib import Path

from source_contracts import frontend_remainder_source, frontend_source

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def test_v1035_release_identity_and_cohesive_frontend_load_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,5)
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    expected=[
        '/static/js/core/typography.js',
        '/static/js/core/ui.js',
        '/static/js/features/reviews/view.js',
        '/static/js/features/reviews/reader.js',
        '/static/js/features/reviews/workspace.js',
        '/static/js/features/reviews/details.js',
        '/static/js/features/reviews/panels.js',
        '/static/js/features/reviews/lifecycle.js',
        '/static/js/features/projects/menu.js',
        '/static/js/features/projects/settings.js',
        '/static/js/features/projects/backups.js',
        '/static/js/features/projects/lifecycle.js',
        '/static/js/features/projects/export_recovery.js',
        '/static/js/features/manuscript/view.js',
        '/static/js/features/manuscript/bookmarks.js',
        '/static/js/features/manuscript/rendering.js',
        '/static/js/features/manuscript/history.js',
        '/static/js/features/manuscript/workspace.js',
        '/static/js/features/manuscript/persistence.js',
        '/static/js/features/maps/view.js',
    ]
    positions=[loaded.index(path) for path in expected]
    assert positions==sorted(positions)
    assert loaded[-1]=='/static/app.js'


def test_v1035_retires_mixed_owner_frontend_monoliths():
    assert not (STATIC/'js/features/reviews.js').exists()
    assert not (STATIC/'js/features/projects.js').exists()
    ui=(STATIC/'js/core/ui.js').read_text(encoding='utf-8')
    rendering=(STATIC/'js/features/manuscript/rendering.js').read_text(encoding='utf-8')
    assert 'function toggleChapterBookmark(' not in ui
    assert 'function applyReviewerTextSize(' not in ui
    assert 'function applyMapRulerScale(' not in ui
    assert 'async function performUndo(' not in rendering
    assert 'async function flushSave(' not in rendering
    combined=frontend_source(ROOT)
    for symbol in (
        'function toggleChapterBookmark(',
        'function applyReviewerTextSize(',
        'function applyMapRulerScale(',
        'async function performUndo(',
        'async function flushSave(',
    ):
        assert symbol in combined


def test_v1035_frontend_packages_are_real_owners_not_facades():
    expected={
        'js/core/typography.js',
        'js/features/reviews/README.md',
        'js/features/reviews/reader.js',
        'js/features/reviews/workspace.js',
        'js/features/reviews/details.js',
        'js/features/reviews/panels.js',
        'js/features/reviews/lifecycle.js',
        'js/features/projects/README.md',
        'js/features/projects/menu.js',
        'js/features/projects/settings.js',
        'js/features/projects/backups.js',
        'js/features/projects/lifecycle.js',
        'js/features/projects/export_recovery.js',
        'js/features/manuscript/bookmarks.js',
        'js/features/manuscript/view.js',
        'js/features/manuscript/history.js',
        'js/features/manuscript/workspace.js',
        'js/features/manuscript/persistence.js',
        'js/features/maps/view.js',
    }
    present={str(path.relative_to(STATIC)).replace('\\','/') for path in STATIC.rglob('*') if path.is_file()}
    assert expected <= present
    assert 'function renderReviews()' in (STATIC/'js/features/reviews/workspace.js').read_text(encoding='utf-8')
    assert 'async function openBackups(' in (STATIC/'js/features/projects/backups.js').read_text(encoding='utf-8')
    assert 'async function performUndo(' in (STATIC/'js/core/history.js').read_text(encoding='utf-8')
    assert 'function recordUndo(' in (STATIC/'js/features/manuscript/history.js').read_text(encoding='utf-8')
    assert 'async function flushSave(' in (STATIC/'js/features/manuscript/persistence.js').read_text(encoding='utf-8')
    assert len(frontend_remainder_source(ROOT).splitlines()) <= 20


def test_v1035_docs_match_the_current_frontend_ownership_tree():
    docs=(STATIC/'js/README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '10.3.5' in docs
    assert '`features/reviews/`' in docs
    assert '`features/projects/`' in docs
    assert '`core/typography.js`' in docs
    assert '`history.js`' in (STATIC/'js/features/manuscript/README.md').read_text(encoding='utf-8')
    assert 'CSS remains intentionally monolithic' in docs
    assert 'frontend ownership cleanup' in architecture
