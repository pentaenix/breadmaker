from source_contracts import frontend_source_from_app_path
from pathlib import Path


def test_v4143_cuts_mutations_share_one_sync_boundary_and_details_label():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(root/'static/app.js')

    assert 'async function notifyCutsChanged(preferId=null,{removeId=null}={})' in js
    assert 'cutsWorkspace.dirty=true' in js
    assert "if(panel&&!panel.classList.contains('hidden'))await reloadCutsWorkspace(preferId)" in js
    assert 'let cutsWorkspace={items:[],selectedId:null,dirty:true};' in js
    assert 'refresh||cutsWorkspace.dirty||!cutsWorkspace.items.length' in js

    # Every current collection-changing UI path must invalidate/refresh Cuts.
    import_block=js[js.index('async function importCutsPackage'):js.index('async function openProjectMetadata')]
    delete_chapter=js[js.index('async function cutChapter'):js.index('async function deleteChapterPermanently')]
    cut_selection=js[js.index('async function cutSelection(items)'):js.index('function deleteTextRange')]
    permanent_delete=js[js.index('async function permanentlyDeleteCut'):js.index('function cutMenuItems')]
    insert_cut=js[js.index('async function insertCutAtCursor'):js.index('function closeCutsPanel')]
    undo=js[js.index('async function performUndo'):js.index('async function performRedo')]
    redo=js[js.index('async function performRedo'):js.index('function requestHistory')]

    for block in (import_block,delete_chapter,cut_selection,permanent_delete,insert_cut,undo,redo):
        assert 'notifyCutsChanged' in block

    assert "{label:'Details',action:()=>openCutDetails(cut)}" in js
    assert "showModal('Cut Details'" in js
    assert "label:'Information'" not in js


def test_v4143_docs_define_additive_import_as_synchronized_collection_state():
    root=Path(__file__).resolve().parents[1]
    readme=(root/'README.md').read_text(encoding='utf-8')
    architecture=(root/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'Import Cuts' in readme and 'notifyCutsChanged()' in readme
    assert 'additive import succeeds' in readme
    assert 'A closed panel stays marked dirty and reloads on the next open.' in architecture
    assert 'Cut Details modal' in architecture
