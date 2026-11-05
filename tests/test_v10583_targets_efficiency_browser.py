"""Completed timers must not fill the durable outbox with no-op clock writes."""
from playwright.sync_api import expect
from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import open_targets,write_more


def tick(page,base,steps=6,seconds=10):
    return page.evaluate('''({base,steps,seconds})=>{
      for(let i=1;i<=steps;i++){
        targets.lastTick=base+(i-1)*seconds*1000;
        targets.lastActivity=base+i*seconds*1000;
        Date.now=()=>base+i*seconds*1000;
        targetsClock(Date.now());
      }
      return targetsModel.progress(targets.doc).map(p=>({metric:p.metric,value:p.value,achieved:!!p.achieved}));
    }''',{'base':base,'steps':steps,'seconds':seconds})


def _completed_timers(home,local,engine):
    with browser(home,local=local,engine=engine,page_options={'timezone_id':'UTC'}) as (_,page,_):
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        base=page.evaluate('''()=>{const noon=new Date();noon.setUTCHours(12,0,0,0);const base=noon.getTime();Date.now=()=>base;targets.lastTick=base;targets.lastActivity=base;window.targetSoundPlays=0;HTMLMediaElement.prototype.play=function(){targetSoundPlays++;return Promise.resolve()};Object.defineProperty(document,'hasFocus',{configurable:true,value:()=>true});return base}''')
        open_targets(page)
        page.get_by_role('switch',name='Enable targets',exact=True).click()
        for metric,amount in [('time',1),('active',1),('words',2)]:
            page.locator('#targetsAdd').click()
            row=page.locator('[data-target-goal]').last
            row.locator('[data-target-field="metric"]').select_option(metric)
            row.locator('[data-target-field="amount"]').fill(str(amount))
        page.locator('#targetsSave').click();expect(page.locator('#modal')).to_be_hidden()
        progress=tick(page,base)
        assert [(p['value'],p['achieved']) for p in progress]==[(60,True),(60,True),(0,False)]
        page.evaluate('()=>flushTargets()')
        before=page.evaluate('()=>targetsRequest()')
        assert page.evaluate('()=>targets.pending.length')==0
        puts=[]
        page.on('request',lambda request:puts.append(request.url) if request.method=='PUT' and '/api/ui-state/targets-v1' in request.url else None)
        # There is still foreground/activity time, but neither completed timer
        # can consume it. A pending word goal must not create timer operations.
        progress=tick(page,base+60000)
        assert [(p['value'],p['achieved']) for p in progress]==[(60,True),(60,True),(0,False)]
        assert page.evaluate('()=>targets.pending.length')==0,'completed timers must not enqueue no-op contributions'
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')==0
        page.evaluate('()=>flushTargets()')
        assert puts==[],'read-only sync must not rewrite unchanged Targets'
        assert page.evaluate('async()=>(await targetsRequest()).revision')==before['revision']
        # Real prose is still tracked when the timer goals have finished.
        write_more(page,' one')
        assert page.evaluate('()=>targetsModel.progress(targets.doc).find(p=>p.metric==="words").value')==1
        page.evaluate('()=>flushTargets()')
        assert len(puts)==1
        # The next local-calendar period starts fresh and resumes timing.
        progress=tick(page,base+86400000,steps=1,seconds=1)
        assert [(p['value'],p['achieved']) for p in progress]==[(1,False),(1,False),(0,False)]
        assert page.evaluate('()=>targets.pending.length')==1
        page.evaluate('()=>flushTargets()')
        assert len(puts)==2
        assert page.evaluate('()=>Object.values(targets.doc.entries).filter(e=>e.achieved).length')==2
        assert page.evaluate('()=>targetSoundPlays')==0
        assert not errors,errors


def test_completed_targets_timers_stop_noop_writes_local_chromium(tmp_path):_completed_timers(tmp_path,True,'chromium')
def test_completed_targets_timers_stop_noop_writes_hosted_chromium(tmp_path):_completed_timers(tmp_path,False,'chromium')
def test_completed_targets_timers_stop_noop_writes_local_webkit(tmp_path):_completed_timers(tmp_path,True,'webkit')
def test_completed_targets_timers_stop_noop_writes_hosted_webkit(tmp_path):_completed_timers(tmp_path,False,'webkit')
