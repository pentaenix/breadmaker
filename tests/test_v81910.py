from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import base64
from pathlib import Path

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X7xLAAAAAElFTkSuQmCC'
)


def test_v81910_image_move_can_split_prose_at_drop_offset(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    detail = assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))
    first = detail['blocks'][0]
    assert_ok(client.put(f'/api/chapters/{chapter["id"]}/blocks', json={'blocks': [{
        'id': first['id'], 'block_type': 'paragraph', 'semantic_style': 'body',
        'content_html': 'abcdef', 'text_content': 'abcdef', 'image_path': '', 'scene_id': None,
    }]}))
    detail = assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))
    target = detail['blocks'][0]
    asset = assert_ok(client.post('/api/assets', files={'file': ('move.png', PNG, 'image/png')}))['path']
    image = assert_ok(client.post('/api/manuscript/images/insert', json={
        'image_path': asset, 'target_block_id': target['id'], 'placement': 'after',
        'image_width': 23, 'image_footer': 'A footer', 'image_description': 'A description',
    }))

    moved = assert_ok(client.post(f'/api/manuscript/images/{image["block_id"]}/move', json={
        'target_block_id': target['id'], 'placement': 'at_offset', 'offset': 3,
    }))
    assert moved['block_id'] == image['block_id']
    after = assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    assert [row['block_type'] for row in after] == ['paragraph', 'image', 'paragraph']
    assert after[0]['text_content'] == 'abc'
    assert after[1]['id'] == image['block_id']
    assert after[1]['image_width'] == 23
    assert after[1]['image_footer'] == 'A footer'
    assert after[1]['image_description'] == 'A description'
    assert after[2]['text_content'] == 'def'


def test_v81910_image_interaction_contract_is_present():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8, 19, 10)
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    css = frontend_styles_source_from_styles_path(ROOT.joinpath('static', 'styles.css'))
    assert 'target_wrap:wrap' in js
    assert "anchor.target_wrap?.isConnected" in js
    assert 'manuscript-image-drop-indicator' in js
    assert 'background:#111' in css
    assert 'width:min(86%,64ch)' in css
    assert "event.key==='Backspace'||event.key==='Delete'" in js
    assert 'removeManuscriptImageWrap(adjacent' in js
