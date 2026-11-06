"""Silent, isolated interaction checks with deliberately held network responses."""
from time import perf_counter
import os
from pathlib import Path
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser
from tests.test_v10581_canvas_navigation_browser import _board
from tests.test_v10580_targets_browser import open_targets


def _capture(page,name):
    directory=os.environ.get('BREADMAKER_QA_EVIDENCE_DIR')
    if directory:
        path=Path(directory)/name
        page.screenshot(path=str(path))


def _review(page):
    return page.evaluate("""async()=>{
      const review=await api('/api/reviews',{method:'POST',body:JSON.stringify({name:'Synthetic interaction review'})});
      await refresh(false);setView('reviews');selectReview(review.id);return review.id;
    }""")


def test_chime_separator_exists_only_when_sound_picker_is_present(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        open_targets(page)
        page.locator('[data-target-toggle=enabled]').click()
        chime=page.locator('[data-target-toggle=chime]');row=chime.locator('..')
        expect(page.locator('#targetsSounds')).to_be_hidden()
        assert row.evaluate('(el)=>getComputedStyle(el).borderBottomWidth')=='0px'
        chime.click();expect(page.locator('#targetsSounds')).to_be_visible()
        assert row.evaluate('(el)=>getComputedStyle(el).borderBottomWidth')=='1px'
        chime.click();expect(page.locator('#targetsSounds')).to_be_hidden()
        assert row.evaluate('(el)=>getComputedStyle(el).borderBottomWidth')=='0px'
        _capture(page,'targets-chime-off.png')


def test_canvas_menu_does_not_wait_for_populate_and_ignores_late_response(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        _,errors=_board(page);held=[]
        page.route('**/api/canvas-populate*',lambda route:held.append(route))
        box=page.locator('#canvasStage').bounding_box();start=perf_counter()
        page.mouse.click(box['x']+35,box['y']+35,button='right')
        expect(page.locator('#contextMenu')).to_be_visible(timeout=500)
        expect(page.locator('#contextMenu').get_by_role('button',name='Add card here',exact=True)).to_be_enabled()
        elapsed=(perf_counter()-start)*1000
        assert len(held)==1
        print(f'Canvas menu while Populate request remains held: {elapsed:.1f}ms')
        page.keyboard.press('Escape')
        page.evaluate("()=>openContextMenu(30,100,[{label:'Newer synthetic menu',action:()=>{}}])")
        held[0].continue_()
        page.wait_for_function('()=>!!state.canvasPopulateCatalog')
        expect(page.locator('#contextMenu')).to_contain_text('Newer synthetic menu')
        assert not errors,errors


def test_hosted_review_participant_creation_does_not_replay_access_in_undo(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        rid=_review(page);writes=[]
        page.on('request',lambda request:writes.append(request) if request.method=='POST' and request.url.split('?')[0].endswith('/participants') else None)
        page.locator('#reviewParticipantsBtn').click();page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Synthetic reviewer')
        page.locator('#reviewParticipantConfirm').click()
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('#reviewParticipantsPanelBody')).to_contain_text('Synthetic reviewer')
        assert len(writes)==1 and 'x-breadmaker-action' not in writes[0].headers
        people=page.evaluate('(rid)=>api(`/api/reviews/${rid}/participants`)',rid)
        assert any(person['display_name']=='Synthetic reviewer' for person in people)
        assert 'HOSTED_ACTION_ENTITY_UNSUPPORTED' not in page.locator('#toast').inner_text()


def test_review_marker_empty_overflow_is_inside_split_button_at_phone_and_desktop(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        _review(page);page.locator('#reviewMarkersBtn').click()
        for width in [860,390]:
            page.set_viewport_size({'width':width,'height':844})
            page.evaluate('(theme)=>document.documentElement.dataset.theme=theme','dark' if width==390 else 'light')
            main=page.locator('#reviewMarkerEmptyNew');more=page.locator('#reviewMarkerEmptyMore')
            expect(main).to_be_visible();expect(more).to_be_visible()
            a=main.bounding_box();b=more.bounding_box()
            assert b['x']>=a['x'] and b['x']+b['width']<=a['x']+a['width']
            assert b['y']>=a['y'] and b['y']+b['height']<=a['y']+a['height']
            _capture(page,f'review-marker-empty-{width}.png')
        more.click();expect(page.locator('#contextMenu')).to_be_visible()


def _task(page):
    return page.evaluate("""async()=>{
      const task=await api('/api/tasks',{method:'POST',body:JSON.stringify({title:'Immediate synthetic task',status:'open'})});
      invalidateTaskCache();state.taskFilter='open';setView('tasks');await loadTasks();return task.id;
    }""")


def test_task_moves_before_patch_returns_and_failure_restores_it(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        task=_task(page);held=[];print('TASK_PHASE: seeded',task,flush=True)
        page.route(f'**/api/tasks/{task}*',lambda route:held.append(route) if route.request.method=='PATCH' else route.continue_())
        row=page.locator('.task-row').filter(has_text='Immediate synthetic task');start=perf_counter()
        with page.expect_request(f'**/api/tasks/{task}*'):
            row.locator('input[type=checkbox]').click()
            expect(row).to_have_count(0,timeout=500)
        print('TASK_PHASE: patch held',len(held),flush=True)
        print(f'Task removed from Open before network acknowledgement: {(perf_counter()-start)*1000:.1f}ms')
        page.evaluate('()=>0')
        assert len(held)==1
        held[0].fulfill(status=500,json={'detail':'Synthetic status failure'})
        expect(row).to_have_count(1,timeout=8000)
        expect(row.locator('input[type=checkbox]')).not_to_be_checked()
        saved=page.evaluate("(id)=>apiList('/api/tasks','tasks').then(rows=>rows.find(row=>row.id===id))",task)
        assert saved['status']=='open'


def test_task_uncheck_moves_before_patch_and_keeps_structural_undo(tmp_path):
    with browser(tmp_path,local=False) as (_,page,_):
        task=_task(page)
        page.evaluate('async(id)=>{await setTaskStatus(id,"done");state.taskFilter="done";await loadTasks()}',task)
        held=[];page.route(f'**/api/tasks/{task}*',lambda route:held.append(route) if route.request.method=='PATCH' else route.continue_())
        row=page.locator('.task-row').filter(has_text='Immediate synthetic task')
        with page.expect_request(f'**/api/tasks/{task}*'):
            row.locator('input[type=checkbox]').click();expect(row).to_have_count(0,timeout=500)
        page.evaluate('()=>0');assert len(held)==1
        held[0].continue_();page.wait_for_function('()=>pendingTaskStatuses.size===0')
        assert page.evaluate("(id)=>apiList('/api/tasks','tasks').then(rows=>rows.find(row=>row.id===id))",task)['status']=='open'
        page.unroute(f'**/api/tasks/{task}*')
        page.evaluate('async()=>{await state.structuralChain;await performUndo()}')
        assert page.evaluate("(id)=>apiList('/api/tasks','tasks').then(rows=>rows.find(row=>row.id===id))",task)['status']=='done'


def test_local_review_participant_keeps_existing_undo(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        _review(page)
        page.locator('#reviewParticipantsBtn').click();page.locator('#reviewParticipantsPanelAdd').click()
        page.locator('#reviewParticipantNameInput').fill('Local synthetic reviewer');page.locator('#reviewParticipantConfirm').click()
        expect(page.locator('#reviewParticipantsPanelBody')).to_contain_text('Local synthetic reviewer')
        page.evaluate('async()=>{await state.structuralChain;await performUndo()}')
        expect(page.locator('#reviewParticipantsPanelBody')).not_to_contain_text('Local synthetic reviewer')
