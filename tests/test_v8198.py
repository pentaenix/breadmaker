from source_contracts import frontend_source_from_app_path
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]


def test_v8198_split_view_source_contract():
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    assert "option('scene',i18nSource('Scene'))" in js
    assert "option('tab',i18nText('split.full_tab'))" in js
    assert "id=\"splitTabSelect\"" in js
    assert "splitSceneRows(chooserTabId)" in js
    assert "async function openSceneSplit(sceneId)" in js
    assert "async function openTabSplit(tabId)" in js
    assert "/api/book?tab_id=${encodeURIComponent(tabId)}" in js


def test_v8198_full_tab_label_is_localized_everywhere():
    for path in (ROOT/'locales').glob('*.json'):
        catalog=json.loads(path.read_text(encoding='utf-8'))
        assert catalog['strings'].get('split.full_tab'), path.name
