"""Local durable limits plus real SDK framing over synthetic stores; no network."""
from copy import deepcopy
from pathlib import Path
import json,os,subprocess,sys,time
import pytest
from hosted.security.account_word_campaign import BackfillCampaign,DEFAULT_LIMITS
from hosted.security.account_word_backfill import fingerprint
from hosted.security.account_word_backfill_store import BackfillBudget,BackfillBudgetExceeded,BackfillUnavailable,BackfillOutcomeUnknown
from hosted.security.account_word_operator import inspect_account_plan,operator_worker
from tests.test_v10596_account_word_content import content
from tests.test_v10596_account_word_transport import sdk,UID,PID,until

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Durable local operator requires POSIX locking')
BORN='2026-11-16T00:00:00Z';PROJECT='breadmaker-test-123';BUCKET='synthetic-bucket'


def plan():
    data=dict(schema_version=1,user_id=UID,user_created_at=BORN,projects={PID:dict(created_at=BORN,generation=1)})
    return dict(data,fingerprint=fingerprint(data))


def options(plans=None,limits=None):
    return dict(project=PROJECT,bucket=BUCKET,approved_plans=plans or [plan()],until=int(time.time())+3600,limits=limits or dict(DEFAULT_LIMITS))


def test_cumulative_reservations_survive_successful_invocation_and_process_restart(tmp_path):
    p=tmp_path/'campaign.json';kw=options();c=BackfillCampaign(p,**kw)
    c.run(lambda:c.reserve(reads=3,writes=1,rpcs=2))
    next_session=BackfillCampaign(p,**kw);next_session.run(lambda:next_session.reserve(reads=4,rpcs=1))
    state=next_session.snapshot();assert state['used']['reads']==7 and state['used']['writes']==1 and state['used']['rpcs']==3 and state['status']=='active'
    text=p.read_text();assert UID not in text and PID not in text and BORN not in text
    assert p.stat().st_mode&0o777==0o600
    changed=c.limits;changed['reads']=999;assert c.limits['reads']!=999
    copied=c.plan_for(UID);copied['projects'].clear();assert c.plan_for(UID)['projects']


def test_cumulative_cap_stops_before_attempt_and_cannot_reset_on_new_budget(tmp_path):
    p=tmp_path/'campaign.json';limits=dict(DEFAULT_LIMITS,reads=5);kw=options(limits=limits);c=BackfillCampaign(p,**kw)
    c.run(lambda:c.reserve(reads=3));calls=[]
    def too_many():c.reserve(reads=3);calls.append('forbidden provider call')
    with pytest.raises(BackfillBudgetExceeded):c.run(too_many)
    assert not calls and c.snapshot()['used']['reads']==3 and c.snapshot()['status']=='stopped'
    c2=BackfillCampaign(p,**kw)
    with pytest.raises(BackfillBudgetExceeded):c2.run(lambda:calls.append('forbidden retry'))
    assert not calls


@pytest.mark.parametrize('fault',['scope','limits','until'])
def test_reopening_cannot_change_the_approved_scope_or_allowance(tmp_path,fault):
    p=tmp_path/'campaign.json';kw=options();c=BackfillCampaign(p,**kw);c.run(lambda:c.reserve(reads=1))
    other=deepcopy(kw)
    if fault=='scope':other['bucket']='other-bucket'
    elif fault=='limits':other['limits']['reads']+=1
    else:other['until']+=1
    with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**other)
    assert c.snapshot()['used']['reads']==1


def test_first_error_stops_all_later_work_without_saving_private_exception_text(tmp_path):
    p=tmp_path/'campaign.json';c=BackfillCampaign(p,**options())
    def broken():c.reserve(rpcs=2);raise ValueError('Synthetic private content must not enter checkpoint')
    with pytest.raises(ValueError):c.run(broken)
    assert c.snapshot()['status']=='stopped' and c.snapshot()['used']['rpcs']==2
    assert 'Synthetic private content' not in p.read_text()
    with pytest.raises(BackfillBudgetExceeded):c.run(lambda:pytest.fail('Continued after first error'))


