from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v710_scrollbar_theme_computed_styles(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Scrollbar Theme 7.1.0')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(200)
        values=page.evaluate("""()=>{
          const root=document.documentElement;
          const scroller=document.querySelector('.document-scroller');
          const read=()=>({
            scheme:getComputedStyle(root).colorScheme,
            scrollbar:getComputedStyle(scroller).scrollbarColor,
            track:getComputedStyle(root).getPropertyValue('--scrollbar-track').trim(),
            thumb:getComputedStyle(root).getPropertyValue('--scrollbar-thumb').trim(),
          });
          root.dataset.theme=''; const light=read();
          root.dataset.theme='dark'; const dark=read();
          root.dataset.theme='dusk'; const dusk=read();
          return {light,dark,dusk};
        }""")
        assert values['light']['scheme']=='light'
        assert values['dark']['scheme']=='dark'
        assert values['dusk']['scheme']=='light'
        assert values['light']['scrollbar'] != values['dark']['scrollbar']
        assert values['dark']['scrollbar'] != values['dusk']['scrollbar']
        assert not errors,errors
    finally:
        _shutdown_page(page)
