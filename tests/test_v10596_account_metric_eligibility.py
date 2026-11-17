from copy import deepcopy
import pytest
from hosted.security.account_metric_eligibility import GoogleFirestoreAccountEligibility
from hosted.security.saved_word_reader import SavedWordsUnavailable
from tests.test_v10596_account_word_reader import rows,UID,PID,BORN
from tests.unit.test_saved_word_reader import Client
from tests.test_v10596_account_word_transport import sdk


def eligibility(client,**kw):
    return GoogleFirestoreAccountEligibility(client,enabled=True,monotonic=lambda:100,**kw)


def test_eligibility_survives_dated_word_changes_but_never_account_closure():
    client=Client(rows());reader=eligibility(client);before=reader.snapshot(deadline=130)
    client.rows['projects/'+PID]['generation']+=1
    client.rows['users/'+UID+'/metrics/saved_words_v1']['saved_words']=99
    edited=reader.snapshot(deadline=130)
    assert before['fingerprint']==edited['fingerprint'] and before['roots']!=edited['roots']
    client.rows['users/'+UID]['status']='erasure_pending'
    closed=reader.snapshot(deadline=130)
    assert closed['fingerprint']!=edited['fingerprint'] and not closed['profiles']
    assert all('/metrics/' not in call[1] for call in client.calls)


@pytest.mark.parametrize('change',['root_removal','new_project','owner_transfer','freeze','new_account'])
def test_cached_eligibility_changes_for_each_population_or_owner_change(change):
    client=Client(rows());reader=eligibility(client);before=reader.snapshot(deadline=130)['fingerprint']
    other='usr_'+'b'*32
    if change=='root_removal':client.rows['projects/'+PID]['status']='deleted'
    elif change=='new_project':
        new='c'*32;client.rows['projects/'+new]=dict(status='active',generation=1,created_at=BORN)
        client.rows['projects/'+new+'/members/'+UID]=dict(user_id=UID,role='owner',created_at=BORN)
    elif change=='freeze':client.rows['account_erasure_locks/'+UID]={'user_id':UID}
    else:
        client.rows['users/'+other]=dict(user_id=other,status='active',created_at=BORN)
        if change=='owner_transfer':
            client.rows['projects/'+PID+'/members/'+UID]['role']='editor'
            client.rows['projects/'+PID+'/members/'+other]=dict(user_id=other,role='owner',created_at=BORN)
    assert reader.snapshot(deadline=130)['fingerprint']!=before


def test_real_sdk_eligibility_queries_are_part_of_the_given_transaction(sdk):
    store,api,raw,_=sdk
    tx=store.client.transaction(max_attempts=1);tx._id=b'synthetic-word-transaction'
    reader=GoogleFirestoreAccountEligibility(store.client,transaction=tx,enabled=True)
    import time
    data=reader.snapshot(deadline=time.monotonic()+20)
    assert len(data['fingerprint'])==64
    assert all(request['transaction']==tx.id for name,request in api.calls if name in {'get','query'})


def test_disabled_eligibility_never_contacts_a_provider():
    client=Client(rows())
    with pytest.raises(SavedWordsUnavailable):GoogleFirestoreAccountEligibility(client).snapshot(deadline=130)
    assert client.calls==[]
