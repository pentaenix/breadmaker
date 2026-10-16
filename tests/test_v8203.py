from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8203_review_panel_open_state_and_replace_diff_are_explicit():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,3)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    html = ROOT.joinpath('static','index.html').read_text()

    assert 'panel_open:!!manuscriptReviewsWorkspace.panelOpen' in js
    assert 'localStorage.setItem(manuscriptReviewsLocalStateKey(),JSON.stringify(value))' in js
    assert 'manuscriptReviewsWorkspace.panelOpen=value.panel_open===true' in js
    assert "if(bootView==='manuscript'&&manuscriptReviewsWorkspace.panelOpen)" in js
    assert 'manuscriptReviewsWorkspace.panelOpen=true' in js
    assert 'manuscriptReviewsWorkspace.panelOpen=false' in js

    assert '::highlight(manuscript-review-suggestion-delete),::highlight(manuscript-review-suggestion-replace){color:var(--danger)' in css
    assert '.manuscript-review-proposal-decoration{position:absolute' in css
    assert 'box-shadow:inset 0 -2px 0' in css
    version=ROOT.joinpath('VERSION').read_text().strip()
