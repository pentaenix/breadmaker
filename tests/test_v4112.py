from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4112_checkpoints_are_native_manuscript_ui_not_modal():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'id="checkpointPanel"' in html
    assert 'id="checkpointCompareToolbar"' in html
    assert 'id="checkpointSplitSelect"' in html
    assert "showModal('Checkpoints'" not in js
    assert 'checkpoint-modal' not in css
    assert 'openCheckpointSplit' in js
    assert '<option value="checkpoint">Checkpoint</option>' in js
    assert "$('#editor').getAttribute" not in js  # no checkpoint-specific fake editor replacement
    assert '.checkpoint-panel' in css and '.checkpoint-context-toolbar' in css


def test_v4112_live_manuscript_is_the_left_side_of_checkpoint_comparison():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    assert "const left=$('#documentScroller'),right=$('#splitBody')" in js
    assert "state.split={type:'checkpoint'" in js
    assert "$('#checkpointSplitShowDiff').onchange" in js
    assert 'clearCheckpointLiveHighlights' in js
    assert 'checkpoint-live-change' in js
    assert 'closeCheckpointPanel();await renderCheckpointSplit()' in js
