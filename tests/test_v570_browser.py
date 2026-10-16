from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def _rgb_luminance(rgb: str) -> float:
    vals=[int(x.strip())/255 for x in rgb[rgb.index('(')+1:rgb.index(')')].split(',')[:3]]
    def linear(c): return c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4
    r,g,b=map(linear,vals)
    return .2126*r+.7152*g+.0722*b


def test_v570_dusk_is_soft_paper_and_editors_eye_is_not_a_flash(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.7 Dusk reading comfort')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("setTheme('dusk'); setView('manuscript')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dusk'")
        colors=page.evaluate("""()=>({
          bg:getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
          surface:getComputedStyle(document.documentElement).getPropertyValue('--surface').trim(),
          surface2:getComputedStyle(document.documentElement).getPropertyValue('--surface2').trim(),
          text:getComputedStyle(document.documentElement).getPropertyValue('--text').trim(),
          paper:getComputedStyle(document.querySelector('.paper')).backgroundColor,
          scroller:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor
        })""")
        assert colors['bg']=='#b6b1a8'
        assert colors['surface']=='#dfdbd2'
        assert colors['surface2']=='#cbc6bc'
        assert colors['text']=='#292a27'
        assert colors['paper']=='rgb(226, 222, 213)'
        assert colors['scroller']=='rgb(182, 177, 168)'
        # The reading paper is lighter than its surround, but distinctly below white.
        assert _rgb_luminance(colors['paper']) > _rgb_luminance(colors['scroller'])
        assert _rgb_luminance(colors['paper']) < .78

        # Force each Eye preset deterministically; every Dusk background must remain muted.
        eye=[]
        for i in range(6):
            val=page.evaluate("""i=>{state.editorsEyeActive=true;state.editorsEyePresetIndex=i;applyEditorsEyePreset(i);document.querySelector('#documentScroller').classList.add('editors-eye-active');const el=document.querySelector('#documentScroller');return {theme:editorsEyeTheme(),bg:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color}}""",i)
            eye.append(val)
        assert all(x['theme']=='dusk' for x in eye)
        # None of the Dusk Eye variants approaches white/light-mode flash luminance.
        assert max(_rgb_luminance(x['bg']) for x in eye) < .66
        assert min(_rgb_luminance(x['bg']) for x in eye) > .48
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
