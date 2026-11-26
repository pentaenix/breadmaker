from pathlib import Path

import pytest
from fastapi import HTTPException

from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedStructuredRepository, HostedUserRepository
from hosted.usage_consent import FIELD, NOTICE_SHA256, NOTICE_VERSION, UsageConsent

UID = 'usr_' + '1' * 32
OTHER = 'usr_' + '2' * 32
STAMP = '2026-11-26T12:00:00Z'


@pytest.fixture
def setup():
    store = MemoryDocumentStore()
    for uid in (UID, OTHER):
        store.run_transaction(lambda tx: tx.create(('users', uid), dict(
            user_id=uid, status='active', generation=1, created_at=STAMP, display_name='Synthetic')))
    fenced = AccountLifecycleDocumentStore(store, actor=lambda: UID)
    return store, fenced, UsageConsent(fenced, clock=lambda: STAMP)


def body(choice, revision=0):
    return dict(choice=choice, revision=revision, notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)


def test_shipped_copy_pins_the_same_notice_as_the_server():
    client = (Path(__file__).parents[2] / 'static/js/features/account/usage-consent.js').read_text()
    assert f"version:'{NOTICE_VERSION}'" in client
    assert f"sha256:'{NOTICE_SHA256}'" in client
    assert 'notice_version:usageConsentNotice.version,notice_sha256:usageConsentNotice.sha256' in client


@pytest.mark.parametrize('choice', [True, False])
def test_both_choices_persist_across_instances_and_profile_updates(setup, choice):
    store, fenced, service = setup
    assert service.get(UID)['allowed'] is False and service.get(UID)['needs_choice']
    result = service.save(UID, body(choice))
    assert result['choice'] is choice and result['allowed'] is choice and not result['needs_choice']
    record = store.get(('users', UID)).data[FIELD]
    assert record == dict(choice=choice, revision=1, notice_version=NOTICE_VERSION,
                          notice_sha256=NOTICE_SHA256, updated_at=STAMP)
    assert UsageConsent(fenced).get(UID) == result
    assert service.get(OTHER)['choice'] is None
    users = HostedUserRepository(HostedStructuredRepository(fenced))
    users.update_self(UID, {'display_name': 'Changed'}, expected_generation=2, operation_id='profile_change')
    assert service.get(UID) == result
    assert store.get(('users', UID)).data['generation'] == 3
    # Private consent record must never leak through the public profile.
    from hosted.runtime_services import _clean_public
    assert FIELD not in _clean_public(users.get_self(UID))


def test_network_retry_and_old_tab_cannot_undo_new_refusal(setup):
    store, _, service = setup
    on = service.save(UID, body(True))
    assert service.save(UID, body(True)) == on
    assert store.get(('users', UID)).data['generation'] == 2
    off = service.save(UID, body(False, 1))
    with pytest.raises(DocumentConflict):
        service.save(UID, body(True))
    assert service.get(UID) == off


@pytest.mark.parametrize('bad', [None, [], {}, body(1), body('false'), body(None),
                               body(True, True), body(True, -1), body(True, 2**53),
                               {**body(True), 'user_id': OTHER},
                               {**body(True), 'updated_at': STAMP}])
def test_untrusted_fields_and_malformed_choices_never_write(setup, bad):
    store, _, service = setup
    with pytest.raises(HTTPException) as error:
        service.save(UID, bad)
    assert error.value.status_code == 400
    assert FIELD not in store.get(('users', UID)).data


@pytest.mark.parametrize('field', ['notice_version', 'notice_sha256'])
def test_old_tab_cannot_accept_new_notice_or_purpose(setup, field):
    store, _, service = setup
    with pytest.raises(HTTPException) as error:
        service.save(UID, {**body(True), field: 'older-copy'})
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'USAGE_NOTICE_CHANGED'
    assert FIELD not in store.get(('users', UID)).data


@pytest.mark.parametrize('freeze', ['status', 'marker', 'lock', 'absent'])
def test_frozen_or_missing_account_cannot_read_or_change_choice(setup, freeze):
    store, _, service = setup
    def change(tx):
        if freeze == 'absent':
            tx.delete(('users', UID))
        elif freeze == 'lock':
            tx.create(('account_erasure_locks', UID), {'user_id': UID})
        else:
            data = dict(tx.get(('users', UID)).data)
            data['status' if freeze == 'status' else '_bm_erasure_operation'] = 'erasure_pending'
            tx.set(('users', UID), data)
    store.run_transaction(change)
    for action in (lambda: service.get(UID), lambda: service.save(UID, body(True))):
        with pytest.raises(HostedAuthorizationError):
            action()


@pytest.mark.parametrize('change', ['notice_version', 'notice_sha256'])
def test_new_scope_turns_permission_off_until_another_choice(setup, change):
    store, _, service = setup
    service.save(UID, body(True))
    def old(tx):
        data = dict(tx.get(('users', UID)).data)
        data[FIELD] = {**data[FIELD], change: 'old-notice'}
        tx.set(('users', UID), data)
    store.run_transaction(old)
    assert service.get(UID) == dict(choice=None, allowed=False, needs_choice=True,
                                    revision=1, notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)
    assert service.save(UID, body(False, 1))['choice'] is False
