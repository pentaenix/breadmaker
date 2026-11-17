"""Backend account-owned word state in the canonical transaction.

Below journal/coverage, above the lifecycle fence. Backfill proofs live under the
owner's users subtree; canonical content and historical Undo images stay intact.
No general counter, client construction, activation or content read occurs here.
"""
from copy import deepcopy
import hashlib
import json
import time
from hosted.action_journal import _BufferedTransaction
from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.security.saved_word_identity import verify_owner
from hosted.security.saved_word_projection import DEFINITION, verified_contribution, Contribution
from hosted.security import account_word_state as records


def _canonical(data):
    return None if data is None else {k: v for k, v in data.items() if k != '_bm_read_bundle'}


def proof_path(uid, user_created, pid, project_created, bid):
    key = hashlib.sha256(json.dumps([require_user_id(uid), user_created,
        require_project_id(pid), project_created, str(bid)], separators=(',', ':')).encode()).hexdigest()
    return ('users', uid, 'word_block_counts', key)


def proof_count(raw, *, uid_created, pid, project_created, bid, generation):
    if (type(raw) is not dict or set(raw) != {'schema_version', 'definition', 'user_created_at',
            'project_id', 'project_created_at', 'block_id', 'source_generation', 'words'}
            or type(raw['schema_version']) is not int or raw['schema_version'] != 1
            or raw['definition'] != DEFINITION or raw['user_created_at'] != uid_created
            or raw['project_id'] != pid or raw['project_created_at'] != project_created
            or raw['block_id'] != bid or type(generation) is not int
            or type(raw['source_generation']) is not int or raw['source_generation'] != generation):
        return None
    try: return Contribution(raw['words'])
    except ValueError: return None


class _Transaction(_BufferedTransaction):
    def __init__(self, tx):
        super().__init__(tx); self.metadata = {}

    def get_many(self, paths): return [self.get(p) for p in paths]

    def saved_word_metadata(self, pid, *, limit, timeout):
        if self.pending: raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        rows = self.tx.saved_word_metadata(pid, limit=limit, timeout=timeout)
        self.metadata[(pid, limit)] = deepcopy(rows)
        return rows

    def saved_word_project(self, pid, *, timeout):
        if self.pending: raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.saved_word_project(pid, timeout=timeout)

    def count_collection(self, path):
        if self.pending: raise DocumentConflict('WORD_METADATA_QUERY_AFTER_WRITE')
        return self.tx.count_collection(path)


