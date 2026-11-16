from copy import deepcopy
import pytest
from hosted.security.account_word_backfill_store import BackfillBudget,BackfillBudgetExceeded,BackfillUnavailable
from hosted.security.account_word_operator import operator_worker
from tests.test_v10596_account_word_transport import sdk,UID,until
from tests.test_v10596_account_word_content import content


def test_metadata_budget_is_shared_and_exhaustion_cannot_write(sdk):
    store,api,raw,w=sdk;store.budget=BackfillBudget(reads=1)
    before=deepcopy(raw._documents)
    with pytest.raises(BackfillBudgetExceeded):w.inspect_account(UID,deadline=until())
    assert raw._documents==before and store.budget.used['reads']==1
    assert not any(name=='commit' for name,_ in api.calls)


def test_write_reservation_rejects_before_commit_instead_of_publishing_partial_state(sdk):
    store,api,raw,w=sdk;store.budget=BackfillBudget(writes=1)
    plan=w.inspect_account(UID,deadline=until());before=deepcopy(raw._documents)
    with pytest.raises(BackfillBudgetExceeded):w.initialize_account(plan,approved=True,deadline=until())
    assert raw._documents==before
    assert all(not request['writes'] for name,request in api.calls if name=='commit')


def test_download_allowance_is_reserved_before_media_get(content):
    reader,args,raw,api,blob,requested,_=content;reader.store.budget=BackfillBudget(download_bytes=1)
    before=deepcopy(raw._documents)
    with pytest.raises(BackfillBudgetExceeded):reader(**args)
    assert [name for name,_ in blob.calls]==['reload'] and raw._documents==before


def test_operator_reuses_clients_and_requires_exact_project_bucket_and_whole_run_budget(content):
    reader,args,raw,api,blob,requested,_=content;objects=reader.objects;store=reader.store
    objects._client.project=store.client.project;objects._bucket.name='synthetic-bucket'
    budget=BackfillBudget()
    worker=operator_worker(store.client,objects,owner_user_id=UID,expected_project=store.client.project,
        expected_bucket='synthetic-bucket',budget=budget)
    worker.inspect_account(UID,deadline=until());assert budget.used['reads']>0
    before=len(api.calls)
    with pytest.raises(BackfillUnavailable):operator_worker(store.client,objects,owner_user_id=UID,
        expected_project=store.client.project,expected_bucket='foreign-bucket',budget=BackfillBudget())
    assert len(api.calls)==before
