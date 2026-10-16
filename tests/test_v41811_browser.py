from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
import shutil
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def _browser_html():
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'),'html.parser')
    for link in soup.find_all('link'): link.decompose()
    for script in soup.find_all('script'): script.decompose()
    style=soup.new_tag('style');style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'));soup.head.append(style)
    return str(soup)


def test_v41811_full_text_compound_control_layout():
    executable=next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(name)),None)
    if not executable: pytest.skip('Chromium is not installed')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':1000,'height':720});page.set_content(_browser_html())
            control=page.locator('#fullDocumentControl');full=page.locator('#bookViewBtn');menu=page.locator('#navigatorMenuBtn')
            assert control.is_visible() and full.is_visible() and menu.is_visible()
            metrics=page.evaluate("""()=>{const c=document.querySelector('#fullDocumentControl').getBoundingClientRect(),f=document.querySelector('#bookViewBtn').getBoundingClientRect(),m=document.querySelector('#navigatorMenuBtn').getBoundingClientRect();return {cLeft:c.left,cRight:c.right,cCenter:c.left+c.width/2,fCenter:f.left+f.width/2,mCenter:m.left+m.width/2}}""")
            assert abs(metrics['cCenter']-metrics['fCenter'])<2
            assert metrics['mCenter']<metrics['cRight'] and metrics['mCenter']>metrics['cCenter']
            # When only one navigator is available, the whole switch row can disappear without affecting the embedded menu.
            page.locator('#manuscriptNavToolbar').evaluate("el=>el.classList.add('hidden')")
            assert not page.locator('#manuscriptNavToolbar').is_visible()
            assert menu.is_visible()
            assert page.locator('#viewChaptersBtn').inner_text().strip().startswith('Show Chapters')
        finally:
            browser.close()
