"""Silent lease navigation checks; no narration or audio actions."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser


def _exercise_departing_document_cannot_start_lease_heartbeat(tmp_path,local,engine):
    with browser(tmp_path,local=local,engine=engine) as (base,page,pid):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.evaluate('''()=>{window.testLeaseNavCalls=0;const original=fetch;
          window.fetch=(url,...args)=>{if(url==='/api/lease')testLeaseNavCalls++;return original(url,...args)}}''')
        document=page.evaluate_handle('document');departing=[]
        held=base+'/p/'+pid+'?lease-navigation-regression=1'
        def navigation(route):
            if engine=='webkit':
                # Old WebKit documents remain executable while navigation is provisional.
                departing.append(document.evaluate('d=>d.defaultView.eval("heartbeatLease();testLeaseNavCalls")'))
            route.continue_()
        page.route(held,navigation);page.goto(held)
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        if engine=='webkit':assert departing==[0]
        assert not errors,errors


def _exercise_cancelled_navigation_resumes_lease_and_retains_conflict_fence(tmp_path,engine):
    with browser(tmp_path,local=True,engine=engine) as (base,page,_):
        page.evaluate('''()=>{window.testLeaseNavCalls=0;const original=fetch;
          window.fetch=(url,...args)=>{if(url==='/api/lease')testLeaseNavCalls++;return original(url,...args)};
          dispatchEvent(new Event('beforeunload'));heartbeatLease()}''')
        assert page.evaluate('()=>testLeaseNavCalls')==0
        with page.expect_response(base+'/api/lease') as renewed:
            page.locator('#viewBtn').click()
        assert renewed.value.status==200
        page.keyboard.press('Escape')
        page.route('**/api/lease',lambda route:route.fulfill(status=409,content_type='application/json',body='{"detail":"Project already open"}'))
        page.evaluate("()=>{dispatchEvent(new Event('beforeunload'))}")
        page.locator('#viewBtn').click()
        expect(page.locator('#modalTitle')).to_have_text('Project already open')
        expect(page.locator('#reloadLease')).to_be_visible()
        assert page.locator('#editor').get_attribute('contenteditable')=='false'


def _exercise_navigation_aborts_inflight_lease_without_unhandled_error(tmp_path,engine):
    with browser(tmp_path,local=True,engine=engine) as (base,page,_):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        held=[];page.route('**/api/lease',lambda route:held.append(route))
        with page.expect_request(base+'/api/lease'):
            page.evaluate('()=>{window.pendingLeaseHeartbeat=heartbeatLease()}')
        page.evaluate('''async()=>{window.previousLeaseController=leaseHeartbeatController;
          dispatchEvent(new Event('beforeunload'));await pendingLeaseHeartbeat}''')
        assert page.evaluate('()=>previousLeaseController.signal.aborted&&leaseHeartbeatController===null')
        assert held
        assert not errors,errors


# Explicit cases match the release verifier's one-item browser jobs.

def test_departing_document_cannot_start_lease_heartbeat_chromium_local(tmp_path):
    _exercise_departing_document_cannot_start_lease_heartbeat(tmp_path,True,'chromium')

def test_departing_document_cannot_start_lease_heartbeat_chromium_hosted(tmp_path):
    _exercise_departing_document_cannot_start_lease_heartbeat(tmp_path,False,'chromium')

def test_departing_document_cannot_start_lease_heartbeat_webkit_local(tmp_path):
    _exercise_departing_document_cannot_start_lease_heartbeat(tmp_path,True,'webkit')

def test_departing_document_cannot_start_lease_heartbeat_webkit_hosted(tmp_path):
    _exercise_departing_document_cannot_start_lease_heartbeat(tmp_path,False,'webkit')

def test_cancelled_navigation_resumes_lease_and_retains_conflict_fence_chromium(tmp_path):
    _exercise_cancelled_navigation_resumes_lease_and_retains_conflict_fence(tmp_path,'chromium')

def test_cancelled_navigation_resumes_lease_and_retains_conflict_fence_webkit(tmp_path):
    _exercise_cancelled_navigation_resumes_lease_and_retains_conflict_fence(tmp_path,'webkit')

def test_navigation_aborts_inflight_lease_without_unhandled_error_chromium(tmp_path):
    _exercise_navigation_aborts_inflight_lease_without_unhandled_error(tmp_path,'chromium')

def test_navigation_aborts_inflight_lease_without_unhandled_error_webkit(tmp_path):
    _exercise_navigation_aborts_inflight_lease_without_unhandled_error(tmp_path,'webkit')
