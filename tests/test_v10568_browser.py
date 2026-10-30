"""Missing-link recovery and durable first blank project, disposable hosted API."""
import re

from playwright.sync_api import expect

from tests.test_v10548_browser import hosted_browser

MISSING='f'*32


def ready(page):
    page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')


def test_stale_link_saves_the_first_empty_project_without_typing_or_reload_duplicates(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        creates=[];errors=[];redirects=[]
        page.on('request',lambda r:creates.append(r) if r.method=='POST' and r.url==base+'/api/projects' else None)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('response',lambda r:redirects.append(r) if r.status==303 else None)
        page.goto(base+'/p/'+MISSING,wait_until='domcontentloaded')
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'));ready(page)
        pid=page.url.rsplit('/',1)[-1]
        assert pid!=MISSING and len(creates)==1
        assert redirects and redirects[0].headers['location']=='/?new_project=1'
        expect(page.locator('#saveState')).to_have_text('Saved')
        expect(page.locator('.manuscript-block').first).to_have_text('')
        workspace=page.request.get(base+'/api/workspace').json()
        assert [row['id'] for row in workspace['projects']]==[pid]
        page.reload(wait_until='domcontentloaded');ready(page)
        assert page.url.endswith('/p/'+pid) and len(creates)==1
        assert len(page.request.get(base+'/api/workspace').json()['projects'])==1
        expect(page.locator('#saveState')).to_have_text('Saved')
        denied=page.request.get(base+'/api/projects/'+MISSING+'/members')
        assert denied.status==404 and denied.json()=={'detail':'PROJECT_NOT_FOUND'}
        assert not errors


def test_stale_link_opens_new_blank_without_overwriting_existing_project(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        old=page.request.post(base+'/api/projects',data={'name':'Preserve synthetic original'},headers={'Origin':base}).json()['project']
        creates=[];errors=[]
        page.on('request',lambda r:creates.append(r) if r.method=='POST' and r.url==base+'/api/projects' else None)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base+'/p/'+MISSING+'/canvas',wait_until='domcontentloaded')
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'));ready(page)
        assert page.url.rsplit('/',1)[-1]!=old['id'] and len(creates)==1
        expect(page.locator('#saveState')).to_have_text('Saved')
        expect(page.locator('.manuscript-block').first).to_have_text('')
        marker='Disposable new project, never old manuscript'
        page.locator('.manuscript-block').first.fill(marker)
        expect(page.locator('.manuscript-block').first).to_have_text(marker)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        preserved=page.request.get(base+'/api/bootstrap',headers={'X-Breadmaker-Project':old['id']}).json()
        assert preserved['project']['name']=='Preserve synthetic original'
        assert preserved['project']['generation']==old['generation']
        assert all(not row['text_content'] for row in preserved['blocks'])
        assert len(page.request.get(base+'/api/workspace').json()['projects'])==2
        assert not errors


def test_first_project_retry_after_lost_response_reuses_committed_empty_project(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        attempts=[];committed=[];errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def lose_response(route):
            attempts.append(route.request.headers['idempotency-key'])
            response=route.fetch();assert response.ok
            committed.append(response.json()['project']['id'])
            if len(attempts)==1:route.fulfill(status=503,json={'detail':'Synthetic response lost after commit'})
            else:route.fulfill(response=response)
        page.route(base+'/api/projects',lose_response)
        page.goto(base,wait_until='domcontentloaded');ready(page)
        expect(page.locator('#saveState')).to_have_text('Not saved')
        page.locator('[data-view=tasks]').click()
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}/tasks$'));ready(page)
        assert len(attempts)==2 and attempts[0]==attempts[1]
        assert committed[0]==committed[1] and '/p/'+committed[0]+'/tasks' in page.url
        workspace=page.request.get(base+'/api/workspace').json()
        assert [row['id'] for row in workspace['projects']]==[committed[0]]
        assert not errors
