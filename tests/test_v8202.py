from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v8202_manuscript_review_hover_is_non_redundant_and_actions_are_layered():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,20,2)
    js = frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))

    assert 'data-manuscript-review-hover-accept' in js
    assert 'data-manuscript-review-hover-reject' in js
    assert "body=comment?String(entry.item.body||''):''" in js
    assert 'manuscript-review-hover-selection' not in js
    assert "task?[task]:items" in js
    assert "group.entries.length>1" in js
    assert "marker.classList.toggle('is-selected'" not in js
    assert '.context-menu.review-hover-context{z-index:3300}' in css
    assert '.manuscript-review-gutter-count{position:static' in css
