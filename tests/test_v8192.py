from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import json
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_v8192_manuscript_toolbar_order_bookmark_navigation_and_marker_name_contract():
    assert tuple(map(int,(ROOT / 'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8,19,2)
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT / 'static' / 'app.js')
    css = frontend_styles_source_from_styles_path((ROOT / 'static' / 'styles.css'))
    soup = BeautifulSoup(html, 'html.parser')

    toolbar = soup.select_one('.document-toolbar')
    direct_ids = [node.get('id') for node in toolbar.find_all(recursive=False) if node.get('id')]
    assert direct_ids == [
        'commentsBtn', 'markupBtn', 'manuscriptImageBtn', 'checkpointBtn',
        'manuscriptReviewsBtn', 'cutsBtn', 'editorsEyeBtn',
    ]

    assert 'viewport-bookmark-button{position:sticky;top:1px;left:1px' in css
    assert 'function wireSidebarBookmarkNavigation(button)' in js
    assert 'if(chapterBookmark)wireSidebarBookmarkNavigation(chapterBookmark)' in js
    assert 'if(sceneBookmark)wireSidebarBookmarkNavigation(sceneBookmark)' in js
    assert "$('#bookmarkBtn').oncontextmenu=event=>" in js
    assert "label:'Remove Bookmark'" in js

    expected = {
        'en': 'Markers', 'es': 'Marcadores', 'fr': 'Marqueurs', 'it': 'Marcatori',
        'pt-BR': 'Marcadores', 'ja': 'マーカー', 'zh-CN': '标记',
    }
    for locale, label in expected.items():
        payload = json.loads((ROOT / 'locales' / f'{locale}.json').read_text(encoding='utf-8'))
        assert payload['strings']['static.attr.aria.label.f7dad5e9'] == label
        assert payload['strings']['static.text.text.1095201f'] == label
        assert payload['strings']['checkpoint.include_markup'] == label
