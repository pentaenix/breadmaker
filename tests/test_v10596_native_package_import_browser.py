"""Native package UI uses real modal/runtime code and deterministic HTTP jobs.

Backend archive decoding/publication is covered separately. These browser items
isolate interrupted, repeated and stale UI flows, without a provider or server.
"""
from contextlib import contextmanager
import json
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import expect

from scripts.browser_runtime import start_browser, close_browser
from source_contracts import frontend_styles_source_from_styles_path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'a' * 32
IMPORTED = 'b' * 32
PAYLOAD = {'name': 'Synthetic manuscript.breadmaker', 'mimeType': 'application/zip',
           'buffer': b'SYNTHETIC private archive bytes'}


def shell():
    soup = BeautifulSoup((ROOT / 'static/index.html').read_text(), 'html.parser')
    for element in soup.find_all(['script', 'link']):
        element.decompose()
    style = soup.new_tag('style')
    style.string = frontend_styles_source_from_styles_path(ROOT / 'static/styles.css')
    soup.head.append(style)
    source = '''
    const state={runtime:{capabilities:{hosted_persistence:true,account_sessions:true,project_package_import_resumable:true}},profile:{id:'actor-one'},project:{id:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},workspace:{}};
    let workspaceNavigation=0;
    function currentProjectIdentity(){return state.project.id}
    function currentUserIdentity(){return state.profile.id}
    function currentView(){return 'manuscript'}
    async function flushSave(){}
    function closeViewPopover(){} function closeCanvasViewPopover(){}
    function closeMapViewPopover(){} function closeReviewerViewPopover(){}
    function hideProjectMenu(){}
    const opened=[];
    function openWorkspaceProject(id){opened.push(id)}
    '''
    for path in ['static/js/core/i18n.js', 'static/js/core/runtime.js',
                 'static/js/core/ui.js', 'static/js/features/projects/lifecycle.js']:
        source += '\n' + (ROOT / path).read_text()
    source += '\ni18nCatalog=' + (ROOT / 'locales/en.json').read_text() + ';i18nReady=true;'
    source += '''
    document.getElementById('app').inert=false;
    document.getElementById('projectImportInput').onchange=importProjectPackage;
    window.syntheticReady=true;
    '''
    script = soup.new_tag('script')
    script.string = source
    soup.body.append(script)
    return str(soup)


class Jobs:
    def __init__(self):
        self.calls = []
        self.hold = None
        self.held = None
        self.next_batch = 0
        self.phase = 'staging'
        self.preparation_groups = 0
        self.prepared_batches = 0
        self.finish_lost = False
        self.status_error = None
        self.cancel_error = None
        self.job = 'synthetic-native-job'

    def status(self):
        result = dict(job_id=self.job, next_batch=self.next_batch, total_batches=2,
                      phase=self.phase, ready=self.next_batch >= 2,
                      completed_records=self.next_batch * 1000, total_records=2000,
                      completed_images=self.next_batch, total_images=2)
        result['prepared_batches'] = self.prepared_batches
        if self.phase == 'preparing':
            result['total_batches'] = 24
        if self.phase == 'completed':
            result.update(project={'id': IMPORTED}, reload=True)
        return result

    def handler(self, route):
        request = route.request
        url = request.url.split('/api/', 1)[1]
        operation = ('status' if request.method == 'GET' else
                     'step' if '/step?' in url else
                     'finish' if url.endswith('/finish') else
                     'cancel' if url.endswith('/cancel') else 'begin')
        self.calls.append((operation, request))
        if self.hold == operation:
            self.held = route
            return
        self.reply(route, operation)

    def reply(self, route, operation, *, error=None):
        if error:
            route.fulfill(status=error[0], json={'detail': error[1]})
            return
        if operation == 'status' and self.status_error:
            self.reply(route, operation, error=self.status_error)
            return
        if operation == 'cancel' and self.cancel_error:
            self.reply(route, operation, error=self.cancel_error)
            return
        if operation == 'step':
            self.next_batch += 1
            self.phase = 'ready' if self.next_batch == 2 else 'staging'
        elif operation == 'finish':
            self.phase = 'completed'
            if self.finish_lost:
                route.abort('failed')
                return
        elif operation == 'cancel':
            self.phase = 'cancelled'
        elif operation == 'begin':
            if self.preparation_groups:
                self.preparation_groups -= 1
                self.prepared_batches += 8
                self.phase = 'preparing'
            elif self.phase == 'preparing':
                self.phase = 'staging'
        route.fulfill(json=self.status())

    def release(self, operation, *, error=None):
        route, self.held = self.held, None
        assert route is not None
        self.reply(route, operation, error=error)

    def count(self, operation):
        return sum(kind == operation for kind, _ in self.calls)


