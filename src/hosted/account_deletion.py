from __future__ import annotations

"""M9C3: exhaustive *memory-only* account erasure preflight.

This deliberately cannot delete anything. A user's author-owned projects,
collaborations, provider bindings, and scoped objects cannot be treated as one
unconditional delete: project ownership/retention and provider identity have
separate authorities. Live Google inventory and account erasure remain disabled.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore

MAX_ACCOUNT_ITEMS = 25000


@dataclass(frozen=True)
class AccountDescendantProof:
    """Trusted, injected read-only classification; never deletion authority.

    Private record schemas stay in their owning backend. The public planner
    accepts only an exact owner/incarnation proof and retains explicit blockers.
    """
    user_id: str
    user_created_at: str
    category: str

    def __post_init__(self):
        from hosted.security.saved_word_identity import _stamp
        require_user_id(self.user_id);_stamp(self.user_created_at)
        if self.category not in {'metric_data','metric_history'}:
            raise ValueError('HOSTED_ACCOUNT_CLASSIFICATION_INVALID')


@dataclass(frozen=True)
class AccountDeletionPreview:
    user_id: str
    owned_projects: tuple[str, ...]
    collaborative_projects: tuple[str, ...]
    missing_project_roots: tuple[str, ...]
    identity_binding_paths: tuple[tuple[str, ...], ...]
    account_document_paths: tuple[tuple[str, ...], ...]
    account_object_names: tuple[str, ...]
    independent_recovery_archive_count: int
    blockers: tuple[str, ...]
    fingerprint: str
    exhaustive_for_memory: bool = True
    physical_deletion_authorized: bool = False
    retained_purge_audit_paths: tuple[tuple[str, ...], ...] = ()
    staged_import_projects: tuple[str, ...] = ()
    staged_import_document_paths: tuple[tuple[str, ...], ...] = ()
    staged_import_object_names: tuple[str, ...] = ()
    saved_word_ledger_paths: tuple[tuple[str, ...], ...] = ()
    saved_word_policy_reference_paths: tuple[tuple[str, ...], ...] = ()
    account_metric_data_paths: tuple[tuple[str, ...], ...] = ()
    account_metric_history_paths: tuple[tuple[str, ...], ...] = ()
    account_metric_classification_enabled: bool = False

    def public(self) -> dict:
        """Suppress account/provider identifiers and object paths in UI summaries."""
        result = {
            'owned_project_count': len(self.owned_projects),
            'collaboration_count': len(self.collaborative_projects),
            'identity_binding_count': len(self.identity_binding_paths),
            'account_document_count': len(self.account_document_paths),
            'account_object_count': len(self.account_object_names),
            'independent_recovery_archive_count': self.independent_recovery_archive_count,
            'blockers': list(self.blockers),
            'fingerprint': self.fingerprint,
            'physical_deletion_authorized': False,
            'retained_purge_audit_count': len(self.retained_purge_audit_paths),
            'staged_import_project_count': len(self.staged_import_projects),
            'staged_import_document_count': len(self.staged_import_document_paths),
            'staged_import_object_count': len(self.staged_import_object_names),
            'saved_word_ledger_count': len(self.saved_word_ledger_paths),
            'saved_word_policy_reference_count': len(self.saved_word_policy_reference_paths),
        }
        if self.account_metric_classification_enabled:
            result.update(account_metric_data_count=len(self.account_metric_data_paths),
                          account_metric_history_count=len(self.account_metric_history_paths))
        return result


class HostedAccountDeletionPlanner:
    """Read-only, authenticated planner for exact in-memory drivers ONLY.

    Google Firestore group queries do not prove absence of orphaned parents or
    provider-user bindings; no ordinary production adapter is accepted here.
    Memory scans take one document lock, then perform an optimistic revision
    fence after object inventory (which is *still not* a cloud-wide snapshot).
    """

    def __init__(self, documents: MemoryDocumentStore, content: MemoryObjectStore,
                 recovery: MemoryObjectStore, actor_user_id: Callable[[], str], *,
                 saved_word_retention_enabled: bool = False, account_descendant_classifier=None):
        if (type(documents) is not MemoryDocumentStore or type(content) is not MemoryObjectStore
                or type(recovery) is not MemoryObjectStore or content is recovery
                or not callable(actor_user_id)):
            raise ValueError('HOSTED_ACCOUNT_DISCOVERY_MEMORY_ONLY')
        self.documents, self.content, self.recovery = documents, content, recovery
        self.actor_user_id = actor_user_id
        self.saved_word_retention_enabled = saved_word_retention_enabled is True
        if account_descendant_classifier is not None and not callable(account_descendant_classifier):
            raise ValueError('HOSTED_ACCOUNT_CLASSIFIER_INVALID')
        if account_descendant_classifier is not None and not self.saved_word_retention_enabled:
            raise ValueError('HOSTED_ACCOUNT_LEGACY_RETENTION_REVIEW_REQUIRED')
        self.account_descendant_classifier=account_descendant_classifier

    def _snapshot(self) -> tuple[tuple[tuple[str, ...], dict, int], ...]:
        with self.documents._lock:
            return tuple(sorted((path, dict(record[0]), int(record[1]))
                                for path, record in self.documents._documents.items()))

    def preview(self, user_id: str) -> AccountDeletionPreview:
        uid = require_user_id(user_id)
        if require_user_id(self.actor_user_id()) != uid:
            raise HostedAuthorizationError('HOSTED_ACCOUNT_SELF_ONLY')
        before = self._snapshot()
        by_path = {path: (data, revision) for path, data, revision in before}
        if ('users', uid) not in by_path:
            raise DocumentConflict('HOSTED_ACCOUNT_PROFILE_MISSING')
        owned, collaborating, missing = set(), set(), set()
        bindings, account_docs, unknown, audit_docs = set(), set(), set(), set()
        metric_data,metric_history=set(),set()
        from hosted.package_import_ownership import import_job_scope, import_child_owned
        jobs = {}
        for path, data, _ in before:
            if len(path) == 4 and path[:3] == ('users', uid, 'package_imports'):
                try:
                    pid = import_job_scope(path, data, lambda target: by_path.get(target, (None,))[0])
                    jobs[path] = (data, pid)
                except (DocumentConflict, ValueError):
                    unknown.add(path)
        for path, data, _ in before:
            if path[:2] == ('users', uid):
                account_docs.add(path)
                if len(path) > 2:
                    job = jobs.get(path[:4])
                    known = job is not None and (path == path[:4] or import_child_owned(path, data, path[:4], job[0]))
                    if not known and self.account_descendant_classifier is not None:
                        from copy import deepcopy
                        def lookup(target):
                            row=by_path.get(tuple(target))
                            return deepcopy(row[0]) if row is not None else None
                        try:
                            proof=self.account_descendant_classifier(path,deepcopy(data),
                                profile=deepcopy(by_path[('users',uid)][0]),lookup=lookup)
                            if (type(proof) is AccountDescendantProof and proof.category in {'metric_data','metric_history'} and proof.user_id==uid
                                    and proof.user_created_at==by_path[('users',uid)][0].get('created_at')):
                                (metric_data if proof.category=='metric_data' else metric_history).add(path)
                                known=True
                        except Exception:
                            # Unknown/malformed/orphaned records remain blockers.
                            pass
                    # Unknown descendants remain a hard completeness blocker.
                    if not known:
                        unknown.add(path)
            elif len(path) == 4 and path[:1] == ('projects',) and path[2:] == ('members', uid):
                pid = require_project_id(path[1])
                role = data.get('role')
                if role == 'owner':
                    owned.add(pid)
                elif role == 'editor':
                    collaborating.add(pid)
                else:
                    unknown.add(path)
                if ('projects', pid) not in by_path:
                    missing.add(pid)
                account_docs.add(path)
            elif (len(path) == 2 and path[0] in ('purge_preparations', 'purge_sandbox_executions')
                  and data.get('owner_user_id') == uid):
                # Retain the independently scoped post-purge evidence. It is
                # never an account-data deletion candidate. Invalid receipts
                # are unknown, not silently blessed as audit evidence.
                if (data.get('project_id') == path[1]
                        and data.get('physical_purge_authorized') is False
                        and data.get('state') in ('prepared_read_only', 'sandbox_armed',
                                                  'sandbox_running', 'sandbox_completed')):
                    audit_docs.add(path)
                else:
                    unknown.add(path)
                    account_docs.add(path)
            elif path == ('account_sandbox_closures', uid):
                # Durable sandbox receipt is preserved outside user profile
                # cleanup, never mistaken for an unknown account document.
                if (data.get('user_id') == uid
                        and data.get('production_account_deletion_authorized') is False
                        and data.get('external_identity_revocation_required') is True):
                    audit_docs.add(path)
                else:
                    unknown.add(path)
                    account_docs.add(path)
            elif len(path) == 2 and path[0] == 'identity_bindings' and data.get('user_id') == uid:
                bindings.add(path)
                account_docs.add(path)
            elif len(path) == 2 and path[0] == 'hosted_import_operations' and data.get('user_id') == uid:
                account_docs.add(path)
            elif (data.get('user_id') == uid or data.get('owner_user_id') == uid
                  or data.get('actor_user_id') == uid
                  or (len(path) >= 2 and path[0] not in ('projects', 'users') and path[1] == uid)):
                # Unknown account-dependent data is not silently omitted.
                unknown.add(path)
                account_docs.add(path)
        if owned & collaborating:
            raise DocumentConflict('HOSTED_ACCOUNT_MEMBERSHIP_INCONSISTENT')
        staged = {pid for _, pid in jobs.values() if ('projects', pid) not in by_path}
        from hosted.security.saved_word_retention import POLICY_PATH, LEDGER_COLLECTION, policy_references_user
        word_ledgers, word_policies, orphan_ledgers = set(), set(), set()
        policy_unverified = False
        if self.saved_word_retention_enabled:
            # Also retain attribution through existing purge audits after roots and
            # memberships disappear. Sidecars are not account-delete candidates.
            audited_projects = {by_path[path][0].get('project_id') for path in audit_docs}
            word_ledgers = {path for path in by_path if len(path) == 2
                            and path[0] == LEDGER_COLLECTION and path[1] in owned | staged | audited_projects}
            # With neither root nor membership/audit attribution, an orphan sidecar
            # cannot safely be assigned to a user. Conservatively block erasure
            # claims until separately reviewed; this grants no deletion authority.
            membership_projects = {p[1] for p in by_path if len(p) >= 4
                                   and p[0] == 'projects' and p[2] == 'members'}
            orphan_ledgers = {path for path in by_path if len(path) == 2 and path[0] == LEDGER_COLLECTION
                              and ('projects', path[1]) not in by_path
                              and path[1] not in membership_projects}
            word_ledgers |= orphan_ledgers
            word_policies = set()
            policy_unverified = False
            if POLICY_PATH in by_path:
                try:
                    if policy_references_user(by_path[POLICY_PATH][0], uid):
                        word_policies.add(POLICY_PATH)
                except (ValueError, TypeError, RuntimeError):
                    policy_unverified = True
                    word_policies.add(POLICY_PATH)
        staged_docs = {path for path, _, _ in before if len(path) >= 2 and path[0] == 'projects' and path[1] in staged}
        def staged_objects():
            return tuple(sorted((o.name, o.generation, o.sha256) for pid in staged
                                for o in self.content.list_prefix(f'projects/{pid}/')))
        staged_names = staged_objects()
        # Even empty prefixes retain their binding until separately reviewed;
        # never erase the reservation and strand an in-flight provider upload.
        names = tuple(sorted((o.name, o.generation, o.sha256)
                             for o in self.content.list_prefix(f'users/{uid}/')))
        recoveries = []
        for pid in sorted(owned):
            recoveries.extend((o.name, o.generation, o.sha256) for o in
                              self.recovery.list_prefix(f'recovery/v1/projects/{pid}/'))
        if len(before) + len(names) + len(staged_names) + len(recoveries) > MAX_ACCOUNT_ITEMS:
            raise ValueError('HOSTED_ACCOUNT_INVENTORY_TOO_LARGE')
        # All Firestore data and account-owned objects must remain stable while
        # inventorying; the preview is never considered an execution authority.
        if self._snapshot() != before:
            raise DocumentConflict('HOSTED_ACCOUNT_CONCURRENT_MUTATION')
        if tuple(sorted((o.name, o.generation, o.sha256) for o in
                        self.content.list_prefix(f'users/{uid}/'))) != names:
            raise DocumentConflict('HOSTED_ACCOUNT_CONCURRENT_MUTATION')
        if staged_objects() != staged_names:
            raise DocumentConflict('HOSTED_ACCOUNT_CONCURRENT_MUTATION')
        renewed_archives = []
        for pid in sorted(owned):
            renewed_archives.extend((o.name, o.generation, o.sha256) for o in
                                    self.recovery.list_prefix(f'recovery/v1/projects/{pid}/'))
        if sorted(renewed_archives) != sorted(recoveries):
            raise DocumentConflict('HOSTED_ACCOUNT_CONCURRENT_MUTATION')
        blockers = ['HOSTED_ACCOUNT_LIVE_PROVIDER_NOT_ATTESTED',
                    'HOSTED_ACCOUNT_PROVIDER_IDENTITY_REVOCATION_REQUIRED']
        if owned:
            blockers.append('HOSTED_ACCOUNT_OWNED_PROJECT_RETENTION_AND_PURGE_REQUIRED')
        if staged:
            blockers.append('HOSTED_ACCOUNT_STAGED_IMPORT_RETENTION_AND_PURGE_REQUIRED')
        if collaborating:
            blockers.append('HOSTED_ACCOUNT_COLLABORATION_DETACH_REQUIRED')
        if missing:
            blockers.append('HOSTED_ACCOUNT_ORPHANED_PROJECT_MEMBERSHIP')
        if unknown:
            blockers.append('HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES')
        if metric_data:
            blockers.append('HOSTED_ACCOUNT_METRIC_DATA_ERASURE_REVIEW_REQUIRED')
        if metric_history:
            blockers.append('HOSTED_ACCOUNT_METRIC_HISTORY_RETENTION_REVIEW_REQUIRED')
        if word_ledgers:
            blockers.append('HOSTED_ACCOUNT_SAVED_WORD_LEDGER_CLEANUP_REQUIRED')
        if orphan_ledgers:
            blockers.append('HOSTED_ACCOUNT_SAVED_WORD_ORPHAN_LEDGER_UNATTRIBUTED')
        if word_policies:
            blockers.append('HOSTED_ACCOUNT_SAVED_WORD_POLICY_REVIEW_REQUIRED')
        if policy_unverified:
            blockers.append('HOSTED_ACCOUNT_SAVED_WORD_POLICY_UNVERIFIED')
        # Include all observed revisions, not just paths. No PII leaves the
        # operator boundary; callers see a hash and aggregate counts.
        relevant_paths = account_docs | audit_docs | staged_docs | word_ledgers | word_policies | {('projects', pid) for pid in owned | collaborating
                                          if ('projects', pid) in by_path}
        canonical = {
            'uid': uid,
            'docs': [[list(path), by_path[path][1]] for path in sorted(relevant_paths)],
            'owned': sorted(owned), 'collaborations': sorted(collaborating),
            'account_objects': names, 'recovery': sorted(recoveries),
            'staged_import_projects': sorted(staged), 'staged_import_objects': staged_names,
            'retained_audits': [list(p) for p in sorted(audit_docs)],
        }
        if self.account_descendant_classifier is not None:
            canonical.update(account_metric_data=[list(p) for p in sorted(metric_data)],
                             account_metric_history=[list(p) for p in sorted(metric_history)])
        fingerprint = hashlib.sha256(json.dumps(canonical,sort_keys=True,
                      separators=(',',':')).encode('utf-8')).hexdigest()
        return AccountDeletionPreview(
            uid, tuple(sorted(owned)), tuple(sorted(collaborating)),
            tuple(sorted(missing)), tuple(sorted(bindings)), tuple(sorted(account_docs)),
            tuple(name for name, _, _ in names), len(recoveries),
            tuple(sorted(blockers)), fingerprint, retained_purge_audit_paths=tuple(sorted(audit_docs)),
            staged_import_projects=tuple(sorted(staged)), staged_import_document_paths=tuple(sorted(staged_docs)),
            staged_import_object_names=tuple(name for name, _, _ in staged_names),
            saved_word_ledger_paths=tuple(sorted(word_ledgers)),
            saved_word_policy_reference_paths=tuple(sorted(word_policies)),
            account_metric_data_paths=tuple(sorted(metric_data)),
            account_metric_history_paths=tuple(sorted(metric_history)),
            account_metric_classification_enabled=self.account_descendant_classifier is not None,
        )
