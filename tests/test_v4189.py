from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v4189_tab_navigation_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '<span class="nav-section-label">Tabs</span>' in html
    assert 'id="toggleBookNavBtn"' in html and 'id="tabsMenuBtn"' in html
    assert '>Full Text</button>' in html
    assert 'id="navigatorMenuBtn"' in html
    assert 'id="addDocumentBtn"' not in html and 'id="addChapter"' not in html and 'id="navigatorShowBtn"' not in html
    assert "function tabsMenuItems(){return [{label:'New Tab'" in js
    assert 'id="tabsToggleBtn"' in html and "label:'Import Tabs…'" in js
    assert "{label:sceneMode?'Add Scene':'Add Chapter'" in js
    assert "Show Chapters" in js and "Show Scenes" in js
    assert "$('#toggleBookNavBtn').onclick" in js
    assert "api('/api/ui-state/tabs-expanded'" in js and "api('/api/ui-state/book-nav-collapsed'" in js
    assert "$('#activeDocumentTitle').textContent=title" in js
    assert '.book-nav.collapsed{width:58px;min-width:58px}' in css
    assert '.book-nav.collapsed .document-list,.book-nav.collapsed .document-navigation' in css


def test_v4189_tab_menu_and_details_follow_standard():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    start=js.index('function documentMenuItems(doc)')
    end=js.index('function openDocumentMenu',start)
    menu=js[start:end]
    assert "Import tabs" not in menu and menu.index("label:'Details'") < menu.index("label:'Delete'")
    assert "showModal('Tab Details'" in js
    assert 'id="deleteDocumentFromDetails" class="danger">Delete tab…</button><button id="saveTabConfig">Save</button>' in js
    assert "api(`/api/documents/${doc.id}`,{method:'DELETE'})" in js
    assert "aria-label=\"Tab actions for" in js


def test_v4189_tab_delete_promotes_primary_and_preserves_one_tab(client,fresh_project):
    primary=next(d for d in fresh_project['bootstrap']['tabs'] if d['is_manuscript'])
    secondary=assert_ok(client.post('/api/documents',json={'title':'Research','description':'Notes'}))
    result=assert_ok(client.delete(f"/api/documents/{primary['id']}"))
    assert result['next_document_id']==secondary['id']
    boot=assert_ok(client.get('/api/bootstrap'))
    assert [d['id'] for d in boot['tabs']]==[secondary['id']]
    assert boot['tabs'][0]['is_manuscript']==1
    blocked=client.delete(f"/api/documents/{secondary['id']}")
    assert blocked.status_code==409
    assert 'must keep at least one document' in blocked.text.lower()


def test_v4189_manuscript_panel_empty_states_and_note_deselection_contract():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    for cls in ('marker-empty-state','checkpoint-empty-list','cuts-empty','comments-empty'):
        assert f'manuscript-panel-empty {cls}' in js
    assert '.manuscript-panel-empty{' in css
    assert "box.innerHTML='';box.classList.add('hidden');panel?.classList.add('detail-hidden')" in js
    assert "function deselectCommentThread()" in js
    assert "if(!event.target.closest('[data-comment-row]'))deselectCommentThread()" in js
    assert 'No note selected' not in js
    assert '.comments-panel.detail-hidden .comments-list{flex:1 1 auto;border-bottom:0}' in css


def test_v4189_chapter_details_label_is_consistent():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "showModal('Chapter Details',`<div class=\"field\"><label>Details</label>" in js


def test_v4189_documentation_records_tab_navigation_contract():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '### 4.18.9 Tab rail, Tab deletion, and Manuscript empty states' in readme
    assert '### 4.18.9 Manuscript Tab-navigation contract' in architecture
    assert 'Tabs → selected Tab → Full Text / Chapters / Scenes' in architecture
    assert 'Add Chapter/Add Scene' in architecture
