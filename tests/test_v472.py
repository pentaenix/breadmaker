from source_contracts import backend_source,frontend_source_from_app_path
from pathlib import Path


def test_v472_version_routes_and_focus_clear_contract(client):
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(Path('static/app.js'))
    app=backend_source(Path('.'))
    assert "const VIEW_ROUTES={manuscript:'/',canvas:'/canvas',maps:'/maps',tasks:'/tasks',reviews:'/reviews'}" in js
    assert "setView(viewFromRoute(),{historyMode:'replace'})" in js
    assert "window.addEventListener('popstate'" in js
    assert "$('#mapStage')?.addEventListener('dblclick'" in js
    assert 'clearMapLineFocus()' in js
    assert 'APP_SHELL_PATHS={"/","/canvas","/maps","/tasks","/reviews"}' in app
    for route in ('/','/canvas','/maps','/tasks','/reviews'):
        response=client.get(route)
        assert response.status_code == 200
        assert '{{i18n:static.text.text.b30ebb7e}}' in response.text
        assert response.headers.get('cache-control') == 'no-store, max-age=0'
