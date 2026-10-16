from __future__ import annotations
from source_contracts import frontend_source_from_app_path
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v701_release_and_language_catalog_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,0,1)
    assert {'en.json','es.json'} <= {p.name for p in (ROOT/'locales').glob('*.json')}
    data=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    for key in [
        'settings.language.application_section','settings.language.row_label','settings.language.modal_title',
        'settings.language.search_label','settings.language.search_placeholder','settings.language.available_label',
        'settings.language.current_label','settings.language.no_results','settings.language.changed','settings.language.load_error',
    ]:
        assert key in data['strings']


def test_v701_language_picker_is_application_preference_and_searches_both_names():
    app=frontend_source_from_app_path(ROOT/'static/app.js')
    index=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert "I18N_STORAGE_KEY='breadmaker-language'" in app
    assert "localStorage.setItem(I18N_STORAGE_KEY" in app
    assert "localStorage.getItem('breadmaker-language')" in index
    assert "locale.english_name" in app and "locale.native_name" in app and "locale.code" in app
    assert "normalizeLocaleSearch" in app
    assert "openLanguageSettings" in app
    assert '/api/locales' in app
    version=(ROOT/'VERSION').read_text().strip()


def test_v701_locale_loader_accepts_future_locale_and_preserves_english_fallback():
    app=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "payload.code==='en'" not in app
    assert 'validLocalePayload(payload)' in app
    assert "load(code).catch(error=>code==='en'?Promise.reject(error):load('en'))" in (ROOT/'static/index.html').read_text(encoding='utf-8')
