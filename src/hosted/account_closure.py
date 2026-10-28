"""Non-HTTP first stage of account deletion; retained data is NOT purged here.

Fences the old Breadmaker account, retires its solely owned projects, deletes
the exact provider UID, and only THEN releases its existing email grant. The
old UID admission/binding stays tombstoned: fresh signup creates a NEW account,
never an email-based merge. Backup/Trash retention and physical-purge gates stay
unchanged. A durable pending-erasure record explicitly tracks the unfinished
data stage; closing an identity is not claimed as complete physical erasure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json

from auth.identity_platform import IdentityPlatformAdmissionStore
from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.ids import identity_binding_id, require_opaque_id, require_project_id, require_user_id
from hosted.storage.contracts import ObjectAlreadyExists, ObjectGenerationConflict

MAX_PROJECTS = 128
PURGE_BLOCKERS = ('verified_independent_restorable_archive', 'provider_upload_quiescence',
                  'complete_account_reference_census', 'retention_aware_physical_executor')


def _packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _entry(row):
    return dict(row.data) if row is not None else {}


@dataclass(frozen=True)
class ClosurePlan:
    request_id: str
    confirmation: str
    user_id: str
    external_uid: str
    greenlist_digest: str
    binding_path: tuple[str, ...]
    owned_projects: tuple[str, ...]

    def public(self):
        return {'request_id': self.request_id, 'confirmation': self.confirmation,
                'owned_project_count': len(self.owned_projects),
                'preserve_allowlist': True, 'data_erasure_complete': False,
                'stage': 'close_identity_and_retain_data'}


class StagedAccountClosure:
    """Only a dedicated operator process may construct production dependencies.

    Identity adapter: get(uid)->record|None (None ONLY on explicit provider 404),
    disable(uid), revoke(uid), delete(uid). Google exceptions never become proof
    of absence. Provider side effects occur OUTSIDE retryable DB transactions.
    """
    def __init__(self, documents, identities, audit_objects, *, project, now=None, checkpoint=None):
        self.documents, self.identities, self.audit = documents, identities, audit_objects
        self.project = project
        # This is Breadmaker's account-binding namespace, not the Google JWT iss.
        self.issuer = f'identity-platform://{project}'
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.checkpoint = checkpoint or (lambda _state: None)

    def _clock(self):
        instant = self.now()
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise ValueError('ACCOUNT_ERASURE_CLOCK_INVALID')
        return instant.astimezone(timezone.utc)

    @staticmethod
    def _request(email, operation):
        normalized = IdentityPlatformAdmissionStore.normalize_greenlist_email(email)
        if not normalized:
            raise ValueError('ACCOUNT_ERASURE_EMAIL_INVALID')
        op = require_opaque_id(operation, label='erasure_operation')
        return _hash('account-erasure/v1\n'+normalized+'\n'+op), _hash(normalized)

    @staticmethod
    def _bounded(rows):
        rows = list(rows)
        if len(rows) > MAX_PROJECTS:
            raise DocumentConflict('ACCOUNT_ERASURE_SCOPE_LIMIT')
        return rows

    def _owned(self, uid):
        owned = []
        for row in self._bounded(self.documents.query_collection_group('members', field='user_id', value=uid)):
            if (len(row.path) != 4 or row.path[0] != 'projects' or row.path[2:] != ('members', uid)
                    or row.data.get('user_id') != uid or row.data.get('role') != 'owner'):
                raise DocumentConflict('ACCOUNT_ERASURE_COLLABORATION_REVIEW_REQUIRED')
            owned.append(require_project_id(row.path[1]))
        if len(set(owned)) != len(owned):
            raise DocumentConflict('ACCOUNT_ERASURE_DUPLICATE_MEMBERSHIP')
        return tuple(sorted(owned))

    @staticmethod
    def _sole_owner(tx, pid, uid):
        members = StagedAccountClosure._bounded(tx.query_collection(('projects', pid, 'members')))
        if (len(members) != 1 or members[0].path != ('projects', pid, 'members', uid)
                or members[0].data.get('role') != 'owner' or members[0].data.get('user_id') != uid):
            raise DocumentConflict('ACCOUNT_ERASURE_SHARED_PROJECT_REVIEW_REQUIRED')

    def plan(self, email, *, operation) -> ClosurePlan:
        rid, digest = self._request(email, operation)
        grant = _entry(self.documents.get(('identity_platform_greenlist', digest)))
        uid = grant.get('bound_uid')
        normalized = IdentityPlatformAdmissionStore.normalize_greenlist_email(email)
        if (grant.get('status') != 'active' or grant.get('email') != normalized
                or not isinstance(uid, str) or not 1 <= len(uid) <= 256):
            raise DocumentConflict('ACCOUNT_ERASURE_ACTIVE_BOUND_GRANT_REQUIRED')
        admission = _entry(self.documents.get(IdentityPlatformAdmissionStore.admission_path(uid)))
        if (admission.get('external_uid') != uid or admission.get('status') != 'active'
                or admission.get('greenlist_digest') != digest):
            raise DocumentConflict('ACCOUNT_ERASURE_ADMISSION_MISMATCH')
        bindings = self._bounded(self.documents.query_collection(('identity_bindings',), field='subject', value=uid))
        if len(bindings) != 1:
            raise DocumentConflict('ACCOUNT_ERASURE_EXACT_BINDING_REQUIRED')
        binding = bindings[0]
        expected = ('identity_bindings', identity_binding_id(self.issuer+'\n'+uid))
        if binding.path != expected or binding.data.get('issuer') != self.issuer:
            raise DocumentConflict('ACCOUNT_ERASURE_BINDING_MISMATCH')
        user = require_user_id(binding.data.get('user_id'))
        profile = _entry(self.documents.get(('users', user)))
        if profile.get('status', 'active') != 'active' or profile.get('user_id') != user:
            raise DocumentConflict('ACCOUNT_ERASURE_ACTIVE_PROFILE_REQUIRED')
        provider = self.identities.get(uid)
        if provider is None or getattr(provider, 'uid', None) != uid:
            raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_UID_MISMATCH')
        primary = (getattr(provider, 'email_verified', False) is True and
                   IdentityPlatformAdmissionStore.normalize_greenlist_email(getattr(provider, 'email', '')) == normalized)
        google = any(getattr(item, 'provider_id', '') == 'google.com' and getattr(item, 'uid', '')
                     and normalized.endswith('@gmail.com')
                     and IdentityPlatformAdmissionStore.normalize_greenlist_email(getattr(item, 'email', '')) == normalized
                     for item in (getattr(provider, 'provider_data', None) or []))
        if not primary and not google:
            raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_EMAIL_UNCONFIRMED')
        owned = self._owned(user)
        for pid in owned:
            def check(tx):
                if tx.get(('projects', pid)) is None:
                    raise ProjectUnavailable('PROJECT_NOT_FOUND')
                self._sole_owner(tx, pid, user)
            self.documents.run_transaction(check)
        # The human approves this exact account and ALL its owned projects, not
        # a stale list of generations. Inventory is repeated AFTER account freeze.
        confirmation = _hash('\n'.join((self.project, rid, digest, uid, user, self.issuer)))
        return ClosurePlan(rid, confirmation, user, uid, digest, binding.path, owned)

    @staticmethod
    def _public(data):
        return {'request_id': data['request_id'], 'state': data['state'],
                'account_closed': data['state'] == 'closed_retention_pending',
                'data_erasure_complete': False, 'physical_purge_authorized': False,
                'retained_project_count': len(data.get('owned_project_ids', [])),
                'retain_until': data['retain_until'], 'purge_blockers': list(PURGE_BLOCKERS)}

    def close(self, email, *, operation, confirmation):
        rid, digest = self._request(email, operation)
        path = ('account_erasures', rid)
        existing = self.documents.get(path)
        if existing is None:
            plan = self.plan(email, operation=operation)
            if confirmation != plan.confirmation:
                raise DocumentConflict('ACCOUNT_ERASURE_CONFIRMATION_MISMATCH')
            # Firestore is writable by the web runtime. An immutable intent in
            # independent recovery storage binds the HUMAN-confirmed target;
            # application-authored control rows are never execution authority.
            self._intent(rid, digest, plan.user_id, plan.external_uid, confirmation, create=True)
            stamp = self._clock()
            def freeze(tx):
                request, lock = tx.get(path), tx.get(('account_erasure_locks', plan.user_id))
                grant = _entry(tx.get(('identity_platform_greenlist', digest)))
                admission_path = IdentityPlatformAdmissionStore.admission_path(plan.external_uid)
                admission = _entry(tx.get(admission_path))
                profile = _entry(tx.get(('users', plan.user_id)))
                binding = _entry(tx.get(plan.binding_path))
                if request is not None:
                    return dict(request.data)
                if lock is not None:
                    raise DocumentConflict('ACCOUNT_ERASURE_ALREADY_REQUESTED')
                if (grant.get('status') != 'active' or grant.get('bound_uid') != plan.external_uid
                        or _hash(grant.get('email', '')) != digest
                        or admission.get('external_uid') != plan.external_uid or admission.get('status') != 'active'
                        or admission.get('greenlist_digest') != digest
                        or profile.get('user_id') != plan.user_id or profile.get('status', 'active') != 'active'
                        or binding.get('user_id') != plan.user_id or binding.get('subject') != plan.external_uid
                        or binding.get('issuer') != self.issuer):
                    raise DocumentConflict('ACCOUNT_ERASURE_SCOPE_CHANGED')
                data = {'request_id': rid, 'confirmation': plan.confirmation, 'project': self.project,
                    'user_id': plan.user_id, 'external_uid': plan.external_uid,
                    'greenlist_digest': digest, 'binding_path': list(plan.binding_path),
                    'state': 'account_fenced', 'requested_at': stamp.isoformat(),
                    'retain_until': (stamp+timedelta(days=30)).isoformat(), 'owned_project_ids': [],
                    'data_erasure_complete': False, 'physical_purge_authorized': False,
                    'purge_blockers': list(PURGE_BLOCKERS)}
                profile.update(status='erasure_pending', _bm_erasure_operation=rid,
                               generation=int(profile.get('generation', 0))+1)
                admission.update(status='erasure_pending')
                binding.update(status='erasure_pending')
                tx.set(('users', plan.user_id), profile)
                tx.set(admission_path, admission)
                tx.set(plan.binding_path, binding)
                tx.create(('account_erasure_locks', plan.user_id), {'request_id': rid})
                tx.create(path, data)
                return data
            data = self.documents.run_transaction(freeze)
            self.checkpoint('account_fenced')
        else:
            data = dict(existing.data)
        if (data.get('request_id') != rid or data.get('confirmation') != confirmation
                or data.get('greenlist_digest') != digest or data.get('project') != self.project):
            raise DocumentConflict('ACCOUNT_ERASURE_REPLAY_SCOPE_MISMATCH')
        self._intent(rid, digest, require_user_id(data['user_id']), data['external_uid'], confirmation, create=False)
        if data['state'] == 'closed_retention_pending':
            self._audit(data, create=False)
            return self._public(data)  # Never touch a subsequently admitted UID.
        uid, external = require_user_id(data['user_id']), data['external_uid']

        if data['state'] == 'account_fenced':
            owned = self._owned(uid)
            for pid in owned:
                def freeze_project(tx):
                    self._assert_request(tx, path, data)
                    root_path = ('projects', pid)
                    root = _entry(tx.get(root_path))
                    self._sole_owner(tx, pid, uid)
                    if not root:
                        raise ProjectUnavailable('PROJECT_NOT_FOUND')
                    previous = root.get('_bm_erasure_operation')
                    if previous == rid:
                        return
                    if previous or root.get('status', 'active') not in {'active', 'deleted'}:
                        raise DocumentConflict('ACCOUNT_ERASURE_PROJECT_CHANGED')
                    root.update(status='erasure_pending', _bm_erasure_operation=rid,
                                generation=int(root.get('generation', 0))+1,
                                deleted_at=root.get('deleted_at') or data['requested_at'])
                    tx.set(root_path, root)
                self.documents.run_transaction(freeze_project)
                self.checkpoint('project_fenced')
            if self._owned(uid) != owned:
                raise DocumentConflict('ACCOUNT_ERASURE_MEMBERSHIP_DRIFT')
            def projects_done(tx):
                self._assert_request(tx, path, data)
                for pid in owned:
                    if _entry(tx.get(('projects', pid))).get('_bm_erasure_operation') != rid:
                        raise DocumentConflict('ACCOUNT_ERASURE_PROJECT_CHANGED')
                updated = {**data, 'state': 'projects_fenced', 'owned_project_ids': list(owned)}
                tx.set(path, updated)
                return updated
            data = self.documents.run_transaction(projects_done)

        if data['state'] == 'projects_fenced':
            self._assert_request_read(path, data)
            provider = self.identities.get(external)
            if provider is not None:
                if getattr(provider, 'uid', None) != external:
                    raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_UID_MISMATCH')
                self.identities.disable(external)
                disabled = self.identities.get(external)
                if disabled is not None:
                    if getattr(disabled, 'uid', None) != external or getattr(disabled, 'disabled', False) is not True:
                        raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_DISABLE_UNCONFIRMED')
                    self.identities.revoke(external)
                    self.identities.delete(external)
            self.checkpoint('provider_delete_returned')
            if self.identities.get(external) is not None:
                raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_DELETE_UNCONFIRMED')
            def record_absence(tx):
                self._assert_request(tx, path, data)
                updated = {**data, 'state': 'identity_deleted',
                           'identity_absence_verified_at': self._clock().isoformat()}
                tx.set(path, updated)
                return updated
            data = self.documents.run_transaction(record_absence)
            self.checkpoint('identity_deleted')

        if data['state'] != 'identity_deleted':
            raise DocumentConflict('ACCOUNT_ERASURE_STATE_INVALID')
        self._assert_request_read(path, data)
        if self.identities.get(external) is not None:
            raise DocumentConflict('ACCOUNT_ERASURE_PROVIDER_DELETE_UNCONFIRMED')
        receipt = self._audit(data)
        self.checkpoint('audit_written')
        def complete(tx):
            self._assert_request(tx, path, data)
            grant_path = ('identity_platform_greenlist', digest)
            grant = _entry(tx.get(grant_path))
            admission_path = IdentityPlatformAdmissionStore.admission_path(external)
            admission = _entry(tx.get(admission_path))
            if (grant.get('status') != 'active' or grant.get('bound_uid') != external
                    or _hash(grant.get('email', '')) != digest
                    or admission.get('status') != 'erasure_pending' or admission.get('external_uid') != external):
                raise DocumentConflict('ACCOUNT_ERASURE_GRANT_CHANGED')
            # Preserve email, allowed providers, expiry and all operator settings.
            grant['bound_uid'] = ''
            admission['status'] = 'deleted'
            updated = {**data, 'state': 'closed_retention_pending', 'audit_receipt': receipt}
            tx.set(grant_path, grant)
            tx.set(admission_path, admission)
            tx.set(path, updated)
            return updated
        return self._public(self.documents.run_transaction(complete))

    @staticmethod
    def _assert_request(tx, path, data):
        current = _entry(tx.get(path))
        profile = _entry(tx.get(('users', data['user_id'])))
        lock = _entry(tx.get(('account_erasure_locks', data['user_id'])))
        grant = _entry(tx.get(('identity_platform_greenlist', data['greenlist_digest'])))
        admission = _entry(tx.get(IdentityPlatformAdmissionStore.admission_path(data['external_uid'])))
        binding = _entry(tx.get(tuple(data['binding_path'])))
        if (current != data or profile.get('status') != 'erasure_pending'
                or profile.get('_bm_erasure_operation') != data['request_id']
                or lock.get('request_id') != data['request_id']
                or grant.get('status') != 'active' or grant.get('bound_uid') != data['external_uid']
                or _hash(grant.get('email', '')) != data['greenlist_digest']
                or admission.get('external_uid') != data['external_uid'] or admission.get('status') != 'erasure_pending'
                or admission.get('greenlist_digest') != data['greenlist_digest']
                or binding.get('user_id') != data['user_id'] or binding.get('subject') != data['external_uid']
                or binding.get('issuer') != f"identity-platform://{data['project']}"
                or binding.get('status') != 'erasure_pending'):
            raise DocumentConflict('ACCOUNT_ERASURE_FENCE_CHANGED')

    def _assert_request_read(self, path, data):
        self.documents.run_transaction(lambda tx: self._assert_request(tx, path, data))

    def status(self, email, *, operation):
        rid, digest = self._request(email, operation)
        data = _entry(self.documents.get(('account_erasures', rid)))
        if data.get('greenlist_digest') != digest or data.get('project') != self.project:
            raise DocumentConflict('ACCOUNT_ERASURE_REQUEST_NOT_FOUND')
        self._intent(rid, digest, require_user_id(data['user_id']), data['external_uid'], data['confirmation'], create=False)
        if data['state'] == 'closed_retention_pending':
            self._audit(data, create=False)
        return self._public(data)

    def _intent(self, rid, digest, uid, external, confirmation, *, create):
        expected = _hash('\n'.join((self.project, rid, digest, external, uid, self.issuer)))
        if expected != confirmation:
            raise DocumentConflict('ACCOUNT_ERASURE_INTENT_SCOPE_MISMATCH')
        name = f'erasure-audit/v1/closures/{rid}/intent.json'
        return self._audit_record(name, {'format': 'breadmaker-account-closure-intent-v1',
            'request_id': rid, 'confirmation': confirmation, 'account_hash': _hash(uid),
            'provider_uid_hash': _hash(external), 'stage': 'identity_closure_only',
            'physical_purge_authorized': False}, create=create)

    def _audit(self, data, *, create=True):
        name = f"erasure-audit/v1/closures/{data['request_id']}/identity-deleted.json"
        record = {'format': 'breadmaker-account-closure-v1', 'request_id': data['request_id'],
                  'account_hash': _hash(data['user_id']), 'provider_uid_hash': _hash(data['external_uid']),
                  'identity_absence_verified_at': data['identity_absence_verified_at'],
                  'retained_project_count': len(data['owned_project_ids']),
                  'state': 'closed_retention_pending', 'data_erasure_complete': False}
        return self._audit_record(name, record, create=create)

    def _audit_record(self, name, record, *, create):
        raw = _packed(record)
        if create and self.audit.stat(name) is None:
            try:
                self.audit.write_chunks(name, [raw], max_bytes=8192, content_type='application/json',
                                        if_generation_match=0, metadata={'breadmaker-sha256': hashlib.sha256(raw).hexdigest()})
            except (ObjectAlreadyExists, ObjectGenerationConflict):
                pass
        meta = self.audit.stat(name)
        if meta is None or meta.size != len(raw) or meta.sha256 != hashlib.sha256(raw).hexdigest():
            raise DocumentConflict('ACCOUNT_ERASURE_AUDIT_MISMATCH')
        actual = b''.join(self.audit.read_chunks(name, max_bytes=8192, if_generation_match=meta.generation))
        if actual != raw:
            raise DocumentConflict('ACCOUNT_ERASURE_AUDIT_MISMATCH')
        return {'name': name, 'generation': meta.generation, 'sha256': meta.sha256}
