"""Uncomposed transaction guard for saved-word coverage.

Install BELOW ActionDocumentStore so Undo/Redo and journal fallback pass through
this boundary. Never install inside a journal callback: the ledger is operational
state, not a user-authorized project mutation or an Undo record.

Explicit new-root composition can establish coverage from bounded metadata at
publication. Existing manuscripts require separate initialization. Mutations may
maintain complete coverage, but never promote unknown.
"""
from hosted.action_journal import _BufferedTransaction
from hosted.firestore.ids import require_project_id
from hosted.firestore.contracts import DocumentConflict
from hosted.security.saved_word_projection import (
    DEFINITION, Projection, prepare_replacement, verified_contribution,
)
import time

MAX_INITIAL_BLOCKS = 1000
NEW_PROJECT_METADATA_SECONDS = 2.0


def _ledger(revision, projection=None):
    complete = projection is not None and projection.words is not None
    return {'schema_version': 1, 'definition': DEFINITION,
            'state': 'complete' if complete else 'unknown',
            'canonical_revision': revision,
            'blocks': projection.blocks if complete else None,
            'words': projection.words if complete else None}


def _maintain(previous, before_root, after_root, changes):
    revision = (after_root or {}).get('generation')
    if type(revision) is not int or not 1 <= revision < 2**53:
        return _ledger(None)
    try:
        if (set(previous) != {'schema_version', 'definition', 'state', 'canonical_revision', 'blocks', 'words'}
                or previous['schema_version'] != 1 or type(previous['schema_version']) is not int
                or previous['definition'] != DEFINITION or previous['state'] != 'complete'
                or not before_root or not after_root or after_root.get('status') != 'active'
                or before_root.get('status') != 'active'
                or type(before_root.get('generation')) is not int
                or previous['canonical_revision'] != before_root['generation']):
            return _ledger(revision)
        current = Projection(previous['canonical_revision'], previous['blocks'], previous['words'])
        if not changes and revision == current.revision:
            return _ledger(revision, current)
        if revision != current.revision + 1:
            return _ledger(revision)
        removed, added = [], []
        for before, after in changes:
            for row, destination in ((before, removed), (after, added)):
                if row is not None:
                    item = verified_contribution(row)
                    if item is None:
                        return _ledger(revision)
                    destination.append(item)
        result = prepare_replacement(current, expected_revision=current.revision,
            next_revision=revision, removed=removed, added=added, enabled=True)
        return _ledger(revision, result)
    except (ValueError, TypeError, KeyError):
        return _ledger(revision)


LEDGER_COLLECTION = 'admin_saved_word_coverage'


class _CoverageTransaction(_BufferedTransaction):
    def get_many(self, paths):
        return [self.get(path) for path in paths]

    def count_collection(self, path):
        if any(p[:-1] == tuple(path) for p in self.pending):
            raise ValueError('WORD_COVERAGE_COUNT_AFTER_WRITE')
        return self.tx.count_collection(path)


