from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok


def _save_text(client, chapter_id:int, text:str):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}))


def test_v4122_project_center_cleanup_contract():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'Design principles' not in js and 'Breadmaker Design Principles' not in js
    assert 'Browse all' in js and 'openAllProjects' in js and 'projectExplorerSearch' in js
    assert 'Export PDF' in js and 'openPdfExport' in js
    assert 'id="historyPanel"' not in html
    assert 'history-row-menu' in css
    assert 'class="quiet-button" data-recover-backup' in js


def test_v4122_history_export_delete_and_restore_is_new_timeline_step(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'A B C')
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'ABC version','auto',False)
    _save_text(client,chapter['id'],'B C A')
    exported=client.get(f'/api/revisions/{rid}/export-txt')
    assert exported.status_code==200 and 'A B C' in exported.text and exported.headers['content-type'].startswith('text/plain')
    restored=assert_ok(client.post(f'/api/revisions/{rid}/restore'))
    assert restored['history_revision_id']!=rid
    rows=assert_ok(client.get('/api/history'))['revisions']
    labels=[r['label'] for r in rows]
    assert 'ABC version' in labels
    assert any(label.startswith('Restored History: ABC version') for label in labels)
    blocks=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    assert blocks[0]['text_content']=='A B C'
    doomed=next(r for r in rows if r['id']!=rid and r['label'].startswith('Restored History:'))
    assert_ok(client.delete(f"/api/revisions/{doomed['id']}"))
    remaining=assert_ok(client.get('/api/history'))['revisions']
    assert any(r['id']==rid for r in remaining)
    assert not any(r['id']==doomed['id'] for r in remaining)


def test_v4122_health_reports_complete_project_footprint(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    (storage.ASSET_DIR/'health.bin').write_bytes(b'x'*4096)
    export_dir=storage.PROJECT_ROOT/'exports'; export_dir.mkdir(exist_ok=True); (export_dir/'note.txt').write_bytes(b'y'*2048)
    result=assert_ok(client.get('/api/project/health'))
    assert result['database']['ok'] is True
    assert result['sizes']['project_total']>=result['sizes']['database']+4096+2048
    assert result['sizes']['assets']>=4096 and result['sizes']['exports']>=2048
    assert result['counts']['assets']>=1
    for key in ('history','undo','checkpoints','cuts','content_objects','preserved_assets'):
        assert key in result['counts']
    assert 'preservation' in result and 'root_bytes' in result['preservation']


def test_v4122_pdf_export_is_real_pdf_and_uses_primary_manuscript(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'PDF export paragraph.')
    response=client.post('/api/export/pdf',json={
        'cover':True,'title':'Breadmaker PDF Test','page_numbering':True,
        'header':'Review Copy','watermark':'DRAFT','font_size':11,
        'font_type':'book-serif','page_size':'letter','chapter_page_breaks':True,
    })
    assert response.status_code==200
    assert response.headers['content-type'].startswith('application/pdf')
    assert response.content.startswith(b'%PDF-') and len(response.content)>1000
    assert 'Breadmaker%20PDF%20Test.pdf' in response.headers.get('content-disposition','')
