from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import assets as storage_assets
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import schema as storage_schema

from pathlib import Path
import base64

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]

# A real 1x1 transparent PNG. The image feature must preserve these bytes exactly;
# Breadmaker does not flatten or transcode project images.
TRANSPARENT_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7xLAAAAAElFTkSuQmCC'
)


def _set_single_paragraph(client, chapter_id: int, text: str):
    detail = assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    block = detail['blocks'][0]
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks', json={'blocks': [{
        'id': block['id'], 'block_type': 'paragraph', 'semantic_style': 'body',
        'content_html': text, 'text_content': text, 'image_path': '',
    }]}))
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks'][0]


def test_v8193_schema_and_image_panel_contract():
    assert APP_SCHEMA_VERSION == storage_schema.SCHEMA_VERSION == 42
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8, 19, 3)
    html = ROOT.joinpath('static', 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    schema = ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')

    assert 'id="manuscriptImageSize"' in html and 'min="5" max="100"' in html
    assert 'id="manuscriptImageFooter"' in html
    assert 'id="manuscriptImageDescription"' in html
    assert 'id="manuscriptImageFileInput"' in html
    assert 'image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp' in html
    assert 'Images are placed' not in html
    assert 'manuscriptImageHoverTimer=setTimeout(()=>showImageDescriptionFor(image,text),500)' in js
    assert 'function queueManuscriptImageDescription(wrap)' in js
    assert "addEventListener('paste',event=>" in js
    assert "application/x-breadmaker-manuscript-image" in js
    assert 'image_width INTEGER NOT NULL DEFAULT 100' in schema
    assert "image_footer TEXT NOT NULL DEFAULT ''" in schema
    assert "image_description TEXT NOT NULL DEFAULT ''" in schema


def test_v8193_content_addressed_upload_preserves_transparent_png_and_expected_formats(client, fresh_project):
    first = assert_ok(client.post('/api/assets', files={'file': ('transparent.png', TRANSPARENT_PNG, 'image/png')}))
    second = assert_ok(client.post('/api/assets', files={'file': ('again.png', TRANSPARENT_PNG, 'image/png')}))
    assert first['path'] == second['path']
    assert second['reused'] is True
    stored = storage_context.ASSET_DIR / Path(first['path']).name
    assert stored.read_bytes() == TRANSPARENT_PNG

    # The upload boundary intentionally stores original bytes without decoding or
    # transcoding; browser-supported image formats are accepted by type/suffix.
    samples = [
        ('photo.jpg', b'jpeg-bytes', 'image/jpeg'),
        ('alpha.webp', b'webp-bytes', 'image/webp'),
        ('motion.gif', b'gif-bytes', 'image/gif'),
        ('modern.avif', b'avif-bytes', 'image/avif'),
        ('legacy.bmp', b'bmp-bytes', 'image/bmp'),
    ]
    for name, payload, mime in samples:
        response = client.post('/api/assets', files={'file': (name, payload, mime)})
        assert response.status_code == 200, response.text
        path = storage_context.ASSET_DIR / Path(response.json()['path']).name
        assert path.read_bytes() == payload


def test_v8193_image_insert_metadata_checkpoint_review_and_cut_roundtrip(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    target = _set_single_paragraph(client, chapter['id'], 'hello world')
    asset = assert_ok(client.post('/api/assets', files={'file': ('transparent.png', TRANSPARENT_PNG, 'image/png')}))['path']

    inserted = assert_ok(client.post('/api/manuscript/images/insert', json={
        'image_path': asset, 'target_block_id': target['id'], 'placement': 'at_offset', 'offset': 5,
    }))
    detail = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert [b['block_type'] for b in detail['blocks']] == ['paragraph', 'image', 'paragraph']
    assert detail['blocks'][0]['text_content'] == 'hello'
    assert detail['blocks'][2]['text_content'] == ' world'
    image = detail['blocks'][1]
    assert image['id'] == inserted['block_id']
    assert image['image_width'] == 100 and image['image_footer'] == '' and image['image_description'] == ''

    outgoing = []
    for block in detail['blocks']:
        row = {
            'id': block['id'], 'block_type': block['block_type'], 'semantic_style': block['semantic_style'],
            'content_html': block['content_html'], 'text_content': block['text_content'],
            'image_path': block['image_path'], 'scene_id': block['scene_id'],
        }
        if block['block_type'] == 'image':
            row.update(image_width=37, image_footer='Source: field notes', image_description='A transparent one-pixel test image.')
        outgoing.append(row)
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={'blocks': outgoing}))

    saved = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][1]
    assert saved['image_width'] == 37
    assert saved['image_footer'] == 'Source: field notes'
    assert saved['image_description'] == 'A transparent one-pixel test image.'

    checkpoint = assert_ok(client.post('/api/checkpoints', json={'scope': 'chapter', 'chapter_id': chapter['id'], 'name': 'Image checkpoint'}))
    checkpoint_snapshot = assert_ok(client.get(f"/api/checkpoints/{checkpoint['id']}"))['snapshot']
    cp_image = next(b for b in checkpoint_snapshot['blocks'] if b['block_type'] == 'image')
    assert (cp_image['image_width'], cp_image['image_footer'], cp_image['image_description']) == (
        37, 'Source: field notes', 'A transparent one-pixel test image.'
    )

    review = assert_ok(client.post('/api/reviews', json={'name': 'Image review', 'permission_read': True}))
    started = assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    with storage_mutations.tx(track_generation=False) as con:
        rv_image = dict(con.execute(
            "SELECT image_width,image_footer,image_description FROM review_snapshot_blocks WHERE snapshot_id=? AND block_type='image'",
            (started['snapshot']['id'],),
        ).fetchone())
    assert (rv_image['image_width'], rv_image['image_footer'], rv_image['image_description']) == (
        37, 'Source: field notes', 'A transparent one-pixel test image.'
    )

    cut = assert_ok(client.post('/api/cuts', json={
        'title': 'Image cut',
        'source_chapter_id': chapter['id'],
        'content_html': f'<figure data-width="37"><img src="{asset}" alt="A transparent one-pixel test image."><figcaption>Source: field notes</figcaption></figure>',
    }))
    cuts = assert_ok(client.get('/api/cuts'))
    saved_cut = next(row for row in cuts if row['id'] == cut['id'])
    assert 'data-width="37"' in saved_cut['content_html']
    assert '<figcaption>Source: field notes</figcaption>' in saved_cut['content_html']
    assert 'alt="A transparent one-pixel test image."' in saved_cut['content_html']


def test_v8193_orphan_image_gc_waits_for_references_and_safe_boundary(client, fresh_project):
    orphan = assert_ok(client.post('/api/assets', files={'file': ('orphan.png', TRANSPARENT_PNG + b'orphan', 'image/png')}))['path']
    orphan_file = storage_context.ASSET_DIR / Path(orphan).name
    assert orphan_file.is_file()
    assert storage_assets.cleanup_unreferenced_project_images() >= 1
    assert not orphan_file.exists()

    chapter = fresh_project['bootstrap']['chapters'][0]
    block = _set_single_paragraph(client, chapter['id'], 'anchor')
    referenced = assert_ok(client.post('/api/assets', files={'file': ('kept.png', TRANSPARENT_PNG + b'kept', 'image/png')}))['path']
    assert_ok(client.post('/api/manuscript/images/insert', json={
        'image_path': referenced, 'target_block_id': block['id'], 'placement': 'after', 'offset': 0,
    }))
    kept_file = storage_context.ASSET_DIR / Path(referenced).name
    assert kept_file.is_file()
    storage_assets.cleanup_unreferenced_project_images()
    assert kept_file.is_file()
