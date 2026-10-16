from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v4188_manuscript_action_affordance_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    for cls in ('document-more manuscript-item-more','chapter-more manuscript-item-more','scene-more manuscript-item-more','cuts-more manuscript-item-more','marker-more manuscript-item-more','checkpoint-more manuscript-item-more','comments-more manuscript-item-more'):
        assert cls in js
    assert '.manuscript-item-more{' in css
    assert 'function documentMenuItems(doc)' in js and 'row.oncontextmenu=e=>openDocumentMenu(e,doc)' in js
    assert 'function chapterMenuItems(ch)' in js and "row.oncontextmenu=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;openChapterMenu(e,ch)}" in js
    assert 'function sceneMenuItems(sceneId)' in js and "row.oncontextmenu=e=>{if(e.target.closest('.sidebar-bookmark-icon'))return;openSceneMenu(e,scene.id)}" in js
    assert 'function cutMenuItems(cut)' in js and 'row.oncontextmenu=e=>{const cut=cutsWorkspace.items.find' in js


def test_v4188_cut_menu_order_and_details_footer_are_standardized():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    start=js.index('function cutMenuItems(cut)')
    end=js.index('function openCutMenu',start)
    menu=js[start:end]
    assert menu.index("label:'Insert'") < menu.index("label:'Details'") < menu.index("label:'Delete'")
    assert 'separator:true' not in menu
    assert "label:'Add Note'" not in menu and 'Delete Permanently' not in menu
    assert 'id="cutInformationDelete" class="danger">Delete cut…</button><button id="cutInformationSave">Save</button>' in js
    assert 'cutInformationClose' not in js and 'Save Note' not in js


def test_v4188_checkpoint_details_has_delete_and_save():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert 'id="checkpointEditDelete" class="danger">Delete checkpoint…</button><button id="checkpointEditSave">Save</button>' in js
    assert "$('#checkpointEditDelete').onclick" in js


def test_v4188_documentation_records_menu_standard():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '### 4.18.8 Manuscript action-menu standard' in readme
    assert '### 4.18.8 Manuscript action affordance contract' in architecture
    assert 'entity-specific operational actions appear first' in architecture
    assert '**Cut** is also danger-styled' in architecture
