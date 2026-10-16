from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import assets as storage_assets
from storage import context as storage_context
from storage import history as storage_history

import base64
import json
import re
from pathlib import Path

from conftest import assert_ok
ROOT = Path(__file__).resolve().parents[1]
TRANSPARENT_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7xLAAAAAElFTkSuQmCC'
)


def _image_occurrence(client, chapter_id: int, *, width=41, footer='Old source', description='Old description'):
    detail = assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    target = detail['blocks'][0]
    asset = assert_ok(client.post('/api/assets', files={'file': ('compare.png', TRANSPARENT_PNG, 'image/png')}))['path']
    inserted = assert_ok(client.post('/api/manuscript/images/insert', json={
        'image_path': asset,
        'target_block_id': target['id'],
        'placement': 'after',
        'image_width': width,
        'image_footer': footer,
        'image_description': description,
    }))
    return asset, inserted['block_id']


def _update_image(client, chapter_id: int, image_id: str, *, width: int, footer: str, description: str):
    detail = assert_ok(client.get(f'/api/chapters/{chapter_id}'))
    blocks=[]
    for block in detail['blocks']:
        row={
            'id': block['id'], 'block_type': block['block_type'], 'semantic_style': block['semantic_style'],
            'content_html': block['content_html'], 'text_content': block['text_content'],
            'image_path': block['image_path'], 'scene_id': block['scene_id'],
        }
        if str(block['id']) == str(image_id):
            row.update(image_width=width, image_footer=footer, image_description=description)
        elif block['block_type']=='image':
            row.update(
                image_width=block['image_width'], image_footer=block['image_footer'],
                image_description=block['image_description'],
            )
        blocks.append(row)
    assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks', json={'blocks': blocks}))


def test_v8197_image_prompts_use_breadmaker_question_voice():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8, 19, 7)
    en=json.loads(ROOT.joinpath('locales','en.json').read_text(encoding='utf-8'))['strings']
    assert en['manuscript.image_footer_placeholder'] == 'What should appear below the image?'
    assert en['manuscript.image_description_placeholder'] == 'What does the image show?'
    for code in ('en','es','fr','it','ja','pt-BR','zh-CN'):
        data=json.loads(ROOT.joinpath('locales',f'{code}.json').read_text(encoding='utf-8'))
        assert 'manuscript.image_footer_placeholder' not in data
        assert data['strings']['manuscript.image_footer_placeholder'].endswith(('?','？'))
        assert data['strings']['manuscript.image_description_placeholder'].endswith(('?','？'))


def test_v8197_checkpoint_compare_renders_image_and_detects_occurrence_metadata(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    asset,image_id=_image_occurrence(client,chapter['id'])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Image baseline'}))

    _update_image(client,chapter['id'],image_id,width=73,footer='New source',description='New description')
    comparison=assert_ok(client.get(f"/api/checkpoints/compare/versions?left={cp['id']}&right=current"))
    image_rows=[row for row in comparison['rows'] if row['left_style']=='image' or row['right_style']=='image']
    assert image_rows and any(row['kind']=='changed' for row in image_rows)
    row=next(row for row in image_rows if row['kind']=='changed')
    assert 'checkpoint-image-figure' in row['left']
    assert '--checkpoint-image-width:41%' in row['left']
    assert f'src="{asset}"' in row['left']
    assert 'alt="Old description"' in row['left']
    assert '<figcaption>Old source</figcaption>' in row['left']
    assert '--checkpoint-image-width:73%' in row['right']
    assert 'alt="New description"' in row['right']
    assert '<figcaption>New source</figcaption>' in row['right']

    # A reference-only Checkpoint split uses the client snapshot renderer rather
    # than the compare API; keep that path first-class too.
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert 'checkpointImageUnitHtmlClient' in js
    assert 'u.html||escapeHtml(u.text)' in js


def test_v8197_history_compare_serves_immutable_revision_image_after_current_asset_is_gone(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    asset,image_id=_image_occurrence(
        client,chapter['id'],width=58,footer='Historical source',description='Historical description'
    )
    original_path=storage_context.ASSET_DIR / Path(asset).name
    assert original_path.is_file()
    revision=storage_history.capture_local_revision(label='Image history baseline',creation_reason='v8197',force_checkpoint=True)

    # Remove the live occurrence and safely collect the Current asset. History must
    # still own and render its immutable copy.
    detail=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))
    remaining=[]
    for block in detail['blocks']:
        if str(block['id'])==str(image_id):
            continue
        remaining.append({
            'id':block['id'],'block_type':block['block_type'],'semantic_style':block['semantic_style'],
            'content_html':block['content_html'],'text_content':block['text_content'],
            'image_path':block['image_path'],'scene_id':block['scene_id'],
        })
    assert_ok(client.put(f'/api/chapters/{chapter["id"]}/blocks',json={'blocks':remaining}))
    storage_assets.cleanup_unreferenced_project_images()
    assert not original_path.exists()

    comparison=assert_ok(client.get(
        f"/api/history/v2/revisions/{revision['revision_id']}/compare-current?scope=chapter&chapter_id={chapter['id']}"
    ))
    image_row=next(row for row in comparison['rows'] if row['left_style']=='image')
    assert image_row['kind']=='removed'
    match=re.search(r'src="([^"]+/assets/[^"]+)"',image_row['left'])
    assert match, image_row['left']
    history_src=match.group(1)
    assert f"/api/history/v2/revisions/{revision['revision_id']}/assets/" in history_src
    assert '--checkpoint-image-width:58%' in image_row['left']
    assert '<figcaption>Historical source</figcaption>' in image_row['left']
    assert 'alt="Historical description"' in image_row['left']

    response=client.get(history_src)
    assert response.status_code==200
    assert response.content==TRANSPARENT_PNG
