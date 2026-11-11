"""Disabled billing foundation. No provider SDK, network, storage or activation.

PaidPeriod is trusted server-side input for a future verified billing adapter,
never a browser request model. This module does not grant hosted admission or
change existing application authorization. The disabled adapter is the ONLY
runtime adapter shipped in this phase.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('BILLING_AWARE_TIMESTAMP_REQUIRED')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PaidPeriod:
    account_id: str
    paid_from: datetime
    paid_until: datetime
    cancel_at_period_end: bool = False
    revoked_at: datetime | None = None

    def __post_init__(self):
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError('BILLING_ACCOUNT_REQUIRED')
        if _utc(self.paid_until) <= _utc(self.paid_from):
            raise ValueError('BILLING_PERIOD_INVALID')
        if type(self.cancel_at_period_end) is not bool:
            raise ValueError('BILLING_CANCELLATION_INVALID')
        if self.revoked_at is not None:
            _utc(self.revoked_at)

    def allows_paid_access(self, *, authenticated_account_id: str, now: datetime) -> bool:
        """Cancellation stops renewal; it does not discard the paid remainder.

        Failed renewal must not create/extend this paid period. Refund/dispute
        revocation is explicit and requires a verified server-side policy.
        """
        instant = _utc(now)
        return (
            authenticated_account_id == self.account_id
            and _utc(self.paid_from) <= instant < _utc(self.paid_until)
            and (self.revoked_at is None or instant < _utc(self.revoked_at))
        )


class BillingUnavailable(RuntimeError):
    pass


class BillingService(Protocol):
    """Future adapters must derive account identity from the verified session."""
    def account_snapshot(self, *, authenticated_account_id: str) -> dict: ...
    def checkout(self, *, authenticated_account_id: str) -> str: ...
    def portal(self, *, authenticated_account_id: str) -> str: ...
    def cancel_at_period_end(self, *, authenticated_account_id: str) -> None: ...


class DisabledBillingService:
    def account_snapshot(self, *, authenticated_account_id: str) -> dict:
        if not authenticated_account_id:
            raise ValueError('BILLING_ACCOUNT_REQUIRED')
        # Unavailable is deliberately distinct from a known free/paid plan.
        return {'schema_version': 1, 'status': 'unavailable', 'connected': False,
                'can_checkout': False, 'can_manage': False, 'can_cancel': False,
                'plan': None, 'paid_until': None, 'cancel_at_period_end': None}

    def checkout(self, *, authenticated_account_id: str) -> str:
        raise BillingUnavailable('BILLING_NOT_CONNECTED')

    def portal(self, *, authenticated_account_id: str) -> str:
        raise BillingUnavailable('BILLING_NOT_CONNECTED')

    def cancel_at_period_end(self, *, authenticated_account_id: str) -> None:
        raise BillingUnavailable('BILLING_NOT_CONNECTED')

    def dashboard_snapshot(self) -> dict:
        """Aggregate-only seam. No customer IDs, emails, portal links or raw events.

        Future schema groups by currency and separates sales, tax, fees, refunds,
        accrued net, MRR and settled payouts. Empty is unknown, never zero revenue.
        """
        return {'schema_version': 1, 'source': 'unconnected', 'connected': False,
                'observed_at': None, 'coverage': None, 'currencies': []}
