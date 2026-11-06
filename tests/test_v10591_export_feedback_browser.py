"""Project download errors preserve writing, modal ownership and recovery limits."""
import zipfile
import json
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser


def _native(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuExport').press('ArrowRight')
    page.locator('#projectMenuExportProject').click()


def test_hosted_native_codec_error_stays_in_phone_workspace_with_zip_alternative(tmp_path):
    with browser(tmp_path,local=False) as (base,page,pid):
        page.set_viewport_size({'width':390,'height':844})
        page.locator('.manuscript-block').first.fill('Synthetic text kept after export refusal')
        page.route('**/api/projects/*/export?*',lambda route:route.fulfill(status=400,json={'detail':'HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED'}))
        _native(page)
        expect(page.locator('#modalTitle')).to_have_text('This project needs a hosted recovery snapshot')
        assert page.url==base+'/p/'+pid
        expect(page.locator('#modalBody')).to_contain_text('Nothing has been removed')
        expect(page.locator('#projectExportReadable')).to_be_in_viewport()
        assert page.locator('#modal .modal-card').evaluate('(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
        page.locator('#projectExportReadable').click()
        expect(page.locator('#modalTitle')).to_have_text('Export readable ZIP')
        expect(page.locator('#modalBody')).to_contain_text('Restore compatibility and upload limits')
        page.keyboard.press('Escape');expect(page.locator('#brandButton')).to_be_focused()
        page.reload();page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('.manuscript-block').first).to_contain_text('Synthetic text kept after export refusal')


def _native_download(tmp_path,local):
    with browser(tmp_path,local=local) as (base,page,pid):
        page.locator('.manuscript-block').first.fill('Synthetic native download keeps pending words')
        with page.expect_download() as result:_native(page)
        assert page.url==base+'/p/'+pid
        with zipfile.ZipFile(result.value.path()) as archive:
            assert 'project.sqlite3' in archive.namelist()
        page.reload();page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('.manuscript-block').first).to_contain_text('Synthetic native download keeps pending words')


def test_local_native_download_saves_pending_text_without_leaving_workspace(tmp_path):
    _native_download(tmp_path,True)

def test_hosted_native_download_saves_pending_text_without_leaving_workspace(tmp_path):
    _native_download(tmp_path,False)


def test_late_native_failure_preserves_another_modal_and_duplicate_guard(tmp_path):
    with browser(tmp_path,local=False) as (base,page,pid):
        held=[]
        page.route('**/api/projects/*/export?*',lambda route:held.append(route))
        _native(page);page.wait_for_function('()=>projectFileExportInFlight')
        _native(page)
        assert len(held)==1
        page.locator('#brandButton').click();page.locator('#projectMenuSettings').click()
        expect(page.locator('#modalTitle')).to_have_text('Project Settings')
        held[0].fulfill(status=400,json={'detail':'HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED'})
        page.wait_for_function('()=>!projectFileExportInFlight')
        expect(page.locator('#modalTitle')).to_have_text('Project Settings')
        expect(page.locator('#toast')).to_contain_text('hosted recovery snapshot')
        assert page.url==base+'/p/'+pid


def test_html_auth_response_is_not_downloaded_as_native_project(tmp_path):
    with browser(tmp_path,local=False) as (base,page,pid):
        downloaded=[];page.on('download',lambda download:downloaded.append(download))
        page.route('**/api/projects/*/export?*',lambda route:route.fulfill(status=200,content_type='text/html',body='<html>Sign in</html>'))
        _native(page)
        expect(page.locator('#toast')).to_contain_text('Project could not be downloaded')
        assert page.url==base+'/p/'+pid and not downloaded


def test_hosted_json_download_remains_in_workspace_and_keeps_pending_text(tmp_path):
    with browser(tmp_path,local=False) as (base,page,pid):
        page.locator('.manuscript-block').first.fill('Synthetic JSON pending words')
        page.locator('#brandButton').click();page.locator('#projectMenuExport').press('ArrowRight')
        with page.expect_download() as result:page.locator('#projectMenuExportJsonProject').click()
        data=json.loads(result.value.path().read_text())
        assert data['format']=='breadmaker-hosted-json' and data['source_project_id']==pid
        assert 'Synthetic JSON pending words' in json.dumps(data['entities'])
        assert page.url==base+'/p/'+pid
