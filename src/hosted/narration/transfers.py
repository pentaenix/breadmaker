"""Durable, fail-closed narration byte/attempt/concurrency reservations.

No cloud SDK import, external I/O in transaction callbacks, or runtime activation.
Every HTTP attempt needs a NEW server-side permit before reading its payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
import re
import secrets
from typing import Callable

from hosted.firestore.contracts import DocumentStore, Transaction
from hosted.firestore.ids import require_project_id, require_user_id

GIB = 1024**3
MAX_RESPONSE_BYTES = 2 * 1024**2
_COLLECTION = 'narration_transfer_usage_v1'


class TransferDenied(RuntimeError):
    """Structural code and Retry-After only; no private fields in exceptions."""
    def __init__(self, code: str, retry_after: int):
        super().__init__(code)
        self.code = code
        self.retry_after = max(1, retry_after)


@dataclass(frozen=True)
class TransferPolicy:
    audio_bytes: int = 10 * GIB
    model_bytes: int = 4 * GIB
    global_bytes: int = 40 * GIB
    account_attempts: int = 20_000
    global_attempts: int = 100_000
    concurrent: int = 4
    burst: int = 32
    requests_per_second: int = 4
    lease_seconds: int = 90

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('NARRATION_TRANSFER_POLICY_INVALID')
        if self.concurrent > 4 or self.lease_seconds != 90:
            raise ValueError('NARRATION_TRANSFER_POLICY_INVALID')


@dataclass(frozen=True)
class TransferPermit:
    account_id: str
    project_id: str
    kind: str
    period: str
    attempt: str
    response_bytes: int
    expires_ms: int
    account_bytes_remaining: int


def _integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise RuntimeError('NARRATION_BUDGET_STATE_INVALID')
    return value


def _paths(account_id, period):
    return ((_COLLECTION, f'account_{account_id}_{period}'),
            (_COLLECTION, f'global_{period}'))


class NarrationTransferBudget:
    """Server composition supplies authority; policy is never browser configurable.

    authorize(tx, account, project, kind) MUST be read-only and check the current
    authenticated account + project fences; the callback must return literal True.
    """
    def __init__(self, documents: DocumentStore, *, authorize: Callable,
                 policy: TransferPolicy | None = None, now: Callable | None = None):
        if not callable(authorize):
            raise ValueError('NARRATION_TRANSFER_AUTHORITY_REQUIRED')
        self.documents = documents
        self.authorize = authorize
        self.policy = policy or TransferPolicy()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _time(self):
        stamp = self.now()
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('NARRATION_TRANSFER_CLOCK_INVALID')
        stamp = stamp.astimezone(timezone.utc)
        if stamp.year < 2020 or stamp.year > 9998:
            raise ValueError('NARRATION_TRANSFER_CLOCK_INVALID')
        reset = datetime(stamp.year + (stamp.month == 12), stamp.month % 12 + 1, 1, tzinfo=timezone.utc)
        return stamp.strftime('%Y%m'), int(stamp.timestamp() * 1000), ceil((reset - stamp).total_seconds())

    def _account(self, row, period, now_ms):
        if row is None:
            return {'v': 1, 'period': period, 'audio_bytes': 0, 'model_bytes': 0,
                    'requests': 0, 'tokens_milli': self.policy.burst * 1000,
                    'refill_ms': now_ms, 'active': {}}
        data = dict(row.data)
        expected = {'v', 'period', 'audio_bytes', 'model_bytes', 'requests',
                    'tokens_milli', 'refill_ms', 'active'}
        if set(data) != expected or type(data['v']) is not int or data['v'] != 1 or data['period'] != period:
            raise RuntimeError('NARRATION_BUDGET_STATE_INVALID')
        for name in ('audio_bytes', 'model_bytes', 'requests', 'tokens_milli', 'refill_ms'):
            _integer(data[name])
        if not isinstance(data['active'], dict) or len(data['active']) > 4:
            raise RuntimeError('NARRATION_BUDGET_STATE_INVALID')
        for key, value in data['active'].items():
            if (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key)
                    or not isinstance(value, dict) or set(value) != {'project', 'kind', 'until_ms'}
                    or value['kind'] not in ('audio', 'model')):
                raise RuntimeError('NARRATION_BUDGET_STATE_INVALID')
            require_project_id(value['project'])
            _integer(value['until_ms'])
        data['active'] = {key: dict(value) for key, value in data['active'].items()}
        return data

    @staticmethod
    def _global(row, period):
        if row is None:
            return {'v': 1, 'period': period, 'bytes': 0, 'requests': 0}
        data = dict(row.data)
        if (set(data) != {'v', 'period', 'bytes', 'requests'} or type(data['v']) is not int
                or data['v'] != 1 or data['period'] != period):
            raise RuntimeError('NARRATION_BUDGET_STATE_INVALID')
        _integer(data['bytes']); _integer(data['requests'])
        return data

    def reserve(self, *, account_id: str, project_id: str, kind: str,
                response_bytes: int) -> TransferPermit:
        """One fresh server reservation per response; NO client idempotency key.

        The returned permit is private server state, not a replayable bearer URL.
        Failed/disconnected delivery stays charged to avoid uncertain refunds.
        """
        account_id = require_user_id(account_id)
        project_id = require_project_id(project_id)
        if kind not in ('audio', 'model') or type(kind) is not str:
            raise ValueError('NARRATION_TRANSFER_KIND_INVALID')
        if type(response_bytes) is not int or not 1 <= response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError('NARRATION_TRANSFER_LENGTH_INVALID')
        period, now_ms, reset_seconds = self._time()
        account_path, global_path = _paths(account_id, period)
        attempt = secrets.token_hex(16)  # generated ONCE, outside retrying callback
        expires = now_ms + self.policy.lease_seconds * 1000

        def commit(tx: Transaction):
            if self.authorize(tx, account_id, project_id, kind) is not True:
                raise TransferDenied('NARRATION_ACCESS_DENIED', 60)
            account_row, global_row = tx.get_many([account_path, global_path])
            account = self._account(account_row, period, now_ms)
            global_usage = self._global(global_row, period)
            field = kind + '_bytes'
            allowance = getattr(self.policy, field)
            if account[field] + response_bytes > allowance:
                raise TransferDenied('NARRATION_MONTHLY_TRANSFER_LIMIT', reset_seconds)
            if account['requests'] >= self.policy.account_attempts:
                raise TransferDenied('NARRATION_MONTHLY_REQUEST_LIMIT', reset_seconds)
            if (global_usage['bytes'] + response_bytes > self.policy.global_bytes
                    or global_usage['requests'] >= self.policy.global_attempts):
                raise TransferDenied('NARRATION_ALPHA_TRANSFER_CAPACITY', reset_seconds)
            # Reads of the previous month's lease row keep rollover from doubling
            # concurrency. No counter resets/refunds and no write to that row here.
            previous = datetime.strptime(period, '%Y%m').replace(tzinfo=timezone.utc)
            previous_period = (previous - timedelta(days=1)).strftime('%Y%m')
            prior_row = tx.get(_paths(account_id, previous_period)[0])
            prior = self._account(prior_row, previous_period, now_ms)
            active = {key: value for key, value in account['active'].items() if value['until_ms'] > now_ms}
            prior_count = sum(value['until_ms'] > now_ms for value in prior['active'].values())
            if len(active) + prior_count >= self.policy.concurrent:
                deadlines = [value['until_ms'] for value in (*active.values(), *prior['active'].values())
                             if value['until_ms'] > now_ms]
                raise TransferDenied('NARRATION_TRANSFER_BUSY', ceil((min(deadlines) - now_ms) / 1000))
            elapsed = max(0, now_ms - account['refill_ms'])
            tokens = min(self.policy.burst * 1000,
                         account['tokens_milli'] + elapsed * self.policy.requests_per_second)
            if tokens < 1000:
                wait_ms = ceil((1000 - tokens) / self.policy.requests_per_second)
                raise TransferDenied('NARRATION_TRANSFER_BURST_LIMIT', ceil(wait_ms / 1000))
            active[attempt] = {'project': project_id, 'kind': kind, 'until_ms': expires}
            account.update({field: account[field] + response_bytes,
                            'requests': account['requests'] + 1, 'active': active,
                            'tokens_milli': tokens - 1000,
                            'refill_ms': max(now_ms, account['refill_ms'])})
            global_usage.update(bytes=global_usage['bytes'] + response_bytes,
                                requests=global_usage['requests'] + 1)
            # All authority and ledger reads precede ALL writes (Google contract).
            tx.set(account_path, account); tx.set(global_path, global_usage)
            return TransferPermit(account_id, project_id, kind, period, attempt, response_bytes,
                                  expires, allowance - account[field])

        return self.documents.run_transaction(commit)

    def finish(self, permit: TransferPermit) -> bool:
        """Server-only, idempotent lease release, even after account revocation.

        NEVER refunds usage or mutates authority/canonical roots. Adapters must
        not deserialize this capability from client input.
        """
        if not isinstance(permit, TransferPermit):
            raise ValueError('NARRATION_TRANSFER_PERMIT_INVALID')
        require_user_id(permit.account_id); require_project_id(permit.project_id)
        if not re.fullmatch(r'20[0-9]{4}', permit.period) or not re.fullmatch(r'[0-9a-f]{32}', permit.attempt):
            raise ValueError('NARRATION_TRANSFER_PERMIT_INVALID')
        path = _paths(permit.account_id, permit.period)[0]

        def commit(tx):
            row = tx.get(path)
            if row is None:
                return False
            account = self._account(row, permit.period, 0)
            lease = account['active'].get(permit.attempt)
            if lease is None:
                return False
            if lease != {'project': permit.project_id, 'kind': permit.kind, 'until_ms': permit.expires_ms}:
                raise ValueError('NARRATION_TRANSFER_PERMIT_INVALID')
            del account['active'][permit.attempt]
            tx.set(path, account)
            return True

        return self.documents.run_transaction(commit)
