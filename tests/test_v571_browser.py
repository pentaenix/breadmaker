from __future__ import annotations

import requests

from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try:
        requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception:
        pass


def test_v571_dusk_manuscript_is_one_surface_and_eye_recolors_it(scene_live_server,scene_browser):
    base=scene_live_server; _new_project(base,'5.7.1 Dusk manuscript parity')
    page,errors=_page(base,scene_browser)
    try:
        page.evaluate("setTheme('dusk'); setView('manuscript')")
        page.wait_for_function("()=>document.documentElement.dataset.theme==='dusk'")
        base_colors=page.evaluate("""()=>({
          paper:getComputedStyle(document.querySelector('.paper')).backgroundColor,
          paperShadow:getComputedStyle(document.querySelector('.paper')).boxShadow,
          scroller:getComputedStyle(document.querySelector('#documentScroller')).backgroundColor,
          paperEffective:getComputedStyle(document.querySelector('.paper')).backgroundColor
        })""")
        assert base_colors['paper']=='rgba(0, 0, 0, 0)'
        assert base_colors['paperShadow']=='none'
        assert base_colors['scroller']=='rgb(226, 222, 213)'

        eye=page.evaluate("""()=>{
          state.editorsEyeActive=true; state.editorsEyePresetIndex=0; applyEditorsEyePreset(0);
          const scroller=document.querySelector('#documentScroller'); scroller.classList.add('editors-eye-active');
          const paper=document.querySelector('.paper');
          return {scroller:getComputedStyle(scroller).backgroundColor,paper:getComputedStyle(paper).backgroundColor,expected:getComputedStyle(scroller).getPropertyValue('--ee-bg').trim()}
        }""")
        assert eye['paper']=='rgba(0, 0, 0, 0)'
        assert eye['scroller'] != base_colors['scroller']
        assert eye['expected']
        assert not errors,errors
    finally:
        _release(base); _shutdown_page(page)
