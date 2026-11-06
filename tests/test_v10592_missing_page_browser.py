"""Actual hosted composition keeps unknown pages404 and known SPA views usable."""
import os
from pathlib import Path
from playwright.sync_api import expect
from tests.readable_export_browser_fixture import browser


def test_real_hosted_unknown_pages_are_html404_without_spa_or_api_fallback(tmp_path):
    with browser(tmp_path,local=False) as (base,page,pid):
        result=page.goto(base+'/qa-page-that-does-not-exist',wait_until='domcontentloaded')
        assert result.status==404
        expect(page.get_by_role('heading',name='Page not found',exact=True)).to_be_visible()
        expect(page.get_by_role('link',name='Return to Breadmaker',exact=True)).to_have_attribute('href','/')
        assert page.request.get(base+'/static/css/shared/missing-page.css').status==200
        headers={'Accept':'text/html'}
        nested=page.request.get(base+f'/p/{pid}/not-a-workspace',headers=headers)
        assert nested.status==404 and nested.text()==result.text()
        api=page.request.get(base+'/api/qa-missing-endpoint',headers=headers)
        asset=page.request.get(base+'/static/qa-missing-image.png',headers=headers)
        assert api.status==asset.status==404
        assert api.json()['detail']=='Not Found' and asset.json()['detail']=='Not Found'
        known=page.goto(base+f'/p/{pid}/maps',wait_until='domcontentloaded')
        assert known.status==200
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        expect(page.locator('#mapsView')).to_be_visible()


def test_missing_page_phone_spanish_keyboard_and_return_link(tmp_path):
    with browser(tmp_path,local=False) as (base,page,_):
        page.set_viewport_size({'width':390,'height':844})
        page.set_extra_http_headers({'Accept-Language':'es-MX,es;q=.9,en;q=.5'})
        result=page.goto(base+'/another-missing-page',wait_until='domcontentloaded');assert result.status==404
        expect(page.get_by_role('heading',name='Página no encontrada',exact=True)).to_be_visible()
        assert page.locator('html').get_attribute('lang')=='es'
        assert page.locator('.identity-card').evaluate('(el)=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
        assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
        page.keyboard.press('Tab');expect(page.locator('.identity-brand')).to_be_focused()
        page.keyboard.press('Tab');expect(page.get_by_role('link',name='Volver a Breadmaker',exact=True)).to_be_focused()
        if directory:=os.environ.get('BREADMAKER_QA_EVIDENCE_DIR'):
            page.screenshot(path=str(Path(directory)/'hosted-missing-page-phone-es.png'))
        page.keyboard.press('Enter')
        page.wait_for_url(base+'/**')
        expect(page.get_by_role('heading',name='Página no encontrada',exact=True)).to_have_count(0)
