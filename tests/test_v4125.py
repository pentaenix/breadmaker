from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
import json
from pathlib import Path

import pytest
from fastapi import HTTPException


def test_v4125_canvas_typography_config_remains_validated_default_seed(app_module_fixture, client):
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/'config/canvas.json').read_text(encoding='utf-8'))
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 4)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'styles.css?v=5.4.4' in html and 'app.js?v=5.4.4' in html
    assert cfg['version']==1
    assert cfg['node_typography']['content_font_size_pt']==15.5
    assert '#addNodePanelBtn{white-space:nowrap' in css
    assert '--canvas-node-font-size' in css and '--canvas-node-font-size' in js
    assert '--canvas-font-size' in css and 'configuredCanvasTextSize' in js
    assert 'textSize:state.canvasFontSize' in js and 'font:state.canvasFont' in js
    boot=client.get('/api/bootstrap')
    assert boot.status_code==200
    assert boot.json()['canvas_config']==cfg


def test_v4125_canvas_typography_config_fails_closed(app_module_fixture,tmp_path,monkeypatch):
    bad=tmp_path/'canvas.json'
    bad.write_text('{"version":1,"node_typography":{"content_font_size_pt":200}}',encoding='utf-8')
    monkeypatch.setattr(app_module_fixture,'CANVAS_CONFIG',bad)
    with pytest.raises(HTTPException) as exc:
        app_module_fixture._canvas_config_raw()
    assert exc.value.status_code==500
    assert '8 to 36' in str(exc.value.detail)
