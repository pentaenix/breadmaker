"""Real account fragment/styles, synthetic unavailable billing, no provider calls."""
import json
from pathlib import Path

import pytest
from scripts.browser_runtime import start_browser, close_browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width', [320, 900, 1280])
def test_subscription_unavailable_reuses_account_section_without_actions(width):
    playwright, browser, _ = start_browser()
    try:
        page = browser.new_page(viewport={'width': width, 'height': 720})
        page.set_content('<html><body><div id="fixture" class="account-security-shell"></div></body></html>')
        for path in ['static/styles.css', 'static/css/profile/account-security.css']:
            page.add_style_tag(content=(ROOT/path).read_text())
        page.add_script_tag(content='''
        let catalog={};
        function i18nText(key){return catalog[key]||key}
        function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
        ''')
        page.add_script_tag(content=(ROOT/'static/js/features/account/security.js').read_text())
        for theme in ['light', 'dark']:
            page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
            for locale in ['en', 'es', 'fr', 'it', 'ja', 'pt-BR', 'zh-CN']:
                strings = json.loads((ROOT/f'locales/{locale}.json').read_text())['strings']
                page.evaluate('strings=>{catalog=strings;document.getElementById("fixture").innerHTML=accountSubscriptionSection({billing:{connected:false,status:"unavailable"}})}', strings)
                assert page.locator('h3').inner_text() == strings['account_security.subscription']
                assert page.locator('.account-security-empty').inner_text() == strings['account_security.subscription_unavailable']
                assert page.locator('button,a,input').count() == 0
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert page.locator('section').get_attribute('aria-labelledby') == 'accountSubscriptionHeading'
        for snapshot in [{}, {'billing': None}, {'billing': {'connected': True}}, {'billing': {'connected': False, 'status': 'active'}}]:
            assert page.evaluate('s=>accountSubscriptionSection(s)', snapshot) == ''
        page.close()
    finally:
        close_browser(playwright, browser)
