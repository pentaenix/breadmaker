"""Durable pronunciation admission. No text, dictionaries or network in ledgers.

Reserve the whole possible helper call before I/O; only a confirmed successful
call can settle to measured SERVER wall time. Crashes/timeouts remain charged.
This limits feature usage, not anonymous traffic or total Google billing.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
import secrets

from hosted.firestore.ids import require_project_id, require_user_id
from hosted.narration.transfers import TransferDenied

COLLECTION = 'narration_pronunciation_usage_v1'
RESERVE_MS = 40_000
LEASE_MS = 60_000


@dataclass(frozen=True)
class PronunciationPolicy:
    account_characters: int = 5_000_000
    global_characters: int = 20_000_000
    account_milliseconds: int = 3_600_000
    global_milliseconds: int = 10_800_000
    account_attempts: int = 20_000
    global_attempts: int = 100_000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('NARRATION_PRONUNCIATION_POLICY_INVALID')


@dataclass(frozen=True)
class PronunciationPermit:
    account: str
    project: str
    period: str
    attempt: str
    expires_ms: int


def _paths(uid, period):
    return ((COLLECTION, f'account_{uid}_{period}'), (COLLECTION, f'global_{period}'))


def _row(stored, period):
    if stored is None:
        return dict(v=1, period=period, characters=0, milliseconds=0, requests=0, active={})
    data = dict(stored.data)
    if (set(data) != {'v', 'period', 'characters', 'milliseconds', 'requests', 'active'}
            or type(data['v']) is not int or data['v'] != 1 or data['period'] != period
            or any(type(data[key]) is not int or not 0 <= data[key] < 2**63
                   for key in ('characters', 'milliseconds', 'requests'))
            or not isinstance(data['active'], dict) or len(data['active']) > 1):
        raise RuntimeError('NARRATION_PRONUNCIATION_BUDGET_INVALID')
    for key, lease in data['active'].items():
        if (not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key)
                or not isinstance(lease, dict) or set(lease) != {'account', 'project', 'until_ms'}
                or type(lease['until_ms']) is not int or not 0 < lease['until_ms'] < 2**63):
            raise RuntimeError('NARRATION_PRONUNCIATION_BUDGET_INVALID')
        require_user_id(lease['account']); require_project_id(lease['project'])
    return {**data, 'active': dict(data['active'])}


class PronunciationBudget:
    def __init__(self, documents, *, authorize, policy=None, now=None):
        if not callable(authorize):
            raise ValueError('NARRATION_PRONUNCIATION_AUTHORITY_REQUIRED')
        self.documents, self.authorize = documents, authorize
        self.policy = policy or PronunciationPolicy()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _time(self):
        now = self.now()
        if not isinstance(now, datetime) or now.tzinfo is None or not 2020 <= now.year < 9998:
            raise ValueError('NARRATION_PRONUNCIATION_CLOCK_INVALID')
        now = now.astimezone(timezone.utc)
        reset = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
        return now.strftime('%Y%m'), int(now.timestamp() * 1000), math.ceil((reset - now).total_seconds())

    def reserve(self, uid, pid, *, characters):
        uid, pid = require_user_id(uid), require_project_id(pid)
        if type(characters) is not int or not 1 <= characters <= 30_000:
            raise ValueError('NARRATION_PRONUNCIATION_LENGTH_INVALID')
        period, now_ms, reset = self._time()
        attempt = secrets.token_hex(16)  # once, OUTSIDE retry callback
        permit = PronunciationPermit(uid, pid, period, attempt, now_ms + LEASE_MS)
        previous = (datetime.strptime(period, '%Y%m') - timedelta(days=1)).strftime('%Y%m')
        paths = _paths(uid, period)
        def commit(tx):
            if self.authorize(tx, uid, pid, 'read') is not True:
                raise TransferDenied('NARRATION_ACCESS_DENIED', 60)
            current = [_row(row, period) for row in tx.get_many(list(paths))]
            prior = [_row(row, previous) for row in tx.get_many(list(_paths(uid, previous)))]
            if any(lease['until_ms'] > now_ms for row in (*current, *prior) for lease in row['active'].values()):
                raise TransferDenied('NARRATION_PRONUNCIATION_BUSY', 2)
            for prefix, row in zip(('account', 'global'), current):
                if (row['characters'] + characters > getattr(self.policy, prefix + '_characters')
                        or row['milliseconds'] + RESERVE_MS > getattr(self.policy, prefix + '_milliseconds')
                        or row['requests'] >= getattr(self.policy, prefix + '_attempts')):
                    raise TransferDenied('NARRATION_PRONUNCIATION_MONTHLY_LIMIT', reset)
            for path, row in zip(paths, current):
                row.update(characters=row['characters'] + characters, milliseconds=row['milliseconds'] + RESERVE_MS,
                           requests=row['requests'] + 1, active={attempt: dict(account=uid, project=pid, until_ms=permit.expires_ms)})
                tx.set(path, row)
            return permit
        return self.documents.run_transaction(commit)

    def finish(self, permit, *, confirmed_elapsed_ms=None):
        """Server-only completion. One exact lease; no replayable refund API.

        Requests/characters NEVER refund. Unknown outcomes keep full40s debit.
        Expired/pruned leases cannot refund, even if a late response arrives.
        """
        if (not isinstance(permit, PronunciationPermit)
                or not re.fullmatch('20[0-9]{4}', permit.period)
                or not re.fullmatch('[0-9a-f]{32}', permit.attempt)):
            raise ValueError('NARRATION_PRONUNCIATION_PERMIT_INVALID')
        require_user_id(permit.account); require_project_id(permit.project)
        if confirmed_elapsed_ms is not None and (type(confirmed_elapsed_ms) is not int
                                                or not 0 <= confirmed_elapsed_ms <= RESERVE_MS):
            raise ValueError('NARRATION_PRONUNCIATION_ELAPSED_INVALID')
        _, now_ms, _ = self._time()
        paths = _paths(permit.account, permit.period)
        expected = dict(account=permit.account, project=permit.project, until_ms=permit.expires_ms)
        def commit(tx):
            stored = tx.get_many(list(paths))
            if any(row is None for row in stored):
                return False
            rows = [_row(row, permit.period) for row in stored]
            if any(row['active'].get(permit.attempt) != expected for row in rows):
                return False
            refund = 0 if confirmed_elapsed_ms is None or now_ms >= permit.expires_ms else RESERVE_MS - max(100, confirmed_elapsed_ms)
            if any(row['milliseconds'] < refund for row in rows):
                raise RuntimeError('NARRATION_PRONUNCIATION_BUDGET_INVALID')
            for path, row in zip(paths, rows):
                row['active'].pop(permit.attempt)
                row['milliseconds'] -= refund
                tx.set(path, row)
            return True
        return self.documents.run_transaction(commit)