def test_crash_left_running_checkpoint_is_not_automatically_reopened(tmp_path):
    p=tmp_path/'campaign.json';kw=options();BackfillCampaign(p,**kw)
    script="from hosted.security.account_word_campaign import BackfillCampaign;import os;c=BackfillCampaign("+repr(str(p))+",**"+repr(kw)+");c.run(lambda:(c.reserve(reads=3),os._exit(23)))"
    result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')},capture_output=True,timeout=10)
    assert result.returncode==23,result.stderr
    recovered=BackfillCampaign(p,**kw);state=recovered.snapshot();assert state['status']=='running' and state['used']['reads']==3
    with pytest.raises(BackfillBudgetExceeded):recovered.run(lambda:pytest.fail('Crash caused a fresh allowance'))


def test_single_flight_rejects_a_second_operator_without_provider_work(tmp_path):
    p=tmp_path/'campaign.json';kw=options();c=BackfillCampaign(p,**kw)
    def active():
        with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**kw)
        c.reserve(reads=1)
    c.run(active);assert c.snapshot()['used']['reads']==1


def test_received_response_that_crosses_limit_is_counted_then_stopped(tmp_path):
    p=tmp_path/'campaign.json';kw=options(limits=dict(DEFAULT_LIMITS,response_bytes=100));c=BackfillCampaign(p,**kw)
    with pytest.raises(BackfillBudgetExceeded):c.run(lambda:c.observe_response(101))
    assert c.snapshot()['used']['response_bytes']==101 and c.snapshot()['status']=='stopped'
    assert BackfillCampaign(p,**kw).snapshot()['used']['response_bytes']==101


def worker(content,tmp_path,**limit_changes):
    reader,args,raw,api,blob,requested,_=content;store=reader.store;objects=reader.objects
    objects._client.project=store.client.project;objects._bucket.name=BUCKET
    approved=inspect_account_plan(store.client,owner_user_id=UID,expected_project=PROJECT,budget=BackfillBudget())
    c=BackfillCampaign(tmp_path/'campaign.json',**options(plans=[approved],limits=dict(DEFAULT_LIMITS,**limit_changes)))
    b=BackfillBudget(campaign=c)
    w=operator_worker(store.client,objects,owner_user_id=UID,expected_project=PROJECT,expected_bucket=BUCKET,budget=b,approved_plan=approved)
    return w,c,b,approved,raw,api,blob,requested


def test_complete_campaign_uses_per_account_jobs_and_preserves_canonical_data(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path);before=deepcopy(raw._documents)
    h=w.initialize_account(p,approved=True,deadline=until())['jobs'][0]
    assert w.step(h,expected_cursor=None,approved=True,deadline=until())['phase']=='complete'
    assert raw.get(('users',UID,'metrics','saved_words_v1')).data['saved_words']==3
    state=c.snapshot();assert state['used']['object_rpcs']==2 and state['used']['download_bytes']==blob.size+1
    assert state['used']['response_bytes']>0 and state['used']['content_bytes']==1024*1024 and state['status']=='active'
    for path,value in before.items():assert raw._documents[path]==value
    calls=len(api.calls);again=w.step(h,expected_cursor=None,approved=True,deadline=until());assert again['phase']=='complete'
    assert c.snapshot()['used']['rpcs']>state['used']['rpcs'] and len(names)==1 and len(api.calls)>calls


def test_object_operation_cap_stops_before_download_and_later_blocks(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path,object_rpcs=1)
    h=w.initialize_account(p,approved=True,deadline=until())['jobs'][0];before=deepcopy(raw._documents)
    with pytest.raises(BackfillBudgetExceeded):w.step(h,expected_cursor=None,approved=True,deadline=until())
    assert [name for name,_ in blob.calls]==['reload'] and raw._documents==before and c.snapshot()['status']=='stopped'
    calls=len(api.calls)
    with pytest.raises(BackfillBudgetExceeded):w.inspect_account(UID,deadline=until())
    assert len(api.calls)==calls


