from __future__ import annotations
import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PLACEHOLDER_RE=re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")

def test_v703_spanish_locale_is_complete_and_runtime_safe():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,0,3)
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    es=json.loads((ROOT/'locales/es.json').read_text(encoding='utf-8'))
    assert {k:es[k] for k in ('schema_version','code','english_name','native_name','direction')} == {
        'schema_version':1,'code':'es','english_name':'Spanish','native_name':'Español','direction':'ltr'
    }
    assert set(es['strings'])==set(en['strings'])
    assert es.get('css_variables')==en.get('css_variables')
    assert es.get('source_index')==en.get('source_index')
    for key,english in en['strings'].items():
        spanish=es['strings'][key]
        assert type(spanish) is type(english)
        if isinstance(english,str):
            assert sorted(PLACEHOLDER_RE.findall(spanish))==sorted(PLACEHOLDER_RE.findall(english)), key
            if english: assert spanish, key

def test_v703_locale_discovery_serves_spanish(client):
    data=client.get('/api/locales').json()
    spanish=next(item for item in data['locales'] if item['code']=='es')
    assert spanish['english_name']=='Spanish'
    assert spanish['native_name']=='Español'
    response=client.get('/locales/es.json')
    assert response.status_code==200
    assert response.json()['strings']['settings.language.modal_title']=='Idioma'
