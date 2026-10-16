from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v8180_reversible_round_ui_and_tab_order(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'8.18.0 Reversible Rounds'); page,errors=_page(base,scene_browser)
    try:
        tabs=page.locator('.main-tab').evaluate_all("els=>els.map(e=>e.dataset.view)")
        assert tabs.index('reviews')+1==tabs.index('tasks')
        page.locator('[data-view="reviews"]').click(); page.locator('#emptyCreateReview').click()
        page.locator('#reviewNameInput').fill('Round Trip'); page.locator('#reviewModalConfirm').click()
        page.wait_for_function("()=>state.reviews.find(r=>r.name==='Round Trip')")
        page.locator('#reviewStateBtn').click(); page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews.find(r=>r.name==='Round Trip')?.status==='open'")
        first_id=page.evaluate("state.reviews.find(r=>r.name==='Round Trip').snapshot.id")
        assert page.locator('#reviewStateClose').count()==1

        # Starting a new round does not require closing first; the prior round becomes reversible history.
        page.locator('#reviewStateStart').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function("()=>state.reviews.find(r=>r.name==='Round Trip')?.rounds?.length===2")
        review=page.evaluate("state.reviews.find(r=>r.name==='Round Trip')")
        second_id=review['snapshot']['id']; assert second_id!=first_id
        assert next(r for r in review['rounds'] if r['id']==first_id)['status']=='closed'

        # Reopen the first round from history. Round 2 is paused, not destroyed.
        page.locator(f'[data-review-round-reopen="{first_id}"]').click(); page.locator('#dialogConfirm').click()
        page.wait_for_function(f"()=>state.reviews.find(r=>r.name==='Round Trip')?.snapshot?.id==='{first_id}'")
        review=page.evaluate("state.reviews.find(r=>r.name==='Round Trip')")
        assert next(r for r in review['rounds'] if r['id']==first_id)['status']=='open'
        assert next(r for r in review['rounds'] if r['id']==second_id)['status']=='paused'
        assert not errors,errors
    finally:
        _shutdown_page(page)
