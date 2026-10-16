from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8201_manuscript_review_annotations_use_noninvasive_editorial_language():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,1)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))

    for token in (
        'manuscriptReviewCachedRange',
        'manuscriptReviewRecordHitZones',
        'manuscriptReviewAddProposalDecoration',
        'showManuscriptReviewHover',
        'queueManuscriptReviewHover',
        'finishManuscriptReviewSuggestion',
        '/suggestions/${encodeURIComponent(item.id)}/${action}',
    ):
        assert token in js

    for token in (
        '::highlight(manuscript-review-comments)',
        '::highlight(manuscript-review-suggestion-delete)',
        '.manuscript-review-proposal-decoration',
        '.manuscript-review-hover-card',
        '.manuscript-review-gutter-swatch',
        '.manuscript-review-gutter-change',
    ):
        assert token in css

    # Review rendering must not wrap editable manuscript text.
    assert 'function manuscriptReviewApplyInlineAnnotation' not in js
    assert 'function manuscriptReviewInlineWrapper' not in js
