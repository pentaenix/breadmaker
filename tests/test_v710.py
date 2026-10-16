from __future__ import annotations
from source_contracts import frontend_styles_source_from_styles_path
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
INDEX=(ROOT/'static/index.html').read_text(encoding='utf-8')


def test_v710_release_and_language_set():
    locales={p.stem for p in (ROOT/'locales').glob('*.json')}
    assert locales=={'en','es','fr','it','ja','pt-BR','zh-CN'}
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for code in locales:
        data=json.loads((ROOT/'locales'/f'{code}.json').read_text(encoding='utf-8'))
        assert data['code']==code,code
        assert set(data['strings'])==set(en['strings']),code
        for key,value in en['strings'].items():
            assert sorted(token.findall(value))==sorted(token.findall(data['strings'][key])),(code,key)


def test_v710_scrollbars_are_owned_by_theme():
    assert ':root{color-scheme:light;' in CSS
    assert ':root[data-theme=dusk]{color-scheme:light;' in CSS
    assert ':root[data-theme=dark]{color-scheme:dark;' in CSS
    assert '--scrollbar-track:var(--surface2)' in CSS
    assert '--scrollbar-thumb:var(--line2)' in CSS
    assert '--scrollbar-thumb-hover:var(--muted)' in CSS
    assert '*{scrollbar-color:var(--scrollbar-thumb) var(--scrollbar-track)}' in CSS
    assert '*::-webkit-scrollbar{width:10px;height:10px}' in CSS
    assert '*::-webkit-scrollbar-track{background:var(--scrollbar-track)}' in CSS
    assert '*::-webkit-scrollbar-thumb{background:var(--scrollbar-thumb);' in CSS
    assert '*::-webkit-scrollbar-thumb:hover{background:var(--scrollbar-thumb-hover)}' in CSS
    assert '*::-webkit-scrollbar-corner{background:var(--scrollbar-track)}' in CSS
    # Controls that intentionally hide horizontal scrollbars must retain their override.
    assert '.document-toolbar::-webkit-scrollbar{display:none}' in CSS
    assert '.map-toolbar::-webkit-scrollbar{display:none}' in CSS
    assert '.selection-toolbar::-webkit-scrollbar{display:none}' in CSS
