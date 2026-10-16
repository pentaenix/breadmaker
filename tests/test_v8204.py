from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8204_review_overlays_are_not_manuscript_storage_or_anchor_text():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8, 20, 4)
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    for token in (
        'function manuscriptReviewTextUnits(root)',
        "child.matches?.('[data-manuscript-review-ghost]')",
        'function manuscriptReviewBaseText(root)',
        'function manuscriptProseForStorage(prose)',
        "clone.querySelectorAll('[data-manuscript-review-ghost]')",
        "clone.querySelectorAll('[data-manuscript-review-inline]')",
    ):
        assert token in js
    assert 'manuscriptReviewBaseText(root)' in js
    assert 'content_html:clean.html,text_content:clean.text' in js
