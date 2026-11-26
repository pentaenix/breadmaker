from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from hosted.firestore.memory import MemoryDocumentStore
from hosted.action_journal import ActionDocumentStore
from hosted.activity import records as r
from hosted.activity.journal import ActivityObserver, pulse
from hosted.usage_consent import FIELD, NOTICE_VERSION, NOTICE_SHA256

UID = 'usr_' + 'a' * 32
PID = 'b' * 32
NOW = 1795730400
BORN = '2026-11-01T00:00:00Z'


def seed(*, choice=True):
    raw = MemoryDocumentStore()
    profile = dict(status='active', generation=1, created_at=BORN,
        **{FIELD: dict(choice=choice, revision=1, notice_version=NOTICE_VERSION, notice_sha256=NOTICE_SHA256)})
    raw.run_transaction(lambda tx: tx.create(('users', UID), profile))
    documents = ActionDocumentStore(raw)
    documents.activity = ActivityObserver(lambda: UID, clock=lambda: NOW, nanoseconds=lambda: NOW * 10**9)
    return raw, documents


def row(store):
    item = store.get(('users', UID, r.DAYS, r.day_key(NOW)))
    return dict(item.data) if item else None


def test_two_tabs_count_one_account_minute_and_expire_at_day_boundary():
    raw, _ = seed()
    assert pulse(raw, UID, now=NOW)
    assert pulse(raw, UID, now=NOW + 5)
    assert pulse(raw, UID, now=NOW + 25)
    assert len(row(raw)['minutes']) == 1
    assert row(raw)['last_seen'] == NOW + 25
    expiry = row(raw)['expires_at']
    assert expiry.tzinfo is not None
    assert (expiry - datetime.fromisoformat(r.day_key(NOW)).replace(tzinfo=timezone.utc)).days == r.RETENTION_DAYS


@pytest.mark.parametrize('kind', ['false', 'missing', 'notice', 'hash', 'inactive', 'freeze', 'lock'])
def test_no_tracking_without_current_account_consent(kind):
    raw, documents = seed()
    def change(tx):
        data = dict(tx.get(('users', UID)).data)
        if kind == 'false': data[FIELD]['choice'] = False
        if kind == 'missing': data.pop(FIELD)
        if kind == 'notice': data[FIELD]['notice_version'] = 'old'
        if kind == 'hash': data[FIELD]['notice_sha256'] = 'old'
        if kind == 'inactive': data['status'] = 'erasure_pending'
        if kind == 'freeze': data['_bm_erasure_operation'] = 'closure'
        if kind == 'lock': tx.create(('account_erasure_locks', UID), {})
        tx.set(('users', UID), data)
    raw.run_transaction(change)
    assert pulse(raw, UID, now=NOW) is False
    documents.run_transaction(lambda tx: tx.create(('projects', PID, 'scenes', 'scene_one'), {'generation': 1}))
    assert row(raw) is None


def test_creation_and_save_are_atomic_and_replay_does_not_increment():
    raw, documents = seed()
    path = ('projects', PID, 'blocks', 'block_one')
    def save(tx):
        if tx.get(path): return
        tx.create(path, {'generation': 1, 'block_type': 'paragraph', 'payload_object': {'name': 'opaque-ciphertext'}})
        tx.create(('projects', PID, 'scenes', 'scene_one'), {'generation': 1})
    documents.run_transaction(save)
    before = deepcopy(row(raw))
    documents.run_transaction(save)
    assert row(raw) == before
    assert before['counts']['manuscript_commits'] == 1
    assert before['counts']['scenes_created'] == 1
    with pytest.raises(ValueError):
        def failed(tx):
            tx.create(('projects', PID, 'scenes', 'scene_failed'), {'generation': 1})
            raise ValueError('CAS/authorization rejected')
        documents.run_transaction(failed)
    assert row(raw) == before
    assert raw.get(('projects', PID, 'scenes', 'scene_failed')) is None


def test_task_state_bindings_support_undo_without_decryption_or_new_creations():
    raw, documents = seed()
    path = ('projects', PID, 'tasks', 'task_one')
    first = {'generation': 1, '_bm_private_fields': {'ciphertext': 'opaque-A'}}
    second = {'generation': 2, '_bm_private_fields': {'ciphertext': 'opaque-B'}}
    def create(tx):
        tx.activity_task_states(path, None, 'open')
        tx.create(path, first)
    def complete(tx):
        tx.activity_task_states(path, 'open', 'done')
        tx.set(path, second)
    documents.run_transaction(create)
    documents.run_transaction(complete)
    facts = row(raw)
    assert facts['counts']['tasks_created'] == 1 and facts['counts']['tasks_completed'] == 1
    raw.run_transaction(documents.activity.callback(
        lambda tx: tx.set(path, {**first, 'generation': 3}), restoring=True))
    after = row(raw)
    assert list(after['tasks'].values())[0]['state'] == 'open'
    assert after['counts'] == facts['counts']
    assert 'opaque-A' not in json.dumps(after, default=str)
    assert 'opaque-B' not in json.dumps(after, default=str)
    assert 'task_one' not in json.dumps(after, default=str)


def test_new_grant_does_not_resurrect_previous_consent_interval():
    raw, _ = seed()
    pulse(raw, UID, now=NOW)
    def revise(tx):
        data = dict(tx.get(('users', UID)).data)
        data[FIELD]['revision'] = 3
        tx.set(('users', UID), data)
    raw.run_transaction(revise)
    assert pulse(raw, UID, now=NOW + 60)
    assert row(raw)['consent_revision'] == 3 and len(row(raw)['minutes']) == 1


def test_metadata_only_changes_do_not_manufacture_activity():
    raw, documents = seed()
    documents.run_transaction(lambda tx: tx.create(('projects', PID), {'status': 'importing', 'generation': 1}))
    assert row(raw) is None


def test_unknown_task_ciphertext_is_explicit_not_inferred():
    raw, documents = seed()
    documents.run_transaction(lambda tx: tx.create(('projects', PID, 'tasks', 'task_unknown'),
        {'generation': 1, '_bm_private_fields': {'ciphertext': 'unknown'}}))
    assert list(row(raw)['tasks'].values())[0]['state'] == 'unknown'


def test_overflow_marks_unknown_instead_of_blocking_writing_or_reporting_zero():
    raw, documents = seed()
    def many(tx):
        for index in range(33):
            tx.create(('projects', PID, 'tasks', 'task_' + str(index)), {'generation': 1})
    documents.run_transaction(many)
    assert row(raw)['complete'] is False
    assert raw.get(('projects', PID, 'tasks', 'task_32')) is not None
