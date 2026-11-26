"""Real native transactions/crypto and bounded numeric reads; synthetic data only."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hosted.activity import records as r
from hosted.activity.journal import ActivityObserver, pulse
from hosted.activity.http_api import router_for
from hosted.activity.reader import collect, Unavailable, ROOT_FIELDS
from hosted.action_journal import ActionDocumentStore, ActionJournal, bind_action
from hosted.domain_store import HostedDomainStore
from hosted.usage_consent import FIELD, NOTICE_VERSION, NOTICE_SHA256
from tests.unit.test_activity_records import seed, UID, NOW, BORN


def test_encrypted_task_status_and_undo_are_atomic_without_analytics_decryption():
    from tests.test_v10515_private_content_families import setup, OWNER, SECRET
    raw, wrapper, fields, projects, pid = setup()
    def grant(tx):
        row = dict(tx.get(('users', OWNER)).data)
        row[FIELD] = dict(choice=True, revision=1, notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)
        tx.set(('users', OWNER), row)
    raw.run_transaction(grant)
    documents = ActionDocumentStore(raw)
    documents.activity = ActivityObserver(lambda: OWNER)
    domain = HostedDomainStore(documents, projects.objects, encrypted_fields=fields)
    task = domain.create('task', {'project_id': pid}, {'title': SECRET, 'status': 'open'}, user_id=OWNER)
    journal = ActionJournal(documents)
    token = journal.begin(pid, OWNER, 'Synthetic status change')['token']
    with bind_action(token, pid, OWNER):
        domain.update('task', {'project_id': pid, 'task_id': task['id']}, {'status': 'done'}, user_id=OWNER)
    journal.finish(pid, OWNER, token)
    key = ('users', OWNER, r.DAYS, r.day_key(int(time.time())))
    before = dict(raw.get(key).data)
    assert before['counts']['tasks_created'] == 1 and before['counts']['tasks_completed'] == 1
    journal_row = raw.get(('hosted_actions', OWNER, 'tokens', token)).data
    assert all(record['path'][0] == 'projects' for record in journal_row['records'])
    unwrapped = wrapper.unwrap_count
    wrapper.available = False
    journal.restore(pid, OWNER, token, 'undo')
    after = dict(raw.get(key).data)
    assert wrapper.unwrap_count == unwrapped
    assert list(after['tasks'].values())[0]['state'] == 'open'
    assert after['counts'] == before['counts']
    assert SECRET not in json.dumps(after, default=str)


def test_activity_coexists_with_the_existing_account_word_guards(monkeypatch):
    from tests.test_v10596_account_word_guard import runtime, new_project, A
    from hosted.security.account_word_state import path as word_path
    app, raw, _ = runtime(monkeypatch)
    def grant(tx):
        data = dict(tx.get(('users', A)).data)
        data[FIELD] = dict(choice=True, revision=1, notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)
        tx.set(('users', A), data)
    raw.run_transaction(grant)
    app.projects.domain.documents.activity = ActivityObserver(lambda: A)
    new_project(app, A, 'three saved words')
    assert raw.get(word_path(A)).data['saved_words'] == 3
    day = raw.get(('users', A, r.DAYS, r.day_key(int(time.time())))).data
    assert day['counts']['projects_created'] == 1
    assert day['counts']['manuscript_commits'] > 0


class Snapshot:
    def __init__(self, path, data): self.reference, self.data, self.exists = SimpleNamespace(path=path), data, data is not None
    def to_dict(self): return deepcopy(self.data)


class Query:
    def __init__(self, client, group=None): self.client, self.group, self.fields, self.maximum, self.cutoff = client, group, None, None, None
    def select(self, fields): self.fields = fields; return self
    def limit(self, maximum): self.maximum = maximum; return self
    def where(self, *, filter): self.cutoff = filter.value; return self
    def stream(self, **kwargs):
        assert kwargs['retry'] is None and 0 < kwargs['timeout'] <= 3
        self.client.scans.append((self.group, self.cutoff))
        result = []
        for path, data in sorted(self.client.rows.items()):
            parts = path.split('/')
            match = len(parts) == 2 and parts[0] == 'users' if self.group is None else len(parts) == 4 and parts[2] == self.group
            if match and (self.cutoff is None or data['day'] >= self.cutoff):
                result.append(Snapshot(path, {k: deepcopy(v) for k, v in data.items() if k in self.fields}))
        return iter(result[:self.maximum])


class Client:
    def __init__(self, rows): self.rows, self.mutate, self.scans = rows, None, []
    def collection(self, name): assert name == 'users'; return Query(self)
    def collection_group(self, name): assert name == r.DAYS; return Query(self, name)
    def document(self, name): return SimpleNamespace(path=name)
    def get_all(self, refs, **kwargs):
        assert kwargs['field_paths'] in (ROOT_FIELDS, ['user_id']) and kwargs['retry'] is None and kwargs['timeout'] <= 3
        if self.mutate and kwargs['field_paths'] == ROOT_FIELDS: self.mutate()
        return iter(Snapshot(ref.path, {k: deepcopy(v) for k, v in self.rows[ref.path].items() if k in kwargs['field_paths']}
            if ref.path in self.rows else None) for ref in refs)


class Policy:
    def __init__(self, entries=()): self.value = SimpleNamespace(entries=entries)
    def load_policy(self, **kwargs): return self.value


def fixture():
    raw, _ = seed()
    pulse(raw, UID, now=NOW)
    return Client({'users/' + UID: dict(raw.get(('users', UID)).data),
        'users/' + UID + '/' + r.DAYS + '/' + r.day_key(NOW): dict(raw.get(('users', UID, r.DAYS, r.day_key(NOW))).data)})


def read(client, policy=None, now=NOW):
    return collect(client, started_at=r.day_key(NOW) + 'T00:00:00Z', policy_reader=policy or Policy(),
                   deadline=time.monotonic() + 20, now=now)


def test_reader_deduplicates_accounts_and_returns_no_identifiers():
    result = read(fixture())
    assert result['concurrent_accounts'] == 1 and result['included_accounts'] == 1
    assert result['series'][0]['foreground_account_minutes'] == 1
    assert UID not in json.dumps(result) and BORN not in json.dumps(result)
    assert read(fixture(), now=NOW + 91)['concurrent_accounts'] == 0


def test_reader_withdrawal_exclusion_and_account_birth_are_current():
    client = fixture()
    client.rows['users/' + UID][FIELD]['choice'] = False
    assert read(client)['concurrent_accounts'] == 0
    client = fixture()
    entry = SimpleNamespace(hosted_user_id=UID, hosted_created_at=BORN)
    result = read(client, Policy((entry,)))
    assert result['excluded_accounts'] == 1 and result['concurrent_accounts'] == 0
    client = fixture()
    client.rows['users/' + UID]['created_at'] = '2026-11-02T00:00:00Z'
    assert read(client)['series'][0]['foreground_account_minutes'] == 0


def test_reader_never_returns_partial_zero_for_corruption_or_concurrent_withdrawal():
    client = fixture()
    day = next(key for key in client.rows if r.DAYS in key)
    client.rows[day]['counts']['manuscript_commits'] = True
    with pytest.raises(Unavailable): read(client)


def test_reader_fences_erasure_locks_even_before_profile_freeze():
    client = fixture()
    client.rows['account_erasure_locks/' + UID] = {'user_id': UID}
    assert read(client)['included_accounts'] == 0
    client = fixture()
    client.mutate = lambda: client.rows.update({'account_erasure_locks/' + UID: {'user_id': UID}})
    with pytest.raises(Unavailable): read(client)


def test_settled_history_is_reused_only_for_the_same_current_grant():
    client = fixture()
    old_now = NOW - 3 * 86400
    old = r.blank(old_now, (BORN, 1))
    old['minutes'], old['last_seen'] = [0], old_now
    client.rows['users/' + UID + '/' + r.DAYS + '/' + r.day_key(old_now)] = old
    cache = {}
    started = r.day_key(NOW - 5 * 86400) + 'T00:00:00Z'
    def snapshot():
        return collect(client, started_at=started, policy_reader=Policy(), now=NOW,
                       deadline=time.monotonic() + 20, history_cache=cache)
    assert sum(row['foreground_account_minutes'] for row in snapshot()['series']) == 2
    assert sum(row['foreground_account_minutes'] for row in snapshot()['series']) == 2
    assert [cutoff for group, cutoff in client.scans if group == r.DAYS][-1] == r.day_key(NOW - 86400)
    client.rows['users/' + UID][FIELD]['revision'] = 3
    assert sum(row['foreground_account_minutes'] for row in snapshot()['series']) == 0
    assert [cutoff for group, cutoff in client.scans if group == r.DAYS][-1] == started[:10]
    client = fixture()
    client.mutate = lambda: client.rows['users/' + UID][FIELD].update(choice=False)
    with pytest.raises(Unavailable): read(client)


def test_pulse_http_accepts_no_actor_clock_counter_or_event_fields(monkeypatch):
    raw, documents = seed()
    monkeypatch.setenv('BREADMAKER_ACTIVITY_ENABLED', 'true')
    monkeypatch.setenv('BREADMAKER_ACTIVITY_STARTED_AT', '2026-11-01T00:00:00Z')
    monkeypatch.setenv('BREADMAKER_ACTIVITY_PRODUCER_SHA256', r.bundle_hash())
    runtime = SimpleNamespace(mode='hosted', sessions=SimpleNamespace(metadata=lambda: {'auth_mode': 'identity_platform'}),
        identity=SimpleNamespace(request_user_id=lambda: UID, documents=documents))
    app = FastAPI(); app.include_router(router_for(runtime))
    with TestClient(app) as client:
        for body in ('{"uid":"other"}', '{"count":2}', '{"at":1}', '', '[]', 'true'):
            assert client.post('/api/account/activity-pulse', content=body).status_code == 400
        assert client.post('/api/account/activity-pulse?uid=other', content='{}').status_code == 400
        assert client.post('/api/account/activity-pulse', content='{}').json() == {'accepted': True}
        monkeypatch.setenv('BREADMAKER_ACTIVITY_ENABLED', 'false')
        assert client.post('/api/account/activity-pulse', content='{}').status_code == 404
