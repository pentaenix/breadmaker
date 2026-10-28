"""Real browser cache reuse for public code only; manuscript data stays private."""
from playwright.sync_api import expect
from tests.test_v10548_browser import hosted_browser


def test_release_stylesheet_reuses_browser_cache_without_changing_cascade(tmp_path):
    with hosted_browser(tmp_path) as (base,page):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        css=page.locator('link[href*="reviews/manuscript-layer.css"]').get_attribute('href')
        first=page.request.get(base+css)
        assert first.ok and first.headers['cache-control']=='public, max-age=31536000, immutable'
        # Real document navigation preserves version keys and stylesheet cascade.
        # Unlike Playwright routing (which disables caching), Resource Timing can
        # observe the browser's own cache without intercepting the request.
        before=page.evaluate('()=>getComputedStyle(document.body).backgroundColor')
        page.goto(base+'/tasks',wait_until='domcontentloaded')
        page.wait_for_function('()=>document.documentElement.dataset.breadmakerBootReady==="true"')
        entry=page.evaluate('''href=>{const e=performance.getEntriesByName(new URL(href,location.origin).href).at(-1);
          return e&&{transfer:e.transferSize,decoded:e.decodedBodySize}}''',css)
        assert entry and entry['transfer']==0 and entry['decoded']>0
        assert page.evaluate('()=>getComputedStyle(document.body).backgroundColor')==before
        expect(page.locator('#app')).to_be_visible()
        assert not errors
