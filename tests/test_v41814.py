from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v41814_scene_selection_exposes_primary_remove_and_mixed_overflow_remove():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    segment=js[js.index('function selectionMoreItems'):js.index('async function sceneReferenceRows')]
    assert "offerRemove=scenePlan.kind==='extend'&&scenePlan.canRemove" in segment
    assert "label:'Remove from Scene',action:()=>createSceneFromSelection(items,{forceRemove:true})" in segment
    assert "if(hasUnassigned)return {kind:'extend',sceneId,scene,label:'Add to Scene',canRemove,removeBlockIds}" in segment
    assert "if(canRemove)return {kind:'remove',sceneId,scene,label:'Remove from Scene'" in segment
    assert "label:'Remove from Scene',disabled:true" in segment
    assert "const blockIds=actionKind==='remove'?(plan.removeBlockIds||[]):completeSceneBlockIds(items)" in segment
    assert "button.textContent=plan.label||'Scene';button.disabled=!!plan.disabled" in segment


def test_v41814_scene_rows_use_whole_row_navigation_like_chapters():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    scene_render=js[js.index('function renderSceneTree'):js.index('function chapterMenuItems')]
    assert "row.onclick=e=>{if(e.target.closest('.scene-more,.sidebar-bookmark-icon'))return" in scene_render
    assert "row.ondblclick=e=>{if(e.target.closest('.scene-more,.sidebar-bookmark-icon'))return" in scene_render
    assert "main.onclick=" not in scene_render and "main.ondblclick=" not in scene_render
    assert '.scene-outline-row{min-height:38px;padding:8px 11px;line-height:1.28;cursor:pointer}' in css
    assert '.book-nav.collapsed .scene-outline-main{flex:1 1 auto;width:auto;align-self:stretch;justify-content:center;gap:0}' in css


def test_v41814_release_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert '### 4.18.14 Scene selection and compact-rail parity' in readme
    assert '### 4.18.14 Scene selection and compact-navigation contract' in architecture
