from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v633_minimal_map_labels_do_not_enter_collision_staggering():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    render_start=js.index('function renderMapItemLabel(')
    render_end=js.index('function mapLabelRect(',render_start)
    render=js[render_start:render_end]
    resolver_start=js.index('function resolveMapLabelCollisions(')
    resolver_end=js.index('function mapRenderedLabelOffset(',resolver_start)
    resolver=js[resolver_start:resolver_end]

    # Automatic Minimal labels all start on one shared side/level.
    assert "labelMode==='minimal'?-34" in render
    # Minimal mode deliberately skips the collision staggerer. Full mode still
    # uses the existing resolver and may move labels to additional levels.
    assert "labelMode==='hidden'||labelMode==='minimal'" in resolver
