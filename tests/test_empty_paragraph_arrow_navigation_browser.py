from __future__ import annotations

from test_v41812_browser import (
    _new_project,
    _page,
    _set_blocks,
    _shutdown_page,
    scene_browser,
    scene_live_server,
)


def _active_paragraph_index(page):
    return page.evaluate(
        """()=>[...document.querySelectorAll('.manuscript-block')].indexOf(activeManuscriptProse())"""
    )


def test_arrow_keys_visit_each_empty_paragraph(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, "Empty paragraph arrow navigation")
    _set_blocks(base, chapter["id"], ["alpha", "", "", "omega"])
    page, errors = _page(base, scene_browser)
    try:
        page.evaluate(
            """()=>{const prose=document.querySelectorAll('.manuscript-block')[0];placeCaret(prose,prose.innerText.length)}"""
        )

        page.keyboard.press("ArrowDown")
        assert _active_paragraph_index(page) == 1
        page.keyboard.press("ArrowDown")
        assert _active_paragraph_index(page) == 2

        page.evaluate(
            """()=>{const prose=document.querySelectorAll('.manuscript-block')[3];placeCaret(prose,0)}"""
        )
        page.keyboard.press("ArrowUp")
        assert _active_paragraph_index(page) == 2
        page.keyboard.press("ArrowUp")
        assert _active_paragraph_index(page) == 1

        page.evaluate(
            """()=>{const prose=document.querySelectorAll('.manuscript-block')[3];placeCaret(prose,prose.innerText.length)}"""
        )
        page.keyboard.press("ArrowDown")
        assert _active_paragraph_index(page) == 3

        page.evaluate(
            """()=>placeCaret(document.querySelectorAll('.manuscript-block')[0],0)"""
        )
        page.keyboard.press("ArrowUp")
        assert _active_paragraph_index(page) == 0

        assert page.locator(".manuscript-block").all_inner_texts() == [
            "alpha",
            "",
            "",
            "omega",
        ]
        assert not errors, errors
    finally:
        _shutdown_page(page)
