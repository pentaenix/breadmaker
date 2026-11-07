"""Requested delete/import controls use the real local/hosted UI and persistence."""
import pytest
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser


def _confirm_code(page):
    code = page.locator('.danger-confirm-code').inner_text()
    assert len(code) == 5
    expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
    page.locator('#dangerConfirmInput').fill(code[:4])
    expect(page.locator('#dangerCodeConfirm')).to_be_disabled()
    page.locator('#dangerConfirmInput').fill(code)
    page.locator('#dangerCodeConfirm').click()


@pytest.mark.parametrize('local', [True, False])
def test_chapter_delete_has_no_modal_and_toast_undo_restores_prose(tmp_path, local):
    with browser(tmp_path, local=local) as (base, page, pid):
        page.locator('.manuscript-block').first.fill('Chapter prose to recover')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.evaluate("()=>openContextMenu(180,180,chapterMenuItems(state.chapters[0]))")
        page.locator('#contextMenu').get_by_role('button', name='Delete', exact=True).click()
        expect(page.locator('#modal')).to_be_hidden()
        undo = page.locator('#toast').get_by_role('button', name='Undo', exact=True)
        expect(undo).to_be_visible()
        assert page.evaluate('()=>state.undoStack.length') > 0
        undo.click()
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to recover')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text('Chapter prose to recover')


def test_hosted_replace_append_are_enabled_and_existing_prose_requires_five_characters(tmp_path):
    with browser(tmp_path, local=False) as (base, page, pid):
        payload = {'name': 'Synthetic.txt', 'mimeType': 'text/plain', 'buffer': b'New first paragraph\nNew second paragraph'}
        page.locator('#importInput').set_input_files(payload)
        expect(page.locator('#importDestinationReplace')).to_be_enabled()
        expect(page.locator('#importDestinationTabs')).to_be_enabled()
        page.locator('#cancelImportManuscript').click()
        page.locator('.manuscript-block').first.fill('Existing manuscript stays until confirmed')
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        page.locator('#importInput').set_input_files(payload)
        page.locator('#importDestinationReplace').click()
        expect(page.locator('#dangerConfirmInput')).to_be_visible()
        page.locator('#dangerCodeCancel').click()
        expect(page.locator('#importDestinationReplace')).to_be_visible()
        expect(page.locator('.manuscript-block').first).to_have_text('Existing manuscript stays until confirmed')
        page.locator('#importDestinationReplace').click()
        _confirm_code(page)
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text('New first paragraph')
        page.locator('#undoBtn').click()
        expect(page.locator('.manuscript-block').first).to_have_text('Existing manuscript stays until confirmed')
        page.locator('#importInput').set_input_files(payload)
        page.locator('#importDestinationTabs').click()
        _confirm_code(page)
        expect(page.locator('#modal')).to_be_hidden()
        expect(page.locator('.manuscript-block').first).to_have_text('New first paragraph')
        assert page.evaluate('()=>state.documents.length') == 2
        page.locator('#undoBtn').click()
        page.wait_for_function('()=>state.documents.length===1')
        expect(page.locator('.manuscript-block').first).to_have_text('Existing manuscript stays until confirmed')
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text('Existing manuscript stays until confirmed')
