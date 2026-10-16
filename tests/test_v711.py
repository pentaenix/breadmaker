from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INDEX=(ROOT/'static/index.html').read_text(encoding='utf-8')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
APP=frontend_source_from_app_path(ROOT/'static/app.js')


def test_v711_release_and_avatar_contract():
    assert INDEX.index('id="themeBtn"') < INDEX.index('id="userAvatarButton"')
    assert 'id="userAvatarButton" class="user-avatar-button"' in INDEX
    assert 'id="userAvatar" class="user-avatar" aria-hidden="true"' in INDEX
    assert '{{i18n:user.avatar}}' in INDEX
    assert '.user-avatar{width:' in CSS
    assert 'border-radius:50%' in CSS
    assert 'function userAvatarSeed(name)' in APP
    assert 'function userAvatarPalette(name)' in APP
    assert 'function userAvatarLineSvg(name)' in APP
    assert 'function renderUserProfile()' in APP
    assert "$('#userAvatarButton')?.addEventListener('click'" in APP
    assert "$('#userAvatar').addEventListener" not in APP


def test_v711_all_locales_include_avatar_and_preserve_parity():
    locales={p.stem for p in (ROOT/'locales').glob('*.json')}
    assert locales=={'en','es','fr','it','ja','pt-BR','zh-CN'}
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    assert en['strings']['user.avatar']=='User avatar'
    assert en['source_index']['User avatar']=='user.avatar'
    for code in locales:
        data=json.loads((ROOT/'locales'/f'{code}.json').read_text(encoding='utf-8'))
        assert data['code']==code
        assert data['strings']['user.avatar']
        assert data['source_index']==en['source_index']
        assert set(data['strings'])==set(en['strings'])
        for key,value in en['strings'].items():
            assert sorted(token.findall(value))==sorted(token.findall(data['strings'][key])),(code,key)
