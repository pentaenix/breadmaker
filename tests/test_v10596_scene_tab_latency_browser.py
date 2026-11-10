"""Actual hosted scene rendering from a freshly authorized Full Text snapshot."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser
from tests.test_v10557_browser import _book


SEED = """
from backend.environment import runtime
uid=runtime.identity.active_profile_id()
pid=runtime.projects.create('Synthetic cached scene test')['id']
domain=runtime.projects.domain
parents={'project_id':pid}
domain.create('scene',parents,{'title':'Synthetic cached scene','tab_id':'tab_manuscript','position':0},user_id=uid,entity_id='scene_cached')
for index in range(3):
    prose=f'Synthetic scene paragraph {index}'
    domain.create('manuscript_block',parents,{'chapter_id':'chapter_1','tab_id':'tab_manuscript','scene_id':'scene_cached','position':index+1,'block_type':'paragraph','semantic_style':'body','content_html':prose,'text_content':prose},user_id=uid,entity_id=f'block_cached_{index}')
"""


def test_cached_scene_opens_after_authority_check_without_redownloading_prose(tmp_path):
    with hosted_browser(tmp_path, setup_code=SEED) as (base, page):
        projects = page.request.get(base + '/api/workspace').json()['projects']
        pid = next(project['id'] for project in projects if project['name'] == 'Synthetic cached scene test')
        sid = 'scene_cached'
        page.goto(base + '/p/' + pid, wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        _book(page)
        expected = page.evaluate('(sid)=>state.bookData.blocks.filter(b=>b.scene_id===sid).map(b=>b.text_content)', sid)
        assert len(expected) == 3
        reads = []
        page.on('request', lambda request: reads.append(request.url)
                if request.method == 'GET' and ('/api/book?' in request.url or '/manuscript' in request.url or '/api/chapters/' in request.url) else None)
        page.evaluate('async(sid)=>await selectScene(sid)', sid)
        assert page.evaluate('()=>state.viewMode') == 'scene'
        expect(page.locator('#editor .manuscript-block')).to_have_count(3)
        assert page.locator('#editor .manuscript-block').all_text_contents() == expected
        assert len(reads) == 1 and '/api/book?' in reads[0]
        assert page.evaluate('()=>!state.dirty&&!state.saveInFlight')
