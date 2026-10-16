from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = frontend_source_from_app_path(ROOT / 'static/app.js')
INDEX = (ROOT / 'static/index.html').read_text(encoding='utf-8')




def test_v722_shift_shape_and_line_contract():
    # Circle remains a supported legacy persisted shape, but is no longer a creation tool.
    figure_tools = re.search(r"const FIGURE_SHAPES=\[(.*?)\];", APP)
    assert figure_tools
    tools = figure_tools.group(1)
    assert "id:'rect'" in tools and "id:'triangle'" in tools and "id:'oval'" in tools and "id:'line'" in tools
    assert "id:'circle'" not in tools

    # Rectangle/Oval use Shift to make their draw bounds square, and line drawing/editing
    # shares one snapping helper rather than having separate behavior paths.
    assert "constrainSquare&&['rect','oval'].includes(shape)" in APP
    assert "function snapLineFigureEndpoint(origin,point)" in APP
    assert "const step=Math.PI/4" in APP
    assert "lineFigureEndpoint(start,rawEnd,e.shiftKey)" in APP
    assert "lineFigureEndpoint(fixed,raw,e.shiftKey)" in APP
    assert "e.shiftKey&&['rect','oval'].includes(node.shape)" in APP


def test_v722_existing_circle_data_remains_compatible():
    # Do not strand old projects just because Circle was removed from the maker palette.
    app_py = backend_source(ROOT)
    project_json = (ROOT / 'src' / 'formats' / 'project_json' / 'validation.py').read_text(encoding='utf-8')
    assert "FIGURE_NODE_SHAPES={'rect','circle','triangle','oval','line'}" in app_py
    assert '{"rect","circle","triangle","oval","line"}' in project_json


def test_v722_locales_remain_key_and_placeholder_compatible():
    en = json.loads((ROOT / 'locales/en.json').read_text(encoding='utf-8'))
    token = re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for path in sorted((ROOT / 'locales').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert set(data['strings']) == set(en['strings']), path.name
        assert data.get('source_index', {}) == en.get('source_index', {}), path.name
        for key, value in en['strings'].items():
            assert sorted(token.findall(value)) == sorted(token.findall(data['strings'][key])), (path.name, key)
