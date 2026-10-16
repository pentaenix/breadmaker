from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')
CSS=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
INDEX=(ROOT/'static/index.html').read_text()

def test_v724_release_and_quick_open_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,2,4)
    assert 'styles.css?v=' in INDEX and 'app.js?v=' in INDEX
    assert 'projectMenuQuickOpen' not in APP
    assert 'quick_open.menu_label' not in APP
    assert 'id="quickOpenBtn"' in INDEX
    assert '#quickOpenBtn{width:31px;height:31px' in CSS
    assert "key==='k'" in APP and 'openQuickOpen()' in APP

def test_v724_undo_toast_exposes_redo_and_locales_match():
    assert "toast('Undone',4200,{label:i18nText('action.redo'),onClick:()=>appRedo()})" in APP
    files=sorted((ROOT/'locales').glob('*.json'))
    catalogs=[json.loads(p.read_text()) for p in files]
    assert len(catalogs)==7
    en=next(c for c in catalogs if c['code']=='en')
    keys=set(en['strings'])
    assert 'action.redo' in keys and 'quick_open.menu_label' not in keys
    ph=lambda value: sorted(re.findall(r'\{[A-Za-z0-9_]+\}',str(value)))
    for c in catalogs:
        assert set(c['strings'])==keys
        assert c['strings']['action.redo'].strip()
        assert ph(c['strings']['action.redo'])==ph(en['strings']['action.redo'])
