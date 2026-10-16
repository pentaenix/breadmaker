from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import schema as storage_schema

import json
from pathlib import Path
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]
LOCALES=('en','es','fr','it','ja','pt-BR','zh-CN')


def test_v8150_visual_cleanup_foundation_survives_current_release():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,15,0)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION>=29
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'id="reviewCollectionMore"' in html
    assert '.review-note-visibility' in css and 'align-items:center' in css
    assert 'reviewFeedbackFilters' in js
    assert '.review-feedback-item{padding:12px 2px;border:0' in css


def test_v8150_all_seven_locales_cover_review_controls_and_find_ampersand():
    expected={'reviews.more_actions','reviews.question_count','reviews.participant_count'}
    baseline=None
    for code in LOCALES:
        data=json.loads(ROOT.joinpath('locales',f'{code}.json').read_text(encoding='utf-8'))
        strings=data['strings']
        assert expected <= set(strings)
        if baseline is None: baseline=set(strings)
        else: assert set(strings)==baseline
        assert 'Find &amp; Replace' not in data.get('source_index',{})
        assert data.get('source_index',{}).get('Find & Replace')=='static.text.text.e66e828f'
    en=json.loads(ROOT.joinpath('locales','en.json').read_text(encoding='utf-8'))
    assert en['strings']['static.text.text.e66e828f']=='Find & Replace'


def test_v8150_review_ui_uses_shared_theme_tokens_for_three_themes():
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    assert ':root[data-theme=dusk]' in css and ':root[data-theme=dark]' in css
    section=css.split('/* 8.15.0 — Reviews progressive-disclosure visual language. */',1)[1]
    for token in ('var(--surface)','var(--surface2)','var(--text)','var(--muted)','var(--line)','var(--accent)','var(--accent-soft)'):
        assert token in section
    assert '.review-overview{width:min(780px' in section
