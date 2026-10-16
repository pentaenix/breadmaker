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


def test_v4176_panels_push_and_split_reflows():
    executable=next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(name)),None)
    if not executable:
        pytest.skip('Chromium is not installed')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':850,'height':800})
            page.set_content(_browser_html(),wait_until='domcontentloaded')
            page.evaluate("""() => {
              const panel=document.querySelector('#checkpointPanel');
              panel.classList.remove('hidden');
              document.querySelector('#checkpointList').innerHTML=Array.from({length:30},(_,i)=>`<div class='checkpoint-list-row'><button class='checkpoint-use'>Checkpoint ${i+1}</button><button class='checkpoint-more'>⋮</button></div>`).join('');
            }""")
            panel=page.evaluate("""() => {
              const live=document.querySelector('#manuscriptPrimaryPane').getBoundingClientRect();
              const p=document.querySelector('#checkpointPanel').getBoundingClientRect();
              const toolbar=document.querySelector('.document-toolbar');
              const list=document.querySelector('#checkpointList');
              return {liveRight:live.right,panelLeft:p.left,position:getComputedStyle(document.querySelector('#checkpointPanel')).position,toolbarClient:toolbar.clientWidth,toolbarScroll:toolbar.scrollWidth,listClient:list.clientHeight,listScroll:list.scrollHeight,listOverflow:getComputedStyle(list).overflowY};
            }""")
            assert panel['position']=='static'
            assert panel['liveRight'] <= panel['panelLeft']+1
            assert panel['toolbarScroll'] > panel['toolbarClient']
            assert panel['listOverflow']=='auto' and panel['listScroll']>panel['listClient']

            page.evaluate("""() => {
              document.querySelector('#checkpointPanel').classList.add('hidden');
              document.querySelector('#splitPane').classList.remove('hidden');
              document.querySelector('#splitBody').classList.add('checkpoint-split-body');
              document.querySelector('#splitBody').innerHTML='<article class="checkpoint-split-paper">'+('<p>Long reference text that must wrap inside its pane instead of overlaying the live manuscript.</p>'.repeat(20))+'</article>';
            }""")
            split=page.evaluate("""() => {
              const live=document.querySelector('#manuscriptPrimaryPane').getBoundingClientRect();
              const pane=document.querySelector('#splitPane').getBoundingClientRect();
              const paper=document.querySelector('.checkpoint-split-paper').getBoundingClientRect();
              const style=getComputedStyle(document.querySelector('#splitPane'));
              return {liveRight:live.right,paneLeft:pane.left,paneRight:pane.right,paperLeft:paper.left,paperRight:paper.right,minWidth:style.minWidth,position:style.position};
            }""")
            assert split['position']=='static'
            assert split['minWidth']=='0px'
            assert split['liveRight'] <= split['paneLeft']+1
            assert split['paperLeft'] >= split['paneLeft']-1
            assert split['paperRight'] <= split['paneRight']+1
        finally:
            browser.close()
