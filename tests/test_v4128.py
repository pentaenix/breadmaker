from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path
from bs4 import BeautifulSoup
from conftest import assert_ok


def _set_text(client, chapter_id: int, text: str):
    return assert_ok(client.put(
        f'/api/chapters/{chapter_id}/blocks',
        json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]},
    ))


def test_v4128_manuscript_cuts_static_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    soup=BeautifulSoup(html,'html.parser')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html

    view_focus=soup.select_one('#viewFocusBtn'); checkpoint=soup.select_one('#checkpointBtn'); eye=soup.select_one('#editorsEyeBtn')
    assert view_focus and checkpoint and eye and soup.select_one('#focusBtn') is None
    assert checkpoint.find_next('button').get('id')=='cutsBtn'
    assert soup.select_one('#cutsBtn').find_next('button').get('id')=='editorsEyeBtn'
    assert checkpoint.get('aria-describedby')=='checkpointTooltip'
    assert checkpoint.get('title') is None
    tooltip=soup.select_one('#checkpointTooltip')
    assert tooltip and 'Deliberately save a version' in tooltip.get_text(' ',strip=True)

    toolbar=soup.select_one('#selectionToolbar')
    direct=[b.get('data-selection-action') for b in toolbar.select('button[data-selection-action]')]
    assert 'cuts' in direct
    assert 'task' not in direct and 'map' not in direct
    assert direct.index('cuts') < direct.index('comment')
    assert "{label:'Task',action:()=>taskFromSelection(items)}" in js
    assert "{label:'Map',action:()=>mapFromSelection(items)}" in js
    assert "else if(action==='cuts')cutSelection(items)" in js
    assert "confirmDialog('Move Selection to Cuts'" not in js
    assert 'showCutsOnboardingOnce' in js
    assert '/api/ui-state/cuts-onboarding-seen' in js


def test_v4128_session_undo_reverses_cuts_without_making_history_own_them(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _set_text(client,chapter['id'],'A B C')
    history_before=[x['id'] for x in assert_ok(client.get('/api/history'))['revisions']]
    token=assert_ok(client.post('/api/undo/begin?label=Move%20to%20Cuts'))['token']

    created=assert_ok(client.post('/api/cuts',json={
        'title':'Cut B','content_html':'<p>B</p>','source_chapter_id':chapter['id'],
    }))
    assert created['id']
    _set_text(client,chapter['id'],'A C')
    assert_ok(client.post(f'/api/undo/finish/{token}'))
    assert len(assert_ok(client.get('/api/cuts')))==1
    assert [x['id'] for x in assert_ok(client.get('/api/history'))['revisions']]==history_before

    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    assert assert_ok(client.get('/api/cuts'))==[]
    restored=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['text_content'] for b in restored['blocks']]==['A B C']

    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    cuts=assert_ok(client.get('/api/cuts'))
    assert len(cuts)==1 and cuts[0]['content_text'].strip()=='B'
    redone=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['text_content'] for b in redone['blocks']]==['A C']