@contextmanager
def browser():
    driver, instance, _ = start_browser()
    page = instance.new_page(viewport={'width': 1200, 'height': 850})
    page.set_default_timeout(2500)
    service, errors = Jobs(), []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('http://127.0.0.1/native-import', lambda route: route.fulfill(content_type='text/html', body=shell()))
    page.route('**/api/**', service.handler)
    try:
        page.goto('http://127.0.0.1/native-import')
        page.wait_for_function('()=>window.syntheticReady')
        yield page, service
        assert not errors
    finally:
        close_browser(driver, instance)


def select(page, payload=PAYLOAD):
    page.locator('#projectImportInput').set_input_files(payload)


def pending(page):
    return page.evaluate('()=>Object.entries(sessionStorage).filter(([key])=>key.startsWith("breadmaker-pending-package-import:")).map(([,value])=>JSON.parse(value))')


def paused(page):
    expect(page.locator('#projectPackageImportStatus')).to_contain_text('Import paused')


def test_native_package_large_selection_progress_cancel_and_responsive_modal(tmp_path):
    with browser() as (page, service):
        service.hold = 'begin'
        large = {**PAYLOAD, 'buffer': b'SYNTHETIC' + b'x' * 4_690_000}
        select(page, large)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        expect(page.locator('#cancelProjectPackageImport')).to_be_disabled()
        assert page.locator('#projectImportInput').input_value() == ''
        assert page.locator('#projectPackageImportTrack').get_attribute('aria-valuenow') is None
        select(page, large)
        assert service.count('begin') == 1
        service.hold = 'step'
        service.release('begin')
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        expect(page.locator('#projectPackageImportCounts')).to_have_text('Records: 0 / 2,000 · Images: 0 / 2')
        for width, theme in [(1200, 'light'), (780, 'dark'), (390, 'light')]:
            page.set_viewport_size({'width': width, 'height': 850})
            page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
            box = page.locator('.modal-card').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1
            assert page.locator('#backProjectPackageImport').is_visible()
            assert page.locator('#cancelProjectPackageImport').is_visible()
        page.screenshot(path=str(tmp_path / 'native-package-mobile-progress.png'))
        page.locator('#cancelProjectPackageImport').click()
        page.evaluate('()=>document.getElementById("cancelProjectPackageImport").click()')
        service.release('step')
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Import cancelled')
        assert service.count('cancel') == 1 and service.count('finish') == 0
        assert page.evaluate('opened') == []
        assert 'SYNTHETIC' not in json.dumps(pending(page))
        page.locator('#backProjectPackageImport').click()
        assert not page.evaluate('document.getElementById("app").inert')


def test_native_package_reselect_after_reload_resumes_original_job():
    with browser() as (page, service):
        service.hold = 'step'
        select(page)
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        service.next_batch = 1
        service.release('step', error=(503, 'Temporary network failure'))
        paused(page)
        original = pending(page)[0]
        service.hold = None
        page.reload()
        select(page)
        page.wait_for_function('()=>opened.length===1')
        assert page.evaluate('opened') == [IMPORTED]
        assert service.count('begin') == 1
        assert pending(page)[0]['key'] == original['key']
        assert pending(page)[0]['project'] == IMPORTED
        steps = [request.url for kind, request in service.calls if kind == 'step']
        assert steps[-1].endswith('batch=1')


