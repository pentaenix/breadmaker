from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_v640_release_contract():
    version=(ROOT/'VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (6,4,0)
    app=frontend_source_from_app_path(ROOT/'static'/'app.js')
    assert 'nextMapItemDefaultName' in app
    assert 'openPendingMapItemLinker' in app
    assert 'requestAnimationFrame(autoScroll)' in app
    assert 'data-pending-map-link-delete' in app
