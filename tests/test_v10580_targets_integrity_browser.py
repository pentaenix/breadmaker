"""Real local/hosted transactions and independent browser durability stores."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import requests

from tests.test_v10569_narration_browser import browser
from tests.test_v10580_targets_browser import add_word_goal


def target_key(page):
    return page.evaluate("()=>runtimeSupports('hosted_persistence')?'targets-v1':'targets-v1-'+currentUserIdentity()")


def client_headers(page,base,pid):
    return {'Origin':base,'X-Breadmaker-Project':pid,
            'X-Breadmaker-User':page.evaluate('currentUserIdentity()'),
            'X-Breadmaker-Client':page.evaluate('CLIENT_ID')}


def _exercise_targets_revision_is_checked_inside_the_store_transaction(tmp_path,local):
    with browser(tmp_path,local=local) as (base,page,pid):
        url=base+'/api/ui-state/'+target_key(page);headers=client_headers(page,base,pid)
        def project_generation():
            if local:return page.request.get(base+'/api/bootstrap',headers=headers).json()['project']['project_generation']
            return page.request.get(base+'/api/project/metadata',headers=headers).json()['generation']
        generation=project_generation()
        initial=page.request.get(url,headers=headers).json()
        assert page.request.put(url,data={'value':{}},headers=headers).status==428
        barrier=Barrier(2)
        def write(value):
            barrier.wait(timeout=2)
            return requests.put(url,json={'value':{'version':1,'counter':value},'expected_revision':initial['revision']},headers=headers,timeout=3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(write,[2,3]))
        assert sorted(response.status_code for response in results)==[200,409]
        assert next(r for r in results if r.status_code==409).json()['detail']=='TARGETS_REVISION_CONFLICT'
        saved=page.request.get(url,headers=headers).json();assert saved['value']['counter'] in [2,3]
        assert page.request.put(url,data={'value':{'counter':99},'expected_revision':initial['revision']},headers=headers).status==409
        assert page.request.get(url,headers=headers).json()['value']==saved['value']
        oversized=page.request.put(url,data={'value':{'padding':'x'*(256*1024)},'expected_revision':saved['revision']},headers=headers)
        assert oversized.status in [400,413]
        assert page.request.get(url,headers=headers).json()['value']==saved['value']
        # The optional extension does not change ordinary preference semantics.
        assert page.request.put(base+'/api/ui-state/ordinary-preference',data={'value':7},headers=headers).status==200
        assert project_generation()==generation


def _exercise_targets_independent_devices_retry_a_forced_atomic_merge_conflict(tmp_path,local,shared_tab=False):
    with browser(tmp_path,local=local) as (base,page,pid):
        add_word_goal(page)
        interval=page.evaluate("""async()=>{const settings=structuredClone(targets.doc);settings.goals.push({id:uid(),metric:'time',period:'daily',amount:600,created:Date.now()});targetsEnqueue({},Date.now(),settings);await flushTargets();const end=targetsModel.period('daily').end;return [end-3000,end-1000]}""")
        context=None
        if shared_tab:
            with page.expect_popup() as opened:page.evaluate("()=>window.open('about:blank','_blank')")
            other=opened.value
        else:
            context=page.context.browser.new_context()
            # Share only the test editing lease; IDB and stream IDs are isolated.
            context.add_init_script('sessionStorage.setItem("breadmaker-client-id",'+json.dumps(page.evaluate('CLIENT_ID'))+')')
            other=context.new_page()
        try:
            other.goto(base+'/p/'+pid);other.wait_for_function('()=>targets.ready')
            page.evaluate('span=>targetsEnqueue({words:2,time:2},Date.now(),null,{time:span})',interval)
            other.evaluate('span=>targetsEnqueue({words:3,time:2},Date.now(),null,{time:span})',interval)
            page.evaluate('async()=>await targetsJournal.drain()');other.evaluate('async()=>await targetsJournal.drain()')
            held=[];release=[False];conflicts=[]
            def pause_put(route):
                if route.request.method=='PUT' and not release[0]:held.append(route)
                else:route.continue_()
            for surface in [page,other]:
                surface.route('**/api/ui-state/targets-v1*',pause_put)
                surface.on('response',lambda response:conflicts.append(response.status) if '/api/ui-state/targets-v1' in response.url and response.status==409 else None)
                surface.evaluate('()=>{window.integritySave=flushTargets()}')
            for _ in range(30):
                if len(held)==2:break
                page.wait_for_timeout(10)
            assert len(held)==2,'both devices must read the same revision before either PUT'
            release[0]=True
            for route in held:route.continue_()
            page.evaluate('async()=>await window.integritySave');other.evaluate('async()=>await window.integritySave')
            assert conflicts==[409]
            result=page.evaluate('''async span=>{const saved=(await targetsRequest()).value,progress=targetsModel.progress(saved),clock=progress.find(p=>p.metric==='time');return {value:progress[0].value,sources:Object.keys(saved.receipts).length,overlap:clock.intervals.reduce((sum,[from,to])=>sum+Math.max(0,Math.min(to,span[1])-Math.max(from,span[0])),0)}}''',interval)
            assert result=={'value':5,'sources':1 if shared_tab else 2,'overlap':2000}
            for surface in [page,other]:
                assert surface.evaluate('''async()=>{const doc=(await targetsRequest()).value,rows=await targetsJournal.read(targets.context);return rows.every(op=>op.sequence>(doc.receipts[op.stream]||0))}'''),'acknowledged operations must be removed; fresh clock ticks can remain pending'
        finally:
            if context:context.close()
            else:other.close()


def _exercise_targets_simultaneous_tabs_share_atomic_sequence_and_outbox(tmp_path,local):
    _exercise_targets_independent_devices_retry_a_forced_atomic_merge_conflict(tmp_path,local,shared_tab=True)


def _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path,local,fail_idb,engine='chromium'):
    with browser(tmp_path,local=local,engine=engine) as (base,page,pid):
        add_word_goal(page)
        context=page.context.browser.new_context()
        context.add_init_script('sessionStorage.setItem("breadmaker-client-id",'+json.dumps(page.evaluate('CLIENT_ID'))+')')
        source=context.new_page()
        try:
            source.goto(base+'/p/'+pid);source.wait_for_function('()=>targets.ready')
            # No pagehide/blur request can save progress: simulate a severed transport.
            source.route('**/api/ui-state/targets-v1*',lambda route:route.abort('failed') if route.request.method=='PUT' else route.continue_())
            if fail_idb:
                source.evaluate("""()=>{const original=IDBDatabase.prototype.transaction;IDBDatabase.prototype.transaction=function(names,...rest){if(Array.isArray(names)&&names.includes('streams'))throw new Error('Synthetic abrupt staging failure');return original.call(this,names,...rest)}}""")
            source.evaluate('()=>targetsEnqueue({words:7})')
            source.evaluate('async()=>{try{await targetsJournal.drain()}catch{}}')
            recovered=context.new_page()
            source.close(run_before_unload=False)
            assert page.evaluate('async()=>targetsModel.progress((await targetsRequest()).value)[0].value')==0
            recovered.goto(base+'/p/'+pid);recovered.wait_for_function('()=>targets.ready')
            recovered.evaluate('async()=>await flushTargets()')
            assert recovered.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==7
            assert recovered.evaluate('async()=>await targetsJournal.read(targets.context)')==[]
            # A fresh read/retry cannot resurrect the acknowledged interval.
            recovered.evaluate('async()=>await flushTargets()')
            assert recovered.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==7
        finally:context.close()


def _exercise_targets_webkit_recovers_prejournal_in_a_new_tab(tmp_path,local):
    _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path,local,True,engine='webkit')


def _exercise_targets_failed_prejournal_cleanup_cannot_resurrect_an_acknowledged_operation(tmp_path,local):
    with browser(tmp_path,local=local) as (_,page,_):
        add_word_goal(page)
        page.evaluate("""()=>{window.originalTargetsRemove=Storage.prototype.removeItem;Storage.prototype.removeItem=function(key){if(key.startsWith('breadmaker-targets-journal:'))throw new Error('Synthetic cleanup failure');return window.originalTargetsRemove.call(this,key)};targetsEnqueue({words:2})}""")
        page.evaluate('async()=>await flushTargets()')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==2
        assert page.evaluate('async()=>(await targetsJournal.read(targets.context)).length')==1,'retain the IDB identity until its pre-journal is proven removed'
        page.evaluate('()=>Storage.prototype.removeItem=window.originalTargetsRemove')
        page.evaluate('async()=>await flushTargets()')
        assert page.evaluate('()=>targetsModel.progress(targets.doc)[0].value')==2
        assert page.evaluate('async()=>await targetsJournal.read(targets.context)')==[]


# Explicit cases preserve all parameter combinations under one-item browser jobs.

def test_targets_revision_is_checked_inside_the_store_transaction_local(tmp_path):
    _exercise_targets_revision_is_checked_inside_the_store_transaction(tmp_path, local=True)

def test_targets_revision_is_checked_inside_the_store_transaction_hosted(tmp_path):
    _exercise_targets_revision_is_checked_inside_the_store_transaction(tmp_path, local=False)

def test_targets_independent_devices_retry_a_forced_atomic_merge_conflict_local(tmp_path):
    _exercise_targets_independent_devices_retry_a_forced_atomic_merge_conflict(tmp_path, local=True)

def test_targets_independent_devices_retry_a_forced_atomic_merge_conflict_hosted(tmp_path):
    _exercise_targets_independent_devices_retry_a_forced_atomic_merge_conflict(tmp_path, local=False)

def test_targets_simultaneous_tabs_share_atomic_sequence_and_outbox_local(tmp_path):
    _exercise_targets_simultaneous_tabs_share_atomic_sequence_and_outbox(tmp_path, local=True)

def test_targets_simultaneous_tabs_share_atomic_sequence_and_outbox_hosted(tmp_path):
    _exercise_targets_simultaneous_tabs_share_atomic_sequence_and_outbox(tmp_path, local=False)

def test_targets_new_tab_recovers_unacknowledged_progress_after_close_local_idb(tmp_path):
    _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path, local=True, fail_idb=False)

def test_targets_new_tab_recovers_unacknowledged_progress_after_close_local_prejournal(tmp_path):
    _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path, local=True, fail_idb=True)

def test_targets_new_tab_recovers_unacknowledged_progress_after_close_hosted_idb(tmp_path):
    _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path, local=False, fail_idb=False)

def test_targets_new_tab_recovers_unacknowledged_progress_after_close_hosted_prejournal(tmp_path):
    _exercise_targets_new_tab_recovers_unacknowledged_progress_after_close(tmp_path, local=False, fail_idb=True)

def test_targets_webkit_recovers_prejournal_in_a_new_tab_local(tmp_path):
    _exercise_targets_webkit_recovers_prejournal_in_a_new_tab(tmp_path, local=True)

def test_targets_webkit_recovers_prejournal_in_a_new_tab_hosted(tmp_path):
    _exercise_targets_webkit_recovers_prejournal_in_a_new_tab(tmp_path, local=False)

def test_targets_failed_prejournal_cleanup_cannot_resurrect_an_acknowledged_operation_local(tmp_path):
    _exercise_targets_failed_prejournal_cleanup_cannot_resurrect_an_acknowledged_operation(tmp_path, local=True)

def test_targets_failed_prejournal_cleanup_cannot_resurrect_an_acknowledged_operation_hosted(tmp_path):
    _exercise_targets_failed_prejournal_cleanup_cannot_resurrect_an_acknowledged_operation(tmp_path, local=False)
