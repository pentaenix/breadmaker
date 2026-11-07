"""Real hosted Canvas version routes, opaque IDs, visible selection and Undo."""
from playwright.sync_api import expect

from tests.test_v10548_browser import hosted_browser


SEED = '''
import json
from backend.environment import runtime
uid=runtime.identity.active_profile_id()
pid=runtime.projects.create('Synthetic native Structure Versions')['id']
domain=runtime.projects.domain
def create(kind,parents,data,eid):
    return domain.create(kind,{'project_id':pid,**parents},data,user_id=uid,entity_id=eid)
cid='canvas_native'
create('canvas',{},dict(name='Native Structure',canvas_type='developmental',developmental_scope='chapters',position=0),cid)
create('chapter',{},dict(title='Synthetic second chapter',tab_id='tab_manuscript',position=1),'chapter_second')
for number,chapter in enumerate(['chapter_1','chapter_second']):
    create('canvas_node',{'canvas_id':cid},dict(node_type='chapter',chapter_id=chapter,title=chapter,shape='rect',x=120,y=390,w=143,h=52,lane_position=number,color='',text_color='',label_id=None),f'node_native_{number}')
for number,vid in enumerate(['version_original','version_reverse']):
    saved={'schema':3,'nodes':[],'edges':[],'structural_styles':{},'viewport':{'zoom':1,'left':0,'top':0,'laneLeft':0}}
    create('canvas_version',{'canvas_id':cid},dict(name='Original' if number==0 else 'Reversed',position=number,workspace_json=json.dumps(saved)),vid)
    for index,chapter in enumerate(['chapter_1','chapter_second']):
        create('canvas_version_item',{'canvas_id':cid,'version_id':vid},dict(entity_type='chapter',entity_id=chapter,title=chapter,x=120,y=390,placed=1,lane_position=index if number==0 else 1-index),f'item_native_{index}')
'''


def test_hosted_native_structure_version_click_and_undo_use_real_atomic_api(tmp_path):
    with hosted_browser(tmp_path, setup_code=SEED) as (base, page):
        errors, activations = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: activations.append(response.status)
                if response.url.endswith('/versions/version_reverse/activate') else None)
        projects = page.request.get(base + '/api/workspace').json()['projects']
        pid = next(project['id'] for project in projects if project['name'] == 'Synthetic native Structure Versions')
        page.goto(base + '/p/' + pid + '/canvas', wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.wait_for_function('()=>state.currentCanvas?.id==="canvas_native"&&state.canvasVersions.length===2')
        assert page.evaluate('state.activeCanvasVersionId') == 'version_original'
        assert page.evaluate('()=>canvasVersionDirty()') is False
        page.locator('#canvasStructureBtn').click()
        expect(page.locator('.canvas-version-list-row')).to_have_count(2)
        page.locator('[data-canvas-version-use="version_reverse"]').click()
        page.wait_for_function('()=>state.activeCanvasVersionId==="version_reverse"&&!canvasVersionSwitchBusy')
        assert page.evaluate('()=>developmentalPlacedCards().map(node=>node.chapter_id)') == ['chapter_second', 'chapter_1']
        assert activations == [200]
        page.evaluate('async()=>await appUndo()')
        page.wait_for_function('()=>developmentalPlacedCards()[0]?.chapter_id==="chapter_1"')
        page.evaluate('async()=>await appRedo()')
        page.wait_for_function('()=>developmentalPlacedCards()[0]?.chapter_id==="chapter_second"')
        manuscript = page.request.get(base + '/api/bootstrap', headers={'X-Breadmaker-Project': pid}).json()['chapters']
        assert [row['id'] for row in sorted(manuscript, key=lambda row: row['position'])] == ['chapter_1', 'chapter_second']
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.wait_for_function('()=>state.activeCanvasVersionId==="version_reverse"')
        assert page.evaluate('()=>canvasVersionDirty()') is False
        assert not errors, errors
