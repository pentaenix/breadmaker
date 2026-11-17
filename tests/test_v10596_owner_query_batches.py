"""Current-transaction owner cardinality, including collection-group phantoms."""
from copy import deepcopy
import time
import pytest
from google.cloud.firestore_v1 import types
from google.api_core.exceptions import FailedPrecondition
from hosted.firestore.owner_queries import owner_query
from hosted.security.account_metric_eligibility import GoogleFirestoreAccountEligibility
from hosted.security.account_word_backfill_store import BackfillUnavailable, BackfillOutcomeUnknown
from hosted.security.saved_word_reader import SavedWordsUnavailable
from tests.test_v10596_account_word_transport import sdk, UID, PID, BORN, until


def census(store):
    tx=store.client.transaction(max_attempts=1);tx._id=b'synthetic-word-transaction'
    return GoogleFirestoreAccountEligibility(store.client,transaction=tx,enabled=True).snapshot(deadline=until())


def seed(raw,n):
    raw.run_transaction(lambda tx:tx.delete(('projects',PID)))
    pids=[format(i,'032x') for i in range(n)]
    def fill(tx):
        for pid in pids:
            tx.create(('projects',pid),dict(status='active',generation=1,created_at=BORN))
            tx.create(('projects',pid,'members',UID),dict(user_id=UID,role='owner',created_at=BORN))
    raw.run_transaction(fill)
    return pids


@pytest.mark.parametrize('n',[0,1,20,21,100])
def test_real_sdk_chunks_preserve_complete_coverage_and_current_transaction(sdk,n):
    store,api,raw,_=sdk;pids=seed(raw,n)
    facts=census(store)
    assert set(facts['roots'])==set(pids) and set(facts['manifests'][UID])==set(pids)
    calls=[r for method,r in api.calls if method=='query' and r['structured_query'].from_[0].all_descendants]
    assert len(calls)==(n+19)//20
    for r in calls:
        q=r['structured_query'];branches=q.where.composite_filter.filters[1].composite_filter.filters
        assert len(branches)<=20 and q.limit==len(branches)+1
        assert r['transaction']==b'synthetic-word-transaction'
        assert [f.field_path for f in q.select.fields]==['user_id','role','created_at']
        assert all(int(x.field_filter.op)==5 and x.field_filter.field.field_path=='role'
            and x.field_filter.value.string_value=='owner' for x in q.where.composite_filter.filters[:1])
        for branch in branches:
            low,high=branch.composite_filter.filters
            assert low.field_filter.field.field_path==high.field_filter.field.field_path=='__name__'
            assert int(low.field_filter.op)==4 and int(high.field_filter.op)==1
            assert high.field_filter.value.reference_value==low.field_filter.value.reference_value+'\uf8ff'
    # Canonical protobuf equality rejects an implicit order/filter/cursor widening.
    if calls:
        first=pids[:20]
        assert calls[0]['structured_query']==owner_query(store.client,first)._to_protobuf()


@pytest.mark.parametrize('fault',['missing','duplicate','nested','prefix_suffix','wrong_id','missing_birth','invalid_birth'])
def test_each_invalid_owner_shape_rejects_the_whole_census(sdk,fault):
    store,_,raw,_=sdk
    other='usr_'+'c'*32;path=('projects',PID,'members',UID)
    def break_it(tx):
        if fault=='missing':tx.delete(path);return
        if fault=='duplicate':p=('projects',PID,'members',other)
        elif fault=='nested':p=('projects',PID,'other','x','members',other)
        elif fault=='prefix_suffix':p=('projects',PID+'x','members',other)
        else:p=path
        data=dict(user_id=other if fault in {'duplicate','nested','prefix_suffix','wrong_id'} else UID,role='owner',created_at=BORN)
        if fault=='missing_birth':data.pop('created_at')
        if fault=='invalid_birth':data['created_at']='not a timestamp'
        tx.set(p,data)
    raw.run_transaction(break_it)
    with pytest.raises((SavedWordsUnavailable,ValueError)):census(store)


