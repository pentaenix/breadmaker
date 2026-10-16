from __future__ import annotations
from source_contracts import frontend_styles_source_from_styles_path
from source_contracts import frontend_source_from_app_path

from storage import schema as storage_schema
import json
from pathlib import Path
from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]
LOCALES=('en','es','fr','it','ja','pt-BR','zh-CN')

def test_v8152_release_contract_review_ui_follows_manuscript():
    version=ROOT.joinpath('VERSION').read_text().strip()
    assert tuple(map(int,version.split('.'))) >= (8,15,2)
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=31
    html=ROOT.joinpath('static/index.html').read_text()
    js=frontend_source_from_app_path(ROOT.joinpath('static/app.js'))
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static/styles.css'))
    for control in ('reviewParticipantsBtn','reviewQuestionsBtn','reviewStateBtn'):
        assert f'id="{control}"' in html
        assert 'tool-icon-button' in html[html.index(f'id="{control}"')-100:html.index(f'id="{control}"')+300]
    for removed in ('reviewScopeBtn','reviewPermissionsBtn','reviewOpenReviewerBtn','newReviewBtn','reviewCategoryFilterBtn'):
        assert f'id="{removed}"' not in html
        assert removed not in js
    for panel in ('reviewParticipantsPanel','reviewQuestionsPanel','reviewStatePanel'):
        assert f'id="{panel}"' in html
    for closer in ('closeReviewParticipantsPanel','closeReviewQuestionsPanel','closeReviewStatePanel'):
        assert f'id="{closer}"' in html
    assert "reviews.participants_manage" not in js[js.index('function reviewMenuItems'):js.index('function openReviewMenu')]
    assert 'id="startReviewBtn"' not in js
    assert 'reviewVisibilityInput' not in js
    assert 'reviews.visibility_help' not in js
    assert 'reviewCategoryCreateInline' not in js
    assert 'reviewInlineVisibility' in js and 'reviews.reviewers_see_notes' in js
    assert 'queueReviewItemHover' in js and 'positionMarkerPanelHover(card,row)' in js
    assert 'reviewCategoriesBtn' not in html and 'reviewCategoriesPanel' not in html
    assert '@media(max-width:640px)' in css and '.review-state-panel' in css


def test_v8152_review_description_is_canonical(client,fresh_project):
    created=assert_ok(client.post('/api/reviews',json={'name':'Beta Pass','description':'Reader experience and pacing.'}))
    assert created['description']=='Reader experience and pacing.'
    rid=created['id']
    updated=assert_ok(client.patch(f'/api/reviews/{rid}',json={'description':'Focus on clarity.'}))
    assert updated['description']=='Focus on clarity.'
    listed=assert_ok(client.get('/api/reviews'))
    assert next(r for r in listed if r['id']==rid)['description']=='Focus on clarity.'


def test_v8152_locales_have_same_review_ui_language():
    required={'reviews.description','reviews.description_placeholder','reviews.state','reviews.panel_open_hint','reviews.inline_feedback_visibility_label','reviews.inline_feedback_private','reviews.inline_feedback_shared','reviews.current_state'}
    baseline=None
    for code in LOCALES:
        strings=json.loads(ROOT.joinpath('locales',f'{code}.json').read_text())['strings']
        assert required <= set(strings)
        if baseline is None: baseline=set(strings)
        else: assert set(strings)==baseline
