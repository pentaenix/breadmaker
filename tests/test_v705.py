from __future__ import annotations
from source_contracts import frontend_source_from_app_path
import json,re
from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]
APP=frontend_source_from_app_path(ROOT/'static/app.js')


def test_v705_release_and_catalog_contract():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (7,0,5)
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    es=json.loads((ROOT/'locales/es.json').read_text(encoding='utf-8'))
    assert set(en['strings'])==set(es['strings'])
    assert en['source_index']==es['source_index']
    assert en.get('css_variables')==es.get('css_variables')
    token=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
    for key in en['strings']:
        assert sorted(token.findall(en['strings'][key]))==sorted(token.findall(es['strings'][key])),key
    for key in [
        'tab.actions_for','notes.count','markup.highlighter_active','links.canvas_named','links.card_named',
        'links.map_named','links.item_named','canvas.objects_selected','canvas.connectors_selected',
        'developmental.delete_card','map.connection_point','map.line_synced_changes','import.heading',
        'map.attach_start','map.attach_location','map.attach_end','scene.end_named','cuts.from_chapter',
        'cuts.across_chapters','canvas.version.numbered','error.MAP_021','error.RECOVERY_002',
        'error.VALIDATION_001','error.UNHANDLED_001','error.JSON_IMPORT_001','error.JSON_IMPORT_005',
        'error.MAP_014','error.MAP_015','error.MAP_016','error.PROJECT_JSON_011','error.PROJECT_IMPORT_003',
        'error.BACKUP_VERIFY_001',
    ]:
        assert key in en['strings'] and key in es['strings'],key


def test_v705_every_literal_i18n_source_has_a_catalog_mapping():
    en=json.loads((ROOT/'locales/en.json').read_text(encoding='utf-8'))
    values=set(re.findall(r"i18nSource\(\s*'([^']+)'\s*\)",APP))
    values.update(re.findall(r'i18nSource\(\s*"([^"]+)"\s*\)',APP))
    missing=sorted(value for value in values if value not in en['source_index'])
    assert missing==[]


def test_v705_dynamic_localization_paths_do_not_bypass_runtime_catalog():
    required=[
        "i18nText('tab.actions_for',{name:doc.title})",
        "i18nText('canvas.objects_selected'",
        "i18nText('canvas.connectors_selected'",
        "?'map.attach_start':'map.attach_location'",
        "i18nText('map.attach_end'",
        "i18nText('import.heading'",
        "i18nSource(row.kind==='added'?'Added in Current':'Changed in Current')",
        "i18nText('scene.end_named'",
        "i18nText('notes.count'",
        "i18nText('markup.highlighter_active'",
        "i18nText('map.connection_point'",
        "i18nText('map.line_synced_changes'",
        "i18nText('developmental.delete_card'",
        "i18nText('links.canvas_named'",
        "i18nText('links.item_named'",
    ]
    for needle in required:
        assert needle in APP,needle
    assert '>End · ' not in APP
    assert 'Attach as start of' not in APP
    assert 'Attach as location for' not in APP
    assert 'Attach as end of' not in APP
    assert 'Heading ${' not in APP


def test_v705_locale_formatting_is_breadmaker_owned():
    # Raw browser-locale formatting must be confined to the i18nNumber helper.
    assert APP.count('.toLocaleString(')==2  # active locale + defensive browser fallback in one helper
    assert 'function i18nDate(' in APP and 'Intl.DateTimeFormat(i18nLocale()' in APP
    assert 'function i18nDateTime(' in APP and 'function i18nTime(' in APP
    for needle in ['checkpointHumanTime','historyHumanTime','cutHumanTime','commentTime']:
        assert needle in APP
    assert "new Date(cp.created_at).toLocaleString()" not in APP
    assert "new Date(rev.created_at).toLocaleString()" not in APP


def test_v705_generated_defaults_are_localized_at_creation_time():
    required=[
        "i18nSource('Untitled Book')",
        "i18nSource('Notes')",
        "chapter.default_name",
        "default_title_prefix:i18nSource('Scene')",
        "i18nSource('Untitled Chapter')",
        "i18nSource('Untitled section')",
        "i18nSource('Untitled Card')",
        "i18nSource('Untitled Line')",
        "i18nSource('Manuscript beat')",
        "i18nSource('New Branch')",
        "title:i18nSource('Branch')",
        "i18nText('cuts.from_chapter'",
        "i18nSource('Linked tab')",
        "i18nSource('Saved from automatic History.')",
        "i18nText('canvas.version.numbered'",
        "body:JSON.stringify({chapter_id:chapterId,title})",
        "replacement_name:i18nSource('Untitled Book')",
    ]
    for needle in required:
        assert needle in APP,needle


def test_v705_coded_backend_errors_have_structured_envelope(client,fresh_project):
    story=assert_ok(client.post('/api/maps',json={'name':'Localization Errors'}))
    line=assert_ok(client.post(f"/api/maps/{story['id']}/lines",json={'name':'Only Line'}))
    left=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'A','planned_start':.2}))
    right=assert_ok(client.post(f"/api/map-lines/{line['id']}/items",json={'title':'B','planned_start':.7}))
    denied=client.post(f"/api/maps/{story['id']}/junctions",json={'kind':'branch','from_item_id':left['id'],'to_item_id':right['id']})
    assert denied.status_code==400
    detail=denied.json()['detail']
    assert detail=={'code':'MAP_021','message':'A junction must connect two different Lines.','params':{}}

    invalid_anchor=client.post(f"/api/map-lines/{line['id']}/items",json={'title':'Invalid','start_anchor_type':'banana'})
    assert invalid_anchor.status_code==400
    detail=invalid_anchor.json()['detail']
    assert detail['code']=='MAP_014' and detail['params']=={}

    missing=client.post('/api/backups/not-a-real-backup.breadmaker/restore-as-project')
    assert missing.status_code==404
    detail=missing.json()['detail']
    assert detail['code']=='RECOVERY_002' and detail['message']=='Backup not found' and detail['params']=={}


def test_v705_common_uncoded_backend_errors_have_spanish_source_fallbacks():
    es=json.loads((ROOT/'locales/es.json').read_text(encoding='utf-8'))
    for source in [
        'Chapter not found','Scene not found','Scene name cannot be empty',
        'A Scene cannot cross separate source-document tabs','Note cannot be empty',
        'Canvas name cannot be empty','Checkpoint not found','Image format not supported',
        'Confirmation code did not match.',
    ]:
        key=es['source_index'].get(source)
        assert key and es['strings'].get(key) and es['strings'][key]!=source,source
