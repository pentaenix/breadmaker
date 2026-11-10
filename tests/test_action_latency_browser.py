"""Real task Undo/Redo issues one list read without losing restored state."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_task_undo_redo_coalesces_workspace_entry_and_explicit_refresh(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        page.goto(base,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        page.locator('.main-tab[data-view="tasks"]').click()
        page.locator('#newTaskBtn').click()
        page.locator('#taskName').fill('Synthetic coalesced task')
        page.locator('#saveTask').click()
        expect(page.locator('#taskList')).to_contain_text('Synthetic coalesced task')
        page.locator('#taskList [data-task-more]').click()
        page.get_by_text('Delete',exact=True).click()
        expect(page.locator('#taskList')).not_to_contain_text('Synthetic coalesced task')
        reads=[]
        page.on('request',lambda r:reads.append(r.url) if r.method=='GET' and r.url==base+'/api/tasks' else None)
        page.locator('#undoBtn').click()
        expect(page.locator('#taskList')).to_contain_text('Synthetic coalesced task')
        page.wait_for_function('()=>!state.historyBusy')
        assert len(reads)==1,reads
        reads.clear()
        page.locator('#redoBtn').click()
        expect(page.locator('#taskList')).not_to_contain_text('Synthetic coalesced task')
        page.wait_for_function('()=>!state.historyBusy')
        assert len(reads)==1,reads
