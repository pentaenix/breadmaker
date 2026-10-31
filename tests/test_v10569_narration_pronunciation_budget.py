"""Synthetic cross-instance helper limits, current authority and private transport."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hosted.firestore.memory import MemoryDocumentStore
from hosted.narration.http import build_recording_router
from hosted.narration.pronunciation_budget import (COLLECTION, RESERVE_MS, PronunciationBudget,
    PronunciationPolicy, _paths)
from hosted.narration.pronunciation_client import HostedPronunciation, PrivatePronunciationClient
from hosted.narration.transfers import TransferDenied
from narration.pronunciation import PronunciationError

UID, PID = 'usr_' + 'a' * 32, 'c' * 32
URL = 'https://breadmaker-pronunciation-123456.us-central1.run.app'
RESULT = {'items': [{'type': 'phonetic', 'value': 'a ', 'subtitles': 'Synthetic'}]}


@pytest.fixture
def env():
    docs = MemoryDocumentStore()
    clock = [datetime(2026, 10, 30, tzinfo=timezone.utc)]
    allowed = [True]
    def authority(tx, uid, pid, kind):
        assert (uid, pid, kind) == (UID, PID, 'read')
        return allowed[0]
    return docs, clock, allowed, PronunciationBudget(docs, authorize=authority, now=lambda: clock[0])


def rows(env):
    return [dict(env[0].get(path).data) for path in _paths(UID, env[1][0].strftime('%Y%m'))]


def reserve(env):
    return env[3].reserve(UID, PID, characters=9)


def test_reservation_precharges_both_ledgers_and_success_settles_only_once(env):
    permit = reserve(env)
    assert all(row['milliseconds'] == RESERVE_MS and row['characters'] == 9 for row in rows(env))
    assert env[3].finish(permit, confirmed_elapsed_ms=1234)
    assert not env[3].finish(permit, confirmed_elapsed_ms=0)
    assert all(row['milliseconds'] == 1234 and row['requests'] == 1 and not row['active'] for row in rows(env))
    assert 'Synthetic' not in json.dumps(rows(env))


def test_failed_or_uncertain_attempt_never_refunds_usage(env):
    permit = reserve(env)
    assert env[3].finish(permit)
    assert all(row['milliseconds'] == RESERVE_MS and not row['active'] for row in rows(env))


def test_expired_late_success_cannot_refund_or_block_future_work(env):
    permit = reserve(env)
    env[1][0] += timedelta(seconds=61)
    assert env[3].finish(permit, confirmed_elapsed_ms=1)
    assert all(row['milliseconds'] == RESERVE_MS for row in rows(env))
    reserve(env)


def test_concurrent_replicas_admit_only_one_private_helper_call(env):
    def attempt(_):
        try:
            replica = PronunciationBudget(env[0], authorize=env[3].authorize, now=env[3].now)
            return replica.reserve(UID, PID, characters=9)
        except TransferDenied:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(attempt, range(8)))
    assert sum(item is not None for item in result) == 1
    assert all(row['requests'] == 1 for row in rows(env))


def test_month_rollover_does_not_double_helper_concurrency(env):
    env[1][0] = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    permit = reserve(env)
    env[1][0] += timedelta(seconds=2)
    with pytest.raises(TransferDenied, match='BUSY'):
        reserve(env)
    env[3].finish(permit, confirmed_elapsed_ms=2000)
    assert reserve(env).period == '202611'


def test_all_quota_dimensions_reject_before_debit_or_network(env):
    for field in ('account_characters', 'global_characters', 'account_milliseconds',
                  'global_milliseconds', 'account_attempts', 'global_attempts'):
        docs = MemoryDocumentStore()
        policy = PronunciationPolicy(**{field: 1})
        budget = PronunciationBudget(docs, authorize=env[3].authorize, policy=policy, now=env[3].now)
        if field.endswith('_attempts'):
            permit = budget.reserve(UID, PID, characters=9)
            budget.finish(permit)
        before = [(row.path, row.data) for row in docs.query_collection((COLLECTION,))]
        with pytest.raises(TransferDenied, match='MONTHLY_LIMIT'):
            budget.reserve(UID, PID, characters=9)
        assert before == [(row.path, row.data) for row in docs.query_collection((COLLECTION,))]


def test_false_authority_and_unknown_schema_fail_closed_without_new_debits(env):
    env[2][0] = None
    with pytest.raises(TransferDenied, match='ACCESS_DENIED'):
        reserve(env)
    assert not env[0].query_collection((COLLECTION,))
    env[2][0] = True
    path = _paths(UID, '202610')[0]
    env[0].run_transaction(lambda tx: tx.set(path, {'v': 999}))
    with pytest.raises(RuntimeError, match='BUDGET_INVALID'):
        reserve(env)
    assert env[0].get(path).data == {'v': 999}


def test_provider_retry_does_not_double_debit(env):
    docs = MemoryDocumentStore(retry_once=True)
    budget = PronunciationBudget(docs, authorize=env[3].authorize, now=env[3].now)
    permit = budget.reserve(UID, PID, characters=9)
    assert all(row.data['requests'] == 1 for row in docs.query_collection((COLLECTION,)))
    budget.finish(permit, confirmed_elapsed_ms=500)
    assert all(row.data['milliseconds'] == 500 for row in docs.query_collection((COLLECTION,)))


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, *, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]


class Session:
    def __init__(self, *, response=None, metadata=None):
        self.calls = []
        self.response = response or Response(json.dumps(RESULT).encode())
        self.metadata = metadata or Response(b'a.b.c', headers={'Metadata-Flavor': 'Google'})
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs)); return self.metadata
    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs)); return self.response


def test_private_transport_uses_fixed_audience_metadata_token_and_no_redirects():
    session = Session()
    result = PrivatePronunciationClient(URL, session_factory=lambda: session).convert('ja', 'Synthetic', [])
    assert result == RESULT and len(session.calls) == 2
    metadata, post = session.calls
    assert metadata[2]['params'] == {'audience': URL, 'format': 'full'}
    assert metadata[2]['headers'] == {'Metadata-Flavor': 'Google'}
    assert post[1] == URL + '/pronounce' and post[2]['headers']['Authorization'] == 'Bearer a.b.c'
    assert all(call[2]['allow_redirects'] is False and call[2]['stream'] for call in session.calls)
    assert json.loads(post[2]['data']) == dict(language='ja', text='Synthetic', words=[])


def test_untrusted_helper_addresses_and_invalid_input_never_make_requests():
    for url in ('http://helper.run.app', 'https://evil.example', URL + '/other', URL + '?secret=1',
                'https://user:pass@helper.run.app', 'https://helper.run.app:443'):
        with pytest.raises(ValueError, match='HELPER_URL_INVALID'):
            PrivatePronunciationClient(url)
    def prohibited(): raise AssertionError('must validate before networking')
    with pytest.raises(PronunciationError, match='INVALID'):
        PrivatePronunciationClient(URL, session_factory=prohibited).convert('ja', '', [])


def test_private_transport_rejects_redirects_large_or_corrupt_results_without_leaking_body():
    for response in (Response(b'Synthetic secret', 302), Response(b'x' * (2 * 1024**2 + 1)),
                     Response(b'{"items":[{"bad":"Synthetic secret"}]}')):
        session = Session(response=response)
        with pytest.raises(PronunciationError) as error:
            PrivatePronunciationClient(URL, session_factory=lambda: session).convert('ja', 'Synthetic', [])
        assert 'secret' not in str(error.value) and len(session.calls) == 2
    session = Session(metadata=Response(b'a.b.c', headers={}))
    with pytest.raises(PronunciationError, match='UNAVAILABLE'):
        PrivatePronunciationClient(URL, session_factory=lambda: session).convert('ja', 'Synthetic', [])
    assert len(session.calls) == 1


def test_service_precharges_before_client_and_rechecks_revocation_afterward(env):
    class Client:
        def convert(self, *args):
            assert all(row['milliseconds'] == RESERVE_MS for row in rows(env))
            env[2][0] = False
            return RESULT
    with pytest.raises(PronunciationError, match='ACCESS_DENIED'):
        HostedPronunciation(env[3], Client()).convert(UID, PID, {'language': 'ja', 'text': 'Synthetic'})
    assert all(row['milliseconds'] == RESERVE_MS and not row['active'] for row in rows(env))


def test_service_success_uses_server_measured_time_not_client_supplied_cost(env):
    class Client:
        def convert(self, *args): return RESULT
    clock = iter((100.0, 100.5))
    service = HostedPronunciation(env[3], Client(), monotonic=lambda: next(clock))
    assert service.convert(UID, PID, {'language': 'ja', 'text': 'Synthetic'}) == RESULT
    assert all(row['milliseconds'] == 500 for row in rows(env))
    with pytest.raises(PronunciationError, match='INVALID'):
        service.convert(UID, PID, {'language': 'ja', 'text': 'Synthetic', 'milliseconds': 0})


def test_http_pronunciation_is_bounded_and_uses_server_context(env):
    class Client:
        def convert(self, *args): return RESULT
    service = HostedPronunciation(env[3], Client())
    app = FastAPI()
    app.include_router(build_recording_router(provider=lambda: object(), identity=lambda: UID,
        project=lambda request: PID, pronunciation_provider=lambda: service))
    http = TestClient(app)
    response = http.post('/api/narration-pronunciation', json={'language': 'ja', 'text': 'Synthetic'})
    assert response.status_code == 200 and response.json() == RESULT
    assert response.headers['cache-control'] == 'private, no-store'
    count = rows(env)[0]['requests']
    assert http.post('/api/narration-pronunciation', content=b'x' * (256 * 1024 + 1)).status_code == 413
    assert http.post('/api/narration-pronunciation', json={'language': 'ja', 'text': 'x', 'uid': UID}).status_code == 400
    assert rows(env)[0]['requests'] == count
