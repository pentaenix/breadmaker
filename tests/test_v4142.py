from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v4142_cuts_icon_is_clean_scissors_and_structural_history_refreshes_open_panel():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))

    cuts_button=html[html.index('id="cutsBtn"'):html.index('id="editorsEyeBtn"')]
    assert 'cuts-dashed-line' not in cuts_button
    assert '<circle cx="6" cy="7" r="2.6"/>' in cuts_button
    assert '<circle cx="6" cy="17" r="2.6"/>' in cuts_button
    assert 'M8.25 8.35 20 17M8.25 15.65 20 7' in cuts_button
    assert 'stroke-width:1.8' in css
    assert 'cuts-dashed-line' not in css

    assert 'async function notifyCutsChanged(preferId=null,{removeId=null}={})' in js
    assert js.count('await reopenAfterStructuralRestore(target);await notifyCutsChanged();')==2
