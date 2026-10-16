from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8208_review_comments_use_live_current_text_anchors_without_mutating_snapshot_contract():
    assert tuple(map(int, ROOT.joinpath("VERSION").read_text().strip().split("."))) >= (8, 20, 8)
    js = frontend_source_from_app_path(ROOT.joinpath("static", "app.js"))
    for token in (
        "manuscriptReviewContextBoundary",
        "manuscriptReviewCommonPrefixLength",
        "manuscriptReviewCommonSuffixLength",
        "resilient=kind==='comment'||kind==='highlight'",
        "manuscriptReviewCachedRange(entry)",
        "manuscriptReviewRangeForItem(entry.item,entry.kind)",
    ):
        assert token in js
    # Suggestions deliberately keep exact-source validation; only comments/highlights are live-reanchored.
    assert "if(!resilient&&pieces.join('\\n')!==String(item.selected_text||''))return null" in js
