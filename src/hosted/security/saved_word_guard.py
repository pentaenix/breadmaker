"""Uncomposed transaction guard for saved-word coverage.

Install BELOW ActionDocumentStore so Undo/Redo and journal fallback pass through
this boundary. Never install inside a journal callback: the ledger is operational
state, not a user-authorized project mutation or an Undo record.

Until all canonical writers carry reviewed contributions this guard only
invalidates. It cannot promote any project to complete, even a newly empty one.
"""
from hosted.action_journal import _BufferedTransaction
from hosted.firestore.ids import require_project_id

LEDGER_COLLECTION = 'admin_saved_word_coverage'


class _CoverageTransaction(_BufferedTransaction):
    def get_many(self, paths):
        return [self.get(path) for path in paths]

    def count_collection(self, path):
        if any(p[:-1] == tuple(path) for p in self.pending):
            raise ValueError('WORD_COVERAGE_COUNT_AFTER_WRITE')
        return self.tx.count_collection(path)


class SavedWordCoverageGuard:
    def __init__(self, store, *, enabled=False):
        self.store = store
        self.enabled = enabled is True

    def __getattr__(self, name):
        return getattr(self.store, name)

    def run_transaction(self, fn, *, max_attempts=5):
        if not self.enabled:
            return self.store.run_transaction(fn, max_attempts=max_attempts)

        def execute(tx):
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
            ledgers = {}
            for pid in sorted(affected):
                path = (LEDGER_COLLECTION, pid)
                if path in buffered.pending:
                    raise ValueError('WORD_COVERAGE_LEDGER_WRITE_FORBIDDEN')
                old = tx.get(path)
                # Do not create operational metadata for existing projects just
                # because an unrelated save occurred. Initialization is separate.
                if old is None:
                    continue
                root_path = ('projects', pid)
                root = buffered.get(root_path)
                revision = root.data.get('generation') if root is not None else None
                if type(revision) is not int or revision < 1:
                    revision = None
                ledgers[path] = {
                    'schema_version': 1,
                    'definition': 'saved_text_unicode_words_v1',
                    'state': 'unknown',
                    'canonical_revision': revision,
                    'blocks': None,
                    'words': None,
                }
            # All reads, including coverage/root reads, precede provider writes.
            for path, data in buffered.pending.items():
                if data is None:
                    tx.delete(path)
                elif buffered.before[path] is None:
                    tx.create(path, data)
                else:
                    tx.set(path, data)
            for path, data in ledgers.items():
                tx.set(path, data)
            return result

        return self.store.run_transaction(execute, max_attempts=max_attempts)
