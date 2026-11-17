"""Explicit operator assembly; no credentials, automatic retries or activation.

Inspection has no content reader or writes. Mutation-capable workers require a
matching local durable campaign, inspected account plan and whole-run budget.
"""
import time
from hosted.security.account_word_backfill_store import BoundedAccountWordStore,BackfillBudget,BackfillUnavailable,BackfillBudgetExceeded
from hosted.security.account_word_content import BoundedCurrentBlockReader
from hosted.security.account_word_backfill import AccountWordBackfill,job_path
from hosted.security.account_word_campaign import BackfillCampaign,_plan
from hosted.security.account_word_campaign_transport import metered_client
from hosted.storage.google_driver import GoogleCloudObjectStore


def inspect_account_plan(client,*,owner_user_id,expected_project,budget):
    if type(budget) is not BackfillBudget or budget.campaign is not None:raise BackfillUnavailable('WORD_BACKFILL_INSPECTION_BUDGET_REQUIRED')
    store=BoundedAccountWordStore(metered_client(client,budget),project_id=expected_project,owner_user_id=owner_user_id,budget=budget)
    def no_content(**kw):raise BackfillUnavailable('WORD_BACKFILL_INSPECTION_ONLY')
    return AccountWordBackfill(store,no_content,enabled=True).inspect_account(owner_user_id,deadline=min(budget.deadline,time.monotonic()+29))


def inspect_job_state(client,*,approved_plan,handle,expected_project,budget):
    """Explicit bounded reconciliation only; cannot clear/restart a campaign."""
    if type(budget) is not BackfillBudget or budget.campaign is not None:raise BackfillUnavailable('WORD_BACKFILL_INSPECTION_BUDGET_REQUIRED')
    plan=_plan(approved_plan)
    store=BoundedAccountWordStore(metered_client(client,budget),project_id=expected_project,owner_user_id=plan['user_id'],budget=budget)
    def no_content(**kw):raise BackfillUnavailable('WORD_BACKFILL_INSPECTION_ONLY')
    wrapper=_CampaignWorker(AccountWordBackfill(store,no_content,enabled=True),plan,None)
    wrapper._handle(handle)
    return wrapper._worker.inspect_job(handle,deadline=min(budget.deadline,time.monotonic()+29))


class _CampaignWorker:
    def __init__(self,worker,plan,campaign):self._worker,self._plan,self._campaign=worker,plan,campaign
    def _handle(self,handle):
        if (type(handle) is not dict or set(handle)-{'user_id','project_id','job_id','cursor','phase'}
                or not {'user_id','project_id','job_id'}<=set(handle) or handle['user_id']!=self._plan['user_id']
                or handle['project_id'] not in self._plan['projects']):raise BackfillUnavailable('WORD_CAMPAIGN_HANDLE_SCOPE')
        pid=handle['project_id'];expected=job_path(self._plan['user_id'],self._plan['user_created_at'],pid,self._plan['projects'][pid])[-1]
        if handle['job_id']!=expected:raise BackfillUnavailable('WORD_CAMPAIGN_HANDLE_SCOPE')
    def inspect_account(self,uid,*,deadline):
        def inspect():
            if uid!=self._plan['user_id']:raise BackfillUnavailable('WORD_CAMPAIGN_OWNER_SCOPE')
            actual=self._worker.inspect_account(uid,deadline=deadline)
            if actual!=self._plan:raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_STALE')
            return actual
        return self._campaign.run(inspect)
    def initialize_account(self,plan,*,approved=False,deadline):
        def initialize():
            if _plan(plan)!=self._plan:raise BackfillUnavailable('WORD_CAMPAIGN_PLAN_SCOPE')
            return self._worker.initialize_account(plan,approved=approved,deadline=deadline)
        return self._campaign.run(initialize)
    def inspect_job(self,handle,*,deadline):
        def inspect():self._handle(handle);return self._worker.inspect_job(handle,deadline=deadline)
        return self._campaign.run(inspect)
    def step(self,handle,*,expected_cursor,approved=False,deadline):
        def step():
            self._handle(handle)
            return self._worker.step(handle,expected_cursor=expected_cursor,approved=approved,deadline=deadline)
        return self._campaign.run(step)