def test_retained_owners_outside_selected_prefixes_do_not_consume_the_limit(sdk):
    store,api,raw,_=sdk
    def retained(tx):
        for i in range(250):
            pid=format(i,'032x')
            tx.create(('projects',pid),dict(status='deleted',generation=1,created_at=BORN))
            tx.create(('projects',pid,'members',UID),dict(user_id=UID,role='owner',created_at=BORN))
    raw.run_transaction(retained)
    assert set(census(store)['roots'])=={PID}
    groups=[r for m,r in api.calls if m=='query' and r['structured_query'].from_[0].all_descendants]
    assert len(groups)==1 and groups[0]['structured_query'].limit==2


def test_backfill_uses_the_same_five_groups_for_hundred_active_roots(sdk):
    _,api,raw,w=sdk;pids=seed(raw,100)
    plan=w.inspect_account(UID,deadline=until())
    assert set(plan['projects'])==set(pids)
    queries=[r for m,r in api.calls if m=='query']
    assert len(queries)==6 and sum(r['structured_query'].from_[0].all_descendants for r in queries)==5
    assert not any(p[0]=='users' and len(p)>2 for p in raw._documents)


def test_inactive_owners_are_batched_and_do_not_create_eligible_contributions(sdk):
    store,api,raw,_=sdk;pids=seed(raw,100)
    def inactive(tx):
        for i,pid in enumerate(pids):
            uid='usr_'+format(i+1000,'032x')
            tx.delete(('projects',pid,'members',UID))
            tx.create(('projects',pid,'members',uid),dict(user_id=uid,role='owner',created_at=BORN))
    raw.run_transaction(inactive)
    facts=census(store)
    assert len(facts['inactive'])==100 and facts['manifests']=={UID:{}}
    assert len(api.calls)==9 and sum(m=='get' for m,_ in api.calls)==2


@pytest.mark.parametrize('race',['extra_owner','new_project','transfer','freeze'])
def test_owner_or_population_races_abort_before_any_derived_write(sdk,race):
    store,api,raw,w=sdk;plan=w.inspect_account(UID,deadline=until())
    before=deepcopy(raw._documents);other='usr_'+'c'*32
    def change(raw):
        def mutate(tx):
            if race=='freeze':tx.create(('account_erasure_locks',UID),dict(user_id=UID));return
            pid=PID
            if race=='new_project':
                pid='c'*32;tx.create(('projects',pid),dict(status='active',generation=1,created_at=BORN))
            if race=='transfer':
                tx.set(('projects',PID,'members',UID),dict(user_id=UID,role='editor',created_at=BORN))
            tx.create(('projects',pid,'members',other),dict(user_id=other,role='owner',created_at=BORN))
        raw.run_transaction(mutate)
    api.before_commit=change
    with pytest.raises(BackfillOutcomeUnknown):w.initialize_account(plan,approved=True,deadline=until())
    assert not any(p[0]=='users' and len(p)>2 for p in raw._documents)
    assert raw.get(('projects',PID)).data==before[('projects',PID)][0]


def test_missing_index_has_no_retry_or_broader_query_fallback(sdk):
    store,api,_,_=sdk;original=api.run_query;attempts=[]
    def unavailable(request,**kw):
        if request['structured_query'].from_[0].all_descendants:
            attempts.append(request);raise FailedPrecondition('Synthetic missing role index')
        return original(request,**kw)
    api.run_query=unavailable
    with pytest.raises(FailedPrecondition):census(store)
    assert len(attempts)==1


def test_driver_checks_elapsed_deadline_during_stream_consumption(sdk,monkeypatch):
    from types import SimpleNamespace
    from hosted.firestore import google_driver
    store,api,_,_=sdk;clock=[100.0];original=api.run_query
    def expired(request,**kw):
        stream=original(request,**kw)
        def delayed():
            for row in stream:
                clock[0]+=3
                yield row
        return delayed()
    monkeypatch.setattr(google_driver,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    api.run_query=expired
    with pytest.raises(BackfillUnavailable):
        store.run_transaction(lambda tx:tx.saved_word_owner_batch([PID],timeout=2),deadline=until())
    assert not [r for m,r in api.calls if m=='commit']
