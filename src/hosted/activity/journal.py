"""Observe successful canonical commits in their existing Firestore transaction.

Numeric records are outside structural Undo, while Undo/Redo still update task
observations. Task states come only from the normal authorized mutation's
already-open plaintext, or a previously observed ciphertext/state binding.
The collector never decrypts or scans content for analytics.
"""
from datetime import datetime, timezone, timedelta
import time
import re

from hosted.action_journal import _BufferedTransaction
from . import records as r


class ActivityTransaction(_BufferedTransaction):
    def __init__(self, tx):
        super().__init__(tx)
        self.task_hints = {}

    def __getattr__(self, name):
        return getattr(self.tx, name)

    def get_many(self, paths):
        paths = [tuple(path) for path in paths]
        unknown = list(dict.fromkeys(path for path in paths if path not in self.pending))
        batch = getattr(self.tx, 'get_many', None)
        rows = batch(unknown) if batch is not None and unknown else [self.tx.get(path) for path in unknown]
        found = dict(zip(unknown, rows, strict=True))
        return [self.get(path) if path in self.pending else found[path] for path in paths]

    def activity_task_states(self, path, before, after):
        self.task_hints[tuple(path)] = (before, after)

    def flush(self):
        for path, data in self.pending.items():
            if data is None:
                self.tx.delete(path)
            elif self.before[path] is None:
                self.tx.create(path, data)
            else:
                self.tx.set(path, data)


class ActivityObserver:
    def __init__(self, actor, *, clock=time.time, nanoseconds=time.time_ns):
        self.actor, self.clock, self.nanoseconds = actor, clock, nanoseconds

    def callback(self, fn, *, restoring=False):
        def observe(tx):
            buffered = ActivityTransaction(tx)
            result = fn(buffered)  # Failed authorization/CAS never reaches metrics.
            changes = [(p, buffered.before[p], v) for p, v in buffered.pending.items()
                       if r.canonical(p) and buffered.before[p] != v]
            if changes:
                try:
                    uid = self.actor()
                except Exception:
                    uid = None  # Background/local operations have no actor.
                if uid:
                    self._record(buffered, uid, changes, restoring=restoring)
            buffered.flush()
            return result
        return observe

    def _record(self, tx, uid, changes, *, restoring):
        tasks = [(p, b, a) for p, b, a in changes if r.task_path(p)]
        writing = any(len(p) == 4 and p[2] == 'blocks' and after is not None
                   and after.get('block_type', 'paragraph') in {'paragraph', 'text'}
                   and (before or {}).get('payload_object') != after.get('payload_object')
                   for p, before, after in changes)
        creations = [r.collection(p) for p, before, after in changes
                     if before is None and after is not None and r.collection(p)
                     and (len(p) != 2 or after.get('status') == 'active')]
        if not tasks and (restoring or not writing and not creations):
            return
        grant = r.eligible(tx.get(('users', uid)))
        if grant is None or tx.get(('account_erasure_locks', uid)) is not None:
            return
        now = int(self.clock())
        path = ('users', uid, r.DAYS, r.day_key(now))
        previous = tx.get(path)
        data = r.load(dict(previous.data) if previous else None, now, grant)
        if not restoring:
            for kind in creations:
                r.increment(data, kind)
            # One commit transaction, not one count per paragraph or HTTP retry.
            if writing:
                r.increment(data, 'manuscript_commits')
        # A pathological batch must not block writing or manufacture a subtotal.
        if len(tasks) > 32:
            data['complete'] = False
        else:
            for task, before, after in tasks:
                self._task(tx, uid, grant, task, before, after, now, data, restoring=restoring)
        tx.set(path, data)

    def _task(self, tx, uid, grant, path, before, after, now, day, *, restoring):
        key = r.digest(path)
        if key not in day['tasks'] and len(day['tasks']) >= r.MAX_TASKS:
            day['complete'] = False
            return
        proof_path = ('users', uid, r.TASKS, key)
        previous = tx.get(proof_path)
        proof = dict(previous.data) if previous else {}
        if ((proof.get('account_created_at'), proof.get('consent_revision')) != grant
                or type(proof.get('versions')) is not dict):
            proof = dict(account_created_at=grant[0], consent_revision=grant[1], versions={})
        versions = {k: v for k, v in proof['versions'].items()
                    if type(k) is str and re.fullmatch('[0-9a-f]{64}', k)
                    and type(v) is dict and set(v) == {'state', 'at'} and type(v.get('at')) is int
                    and now - r.RETENTION_DAYS * 86400 <= v['at'] <= now
                    and v.get('state') in r.STATES}
        hints = tx.task_hints.get(path, (None, None))
        for row, state in zip((before, after), hints):
            fingerprint = r.task_fingerprint(row)
            if fingerprint and state in r.STATES:
                versions[fingerprint] = dict(state=state, at=now)
        # Bounded ciphertext-version receipts allow Undo without content reads.
        versions = dict(sorted(versions.items(), key=lambda item: item[1]['at'])[-16:])
        state = None if after is None else versions.get(r.task_fingerprint(after), {}).get('state', 'unknown')
        if after and state == 'unknown' and '_bm_private_fields' not in after:
            state = after.get('status') if after.get('status') in r.STATES else 'unknown'
        if not restoring and path in tx.task_hints and state in ('done', 'archived') and state != hints[0]:
            r.increment(day, 'tasks_completed' if state == 'done' else 'tasks_archived')
        day['tasks'][key] = dict(state=state,
            generation=int((after or before or {}).get('generation', 0)),
            observed_ns=min(self.nanoseconds(), (now + 1) * 10**9 - 1))
        proof.update(versions=versions, expires_at=datetime.fromtimestamp(now, timezone.utc)
                     + timedelta(days=r.RETENTION_DAYS))
        tx.set(proof_path, proof)


def pulse(documents, uid, *, now=None):
    now = int(time.time() if now is None else now)
    def mutate(tx):
        grant = r.eligible(tx.get(('users', uid)))
        if grant is None or tx.get(('account_erasure_locks', uid)) is not None:
            return False
        path = ('users', uid, r.DAYS, r.day_key(now))
        previous = tx.get(path)
        day = r.load(dict(previous.data) if previous else None, now, grant)
        if now - day['last_seen'] < 20:
            return True
        minute = now % 86400 // 60
        if minute not in day['minutes']:
            day['minutes'].append(minute)
        day['last_seen'] = now
        tx.set(path, day)
        return True
    return documents.run_transaction(mutate)
