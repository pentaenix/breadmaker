from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def test_v6312_source_line_menu_syncs_only_on_request(scene_live_server,scene_browser):
    base=scene_live_server
    _,chapter=_new_project(base,'Maps 6.3.12 explicit sync')
    story=requests.post(base+'/api/maps',json={'name':'Explicit Sync','layout_mode':'scaled','label_mode':'minimal'},timeout=10).json()
    populated=requests.post(base+f"/api/maps/{story['id']}/populate",json={'template_id':'chapters'},timeout=10).json()
    detail=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
    line=next(row for row in detail['lines'] if row['id']==populated['line_id'])
    original_count=len(line['items'])

    # Change the source after population. GET must stay a snapshot.
    boot=requests.get(base+'/api/bootstrap',timeout=10).json();tab=next(d for d in boot['tabs'] if d.get('is_manuscript'))
    requests.post(base+'/api/chapters',json={'title':'Later Chapter','tab_id':tab['id']},timeout=10).raise_for_status()
    still=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
    assert len(next(row for row in still['lines'] if row['id']==line['id'])['items'])==original_count

    page,errors=_page(base,scene_browser)
    try:
        page.locator('[data-view="maps"]').click();page.wait_for_timeout(250)
        page.locator(f'.map-tree-map-row[data-map-id="{story["id"]}"]').click();page.wait_for_timeout(220)
        row=page.locator(f'.map-tree-line-row[data-map-tree-line="{line["id"]}"]')
        row.locator('.map-tree-line-more').click();page.wait_for_timeout(120)
        sync=page.locator('.context-menu').get_by_text('Sync',exact=True)
        assert sync.count()==1
        sync.click();page.wait_for_timeout(420)
        synced=requests.get(base+f"/api/maps/{story['id']}",timeout=10).json()
        assert len(next(row for row in synced['lines'] if row['id']==line['id'])['items'])==original_count+1
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
