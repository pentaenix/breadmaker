from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
INDEX=(ROOT/'static/index.html').read_text()

def test_v725_release_and_consistency_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,2,5)
    assert 'styles.css?v=' in INDEX and 'app.js?v=' in INDEX
    assert '•••' not in APP and '•••' not in INDEX
    assert 'toolbar-title-text' in INDEX and 'toolbar-title-text' in CSS
    assert '<div id="mapCurrentName" class="map-current-name toolbar-title-text"></div>' in INDEX
    assert 'input[type=checkbox]' in CSS and 'appearance:none' in CSS
    assert 'ui-close-button' in INDEX and 'ui-close-button' in CSS
    assert '--search-hit:' in CSS and '--reference-hit:' in CSS
    assert 'background:#ffe86c' not in CSS
    assert 'id="quickOpenBtn"' in INDEX and '<svg viewBox="0 0 24 24"' in INDEX

def test_v725_undo_and_quick_open_contract():
    assert "toast('Redone',4200,{label:i18nText('action.undo'),onClick:()=>appUndo()})" in APP
    assert "label:'Delete',danger:true,action:()=>deleteTask(task.id)" in APP
    assert "confirmDialog('Delete Task'" not in APP
    assert "undoToast(i18nText('tasks.deleted'))" in APP
    assert "undoToast(i18nText('map.item_deleted'))" in APP
    assert "undoToast(i18nText('map.junction_deleted'))" in APP
    assert "quickOpenConcurrent(state.canvases" in APP and "quickOpenConcurrent(state.maps" in APP
    assert "searchText:`${node.title||''} ${quickOpenPlainText(node.body)}" in APP
    assert "context:chapterName(x.chapter_id)" in APP
    assert "quickOpenCanvasOwner" in APP and "quickOpenMapItemOwner" in APP
    assert "itemGroup.onmousemove=positionMapTooltip" in APP
    assert "persistCurrentCanvasViewport().catch" in APP and "persistMapViewportNow().catch" in APP

def test_v725_locale_parity():
    files=sorted((ROOT/'locales').glob('*.json'))
    catalogs=[json.loads(p.read_text()) for p in files]
    assert len(catalogs)==7
    en=next(c for c in catalogs if c['code']=='en')
    keys=set(en['strings'])
    required={'ui.close','tasks.deleted','map.item_deleted','map.junction_deleted','action.undo','action.redo'}
    assert required <= keys
    ph=lambda value: sorted(re.findall(r'\{[A-Za-z0-9_]+\}',str(value)))
    for c in catalogs:
        assert set(c['strings'])==keys
        for key in required:
            assert c['strings'][key].strip()
            assert ph(c['strings'][key])==ph(en['strings'][key])
