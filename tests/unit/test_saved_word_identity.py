import pytest
from hosted.security.saved_word_identity import verify_identity, match_pinned_identity
from hosted.firestore.ids import identity_binding_id

ISSUER = 'identity-platform://synthetic-project'
SUBJECT = 'external-not-hosted'
UID = 'usr_' + 'a'*32


def inputs():
    return dict(issuer=ISSUER, subject=SUBJECT,
        binding_path=('identity_bindings', identity_binding_id(ISSUER+'\n'+SUBJECT)),
        binding={'issuer': ISSUER, 'subject': SUBJECT, 'user_id': UID,
                 'status': 'active', 'created_at_epoch': 1700000001.0,
                 'created_at': '2023-11-14T22:13:21+00:00'},
        provider={'issuer': ISSUER, 'subject': SUBJECT, 'created_at_ms': 1700000000000},
        hosted_user={'user_id': UID, 'status': 'active', 'created_at': '2023-11-14T22:13:20+00:00'})


def test_verified_binding_is_not_uid_equality_or_email_join():
    data = inputs()
    proof = verify_identity(**data)
    assert proof.hosted_user_id == UID and proof.hosted_user_id != proof.subject
    data['binding']['email'] = 'changed@example.invalid'
    data['provider']['email'] = 'other@example.invalid'
    assert verify_identity(**data) == proof
    assert len(proof.fingerprint) == 64


@pytest.mark.parametrize('mutation', [
    lambda d: d['provider'].pop('created_at_ms'),
    lambda d: d['provider'].update(created_at_ms=1700000002000),
    lambda d: d['provider'].update(tenant_id='unconfigured-tenant'),
    lambda d: d['binding'].update(user_id='raw-provider-uid'),
    lambda d: d['hosted_user'].update(created_at='2023-11-15T00:00:00Z'),
    lambda d: d.update(binding_path=('identity_bindings', 'wrong')),
])
def test_missing_reused_or_mismatched_identity_fails_closed(mutation):
    data = inputs(); mutation(data)
    with pytest.raises(ValueError):
        verify_identity(**data)


def test_policy_pin_detects_binding_relink_and_incarnation_changes():
    data = inputs(); before = verify_identity(**data)
    data['binding']['created_at_epoch'] += 1
    after = verify_identity(**data)
    assert not match_pinned_identity(before, after)
    assert match_pinned_identity(before, before)


def test_unique_owner_not_collaborator_attribution():
    from hosted.security.saved_word_identity import verify_owner
    pid = 'a'*32
    user = inputs()['hosted_user']
    owner = (('projects', pid, 'members', UID), {'user_id': UID, 'role': 'owner', 'created_at': '2023-11-14T22:13:21+00:00'})
    assert verify_owner(project_id=pid, owner_rows=[owner], hosted_user=user).hosted_user_id == UID
    for rows in [[], [owner, owner], [(owner[0], {'user_id': UID, 'role': 'editor'})]]:
        with pytest.raises(ValueError):
            verify_owner(project_id=pid, owner_rows=rows, hosted_user=user)


def test_owner_membership_cannot_attach_to_recreated_hosted_user():
    from hosted.security.saved_word_identity import verify_owner
    pid = 'a'*32
    owner = (('projects', pid, 'members', UID), {'user_id': UID, 'role': 'owner',
        'created_at': '2023-11-14T22:13:21+00:00'})
    user = {**inputs()['hosted_user'], 'created_at': '2024-01-01T00:00:00Z'}
    with pytest.raises(ValueError, match='WORD_OWNER_INCARNATION_UNPROVEN'):
        verify_owner(project_id=pid, owner_rows=[owner], hosted_user=user)
