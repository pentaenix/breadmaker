from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
import shutil
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]

def browser_html():
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(),'html.parser')
    for link in soup.find_all('link'): link.decompose()
    for script in soup.find_all('script'): script.decompose()
    style=soup.new_tag('style');style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'));soup.head.append(style)
    return str(soup)

def test_v41810_full_text_button_and_compact_f():
    executable=next((shutil.which(n) for n in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(n)),None)
    if not executable: pytest.skip('Chromium is not installed')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':1000,'height':720});page.set_content(browser_html())
            full=page.locator('#bookViewBtn');assert full.inner_text().strip()=='Full Text'
            fm=full.evaluate("el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {cx:r.left+r.width/2,align:s.textAlign}}")
            control=page.locator('#fullDocumentControl');cm=control.evaluate("el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {cx:r.left+r.width/2,border:s.borderTopWidth}}")
            rm=page.locator('.book-nav').evaluate("el=>{const r=el.getBoundingClientRect();return {cx:r.left+r.width/2}}")
            assert abs(fm['cx']-rm['cx'])<2 and abs(cm['cx']-rm['cx'])<2 and fm['align']=='center' and float(cm['border'][:-2])>=1
            assert page.locator('#tabsToggleBtn').is_visible() and page.locator('#tabsMenuBtn').is_visible()
            page.locator('.book-nav').evaluate("el=>el.classList.add('collapsed')")
            assert page.locator('#collapsedFullTextBtn').is_visible()
            assert page.locator('#collapsedFullTextBtn').inner_text().strip()=='F'
            assert not full.is_visible()
        finally:
            browser.close()