def test_write_cap_failure_is_known_before_dispatch_not_an_unknown_commit(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path,writes=1);before=deepcopy(raw._documents)
    with pytest.raises(BackfillBudgetExceeded):w.initialize_account(p,approved=True,deadline=until())
    assert raw._documents==before and all(not r['writes'] for method,r in api.calls if method=='commit')
    assert c.snapshot()['used']['writes']==0


def test_lost_commit_response_stops_campaign_and_keeps_charged_landed_state(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path);api.uncertain=True
    with pytest.raises(BackfillOutcomeUnknown):w.initialize_account(p,approved=True,deadline=until())
    assert raw.get(('users',UID,'metrics','saved_words_v1')) is not None
    state=c.snapshot();assert state['status']=='stopped' and state['stop_reason']=='unknown_commit' and state['used']['writes']==2
    assert api.calls[-1][0]=='commit'
    calls=len(api.calls)
    with pytest.raises(BackfillBudgetExceeded):w.initialize_account(p,approved=True,deadline=until())
    assert len(api.calls)==calls


def test_budget_failure_after_commit_ack_is_reported_unknown_not_rolled_back(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    def after_dispatch(_raw):b.limits['response_bytes']=b.used['response_bytes']+1
    api.before_commit=after_dispatch
    with pytest.raises(BackfillOutcomeUnknown):w.initialize_account(p,approved=True,deadline=until())
    assert raw.get(('users',UID,'metrics','saved_words_v1')) is not None
    assert c.snapshot()['stop_reason']=='unknown_commit' and c.snapshot()['used']['writes']==2


def test_stale_plan_or_foreign_job_stops_before_content_access(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    with pytest.raises(BackfillUnavailable):w.step(dict(user_id=UID,project_id=PID,job_id='word_'+'f'*64),expected_cursor=None,approved=True,deadline=until())
    assert not names and c.snapshot()['status']=='stopped'


@pytest.mark.parametrize('fault',['negative','bool','unknown'])
def test_corrupt_checkpoint_never_grants_work(tmp_path,fault):
    p=tmp_path/'campaign.json';kw=options();BackfillCampaign(p,**kw);data=json.loads(p.read_text())
    if fault=='negative':data['used']['reads']=-1
    elif fault=='bool':data['used']['reads']=True
    else:data['unreviewed']='synthetic'
    p.write_text(json.dumps(data))
    with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**kw)


def test_checkpoint_symlink_is_rejected_without_modifying_target(tmp_path):
    target=tmp_path/'target';target.write_text('synthetic');p=tmp_path/'campaign.json';p.symlink_to(target)
    with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**options())
    assert target.read_text()=='synthetic'


