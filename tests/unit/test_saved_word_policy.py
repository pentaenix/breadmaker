from dataclasses import replace
import pytest
from hosted.security.saved_word_identity import WordIdentity
from hosted.security.saved_word_policy import WordPolicy, prepare_review, candidate_update


def identity(index=1, subject=None):
    return WordIdentity('identity-platform://synthetic', subject or 'external_'+str(index), 1700000000000,
        'usr_'+format(index, '032x'), '2023-11-14T22:13:20Z', 1700000001.0, '2023-11-14T22:13:21Z')


def review(policy, target, action='exclude', key='request_1'):
    return prepare_review(policy, target, action=action, expected_revision=policy.revision,
        idempotency_key=key, now=100, enabled=True)


def apply(policy, target, prepared, receipts=None, now=101):
    return candidate_update(policy, target, prepared, applied_receipts=receipts or {}, now=now, enabled=True)


def test_reversible_candidates_and_inert_public_preview():
    target = identity(); before = WordPolicy('initial')
    prepared = review(before, target)
    assert prepared.public()['executable'] is False and prepared.public()['mode'] == 'preview_only'
    assert 'subject' not in prepared.public() and before.entries == ()
    excluded = apply(before, target, prepared).policy
    included = apply(excluded, target, review(excluded, target, 'include', 'request_2')).policy
    assert excluded.entries == (target,) and included.entries == ()
    assert before.revision != excluded.revision != included.revision


def test_alias_noop_does_not_use_second_slot_or_change_pin():
    target = identity(); alias = identity(subject='other_alias')
    policy = WordPolicy('existing', (target,))
    prepared = review(policy, alias)
    assert not prepared.public()['changes_policy']
    assert apply(policy, alias, prepared).policy == policy


def test_33rd_owner_rejected_before_review_and_duplicate_policy_rejected():
    policy = WordPolicy('full', tuple(identity(i) for i in range(1, 33)))
    with pytest.raises(ValueError, match='WORD_EXCLUSION_LIMIT'):
        review(policy, identity(33))
    with pytest.raises(ValueError, match='DUPLICATE_OWNER'):
        WordPolicy('bad', (identity(), identity(subject='alias')))


def test_mapping_change_expiry_conflict_and_replay():
    target = identity(); policy = WordPolicy('initial'); prepared = review(policy, target)
    with pytest.raises(ValueError, match='MAPPING_CHANGED'):
        apply(policy, replace(target, binding_created_at_epoch=1700000002.0), prepared)
    with pytest.raises(ValueError, match='EXPIRED_OR_STALE'):
        apply(policy, target, prepared, now=161)
    with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
        apply(policy, target, prepared, receipts={'request_1': 'different'})
    candidate = apply(policy, target, prepared)
    replay = apply(candidate.policy, target, prepared,
        receipts={'request_1': prepared.public()['review_fingerprint']}, now=999)
    assert replay.replayed and replay.policy == candidate.policy


def test_defaultoff_does_nothing():
    assert prepare_review(None, None, action=None, expected_revision=None, idempotency_key=None, now=None) is None
    assert candidate_update(None, None, None, applied_receipts=None, now=None) is None


def test_existing_owner_noop_allowed_at_full_capacity():
    policy = WordPolicy('full', tuple(identity(i) for i in range(1, 33)))
    target = identity(1, subject='alias')
    prepared = review(policy, target)
    assert not prepared.public()['changes_policy']
    assert apply(policy, target, prepared).policy == policy


def test_recreated_owner_rejected_before_review():
    original = identity()
    policy = WordPolicy('existing', (original,))
    with pytest.raises(ValueError, match='OWNER_INCARNATION_CHANGED'):
        review(policy, replace(original, hosted_created_at='2024-01-01T00:00:00Z'))


def test_exact_expiry_is_rejected():
    target = identity(); policy = WordPolicy('initial'); prepared = review(policy, target)
    with pytest.raises(ValueError, match='EXPIRED_OR_STALE'):
        apply(policy, target, prepared, now=160)


def test_explicit_stored_removal_does_not_need_current_provider_account():
    from hosted.security.saved_word_policy import prepare_stored_removal
    original = identity(); policy = WordPolicy('existing', (original,))
    prepared = prepare_stored_removal(policy, hosted_user_id=original.hosted_user_id,
        expected_revision='existing', idempotency_key='remove_deleted', now=100, enabled=True)
    assert prepared.public()['action'] == 'include'
    assert prepared.public()['executable'] is False
    proposed = apply(policy, None, prepared).policy
    assert proposed.entries == () and policy.entries == (original,)
    with pytest.raises(ValueError, match='STORED_ENTRY_MISSING'):
        prepare_stored_removal(policy, hosted_user_id=identity(2).hosted_user_id,
            expected_revision='existing', idempotency_key='wrong_entry', now=100, enabled=True)


def test_stored_removal_rejects_changed_entry_even_if_revision_reused():
    from hosted.security.saved_word_policy import prepare_stored_removal
    original = identity(); policy = WordPolicy('existing', (original,))
    prepared = prepare_stored_removal(policy, hosted_user_id=original.hosted_user_id,
        expected_revision='existing', idempotency_key='remove_entry', now=100, enabled=True)
    changed = WordPolicy('existing', (replace(original, subject='changed_alias'),))
    with pytest.raises(ValueError, match='STORED_ENTRY_CHANGED'):
        apply(changed, None, prepared)
