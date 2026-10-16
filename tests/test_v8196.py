from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8196_image_clipboard_snapshots_current_inspector_metadata():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8, 19, 6)
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    assert "const sizeField=$('#manuscriptImageSize')" in js
    assert "footerField=$('#manuscriptImageFooter')" in js
    assert "descriptionField=$('#manuscriptImageDescription')" in js
    assert "image_width:width" in js
    assert "image_footer:String(footer||'')" in js
    assert "image_description:String(description||'')" in js
