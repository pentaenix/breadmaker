from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from storage import schema as storage_schema

from pathlib import Path

from workspace import APP_SCHEMA_VERSION
ROOT=Path(__file__).resolve().parents[1]

def test_v851_review_workspace_uses_main_viewport_and_current_toolbar_contract():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,5,1)
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION >= 27
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'id="reviewOverview"' in html
    assert 'id="reviewParticipantsBtn"' in html
    assert 'id="reviewQuestionsBtn"' in html
    assert 'id="reviewStateBtn"' in html
    assert 'id="reviewParticipantsBtn"' in html
    assert 'id="reviewQuestionsBtn"' in html
    assert 'function renderReviewOverview(review)' in js
    assert 'function syncReviewToolbar(review)' in js
