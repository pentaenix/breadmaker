"""Targets suspend before provisional navigation and replay their durable journal."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import add_word_goal, open_targets


def test_targets_provisional_navigation_stops_reads_in_outgoing_webkit_document(tmp_path):
    with browser(tmp_path,local=False,engine='webkit') as (base,page,pid):
        page.wait_for_function('()=>targets.ready')
        page.evaluate('async()=>await flushTargets()')
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.evaluate('''()=>{window.targetsNavigationRequests=0;const original=fetch;
          window.fetch=(url,...args)=>{if(url.startsWith('/api/ui-state/targets-v1'))targetsNavigationRequests++;return original(url,...args)}}''')
        outgoing=page.evaluate_handle('document');observed=[]
        destination=base+'/p/'+pid+'?targets-navigation-regression=1'
        def navigation(route):
            try:
                # WebKit executes this old document after beforeunload, before
                # pagehide. Starting a request here causes its access-control error.
                observed.append(outgoing.evaluate('''d=>d.defaultView.eval(`(async()=>{
                  await flushTargets();targets.ready=false;const loaded=await loadTargets();
                  return {departing:targetsDeparting,requests:targetsNavigationRequests,loaded};
                })()`)'''))
            finally:
                route.continue_()
        page.route(destination,navigation)
        page.goto(destination,wait_until='domcontentloaded')
        page.wait_for_function('()=>targets.ready')
        assert observed==[{'departing':True,'requests':0,'loaded':False}]
        assert not errors,errors


def test_targets_cancelled_navigation_replays_committed_put_without_double_count(tmp_path):
    with browser(tmp_path,local=False,engine='webkit') as (_,page,_):
        add_word_goal(page,10)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        held=[]
        pattern='**/api/ui-state/targets-v1'
        def hold_commit_response(route):
            if route.request.method=='PUT':
                response=route.fetch();assert response.ok
                held.append((route,response))
                page.evaluate('()=>window.targetsCommitHeld=true')
            else:route.continue_()
        page.route(pattern,hold_commit_response)
        # Await the actual PUT, which is committed by the server but unacknowledged
        # by the page. The durable operation must survive aborting this request.
        with page.expect_request(lambda request:request.method=='PUT' and '/api/ui-state/targets-v1' in request.url):
            page.evaluate('''()=>{
              targetsEnqueue({words:3});
              window.interruptedTargetsSave=flushTargets().catch(error=>{window.targetsNavigationError=error.name});
            }''')
        page.wait_for_function('()=>window.targetsCommitHeld===true')
        assert held
        page.evaluate('''async()=>{
          dispatchEvent(new Event('beforeunload'));await interruptedTargetsSave;
        }''')
        assert page.evaluate('()=>targetsNavigationError')=='AbortError'
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')>0
        assert page.evaluate('()=>targetsRequests.size')==0
        for route,response in held:route.fulfill(response=response)
        page.unroute(pattern,hold_commit_response)
        resumed=[]
        page.on('request',lambda request:resumed.append(request.method) if '/api/ui-state/targets-v1' in request.url else None)
        # A cancelled navigation need not emit pageshow. Real interaction resumes it.
        page.locator('#viewBtn').click();page.keyboard.press('Escape')
        page.wait_for_function('()=>!targetsDeparting&&!targets.saving&&targets.pending.length===0')
        assert page.evaluate('async()=>targetsModel.progress((await targetsRequest()).value)[0].value')==3
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')==0
        assert 'PUT' not in resumed,resumed
        assert not errors,errors


def test_targets_cancelled_navigation_restarts_aborted_load_and_preserves_journal(tmp_path):
    with browser(tmp_path,local=False,engine='webkit') as (_,page,_):
        add_word_goal(page,10)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        held=[];pattern='**/api/ui-state/targets-v1'
        def hold_read(route):held.append(route)
        page.route(pattern,hold_read)
        with page.expect_request(pattern):
            page.evaluate('''()=>{
              targetsEnqueue({words:2});targets.ready=false;
              window.interruptedTargetsLoad=loadTargets();
            }''')
        page.evaluate('''async()=>{
          dispatchEvent(new Event('beforeunload'));window.interruptedTargetsLoaded=await interruptedTargetsLoad;
        }''')
        assert held
        assert page.evaluate('()=>!interruptedTargetsLoaded&&!targets.loading&&!targets.ready&&targetsRequests.size===0')
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')>0
        for route in held:route.abort()
        page.unroute(pattern,hold_read)
        page.locator('#viewBtn').click();page.keyboard.press('Escape')
        page.wait_for_function('()=>targets.ready&&!targets.saving&&targets.pending.length===0')
        assert page.evaluate('async()=>targetsModel.progress((await targetsRequest()).value)[0].value')==2
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')==0
        assert not errors,errors


def test_targets_current_document_denial_still_shows_save_failure_and_keeps_journal(tmp_path):
    with browser(tmp_path,local=False,engine='webkit') as (_,page,_):
        add_word_goal(page,10)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        open_targets(page)
        page.route('**/api/ui-state/targets-v1',lambda route:route.fulfill(status=403,json={'detail':'TARGETS_ACCESS_DENIED'}))
        page.locator('#targetsSave').click()
        expect(page.locator('#targetsError')).to_have_text(page.evaluate("()=>targetText('save_failed')"))
        expect(page.locator('#modal')).to_be_visible()
        expect(page.locator('#targetsSave')).to_be_enabled()
        expect(page.locator('#targetsCancel')).to_be_enabled()
        expect(page.locator('#targetsTop')).to_be_hidden()
        assert page.evaluate('()=>!targetsDeparting&&!targets.ready')
        assert page.evaluate('''async()=>{try{await targetsRequest();return 0}catch(error){return error.status}}''')==403
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')>0
        assert not errors,errors
