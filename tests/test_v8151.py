from __future__ import annotations

from source_contracts import storage_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import schema as storage_schema

import json
from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]
LOCALES=('en','es','fr','it','ja','pt-BR','zh-CN')


def test_v8151_release_contract_manuscript_first_reviews_without_categories():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,15,1)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=33
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'id="reviewCollectionMore"' in html
    assert 'id="newReviewBtn"' not in html
    assert 'id="reviewCategoryFilterBtn"' not in html
    assert 'id="reviewCategoriesBtn"' not in html
    assert 'id="reviewCategoriesPanel"' not in html
    for control in ('reviewParticipantsBtn','reviewQuestionsBtn','reviewStateBtn'):
        assert f'id="{control}"' in html
    assert 'reviewCategory' not in js
    assert 'openReviewCyclePopulateModal' in js
    assert '.review-item{' in css


def test_v8151_categories_are_removed_from_api_and_review_payload(client,fresh_project):
    assert client.get('/api/review-categories').status_code==404
    assert client.post('/api/review-categories',json={'name':'Beta Reading'}).status_code==404
    review=assert_ok(client.post('/api/reviews',json={'name':'Beta Reading'}))
    assert 'category_id' not in review
    boot=assert_ok(client.get('/api/bootstrap'))
    assert 'review_categories' not in boot
    assert all('category_id' not in row for row in boot['reviews'])


def test_v8151_all_seven_locales_include_populate_template_language():
    required={
        'reviews.populate','reviews.populate_cycles','reviews.populate_cycles_hint',
        'reviews.cycle_alpha','reviews.cycle_alpha_description','reviews.cycle_developmental','reviews.cycle_developmental_description',
        'reviews.cycle_authenticity','reviews.cycle_authenticity_description','reviews.cycle_technical','reviews.cycle_technical_description',
        'reviews.cycle_beta','reviews.cycle_beta_description','reviews.cycle_line','reviews.cycle_line_description',
        'reviews.cycle_proof','reviews.cycle_proof_description',
    }
    baseline=None
    for code in LOCALES:
        data=json.loads(ROOT.joinpath('locales',f'{code}.json').read_text(encoding='utf-8'))
        strings=data['strings']
        assert required <= set(strings)
        assert not any(key.startswith('reviews.categories_') or key.startswith('reviews.category_') for key in strings)
        if baseline is None: baseline=set(strings)
        else: assert set(strings)==baseline


def test_v8151_storage_invariant_has_no_category_schema():
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    storage_py=storage_source(ROOT)
    assert 'CREATE TABLE review_categories' not in schema
    assert 'category_id' not in schema
    assert 'review_categories' not in storage_py