def test_native_package_lost_finish_response_reconciles_exact_publication():
    with browser() as (page, service):
        service.finish_lost = True
        select(page)
        page.wait_for_function('()=>opened.length===1')
        assert page.evaluate('opened') == [IMPORTED]
        assert service.count('begin') == 1 and service.count('finish') == 1 and service.count('status') == 1
        assert pending(page)[0]['phase'] == 'completed'


def test_native_package_unknown_finish_and_generic_404_keep_pointer_on_refresh():
    with browser() as (page, service):
        service.finish_lost = True
        service.status_error = (404, 'Not Found')
        select(page)
        paused(page)
        original = pending(page)[0]
        assert original['publishing'] is True
        page.reload()
        select(page)
        paused(page)
        assert pending(page)[0] == original
        assert service.count('begin') == 1 and service.count('finish') == 1
        service.status_error = None
        service.hold = 'status'
        page.locator('#resumeProjectPackageImport').click()
        page.evaluate('()=>document.getElementById("resumeProjectPackageImport").click()')
        assert service.count('status') == 3
        service.release('status')
        page.wait_for_function('()=>opened.length===1')
        assert page.evaluate('opened') == [IMPORTED]


def test_native_package_cancel_racing_publication_never_claims_cancelled():
    with browser() as (page, service):
        service.hold = 'step'
        select(page)
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        page.locator('#cancelProjectPackageImport').click()
        service.cancel_error = (409, 'HOSTED_IMPORT_ALREADY_PUBLISHED')
        service.release('step')
        service.phase = 'completed'
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Import complete')
        assert page.evaluate('opened') == []
        assert pending(page)[0]['project'] == IMPORTED
        expect(page.locator('#resumeProjectPackageImport')).to_have_text('Open project')
        page.locator('#resumeProjectPackageImport').click()
        assert page.evaluate('opened') == [IMPORTED]


def test_native_package_stale_modal_failure_does_not_replace_new_dialog():
    with browser() as (page, service):
        service.hold = 'begin'
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        original = pending(page)[0]
        page.evaluate('()=>showModal("Newer dialog","<p>Keep this view</p>")')
        service.release('begin', error=(500, 'Synthetic old failure'))
        page.wait_for_function('()=>!projectPackageImportIntent.busy')
        expect(page.locator('#modalTitle')).to_have_text('Newer dialog')
        assert page.evaluate('opened') == []
        assert pending(page)[0]['key'] == original['key']
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        keys = [request.headers['idempotency-key'] for kind, request in service.calls if kind == 'begin']
        assert keys == [original['key'], original['key']]
        service.release('begin', error=(503, 'Still unavailable'))
        paused(page)


def test_native_package_newer_project_navigation_keeps_completion_pointer():
    with browser() as (page, service):
        service.hold = 'finish'
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Publishing project…')
        expect(page.locator('#cancelProjectPackageImport')).to_be_disabled()
        page.evaluate('()=>{workspaceNavigation++;state.project.id="cccccccccccccccccccccccccccccccc"}')
        service.release('finish')
        page.wait_for_function('()=>!projectPackageImportIntent.busy')
        assert page.evaluate('opened') == []
        assert pending(page)[0]['project'] == IMPORTED


def test_native_package_actor_change_uses_separate_recovery_metadata():
    with browser() as (page, service):
        service.hold = 'begin'
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        service.release('begin', error=(503, 'Try again'))
        paused(page)
        first = pending(page)[0]
        page.evaluate('()=>state.profile.id="actor-two"')
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        records = pending(page)
        assert len(records) == 2 and records[0]['key'] != records[1]['key']
        assert {record['actor'] for record in records} == {'actor-one', 'actor-two'}
        service.release('begin', error=(503, 'Try again'))
        paused(page)
        assert next(record for record in pending(page) if record['actor'] == 'actor-one') == first


