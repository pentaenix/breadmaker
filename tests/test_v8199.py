from source_contracts import frontend_source_from_app_path
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def _strings(locale='en'):
    return json.loads((ROOT / 'locales' / f'{locale}.json').read_text(encoding='utf-8'))['strings']


def test_v8199_manuscript_tooltips_describe_purpose_not_panel_mechanics():
    en = _strings('en')
    image = en['manuscript.images_tooltip']
    reviews = en['manuscript.reviews_tooltip']

    assert image == 'Add and arrange images between passages, then adjust their size, footer, and description.'
    assert reviews == 'View Review feedback in manuscript context, including highlights, comments, and suggestions.'
    assert 'side panel' not in image.lower()
    assert 'side panel' not in reviews.lower()


def test_v8199_image_and_reviews_tooltips_remain_wired_to_toolbar_buttons():
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = frontend_source_from_app_path(ROOT / 'static' / 'app.js')

    assert 'id="manuscriptImageTooltip"' in html
    assert '{{i18n:manuscript.images_tooltip}}' in html
    assert 'id="manuscriptReviewsTooltip"' in html
    assert '{{i18n:manuscript.reviews_tooltip}}' in html
    assert "attachManuscriptToolTooltip('manuscriptImageBtn','manuscriptImageTooltip')" in js
    assert "attachManuscriptToolTooltip('manuscriptReviewsBtn','manuscriptReviewsTooltip')" in js


def test_v8199_new_tooltip_keys_exist_in_every_locale():
    for locale in ['en', 'es', 'fr', 'it', 'pt-BR', 'ja', 'zh-CN']:
        strings = _strings(locale)
        assert strings['manuscript.images_tooltip'].strip()
        assert strings['manuscript.reviews_tooltip'].strip()
