from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path

import io
from pathlib import Path

from docx import Document
from PIL import Image

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]
def _transparent_png() -> bytes:
    out=io.BytesIO();Image.new('RGBA',(40,20),(40,120,200,96)).save(out,format='PNG');return out.getvalue()

TRANSPARENT_PNG = _transparent_png()


def _insert_image(client, chapter_id: int, *, width=37, footer='Figure source', description='A small transparent square.'):
    detail=assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    target=detail['blocks'][0]
    asset=assert_ok(client.post('/api/assets', files={'file':('export.png',TRANSPARENT_PNG,'image/png')}))['path']
    inserted=assert_ok(client.post('/api/manuscript/images/insert',json={
        'image_path':asset,'target_block_id':target['id'],'placement':'after',
        'image_width':width,'image_footer':footer,'image_description':description,
    }))
    return asset, inserted['block_id']


def test_v81911_docx_export_preserves_image_width_footer_and_description(client, fresh_project):
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,19,11)
    chapter=fresh_project['bootstrap']['chapters'][0]
    _insert_image(client,chapter['id'],width=37,footer='Visible image footer',description='Accessible image description')

    response=client.get('/api/export/docx')
    assert response.status_code==200
    doc=Document(io.BytesIO(response.content))
    assert doc.inline_shapes
    shape=doc.inline_shapes[0]
    section=doc.sections[0]
    usable=int(section.page_width)-int(section.left_margin)-int(section.right_margin)
    assert abs(int(shape.width)-int(usable*.37)) < 2000
    assert shape._inline.docPr.get('descr') == 'Accessible image description'
    assert 'Visible image footer' in [paragraph.text for paragraph in doc.paragraphs]


def test_v81911_pdf_export_receives_occurrence_metadata_and_renders_footer(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _insert_image(client,chapter['id'],width=29,footer='PDF image footer',description='PDF accessibility description')
    response=client.post('/api/export/pdf',json={
        'cover':False,'title':'Image export','page_numbering':False,'header':'','watermark':'',
        'font_size':11,'font_type':'book-serif','page_size':'letter','chapter_page_breaks':True,
    })
    assert response.status_code==200
    assert response.content.startswith(b'%PDF-') and len(response.content)>1000

    source=backend_source(ROOT)
    assert 'image_path,image_width,image_footer,image_description,image_row_id FROM manuscript_blocks' in source
    assert "target_width=doc.width*width_percent/100" in source
    assert "RLParagraph(html.escape(footer).replace('\\n','<br/>'),image_footer)" in source


def test_v81911_readonly_description_hover_and_terminal_drop_are_first_class_contracts():
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'function bindReadOnlyImageDescriptions' in js
    for hook in (
        'bindReadOnlyImageDescriptions(paper)',
        "bindReadOnlyImageDescriptions($('#splitBody'))",
        "bindReadOnlyImageDescriptions($('.cut-information-content'))",
    ):
        assert hook in js
    assert 'function manuscriptImageEndAnchor()' in js
    assert 'function wireManuscriptImageEndDropTarget()' in js
    assert 'manuscript-image-end-drop-indicator' in js
