"""Phone navigation reuses existing translated Manuscript controls."""
import json
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_phone_navigation_labels_resolve_in_every_supported_locale():
    soup = BeautifulSoup((ROOT/'static/index.html').read_text(encoding='utf-8'), 'html.parser')
    backdrop = soup.select_one('#phoneBookNavBackdrop')
    opener = soup.select_one('#phoneBookNavBtn')
    assert backdrop['aria-label'] == '{{i18n:ui.close}}'
    assert opener['aria-label'] == '{{i18n:static.attr.aria.label.2fb6c813}}'
    assert opener['aria-controls'] == soup.select_one('.book-nav')['id']
    assert opener['aria-expanded'] == 'false'
    expected = {'en':'Close', 'es':'Cerrar', 'fr':'Fermer', 'it':'Chiudi',
                'pt-BR':'Fechar', 'ja':'閉じる', 'zh-CN':'关闭'}
    for locale, close in expected.items():
        strings = json.loads((ROOT/'locales'/f'{locale}.json').read_text(encoding='utf-8'))['strings']
        assert strings['ui.close'] == close
        assert strings['static.attr.aria.label.2fb6c813'].strip()
        assert '{{i18n:' not in strings['static.attr.aria.label.2fb6c813']
