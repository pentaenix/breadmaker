from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

from pathlib import Path

from conftest import assert_ok


def _save_text(client, chapter_id:int, text:str):
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}))


def test_v4123_pdf_export_polish_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')
    app=backend_source(root)
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    for field in ('pdfAuthor','pdfCoverDate','pdfVersion','pdfNotes'):
        assert field in js
    assert 'pdf-check-row' in css and 'pdf-checkbox' in css
    assert 'appearance:none' in css and '.pdf-checkbox:checked::after' in css
    assert 'author:str=""' in app and 'cover_date:str=""' in app and 'version:str=""' in app and 'notes:str=""' in app
    assert 'setFillAlpha(.065)' in app
    assert 'watermark_size=max(44,min(76' in app


def test_v4123_pdf_cover_metadata_and_options_generate(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _save_text(client,chapter['id'],'PDF 4.12.3 body text.')
    response=client.post('/api/export/pdf',json={
        'cover':True,'title':'A Very Specific Cover','author':'Ziro Writer',
        'cover_date':'August 14, 2026','version':'Draft 7',
        'notes':'Private review copy.\nSecond line of notes.',
        'page_numbering':False,'header':'Editorial Review','watermark':'REVIEW COPY',
        'font_size':12,'font_type':'clean-sans','page_size':'a4','chapter_page_breaks':False,
    })
    assert response.status_code==200
    assert response.headers['content-type'].startswith('application/pdf')
    assert response.content.startswith(b'%PDF-') and len(response.content)>1200