def test_full_current_size_synthetic_resource_scenario_spans_233_invocations(tmp_path):
    # Resource-vector rehearsal only: no14881 bodies, SDKs or cloud calls.
    c=BackfillCampaign(tmp_path/'campaign.json',**options());remaining=14881;invocations=0
    while remaining:
        n=min(64,remaining);b=BackfillBudget(campaign=c)
        def charge():
            b.reserve(reads=25*n,writes=n+2*((n+7)//8),rpcs=12*n,content_bytes=n*1024*1024,
                      download_bytes=2048*n,object_rpcs=2*n)
            b.observe_response(2*1024*1024)
        c.run(charge);remaining-=n;invocations+=1
    state=c.snapshot();assert invocations==233 and state['used']['content_bytes']==14881*1024*1024
    assert state['used']['response_bytes']==233*2*1024*1024 and state['used']['writes']==18603
    assert all(state['used'][k]<=DEFAULT_LIMITS[k] for k in DEFAULT_LIMITS)


def test_checkpoint_failure_prevents_provider_attempt_and_leaves_uncertain_state(tmp_path,monkeypatch):
    p=tmp_path/'campaign.json';kw=options();c=BackfillCampaign(p,**kw);save=c._save;calls=[]
    def failed_write():
        if c._state['used']['reads']:raise OSError('Synthetic local disk failure')
        save()
    monkeypatch.setattr(c,'_save',failed_write)
    def work():c.reserve(reads=1);calls.append('forbidden provider call')
    with pytest.raises(OSError):c.run(work)
    assert not calls and json.loads(p.read_text())['status']=='running'
    with pytest.raises(BackfillBudgetExceeded):BackfillCampaign(p,**kw).run(lambda:pytest.fail('Continued after checkpoint failure'))


def test_late_response_is_recorded_before_deadline_stop(tmp_path):
    now=[time.time()];kw=options();kw.update(until=int(now[0])+3600,clock=lambda:now[0]);c=BackfillCampaign(tmp_path/'campaign.json',**kw)
    def work():
        c.reserve(rpcs=1);now[0]+=3601;c.observe_response(128)
    with pytest.raises(BackfillBudgetExceeded):c.run(work)
    state=c.snapshot();assert state['used']['response_bytes']==128 and state['status']=='stopped' and state['stop_reason']=='deadline'


def test_lost_final_publication_can_be_reconciled_without_retry_or_new_allowance(content,tmp_path,monkeypatch):
    from hosted.security.account_word_operator import inspect_job_state
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    handle=w.initialize_account(p,approved=True,deadline=until())['jobs'][0];native=api.commit
    def lose_write(request,**kw):
        if request['writes']:api.uncertain=True
        return native(request,**kw)
    monkeypatch.setattr(api,'commit',lose_write)
    with pytest.raises(BackfillOutcomeUnknown):w.step(handle,expected_cursor=None,approved=True,deadline=until())
    before=deepcopy(raw._documents);spent=c.snapshot()['used'];calls=len(api.calls)
    state=inspect_job_state(content[0].store.client,approved_plan=p,handle=handle,expected_project=PROJECT,budget=BackfillBudget())
    assert state['phase']=='complete' and raw._documents==before and c.snapshot()['used']==spent
    assert all(not request['writes'] for method,request in api.calls[calls:] if method=='commit')
    with pytest.raises(BackfillBudgetExceeded):w.step(handle,expected_cursor=None,approved=True,deadline=until())
    assert len(names)==1


def test_final_local_preflight_failure_is_known_before_underlying_dispatch(content,tmp_path,monkeypatch):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path);native=b.reserve;before=deepcopy(raw._documents)
    def expire(**charges):
        native(**charges)
        if charges.get('writes'):b.deadline=time.monotonic()-1
    monkeypatch.setattr(b,'reserve',expire)
    with pytest.raises(BackfillBudgetExceeded):w.initialize_account(p,approved=True,deadline=until())
    assert raw._documents==before and c.snapshot()['stop_reason']=='budget'
    assert all(not request['writes'] for method,request in api.calls if method=='commit')


def test_cumulative_ack_overage_retains_unknown_commit_classification(content,tmp_path):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    def near_cumulative_cap(_raw):
        c._state['used']['response_bytes']=c.limits['response_bytes']-1;c._save()
    api.before_commit=near_cumulative_cap
    with pytest.raises(BackfillOutcomeUnknown):w.initialize_account(p,approved=True,deadline=until())
    state=c.snapshot();assert state['status']=='stopped' and state['stop_reason']=='unknown_commit'
    assert state['used']['response_bytes']>state['limits']['response_bytes']
    assert raw.get(('users',UID,'metrics','saved_words_v1')) is not None