def legacy_flow(page, service, *, hosted):
    page.evaluate('(hosted)=>{state.runtime.capabilities.hosted_persistence=hosted;state.runtime.capabilities.project_package_import_resumable=false}', hosted)
    service.hold = 'begin'
    select(page)
    expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
    expect(page.locator('#cancelProjectPackageImport')).to_be_disabled()
    assert service.calls[0][1].url.endswith('/api/projects/import')
    assert page.locator('#projectPackageImportTrack').get_attribute('aria-valuenow') is None
    assert pending(page) == []
    service.release('begin', error=(500, 'Synthetic package failure'))
    paused(page)
    expect(page.locator('#projectPackageImportError')).to_have_text('Synthetic package failure')
    expect(page.locator('#resumeProjectPackageImport')).not_to_be_visible()
    page.locator('#backProjectPackageImport').click()
    assert not page.evaluate('document.getElementById("app").inert')


def test_native_package_local_sqlite_retains_one_shot_route():
    with browser() as (page, service):
        legacy_flow(page, service, hosted=False)


def test_native_package_old_hosted_server_retains_one_shot_route():
    with browser() as (page, service):
        legacy_flow(page, service, hosted=True)


def test_native_package_requests_do_not_join_another_projects_undo_action():
    with browser() as (page, service):
        page.evaluate('''async()=>{
            hostedStructuralActionToken='unrelated-manuscript-action';
            for(const url of ['/api/projects/import?resumable=true','/api/projects/import/jobs/job/step?batch=0','/api/projects/import/jobs/job/finish','/api/projects/import/jobs/job/cancel','/api/tasks'])await api(url,{method:'POST'});
        }''')
        for _, request in service.calls[:-1]:
            assert 'x-breadmaker-action' not in request.headers
            assert request.headers['idempotency-key']
        assert service.calls[-1][1].headers['x-breadmaker-action'] == 'unrelated-manuscript-action'


def test_native_package_close_before_save_flush_prevents_upload():
    with browser() as (page, service):
        page.evaluate('()=>{flushSave=()=>new Promise(resolve=>window.finishFlush=resolve)}')
        select(page)
        page.locator('#modalClose').click()
        page.evaluate('()=>finishFlush()')
        page.wait_for_function('()=>!projectPackageImportIntent.busy')
        assert service.calls == [] and pending(page) == []
        assert page.evaluate('opened') == []
        assert not page.evaluate('document.getElementById("app").inert')


def test_native_package_preparing_job_replays_same_upload_key_before_steps():
    with browser() as (page, service):
        service.hold = 'begin'
        select(page)
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Uploading and preparing package…')
        service.release('begin', error=(503, 'Preparation interrupted'))
        paused(page)
        original = pending(page)[0]
        page.evaluate('''()=>{
            const key=Object.keys(sessionStorage).find(key=>key.startsWith('breadmaker-pending-package-import:'));
            const value=JSON.parse(sessionStorage.getItem(key));value.job='synthetic-native-job';value.phase='preparing';sessionStorage.setItem(key,JSON.stringify(value));
        }''')
        service.phase = 'preparing'
        service.hold = None
        page.reload()
        select(page)
        page.wait_for_function('()=>opened.length===1')
        keys = [request.headers['idempotency-key'] for kind, request in service.calls if kind == 'begin']
        assert keys == [original['key'], original['key']]
        assert [kind for kind, _ in service.calls] == ['begin', 'status', 'begin', 'step', 'step', 'finish']
        assert page.evaluate('opened') == [IMPORTED]


def test_native_package_preparation_advances_same_key_before_record_steps():
    with browser() as (page, service):
        service.preparation_groups = 2
        select(page)
        page.wait_for_function('()=>opened.length===1')
        assert [kind for kind, _ in service.calls] == ['begin', 'begin', 'begin', 'step', 'step', 'finish']
        keys = [request.headers['idempotency-key'] for kind, request in service.calls if kind == 'begin']
        assert len(set(keys)) == 1 and keys[0] == pending(page)[0]['key']


