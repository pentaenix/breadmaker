from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v4177_release_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'id="splitResizer"' in html and 'role="separator"' in html
    assert "SPLIT_RATIO_KEY='breadmaker.splitRatio.v1'" in js
    assert 'splitPaneRatio=.5' in js
    assert 'splitUsableWidth' in js and 'applySplitRatio' in js and 'wireSplitResizer' in js
    assert "grip.addEventListener('dblclick'" in js
    assert 'v4.17.7 Split View sizing' in css
    assert '.split-resizer{' in css and 'cursor:col-resize' in css
    assert '.checkpoint-context-toolbar,.history-context-toolbar{' in css and 'overflow-x:auto' in css
    assert '.checkpoint-more{' in css and 'height:34px' in css and 'border-radius:4px' in css


def test_v4177_keeps_checkpoint_apply_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'id="checkpointSplitApply"' in html
    assert 'Apply…' in html
    assert 'checkpointApplyPlan' in js
    assert 'applyCheckpointFromUI' in js
