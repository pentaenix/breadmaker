from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from bs4 import BeautifulSoup
import re


def test_v48_editors_eye_static_contract():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    soup=BeautifulSoup(html,'html.parser')

    view_focus=soup.select_one('#viewFocusBtn')
    checkpoint=soup.select_one('#checkpointBtn')
    eye=soup.select_one('#editorsEyeBtn')
    tip=soup.select_one('#editorsEyeTooltip')
    assert view_focus and checkpoint and eye and tip
    assert soup.select_one('#focusBtn') is None
    assert checkpoint.find_next('button').get('id') == 'cutsBtn'
    assert soup.select_one('#cutsBtn').find_next('button').get('id') == 'editorsEyeBtn'
    assert view_focus.get_text(' ',strip=True).startswith('Focus Mode')
    assert 'icon-only' in eye.get('class',[])
    assert eye.select_one('svg.editors-eye-icon') is not None
    assert eye.get('aria-pressed') == 'false'
    assert eye.get('aria-describedby') == 'editorsEyeTooltip'
    assert eye.get('title') is None  # deterministic Breadmaker tooltip, not browser-native title
    assert 'Editor’s Eye' in tip.get_text(' ',strip=True)
    assert 'Temporarily changes your font, background, and layout' in tip.get_text(' ',strip=True)
    assert 'helping your brain spot errors' in tip.get_text(' ',strip=True)

    presets=re.findall(r'^\s*\{font:(.*?),size:([0-9.]+).*?light:\{bg:\'([^\']+)\'.*?dark:\{bg:\'([^\']+)\'',js,re.M)
    assert len(presets) == 6
    assert len({row[0] for row in presets}) == 6
    assert len({row[1] for row in presets}) == 6
    assert len({row[2] for row in presets}) == 6
    assert len({row[3] for row in presets}) == 6
    assert all(14 <= float(row[1]) <= 17 for row in presets)

    assert 'editorsEyeLastPresetIndex' in js
    assert 'if(index===state.editorsEyeLastPresetIndex)' in js
    assert "scroller.classList.add('editors-eye-active')" in js
    assert "scroller.classList.remove('editors-eye-active')" in js
    assert "if (state.editorsEyeActive) applyEditorsEyePreset(state.editorsEyePresetIndex);" in js
    assert "localStorage.setItem('breadmaker-theme', theme)" in js
    assert 'breadmaker-editors-eye' not in js
    assert '/api/' not in js[js.index('function editorsEyeTheme()'):js.index('async function captureAutoHistory')]

    assert '#documentScroller.editors-eye-active' in css
    assert '.editors-eye-button.active' in css
    assert '.editors-eye-tooltip' in css
    assert '.editors-eye-wave' in css
    assert '@media(prefers-reduced-motion:reduce)' in css
