from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v811_release_uses_collection_workspace_review_shell():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip(); assert tuple(map(int,version.split('.'))) >= (8,1,1)
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8'); css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css')); js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'id="reviewRail"' in html and 'id="reviewCurrentName"' in html and 'id="editReviewDetailsBtn"' in html and 'id="emptyCreateReview"' in html
    assert '.review-sidebar' in css and '.review-item.active' in css
    assert 'openReviewCollectionMenu' in js and 'openReviewMenu' in js
