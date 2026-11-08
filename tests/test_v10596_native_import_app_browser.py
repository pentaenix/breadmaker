"""Native file selection through the real app and resumable hosted API."""
import re

from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_native_package_picker_runs_real_resumable_api_and_preserves_source(tmp_path):
    with hosted_browser(tmp_path) as (base, page):
        errors, imports = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: imports.append((response.request.method, response.url, response.status))
                if '/api/projects/import' in response.url else None)
        page.goto(base, wait_until='domcontentloaded')
        page.wait_for_url(re.compile(r'/p/[0-9a-f]{32}$'))
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        source_id = page.url.rsplit('/', 1)[-1]
        marker = 'Synthetic native package browser round trip.'
        page.locator('.manuscript-block').first.fill(marker)
        page.wait_for_function('()=>!state.dirty&&!state.saveInFlight')
        exported = page.request.get(base + '/api/projects/' + source_id + '/export')
        assert exported.ok, exported.text()
        package = tmp_path / 'Synthetic-Portable.breadmaker'
        package.write_bytes(exported.body())
        page.locator('#projectImportInput').set_input_files(str(package))
        page.wait_for_url(lambda url: str(url).startswith(base + '/p/') and not str(url).endswith(source_id))
        expect(page.locator('.manuscript-block').first).to_have_text(marker)
        imported_id = page.url.rsplit('/', 1)[-1]
        assert imported_id != source_id
        assert any('?resumable=true' in url and status == 200 for _, url, status in imports), imports
        assert any('/step?batch=' in url and status == 200 for _, url, status in imports), imports
        assert any(url.endswith('/finish') and status == 200 for _, url, status in imports), imports
        assert all(status < 400 for _, _, status in imports), imports
        assert not errors, errors
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.manuscript-block').first).to_have_text(marker)
        source = page.request.get(base + '/api/bootstrap', headers={'X-Breadmaker-Project': source_id})
        assert source.ok
        assert any(row.get('text_content') == marker for row in source.json()['blocks'])
        second_export = page.request.get(base + '/api/projects/' + imported_id + '/export')
        assert second_export.ok, second_export.text()
