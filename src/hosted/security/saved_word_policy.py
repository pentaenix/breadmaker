"""Pure inert exclusion review/candidate contract. No durable writer or transport.

One pinned identity per hosted owner; aliases do not consume additional slots.
Future persistence MUST atomically CAS the policy and idempotency receipt, and
invalidate policy-bound cache publication. Returning a candidate is not execution.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import re
import secrets
from hosted.security.saved_word_identity import WordIdentity, match_pinned_identity

MAX_EXCLUSIONS = 32
_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}')


def _token(value):
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError('WORD_POLICY_TOKEN')
    return value


@dataclass(frozen=True)
class WordPolicy:
    revision: str
    entries: tuple[WordIdentity, ...] = ()

    def __post_init__(self):
        _token(self.revision)
        if not isinstance(self.entries, tuple) or len(self.entries) > MAX_EXCLUSIONS:
            raise ValueError('WORD_EXCLUSION_LIMIT')
        if any(not isinstance(entry, WordIdentity) for entry in self.entries):
            raise ValueError('WORD_POLICY_IDENTITY_UNVERIFIED')
        if len({entry.hosted_user_id for entry in self.entries}) != len(self.entries):
            raise ValueError('WORD_POLICY_DUPLICATE_OWNER')
        if len({(entry.issuer, entry.subject) for entry in self.entries}) != len(self.entries):
            raise ValueError('WORD_POLICY_DUPLICATE_EXTERNAL_IDENTITY')


@dataclass(frozen=True)
class WordReview:
    target: WordIdentity
    payload: tuple
    stored_removal: bool = False

    def public(self):
        return dict(self.payload)


@dataclass(frozen=True)
class WordPolicyCandidate:
    policy: WordPolicy
    idempotency_key: str
    review_fingerprint: str
    replayed: bool = False


def _fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare_review(policy, target, *, action, expected_revision, idempotency_key,
                   now, enabled=False):
    if enabled is not True:
        return None
    if not isinstance(policy, WordPolicy) or not isinstance(target, WordIdentity):
        raise ValueError('WORD_POLICY_IDENTITY_UNVERIFIED')
    if action not in {'include', 'exclude'} or expected_revision != policy.revision:
        raise ValueError('WORD_POLICY_REVISION_CONFLICT')
    if type(now) is not int or now < 0:
        raise ValueError('WORD_POLICY_TIME')
    _token(idempotency_key)
    same_external = next((entry for entry in policy.entries
                          if (entry.issuer, entry.subject) == (target.issuer, target.subject)), None)
    if same_external is not None and not match_pinned_identity(same_external, target):
        raise ValueError('WORD_POLICY_EXTERNAL_IDENTITY_CHANGED')
    previous = next((entry for entry in policy.entries if entry.hosted_user_id == target.hosted_user_id), None)
    if previous is not None and previous.hosted_created_at != target.hosted_created_at:
        raise ValueError('WORD_POLICY_OWNER_INCARNATION_CHANGED')
    currently = previous is not None
    desired = action == 'exclude'
    changed = currently != desired
    if changed and desired and len(policy.entries) >= MAX_EXCLUSIONS:
        raise ValueError('WORD_EXCLUSION_LIMIT')
    after = secrets.token_hex(16) if changed else policy.revision
    values = {'schema_version': 1, 'mode': 'preview_only', 'executable': False,
        'scope': 'saved_words_only', 'action': action, 'hosted_user_id': target.hosted_user_id,
        'currently_excluded': currently, 'desired_excluded': desired, 'changes_policy': changed,
        'expected_before_revision': policy.revision, 'expected_after_revision': after,
        'mapping_fingerprint': target.fingerprint, 'idempotency_key': idempotency_key,
        'invalidate_word_totals': changed, 'prepared_at': now, 'expires_at': now+60}
    values['review_fingerprint'] = _fingerprint(values)
    return WordReview(target, tuple(values.items()))


def candidate_update(policy, current_target, review, *, applied_receipts, now, enabled=False):
    if enabled is not True:
        return None
    if not isinstance(policy, WordPolicy) or not isinstance(review, WordReview):
        raise ValueError('WORD_POLICY_REVIEW_UNVERIFIED')
    values = review.public()
    claimed = values.get('review_fingerprint')
    if _fingerprint({key: value for key, value in values.items() if key != 'review_fingerprint'}) != claimed:
        raise ValueError('WORD_POLICY_REVIEW_CHANGED')
    key = values['idempotency_key']
    if key in applied_receipts:
        if applied_receipts[key] != claimed:
            raise ValueError('WORD_POLICY_IDEMPOTENCY_CONFLICT')
        return WordPolicyCandidate(policy, key, claimed, True)
    if (type(now) is not int or not values['prepared_at'] <= now < values['expires_at']
            or policy.revision != values['expected_before_revision']):
        raise ValueError('WORD_POLICY_REVIEW_EXPIRED_OR_STALE')
    if review.stored_removal:
        # A server-owned removal review targets the exact existing filter, not
        # a currently authenticating provider account. Never add via this path.
        existing = next((entry for entry in policy.entries
                         if entry.hosted_user_id == review.target.hosted_user_id), None)
        if (values['action'] != 'include' or values['desired_excluded'] is not False
                or not match_pinned_identity(existing, review.target)
                or review.target.fingerprint != values['mapping_fingerprint']):
            raise ValueError('WORD_POLICY_STORED_ENTRY_CHANGED')
        current_target = review.target
    elif (not match_pinned_identity(review.target, current_target)
            or current_target.fingerprint != values['mapping_fingerprint']):
        raise ValueError('WORD_POLICY_MAPPING_CHANGED')
    previous = next((entry for entry in policy.entries if entry.hosted_user_id == current_target.hosted_user_id), None)
    if (previous is not None) != values['currently_excluded']:
        raise ValueError('WORD_POLICY_REVIEW_STALE')
    entries = policy.entries
    if values['changes_policy']:
        if values['desired_excluded']:
            entries = (*entries, current_target)
        else:
            entries = tuple(entry for entry in entries if entry.hosted_user_id != current_target.hosted_user_id)
    proposed = WordPolicy(values['expected_after_revision'], entries)
    return WordPolicyCandidate(proposed, key, claimed)


def prepare_stored_removal(policy, *, hosted_user_id, expected_revision,
                           idempotency_key, now, enabled=False):
    """Explicitly review removing an existing filter even if provider is gone.

    Uses the exact stored server policy entry. This is not proof of a current
    provider mapping and cannot create an exclusion or mutate any account.
    Future handler must retrieve this server-owned review, never trust a flag
    supplied in request JSON.
    """
    if enabled is not True:
        return None
    if not isinstance(policy, WordPolicy) or policy.revision != expected_revision:
        raise ValueError('WORD_POLICY_REVISION_CONFLICT')
    target = next((entry for entry in policy.entries if entry.hosted_user_id == hosted_user_id), None)
    if target is None:
        raise ValueError('WORD_POLICY_STORED_ENTRY_MISSING')
    review = prepare_review(policy, target, action='include', expected_revision=expected_revision,
        idempotency_key=idempotency_key, now=now, enabled=True)
    return replace(review, stored_removal=True)
