from __future__ import annotations

import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'static'


def _read(relative: str) -> str:
    return (STATIC/relative).read_text(encoding='utf-8')


def test_v1039_release_identity_and_final_frontend_owner_order():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (10,3,9)
    html=_read('index.html')
    loaded=re.findall(r'<script[^>]+src="([^"?]+)',html)
    expected=[
        '/static/js/core/ui.js',
        '/static/js/core/collections.js',
        '/static/js/core/routing.js',
        '/static/js/core/history.js',
        '/static/js/features/manuscript/view.js',
        '/static/js/features/manuscript/history.js',
        '/static/js/features/canvas/hierarchy.js',
        '/static/js/features/maps/workspace.js',
        '/static/js/features/durability/history.js',
    ]
    positions=[loaded.index(path) for path in expected]
    assert positions==sorted(positions)


def test_v1039_application_history_is_core_owned_but_manuscript_snapshots_remain_feature_owned():
    routing=_read('js/core/routing.js')
    core_history=_read('js/core/history.js')
    manuscript_history=_read('js/features/manuscript/history.js')
    assert 'function currentView(' in routing
    assert 'async function performUndo(' in core_history
    assert 'function withStructuralUndo(' in core_history
    assert 'async function beginStructuralUndo(' in core_history
    assert 'function recordUndo(' in manuscript_history
    assert 'async function restoreEditSnapshot(' in manuscript_history
    assert 'function withStructuralUndo(' not in manuscript_history
    assert 'function currentView(' not in manuscript_history


def test_v1039_workspace_restore_and_collection_helpers_have_correct_owners():
    manuscript_view=_read('js/features/manuscript/view.js')
    canvas=_read('js/features/canvas/hierarchy.js')
    maps=_read('js/features/maps/workspace.js')
    collections=_read('js/core/collections.js')
    durability=_read('js/features/durability/history.js')
    manuscript_history=_read('js/features/manuscript/history.js')
    assert 'async function restoreManuscriptUIState(' in manuscript_view
    assert 'async function restoreManuscriptUIState(' not in canvas
    assert 'function compactRailLabel(' in collections
    assert 'function appendCollectionQuickAdd(' in collections
    assert 'function canvasRailLabel(' in canvas
    assert 'function mapRailLabel(' in maps
    assert 'function mapLineRailLabel(' in maps
    assert 'function mapRailLabel(' not in canvas
    assert 'async function notifyCutsChanged(' in durability
    assert 'async function notifyCutsChanged(' not in manuscript_history


def test_v1039_docs_mark_frontend_architecture_freeze_and_shared_owners():
    docs=_read('js/README.md')
    agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '10.3.9' in docs
    assert '`core/history.js`' in docs
    assert '`core/collections.js`' in docs
    assert 'frontend architecture freeze' in agents.lower()
    assert '10.3.9' in architecture
    assert 'frontend architecture freeze' in architecture.lower()
