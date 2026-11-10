"""Actual hosted scene rendering from a freshly authorized Full Text snapshot."""
import pytest

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


def _scene_typing_survives_reload(tmp_path, *, cached):
    with hosted_browser(tmp_path, setup_code=SEED) as (base, page):
        projects = page.request.get(base + '/api/workspace').json()['projects']
        pid = next(project['id'] for project in projects if project['name'] == 'Synthetic cached scene test')
        page.goto(base + '/p/' + pid, wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        if cached:
            _book(page)
            assert page.evaluate('()=>state.bookCache.size>0')
        else:
            page.evaluate('()=>state.bookCache.clear()')
        reads = []
        failures = []
        page.on('request', lambda request: reads.append(request.url)
                if request.method == 'GET' and '/api/scenes/scene_cached/manuscript' in request.url else None)
        page.on('response', lambda response: failures.append((response.status, response.url))
                if response.status >= 400 else None)
        page.evaluate('async()=>await selectScene("scene_cached")')
        expect(page.locator('#editor .manuscript-block')).to_have_count(3)
        assert len(reads) == (0 if cached else 1)
        assert page.evaluate('()=>!!state.chapterRevisions.get("chapter_1")')
        text = 'Synthetic saved scene prose from ' + ('cached Full Text' if cached else 'direct scene load')
        with page.expect_response(lambda response: response.request.method == 'PUT'
                and '/api/scenes/scene_cached/blocks' in response.url) as saved:
            page.locator('#editor .manuscript-block').first.fill(text)
        assert saved.value.status == 200, saved.value.text()
        payload = saved.value.request.post_data_json
        assert payload['expected_revisions']['chapter_1']
        assert payload['blocks'][0]['text_content'] == text
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight', timeout=5000)
        assert 'Save failed' not in page.locator('#saveState').inner_text()
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        # Force the authoritative direct-read path after navigation reload; this
        # assertion cannot pass using the prior page's cached scene snapshot.
        page.evaluate('()=>state.bookCache.clear()')
        page.evaluate('async()=>await selectScene("scene_cached")')
        expect(page.locator('#editor .manuscript-block').first).to_have_text(text)
        assert page.locator('#editor .manuscript-block').all_text_contents()[1:] == [
            'Synthetic scene paragraph 1', 'Synthetic scene paragraph 2']
        assert page.evaluate('()=>!state.dirty&&!state.saveInFlight')
        assert not failures, failures


def test_cached_full_text_to_scene_typing_persists_after_reload(tmp_path):
    _scene_typing_survives_reload(tmp_path, cached=True)


def test_direct_scene_typing_persists_after_reload(tmp_path):
    _scene_typing_survives_reload(tmp_path, cached=False)


@pytest.mark.parametrize('status,error', [
    (409, 'HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE'),
    (400, 'HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE'),
])
def test_unsupported_scene_save_keeps_editing_and_draft_without_lease_modal(tmp_path, status, error):
    with hosted_browser(tmp_path, setup_code=SEED) as (base, page):
        projects = page.request.get(base + '/api/workspace').json()['projects']
        pid = next(project['id'] for project in projects if project['name'] == 'Synthetic cached scene test')
        page.goto(base + '/p/' + pid, wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.evaluate('async()=>await selectScene("scene_cached")')
        page.route('**/api/scenes/scene_cached/blocks', lambda route: route.fulfill(status=status, json={'detail': error}))
        # Observe scheduling without replacing the actual timers or waiting for
        # an arbitrary delay to infer that automatic retries are absent.
        page.evaluate('''()=>{
            window.sceneRetryDelays=[];const native=window.setTimeout;
            window.setTimeout=function(callback,delay,...args){
                if(delay===1500&&String(callback).includes('flushSave'))window.sceneRetryDelays.push(delay);
                return native.call(window,callback,delay,...args);
            };
        }''')
        text = 'Synthetic unsaved scene draft stays editable'
        with page.expect_response(lambda response: response.request.method == 'PUT'
                and '/api/scenes/scene_cached/blocks' in response.url) as rejected:
            page.locator('#editor .manuscript-block').first.fill(text)
        assert rejected.value.status == status
        page.wait_for_function('()=>state.dirty&&!state.saveInFlight&&document.querySelector("#saveState").textContent==="Save failed"')
        expect(page.locator('#editor .manuscript-block').first).to_have_text(text)
        expect(page.locator('#editor .manuscript-block').first).to_be_editable()
        expect(page.locator('#modal')).not_to_be_visible()
        assert page.evaluate('()=>!leaseLost&&state.dirtyChapters.has("chapter_1")&&window.sceneRetryDelays.length===0')
        response = page.request.get(base + '/api/scenes/scene_cached/manuscript', headers={'X-Breadmaker-Project': pid})
        assert response.ok
        assert response.json()['blocks'][0]['text_content'] == 'Synthetic scene paragraph 0'