class AccountWordGuard:
    def __init__(self, store, *, enabled=False, resource_budget=None):
        self.store, self.enabled = store, enabled is True
        self.resource_budget = resource_budget

    def __getattr__(self, name): return getattr(self.store, name)

    def run_transaction(self, fn, *, max_attempts=5):
        if not self.enabled: return self.store.run_transaction(fn, max_attempts=max_attempts)
        if self.resource_budget is None:
            return self.store.run_transaction(lambda tx: self._execute(tx, fn), max_attempts=max_attempts)
        from .metric_projection_budget import transaction_scope
        def bounded(tx):
            try:adapter = self.resource_budget.projection()
            except Exception:adapter = None
            with transaction_scope(adapter):return self._execute(tx, fn)
        return self.store.run_transaction(bounded, max_attempts=max_attempts)

    @staticmethod
    def _operational(path):
        return (len(path) == 4 and path[0] == 'users' and
            (path[2] == 'word_block_counts' or path[2:] == ('metrics', 'saved_words_v1')
             or path[2] == 'metric_jobs' and path[3].startswith('word_')))

    def _new_project(self, tx, buffered, pid, root):
        empty = dict(created_at=root['created_at'], revision=root['generation'],
                     blocks=0, known_blocks=0, known_words=0)
        deadline = time.monotonic() + 2
        try:
            rows = buffered.metadata.get((pid, 1001))
            if rows is None: rows = tx.saved_word_metadata(pid, limit=1001, timeout=2)
            if len(rows) > 1000: return records.project_entry(**empty, initialized=False)
            found = {}
            for row in rows:
                if len(row.path) != 4 or row.path[:3] != ('projects', pid, 'blocks') or row.path in found:
                    raise ValueError('ACCOUNT_WORD_METADATA_INVALID')
                found[row.path] = dict(row.data)
            for path, data in buffered.pending.items():
                if len(path) == 4 and path[:3] == ('projects', pid, 'blocks'):
                    if data is None: found.pop(path, None)
                    else: found[path] = data
            if len(found) > 1000 or time.monotonic() >= deadline:
                return records.project_entry(**empty, initialized=False)
            counts = [verified_contribution(row) for row in found.values()]
            return records.project_entry(created_at=root['created_at'], revision=root['generation'],
                blocks=len(found), known_blocks=sum(c is not None for c in counts),
                known_words=sum(c.words for c in counts if c is not None), initialized=True)
        except Exception:
            return records.project_entry(**empty, initialized=False)

    def _execute(self, tx, fn):
        buffered = _Transaction(tx); result = fn(buffered)
        if any(self._operational(p) for p in buffered.pending):
            raise DocumentConflict('ACCOUNT_WORD_DIRECT_WRITE_FORBIDDEN')
        from .metric_projection_budget import current_scope,ProjectionTransaction,ProjectionBudgetUnavailable,write_envelope
        from .saved_word_guard import LEDGER_COLLECTION
        scope=current_scope();updates={}
        if scope is None:
            updates=self._project(tx,buffered)
        elif not scope.failed:
            try:
                metered=ProjectionTransaction(tx,scope);buffered.tx=metered
                updates=self._project(metered,buffered)
                scope.reserve(writes=len(updates),write_bytes=sum(write_envelope(p,d) for p,d in updates.items()))
                for path,data in updates.items():
                    scope.envelope('account',path[1],data['user_created_at'],4*1024**2)
            except ProjectionBudgetUnavailable:scope.failed=True
        if scope is not None and scope.failed:
            updates={}
            # Coverage updates were buffered above; a later account-budget
            # failure must discard them together with account contributions.
            buffered.pending={p:d for p,d in buffered.pending.items() if not p or p[0]!=LEDGER_COLLECTION}
        # Mandatory project-root erasure cleanup is not optional reporting.
        erased=[p[1] for p,d in buffered.pending.items() if len(p)==2 and p[0]=='projects' and d is None]
        if scope is not None and scope.failed:
            for pid in erased:tx.delete((LEDGER_COLLECTION,pid))
        # Lifecycle checks and provider commit remain outside this buffer.
        for p, data in buffered.pending.items():
            if data is None: tx.delete(p)
            elif buffered.before[p] is None: tx.create(p, data)
            else: tx.set(p, data)
        for p, data in updates.items(): tx.set(p, data)
        return result

    def _project(self, tx, buffered):
        changed = {require_project_id(p[1]) for p, data in buffered.pending.items()
            if len(p) >= 2 and p[0] == 'projects' and _canonical(data) != _canonical(buffered.before[p])
            and (len(p) == 2 or len(p) == 4 and p[2] in {'blocks', 'chapters', 'members'})}
        accounts, original = {}, {}
        def account(uid):
            if uid not in accounts:
                profile = buffered.get(('users', require_user_id(uid)))
                if profile is None or profile.data.get('status') != 'active':
                    raise DocumentConflict('ACCOUNT_WORD_OWNER_UNAVAILABLE')
                born = profile.data['created_at']; old = tx.get(records.path(uid))
                original[uid] = dict(old.data) if old else None
                newly_created = ('users', uid) in buffered.pending and buffered.before[('users', uid)] is None
                if newly_created and old is not None: raise DocumentConflict('ACCOUNT_WORD_USER_ID_REUSE')
                accounts[uid] = (records.parse(dict(old.data), uid, born) if old else
                    records.state(uid, born, initialized=newly_created))
            return accounts[uid]
        for p, data in buffered.pending.items():
            if len(p) == 2 and p[0] == 'users' and data is not None and buffered.before[p] is None:
                account(p[1])
        for pid in sorted(changed):
            root_path = ('projects', pid)
            after_doc = buffered.get(root_path)
            after = dict(after_doc.data) if after_doc else None
            before = (buffered.before[root_path] if root_path in buffered.before else after)
            raw_owners = tx.saved_word_owners(pid, timeout=2)
            owners_before = {row.path: dict(row.data) for row in raw_owners}
            owners_after = dict(owners_before)
            for p, data in buffered.pending.items():
                if len(p) == 4 and p[:3] == ('projects', pid, 'members'):
                    owners_after.pop(p, None)
                    if data is not None and data.get('role') == 'owner': owners_after[p] = data
            if len(owners_before) == 1:
                old_uid = next(iter(owners_before.values())).get('user_id')
                if after is None or after.get('status') != 'active' or old_uid not in [r.get('user_id') for r in owners_after.values()]:
                    old_profile = buffered.get(('users', require_user_id(old_uid)))
                    if old_profile is not None and old_profile.data.get('status') == 'active':
                        record = account(old_uid); record['projects'].pop(pid, None)
            if not after or after.get('status') != 'active': continue
            if len(owners_after) != 1:
                raise DocumentConflict('ACCOUNT_WORD_OWNER_UNVERIFIED')
            uid = next(iter(owners_after.values())).get('user_id')
            profile = buffered.get(('users', require_user_id(uid)))
            # Counting does not change collaborator access. Inactive/missing
            # owners are excluded by the account selector; retain their records.
            # The enclosing lifecycle fence still decides write authorization.
            if profile is None or profile.data.get('status') != 'active': continue
            owner = verify_owner(project_id=pid, owner_rows=list(owners_after.items()), hosted_user=dict(profile.data))
            record = account(owner.hosted_user_id)
            entry = record['projects'].get(pid)
            if entry is None and len(record['projects'])>=records.MAX_PROJECTS:
                # Reporting bounds never impose a new product write/access cap.
                # Preserve existing proofs and require a later complete census.
                record['initialized']=False
                continue
            if before is None:
                if entry is not None: raise DocumentConflict('ACCOUNT_WORD_PROJECT_ID_REUSE')
                entry = self._new_project(tx, buffered, pid, after)
            elif (entry is None or entry['project_created_at'] != after.get('created_at')
                    or entry['canonical_revision'] != before.get('generation')):
                entry = records.project_entry(created_at=after['created_at'], revision=after['generation'],
                    blocks=0, known_blocks=0, known_words=0, initialized=False)
            else:
                entry = dict(entry)
                changes = [(p, buffered.before[p], data) for p, data in buffered.pending.items()
                    if len(p) == 4 and p[:3] == ('projects', pid, 'blocks')
                    and _canonical(buffered.before[p]) != _canonical(data)]
                if changes and after['generation'] != before['generation'] + 1:
                    entry['initialized'] = False
                if entry['initialized']:
                    blocks, known, words = entry['blocks'], entry['known_blocks'], entry['known_words']
                    for p, old_block, new_block in changes:
                        counts = []
                        for row in (old_block, new_block):
                            count = verified_contribution(row) if row is not None else None
                            if row is not None and count is None:
                                saved = tx.get(proof_path(uid, record['user_created_at'], pid, after['created_at'], p[-1]))
                                count = proof_count(dict(saved.data) if saved else None,
                                    uid_created=record['user_created_at'], pid=pid, project_created=after['created_at'],
                                    bid=p[-1], generation=row.get('generation'))
                            counts.append(count)
                        if old_block is not None and counts[0] is None and known == blocks:
                            entry['initialized'] = False; break
                        blocks += int(new_block is not None) - int(old_block is not None)
                        known += int(counts[1] is not None) - int(counts[0] is not None)
                        words += (counts[1].words if counts[1] else 0) - (counts[0].words if counts[0] else 0)
                    if entry['initialized']:
                        if not (0<=known<=blocks<=records.MAX_BLOCKS and 0<=words<=records.MAX_WORDS
                                and (known>0 or words==0)):
                            # Reporting bounds do not restrict valid canonical
                            # saves. Keep the prior bounded evidence, mark it
                            # unknown and let normal authorization still decide.
                            entry['initialized']=False
                        else:entry.update(blocks=blocks, known_blocks=known, known_words=words)
                entry['canonical_revision'] = after['generation']
                entry = records.project_entry(created_at=entry['project_created_at'], revision=entry['canonical_revision'],
                    blocks=entry['blocks'], known_blocks=entry['known_blocks'], known_words=entry['known_words'],
                    initialized=entry['initialized'])
            record['projects'][pid] = entry
        updates = {}
        for uid, record in accounts.items():
            updated = records.state(uid, record['user_created_at'], projects=record['projects'],
                                    initialized=record['initialized'])
            if updated != original[uid]: updates[records.path(uid)] = updated
        return updates
