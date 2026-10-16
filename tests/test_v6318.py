from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v6318_map_details_uses_current_control_renderer():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    html=(root/'static/index.html').read_text(encoding='utf-8')
    version=(root/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (6,3,18)
    details=js[js.index('async function configureCurrentMap()'):js.index('async function autoArrangeCurrentMap')]
    assert 'renderMapControls()' in details
    assert 'syncMapControls' not in js
