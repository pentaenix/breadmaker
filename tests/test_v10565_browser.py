"""Each workspace can be the first persisted intent of a hosted account."""
from contextlib import contextmanager
import re

from playwright.sync_api import expect
import requests

from tests.test_v10548_browser import hosted_browser


@contextmanager
def first_workspace(home, view):
    with hosted_browser(home) as (base, page):
        errors, creates = [], []
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.on('response',lambda response:errors.append(f'HTTP {response.status}') if response.status>=400 else None)
        page.on('request',lambda request:creates.append(request) if request.method=='POST' and request.url==base+'/api/projects' else None)
        first = [True]
        def empty_workspace(route):
            response=route.fetch();body=response.json()
            if first[0]:
                first[0]=False;body.update(projects=[],active_project_id='')
            route.fulfill(response=response,json=body)
        page.route('**/api/workspace',empty_workspace)
        page.goto(base,wait_until='domcontentloaded',timeout=8000)
        expect(page.locator('body')).to_have_class(re.compile('hosted-zero-project'))
        for name in ('canvas','maps','tasks','reviews'):
            button=page.locator(f'.main-tab[data-view="{name}"]')
            expect(button).to_be_enabled()
            assert button.evaluate('(el)=>getComputedStyle(el).pointerEvents')!='none'
        expect(page.locator('#saveState')).to_be_visible()
        page.locator(f'.main-tab[data-view="{view}"]').click()
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}/'+view+r'$'),timeout=6000)
        expect(page.locator(f'#{view}View')).to_have_class(re.compile('active'))
        expect(page.locator('body')).not_to_have_class(re.compile('hosted-zero-project'))
        assert len(creates)==1
        assert page.evaluate('sessionStorage.getItem("breadmaker-hosted-first-draft")') is None
        try:
            yield base,page,page.url.split('/')[4],errors
        finally:
            assert not errors,errors


def test_first_canvas_saves_and_survives_reload_without_manuscript_input(tmp_path):
    with first_workspace(tmp_path,'canvas') as (base,page,pid,errors):
        page.locator('#emptyCreateCanvas').click()
        page.locator('#newCanvasName').fill('First canvas')
        page.locator('#createCanvasConfirm').click()
        page.wait_for_function('()=>state.currentCanvas?.name==="First canvas"')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#canvasView')).to_have_class(re.compile('active'))
        page.wait_for_function('()=>state.currentCanvas?.name==="First canvas"')
        rows=requests.get(base+'/api/bootstrap',headers={'X-Breadmaker-Project':pid},timeout=2).json()['canvases']
        assert any(row['name']=='First canvas' for row in rows)


def test_first_map_saves_and_survives_reload_without_manuscript_input(tmp_path):
    with first_workspace(tmp_path,'maps') as (base,page,pid,errors):
        page.evaluate('()=>{void newMapDialog()}')
        page.locator('#dialogText').fill('First map')
        page.locator('#dialogConfirm').click()
        expect(page.locator('#mapCurrentName')).to_have_text('First map')
        # Do not turn an in-flight render into an artificial unload failure.
        page.wait_for_load_state('networkidle',timeout=2500)
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#mapsView')).to_have_class(re.compile('active'))
        expect(page.locator('#mapCurrentName')).to_have_text('First map')
        rows=requests.get(base+'/api/bootstrap',headers={'X-Breadmaker-Project':pid},timeout=2).json()['maps']
        assert any(row['name']=='First map' for row in rows)


def test_first_task_saves_and_survives_reload_without_manuscript_input(tmp_path):
    with first_workspace(tmp_path,'tasks') as (base,page,pid,errors):
        page.locator('#newTaskBtn').click()
        page.locator('#taskName').fill('First task')
        page.locator('#saveTask').click()
        expect(page.locator('#tasksView')).to_contain_text('First task')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#tasksView')).to_have_class(re.compile('active'))
        expect(page.locator('#tasksView')).to_contain_text('First task')
        rows=requests.get(base+'/api/tasks',headers={'X-Breadmaker-Project':pid},timeout=2).json()['tasks']
        assert any(row['title']=='First task' for row in rows)


def test_first_review_saves_and_survives_reload_without_manuscript_input(tmp_path):
    with first_workspace(tmp_path,'reviews') as (base,page,pid,errors):
        page.evaluate('()=>{void createReviewDialog()}')
        page.locator('#reviewNameInput').fill('First review')
        page.locator('#reviewModalConfirm').click()
        expect(page.locator('#reviewsView')).to_contain_text('First review')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('#reviewsView')).to_have_class(re.compile('active'))
        expect(page.locator('#reviewsView')).to_contain_text('First review')
        rows=requests.get(base+'/api/bootstrap',headers={'X-Breadmaker-Project':pid},timeout=2).json()['reviews']
        assert any(row['name']=='First review' for row in rows)
