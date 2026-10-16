from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v41810_tab_header_and_full_text_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'id="tabsToggleBtn"' in html and 'id="tabsMenuBtn"' in html
    assert '>Full Text</button>' in html
    assert 'id="collapsedFullTextBtn"' in html and '>F</button>' in html
    assert "state.tabsExpanded?'▾':'▸'" in js
    assert "function tabsMenuItems(){return [{label:'New Tab'" in js
    assert "{label:'Import Tabs…'" in js
    assert "Hide Tabs':'Show Tabs" not in js[js.index('function tabsMenuItems'):js.index('function openTabsMenu')]
    assert '.book-nav.collapsed .collapsed-full-text{display:block}' in css
    assert '.book-nav.collapsed .scene-nav-empty{display:none!important}' in css


def test_v41810_tab_item_menu_scope_and_order():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    start=js.index('function documentMenuItems(doc)')
    end=js.index('function openDocumentMenu',start)
    menu=js[start:end]
    assert 'Import tabs' not in menu and 'Import Tabs' not in menu
    assert "label:'Create canvas from tab…'" in menu
    assert "label:'Set as Main Manuscript'" in menu
    assert menu.index("label:'Create canvas from tab…'") < menu.index("label:'Details'") < menu.index("label:'Delete'")
    assert "doc.is_manuscript?[]:[{label:'Set as Main Manuscript'" in menu
    # Canvas creation is unconditional rather than hidden on the primary Tab.
    assert "doc.is_manuscript?[]:[{label:'Create canvas" not in menu


def test_v41810_can_transfer_main_manuscript_role(client,fresh_project):
    primary=next(d for d in fresh_project['bootstrap']['tabs'] if d['is_manuscript'])
    secondary=assert_ok(client.post('/api/documents',json={'title':'Second Draft','description':'alternate'}))
    result=assert_ok(client.post(f"/api/documents/{secondary['id']}/make-manuscript"))
    assert result['id']==secondary['id']
    boot=assert_ok(client.get('/api/bootstrap'))
    by_id={d['id']:d for d in boot['tabs']}
    assert by_id[secondary['id']]['is_manuscript']==1
    assert by_id[primary['id']]['is_manuscript']==0
    # No content container is deleted or moved when the role changes.
    assert {d['id'] for d in boot['tabs']}=={primary['id'],secondary['id']}
    assert any(c['tab_id']==primary['id'] for c in boot['chapters'])
    assert any(c['tab_id']==secondary['id'] for c in boot['chapters'])


def test_v41810_documentation_records_menu_ownership():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '### 4.18.10 Full Text and Tab-role controls' in readme
    assert '### 4.18.10 Full Text and Tab-menu contract' in architecture
    assert 'Collection operations must not be duplicated in individual Tab menus' in architecture
    assert '**F** shortcut opens Full Text' in architecture
