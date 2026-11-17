from copy import deepcopy
import pytest
from hosted.security import account_word_state as state

UID = 'usr_' + 'a' * 32
OTHER = 'usr_' + 'b' * 32
PID = 'c' * 32
BORN = '2026-11-16T00:00:00Z'


def project(words=5):
    return state.project_entry(created_at=BORN, revision=3, blocks=2,
        known_blocks=2, known_words=words, initialized=True)


def account(uid=UID, words=5):
    record = state.state(uid, BORN, projects={PID: project(words)}, initialized=True)
    return {'profile': {'user_id': uid, 'created_at': BORN, 'status': 'active'},
            'words': record, 'owned_projects': {PID: (BORN, 3)}}


def test_missing_initialization_and_partial_data_are_unknown_never_zero():
    assert state.state(UID, BORN)['saved_words'] is None
    assert state.state(UID, BORN, initialized=True)['saved_words'] == 0
    row = project(); row['known_blocks'] = 1
    assert state.state(UID, BORN, projects={PID: row}, initialized=True)['saved_words'] is None


@pytest.mark.parametrize('mutation', ['counter', 'birth', 'duplicate', 'stale', 'bad_source'])
def test_account_source_and_manifest_are_strict(mutation):
    row = account()
    if mutation == 'counter': row['words']['saved_words'] = 99
    elif mutation == 'birth': row['profile']['created_at'] = '2026-11-17T00:00:00Z'
    elif mutation == 'duplicate':
        with pytest.raises(ValueError, match='DUPLICATE_ACCOUNT'): state.sum_accounts([row, row])
        return
    elif mutation == 'stale': row['owned_projects'][PID] = (BORN, 4)
    else: row['words']['projects'] = []
    with pytest.raises(ValueError): state.sum_accounts([row])


def test_inactive_account_contribution_disappears_without_global_repair_or_deletion():
    row = account(); before = deepcopy(row['words'])
    assert state.sum_accounts([row])['all_saved_words'] == 5
    row['profile']['status'] = 'erasure_pending'
    assert state.sum_accounts([row]) == dict(all_saved_words=0, included_saved_words=0, excluded_saved_words=0)
    assert row['words'] == before


def test_account_test_filter_and_unique_project_attribution():
    row = account()
    assert state.sum_accounts([row], excluded={UID}) == dict(all_saved_words=5, included_saved_words=0, excluded_saved_words=5)
    with pytest.raises(ValueError, match='DUPLICATE_PROJECT'): state.sum_accounts([row, account(OTHER)])


def test_boolean_one_cannot_impersonate_a_verified_numeric_counter():
    row=account(words=1);row['words']['saved_words']=True
    with pytest.raises(ValueError):state.sum_accounts([row])
