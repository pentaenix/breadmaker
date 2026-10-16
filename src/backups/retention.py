from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# 7.7.5 recovery retention is intentionally independent from Revision History.
# Generation is always the ordering authority. Dates are grouping metadata only.

@dataclass(frozen=True)
class RecoveryRetentionPolicy:
    name: str
    current_workday: int
    previous_workdays: int
    weekly: int
    monthly: int
    hard_cap: int

RECOVERY_RETENTION_POLICIES: dict[str, RecoveryRetentionPolicy] = {
    'compact': RecoveryRetentionPolicy('compact', 2, 7, 4, 6, 19),
    'standard': RecoveryRetentionPolicy('standard', 3, 20, 8, 12, 43),
    'extended': RecoveryRetentionPolicy('extended', 5, 45, 16, 24, 90),
}
DEFAULT_RECOVERY_RETENTION_PROFILE = 'standard'

def normalize_recovery_retention_profile(value: object) -> str:
    key = str(value or DEFAULT_RECOVERY_RETENTION_PROFILE).strip().lower()
    if key not in RECOVERY_RETENTION_POLICIES:
        raise ValueError('unsupported recovery retention profile')
    return key


def _parse_time(value: object) -> datetime:
    raw = str(value or '').strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def retention_authority_key(row: dict[str, Any]) -> tuple[int, datetime, str]:
    """Order published recovery states by canonical generation, never clock first."""
    try:
        generation = max(0, int(row.get('project_generation') or 0))
    except (TypeError, ValueError):
        generation = 0
    return (generation, _parse_time(row.get('created_at')), str(row.get('file') or ''))


def _workday(row: dict[str, Any]) -> str:
    # Current packages/publications carry an explicit canonical workday. If a
    # malformed/incomplete candidate lacks it, creation time is grouping metadata only;
    # generation remains the ordering authority.
    explicit = str(row.get('workday') or '').strip()
    if explicit:
        try:
            return datetime.fromisoformat(explicit[:10]).date().isoformat()
        except ValueError:
            pass
    metadata = row.get('recovery_metadata') if isinstance(row.get('recovery_metadata'), dict) else {}
    explicit = str(metadata.get('workday') or '').strip()
    if explicit:
        try:
            return datetime.fromisoformat(explicit[:10]).date().isoformat()
        except ValueError:
            pass
    return _parse_time(row.get('created_at')).date().isoformat()


def recovery_retention_partition(rows: list[dict[str, Any]], profile: str = DEFAULT_RECOVERY_RETENTION_PROFILE) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (keep, prune) for already-PUBLISHED recovery points.

    Correctness comes only from generation. Workday/week/month grouping affects which
    older immutable publications are retained, never which state is newest.
    """
    policy = RECOVERY_RETENTION_POLICIES[normalize_recovery_retention_profile(profile)]
    ordered = sorted((dict(row) for row in rows), key=retention_authority_key, reverse=True)
    if not ordered:
        return [], []

    current_workday = _workday(ordered[0])
    keep: list[dict[str, Any]] = []
    current_count = 0
    recent_days: set[str] = set()
    weekly: set[tuple[int, int]] = set()
    monthly: set[tuple[int, int]] = set()

    # Distinct active workdays are defined by actual recovery points, not elapsed
    # calendar days. No writing therefore consumes no "recent day" slot.
    active_days: list[str] = []
    for row in ordered:
        day = _workday(row)
        if day != current_workday and day not in active_days:
            active_days.append(day)
    recent_day_set = set(active_days[:policy.previous_workdays])

    for row in ordered:
        day = _workday(row)
        selected = False
        if day == current_workday:
            if current_count < policy.current_workday:
                selected = True
                current_count += 1
        elif day in recent_day_set:
            if day not in recent_days:
                selected = True
                recent_days.add(day)
        else:
            stamp = _parse_time(day)
            iso = stamp.isocalendar()
            week_key = (int(iso.year), int(iso.week))
            month_key = (stamp.year, stamp.month)
            if len(weekly) < policy.weekly and week_key not in weekly:
                selected = True
                weekly.add(week_key)
            elif len(monthly) < policy.monthly and month_key not in monthly:
                selected = True
                monthly.add(month_key)
        if selected and len(keep) < policy.hard_cap:
            keep.append(row)

    keep_ids = {id(row) for row in keep}
    prune = [row for row in ordered if id(row) not in keep_ids]
    return keep, prune
