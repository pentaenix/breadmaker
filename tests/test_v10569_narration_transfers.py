"""Synthetic transfer-admission behavior; no Google, inference or manuscripts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from hosted.firestore.memory import MemoryDocumentStore
from hosted.narration.transfers import (GIB, MAX_RESPONSE_BYTES, NarrationTransferBudget,
                                        TransferDenied, TransferPolicy)

UID = 'usr_' + 'a' * 32
OTHER = 'usr_' + 'b' * 32
PID = 'c' * 32
SECOND = 'd' * 32
AUTH = ('test_authority', 'synthetic')


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    def __call__(self):
        return self.value


@pytest.fixture
def env():
    store = MemoryDocumentStore()
    store.run_transaction(lambda tx: tx.create(AUTH, {'allowed': True}))
    clock = Clock()
    calls = []
    def authority(tx, account, project, kind):
        calls.append((account, project, kind))
        return tx.get(AUTH).data['allowed']
    budget = NarrationTransferBudget(store, authorize=authority, now=clock)
    return store, clock, budget, calls


def reserve(budget, size=1024, **kwargs):
    return budget.reserve(**({'account_id': UID, 'project_id': PID,
                              'kind': 'audio', 'response_bytes': size} | kwargs))


def denied(code, fn):
    with pytest.raises(TransferDenied) as caught:
        fn()
    assert caught.value.code == code and caught.value.retry_after >= 1


def test_default_allowances_and_estimated_audio_hours():
    p = TransferPolicy()
    assert (p.audio_bytes, p.model_bytes, p.global_bytes) == (10 * GIB, 4 * GIB, 40 * GIB)
    assert 62 < p.audio_bytes / (24000 * 2 * 3600) < 63
    assert p.account_attempts > (p.audio_bytes + p.model_bytes) // MAX_RESPONSE_BYTES * 2
    assert p.concurrent == 4 and p.lease_seconds > 60


def test_provider_retry_does_not_double_debit_or_allocate_extra_lease(env):
    store, _, budget, calls = env
    store.retry_next_transaction()
    permit = reserve(budget)
    row = store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data
    assert row['audio_bytes'] == 1024 and row['requests'] == 1
    assert list(row['active']) == [permit.attempt]
    assert len(calls) == 2


def test_retry_or_range_is_a_new_charged_attempt_not_a_replay_key(env):
    store, _, budget, _ = env
    first = reserve(budget)
    assert budget.finish(first)
    second = reserve(budget)
    assert first.attempt != second.attempt
    row = store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data
    assert row['audio_bytes'] == 2048 and row['requests'] == 2


def test_audio_and_model_have_independent_account_budgets(env):
    store, _, budget, _ = env
    budget.policy = replace(budget.policy, audio_bytes=1024, model_bytes=2048)
    first = reserve(budget)
    budget.finish(first)
    denied('NARRATION_MONTHLY_TRANSFER_LIMIT', lambda: reserve(budget, size=1))
    model = reserve(budget, kind='model', size=2048)
    assert model.account_bytes_remaining == 0
    assert store.get(('narration_transfer_usage_v1', 'global_202610')).data['bytes'] == 3072


def test_limit_is_account_wide_across_projects_and_service_instances(env):
    store, clock, budget, _ = env
    budget.policy = replace(budget.policy, audio_bytes=1024)
    second_instance = NarrationTransferBudget(store, authorize=budget.authorize,
                                              policy=budget.policy, now=clock)
    budget.finish(reserve(budget))
    denied('NARRATION_MONTHLY_TRANSFER_LIMIT',
           lambda: reserve(second_instance, size=1, project_id=SECOND))


def test_independent_account_allowances_but_global_bytes_are_shared(env):
    store, clock, budget, _ = env
    budget.policy = replace(budget.policy, audio_bytes=1024, global_bytes=1500)
    budget.finish(reserve(budget))
    allowed = reserve(budget, account_id=OTHER, size=476)
    assert allowed.account_bytes_remaining == 548
    denied('NARRATION_ALPHA_TRANSFER_CAPACITY', lambda: reserve(budget, account_id=OTHER, size=1))


def test_attempt_count_limits_tiny_response_abuse(env):
    _, _, budget, _ = env
    budget.policy = replace(budget.policy, account_attempts=2)
    for _ in range(2):
        budget.finish(reserve(budget, size=1))
    denied('NARRATION_MONTHLY_REQUEST_LIMIT', lambda: reserve(budget, size=1))


def test_global_request_ceiling_applies_across_accounts(env):
    _, _, budget, _ = env
    budget.policy = replace(budget.policy, global_attempts=1)
    budget.finish(reserve(budget, size=1))
    denied('NARRATION_ALPHA_TRANSFER_CAPACITY', lambda: reserve(budget, account_id=OTHER, size=1))


def test_concurrent_replicas_cannot_overspend_last_available_bytes(env):
    store, clock, budget, _ = env
    policy = replace(budget.policy, audio_bytes=1024)
    def attempt(_):
        replica = NarrationTransferBudget(store, authorize=budget.authorize, policy=policy, now=clock)
        try:
            return reserve(replica).response_bytes
        except TransferDenied as error:
            return error.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(1024) == 1
    assert results.count('NARRATION_MONTHLY_TRANSFER_LIMIT') == 7


def test_concurrency_spans_kind_project_and_replica(env):
    store, clock, budget, _ = env
    permits = [reserve(budget, kind=('audio' if n % 2 else 'model'),
                       project_id=(PID if n % 2 else SECOND)) for n in range(4)]
    replica = NarrationTransferBudget(store, authorize=budget.authorize, now=clock)
    denied('NARRATION_TRANSFER_BUSY', lambda: reserve(replica))
    assert replica.finish(permits[0])
    reserve(replica)


def test_lost_process_lease_expires_without_refunding_transferred_bytes(env):
    store, clock, budget, _ = env
    for _ in range(4):
        reserve(budget)
    denied('NARRATION_TRANSFER_BUSY', lambda: reserve(budget))
    clock.value += timedelta(seconds=91)
    reserve(budget)
    row = store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data
    assert row['audio_bytes'] == 5 * 1024 and row['requests'] == 5
    assert len(row['active']) == 1


def test_month_rollover_does_not_double_concurrency(env):
    _, clock, budget, _ = env
    clock.value = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    permits = [reserve(budget) for _ in range(4)]
    clock.value += timedelta(seconds=2)
    denied('NARRATION_TRANSFER_BUSY', lambda: reserve(budget))
    budget.finish(permits[0])
    new = reserve(budget)
    assert new.period == '202611' and new.account_bytes_remaining == 10 * GIB - 1024


def test_new_month_resets_byte_allowance_but_retains_old_usage(env):
    store, clock, budget, _ = env
    budget.policy = replace(budget.policy, audio_bytes=1024)
    budget.finish(reserve(budget))
    clock.value = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert reserve(budget).account_bytes_remaining == 0
    assert store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data['audio_bytes'] == 1024


def test_normal_parallel_chunk_downloads_fit_generous_burst(env):
    store, _, budget, calls = env
    for _ in range(4):
        permits = [reserve(budget, size=MAX_RESPONSE_BYTES) for _ in range(4)]
        for permit in permits:
            budget.finish(permit)
    assert len(calls) == 16
    row = store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data
    assert row['audio_bytes'] == 32 * 1024**2


def test_burst_throttles_bot_but_refills_without_polling_or_daily_limit(env):
    _, clock, budget, _ = env
    budget.policy = replace(budget.policy, burst=2, requests_per_second=4)
    for _ in range(2):
        budget.finish(reserve(budget, size=1))
    denied('NARRATION_TRANSFER_BURST_LIMIT', lambda: reserve(budget, size=1))
    clock.value += timedelta(milliseconds=250)
    reserve(budget, size=1)


@pytest.mark.parametrize('value', [None, False, 1, {}, 'yes'])
def test_authority_must_return_literal_true_before_any_budget_write(env, value):
    store, _, budget, _ = env
    budget.authorize = lambda *args: value
    denied('NARRATION_ACCESS_DENIED', lambda: reserve(budget))
    assert not store.query_collection(('narration_transfer_usage_v1',))


def test_revocation_checked_each_attempt_finish_can_only_release_lease(env):
    store, _, budget, _ = env
    permit = reserve(budget)
    store.run_transaction(lambda tx: tx.set(AUTH, {'allowed': False}))
    denied('NARRATION_ACCESS_DENIED', lambda: reserve(budget))
    assert budget.finish(permit) and not budget.finish(permit)
    assert store.get(AUTH).data == {'allowed': False}
    row = store.get(('narration_transfer_usage_v1', 'account_' + UID + '_202610')).data
    assert row['audio_bytes'] == 1024 and not row['active']


def test_authority_outage_fails_closed_without_consuming_allowance(env):
    store, _, budget, _ = env
    def outage(*_):
        raise OSError('Synthetic authority unavailable')
    budget.authorize = outage
    with pytest.raises(OSError):
        reserve(budget)
    assert not store.query_collection(('narration_transfer_usage_v1',))


@pytest.mark.parametrize('size', [0, -1, True, 1.5, MAX_RESPONSE_BYTES + 1])
def test_response_bounds_precede_authority_and_payload_io(env, size):
    _, _, budget, calls = env
    with pytest.raises(ValueError, match='LENGTH_INVALID'):
        reserve(budget, size=size)
    assert calls == []


def test_denied_download_never_reads_or_decrypts_payload(env):
    _, _, budget, _ = env
    budget.policy = replace(budget.policy, audio_bytes=1)
    reads = []
    def guarded_payload():
        reserve(budget, size=2)
        reads.append('would read payload only after reservation')
    denied('NARRATION_MONTHLY_TRANSFER_LIMIT', guarded_payload)
    assert reads == []


def test_project_or_account_cannot_finish_another_binding(env):
    _, _, budget, _ = env
    permit = reserve(budget)
    with pytest.raises(ValueError, match='PERMIT_INVALID'):
        budget.finish(replace(permit, project_id=SECOND))
    assert not budget.finish(replace(permit, account_id=OTHER))
    assert budget.finish(permit)


@pytest.mark.parametrize('patch', [{'audio_bytes': -1}, {'requests': True},
                                   {'v': 2}, {'active': []}, {'unexpected': 'field'}])
def test_corrupt_or_unknown_account_ledger_fails_closed(env, patch):
    store, _, budget, _ = env
    permit = reserve(budget)
    budget.finish(permit)
    path = ('narration_transfer_usage_v1', 'account_' + UID + '_202610')
    row = dict(store.get(path).data)
    row.update(patch)
    store.run_transaction(lambda tx: tx.set(path, row))
    with pytest.raises(RuntimeError, match='STATE_INVALID'):
        reserve(budget)
    assert store.get(('narration_transfer_usage_v1', 'global_202610')).data['bytes'] == 1024


def test_google_read_before_write_transaction_contract(env):
    store, _, budget, _ = env
    original = store.run_transaction
    class StrictTransaction:
        def __init__(self, tx):
            self.tx = tx
            self.written = False
        def get(self, path):
            assert not self.written
            return self.tx.get(path)
        def get_many(self, paths):
            return [self.get(path) for path in paths]
        def set(self, path, value):
            self.written = True
            self.tx.set(path, value)
    store.run_transaction = lambda fn: original(lambda tx: fn(StrictTransaction(tx)))
    budget.finish(reserve(budget))


def test_only_derivative_control_rows_are_written_no_generation_or_prose(env):
    store, _, budget, _ = env
    canonical = ('projects', PID)
    store.run_transaction(lambda tx: tx.create(canonical, {'generation': 7, 'status': 'active'}))
    budget.finish(reserve(budget))
    assert store.get(canonical).data == {'generation': 7, 'status': 'active'}
    rows = store.query_collection(('narration_transfer_usage_v1',))
    assert len(rows) == 2
    for row in rows:
        assert all(word not in repr(row.data) for word in ('source_text', 'email', 'token_secret', '.wav', 'http'))


@pytest.mark.parametrize('patch', [{'concurrent': 5}, {'lease_seconds': 10},
                                   {'burst': True}, {'audio_bytes': 0}])
def test_invalid_server_policy_rejected(patch):
    with pytest.raises(ValueError, match='POLICY_INVALID'):
        TransferPolicy(**patch)
