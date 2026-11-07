"""Bounded first-use preparation precedes real hosted chapter Delete and Undo.

Only the adoption protocol is intercepted. Preparation cannot read prose or
delete on cancellation, and real Undo requests remain project-scoped.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.readable_export_browser_fixture import browser


PENDING = "()=>Object.keys(sessionStorage).filter(key=>key.startsWith('breadmaker-pending-chapter-adoption:'))"


def _prepare(page, *, capability=True):
    page.locator('.manuscript-block').first.fill('Chapter prose to retain')
    page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
    page.evaluate('(enabled)=>{state.runtime.capabilities.hosted_manuscript_revisions=enabled}', capability)
    return page.evaluate('()=>state.chapters[0].id')


def _delete(page, *, focused=False):
    if focused:
        page.evaluate('()=>{window.chapterDeleteDone=false;void deleteFocusedChapterSelection(state.chapters[0].id).finally(()=>window.chapterDeleteDone=true)}')
    else:
        page.evaluate('()=>{window.chapterDeleteDone=false;void deleteChapterPermanently(state.chapters[0]).finally(()=>window.chapterDeleteDone=true)}')


class AdoptionProtocol:
    def __init__(self, page):
        self.page = page
        self.starts, self.steps, self.finishes, self.statuses = [], [], [], []
        self.current = self.status(0)
        self.requests = []
        page.on('request', lambda request: self.requests.append(request) if '/api/' in request.url else None)
        self.on_start, self.on_step, self.on_finish, self.on_status = self.start, self.step, self.finish, self.read
        page.route('**/api/manuscript/adoptions', lambda route: self.on_start(route))
        page.route('**/api/manuscript/adoptions/adoption-synthetic', lambda route: self.on_status(route))
        page.route('**/api/manuscript/adoptions/adoption-synthetic/step?*', lambda route: self.on_step(route))
        page.route('**/api/manuscript/adoptions/adoption-synthetic/finish', lambda route: self.on_finish(route))

    @staticmethod
    def status(step, *, activated=False):
        return dict(job_id='adoption-synthetic', next_step=step, records_validated=step*40,
                    family='chapters', ready=step >= 2, activated=activated, source_generation=3)

    def start(self, route):
        self.starts.append(route.request)
        route.fulfill(json=self.current)

    def step(self, route):
        self.steps.append(route.request)
        self.current = self.status(self.current['next_step']+1)
        route.fulfill(json=self.current)

    def finish(self, route):
        self.finishes.append(route.request)
        self.current = self.status(2, activated=True)
        route.fulfill(json=self.current)

    def read(self, route):
        self.statuses.append(route.request)
        route.fulfill(json=self.current)

    def deletes(self):
        return [request for request in self.requests if request.method == 'DELETE']

    def undo_begins(self):
        return [request for request in self.requests if '/api/undo/begin?' in request.url]


@pytest.mark.parametrize('focused', [False, True])
def test_bounded_adoption_finishes_before_delete_undo_and_does_not_read_prose(tmp_path, focused):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        if focused:
            page.set_viewport_size({'width': 430, 'height': 800})
        protocol = AdoptionProtocol(page)
        held = []
        protocol.on_step = lambda route: held.append(route)
        _delete(page, focused=focused)
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('#chapterPreparationStatus')).to_be_visible()
        expect(page.locator('#modalBody')).to_contain_text('one-time preparation')
        assert page.locator('.modal-card').evaluate('(node)=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
        if focused:
            page.screenshot(path=str(tmp_path/'chapter-preparation-narrow.png'))
        assert not protocol.undo_begins() and not protocol.deletes()
        assert not [request for request in protocol.requests if request.method == 'GET' and ('/api/book?' in request.url or re.search(r'/api/chapters/[^/?]+$', request.url))]
        assert all('x-breadmaker-action' not in request.headers for request in protocol.requests if '/api/manuscript/adoptions' in request.url)
        assert len(held) == 1
        protocol.on_step = protocol.step
        protocol.step(held.pop())
        page.wait_for_function('()=>window.chapterDeleteDone')
        expect(page.locator('#modal')).to_be_hidden()
        assert len(protocol.deletes()) == len(protocol.undo_begins()) == len(protocol.finishes) == 1
        events = [request.url for request in protocol.requests]
        assert events.index(protocol.finishes[0].url) < events.index(protocol.undo_begins()[0].url) < events.index(protocol.deletes()[0].url)
        assert protocol.deletes()[0].headers['x-breadmaker-project'] == pid
        assert protocol.deletes()[0].headers.get('x-breadmaker-action')
        assert page.evaluate(PENDING) == []
        page.locator('#toast').get_by_role('button', name='Undo', exact=True).click()
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to retain')


@pytest.mark.parametrize('capability', [False, True])
def test_small_or_already_adopted_delete_does_not_flash_preparation_modal(tmp_path, capability):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page, capability=capability)
        protocol = AdoptionProtocol(page)
        protocol.current = {'ready': True, 'activated': True}
        page.evaluate('''()=>{window.preparationWasShown=false;window.preparationObserver=new MutationObserver(()=>{if(document.querySelector('#chapterPreparationStatus'))window.preparationWasShown=true});preparationObserver.observe(document.querySelector('#modalBody'),{childList:true,subtree:true})}''')
        _delete(page)
        page.wait_for_function('()=>window.chapterDeleteDone')
        assert len(protocol.starts) == int(capability)
        assert not protocol.steps and not protocol.finishes
        assert len(protocol.deletes()) == 1
        assert page.evaluate('()=>window.preparationWasShown') is False
        expect(page.locator('#modal')).to_be_hidden()


@pytest.mark.parametrize('stage,dismiss', [('start', 'Escape'), ('step', 'button'), ('finish', 'close')])
def test_cancel_close_or_escape_prevents_delete_even_after_preparation_response(tmp_path, stage, dismiss):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = AdoptionProtocol(page)
        held = []
        setattr(protocol, 'on_'+stage, lambda route: held.append(route))
        _delete(page)
        expect(page.locator('#chapterPreparationStatus')).to_be_visible()
        if dismiss == 'Escape':
            page.keyboard.press('Escape')
        elif dismiss == 'close':
            page.locator('#modalClose').click()
        else:
            page.locator('#cancelChapterPreparation').click()
        expect(page.locator('#modal')).to_be_hidden()
        getattr(protocol, stage)(held.pop())
        page.wait_for_function('()=>window.chapterDeleteDone')
        assert not protocol.deletes() and not protocol.undo_begins()
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to retain')
        assert len(page.evaluate(PENDING)) == 1
        if stage == 'step':
            # A later explicit Delete resumes validation; Cancel never starts it.
            protocol.on_step = protocol.step
            _delete(page)
            page.wait_for_function('()=>window.chapterDeleteDone')
            assert len(protocol.starts) == len(protocol.statuses) == len(protocol.deletes()) == 1


def test_immediate_undo_waits_for_preparation_and_delete_as_one_action(tmp_path):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = AdoptionProtocol(page)
        held = []
        protocol.on_start = lambda route: held.append(route)
        _delete(page)
        expect(page.locator('#chapterPreparationStatus')).to_be_visible()
        page.evaluate('()=>{window.chapterUndoDone=false;void appUndo().finally(()=>window.chapterUndoDone=true)}')
        assert not protocol.deletes() and not protocol.undo_begins()
        protocol.start(held.pop())
        page.wait_for_function('()=>window.chapterDeleteDone&&window.chapterUndoDone')
        assert len(protocol.deletes()) == 1
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to retain')


@pytest.mark.parametrize('stage', ['undo_begin', 'delete'])
def test_preparation_cancel_wins_until_actual_delete_starts(tmp_path, stage):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = AdoptionProtocol(page)
        steps, mutation = [], []
        protocol.on_step = lambda route: steps.append(route)
        endpoint = '**/api/undo/begin?*' if stage == 'undo_begin' else '**/api/chapters/*/permanent'
        page.route(endpoint, lambda route: mutation.append(route))
        _delete(page)
        expect(page.locator('#chapterPreparationStatus')).to_be_visible()
        protocol.on_step = protocol.step
        protocol.step(steps.pop())
        for _ in range(30):
            if mutation:
                break
            page.wait_for_timeout(20)
        assert len(mutation) == 1
        if stage == 'undo_begin':
            expect(page.locator('#cancelChapterPreparation')).to_be_enabled()
            page.locator('#cancelChapterPreparation').click()
        else:
            expect(page.locator('#cancelChapterPreparation')).to_be_disabled()
            expect(page.locator('#modalClose')).to_be_disabled()
            page.keyboard.press('Escape')
            expect(page.locator('#modal')).to_be_visible()
        mutation.pop().continue_()
        page.wait_for_function('()=>window.chapterDeleteDone')
        expect(page.locator('#modal')).to_be_hidden()
        assert len(protocol.deletes()) == int(stage == 'delete')
        if stage == 'undo_begin':
            expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to retain')
            assert page.evaluate('()=>state.undoStack.filter(item=>item.kind==="structural").length') == 0


@pytest.mark.parametrize('status,code', [(409, 'HOSTED_GENERATION_CONFLICT'), (410, 'ADOPTION_EXPIRED'), (400, 'HOSTED_PRIVATE_BATCH_NOT_READY')])
def test_preparation_failure_never_falls_back_to_old_delete(tmp_path, status, code):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = AdoptionProtocol(page)
        protocol.on_step = lambda route: route.fulfill(status=status, json={'detail': {'code': code, 'message': code}})
        _delete(page)
        page.wait_for_function('()=>window.chapterDeleteDone')
        assert not protocol.deletes() and not protocol.undo_begins()
        expect(page.locator('#toast')).to_contain_text('chapter was not deleted')
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to retain')
        if status in (409, 410):
            assert page.evaluate(PENDING) == []


def _switch_context(page):
    page.evaluate('''()=>{state.workspace.active_project_id='another-project';state.project={name:'Other project sentinel'};state.documents=[{id:'other-document',title:'Other tab'}];state.undoStack=[];state.undoBytes=0;showModal('Other dialog','<p>Keep this dialog</p>')}''')


@pytest.mark.parametrize('stage', ['adoption', 'delete', 'refresh'])
def test_switch_during_preparation_delete_or_refresh_never_installs_stale_state(tmp_path, stage):
    with browser(tmp_path) as (base, page, pid):
        _prepare(page)
        protocol = AdoptionProtocol(page)
        held = []
        if stage == 'adoption':
            protocol.on_step = lambda route: held.append(route)
        elif stage == 'delete':
            page.route('**/api/chapters/*/permanent', lambda route: held.append(route))
        else:
            page.route('**/api/bootstrap?navigation_only=true', lambda route: held.append(route))
        _delete(page)
        if stage == 'adoption':
            expect(page.locator('#chapterPreparationStatus')).to_be_visible()
        else:
            page.wait_for_function('()=>!!hostedChapterDeletion||window.chapterDeleteDone===false')
            # Let the bounded protocol reach the intercepted delete/bootstrap.
            for _ in range(30):
                if held:
                    break
                page.wait_for_timeout(20)
        assert len(held) == 1
        _switch_context(page)
        if stage == 'adoption':
            protocol.step(held.pop())
        else:
            held.pop().continue_()
        page.wait_for_function('()=>window.chapterDeleteDone')
        assert page.evaluate('()=>state.project.name') == 'Other project sentinel'
        assert page.evaluate('()=>state.documents.map(doc=>doc.id)') == ['other-document']
        assert page.evaluate('()=>state.undoStack.length') == 0
        expect(page.locator('#modalTitle')).to_have_text('Other dialog')
        expect(page.locator('#modalBody')).to_have_text('Keep this dialog')
        if stage == 'adoption':
            assert not protocol.deletes() and not protocol.undo_begins()
        else:
            assert protocol.deletes()[0].headers['x-breadmaker-project'] == pid
            finishes = [request for request in protocol.requests if '/api/undo/finish/' in request.url]
            assert len(finishes) == 1 and finishes[0].headers['x-breadmaker-project'] == pid
