from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

def test_v841_permission_labels_are_centered_and_spaced(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,"8.4.1 Permission alignment"); page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="reviews"]').click()
        page.locator('#emptyCreateReview').click()
        rows=page.locator('.review-permission-option')
        assert rows.count()==5
        metrics=rows.evaluate_all("""rows=>rows.map(row=>{
          const input=row.querySelector('input').getBoundingClientRect();
          const text=row.querySelector('span').getBoundingClientRect();
          const style=getComputedStyle(row);
          return {display:style.display,alignItems:style.alignItems,gap:parseFloat(style.columnGap||style.gap),centerDelta:Math.abs((input.top+input.height/2)-(text.top+text.height/2)),distance:text.left-input.right};
        })""")
        for m in metrics:
            assert m['display']=='flex'
            assert m['alignItems']=='center'
            assert m['centerDelta'] < 1.0
            assert m['distance'] >= 8.0
        assert not errors,errors
    finally:_shutdown_page(page)
