from source_contracts import frontend_source_from_app_path
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v8188_review_collection_commands_and_live_accept_refresh_are_wired():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'async function duplicateReview(reviewId)' in js
    assert 'async function deleteReview(reviewId)' in js
    assert "action:()=>duplicateReview(review.id)" in js
    assert "action:()=>deleteReview(review.id)" in js
    accept=js[js.index('function showReviewerSuggestionHover'):js.index('function queueReviewerSuggestionHover')]
    assert 'await api(' in accept
    assert 'await refresh(false)' in accept
    assert 'await reloadManuscriptView()' in accept
    assert 'await refreshWriterReviewSnapshot()' in accept
    assert 'prose.innerHTML=applied.content_html' not in accept

def test_v8188_release_line_is_not_regressed():
    version=tuple(int(part) for part in (ROOT/'VERSION').read_text().strip().split('.'))
    assert version >= (8,18,8)
