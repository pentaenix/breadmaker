from __future__ import annotations

from pathlib import Path

from source_contracts import frontend_styles_source

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'


def test_v1038_release_identity_and_shared_component_catalog():
    version = tuple(map(int, (ROOT / 'VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (10, 3, 8)
    catalog = (STATIC / 'css' / 'shared' / 'COMPONENTS.md').read_text(encoding='utf-8')
    assert 'Reuse before create' in catalog
    assert 'toolbar-button icon-only tool-icon-button' in catalog
    assert 'bare-button ui-close-button' in catalog
    assert 'visually similar copied component' in catalog


def test_v1038_cross_workspace_toolbar_icon_is_shared_not_manuscript_owned():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    css = frontend_styles_source(ROOT)
    assert 'manuscript-tool-button' not in html
    assert '.manuscript-tool-button' not in css
    assert '.tool-icon-button{' in css
    for control in ('commentsBtn', 'addNodePanelBtn', 'mapMetroViewBtn', 'reviewParticipantsBtn', 'reviewerBookmarkBtn'):
        start = html.index(f'id="{control}"')
        snippet = html[start:start + 360]
        assert 'tool-icon-button' in snippet


def test_v1038_shared_context_menu_renderer_is_owned_by_core_ui():
    core = (STATIC / 'js' / 'core' / 'ui.js').read_text(encoding='utf-8')
    manuscript = (STATIC / 'js' / 'features' / 'manuscript' / 'scenes.js').read_text(encoding='utf-8')
    assert 'function openContextMenu(x,y,items,{anchorRect=null}={})' in core
    assert 'function contextMenuItems(container,items,root)' in core
    assert 'function openContextMenu(x,y,items,{anchorRect=null}={})' not in manuscript


def test_v1038_agents_require_component_reuse_before_creation():
    agents = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
    assert 'reuse-before-create' in agents
    assert 'static/css/shared/COMPONENTS.md' in agents
    assert 'tool-icon-button' in agents


def test_v1038_verifier_distinguishes_completed_failure_from_oldest_running_job():
    source = (ROOT / 'scripts' / 'verify_release.py').read_text(encoding='utf-8')
    assert 'last fail' in source
    assert 'last_failed: str | None = None' in source
    assert 'result.child_failures[0][0] if result.child_failures else result.job.name' in source
    assert 'oldest ' in source
