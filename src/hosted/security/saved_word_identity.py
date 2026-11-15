"""Pure trusted-source identity proof for saved-word exclusions.

No provider calls, policy writer or public endpoint. The three inputs must come
from independently authenticated backend readers, NEVER caller JSON or email
joins. Missing incarnation evidence fails closed. No source composes this yet.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import json
import math
from hosted.firestore.ids import identity_binding_id, require_user_id
from hosted.firestore.contracts import HostedValidationError


def _uid(value):
    try:
        return require_user_id(value)
    except HostedValidationError:
        raise ValueError('WORD_IDENTITY_HOSTED_USER') from None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('WORD_IDENTITY_TIMESTAMP')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('WORD_IDENTITY_TIMESTAMP')
    return parsed.timestamp()


@dataclass(frozen=True)
class WordIdentity:
    issuer: str
    subject: str
    provider_created_at_ms: int
    hosted_user_id: str
    hosted_created_at: str
    binding_created_at_epoch: float
    binding_created_at: str

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def verify_identity(*, issuer, subject, binding_path, binding, provider, hosted_user):
    """Resolve provider identity to a DIFFERENT hosted ID with incarnation checks.

    `provider` is a normalized trusted Admin SDK result, not an ID token supplied
    by an analytics caller. Provider creation time is mandatory. A provider UID
    recreated after its binding cannot silently inherit the old hosted account.
    """
    if (not isinstance(issuer, str) or not issuer.startswith('identity-platform://')
            or not 1 <= len(issuer) <= 2048 or '\n' in issuer
            or not isinstance(subject, str) or not 1 <= len(subject) <= 256
            or '\n' in subject):
        raise ValueError('WORD_IDENTITY_SUBJECT')
    if tuple(binding_path) != ('identity_bindings', identity_binding_id(issuer + '\n' + subject)):
        raise ValueError('WORD_IDENTITY_BINDING_PATH')
    if (binding.get('issuer') != issuer or binding.get('subject') != subject
            or binding.get('status') != 'active'
            or provider.get('issuer') != issuer or provider.get('subject') != subject
            or provider.get('tenant_id') not in (None, '')):
        raise ValueError('WORD_IDENTITY_AUTHORITY')
    uid = _uid(binding.get('user_id'))
    if hosted_user.get('user_id') != uid or hosted_user.get('status') != 'active':
        raise ValueError('WORD_IDENTITY_HOSTED_USER')
    created = provider.get('created_at_ms')
    binding_created = binding.get('created_at_epoch')
    if (type(created) is not int or created <= 0
            or type(binding_created) not in (int, float)
            or not math.isfinite(binding_created) or binding_created <= 0
            or created > binding_created * 1000):
        raise ValueError('WORD_IDENTITY_INCARNATION_UNPROVEN')
    hosted_created = hosted_user.get('created_at')
    binding_stamp = binding.get('created_at')
    if _stamp(hosted_created) > _stamp(binding_stamp):
        raise ValueError('WORD_IDENTITY_HOSTED_INCARNATION_UNPROVEN')
    return WordIdentity(issuer, subject, created, uid, hosted_created, float(binding_created), binding_stamp)


def match_pinned_identity(pinned, current):
    """Every field is pinned; a changed mapping requires a fresh policy review."""
    return isinstance(pinned, WordIdentity) and isinstance(current, WordIdentity) and pinned == current


@dataclass(frozen=True)
class WordOwner:
    project_id: str
    hosted_user_id: str
    hosted_created_at: str
    membership_created_at: str


def verify_owner(*, project_id, owner_rows, hosted_user):
    """Require an authoritative complete owner query, capped at two matches.

    Adapter must request role=owner records, not pass filtered participants.
    Multiple owners are ambiguous, never divided or counted twice.
    """
    from hosted.firestore.ids import require_project_id
    pid = require_project_id(project_id)
    if len(owner_rows) != 1:
        raise ValueError('WORD_PROJECT_OWNER_UNPROVEN')
    path, row = owner_rows[0]
    uid = _uid(row.get('user_id'))
    if (tuple(path) != ('projects', pid, 'members', uid) or row.get('role') != 'owner'
            or hosted_user.get('user_id') != uid or hosted_user.get('status') != 'active'):
        raise ValueError('WORD_PROJECT_OWNER_UNPROVEN')
    created = hosted_user.get('created_at')
    member_created = row.get('created_at')
    if _stamp(created) > _stamp(member_created):
        raise ValueError('WORD_OWNER_INCARNATION_UNPROVEN')
    return WordOwner(pid, uid, created, member_created)
