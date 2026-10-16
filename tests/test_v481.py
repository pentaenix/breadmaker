from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
import re


def _rgb(hex_color):
    value=hex_color.lstrip('#')
    return tuple(int(value[i:i+2],16)/255 for i in (0,2,4))


def _luminance(hex_color):
    channels=[]
    for c in _rgb(hex_color):
        channels.append(c/12.92 if c<=0.04045 else ((c+.055)/1.055)**2.4)
    r,g,b=channels
    return .2126*r+.7152*g+.0722*b


def _contrast(a,b):
    x,y=_luminance(a),_luminance(b)
    return (max(x,y)+.05)/(min(x,y)+.05)


def test_v481_editor_eye_polish_performance_and_contrast_contract():
    root=Path(__file__).resolve().parents[1]
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    html=(root/'static/index.html').read_text(encoding='utf-8')

    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html

    rows=re.findall(
        r"\{font:(.*?),size:([0-9.]+).*?light:\{bg:'([^']+)',text:'([^']+)'\},dark:\{bg:'([^']+)',text:'([^']+)'\}\}",
        js,
    )
    assert len(rows)==6
    for _,_,light_bg,light_text,dark_bg,dark_text in rows:
        assert _contrast(light_bg,light_text)>=7
        assert _contrast(dark_bg,dark_text)>=7

    # 4.8.1 deliberately avoids font payloads; fonts are local/system stacks.
    font_files=[p for p in root.rglob('*') if p.suffix.lower() in {'.ttf','.otf','.woff','.woff2'}]
    assert not font_files

    # The wave is compositor-only. Layout/typography must not be CSS-transitioned.
    section=css[css.index('4.8.1 Editor') :]
    wave=section[section.index('.editors-eye-wave{'):section.index('@keyframes editors-eye-wave-sweep')]
    assert 'will-change:transform,opacity' in wave
    assert 'translate3d' in wave
    assert 'font-size' not in wave and 'line-height' not in wave and 'width:' not in wave
    active_editor=section[section.index('#documentScroller.editors-eye-active .editor{'):section.index('#documentScroller.editors-eye-active .manuscript-block')]
    assert 'transition:' not in active_editor
    active_paper=section[section.index('#documentScroller.editors-eye-active .paper{'):section.index('#documentScroller.editors-eye-active .chapter-title')]
    assert 'transition:' not in active_paper

    # Appearance is swapped during the wave rather than before the off animation.
    assert 'const EDITORS_EYE_SWAP_MS=135' in js
    off=js[js.index('async function setEditorsEye(on)'):js.index("const editorsEyeButton=$('#editorsEyeBtn')")]
    off_branch=off[off.index('}else{'):]
    assert "await playEditorsEyeWave('off',()=>{scroller.classList.remove('editors-eye-active');clearEditorsEyeStyles();state.editorsEyePresetIndex=-1})" in off_branch
    assert "scroller.classList.remove('editors-eye-active');clearEditorsEyeStyles()" not in off_branch.split("await playEditorsEyeWave('off'",1)[0]