@pytest.mark.parametrize('target',['checkpoint','lock'])
def test_special_file_checkpoints_and_locks_do_not_block(tmp_path,target):
    p=tmp_path/'campaign.json';special=p if target=='checkpoint' else p.with_name(p.name+'.lock')
    os.mkfifo(special,0o600);started=time.monotonic()
    with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**options())
    assert time.monotonic()-started<1


def test_hardlinked_checkpoint_does_not_grant_operator_work(tmp_path):
    p=tmp_path/'campaign.json';kw=options();BackfillCampaign(p,**kw);os.link(p,tmp_path/'alias')
    with pytest.raises(BackfillUnavailable):BackfillCampaign(p,**kw)


@pytest.mark.parametrize('interruption',[KeyboardInterrupt,SystemExit])
def test_interruption_after_commit_dispatch_is_preserved_and_marked_unknown(content,tmp_path,monkeypatch,interruption):
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path);native=api.commit
    def interrupted(request,**kw):
        result=native(request,**kw)
        if request['writes']:raise interruption(23)
        return result
    monkeypatch.setattr(api,'commit',interrupted)
    with pytest.raises(interruption):w.initialize_account(p,approved=True,deadline=until())
    assert c.snapshot()['stop_reason']=='unknown_commit' and raw.get(('users',UID,'metrics','saved_words_v1')) is not None
    assert api.calls[-1][0]=='commit'


def nine_blocks(content):
    reader,args,raw,api,blob,names,pointer=content
    def seed(tx):
        tx.delete(('projects',PID,'blocks','b_one'))
        for n in range(9):
            bid='b_'+str(n).zfill(2);ptr=dict(pointer,object_name=pointer['object_name'].replace('/b_one/','/'+bid+'/'))
            tx.create(('projects',PID,'blocks',bid),dict(generation=1,block_id=bid,payload_object=ptr))
    raw.run_transaction(seed)
    from types import SimpleNamespace
    def get_blob(name):
        assert '/b_' in name and '/b_one/' not in name;names.append(name);return blob
    reader.objects._bucket=SimpleNamespace(blob=get_blob,name=BUCKET)


def test_real_worker_resumes_with_a_new_invocation_budget_without_resetting_campaign(content,tmp_path):
    from hosted.security.account_word_operator import ready_for_step
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    b.limits['content_bytes']=16*1024*1024
    handle=w.initialize_account(p,approved=True,deadline=until())['jobs'][0]
    assert ready_for_step(b)
    first=w.step(handle,expected_cursor=None,approved=True,deadline=until())
    assert first['phase']=='running' and first['seen_blocks']==8 and b.used['content_bytes']==8*1024*1024
    assert not ready_for_step(b)
    spent=c.snapshot()['used'];reopened=BackfillCampaign(c.path,project=PROJECT,bucket=BUCKET,approved_plans=[p],until=c.until)
    b2=BackfillBudget(campaign=reopened)
    w2=operator_worker(content[0].store.client,content[0].objects,owner_user_id=UID,expected_project=PROJECT,expected_bucket=BUCKET,budget=b2,approved_plan=p)
    second=w2.step(handle,expected_cursor=first['cursor'],approved=True,deadline=until())
    assert second['phase']=='complete' and second['seen_blocks']==9
    assert b2.used['content_bytes']==1024*1024 and reopened.snapshot()['used']['content_bytes']==9*1024*1024
    assert reopened.snapshot()['used']['reads']>spent['reads'] and len(names)==9 and len(set(names))==9
    assert raw.get(('users',UID,'metrics','saved_words_v1')).data['saved_words']==27


def test_explicit_controller_rotates_before_exhaustion_and_never_retries_an_error(content,tmp_path):
    from hosted.security.account_word_operator import process_account_campaign
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    result=process_account_campaign(content[0].store.client,content[0].objects,campaign=c,approved_plan=p,approved=True)
    assert result['status']=='complete' and result['steps']==2 and result['invocations']>=2
    assert len(names)==9 and c.snapshot()['used']['content_bytes']==9*1024*1024


