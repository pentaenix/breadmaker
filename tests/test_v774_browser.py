from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page

CLIENT = 'v41812-browser'


def _release(base):
    try:
        requests.post(base + '/api/lease/release', headers={'X-Breadmaker-Client': CLIENT}, timeout=2)
    except Exception:
        pass


def _write_editor_text(page, text: str):
    page.evaluate(
        """async text=>{
          const el=document.querySelector('.manuscript-block');
          el.innerText=text;
          el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
          await flushSave();
        }""",
        text,
    )


def test_v774_browser_history_uses_immutable_rows_day_groups_and_read_only_compare(scene_live_server, scene_browser):
    base = scene_live_server
    _new_project(base, '7.7.4 History UI Browser')
    page, errors = _page(base, scene_browser)
    try:
        page.wait_for_selector('#editor')
        _write_editor_text(page, 'Historical browser state')
        page.evaluate("async()=>await captureAutoHistory('quiet')")
        _write_editor_text(page, 'Current browser state')

        history = page.evaluate("async()=>await api('/api/history/v2')")
        assert history['revisions']
        assert all(len(str(row['revision_id'])) == 32 for row in history['revisions'])

        page.evaluate("()=>openHistory()")
        page.wait_for_selector('#modal:not(.hidden) .history-modal-row')
        assert page.locator('.history-day-heading').first.is_visible()
        row = page.locator('.history-modal-row').first
        row.locator('[data-history-more]').click()
        menu = page.locator('#historyRowMenu')
        assert menu.locator('[data-history-menu="compare"]').count() == 1
        assert menu.locator('[data-history-menu="checkpoint"]').count() == 1
        assert menu.locator('[data-history-menu="restore"]').count() == 1
        assert menu.locator('[data-history-menu="delete"]').count() == 0
        assert menu.locator('[data-history-menu="export"]').count() == 0
        menu.locator('[data-history-menu="compare"]').click()
        page.wait_for_selector('#historyCompareToolbar:not(.hidden)')
        assert page.locator('#splitPane').is_visible()
        assert 'History' in page.locator('#splitTitle').inner_text()
        assert page.locator('.manuscript-block').first.evaluate('el=>el.isContentEditable') is True
        assert 'read only' in page.locator('#splitBody .checkpoint-readonly-note').inner_text().lower()
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)


def test_v774_browser_restore_is_confirmed_and_advances_forward_generation(scene_live_server, scene_browser):
    base = scene_live_server
    _tab, chapter = _new_project(base, '7.7.4 Restore Browser')
    page, errors = _page(base, scene_browser)
    try:
        page.wait_for_selector('#editor')
        _write_editor_text(page, 'Browser state to restore')
        page.evaluate("async()=>await captureAutoHistory('quiet')")
        _write_editor_text(page, 'Browser current before restore')
        before = page.evaluate("async()=>+(await api('/api/bootstrap')).project.project_generation")

        page.evaluate("()=>openHistory()")
        page.wait_for_selector('#modal:not(.hidden) .history-modal-row')
        page.locator('.history-modal-row').first.locator('[data-history-more]').click()
        page.locator('#historyRowMenu [data-history-menu="restore"]').click()
        page.wait_for_selector('#dialogConfirm')
        message = page.locator('.dialog-message').inner_text()
        assert 'new Current state' in message
        assert 'Recovery Backup' in message
        page.locator('#dialogConfirm').click()

        # The shared browser harness uses set_content(), so location.reload() correctly
        # navigates its synthetic page back to about:blank. Validate the completed UI
        # action through the real server after that navigation rather than reinjecting UI.
        import time
        deadline=time.time()+8
        after=None
        while time.time()<deadline:
            after=int(requests.get(base+'/api/bootstrap',timeout=5).json()['project']['project_generation'])
            if after==before+1: break
            time.sleep(.1)
        assert after == before + 1
        manuscript=requests.get(base+f'/api/chapters/{chapter["id"]}',timeout=5).json()
        assert manuscript['blocks'][0]['text_content'].strip() == 'Browser state to restore'

        history = requests.get(base+'/api/history/v2',timeout=5).json()
        assert any(row.get('cause') == 'pre_restore_safety' and int(row['generation_end']) == before for row in history['revisions'])
        assert any(row.get('cause') == 'restore' and int(row['generation_end']) == after for row in history['revisions'])
        assert not errors, errors
    finally:
        _release(base)
        _shutdown_page(page)
