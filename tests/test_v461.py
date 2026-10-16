from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path


def test_v461_metro_poster_uses_safe_area_and_seeded_station_turns():
    js=frontend_source_from_app_path(Path('static/app.js'))
    css=frontend_styles_source_from_styles_path(Path('static/styles.css'))
    html=Path('static/index.html').read_text(encoding='utf-8')
    assert 'function metroPosterSeed' in js
    assert 'function metroPlanCandidate' in js
    assert 'function metroInitialHeading' in js
    assert 'function metroPlanLineRoute' in js
    assert "map={x:14,y:42" in js
    assert 'turnBudget=metroLineTurnBudget(line)' in js
    assert "metroConfig('routing.segments.turn_cooldown_mm'" in js
    assert '.metro-poster-preview{width:100%;min-height:100%;display:flex;justify-content:center;align-items:flex-start}' in css
    assert tuple(map(int,Path('VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (4,6,0)