class SavedWordCoverageGuard:
    def __init__(self, store, *, enabled=False, initialize_new_projects=False):
        self.store = store
        self.enabled = enabled is True
        self.initialize_new_projects = initialize_new_projects is True

    def _new_project_ledger(self, tx, buffered, pid, root):
        """Only root creation: bounded projected metadata, never old content.

        Imports/restores stage blocks before publishing authority. Read those
        generation-bound contributions in the same publication transaction.
        Missing/oversized/stale evidence produces unknown, never partial zero.
        Ordinary existing-project writes cannot enter this path.
        """
        revision = root.get('generation')
        unknown = _ledger(revision if type(revision) is int else None)
        if root.get('status') != 'active' or type(revision) is not int or revision != 1:
            return unknown
        reader = getattr(tx, 'saved_word_metadata', None)
        if not callable(reader):
            return unknown
        deadline = time.monotonic() + NEW_PROJECT_METADATA_SECONDS
        try:
            rows = reader(pid, limit=MAX_INITIAL_BLOCKS + 1,
                          timeout=NEW_PROJECT_METADATA_SECONDS)
            if time.monotonic() >= deadline or len(rows) > MAX_INITIAL_BLOCKS:
                return unknown
            projected = {}
            for row in rows:
                if (len(row.path) != 4 or row.path[:3] != ('projects', pid, 'blocks')
                        or row.path in projected):
                    return unknown
                projected[row.path] = dict(row.data)
            # Include canonical block changes buffered in this very transaction.
            for path, data in buffered.pending.items():
                if len(path) == 4 and path[:3] == ('projects', pid, 'blocks'):
                    if data is None:
                        projected.pop(path, None)
                    else:
                        projected[path] = data
            if len(projected) > MAX_INITIAL_BLOCKS:
                return unknown
            contributions = [verified_contribution(row) for row in projected.values()]
            if time.monotonic() >= deadline or any(item is None for item in contributions):
                return unknown
            return _ledger(revision, Projection(revision, len(contributions),
                sum(item.words for item in contributions)))
        except Exception:
            # Counting cannot turn missing/unsupported metadata into authority.
            # The enclosing provider transaction still owns commit/conflicts and
            # the account lifecycle fence; no write is performed here.
            return unknown

    def __getattr__(self, name):
        return getattr(self.store, name)

    def run_transaction(self, fn, *, max_attempts=5):
        if not self.enabled:
            return self.store.run_transaction(fn, max_attempts=max_attempts)

        return self.store.run_transaction(lambda tx: self._execute_transaction(tx, fn),
                                          max_attempts=max_attempts)

    def _execute_transaction(self, tx, fn):
        """Internal guard boundary, also used by exact-memory purge rehearsal.

        Caller must already own the enclosing transaction. This grants no new
        authorization and never constructs a provider or commits independently.
        """
        if not self.enabled:
            return fn(tx)
        buffered = _CoverageTransaction(tx)
        result = fn(buffered)
        affected = set()
        for path, after in buffered.pending.items():
            if path and path[0] == LEDGER_COLLECTION:
                raise ValueError('WORD_COVERAGE_LEDGER_WRITE_FORBIDDEN')
            if not path or path[0] != 'projects':
                continue
            if len(path) == 2 or (len(path) == 4 and path[2] in {'blocks', 'chapters'}):
                # Only a canonical state change invalidates: receipt-only
                # replays and byte-identical writes do not change coverage.
                if after != buffered.before[path]:
                    affected.add(require_project_id(path[1]))
        from .metric_projection_budget import current_scope,ProjectionTransaction,ProjectionBudgetUnavailable,write_envelope
        scope=current_scope();ledgers={}
        if scope is None:ledgers=self._project_ledgers(tx,buffered,affected)
        elif not scope.failed:
            try:
                ledgers=self._project_ledgers(ProjectionTransaction(tx,scope),buffered,affected)
                scope.reserve(writes=len(ledgers),write_bytes=sum(write_envelope(p,d) for p,d in ledgers.items()))
                for path,data in ledgers.items():
                    if data is not None:
                        previous=ProjectionTransaction(tx,scope).get(path)
                        scope.retain(path,data,dict(previous.data) if previous is not None else None)
            except ProjectionBudgetUnavailable:scope.failed=True
        if scope is not None and scope.failed:ledgers={}
        # All reads, including coverage/root reads, precede provider writes.
        for path, data in buffered.pending.items():
            if data is None:
                tx.delete(path)
            elif buffered.before[path] is None:
                tx.create(path, data)
            else:
                tx.set(path, data)
        for path, data in ledgers.items():
            if data is None:
                tx.delete(path)
            else:
                tx.set(path, data)
        return result

    def _project_ledgers(self,tx,buffered,affected):
        ledgers = {}
        for pid in sorted(affected):
            path = (LEDGER_COLLECTION, pid)
            if path in buffered.pending:
                raise ValueError('WORD_COVERAGE_LEDGER_WRITE_FORBIDDEN')
            old = tx.get(path)
            # Do not create operational metadata for existing projects just
            # because an unrelated save occurred. Initialization is separate.
            if old is None:
                root_path = ('projects', pid)
                if (self.initialize_new_projects and root_path in buffered.pending
                        and buffered.before[root_path] is None
                        and buffered.pending[root_path] is not None):
                    ledgers[path] = self._new_project_ledger(
                        tx, buffered, pid, buffered.pending[root_path])
                continue
            root_path = ('projects', pid)
            if (self.initialize_new_projects and root_path in buffered.pending
                    and buffered.before[root_path] is None
                    and buffered.pending[root_path] is not None):
                # A reused root ID must not overwrite an unattributed old ledger.
                raise DocumentConflict('WORD_COVERAGE_PROJECT_ID_REUSE')
            after_doc = buffered.get(root_path)
            after_root = dict(after_doc.data) if after_doc else None
            if root_path in buffered.pending and buffered.pending[root_path] is None:
                # Coverage is project-derived state outside the canonical
                # subtree. Physical root erasure must erase it atomically,
                # never leave a retained unknown ledger behind.
                ledgers[path] = None
                continue
            before_root = (buffered.before[root_path] if root_path in buffered.before
                           else after_root)
            changes = [(buffered.before[p], data) for p, data in buffered.pending.items()
                       if len(p) == 4 and p[:3] == ('projects', pid, 'blocks')
                       and buffered.before[p] != data]
            ledgers[path] = _maintain(dict(old.data), before_root, after_root, changes)
        return ledgers



    def initialize_metadata(self, project_id, *, expected_revision, deadline):
        """Explicit metadata-only initializer; no caller/runtime composes this.

        Complete only when every current block has a valid revision-bound count.
        Bounded query never decrypts/reads text. Legacy missing metadata stays
        unknown. A transaction conflict retries the same expected revision and
        fails closed; it cannot silently initialize a newer manuscript.
        """
        if not self.enabled:
            return None
        pid = require_project_id(project_id)
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**53:
            raise ValueError('WORD_INITIALIZATION_REVISION_INVALID')
        if type(deadline) not in (int, float) or not 0 < deadline - time.monotonic() <= 30:
            raise ValueError('WORD_INITIALIZATION_DEADLINE')
        def initialize(tx):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('WORD_INITIALIZATION_DEADLINE')
            project_reader = getattr(tx, 'saved_word_project', None)
            if project_reader is None:
                raise ValueError('WORD_INITIALIZATION_PROVIDER_UNSUPPORTED')
            root = project_reader(pid, timeout=remaining)
            if (root is None or root.data.get('status') != 'active'
                    or type(root.data.get('generation')) is not int
                    or root.data['generation'] != expected_revision):
                raise ValueError('WORD_INITIALIZATION_REVISION_CONFLICT')
            reader = getattr(tx, 'saved_word_metadata', None)
            if reader is None:
                raise ValueError('WORD_INITIALIZATION_PROVIDER_UNSUPPORTED')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('WORD_INITIALIZATION_DEADLINE')
            rows = reader(pid, limit=MAX_INITIAL_BLOCKS + 1, timeout=min(30, remaining))
            if time.monotonic() >= deadline:
                raise ValueError('WORD_INITIALIZATION_DEADLINE')
            total = 0
            seen = set()
            complete = len(rows) <= MAX_INITIAL_BLOCKS
            for row in rows:
                if (len(row.path) != 4 or row.path[:3] != ('projects', pid, 'blocks')
                        or row.path in seen):
                    complete = False
                seen.add(row.path)
                contribution = verified_contribution(dict(row.data))
                if contribution is None:
                    complete = False
                else:
                    total += contribution.words
            try:
                projection = Projection(expected_revision, len(rows), total) if complete else None
            except ValueError:
                projection = None
            result = _ledger(expected_revision, projection)
            tx.set((LEDGER_COLLECTION, pid), result)
            return result
        return self.store.run_transaction(initialize, max_attempts=1)
