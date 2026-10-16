from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8205_active_and_completed_review_feedback_are_separate():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,5)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    for token in (
        "function manuscriptReviewEntries(scope='open')",
        "scope==='open'?String(item.status||'open')==='open'",
        "manuscriptReviewEntries('completed')",
        'manuscriptReviewCompletedToggle',
        'finishManuscriptReviewSuggestion',
        'await reloadManuscriptView()',
    ):
        assert token in js
    assert '.manuscript-review-completed-section' in css
    assert '.manuscript-review-feedback-card.completed' in css
