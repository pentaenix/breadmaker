"""Uncomposed bounded metadata collector with injected trusted policy/accounts.

No SDK client, credential, policy or provider callback is constructed here. Runtime
must not enable this until its expanded metadata access is explicitly approved.
"""
from datetime import datetime, timezone
import time
from hosted.firestore.ids import require_project_id, require_user_id, identity_binding_id
from hosted.firestore.contracts import HostedValidationError
from hosted.security.saved_word_policy import WordPolicy
from hosted.security.saved_word_identity import verify_identity, verify_owner
from hosted.security.saved_word_aggregate import aggregate_verified_snapshot, WordContext

ROOT_FIELDS = ('status', 'generation')
LEDGER_FIELDS = ('schema_version', 'definition', 'state', 'canonical_revision', 'blocks', 'words')
OWNER_FIELDS = ('user_id', 'role', 'created_at')
USER_FIELDS = ('user_id', 'status', 'created_at')
BINDING_FIELDS = ('issuer', 'subject', 'user_id', 'status', 'created_at_epoch', 'created_at')


class SavedWordsUnavailable(ValueError):
    pass


class GoogleFirestoreSavedWordsProducer:
    def __init__(self, client=None, *, policy_reader=None, account_reader=None,
                 issuer=None, enabled=False, monotonic=time.monotonic, clock=None):
        self.client, self.policy_reader, self.account_reader = client, policy_reader, account_reader
        self.issuer, self.enabled = issuer, enabled is True
        self.monotonic = monotonic
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _remaining(self, deadline):
        remaining = deadline-self.monotonic()
        if not 0 < remaining <= 60:
            raise SavedWordsUnavailable('WORD_COLLECTION_DEADLINE')
        return min(4, remaining)

    def _get(self, path, fields, deadline):
        snapshot = self.client.document('/'.join(path)).get(
            field_paths=list(fields), timeout=self._remaining(deadline), retry=None)
        self._remaining(deadline)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        return {key: data[key] for key in fields if key in data}

    def _query(self, path, fields, field, value, limit, deadline):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.client.collection('/'.join(path)).where(filter=FieldFilter(field, '==', value))
        stream = query.select(list(fields)).limit(limit).stream(
            timeout=self._remaining(deadline), retry=None)
        rows = []
        try:
            for snapshot in stream:
                self._remaining(deadline)
                if len(rows) >= limit:
                    raise SavedWordsUnavailable('WORD_COLLECTION_PROVIDER_LIMIT')
                data = snapshot.to_dict() or {}
                rows.append((tuple(snapshot.reference.path.split('/')),
                             {key: data[key] for key in fields if key in data}))
        finally:
            close = getattr(stream, 'close', None)
            if close is not None:
                close()
        self._remaining(deadline)
        return rows

    def _roots(self, deadline):
        rows = self._query(('projects',), ROOT_FIELDS, 'status', 'active', 101, deadline)
        if len(rows) > 100:
            raise SavedWordsUnavailable('WORD_COLLECTION_PROJECT_LIMIT')
        result = {}
        for path, data in rows:
            if len(path) != 2 or path[0] != 'projects':
                raise SavedWordsUnavailable('WORD_COLLECTION_ROOT_PATH')
            pid = require_project_id(path[1])
            if (pid in result or data.get('status') != 'active'
                    or type(data.get('generation')) is not int or not 1 <= data['generation'] < 2**53):
                raise SavedWordsUnavailable('WORD_COLLECTION_ROOT_INVALID')
            result[pid] = data
        return result

    def collect(self, *, deadline):
        if not self.enabled:
            return None
        if (self.client is None or self.policy_reader is None or not callable(self.account_reader)
                or not isinstance(self.issuer, str) or not self.issuer.startswith('identity-platform://')):
            raise SavedWordsUnavailable('WORD_COLLECTION_NOT_CONFIGURED')
        self._remaining(deadline)
        start = self.clock()
        try:
            policy = self.policy_reader.load_policy(deadline=deadline)
            self._remaining(deadline)
            if not isinstance(policy, WordPolicy):
                raise SavedWordsUnavailable('WORD_POLICY_UNVERIFIED')
            before = WordContext(policy.revision)
            roots = self._roots(deadline)
            users = {}
            accounts = {}
            def user(uid):
                uid = require_user_id(uid)
                if uid not in users:
                    users[uid] = self._get(('users', uid), USER_FIELDS, deadline)
                return users[uid]
            current_identities = []
            for pinned in policy.entries:
                self._remaining(deadline)
                if (pinned.issuer != self.issuer or not isinstance(pinned.subject, str)
                        or not 1 <= len(pinned.subject) <= 256 or '\n' in pinned.subject):
                    raise SavedWordsUnavailable('WORD_IDENTITY_ISSUER_UNSUPPORTED')
                key = (pinned.issuer, pinned.subject)
                if key not in accounts:
                    accounts[key] = self.account_reader(issuer=pinned.issuer,
                        subject=pinned.subject, deadline=deadline)
                self._remaining(deadline)
                provider = accounts[key]
                if not isinstance(provider, dict) or set(provider) != {'issuer', 'subject', 'created_at_ms', 'tenant_id'}:
                    raise SavedWordsUnavailable('WORD_IDENTITY_PROVIDER_UNVERIFIED')
                path = ('identity_bindings', identity_binding_id(pinned.issuer+'\n'+pinned.subject))
                binding = self._get(path, BINDING_FIELDS, deadline)
                if binding is None:
                    raise SavedWordsUnavailable('WORD_IDENTITY_BINDING_MISSING')
                profile = user(binding.get('user_id'))
                if profile is None:
                    raise SavedWordsUnavailable('WORD_IDENTITY_USER_MISSING')
                current_identities.append(verify_identity(issuer=pinned.issuer, subject=pinned.subject,
                    binding_path=path, binding=binding, provider=provider, hosted_user=profile))
            projects = []
            for pid, root in sorted(roots.items()):
                ledger = self._get(('admin_saved_word_coverage', pid), LEDGER_FIELDS, deadline)
                owner_rows = self._query(('projects', pid, 'members'), OWNER_FIELDS,
                                         'role', 'owner', 2, deadline)
                owner = None
                if len(owner_rows) == 1:
                    try:
                        uid = require_user_id(owner_rows[0][1].get('user_id'))
                    except HostedValidationError:
                        uid = None
                    # Provider transport failures propagate, never become zero.
                    profile = user(uid) if uid is not None else None
                    try:
                        owner = verify_owner(project_id=pid, owner_rows=owner_rows,
                                             hosted_user=profile or {})
                    except (ValueError, HostedValidationError):
                        owner = None
                projects.append({'project_id': pid, **root, 'owner': owner, 'ledger': ledger})
            if self._roots(deadline) != roots:
                raise SavedWordsUnavailable('WORD_COLLECTION_ROOTS_CHANGED')
            current_revision = self.policy_reader.read_policy_revision(deadline=deadline)
            self._remaining(deadline)
            return aggregate_verified_snapshot(projects=projects, pinned_exclusions=policy.entries,
                current_identities=current_identities, before_context=before,
                after_context=WordContext(current_revision), start=start, end=self.clock(), enabled=True)
        except SavedWordsUnavailable:
            raise
        except Exception:
            # Never return provider exception bodies, document paths or identities.
            raise SavedWordsUnavailable('WORD_COLLECTION_UNAVAILABLE') from None
