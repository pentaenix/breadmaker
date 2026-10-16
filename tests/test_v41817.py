from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v41817_compact_scene_labels_are_name_based_not_position_based():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'function sceneRailLabel(scene)' in js
    assert "title.match(/^Scene\\s+(\\d+)$/i)" in js
    assert "escapeHtml(sceneRailLabel(scene))" in js
    assert '<span class="scene-short">${sceneIndex+1}</span>' not in js


def test_v41817_release_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '### 4.18.17 Compact Scene rail labels' in readme
    assert '### 4.18.17 Compact Scene rail label contract' in architecture
