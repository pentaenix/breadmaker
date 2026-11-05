"""Actual export menus, download bytes, failure feedback and modal ownership."""
import zipfile
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser


def _open(page):
    page.locator('#brandButton').click();page.locator('#projectMenuExport').press('ArrowRight')
    page.locator('#projectMenuExportZip').click()
    expect(page.locator('#modalTitle')).to_have_text('Export readable ZIP')


def download(home,local):
    with browser(home,local=local) as (base,page,pid):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        # Export must flush unsaved writing before taking its snapshot.
        page.locator('.manuscript-block').first.fill('Synthetic export pending save')
        _open(page)
        with page.expect_download() as download:
            page.locator('#zipExportConfirm').click()
        downloaded=download.value
        assert downloaded.suggested_filename.endswith('-readable.zip')
        with zipfile.ZipFile(downloaded.path()) as archive:
            text=archive.read(next(name for name in archive.namelist() if name.startswith('documents/') and name.endswith('.txt'))).decode()
            assert 'Synthetic export pending save' in text
            assert 'Project.breadmaker' in archive.namelist()
        expect(page.locator('#modal')).to_be_hidden()
        assert not errors,errors


def test_local_readable_zip_download_flushes_pending_writing(tmp_path):download(tmp_path,True)


def test_hosted_readable_zip_download_flushes_pending_writing(tmp_path):download(tmp_path,False)


def _geometry(home,width,theme):
    with browser(home,local=True) as (base,page,pid):
        page.set_viewport_size({'width':width,'height':800});page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
        _open(page)
        assert page.locator('#modal').evaluate('(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
        expect(page.locator('#zipExportConfirm')).to_be_in_viewport()
        page.keyboard.press('Escape');expect(page.locator('#brandButton')).to_be_focused()


def test_zip_modal_phone_and_keyboard_focus(tmp_path):_geometry(tmp_path,390,'light')


def test_zip_modal_narrow_desktop_dark_theme(tmp_path):_geometry(tmp_path,860,'dark')


def test_zip_busy_failure_and_late_completion_preserve_new_dialog(tmp_path):
    with browser(tmp_path,local=True) as (base,page,pid):
        _open(page);held=[]
        page.route('**/api/export/zip?*',lambda route:held.append(route))
        page.locator('#zipExportConfirm').click();page.wait_for_function('()=>readableZipExportInFlight')
        expect(page.locator('#zipExportConfirm')).to_be_disabled()
        expect(page.locator('#zipExportConfirm')).to_have_attribute('aria-busy','true')
        held[0].fulfill(status=500,json={'detail':'Synthetic ZIP failure'})
        expect(page.locator('#zipExportConfirm')).to_be_enabled()
        expect(page.locator('#toast')).to_contain_text('Synthetic ZIP failure')
        held.clear();page.locator('#zipExportConfirm').click();page.wait_for_function('()=>readableZipExportInFlight')
        page.keyboard.press('Escape')
        page.evaluate('()=>showModal("Keep this new dialog","Later dialog")')
        with page.expect_download():held[0].fulfill(status=200,body=b'Synthetic download',headers={'Content-Type':'application/zip'})
        expect(page.locator('#modalTitle')).to_have_text('Keep this new dialog')
        page.wait_for_function('()=>!readableZipExportInFlight')
