from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import context as storage_context

import io
import json
import zipfile
from pathlib import Path
from conftest import assert_ok


def _begin(client,label:str)->str:
    return assert_ok(client.post(f"/api/undo/begin?label={label.replace(' ','%20')}"))["token"]


def test_v4140_static_cuts_workspace_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    assert 'id="cutsBtn"' in html
    assert 'id="cutsPanel"' in html
    assert 'id="cutSelectionBtn"' in html
    assert 'id="cutsSearch"' in html
    assert 'id="cutsTooltip"' in html
    assert "$('#cutsBtn').onclick" in js
    assert 'id="cutInsertBtn"' in html and 'id="cutInsertBtn"' not in js
    assert "Cut Details" in js and "Add Note" not in js and "Delete Permanently" not in js
    assert "id=\"cutsAction\"" not in js
    assert 'project-group-title">Writing' not in js
    assert 'id="importCutsAction"' in js and 'id="exportCutsAction"' in js
    assert "location.href='/api/cuts/export'" in js
    assert "'/api/cuts/import'" in js


def test_v4140_cut_note_and_source_label_survive_source_chapter_deletion(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'Saved line','content_html':'<p><strong>Keep</strong> this.</p>',
        'source_chapter_id':chapter['id'],'note':'Maybe use in the ending.'
    }))
    rows=assert_ok(client.get('/api/cuts'))
    row=next(x for x in rows if x['id']==cut['id'])
    assert row['note']=='Maybe use in the ending.'
    assert row['source_project']==fresh_project['bootstrap']['project']['title']
    assert row['source_chapter']==chapter['title']
    assert row['source_display'].endswith(chapter['title'])

    assert_ok(client.patch(f"/api/cuts/{cut['id']}",json={'note':'New reason'}))
    row=next(x for x in assert_ok(client.get('/api/cuts')) if x['id']==cut['id'])
    assert row['note']=='New reason'

    # Deleting the source chapter SET NULLs the FK, but provenance remains readable.
    assert_ok(client.delete(f"/api/chapters/{chapter['id']}"))
    row=next(x for x in assert_ok(client.get('/api/cuts')) if x['id']==cut['id'])
    assert row['source_chapter_id'] is None
    assert row['source_chapter']==chapter['title']
    assert chapter['title'] in row['source_display']


def test_v4140_insert_cut_at_cursor_is_atomic_and_consumes_cut(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    data=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    block=data['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{
        'id':block['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':'<strong>Hello</strong> world','text_content':'Hello world','image_path':''
    }]}))
    cut=assert_ok(client.post('/api/cuts',json={
        'title':'Two paragraphs','content_html':'<p><em>INSERT</em></p><p>Second</p>',
        'source_chapter_id':chapter['id'],'note':'Test note'
    }))

    token=_begin(client,'Insert Cut')
    result=assert_ok(client.post(f"/api/cuts/{cut['id']}/insert",json={
        'chapter_id':chapter['id'],'block_id':block['id'],'offset':5
    }))
    assert len(result['inserted_block_ids'])==2
    assert_ok(client.post(f'/api/undo/finish/{token}'))

    after=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['text_content'] for b in after['blocks']]==['Hello','INSERT','Second',' world']
    assert not any(x['id']==cut['id'] for x in assert_ok(client.get('/api/cuts')))

    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    undone=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['text_content'] for b in undone['blocks']]==['Hello world']
    restored=next(x for x in assert_ok(client.get('/api/cuts')) if x['id']==cut['id'])
    assert restored['note']=='Test note'

    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    redone=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['text_content'] for b in redone['blocks']]==['Hello','INSERT','Second',' world']
    assert not any(x['id']==cut['id'] for x in assert_ok(client.get('/api/cuts')))


def test_v4140_cuts_import_is_additive_and_rewrites_packaged_assets(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    storage_context.ASSET_DIR.mkdir(parents=True,exist_ok=True)
    asset=storage_context.ASSET_DIR/'portable.png';asset.write_bytes(b'fake-png-payload')
    first=assert_ok(client.post('/api/cuts',json={
        'title':'Portable Cut','content_html':'<p>Portable prose</p><img src="/api/assets/portable.png">',
        'source_chapter_id':chapter['id'],'note':'Carry me elsewhere.'
    }))
    exported=client.get('/api/cuts/export')
    assert exported.status_code==200
    package=exported.content
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        manifest=json.loads(z.read('cuts.json'))
        assert manifest['package_format']=='breadmaker-cuts'
        assert manifest['package_version']==1
        assert manifest['cuts'][0]['note']=='Carry me elsewhere.'
        assert '/api/assets/portable.png' in manifest['assets']

    existing=assert_ok(client.post('/api/cuts',json={
        'title':'Already here','content_html':'<p>Do not replace me.</p>','source_chapter_id':chapter['id']
    }))
    before=assert_ok(client.get('/api/cuts'));before_ids={x['id'] for x in before}
    imported=assert_ok(client.post('/api/cuts/import',files={'file':('portable.breadcuts',package,'application/octet-stream')}))
    assert imported=={'ok':True,'imported':1,'additive':True}
    after=assert_ok(client.get('/api/cuts'))
    after_ids={x['id'] for x in after}
    assert before_ids <= after_ids
    assert existing['id'] in after_ids and first['id'] in after_ids
    imported_rows=[x for x in after if x['id'] not in before_ids]
    assert len(imported_rows)==1
    imported_cut=imported_rows[0]
    assert imported_cut['title']=='Portable Cut'
    assert imported_cut['note']=='Carry me elsewhere.'
    assert imported_cut['source_chapter_id'] is None
    assert '/api/assets/portable.png' not in imported_cut['content_html']
    assert '/api/assets/' in imported_cut['content_html']


def test_v4140_import_never_replaces_existing_cuts_even_when_package_is_empty(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    existing=assert_ok(client.post('/api/cuts',json={'title':'Existing','content_html':'<p>Keep.</p>','source_chapter_id':chapter['id']}))
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('cuts.json',json.dumps({'package_format':'breadmaker-cuts','package_version':1,'cuts':[],'assets':{}}))
    result=assert_ok(client.post('/api/cuts/import',files={'file':('empty.breadcuts',payload.getvalue(),'application/octet-stream')}))
    assert result['imported']==0 and result['additive'] is True
    assert any(x['id']==existing['id'] for x in assert_ok(client.get('/api/cuts')))
