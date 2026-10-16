from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v4176_release_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'v4.17.6 Layout correction' in css
    assert '@media (min-width:641px)' in css
    assert 'position:static!important' in css
    assert '.document-toolbar{' in css and 'overflow-x:auto' in css
    assert '.split-pane{' in css and 'min-width:0' in css and 'flex:0 1 39%' in css
    assert '.checkpoint-list{flex:1 1 auto;max-height:none;min-height:0}' in css


def test_v4176_keeps_4175_checkpoint_apply_contract():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'id="checkpointSplitApply"' in html
    assert 'Apply…' in html
    assert 'checkpointApplyPlan' in js
    assert 'applyCheckpointFromUI' in js
    assert 'checkpointSplitRestore' not in html
    assert 'checkpointTags' not in html
