from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import shutil
import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v4181_scene_boundaries_and_header_surface_contract(client,fresh_project):
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert 'id="manuscriptPaper" class="paper" contenteditable="true"' in html
    assert '<div id="chapterTitle" class="chapter-title"' in html
    assert 'appendSceneEndGhost' in js and 'End · ${escapeHtml(scene.title)}' in js
    assert '.ghost-scene-end' in css
    assert '.scene-nav-empty{padding:14px 16px;text-align:center' in css

    boot=fresh_project['bootstrap']; chapter=boot['chapters'][0]
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}")); block=data['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{'id':block['id'],'content_html':'Scene ending','text_content':'Scene ending'}]}))
    scene=assert_ok(client.post('/api/scenes',json={'block_ids':[block['id']]}))
    row=next(s for s in assert_ok(client.get('/api/bootstrap'))['scenes'] if s['id']==scene['id'])
    assert str(row['last_block_id'])==str(block['id'])
    assert row['last_chapter_id']==chapter['id']


def test_v4181_chapter_header_and_prose_can_be_selected_together():
    executable=next((shutil.which(name) for name in ('chromium','chromium-browser','google-chrome','google-chrome-stable','chrome') if shutil.which(name)),None)
    if not executable:
        pytest.skip('Chromium is not installed')
    soup=BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'),'html.parser')
    for link in soup.find_all('link'): link.decompose()
    for script in soup.find_all('script'): script.decompose()
    style=soup.new_tag('style');style.string=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'));soup.head.append(style)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=executable)
        try:
            page=browser.new_page(viewport={'width':900,'height':700})
            page.set_content(str(soup),wait_until='domcontentloaded')
            page.evaluate("""() => {
              const title=document.querySelector('#chapterTitle');title.textContent='Chapter 4: Crossing';
              const editor=document.querySelector('#editor');editor.innerHTML='<div class="manuscript-block-wrap"><div class="manuscript-block">First paragraph of prose.</div></div>';
            }""")
            title=page.locator('#chapterTitle').bounding_box(); prose=page.locator('.manuscript-block').bounding_box()
            page.mouse.move(title['x']+4,title['y']+title['height']/2)
            page.mouse.down();page.mouse.move(prose['x']+120,prose['y']+prose['height']/2,steps=12);page.mouse.up()
            selected=page.evaluate('getSelection().toString()')
            assert 'Chapter 4: Crossing' in selected
            assert 'First paragraph' in selected
        finally:
            browser.close()
