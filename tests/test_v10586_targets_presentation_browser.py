"""Targets presentation uses real state with a fake player; no audible output."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import open_targets


def seed(page):
    page.evaluate("""async()=>{
      await loadTargets();const doc=targetsModel.defaults(),now=Date.now();
      doc.enabled=true;doc.chime=true;
      doc.goals=Array.from({length:8},(_,i)=>({id:'synthetic'+i,metric:'words',period:i%3===0?'daily':i%3===1?'weekly':'monthly',amount:500,created:now-86400000*90}));
      for(let i=0;i<45;i++){
        const goal=doc.goals[i%8],date=new Date(now);date.setDate(date.getDate()-i);
        const period=targetsModel.period(goal.period,date.getTime()),key=goal.id+':'+period.key;
        doc.entries[key]={goal:goal.id,metric:goal.metric,period:goal.period,amount:500,...period,value:i===0?567:i%2===0?500:123,achieved:i%2===0?date.getTime():0};
      }
      const response=await targetsRequest();await targetsRequest({method:'PUT',body:JSON.stringify({value:doc,expected_revision:response.revision})});
      targets.ready=false;await loadTargets();
      window.soundCalls=[];window.soundStops=0;window.Audio=class{
        constructor(src){this.src=src}play(){soundCalls.push(this.src);return Promise.resolve()}pause(){soundStops++}
      };
    }""")


def exercise(home,local,engine,width):
    with browser(home,local=local,engine=engine,page_options={'viewport':{'width':width,'height':844}}) as (_,page,_):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        seed(page);open_targets(page)
        assert page.locator('#projectMenuTargets').inner_text()=='Targets…'
        assert page.locator('#targetsPreview').count()==0
        expect(page.locator('#targetsSound')).to_be_visible()
        assert page.locator('.targets-history-row .targets-ring-seed').count()>0
        for theme in ('light','dusk','dark'):
            page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
            colors=page.locator('#targetsSound option').first.evaluate('(el)=>({ink:getComputedStyle(el).color,paper:getComputedStyle(el).backgroundColor})')
            assert colors['ink']!=colors['paper'] and colors['paper']!='rgba(0, 0, 0, 0)',colors
        page.evaluate('()=>document.documentElement.dataset.theme="light"')
        assert page.evaluate('soundCalls')==[],'opening settings must never preview a sound'
        for sound in ('Fern','Oak'):
            page.locator('#targetsSound').select_option(sound)
        assert page.evaluate('soundCalls')==['/static/audio/targets/Fern.mp3','/static/audio/targets/Oak.mp3']
        assert page.evaluate('soundStops')==1
        expect(page.locator('[data-target-goal]:visible')).to_have_count(1)
        expect(page.locator('#targetsGoalSelect option')).to_have_count(8)
        assert page.locator('#targetsGoalSelect').bounding_box()['width']>=120
        page.locator('#targetsGoalSelect').select_option('synthetic4')
        page.locator('[data-target-goal]:visible [data-target-field=amount]').fill('650')
        page.locator('#targetsGoalSelect').select_option('synthetic0')
        page.locator('#targetsGoalSelect').select_option('synthetic4')
        expect(page.locator('[data-target-goal]:visible [data-target-field=amount]')).to_have_value('650')
        page.locator('#targetsHistoryToggle').click()
        expect(page.locator('#targetsHistoryList')).to_be_visible()
        geometry=page.evaluate("""()=>{
          const card=document.querySelector('.modal-card'),body=document.querySelector('#modalBody'),list=document.querySelector('.targets-history'),row=document.querySelector('.targets-history-row');
          return {card:[card.clientHeight,card.scrollHeight],body:[body.clientHeight,body.scrollHeight],list:[list.clientHeight,list.scrollHeight],row:row.getBoundingClientRect().height,width:document.documentElement.scrollWidth};
        }""")
        assert geometry['card'][1]<=geometry['card'][0]+1,geometry
        assert geometry['body'][1]<=geometry['body'][0]+1,geometry
        assert geometry['list'][1]>geometry['list'][0],geometry
        assert 2<=geometry['list'][0]/geometry['row']<=2.6,geometry
        assert geometry['width']==width
        page.locator('.targets-history').hover();page.mouse.wheel(0,400)
        page.wait_for_function('()=>document.querySelector(".targets-history").scrollTop>0')
        assert page.evaluate('()=>document.querySelector("#modalBody").scrollTop===0&&document.querySelector(".modal-card").scrollTop===0')
        for selector in ('#targetsSave','#targetsCancel','#targetsSound'):
            box=page.locator(selector).bounding_box();assert box and 0<=box['x'] and box['x']+box['width']<=width and box['y']+box['height']<=844
        page.locator('#targetsHistoryView').select_option('chart')
        for period in ('daily','weekly','monthly'):
            page.locator('#targetsHistoryPeriod').select_option(period)
            expect(page.locator('.targets-history-cell').first).to_be_visible()
            assert page.locator('.targets-history-cell').first.get_attribute('aria-label').startswith(period.capitalize())
        page.locator('.targets-history-cell').first.focus()
        expect(page.locator('#contextMenuHoverTip')).to_be_visible()
        page.screenshot(path=str(home/'targets-presentation.png'))
        page.keyboard.press('Escape');expect(page.locator('#modal')).to_be_hidden()
        assert page.evaluate('soundStops')==2
        assert page.evaluate('()=>targets.doc.goals.find(g=>g.id==="synthetic4").amount')==500,'Cancel must preserve saved goals'
        assert '567/500 words' in page.locator('#targetsTop').get_attribute('aria-label')
        page.locator('#targetsTop').hover();expect(page.locator('#targetsTopTip')).to_contain_text('567/500 words')
        assert page.locator('#targetsTop').inner_text()!='✓'
        assert not errors,errors


def test_targets_presentation_local_desktop_silent(tmp_path):exercise(tmp_path,True,'chromium',1280)
def test_targets_presentation_hosted_phone_webkit_silent(tmp_path):exercise(tmp_path,False,'webkit',390)


def test_targets_compact_goal_picker_saves_all_unsaved_goal_edits(tmp_path):
    with browser(tmp_path,local=True) as (_,page,_):
        seed(page);before=page.evaluate('()=>structuredClone(targets.doc.entries)');open_targets(page)
        for identity,amount in [('synthetic4','650'),('synthetic1','800')]:
            page.locator('#targetsGoalSelect').select_option(identity)
            page.locator('[data-target-goal]:visible [data-target-field=amount]').fill(amount)
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        expected=[500,500,500,500,500,500,650,800]
        assert page.evaluate('()=>targets.doc.goals.map(g=>g.amount).sort((a,b)=>a-b)')==expected
        after=page.evaluate('()=>targets.doc.entries')
        for identity,entry in before.items():assert after[identity]==entry,'existing snapshots must remain unchanged'
        page.reload();page.wait_for_function('()=>targets.ready')
        assert page.evaluate('()=>targets.doc.goals.map(g=>g.amount).sort((a,b)=>a-b)')==expected