def test_native_package_preparation_failure_pauses_until_explicit_resume():
    with browser() as (page, service):
        service.preparation_groups = 2
        service.hold = 'begin'
        select(page)
        page.wait_for_function('()=>projectPackageImportIntent.busy')
        service.release('begin')
        page.wait_for_function('()=>projectPackageImportIntent.job?.prepared_batches===8')
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert service.count('begin') == 2 and service.held is not None
        service.release('begin', error=(504, 'Preparation request timed out'))
        paused(page)
        original = pending(page)[0]
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert service.count('begin') == 2 and service.count('step') == 0
        assert original['phase'] == 'preparing' and page.evaluate('opened') == []
        service.hold = None
        page.locator('#resumeProjectPackageImport').click()
        page.wait_for_function('()=>opened.length===1')
        keys = [request.headers['idempotency-key'] for kind, request in service.calls if kind == 'begin']
        assert keys == [original['key']] * 4


def test_native_package_preparation_can_cancel_between_groups():
    with browser() as (page, service):
        service.preparation_groups = 2
        service.hold = 'begin'
        select(page)
        service.release('begin')
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        # The next request may already be in flight; its response may persist
        # the next prefix but must never start record staging after cancellation.
        page.locator('#cancelProjectPackageImport').click()
        if service.held is not None:
            service.release('begin')
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Import cancelled')
        assert service.count('begin') <= 2 and service.count('cancel') == 1
        assert service.count('step') == 0 and service.count('finish') == 0
        assert page.evaluate('opened') == []


def test_native_package_stalled_preparation_response_does_not_loop():
    with browser() as (page, service):
        service.hold = 'begin'
        select(page)
        route, service.held = service.held, None
        assert route is not None
        service.phase = 'preparing'
        route.fulfill(json=service.status())
        paused(page)
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        assert service.count('begin') == 1 and service.count('step') == 0
        assert not pending(page)[0].get('job')
        assert page.evaluate('opened') == []


def test_native_package_unconfirmed_cancel_preserves_job_until_confirmed():
    with browser() as (page, service):
        service.hold = 'step'
        select(page)
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        page.locator('#cancelProjectPackageImport').click()
        service.cancel_error = (503, 'Cancellation not confirmed')
        service.release('step')
        paused(page)
        assert pending(page)[0]['phase'] == 'staging'
        assert page.evaluate('opened') == []
        assert service.count('finish') == 0
        service.cancel_error = None
        page.locator('#resumeProjectPackageImport').click()
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        page.locator('#cancelProjectPackageImport').click()
        service.release('step')
        expect(page.locator('#projectPackageImportStatus')).to_have_text('Import cancelled')
        assert service.count('cancel') == 2 and service.count('finish') == 0


def test_native_package_expired_job_requires_explicit_new_attempt():
    with browser() as (page, service):
        service.hold = 'step'
        select(page)
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        original = pending(page)[0]
        service.release('step', error=(409, 'HOSTED_IMPORT_EXPIRED'))
        paused(page)
        expect(page.locator('#resumeProjectPackageImport')).to_have_text('Start again')
        assert pending(page)[0]['key'] == original['key'] and service.count('begin') == 1
        service.hold = None
        page.locator('#resumeProjectPackageImport').click()
        page.wait_for_function('()=>opened.length===1')
        assert pending(page)[0]['key'] != original['key']
        assert service.count('begin') == 2


def test_native_package_not_found_keeps_original_key_and_job():
    with browser() as (page, service):
        service.hold = 'step'
        select(page)
        expect(page.locator('#cancelProjectPackageImport')).to_be_enabled()
        service.release('step', error=(503, 'Connection interrupted'))
        paused(page)
        original = pending(page)[0]
        service.status_error = (404, 'HOSTED_IMPORT_NOT_FOUND')
        page.locator('#resumeProjectPackageImport').click()
        paused(page)
        assert pending(page)[0] == original and service.count('begin') == 1
        expect(page.locator('#resumeProjectPackageImport')).to_have_text('Resume')
