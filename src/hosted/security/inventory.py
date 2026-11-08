"""Source-only, default-disabled operational inventory. No runtime entry point.

The fixed ``cloud_operational_inventory_v1`` definition counts root ``users``
with explicit ``status == active`` and root ``projects`` with the same status.
These are persisted application accounts and current published project roots,
not distinct people, online users, lifetime creations, or active-owner joins.
Missing status, staged imports, erased and deleted roots are excluded. Some
legacy runtime paths treat missing status as active; this definition does not.

Only provider COUNT aggregate results are read. No documents, identifiers,
email addresses, manuscript bodies, collection-group scans or mutations occur.
The two queries are sequential, NOT one atomic snapshot. Coverage is the local
UTC observation window; each provider read_time must fall inside that window.
``complete`` means both fixed queries completed, not coverage of all usage.

Explicit injection and ``enabled=True`` are mechanical gates, not permission:
privacy notice, operator authorization, IAM and transport/storage review must
precede any live wiring. This module creates no client, reads no environment,
logs no provider errors and writes nothing. Importing it never loads Google.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

SOURCE = "cloud_operational_inventory_v1"
MAX_COUNT = 9_007_199_254_740_991
MAX_PAYLOAD_BYTES = 16_384
MAX_READ_SECONDS = 60
MAX_AGE_SECONDS = 900
_QUERY_TIMEOUT_SECONDS = 20.0
_ROOT_COLLECTIONS = ("users", "projects")
Completeness = Literal["complete", "partial", "unknown"]


class InventoryValidationError(ValueError):
    """Structural errors only; never include provider messages or user data."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise InventoryValidationError("HOSTED_INVENTORY_TIME_INVALID")
    return value.astimezone(timezone.utc)


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise InventoryValidationError("HOSTED_INVENTORY_COUNT_INVALID")
    return value


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class InventorySnapshot:
    """Bounded, content-free result; export rechecks freshness without caching."""

    start: datetime
    end: datetime
    completeness: Completeness = "unknown"
    active_cloud_accounts: int | None = None
    current_cloud_projects: int | None = None

    def __post_init__(self) -> None:
        start, end = _utc(self.start), _utc(self.end)
        if end < start:
            raise InventoryValidationError("HOSTED_INVENTORY_WINDOW_INVALID")
        if self.completeness not in ("complete", "partial", "unknown"):
            raise InventoryValidationError("HOSTED_INVENTORY_COVERAGE_INVALID")
        counts = (self.active_cloud_accounts, self.current_cloud_projects)
        if self.completeness == "complete":
            for value in counts:
                _count(value)
        elif any(value is not None for value in counts):
            raise InventoryValidationError("HOSTED_INVENTORY_INCOMPLETE_VALUES")

    def json_bytes(self, *, now: datetime | None = None) -> bytes:
        """Return the exact shared schema; stale/future/slow data becomes null.

        Preserve the original timestamps, so a consumer can independently reject
        stale/future/overlong observations. Exporting does not refresh a count.
        """
        observed_now = _utc(_utc_now() if now is None else now)
        completeness = self.completeness
        if (self.end > observed_now
                or (observed_now - self.end).total_seconds() > MAX_AGE_SECONDS
                or (self.end - self.start).total_seconds() > MAX_READ_SECONDS):
            completeness = "unknown"
        available = completeness == "complete"
        payload = {
            "schema_version": 1,
            "source": SOURCE,
            "as_of": _stamp(self.end),
            "coverage": {
                "start": _stamp(self.start),
                "end": _stamp(self.end),
                "completeness": completeness,
                "consistency": "non_atomic",
            },
            "metrics": {
                "active_cloud_accounts": self.active_cloud_accounts if available else None,
                "current_cloud_projects": self.current_cloud_projects if available else None,
            },
        }
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise InventoryValidationError("HOSTED_INVENTORY_PAYLOAD_TOO_LARGE")
        return encoded


def _active_status_filter() -> Any:
    # Lazy import: offline/local Editor does not need the hosted SDK.
    from google.cloud.firestore_v1.base_query import FieldFilter
    return FieldFilter("status", "==", "active")


def _active_root_count(client: Any, collection: str) -> tuple[int, datetime]:
    """Internal fixed-root COUNT only. Never fall back to document reads."""
    if collection not in _ROOT_COLLECTIONS:
        raise InventoryValidationError("HOSTED_INVENTORY_COLLECTION_INVALID")
    query = client.collection(collection).where(filter=_active_status_filter())
    # This is an AGGREGATION stream, not a document Query.stream(). Limit
    # consumption to two rows and reject anything but one one-column result.
    # Explicit deadline and no retry prevent SDK default backoff amplification.
    stream = query.count(alias="total").stream(retry=None, timeout=_QUERY_TIMEOUT_SECONDS)
    try:
        iterator = iter(stream)
        missing = object()
        row = next(iterator, missing)
        if not isinstance(row, (list, tuple)) or len(row) != 1:
            raise InventoryValidationError("HOSTED_INVENTORY_RESPONSE_INVALID")
        result = row[0]
        if getattr(result, "alias", None) != "total":
            raise InventoryValidationError("HOSTED_INVENTORY_RESPONSE_INVALID")
        value = _count(getattr(result, "value", None))
        read_time = _utc(getattr(result, "read_time", None))
        if next(iterator, missing) is not missing:
            raise InventoryValidationError("HOSTED_INVENTORY_RESPONSE_INVALID")
        return value, read_time
    finally:
        stream.close()


class GoogleFirestoreInventoryProducer:
    """Opt-in in-memory producer. No credentials, scheduler, endpoint or writer.

    The injected client must already target the authorized hosted database. The
    caller cannot supply a collection, filter, path, query or metric name. A
    failed query invalidates the entire pair, never serving an old good count.
    """

    def __init__(
        self, *, client: Any = None, enabled: bool = False,
        utc_now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(enabled) is not bool:
            raise InventoryValidationError("HOSTED_INVENTORY_ENABLE_INVALID")
        self._client = client
        self._enabled = enabled
        self._utc_now = utc_now
        self._monotonic = monotonic

    def collect(self) -> InventorySnapshot:
        start = _utc(self._utc_now())
        if not self._enabled or self._client is None:
            return InventorySnapshot(start=start, end=start)
        started = self._monotonic()
        results: list[tuple[int, datetime]] = []
        for collection in _ROOT_COLLECTIONS:
            elapsed = self._monotonic() - started
            if not 0 <= elapsed <= MAX_READ_SECONDS - _QUERY_TIMEOUT_SECONDS:
                break
            try:
                results.append(_active_root_count(self._client, collection))
            except Exception:
                # SDK/permissions/network/shape failures have no public details.
                # No exception text, document fallback, retry or last-good cache.
                continue
        end = _utc(self._utc_now())
        elapsed = self._monotonic() - started
        if (not 0 <= elapsed <= MAX_READ_SECONDS or end < start
                or (end - start).total_seconds() > MAX_READ_SECONDS
                or any(not start <= read_time <= end for _, read_time in results)):
            # A backwards wall clock cannot describe a truthful ordered window.
            if end < start:
                raise InventoryValidationError("HOSTED_INVENTORY_CLOCK_REVERSED")
            return InventorySnapshot(start=start, end=end)
        if len(results) != len(_ROOT_COLLECTIONS):
            return InventorySnapshot(
                start=start, end=end, completeness="partial" if results else "unknown",
            )
        return InventorySnapshot(
            start=start, end=end, completeness="complete",
            active_cloud_accounts=results[0][0], current_cloud_projects=results[1][0],
        )
