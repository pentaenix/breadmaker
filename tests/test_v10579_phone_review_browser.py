"""Additional phone review coverage; no voice or audio actions."""
import pytest
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser

PHONE={'viewport':{'width':390,'height':640},'is_mobile':True,'has_touch':True}
PANELS=[('Markers','markupPanel'),('Images','manuscriptImagePanel'),('Checkpoints','checkpointPanel'),('Reviews','manuscriptReviewsPanel'),('Cuts','cutsPanel')]


@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('label,panel_id',PANELS)
def test_phone_tool_sheets_keep_close_control_and_editor_accessible(tmp_path,engine,label,panel_id):
    with browser(tmp_path,page_options=PHONE,engine=engine) as (_,page,_):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.locator('#phoneManuscriptToolsBtn').tap();page.get_by_role('menuitem',name=label,exact=True).tap()
        panel=page.locator('#'+panel_id);expect(panel).to_be_visible()
        sheet=panel.bounding_box();view=page.locator('#manuscriptView').bounding_box()
        assert sheet['x']==0 and sheet['width']==390
        assert abs(sheet['height']-view['height']/2)<=1 and abs(sheet['y']+sheet['height']-view['y']-view['height'])<=1
        close=panel.locator('.panel-head button').last;rect=close.bounding_box()
        assert rect['y']>=sheet['y'] and rect['y']+rect['height']<=sheet['y']+sheet['height']
        assert page.locator('.document-scroller').bounding_box()['height']>60
        close.tap();expect(panel).to_be_hidden()
        assert page.evaluate('()=>scrollX===0&&scrollY===0&&document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors


def test_replacement_menu_preserves_its_own_accessibility_metadata(tmp_path):
    with browser(tmp_path,page_options=PHONE) as (_,page,_):
        page.locator('#phoneWorkspaceBtn').tap()
        page.evaluate('''async()=>{
          openContextMenu(20,100,[{label:'Synthetic action',action:()=>{}}]);
          const menu=document.querySelector('#contextMenu');menu.setAttribute('role','menu');menu.setAttribute('aria-label','Replacement menu');
          await Promise.resolve();
        }''')
        expect(page.locator('#phoneWorkspaceBtn')).to_have_attribute('aria-expanded','false')
        expect(page.locator('#contextMenu')).to_have_attribute('role','menu')
        expect(page.locator('#contextMenu')).to_have_attribute('aria-label','Replacement menu')


@pytest.mark.parametrize('engine',['chromium','webkit'])
def test_selected_note_stays_editable_in_short_phone_sheet(tmp_path,engine):
    with browser(tmp_path,page_options=PHONE,engine=engine) as (_,page,_):
        page.locator('.manuscript-block').first.dblclick()
        page.get_by_role('button',name='Note',exact=True).tap()
        page.locator('#commentCreateText').fill('Synthetic phone note')
        page.locator('#commentCreateSubmit').tap()
        expect(page.locator('#commentCreateText')).to_be_hidden()
        page.locator('#phoneManuscriptToolsBtn').tap();page.get_by_role('menuitem',name='Notes',exact=True).tap()
        page.locator('[data-comment-open]').click()
        note=page.get_by_role('textbox',name='Note',exact=True)
        assert note.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)')>=16
        page.set_viewport_size({'width':390,'height':420})
        assert page.locator('#commentsDetail').bounding_box()['height']>=44
        reply=page.locator('#commentReplyText');reply.fill('Reply from the phone sheet')
        send=page.get_by_role('button',name='Add follow-up',exact=True);send.tap()
        expect(page.locator('[data-comment-message]')).to_have_count(2)
        box=page.get_by_role('button',name='Close Notes',exact=True).bounding_box()
        assert box['y']>=0 and box['y']+box['height']<=420
        assert page.evaluate('()=>scrollX===0&&scrollY===0&&document.documentElement.scrollWidth<=innerWidth')
