from __future__ import annotations
from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
from source_contracts import backend_source, storage_source,frontend_source_from_app_path


def test_v453_maps_share_workspace_neutral_background_and_dark_brand_b_is_white():
    js=frontend_source_from_app_path('static/app.js')
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    app=backend_source(Path('.'))
    storage=storage_source(Path('.'))
    html=open('static/index.html',encoding='utf-8').read()
    version=open('VERSION',encoding='utf-8').read().strip()

    assert "MAP_DEFAULT_BG='#f3f3f1', MAP_DARK_BG='#191a19'" in js
    assert ':root{--bg:#f3f3f1;' in css
    assert ':root[data-theme=dark]{--bg:#191a19;' in css
    assert ':root[data-theme=dark] .brand-mark{background:#000;color:#fff}' in css
    assert "'#f3f3f1'" in app
    assert "DEFAULT '#f3f3f1'" in storage
    assert '#f8f7f2' not in js
    assert '#20211f' not in js
    assert tuple(map(int,version.split('.'))) >= (4,5,3)
    assert 'styles.css?v=' in html and 'app.js?v=' in html