def test_explicit_controller_stops_at_step_cap_before_next_body(content,tmp_path):
    from hosted.security.account_word_operator import process_account_campaign
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    with pytest.raises(BackfillBudgetExceeded):process_account_campaign(content[0].store.client,content[0].objects,campaign=c,approved_plan=p,approved=True,max_steps=1)
    assert len(names)==8 and c.snapshot()['status']=='stopped'


def test_preparation_resolves_only_the_processable_unknown_proof_prefix(content,tmp_path):
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    handle=w.initialize_account(p,approved=True,deadline=until())['jobs'][0];before=len(api.calls)
    state=w.step(handle,expected_cursor=None,approved=True,deadline=until())
    proof_reads=[name for method,request in api.calls[before:] if method=='get' for name in request['documents'] if '/word_block_counts/' in name]
    assert len(proof_reads)==8 and len(names)==8 and state['phase']=='running' and state['seen_blocks']==8


def test_existing_valid_proofs_do_not_consume_the_unknown_body_prefix(content,tmp_path):
    from hosted.security.account_word_guard import proof_path
    from hosted.security.saved_word_projection import DEFINITION
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    born=p['user_created_at'];project_born=p['projects'][PID]['created_at']
    def seed(tx):
        for n in range(8):
            bid='b_'+str(n).zfill(2)
            tx.create(proof_path(UID,born,PID,project_born,bid),dict(schema_version=1,definition=DEFINITION,user_created_at=born,project_id=PID,project_created_at=project_born,block_id=bid,source_generation=1,words=3))
    raw.run_transaction(seed)
    handle=w.initialize_account(p,approved=True,deadline=until())['jobs'][0]
    state=w.step(handle,expected_cursor=None,approved=True,deadline=until())
    assert state['phase']=='complete' and state['seen_blocks']==9 and len(names)==1
    assert raw.get(('users',UID,'metrics','saved_words_v1')).data['saved_words']==27


def test_explicit_controller_stops_on_first_invalid_object_without_later_bodies(content,tmp_path):
    from hosted.security.account_word_operator import process_account_campaign
    nine_blocks(content);w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    blob.value=b'x'*blob.size
    with pytest.raises(BackfillUnavailable):process_account_campaign(content[0].store.client,content[0].objects,campaign=c,approved_plan=p,approved=True)
    assert len(names)==1 and c.snapshot()['status']=='stopped'
    assert not any(path[2:3]==('word_block_counts',) for path in raw._documents)


def test_controller_rotates_before_inspecting_next_project_near_deadline(content,tmp_path,monkeypatch):
    from hosted.security import account_word_operator as operator
    raw=content[2];second='e'*32
    def seed(tx):
        tx.create(('projects',second),dict(raw.get(('projects',PID)).data))
        tx.create(('projects',second,'members',UID),dict(raw.get(('projects',PID,'members',UID)).data))
    raw.run_transaction(seed)
    w,c,b,p,raw,api,blob,names=worker(content,tmp_path)
    native_step=operator._CampaignWorker.step;native_inspect=operator._CampaignWorker.inspect_job
    completed=[];checked=[]
    def step(self,*args,**kw):
        result=native_step(self,*args,**kw)
        if result['phase']=='complete':
            completed.append(self._worker.store.budget)
            self._worker.store.budget.deadline=time.monotonic()-1
        return result
    def inspect(self,*args,**kw):
        if completed:
            assert self._worker.store.budget is not completed[-1]
            checked.append(True)
        return native_inspect(self,*args,**kw)
    monkeypatch.setattr(operator._CampaignWorker,'step',step)
    monkeypatch.setattr(operator._CampaignWorker,'inspect_job',inspect)
    result=operator.process_account_campaign(content[0].store.client,content[0].objects,campaign=c,approved_plan=p,approved=True)
    assert result['projects']==2 and result['steps']==2 and checked and c.snapshot()['status']=='active'
