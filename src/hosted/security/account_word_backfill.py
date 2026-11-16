"""Explicit, resumable current-manuscript counting into account-owned records.

No HTTP route, scheduler, client/credential construction or canonical write.
An injected trusted content reader must enforce its own finite transport/decode
bounds and current authorization; a callback is not an identity proof.
"""
from copy import deepcopy
import hashlib
import json
import time
from hosted.firestore.ids import require_project_id, require_user_id, require_opaque_id
from hosted.firestore.memory import MemoryDocumentStore
from hosted.security.saved_word_identity import verify_owner, _stamp
from hosted.security.saved_word_projection import count_saved_text, verified_contribution, DEFINITION, MAX_TEXT_CHARS
from hosted.security.account_word_guard import proof_path, proof_count
from hosted.security import account_word_state as records
from hosted.security.account_word_backfill_store import BoundedAccountWordStore, BackfillUnavailable,BackfillBudgetExceeded

MAX_PAGE = 32
MAX_TEXT_READS = 8
MAX_STEP_CHARS = 2_000_000
MAX_STEP_BYTES = 8 * 1024 * 1024
JOB_FIELDS = {'schema_version', 'user_created_at', 'project_id', 'project_created_at', 'revision',
              'cursor', 'seen_blocks', 'words', 'phase', 'pages', 'plaintext_reads', 'plaintext_characters'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def job_path(uid, born, pid, pin):
    return ('users', require_user_id(uid), 'metric_jobs', 'word_' + fingerprint(
        [uid, born, require_project_id(pid), pin['created_at'], pin['generation']]))


class AccountWordBackfill:
    def __init__(self, store, read_block, *, enabled=False):
        if type(store) not in (MemoryDocumentStore, BoundedAccountWordStore):
            raise BackfillUnavailable('WORD_BACKFILL_BOUNDED_STORE_REQUIRED')
        if not callable(read_block): raise BackfillUnavailable('WORD_BACKFILL_READER_REQUIRED')
        self.store, self.read_block, self.enabled = store, read_block, enabled is True

    @staticmethod
    def _remaining(deadline):
        left = deadline - time.monotonic()
        if not 0 < left <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
        return min(2, left)

    def _run(self, uid, fn, deadline):
        if not self.enabled: raise BackfillUnavailable('WORD_BACKFILL_DISABLED')
        self._remaining(deadline)
        if type(self.store) is BoundedAccountWordStore:
            if self.store.owner != require_user_id(uid): raise BackfillUnavailable('WORD_BACKFILL_OWNER_SCOPE')
            return self.store.run_transaction(fn, deadline=deadline)
        def local(tx):
            self._remaining(deadline); result = fn(tx); self._remaining(deadline); return result
        return self.store.run_transaction(local, max_attempts=1)

    def _profile(self, tx, uid):
        row = tx.get(('users', require_user_id(uid)))
        if (row is None or row.data.get('user_id') != uid or row.data.get('status') != 'active'
                or tx.get(('account_erasure_locks', uid)) is not None):
            raise BackfillUnavailable('WORD_BACKFILL_ACCOUNT_UNAVAILABLE')
        _stamp(row.data.get('created_at'))
        return dict(row.data)

    def _census(self, tx, uid, deadline):
        profile = self._profile(tx, uid)
        projects = tx.saved_word_active_projects(timeout=self._remaining(deadline))
        if len(projects) > 100: raise BackfillUnavailable('WORD_BACKFILL_PROJECT_LIMIT')
        roots, seen = {}, set()
        for row in projects:
            self._remaining(deadline)
            p = row.path
            if len(p)!=2 or p[0]!='projects' or row.data.get('status')!='active':
                raise BackfillUnavailable('WORD_BACKFILL_ROOT_UNVERIFIED')
            pid = require_project_id(p[1])
            if pid in seen: raise BackfillUnavailable('WORD_BACKFILL_ROOT_UNVERIFIED')
            seen.add(pid)
            owners = tx.saved_word_owners(pid, timeout=self._remaining(deadline))
            if len(owners)!=1:raise BackfillUnavailable('WORD_BACKFILL_OWNER_UNVERIFIED')
            owner_uid=require_user_id(owners[0].data.get('user_id'))
            if owners[0].path!=('projects',pid,'members',owner_uid) or owners[0].data.get('role')!='owner':
                raise BackfillUnavailable('WORD_BACKFILL_OWNER_UNVERIFIED')
            if owner_uid!=uid:continue
            verify_owner(project_id=pid, owner_rows=[(r.path, dict(r.data)) for r in owners], hosted_user=profile)
            _stamp(row.data.get('created_at'))
            generation = row.data.get('generation')
            if type(generation) is not int or not 1 <= generation <= records.MAX_REVISION:
                raise BackfillUnavailable('WORD_BACKFILL_ROOT_UNVERIFIED')
            roots[pid] = dict(created_at=row.data['created_at'], generation=generation)
        return profile, roots

    def inspect_account(self, uid, *, deadline):
        def inspect(tx):
            profile, roots = self._census(tx, uid, deadline)
            data = dict(schema_version=1, user_id=uid, user_created_at=profile['created_at'], projects=roots)
            return dict(data, fingerprint=fingerprint(data))
        return self._run(uid, inspect, deadline)

    def initialize_account(self, plan, *, approved=False, deadline):
        if approved is not True or type(plan) is not dict or set(plan) != {
                'schema_version', 'user_id', 'user_created_at', 'projects', 'fingerprint'}:
            raise BackfillUnavailable('WORD_BACKFILL_APPROVED_PLAN_REQUIRED')
        if type(plan['schema_version']) is not int or plan['schema_version'] != 1:
            raise BackfillUnavailable('WORD_BACKFILL_PLAN_UNVERIFIED')
        claimed = {k: v for k, v in plan.items() if k != 'fingerprint'}
        if fingerprint(claimed) != plan['fingerprint']: raise BackfillUnavailable('WORD_BACKFILL_PLAN_CHANGED')
        uid = require_user_id(plan['user_id'])
        def initialize(tx):
            profile, roots = self._census(tx, uid, deadline)
            if profile['created_at'] != plan['user_created_at'] or roots != plan['projects']:
                raise BackfillUnavailable('WORD_BACKFILL_PLAN_STALE')
            old = tx.get(records.path(uid))
            previous = records.parse(dict(old.data), uid, profile['created_at']) if old else None
            entries, jobs, writes = {}, [], {}
            for pid, pin in roots.items():
                entry = previous['projects'].get(pid) if previous else None
                if entry and entry['project_created_at'] == pin['created_at'] and entry['canonical_revision'] == pin['generation'] and entry['initialized'] and entry['blocks'] == entry['known_blocks']:
                    entries[pid] = entry; continue
                jp = job_path(uid, profile['created_at'], pid, pin)
                stored = tx.get(jp)
                if stored:
                    job = self._parse_job(dict(stored.data), uid, jp, profile, pin)
                    entries[pid] = records.project_entry(created_at=pin['created_at'], revision=pin['generation'],
                        blocks=job['seen_blocks'], known_blocks=job['seen_blocks'], known_words=job['words'],
                        initialized=job['phase'] == 'complete')
                else:
                    job = dict(schema_version=1, user_created_at=profile['created_at'], project_id=pid,
                        project_created_at=pin['created_at'], revision=pin['generation'], cursor=None,
                        seen_blocks=0, words=0, phase='running', pages=0, plaintext_reads=0, plaintext_characters=0)
                    writes[jp] = job
                    entries[pid] = records.project_entry(created_at=pin['created_at'], revision=pin['generation'],
                        blocks=0, known_blocks=0, known_words=0, initialized=False)
                jobs.append(dict(user_id=uid, project_id=pid, job_id=jp[-1], cursor=job['cursor'], phase=job['phase']))
            updated = records.state(uid, profile['created_at'], projects=entries, initialized=True)
            if previous != updated: writes[records.path(uid)] = updated
            self._remaining(deadline)
            for p, data in writes.items(): tx.set(p, data)
            return dict(status='initialized', jobs=jobs, state=updated['state'])
        return self._run(uid, initialize, deadline)

    @staticmethod
    def _parse_job(job, uid, path, profile, pin):
        if (type(job) is not dict or set(job) != JOB_FIELDS or type(job['schema_version']) is not int
                or job['schema_version'] != 1 or job['user_created_at'] != profile['created_at']
                or job['project_created_at'] != pin['created_at'] or job['revision'] != pin['generation']
                or job['phase'] not in {'running', 'complete'}
                or job_path(uid, profile['created_at'], job['project_id'], pin) != path):
            raise BackfillUnavailable('WORD_BACKFILL_JOB_UNVERIFIED')
        for key, limit in [('seen_blocks', records.MAX_BLOCKS), ('words', records.MAX_WORDS),
                ('pages', records.MAX_REVISION), ('plaintext_reads', records.MAX_REVISION),
                ('plaintext_characters', records.MAX_REVISION)]:
            records._integer(job[key], limit)
        if job['cursor'] is not None: require_opaque_id(job['cursor'], label='block_id')
        if job['seen_blocks'] == 0 and job['words'] != 0: raise BackfillUnavailable('WORD_BACKFILL_JOB_UNVERIFIED')
        return job

    def _job(self, tx, handle, deadline):
        uid, pid = require_user_id(handle['user_id']), require_project_id(handle['project_id'])
        profile = self._profile(tx, uid)
        root = tx.get(('projects', pid))
        if root is None or root.data.get('status') != 'active': raise BackfillUnavailable('WORD_BACKFILL_PROJECT_UNAVAILABLE')
        owners = tx.saved_word_owners(pid, timeout=self._remaining(deadline))
        verify_owner(project_id=pid, owner_rows=[(r.path, dict(r.data)) for r in owners], hosted_user=profile)
        pin = dict(created_at=root.data.get('created_at'), generation=root.data.get('generation'))
        jp = job_path(uid, profile['created_at'], pid, pin)
        if jp[-1] != handle['job_id']: raise BackfillUnavailable('WORD_BACKFILL_PROJECT_CHANGED')
        stored = tx.get(jp)
        if stored is None: raise BackfillUnavailable('WORD_BACKFILL_JOB_MISSING')
        job = self._parse_job(dict(stored.data), uid, jp, profile, pin)
        stored_account = tx.get(records.path(uid))
        if stored_account is None: raise BackfillUnavailable('WORD_BACKFILL_ACCOUNT_UNINITIALIZED')
        account = records.parse(dict(stored_account.data), uid, profile['created_at'])
        entry = account['projects'].get(pid)
        if entry != records.project_entry(created_at=pin['created_at'], revision=pin['generation'],
                blocks=job['seen_blocks'], known_blocks=job['seen_blocks'], known_words=job['words'],
                initialized=job['phase'] == 'complete'):
            raise BackfillUnavailable('WORD_BACKFILL_SOURCE_CHANGED')
        return profile, pin, jp, job, account

    def inspect_job(self, handle, *, deadline):
        def inspect(tx):
            _, _, _, job, _ = self._job(tx, handle, deadline)
            return dict(phase=job['phase'], cursor=job['cursor'], seen_blocks=job['seen_blocks'],
                        pages=job['pages'], plaintext_reads=job['plaintext_reads'])
        return self._run(handle['user_id'], inspect, deadline)

    def repair_current_blocks(self, uid, pid, block_ids, *, deadline):
        """Repair only now-current missing proofs after a committed restore.

        Does not initialize legacy coverage from a subset or change any journal
        image. Bounded to eight bodies and the same per-step text budget. A
        replay observes existing proofs instead of adding them twice.
        """
        uid,pid=require_user_id(uid),require_project_id(pid)
        if type(block_ids) not in (list,tuple) or len(block_ids)>128:
            raise BackfillUnavailable('WORD_REPAIR_BLOCK_LIMIT')
        bids=sorted({require_opaque_id(b,label='block_id') for b in block_ids})
        def inspect(tx):
            profile=self._profile(tx,uid);root=tx.get(('projects',pid))
            if root is None or root.data.get('status')!='active':
                raise BackfillUnavailable('WORD_REPAIR_PROJECT_UNAVAILABLE')
            owners=tx.saved_word_owners(pid,timeout=self._remaining(deadline))
            verify_owner(project_id=pid,owner_rows=[(r.path,dict(r.data)) for r in owners],hosted_user=profile)
            saved=tx.get(records.path(uid))
            if saved is None:raise BackfillUnavailable('WORD_REPAIR_UNINITIALIZED')
            account=records.parse(dict(saved.data),uid,profile['created_at']);entry=account['projects'].get(pid)
            if (not account['initialized'] or entry is None or not entry['initialized']
                    or entry['project_created_at']!=root.data.get('created_at')
                    or entry['canonical_revision']!=root.data.get('generation')):
                raise BackfillUnavailable('WORD_REPAIR_UNINITIALIZED')
            missing=[]
            for bid in bids:
                row=tx.saved_word_block(pid,bid,timeout=self._remaining(deadline))
                if row is None or verified_contribution(dict(row.data)) is not None:continue
                gen=row.data.get('generation');records._integer(gen,records.MAX_REVISION)
                if not gen:raise BackfillUnavailable('WORD_REPAIR_GENERATION')
                pp=proof_path(uid,profile['created_at'],pid,entry['project_created_at'],bid)
                proof=tx.get(pp)
                if proof_count(dict(proof.data) if proof else None,uid_created=profile['created_at'],
                        pid=pid,project_created=entry['project_created_at'],bid=bid,generation=gen) is None:
                    missing.append(dict(block_id=bid,generation=gen,path=pp))
            if len(missing)>entry['blocks']-entry['known_blocks']:
                raise BackfillUnavailable('WORD_REPAIR_COVERAGE_UNVERIFIED')
            return profile,account,missing
        profile,account,missing=self._run(uid,inspect,deadline)
        completed=[];chars=byte_count=0
        for item in missing[:MAX_TEXT_READS]:
            self._remaining(deadline)
            if MAX_STEP_CHARS-chars<MAX_TEXT_CHARS or MAX_STEP_BYTES-byte_count<4*MAX_TEXT_CHARS:break
            try:
                body=self.read_block(user_id=uid,project_id=pid,block_id=item['block_id'],
                    expected_generation=item['generation'],deadline=deadline,max_text_chars=MAX_TEXT_CHARS,
                    max_text_bytes=4*MAX_TEXT_CHARS)
                if type(body) is not dict or type(body.get('generation')) is not int or body['generation']!=item['generation']:
                    raise ValueError()
                text=body['text_content'];count=count_saved_text(text)
                size=len(text.encode('utf-8'))
                if len(text)>MAX_TEXT_CHARS or size>4*MAX_TEXT_CHARS:raise ValueError()
                chars+=len(text);byte_count+=size
                completed.append(dict(item,words=count.words));del body,text
            except BackfillBudgetExceeded:raise
            except Exception:raise BackfillUnavailable('WORD_REPAIR_CONTENT_UNAVAILABLE') from None
        if not completed:return dict(state=account['state'],repaired_blocks=0,pending_blocks=len(missing))
        def publish(tx):
            current_profile,current_account,current_missing=inspect(tx)
            if profile!=current_profile or account!=current_account or missing!=current_missing:
                raise BackfillUnavailable('WORD_REPAIR_CURRENT_STATE_CHANGED')
            entry=current_account['projects'][pid]
            entry['known_blocks']+=len(completed);entry['known_words']+=sum(r['words'] for r in completed)
            updated=records.state(uid,profile['created_at'],projects=current_account['projects'],initialized=True)
            for row in completed:
                tx.set(row['path'],dict(schema_version=1,definition=DEFINITION,user_created_at=profile['created_at'],
                    project_id=pid,project_created_at=entry['project_created_at'],block_id=row['block_id'],
                    source_generation=row['generation'],words=row['words']))
            tx.set(records.path(uid),updated)
            return dict(state=updated['state'],repaired_blocks=len(completed),pending_blocks=len(missing)-len(completed))
        return self._run(uid,publish,deadline)

    def _page(self, tx, handle, job, deadline):
        rows = tx.saved_word_page(handle['project_id'], after_id=job['cursor'], limit=33,
                                  timeout=self._remaining(deadline))
        if len(rows) > MAX_PAGE + 1: raise BackfillUnavailable('WORD_BACKFILL_PAGE_UNVERIFIED')
        previous = job['cursor']
        for row in rows:
            if (len(row.path) != 4 or row.path[:3] != ('projects', handle['project_id'], 'blocks')
                    or not set(row.data) <= {'generation', '_bm_saved_word_v1'}):
                raise BackfillUnavailable('WORD_BACKFILL_PAGE_UNVERIFIED')
            bid = require_opaque_id(row.path[-1], label='block_id')
            generation = row.data.get('generation')
            if (previous is not None and bid <= previous or type(generation) is not int
                    or not 1 <= generation <= records.MAX_REVISION):
                raise BackfillUnavailable('WORD_BACKFILL_PAGE_UNVERIFIED')
            previous = bid
        return rows

    def step(self, handle, *, expected_cursor, approved=False, deadline):
        if approved is not True: raise BackfillUnavailable('WORD_BACKFILL_APPROVED_PLAN_REQUIRED')
        def prepare(tx):
            profile, pin, jp, job, account = self._job(tx, handle, deadline)
            if job['phase'] == 'complete' or job['cursor'] != expected_cursor:
                return dict(skip=True, phase=job['phase'], cursor=job['cursor'])
            rows = self._page(tx, handle, job, deadline)
            values = []
            for row in rows[:MAX_PAGE]:
                if len(row.path) != 4 or row.path[:3] != ('projects', handle['project_id'], 'blocks'):
                    raise BackfillUnavailable('WORD_BACKFILL_PAGE_UNVERIFIED')
                bid = require_opaque_id(row.path[-1], label='block_id')
                generation = row.data.get('generation')
                if type(generation) is not int or not 1 <= generation <= records.MAX_REVISION:
                    raise BackfillUnavailable('WORD_BACKFILL_PAGE_UNVERIFIED')
                count = verified_contribution(dict(row.data))
                if count is None:
                    proof = tx.get(proof_path(handle['user_id'], profile['created_at'], handle['project_id'], pin['created_at'], bid))
                    count = proof_count(dict(proof.data) if proof else None, uid_created=profile['created_at'],
                        pid=handle['project_id'], project_created=pin['created_at'], bid=bid, generation=generation)
                values.append(dict(block_id=bid, generation=generation, words=count.words if count else None))
            return dict(skip=False, profile=profile, pin=pin, path=jp, job=job, values=values, exhausted=len(rows) <= MAX_PAGE)
        page = self._run(handle['user_id'], prepare, deadline)
        if page['skip']: return page
        completed, chars, byte_count, reads = [], 0, 0, 0
        for row in page['values']:
            self._remaining(deadline)
            row = dict(row)
            if row['words'] is None:
                if reads >= MAX_TEXT_READS: break
                # Reserve a full permitted block before reading it; do not read
                # one additional body and only then discover the page is full.
                if MAX_STEP_CHARS - chars < MAX_TEXT_CHARS or MAX_STEP_BYTES - byte_count < 4 * MAX_TEXT_CHARS:
                    break
                try:
                    content = self.read_block(user_id=handle['user_id'], project_id=handle['project_id'],
                        block_id=row['block_id'], expected_generation=row['generation'], deadline=deadline,
                        max_text_chars=MAX_TEXT_CHARS, max_text_bytes=4 * MAX_TEXT_CHARS)
                    if type(content) is not dict or content.get('generation') != row['generation']:
                        raise ValueError()
                    text = content['text_content']
                    count = count_saved_text(text)
                    size = len(text.encode('utf-8'))
                except BackfillBudgetExceeded:raise
                except Exception:
                    raise BackfillUnavailable('WORD_BACKFILL_CONTENT_UNAVAILABLE') from None
                self._remaining(deadline)
                if chars + len(text) > MAX_STEP_CHARS or byte_count + size > MAX_STEP_BYTES:
                    if not completed: raise BackfillUnavailable('WORD_BACKFILL_TEXT_BUDGET')
                    break
                chars += len(text); byte_count += size; reads += 1; row['words'] = count.words
                del content, text
            completed.append(row)
        done = page['exhausted'] and len(completed) == len(page['values'])
        if not completed and not done: raise BackfillUnavailable('WORD_BACKFILL_NO_PROGRESS')
        def publish(tx):
            profile, pin, jp, job, account = self._job(tx, handle, deadline)
            if job != page['job']: raise BackfillUnavailable('WORD_BACKFILL_CURSOR_CHANGED')
            rows = self._page(tx, handle, job, deadline)
            actual = [(r.path[-1], r.data.get('generation')) for r in rows[:len(completed)]]
            if actual != [(r['block_id'], r['generation']) for r in completed]:
                raise BackfillUnavailable('WORD_BACKFILL_PAGE_CHANGED')
            if done:
                _, roots = self._census(tx, handle['user_id'], deadline)
                manifest = {pid: dict(created_at=e['project_created_at'], generation=e['canonical_revision'])
                            for pid, e in account['projects'].items()}
                if roots != manifest: raise BackfillUnavailable('WORD_BACKFILL_ACCOUNT_MANIFEST_CHANGED')
            job = deepcopy(job)
            job.update(cursor=completed[-1]['block_id'] if completed else job['cursor'],
                seen_blocks=job['seen_blocks']+len(completed), words=job['words']+sum(r['words'] for r in completed),
                phase='complete' if done else 'running', pages=job['pages']+1,
                plaintext_reads=job['plaintext_reads']+reads, plaintext_characters=job['plaintext_characters']+chars)
            self._parse_job(job, handle['user_id'], jp, profile, pin)
            account['projects'][handle['project_id']] = records.project_entry(created_at=pin['created_at'],
                revision=pin['generation'], blocks=job['seen_blocks'], known_blocks=job['seen_blocks'],
                known_words=job['words'], initialized=done)
            account = records.state(handle['user_id'], profile['created_at'], projects=account['projects'],
                                    initialized=account['initialized'])
            writes = {}
            for row in completed:
                writes[proof_path(handle['user_id'], profile['created_at'], handle['project_id'], pin['created_at'], row['block_id'])] = dict(
                    schema_version=1, definition=DEFINITION, user_created_at=profile['created_at'], project_id=handle['project_id'],
                    project_created_at=pin['created_at'], block_id=row['block_id'], source_generation=row['generation'], words=row['words'])
            writes[jp] = job; writes[records.path(handle['user_id'])] = account
            self._remaining(deadline)
            for path, data in writes.items(): tx.set(path, data)
            return dict(phase=job['phase'], cursor=job['cursor'], seen_blocks=job['seen_blocks'], state=account['state'])
        return self._run(handle['user_id'], publish, deadline)
