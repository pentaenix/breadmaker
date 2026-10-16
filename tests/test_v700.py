from __future__ import annotations
import json,re,zipfile
import pytest
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Comment
from source_contracts import frontend_source,frontend_styles_source_from_styles_path

ROOT=Path(__file__).resolve().parents[1]
MARKER_RE=re.compile(r"\{\{i18n:([^}]+)\}\}")


def test_v700_locale_catalog_has_canonical_english_source():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,0,0)
    locales=sorted((ROOT/'locales').glob('*.json'))
    assert 'en.json' in [p.name for p in locales]
    data=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    assert data['schema_version']==1
    assert data['code']=='en'
    assert data['english_name']=='English'
    assert data['native_name']=='English'
    assert data['direction']=='ltr'
    assert len(data['strings']) >= 1000
    assert len(data.get('source_index',{})) >= 900


def test_v700_every_explicit_locale_marker_resolves_and_css_copy_is_data_driven():
    data=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    keys=set(data['strings'])
    combined='\n'.join([(ROOT/'static/index.html').read_text(encoding='utf-8'),frontend_source(ROOT),frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))])
    missing=sorted(k for k in set(MARKER_RE.findall(combined))-keys if not k.startswith('${'))
    assert not missing, missing[:20]
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    # Human CSS pseudo-element copy must be locale-backed rather than a literal.
    assert not re.search(r"content\s*:\s*['\"][^'\"]*[A-Za-z][^'\"]*['\"]",css)


def test_v700_static_shell_preserves_642_structure_and_has_no_visible_english_literals():
    current=(ROOT/'static/index.html').read_text(encoding='utf-8')
    baseline_zip=Path('/mnt/data/BreadmakerEditor_v6.4.2.zip')
    if not baseline_zip.is_file():
        pytest.skip('archival 6.4.2 release artifact is not bundled with current releases')
    with zipfile.ZipFile(baseline_zip) as z:
        baseline=z.read('BreadmakerEditor/static/index.html').decode('utf-8')
    a=BeautifulSoup(baseline,'html.parser');b=BeautifulSoup(current,'html.parser')
    assert len(a.find_all())==len(b.find_all())==597
    assert len(a.find_all(id=True))==len(b.find_all(id=True))==251
    leaks=[]
    for node in b.descendants:
        if isinstance(node,NavigableString) and not isinstance(node,Comment) and node.parent and node.parent.name not in {'script','style'}:
            text=str(node).strip()
            if text and len(text)>1 and re.search(r'[A-Za-z]',text) and '{{i18n:' not in text:
                leaks.append(text)
    for tag in b.find_all(True):
        for attr in ('title','aria-label','placeholder','aria-description'):
            value=tag.get(attr)
            if value and re.search(r'[A-Za-z]',value) and '{{i18n:' not in value:
                leaks.append(f'{attr}={value}')
    # BeautifulSoup exposes the doctype payload as text; that is structural, not UI.
    assert leaks in ([],['html'])


def test_v700_locale_discovery_api(client):
    response=client.get('/api/locales')
    assert response.status_code==200
    data=response.json()
    assert data['default']=='en'
    by_code={item['code']:item for item in data['locales']}
    assert by_code['en']=={'code':'en','english_name':'English','native_name':'English','direction':'ltr','schema_version':1}
    assert by_code['es']=={'code':'es','english_name':'Spanish','native_name':'Español','direction':'ltr','schema_version':1}
    locale=client.get('/locales/en.json')
    assert locale.status_code==200
    assert locale.json()['native_name']=='English'
