"""Default-disabled, one-shot publication of the two-count inventory contract.

No runtime composition, environment settings, client construction, credential
loading, scheduler or logging. An approved future caller must inject the fixed
store and existing producer. Enabled is a mechanical switch, not permission.
Read current first, collect, require a strictly newer observation, then CAS the
same generation. No retry, timestamp rewriting, history or last-good fallback.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .inventory import InventorySnapshot, MAX_AGE_SECONDS, MAX_PAYLOAD_BYTES, MAX_READ_SECONDS, SOURCE

OBJECT_NAME = "cloud-usage/v1/latest.json"
STORE_SECONDS = 5.0
PUBLICATION_SECONDS = 75.0
MAX_STORAGE_GENERATION = (1 << 64) - 1


class InventoryPublicationError(ValueError):
    pass


class InventoryWriteConflict(InventoryPublicationError):
    pass


class InventoryWriteUncertain(InventoryPublicationError):
    pass


@dataclass(frozen=True)
class InventoryResource:
    bucket: str

    def __post_init__(self):
        if (type(self.bucket) is not str or len(self.bucket) > 63
                or not re.fullmatch(r"breadmaker-admin-usage-[a-z0-9]+(?:-[a-z0-9]+)*", self.bucket)):
            raise InventoryPublicationError("INVENTORY_RESOURCE_INVALID")


def require_generation(value, *, allow_zero=False):
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= MAX_STORAGE_GENERATION:
        raise InventoryPublicationError("INVENTORY_STORAGE_GENERATION_INVALID")
    return value


@dataclass(frozen=True)
class VersionedInventory:
    generation: int
    payload: bytes = field(repr=False)

    def __post_init__(self):
        require_generation(self.generation)
        if type(self.payload) is not bytes or not 0 < len(self.payload) <= MAX_PAYLOAD_BYTES:
            raise InventoryPublicationError("INVENTORY_PAYLOAD_INVALID")


class LatestInventoryStore(Protocol):
    def read_latest(self, *, timeout: float) -> VersionedInventory | None: ...
    def replace_latest(self, payload: bytes, *, if_generation_match: int, timeout: float) -> None: ...


class InventoryProducer(Protocol):
    def collect(self) -> InventorySnapshot: ...


def utc_now():
    return datetime.now(timezone.utc)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise InventoryPublicationError("INVENTORY_TIME_INVALID")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryPublicationError("INVENTORY_PAYLOAD_INVALID")
        result[key] = value
    return result


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise InventoryPublicationError("INVENTORY_PAYLOAD_INVALID")


def _stamp(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise InventoryPublicationError("INVENTORY_TIME_INVALID")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def decode_inventory(payload: bytes, *, now: datetime) -> InventorySnapshot:
    """Strict shared schema; existing stale objects can still fence ordering.

    No extra fields or duplicate keys, non-integer counts, non-UTC time, future
    observations or malformed windows. Incomplete records must contain nulls.
    """
    try:
        if type(payload) is not bytes or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
            raise ValueError()
        data = json.loads(payload, object_pairs_hook=_object)
        _exact(data, ("schema_version", "source", "as_of", "coverage", "metrics"))
        if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["source"] != SOURCE:
            raise ValueError()
        coverage, metrics = data["coverage"], data["metrics"]
        _exact(coverage, ("start", "end", "completeness", "consistency"))
        _exact(metrics, ("active_cloud_accounts", "current_cloud_projects"))
        start, end = _stamp(coverage["start"]), _stamp(coverage["end"])
        if (data["as_of"] != coverage["end"] or coverage["consistency"] != "non_atomic"
                or end > _utc(now) or (end - start).total_seconds() > MAX_READ_SECONDS):
            raise ValueError()
        return InventorySnapshot(start, end, coverage["completeness"],
                                 metrics["active_cloud_accounts"], metrics["current_cloud_projects"])
    except Exception:
        raise InventoryPublicationError("INVENTORY_PAYLOAD_INVALID") from None


class InventoryPublication:
    """One explicit call, at most one write. Disabled touches no dependencies.

    RPC limits require injected dependencies to respect deadlines. The elapsed
    guard prevents a late callback from starting a write; it cannot forcibly
    cancel arbitrary Python callbacks. No background thread or retry is spawned.
    """
    def __init__(self, producer=None, store=None, *, enabled=False, now=utc_now, monotonic=time.monotonic):
        if type(enabled) is not bool or not callable(now) or not callable(monotonic):
            raise InventoryPublicationError("INVENTORY_PUBLICATION_CONFIGURATION_INVALID")
        self._producer, self._store, self._enabled = producer, store, enabled
        self._now, self._clock = now, monotonic

    def publish_once(self) -> str:
        if not self._enabled:
            return "disabled"
        if self._producer is None or self._store is None:
            return "unavailable"
        try:
            started = self._clock()

            def remaining():
                elapsed = self._clock() - started
                if not 0 <= elapsed < PUBLICATION_SECONDS:
                    raise InventoryPublicationError("INVENTORY_PUBLICATION_EXPIRED")
                return min(STORE_SECONDS, PUBLICATION_SECONDS - elapsed)

            current = self._store.read_latest(timeout=remaining())
            previous = None
            if current is not None:
                if type(current) is not VersionedInventory:
                    raise InventoryPublicationError("INVENTORY_CURRENT_INVALID")
                previous = decode_inventory(current.payload, now=self._now())
            remaining()
            snapshot = self._producer.collect()
            if type(snapshot) is not InventorySnapshot:
                raise InventoryPublicationError("INVENTORY_SNAPSHOT_INVALID")
            now = _utc(self._now())
            payload = snapshot.json_bytes(now=now)
            candidate = decode_inventory(payload, now=now)
            if (now - candidate.end).total_seconds() > MAX_AGE_SECONDS:
                return "unavailable"
            if previous is not None and candidate.end <= previous.end:
                return "not_newer"
            write_timeout = remaining()
            try:
                self._store.replace_latest(payload, if_generation_match=current.generation if current else 0,
                                           timeout=write_timeout)
                remaining()
            except InventoryWriteConflict:
                return "conflict"
            except Exception:
                # The provider may have committed before a timeout. No retry.
                return "uncertain"
            return "published"
        except Exception:
            # Never expose payloads, exception strings, resource names or IDs.
            return "unavailable"
