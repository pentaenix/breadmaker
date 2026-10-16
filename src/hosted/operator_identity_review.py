from __future__ import annotations

"""M9C3 operator identity reconciliation, strictly observational.

Firestore identity bindings and Identity Platform user records are different
security authorities. A missing Firestore binding must never be interpreted as
provider revocation; only independently authenticated Admin SDK evidence in a
later privileged job can establish it. No revoke/update/delete methods exist.
"""

import hashlib
import json
from dataclasses import dataclass

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import identity_binding_id, require_user_id

MAX_IDENTITY_BINDINGS = 128


@dataclass(frozen=True)
class IdentityClosureReview:
    user_id: str
    bindings: tuple[tuple[str, str, str], ...]  # issuer, subject, binding key
    fingerprint: str
    external_provider_evidence_required: bool = True
    identity_revocation_complete: bool = False
    physical_deletion_authorized: bool = False

    def public(self) -> dict:
        return {'binding_count': len(self.bindings), 'fingerprint': self.fingerprint,
                'external_provider_evidence_required': True,
                'identity_revocation_complete': False,
                'physical_deletion_authorized': False}


class HostedIdentityClosureInspector:
    """Enumerate known bindings; never equate them with external provider users.

    A full, orphan-proof provider account census is not possible from this
    registered Firestore collection alone. A live provider-side audit is a
    required gate, including after Firestore has been destroyed.
    """

    def __init__(self, documents) -> None:
        self.documents = documents

    def inspect(self, user_id: str) -> IdentityClosureReview:
        uid = require_user_id(user_id)
        records = self.documents.query_collection(('identity_bindings',), field='user_id', value=uid)
        if len(records) > MAX_IDENTITY_BINDINGS:
            raise DocumentConflict('HOSTED_OPERATOR_IDENTITY_BUDGET_EXCEEDED')
        bindings = set()
        revisions = []
        for row in records:
            issuer, subject = row.data.get('issuer'), row.data.get('subject')
            if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
                raise DocumentConflict('HOSTED_OPERATOR_IDENTITY_DATA_INCOMPLETE')
            expected = identity_binding_id(issuer + '\n' + subject)
            path = tuple(row.path)
            if path != ('identity_bindings', expected) or row.data.get('user_id') != uid:
                raise DocumentConflict('HOSTED_OPERATOR_IDENTITY_BINDING_MISMATCH')
            if (issuer, subject, expected) in bindings:
                raise DocumentConflict('HOSTED_OPERATOR_IDENTITY_BINDING_DUPLICATE')
            bindings.add((issuer, subject, expected))
            revisions.append((expected, str(row.revision)))
        canonical = (uid, sorted(bindings), sorted(revisions))
        return IdentityClosureReview(uid, tuple(sorted(bindings)),
            hashlib.sha256(json.dumps(canonical, separators=(',', ':')).encode()).hexdigest())
