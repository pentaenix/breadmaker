"""Revision transport/lifecycle UI against a real hosted editor and Undo API.

The staged-job protocol is intercepted here; backend staging/CAS has separate
service tests. Final publication uses the real bounded inline import so these
checks also prove visible manuscript restoration through application Undo.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.readable_export_browser_fixture import browser


PAYLOAD = {'name': 'Synthetic revision.txt', 'mimeType': 'text/plain',
           'buffer': b'Imported revision paragraph'}
PENDING = "()=>Object.keys(sessionStorage).filter(key=>key.startsWith('breadmaker-pending-manuscript-revision:'))"


def _enable(page):
    page.evaluate("()=>{state.runtime.capabilities.hosted_manuscript_revisions=true}")


def _choose(page, mode='replace'):
    page.locator('#importInput').set_input_files(PAYLOAD)
    page.locator('#importDestinationReplace' if mode == 'replace' else '#importDestinationTabs').click()
    code = page.locator('.danger-confirm-code').inner_text()
    assert len(code) == 5
    expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
    page.locator('#dangerConfirmInput').fill(code[:4])
    expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
    page.locator('#dangerConfirmInput').fill(code)
    page.locator('#dangerCodeConfirm').click()


def _prepare(page):
    _enable(page)
    page.locator('.manuscript-block').first.fill('Original revision paragraph')
    page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')


class RevisionProtocol:
    def __init__(self, base, page, mode='replace'):
        self.base, self.page, self.mode = base, page, mode
        self.starts, self.steps, self.finishes, self.cancels = [], [], [], []
        self.statuses = []
        self.upload = None
        self.result = None
        self.current = self.status(0)
        self.on_start = self.start
        self.on_step = self.step
        self.on_finish = self.finish
        self.on_status = self.read
        self.on_cancel = self.cancel
        page.route('**/api/import?resumable=true&*', lambda route: self.on_start(route))
        page.route('**/api/documents/import?resumable=true&*', lambda route: self.on_start(route))
        page.route('**/api/import/revisions/revision-synthetic', lambda route: self.on_status(route))
        page.route('**/api/import/revisions/revision-synthetic/step?*', lambda route: self.on_step(route))
        page.route('**/api/import/revisions/revision-synthetic/finish', lambda route: self.on_finish(route))
        page.route('**/api/import/revisions/revision-synthetic/cancel', lambda route: self.on_cancel(route))

    def status(self, batch, **extra):
        return dict(job_id='revision-synthetic', next_batch=batch, total_batches=2,
                    ready=batch == 2, **extra)

    def start(self, route):
        self.starts.append(route.request)
        self.upload = route.request
        route.fulfill(json=self.current)

    def step(self, route):
        self.steps.append(route.request)
        self.current = self.status(self.current['next_batch']+1)
        route.fulfill(json=self.current)

    def publish(self, request):
        url = self.upload.url.replace('resumable=true&', '')
        headers = {'Origin': self.base, 'Content-Type': self.upload.headers['content-type'],
                   'X-Breadmaker-Project': request.headers['x-breadmaker-project'],
                   'X-Breadmaker-Action': request.headers['x-breadmaker-action'],
                   'Idempotency-Key': 'synthetic-publication'}
        response = self.page.request.post(url, data=self.upload.post_data_buffer, headers=headers)
        assert response.ok, response.text()
        self.result = dict(response.json(), undo_token=request.headers['x-breadmaker-action'], undo_available=True)
        self.current = dict(self.status(2), completed=True, result=self.result)
        return self.result

    def finish(self, route):
        self.finishes.append(route.request)
        route.fulfill(json=self.publish(route.request))

    def read(self, route):
        self.statuses.append(route.request)
        route.fulfill(json=self.current)

    def cancel(self, route):
        self.cancels.append(route.request)
        route.fulfill(json={'ok': True, 'cancelled': True})


def _revision_batches_publish_once_and_undo_restores_prose(tmp_path, mode):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        if mode == 'append':
            page.set_viewport_size({'width': 430, 'height': 800})
        protocol = RevisionProtocol(base, page, mode)
        held = []
        protocol.on_start = lambda route: held.append(route)
        _choose(page, mode)
        page.wait_for_function('()=>!!document.querySelector("#manuscriptImportProgress")')
        expect(page.locator('#cancelImportManuscript')).to_be_disabled()
        expect(page.locator('#modalClose')).to_be_disabled()
        assert page.locator('.modal-card').evaluate('(node)=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
        page.keyboard.press('Escape')
        expect(page.locator('#modal')).to_be_visible()
        # No timer invents progress while preparation has not returned a job.
        page.wait_for_timeout(220)
        expect(page.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '0')
        protocol.current = protocol.status(0, phase='adopting')
        protocol.on_step = lambda route: held.append(route)
        protocol.start(held.pop())
        expect(page.locator('#cancelImportManuscript')).to_be_enabled()
        expect(page.locator('#manuscriptImportProgressStatus')).to_have_text('Checking the existing manuscript…')
        if mode == 'append':
            page.screenshot(path=str(tmp_path/'revision-progress-narrow.png'))
        assert len(held) == 1
        protocol.current = protocol.status(0)
        held.pop().fulfill(json=protocol.current)
        page.wait_for_function('()=>document.querySelector("#manuscriptImportProgressStatus").textContent.includes("0 / 2")')
        protocol.on_step = protocol.step
        protocol.on_finish = lambda route: held.append(route)
        protocol.step(held.pop())
        expect(page.locator('#manuscriptImportProgressStatus')).to_have_text('Publishing completed manuscript…')
        expect(page.locator('#cancelImportManuscript')).to_be_disabled()
        protocol.finish(held.pop())
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('.manuscript-block').filter(has_text='Imported revision paragraph')).to_be_visible()
        if mode == 'append':
            assert page.evaluate('()=>state.activeDocumentId') == protocol.result['first_document_id']
        assert len(protocol.starts) == len(protocol.finishes) == 1
        assert page.evaluate(PENDING) == []
        page.locator('#undoBtn').click()
        page.wait_for_function('()=>state.documents.length===1')
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')
        assert page.evaluate('()=>state.documents.length') == 1


def test_revision_cancel_waits_for_pending_step_and_does_not_publish(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        held = []
        protocol.on_step = lambda route: held.append(route)
        protocol.on_cancel = lambda route: held.append(route)
        _choose(page)
        expect(page.locator('#cancelImportManuscript')).to_be_enabled()
        page.locator('#cancelImportManuscript').click()
        expect(page.locator('#cancelImportManuscript')).to_be_disabled()
        assert len(held) == 1 and not protocol.cancels and not protocol.finishes
        # A ready response must still settle the already-requested cancellation.
        held.pop().fulfill(json=protocol.status(2))
        page.wait_for_function('()=>document.querySelector("#cancelImportManuscript").textContent==="Cancelling…"')
        page.wait_for_timeout(50)
        assert len(held) == 1
        protocol.cancel(held.pop())
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('#toast')).to_contain_text('Import cancelled')
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')
        assert not protocol.finishes and len(protocol.cancels) == 1
        assert page.evaluate(PENDING) == []


def test_revision_phase_progress_uses_content_counts_not_overall_cursor(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        protocol.current = dict(protocol.status(0), next_batch=7, total_batches=3,
                                phase='adopting', adoption={'records_validated': 73, 'next_step': 7})
        phases = [dict(next_batch=8, total_batches=4, phase='images', image_next=1, image_total=2),
                  dict(next_batch=9, total_batches=11, phase='content', content_batch=0, content_batches=2),
                  dict(next_batch=10, total_batches=11, phase='content', content_batch=1, content_batches=2),
                  dict(next_batch=11, total_batches=11, phase='content', content_batch=2, content_batches=2, ready=True)]
        labels = []
        def phased_step(route):
            protocol.steps.append(route.request)
            labels.append(page.locator('#manuscriptImportProgressStatus').inner_text())
            protocol.current = {**protocol.status(0), **phases.pop(0)}
            route.fulfill(json=protocol.current)
        protocol.on_step = phased_step
        _choose(page)
        expect(page.locator('#modal')).to_be_hidden()
        assert [request.url.rsplit('batch=', 1)[1] for request in protocol.steps] == ['7', '8', '9', '10']
        assert labels == ['Checking the existing manuscript: 73 records', 'Preparing images: 1 / 2',
                          'Importing content: 0 / 2', 'Importing content: 1 / 2']
        expect(page.locator('.manuscript-block').first).to_have_text('Imported revision paragraph')


def test_revision_interrupted_batch_resumes_same_job_after_reload(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        def interrupt(route):
            if 'batch=1' in route.request.url:
                route.fulfill(status=503, json={'detail': 'Synthetic batch interruption'})
            else:
                protocol.step(route)
        protocol.on_step = interrupt
        _choose(page)
        expect(page.locator('#manuscriptImportProgress')).to_have_class(re.compile('failed'))
        keys = page.evaluate(PENDING)
        assert len(keys) == 1
        saved = page.evaluate('(key)=>JSON.parse(sessionStorage.getItem(key))', keys[0])
        assert set(saved) == {'signature', 'key', 'job'}
        assert 'Imported revision paragraph' not in str(saved)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        _enable(page)
        protocol.on_step = protocol.step
        _choose(page)
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text('Imported revision paragraph')
        assert len(protocol.starts) == len(protocol.statuses) == len(protocol.finishes) == 1
        assert [request.url.rsplit('batch=', 1)[1] for request in protocol.steps] == ['0', '1']
        page.locator('#undoBtn').click()
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')


def _revision_unknown_publication_survives_reload_and_recovers_original_undo(tmp_path, mode, undo_available):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page, mode)
        rollbacks = []
        page.on('request', lambda request: rollbacks.append(request.url) if '/api/undo/rollback/' in request.url else None)
        def lose_finish(route):
            protocol.finishes.append(route.request)
            protocol.publish(route.request)
            route.abort('failed')
        protocol.on_finish = lose_finish
        protocol.on_status = lambda route: route.abort('failed')
        _choose(page, mode)
        expect(page.locator('#manuscriptImportProgress')).to_have_class(re.compile('failed'))
        expect(page.locator('.manuscript-import-error')).to_contain_text('Publication could not be confirmed')
        assert not rollbacks and len(page.evaluate(PENDING)) == 1
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        _enable(page)
        protocol.result['undo_available'] = undo_available
        protocol.on_status = protocol.read
        _choose(page, mode)
        expect(page.locator('#modal')).to_be_hidden()
        assert len(protocol.starts) == len(protocol.finishes) == 1
        assert page.evaluate('()=>state.undoStack.filter(item=>item.kind==="structural").map(item=>item.token)') == ([protocol.result['undo_token']] if undo_available else [])
        assert page.evaluate(PENDING) == []
        if undo_available:
            page.locator('#undoBtn').click()
            page.evaluate('async()=>await state.historyChain')
            expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')
        else:
            expect(page.locator('#toast')).to_contain_text('Undo is no longer available')
            expect(page.locator('.manuscript-block').first).to_have_text('Imported revision paragraph')


def _revision_published_cancel_response_recovers_success_instead_of_claiming_cancelled(tmp_path, status_available):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        held, rollbacks = [], []
        page.on('request', lambda request: rollbacks.append(request.url) if '/api/undo/rollback/' in request.url else None)
        protocol.on_step = lambda route: held.append(route)
        def already_published(route):
            protocol.cancels.append(route.request)
            protocol.publish(route.request)
            route.fulfill(status=409, json={'detail': 'HOSTED_IMPORT_ALREADY_PUBLISHED_USE_UNDO'})
        protocol.on_cancel = already_published
        if not status_available:
            protocol.on_status = lambda route: route.abort('failed')
        _choose(page)
        expect(page.locator('#cancelImportManuscript')).to_be_enabled()
        page.locator('#cancelImportManuscript').click()
        held.pop().fulfill(json=protocol.status(2))
        if not status_available:
            expect(page.locator('.manuscript-import-error')).to_contain_text('server reports that this import was published')
            assert not rollbacks and len(page.evaluate(PENDING)) == 1
            page.locator('#closeFailedImport').click()
            protocol.on_status = protocol.read
            _choose(page)
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text('Imported revision paragraph')
        expect(page.locator('#toast')).not_to_contain_text('cancelled')
        assert len(protocol.cancels) == 1 and not protocol.finishes and not rollbacks
        page.locator('#undoBtn').click()
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')


def test_revision_undo_recording_failure_does_not_rollback_published_content(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        failed, rollbacks = [], []
        def fail_undo_once(route):
            if not failed:
                failed.append(route.request)
                route.fulfill(status=503, json={'detail': 'Synthetic Undo recording interruption'})
            else:
                route.continue_()
        page.route('**/api/undo/finish/*', fail_undo_once)
        page.on('request', lambda request: rollbacks.append(request.url) if '/api/undo/rollback/' in request.url else None)
        _choose(page)
        expect(page.locator('#manuscriptImportProgressStatus')).to_have_text('Import complete; Undo needs attention')
        expect(page.locator('.manuscript-block').first).to_have_text('Imported revision paragraph')
        assert not rollbacks and len(page.evaluate(PENDING)) == 1
        page.locator('#closeFailedImport').click()
        _choose(page)
        expect(page.locator('#modal')).to_be_hidden()
        assert len(protocol.starts) == len(protocol.finishes) == 1
        assert page.evaluate('()=>state.undoStack.filter(item=>item.kind==="structural").map(item=>item.token)') == [protocol.result['undo_token']]
        assert page.evaluate(PENDING) == []
        page.locator('#undoBtn').click()
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')


def _terminal_revision_requires_fresh_confirmation_without_automatic_restart(tmp_path, code):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        protocol.on_step = lambda route: route.fulfill(status=503, json={'detail': 'Synthetic interruption'})
        _choose(page)
        expect(page.locator('#manuscriptImportProgress')).to_have_class(re.compile('failed'))
        page.locator('#closeFailedImport').click()
        protocol.on_status = lambda route: route.fulfill(status=409, json={'detail': {'code': code, 'message': code}})
        _choose(page)
        expect(page.locator('.manuscript-import-error')).to_contain_text('confirm a new import')
        assert len(protocol.starts) == 1 and not protocol.finishes
        assert page.evaluate(PENDING) == []
        expect(page.locator('.manuscript-block').first).to_have_text('Original revision paragraph')


def test_revision_stale_response_preserves_replacement_dialog_and_pins_rollback(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = RevisionProtocol(base, page)
        held, rollbacks = [], []
        protocol.on_step = lambda route: held.append(route)
        page.on('request', lambda request: rollbacks.append(request) if '/api/undo/rollback/' in request.url else None)
        _choose(page)
        expect(page.locator('#cancelImportManuscript')).to_be_enabled()
        page.evaluate("()=>{state.workspace.active_project_id='another-project';showModal('Another dialog','<p>Keep this dialog</p>')}")
        held.pop().fulfill(json=protocol.status(2))
        page.evaluate('async()=>await state.structuralChain')
        expect(page.locator('#modalTitle')).to_have_text('Another dialog')
        expect(page.locator('#modalBody')).to_have_text('Keep this dialog')
        assert not protocol.finishes
        assert len(rollbacks) == 1 and rollbacks[0].headers['x-breadmaker-project'] == pid
        assert page.evaluate('()=>state.undoStack.filter(item=>item.kind==="structural").length') == 0


def test_structural_action_header_follows_explicit_originating_project(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        requests = []
        def capture(route):
            requests.append(route.request)
            route.fulfill(json={'ok': True})
        page.route('**/api/synthetic-action-scope', capture)
        page.evaluate('''async pid=>{
            hostedStructuralActionToken='synthetic-action';hostedStructuralActionProject=pid;
            await api('/api/synthetic-action-scope',{method:'POST'});
            await api('/api/synthetic-action-scope',{method:'POST',headers:{'X-Breadmaker-Project':'other'}});
            state.workspace.active_project_id='other';
            await api('/api/synthetic-action-scope',{method:'POST'});
            await api('/api/synthetic-action-scope',{method:'POST',headers:{'X-Breadmaker-Project':pid}});
            hostedStructuralActionToken=null;hostedStructuralActionProject=null;
        }''', pid)
        assert [request.headers.get('x-breadmaker-action') for request in requests] == ['synthetic-action', None, None, 'synthetic-action']
        finishes = []
        def finish_elsewhere(route):
            finishes.append(route.request)
            route.fulfill(json={'changed': True})
        page.route('**/api/undo/finish/synthetic-finished', finish_elsewhere)
        assert page.evaluate('async pid=>await finishStructuralUndo("synthetic-finished","Synthetic finished",{projectId:pid})', pid) is False
        assert finishes[0].headers['x-breadmaker-project'] == pid
        assert page.evaluate('()=>state.undoStack.filter(item=>item.kind==="structural").length') == 0


# Keep one explicit function per browser item for the release gate accounting.

def test_revision_batches_publish_once_and_undo_restores_prose_replace(tmp_path):
    _revision_batches_publish_once_and_undo_restores_prose(tmp_path, 'replace')

def test_revision_batches_publish_once_and_undo_restores_prose_append(tmp_path):
    _revision_batches_publish_once_and_undo_restores_prose(tmp_path, 'append')

def test_revision_unknown_publication_survives_reload_and_recovers_original_undo_replace_true(tmp_path):
    _revision_unknown_publication_survives_reload_and_recovers_original_undo(tmp_path, 'replace', True)

def test_revision_unknown_publication_survives_reload_and_recovers_original_undo_replace_false(tmp_path):
    _revision_unknown_publication_survives_reload_and_recovers_original_undo(tmp_path, 'replace', False)

def test_revision_unknown_publication_survives_reload_and_recovers_original_undo_append_true(tmp_path):
    _revision_unknown_publication_survives_reload_and_recovers_original_undo(tmp_path, 'append', True)

def test_revision_published_cancel_response_recovers_success_instead_of_claiming_cancelled_true(tmp_path):
    _revision_published_cancel_response_recovers_success_instead_of_claiming_cancelled(tmp_path, True)

def test_revision_published_cancel_response_recovers_success_instead_of_claiming_cancelled_false(tmp_path):
    _revision_published_cancel_response_recovers_success_instead_of_claiming_cancelled(tmp_path, False)

def test_terminal_revision_requires_fresh_confirmation_without_automatic_restart_hosted_replay_superseded(tmp_path):
    _terminal_revision_requires_fresh_confirmation_without_automatic_restart(tmp_path, 'HOSTED_REPLAY_SUPERSEDED')

def test_terminal_revision_requires_fresh_confirmation_without_automatic_restart_hosted_import_expired(tmp_path):
    _terminal_revision_requires_fresh_confirmation_without_automatic_restart(tmp_path, 'HOSTED_IMPORT_EXPIRED')

def test_terminal_revision_requires_fresh_confirmation_without_automatic_restart_hosted_import_cancelled(tmp_path):
    _terminal_revision_requires_fresh_confirmation_without_automatic_restart(tmp_path, 'HOSTED_IMPORT_CANCELLED')

def test_terminal_revision_requires_fresh_confirmation_without_automatic_restart_hosted_generation_conflict(tmp_path):
    _terminal_revision_requires_fresh_confirmation_without_automatic_restart(tmp_path, 'HOSTED_GENERATION_CONFLICT')
