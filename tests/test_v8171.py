from source_contracts import frontend_source_from_app_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v8171_review_workspace_startup_helpers_are_present():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,17,1)
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    for helper in (
        'function reviewToolPanelOpen(panelId)',
        'function closeReviewToolPanels(exceptId=null)',
        'function syncReviewToolbar(review)',
        'function reviewStatusLabel(status)',
    ):
        assert helper in js
