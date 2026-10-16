from source_contracts import frontend_styles_source_from_styles_path
from pathlib import Path
import shutil

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def _browser_html():
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'),'html.parser')
    for link in soup.find_all('link'):
        link.decompose()
    for script in soup.find_all('script'):
        script.decompose()
    style=soup.new_tag('style')
    style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    soup.head.append(style)
    return str(soup)


def test_v4189_tab_rail_and_panel_empty_states_render_consistently():
    executable=next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(name)),None)
    if not executable:
        pytest.skip('Chromium is not installed')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':1100,'height':760})
            page.set_content(_browser_html(),wait_until='domcontentloaded')
            assert page.locator('.tabs-head .nav-section-label').inner_text().strip()=='TABS'
            assert page.locator('#toggleBookNavBtn').is_visible()
            assert page.locator('#tabsMenuBtn').is_visible()
            assert page.locator('#bookViewBtn').inner_text().strip()=='Full Text'
            assert page.locator('#navigatorMenuBtn').is_visible()
            assert page.locator('#addDocumentBtn').count()==0
            assert page.locator('#addChapter').count()==0
            assert page.locator('#navigatorShowBtn').count()==0

            # Whole-rail collapse keeps the hamburger and compact rail while hiding Tab/navigation headers.
            page.locator('.book-nav').evaluate("el => el.classList.add('collapsed')")
            page.wait_for_timeout(180)
            collapsed=page.evaluate("""() => ({
              width: document.querySelector('.book-nav').getBoundingClientRect().width,
              toggle: getComputedStyle(document.querySelector('#toggleBookNavBtn')).display,
              tabsMenu: getComputedStyle(document.querySelector('#tabsMenuBtn')).display,
              docs: getComputedStyle(document.querySelector('#documentList')).display,
              nav: getComputedStyle(document.querySelector('.document-navigation')).display
            })""")
            assert collapsed['width'] <= 60
            assert collapsed['toggle'] != 'none'
            assert collapsed['tabsMenu'] == 'none'
            assert collapsed['docs'] == 'none'
            assert collapsed['nav'] == 'none'
            page.locator('.book-nav').evaluate("el => el.classList.remove('collapsed')")

            # Give Cuts and Notes representative empty states without executing app JS.
            page.evaluate("""() => {
              const cuts=document.querySelector('#cutsPanel');
              cuts.classList.remove('hidden');
              document.querySelector('#cutsList').innerHTML='<div class="manuscript-panel-empty cuts-empty"><b>No cuts yet</b><span>Cut prose will appear here.</span></div>';
            }""")
            cut_metrics=page.evaluate("""() => {
              const list=document.querySelector('#cutsList').getBoundingClientRect();
              const empty=document.querySelector('#cutsList .manuscript-panel-empty').getBoundingClientRect();
              const title=getComputedStyle(document.querySelector('#cutsList .manuscript-panel-empty b'));
              return {listTop:list.top,listHeight:list.height,emptyTop:empty.top,emptyHeight:empty.height,font:title.fontSize,align:title.textAlign};
            }""")
            empty_center=cut_metrics['emptyTop']+cut_metrics['emptyHeight']/2
            list_center=cut_metrics['listTop']+cut_metrics['listHeight']/2
            assert abs(empty_center-list_center)<24
            assert cut_metrics['align']=='center'
            assert float(cut_metrics['font'].replace('px',''))<=13.5

            page.evaluate("""() => {
              document.querySelector('#cutsPanel').classList.add('hidden');
              const notes=document.querySelector('#commentsPanel');
              notes.classList.remove('hidden');
              notes.classList.add('detail-hidden');
              document.querySelector('#commentsList').innerHTML='<div class="manuscript-panel-empty comments-empty"><b>No notes yet</b><span>Notes will appear here.</span></div>';
              const detail=document.querySelector('#commentsDetail');
              detail.classList.add('hidden');
              detail.innerHTML='';
            }""")
            note_metrics=page.evaluate("""() => {
              const panel=document.querySelector('#commentsPanel').getBoundingClientRect();
              const list=document.querySelector('#commentsList').getBoundingClientRect();
              const detail=document.querySelector('#commentsDetail');
              return {panelHeight:panel.height,listHeight:list.height,detailDisplay:getComputedStyle(detail).display,emptyText:document.querySelector('#commentsList').innerText};
            }""")
            assert note_metrics['detailDisplay']=='none'
            assert note_metrics['listHeight']>note_metrics['panelHeight']*0.5
            assert 'No note selected' not in note_metrics['emptyText']
        finally:
            browser.close()
