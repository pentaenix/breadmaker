from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
INDEX=(ROOT/'static/index.html').read_text()

def test_v723_polish_source_contracts():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,2,3)
    assert 'styles.css?v=' in INDEX and 'app.js?v=' in INDEX
    assert "key==='k'" in APP and "openQuickOpen()" in APP
    assert "#quickOpenResults{max-height" in CSS and "overflow:auto" in CSS
    assert "scheduleWordCount();" in APP and "setTimeout(countWords,140)" in APP
    assert "async function getTasks" in APP and "taskCache" in APP
    assert "quickOpenConcurrent(state.canvases" in APP and "quickOpenConcurrent(state.maps" in APP
    assert "visibilitychange" in APP and "flushSave().catch" in APP
    assert "task-filter" in APP
    assert "needsConfirm=hasStructural" in APP
    assert "undoToast" in APP
    assert "figure-hit-line" in APP
    assert "grid-template-columns:repeat(4,33px)" in CSS
    assert "background:#fff;color:#b42318" not in CSS
    assert "scheduleMapTreeRender" in APP
    assert "map-viewport-" in APP and "map-active" in APP

def test_v723_locale_parity_and_placeholders():
    files=sorted((ROOT/'locales').glob('*.json'))
    catalogs=[json.loads(p.read_text()) for p in files]
    en=next(c for c in catalogs if c['code']=='en')
    keys=set(en['strings'])
    required={'action.undo','tasks.create_from_search','tasks.milestone_match','canvas.deleted.one','canvas.deleted.many','canvas.connector_deleted.one','canvas.connector_deleted.many'}
    assert required <= keys
    ph=lambda value: sorted(re.findall(r'\{[A-Za-z0-9_]+\}',str(value)))
    for c in catalogs:
        assert set(c['strings'])==keys
        for key in required:
            assert ph(c['strings'][key])==ph(en['strings'][key])
