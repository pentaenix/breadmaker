from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from pathlib import Path
import json

from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v8195_image_inspector_optional_labels_and_placeholders_are_consistent():
    assert tuple(map(int, ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8, 19, 5)
    html = ROOT.joinpath('static', 'index.html').read_text(encoding='utf-8')
    assert '{{i18n:manuscript.image_footer}} <span class="optional-label">{{i18n:manuscript.optional_parenthetical}}</span>' in html
    assert '{{i18n:manuscript.image_description}} <span class="optional-label">{{i18n:manuscript.optional_parenthetical}}</span>' in html
    for code in ('en', 'es', 'fr', 'it', 'ja', 'pt-BR', 'zh-CN'):
        strings = json.loads(ROOT.joinpath('locales', f'{code}.json').read_text(encoding='utf-8'))['strings']
        assert strings['manuscript.optional_parenthetical']
        assert 'optional' not in strings['manuscript.image_footer_placeholder'].lower()
        assert 'optional' not in strings['manuscript.image_description_placeholder'].lower()
    en = json.loads(ROOT.joinpath('locales', 'en.json').read_text(encoding='utf-8'))['strings']
    assert en['manuscript.image_footer_placeholder'] == 'What should appear below the image?'
    assert en['manuscript.image_description_placeholder'] == 'What does the image show?'


def test_v8195_image_insert_can_preserve_clipboard_occurrence_metadata(client, fresh_project):
    chapter = fresh_project['bootstrap']['chapters'][0]
    detail = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    target = detail['blocks'][0]
    asset = assert_ok(client.post('/api/assets', files={'file': ('clip.png', b'clipboard-image', 'image/png')}))['path']
    inserted = assert_ok(client.post('/api/manuscript/images/insert', json={
        'image_path': asset,
        'target_block_id': target['id'],
        'placement': 'after',
        'image_width': 42,
        'image_footer': 'Source: archive',
        'image_description': 'A small archival photograph.',
    }))
    saved = assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    image = next(block for block in saved if block['id'] == inserted['block_id'])
    assert image['image_path'] == asset
    assert image['image_width'] == 42
    assert image['image_footer'] == 'Source: archive'
    assert image['image_description'] == 'A small archival photograph.'


def test_v8195_frontend_declares_image_block_clipboard_contract():
    js = frontend_source_from_app_path(ROOT.joinpath('static', 'app.js'))
    assert "application/x-breadmaker-image-block" in js
    assert "document.addEventListener('copy'" in js
    assert "document.addEventListener('cut'" in js
    assert "manuscriptImageBlockFromClipboard(event.clipboardData)" in js
    assert "insertManuscriptImageReference(imageBlock,anchor)" in js
