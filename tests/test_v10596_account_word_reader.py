from copy import deepcopy
import pytest
from hosted.security import account_word_state as records
from hosted.security.account_word_reader import GoogleFirestoreAccountWordsProducer, SOURCE
from hosted.security.saved_word_reader import SavedWordsUnavailable
from tests.unit.test_saved_word_reader import Client, PolicyReader, NOW, UID, PID, records as legacy_records

BORN='2023-11-14T22:13:20Z'


def rows():
    data=legacy_records();data['projects/'+PID]['created_at']=BORN
    data['users/'+UID+'/metrics/saved_words_v1']=records.state(UID,BORN,initialized=True,projects={
        PID:records.project_entry(created_at=BORN,revision=2,blocks=1,known_blocks=1,known_words=7,initialized=True)})
    return data


def producer(client, policy=None, account=None):
    return GoogleFirestoreAccountWordsProducer(client,policy_reader=policy or PolicyReader(),
        account_reader=account or (lambda **kw: (_ for _ in ()).throw(AssertionError('unneeded provider read'))),
        issuer='identity-platform://synthetic',enabled=True,clock=lambda:NOW,monotonic=lambda:100)


def test_numeric_source_is_account_and_shared_editors_do_not_double_count():
    data=rows();other='usr_'+'b'*32
    data['users/'+other]=dict(user_id=other,status='active',created_at=BORN)
    data['users/'+other+'/metrics/saved_words_v1']=records.state(other,BORN,initialized=True)
    data['projects/'+PID+'/members/'+other]=dict(user_id=other,role='editor',created_at=BORN)
    client=Client(data);result=producer(client).collect(deadline=130)
    assert result['source']==SOURCE and result['schema_version']==2
    assert result['metrics']['all_saved_words']==7  # old project ledger says 3
    assert result['coverage']['eligible_accounts']==2 and result['coverage']['eligible_projects']==1
    assert all(not call[1].startswith('admin_saved_word_coverage') for call in client.calls)
    assert UID not in str(result) and PID not in str(result) and 'NEVER' not in str(result)
    assert all(call[-1]['retry'] is None and call[-1]['timeout']<=4 for call in client.calls)


@pytest.mark.parametrize('kind',['inactive','deleted_root','freeze'])
def test_account_removal_or_freeze_removes_contribution_while_retaining_all_metric_data(kind):
    data=rows()
    if kind=='inactive':data['users/'+UID]['status']='erasure_pending'
    elif kind=='deleted_root':data.pop('users/'+UID)
    else:data['account_erasure_locks/'+UID]={'user_id':UID}
    before=deepcopy(data);result=producer(Client(data)).collect(deadline=130)
    assert result['metrics']==dict(all_saved_words=0,included_saved_words=0,excluded_saved_words=0)
    assert result['coverage']['eligible_accounts']==0 and result['coverage']['eligible_projects']==0
    assert data==before and 'users/'+UID+'/metrics/saved_words_v1' in data


@pytest.mark.parametrize('kind',['missing','partial','stale','foreign_birth'])
def test_incomplete_account_coverage_is_never_partial_zero(kind):
    data=rows();path='users/'+UID+'/metrics/saved_words_v1'
    if kind=='missing':data.pop(path)
    elif kind=='partial':data[path]=records.state(UID,BORN)
    elif kind=='stale':data['projects/'+PID]['generation']+=1
    else:data['users/'+UID]['created_at']='2023-11-14T22:13:21Z'
    result=producer(Client(data)).collect(deadline=130)
    assert set(result['metrics'].values())=={None}
    assert result['coverage']['unknown_accounts']==1


def test_verified_empty_account_has_complete_zero_without_manuscript_access():
    data={'users/'+UID:dict(user_id=UID,status='active',created_at=BORN),
          'users/'+UID+'/metrics/saved_words_v1':records.state(UID,BORN,initialized=True)}
    result=producer(Client(data)).collect(deadline=130)
    assert result['metrics']['all_saved_words']==0 and result['coverage']['completeness']=='complete'


def test_deleted_policy_target_needs_no_provider_cleanup_to_remove_contribution():
    from hosted.security.saved_word_identity import WordIdentity
    from hosted.security.saved_word_policy import WordPolicy
    pin=WordIdentity('identity-platform://synthetic','synthetic',1,UID,BORN,2.0,BORN)
    class Policy(PolicyReader):
        def load_policy(self,*,deadline):return WordPolicy('policy_1',(pin,))
    data=rows();data.pop('users/'+UID)
    result=producer(Client(data),Policy()).collect(deadline=130)
    assert result['metrics']['all_saved_words']==0


def test_test_exclusion_uses_current_verified_owner_and_account_count():
    from hosted.security.saved_word_identity import WordIdentity
    from hosted.security.saved_word_policy import WordPolicy
    from hosted.firestore.ids import identity_binding_id
    issuer='identity-platform://synthetic';subject='external'
    pin=WordIdentity(issuer,subject,1700000000000,UID,BORN,1700000001.0,'2023-11-14T22:13:21Z')
    class Policy(PolicyReader):
        def load_policy(self,*,deadline):return WordPolicy('policy_1',(pin,))
    data=rows();data['identity_bindings/'+identity_binding_id(issuer+'\n'+subject)]=dict(
        issuer=issuer,subject=subject,user_id=UID,status='active',created_at_epoch=1700000001.0,
        created_at='2023-11-14T22:13:21Z')
    result=producer(Client(data),Policy(),lambda **kw:dict(issuer=issuer,subject=subject,
        created_at_ms=1700000000000,tenant_id=None)).collect(deadline=130)
    assert result['metrics']==dict(all_saved_words=7,included_saved_words=0,excluded_saved_words=7)


def test_changed_eligibility_during_collection_cannot_publish_retained_total():
    client=Client(rows());original=client.document
    def document(path):
        reference=original(path)
        if path.endswith('/metrics/saved_words_v1'):
            old=reference.get
            def get(**kw):
                result=old(**kw);client.rows['account_erasure_locks/'+UID]={'user_id':UID};return result
            reference.get=get
        return reference
    client.document=document
    with pytest.raises(SavedWordsUnavailable,match='ELIGIBILITY_CHANGED'):producer(client).collect(deadline=130)


def test_policy_revision_change_and_disabled_producer_cannot_publish_totals():
    assert GoogleFirestoreAccountWordsProducer().collect(deadline=None) is None
    result=producer(Client(rows()),PolicyReader('policy_2')).collect(deadline=130)
    assert set(result['metrics'].values())=={None}
