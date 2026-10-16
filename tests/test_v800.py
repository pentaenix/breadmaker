from __future__ import annotations
from source_contracts import frontend_styles_source_from_styles_path
from source_contracts import backend_source,frontend_source_from_app_path

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v800_reviews_is_an_empty_routed_top_level_workspace(client):
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (8,0,0)
    html=ROOT.joinpath('static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(ROOT.joinpath('static/app.js'))
    app=backend_source(ROOT)

    assert 'data-view="reviews"' in html
    assert '{{i18n:reviews.title}}' in html
    assert 'id="reviewsView"' in html
    assert 'aria-label="{{i18n:reviews.title}}"' in html
    assert "reviews:'/reviews'" in js
    assert '"/reviews"' in app
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static/styles.css'))
    assert "path==='/reviews'?'reviews':'manuscript'" in html
    assert 'html[data-initial-view="reviews"] #reviewsView{display:flex}' in css

    response=client.get('/reviews')
    assert response.status_code==200
    assert response.headers.get('cache-control')=='no-store, max-age=0'


def test_v800_reviews_title_is_localized_in_every_shipped_locale():
    expected={'en':'Reviews','es':'Reseñas','fr':'Relectures','it':'Revisioni','ja':'レビュー','pt-BR':'Revisões','zh-CN':'审阅'}
    for code,title in expected.items():
        data=json.loads(ROOT.joinpath('locales',f'{code}.json').read_text(encoding='utf-8'))
        assert data['strings']['reviews.title']==title