def operator_worker(client,objects,*,owner_user_id,expected_project,expected_bucket,budget,approved_plan):
    if (type(budget) is not BackfillBudget or type(budget.campaign) is not BackfillCampaign
            or type(objects) is not GoogleCloudObjectStore or type(expected_bucket) is not str or not expected_bucket
            or objects._bucket.name!=expected_bucket or objects._client.project!=expected_project):raise BackfillUnavailable('WORD_BACKFILL_OPERATOR_SCOPE')
    campaign=budget.campaign;plan=_plan(approved_plan)
    if (campaign.project!=expected_project or campaign.bucket!=expected_bucket or plan['user_id']!=owner_user_id
            or campaign.plan_for(owner_user_id)!=plan):raise BackfillUnavailable('WORD_BACKFILL_OPERATOR_PLAN_SCOPE')
    store=BoundedAccountWordStore(metered_client(client,budget),project_id=expected_project,owner_user_id=owner_user_id,budget=budget)
    return _CampaignWorker(AccountWordBackfill(store,BoundedCurrentBlockReader(store,objects),enabled=True),plan,campaign)


def ready_for_step(budget):
    """Conservative preflight; rotate BEFORE exhausting an invocation.

    Every provider operation still has its own actual admission. Rotating an
    invocation never replenishes the durable campaign's remaining allowance.
    """
    if type(budget) is not BackfillBudget or budget.campaign is None:return False
    worst=dict(reads=512,writes=34,rpcs=256,content_bytes=16*1024**2,
               download_bytes=64*1024**2,object_rpcs=16,response_bytes=8*1024**2)
    return not budget.failed and budget.deadline-time.monotonic()>=30 and all(budget.limits[k]-budget.used[k]>=v for k,v in worst.items())


def process_account_campaign(client,objects,*,campaign,approved_plan,approved=False,max_steps=10000,resource_budget=None):
    """Explicit operator call only. No scheduler, retry, flags or credentials.

    Source-side root/cursor/ownership CAS still executes for every step. The
    first failure propagates and freezes the shared campaign; no next account
    or invocation is selected after an error or an unknown commit.
    """
    if approved is not True or type(campaign) is not BackfillCampaign or type(max_steps) is not int or not 1<=max_steps<=100000:raise BackfillUnavailable('WORD_CAMPAIGN_APPROVAL_REQUIRED')
    plan=_plan(approved_plan);invocations=0
    def allocate():
        nonlocal invocations
        budget=BackfillBudget(campaign=campaign,resource_budget=resource_budget);invocations+=1
        worker=operator_worker(client,objects,owner_user_id=plan['user_id'],expected_project=campaign.project,
            expected_bucket=campaign.bucket,budget=budget,approved_plan=plan)
        return budget,worker
    budget,worker=allocate();jobs=worker.initialize_account(plan,approved=True,deadline=min(budget.deadline,time.monotonic()+29))['jobs'];steps=0
    for handle in jobs:
        if not ready_for_step(budget):budget,worker=allocate()
        current=worker.inspect_job(handle,deadline=min(budget.deadline,time.monotonic()+29))
        while current['phase']!='complete':
            if steps>=max_steps:
                def stop():raise BackfillBudgetExceeded('WORD_CAMPAIGN_STEP_LIMIT')
                campaign.run(stop)
            if not ready_for_step(budget):budget,worker=allocate()
            current=worker.step(handle,expected_cursor=current['cursor'],approved=True,deadline=min(budget.deadline,time.monotonic()+29));steps+=1
    return dict(status='complete',projects=len(jobs),steps=steps,invocations=invocations)
