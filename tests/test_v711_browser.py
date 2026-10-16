from __future__ import annotations
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def test_v711_avatar_is_name_seeded_two_color_line_art_and_opens_profile(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Avatar 7.1.1')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_function("()=>!!state.profile?.display_name")
        first=page.evaluate("""()=>{
          const avatar=document.querySelector('#userAvatar'),button=document.querySelector('#userAvatarButton'),theme=document.querySelector('#themeBtn');
          const svg=avatar?.querySelector('svg'),circle=svg?.querySelector('circle'),lines=[...svg?.querySelectorAll('line')||[]];
          return {
            seed:userAvatarSeed(state.profile.display_name),
            afterTheme:theme?.nextElementSibling?.classList.contains('user-profile-control')===true,
            nested:button?.contains(avatar)===true,
            circle:getComputedStyle(avatar).borderRadius,
            svg:!!svg,
            background:circle?.getAttribute('fill')||'',
            lineColors:[...new Set(lines.map(line=>line.getAttribute('stroke')||line.parentElement?.getAttribute('stroke')).filter(Boolean))],
            lineCount:lines.length,
            html:avatar?.innerHTML||'',
            directClick:avatar?.onclick!==null,
          };
        }""")
        assert first['afterTheme'] and first['nested']
        assert first['circle']=='50%'
        assert first['svg']
        assert first['seed']
        assert first['background'].startswith('hsl(')
        assert first['lineCount']>=6
        assert len(first['lineColors'])==1
        assert first['directClick'] is False

        # Re-rendering and changing theme must not change the deterministic profile avatar.
        second=page.evaluate("""()=>{const avatar=document.querySelector('#userAvatar');const before=avatar.innerHTML;renderUserProfile();return {before,after:avatar.innerHTML};}""")
        assert second['after']==second['before']==first['html']
        page.locator('#themeBtn').click()
        after_theme=page.locator('#userAvatar').inner_html()
        assert after_theme==first['html']

        # The avatar control itself is now the account/profile affordance.
        page.locator('#userAvatarButton').click()
        page.locator('#userProfilePopover:not(.hidden)').wait_for()
        state_name = page.evaluate("()=>state.profile.display_name")
        assert state_name
        assert state_name in page.locator('#userProfilePopover').inner_text()
        assert not errors,errors
    finally:
        _shutdown_page(page)
