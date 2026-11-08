"""Synthetic only. Written for owner/CI execution; never queries a provider."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hosted.security import inventory

START = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=2)


def _row(value, *, alias="total", read_time=START):
    return [SimpleNamespace(alias=alias, value=value, read_time=read_time)]


class AggregateStream:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        self.consumed += 1
        return next(self.rows)

    def close(self):
        self.closed = True


class SyntheticClient:
    """Only aggregation is supported; document APIs deliberately do not exist."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.streams = []

    def collection(self, name):
        self.calls.append(("collection", name))
        client = self

        class Query:
            def where(self, *, filter):
                client.calls.append(("where", filter))
                return self

            def count(self, *, alias):
                client.calls.append(("count", alias))

                class Aggregate:
                    def stream(self, *, retry, timeout):
                        client.calls.append(("aggregate", retry, timeout))
                        response = client.responses[name]
                        if isinstance(response, Exception):
                            raise response
                        stream = AggregateStream(response)
                        client.streams.append(stream)
                        return stream

                return Aggregate()

        return Query()


def _producer(monkeypatch, responses, *, end=END, enabled=True, monotonic=lambda: 0.0):
    monkeypatch.setattr(inventory, "_active_status_filter", lambda: ("status", "==", "active"))
    client = SyntheticClient(responses)
    clock = iter((START, end))
    producer = inventory.GoogleFirestoreInventoryProducer(
        client=client, enabled=enabled, utc_now=lambda: next(clock), monotonic=monotonic,
    )
    return producer, client


def _payload(snapshot, *, now=END):
    return json.loads(snapshot.json_bytes(now=now))


def test_default_disabled_never_touches_sdk_or_client(monkeypatch):
    def forbidden():
        raise AssertionError("must not load SDK")

    monkeypatch.setattr(inventory, "_active_status_filter", forbidden)
    producer = inventory.GoogleFirestoreInventoryProducer(client=object(), utc_now=lambda: START)
    payload = _payload(producer.collect())
    assert payload["coverage"]["completeness"] == "unknown"
    assert set(payload["metrics"].values()) == {None}


def test_exact_shared_contract_and_fixed_aggregate_queries(monkeypatch):
    producer, client = _producer(monkeypatch, {"users": [_row(7)], "projects": [_row(12)]})
    snapshot = producer.collect()
    assert _payload(snapshot) == {
        "schema_version": 1,
        "source": "cloud_operational_inventory_v1",
        "as_of": "2026-11-08T12:00:02Z",
        "coverage": {"start": "2026-11-08T12:00:00Z", "end": "2026-11-08T12:00:02Z",
                     "completeness": "complete", "consistency": "non_atomic"},
        "metrics": {"active_cloud_accounts": 7, "current_cloud_projects": 12},
    }
    assert client.calls == [
        ("collection", "users"), ("where", ("status", "==", "active")),
        ("count", "total"), ("aggregate", None, 20.0),
        ("collection", "projects"), ("where", ("status", "==", "active")),
        ("count", "total"), ("aggregate", None, 20.0),
    ]
    assert all(stream.closed for stream in client.streams)
    assert len(snapshot.json_bytes(now=END)) <= 16_384


@pytest.mark.parametrize("count", [0, 9_007_199_254_740_991])
def test_true_zero_and_largest_safe_integer_are_available(monkeypatch, count):
    producer, _ = _producer(monkeypatch, {"users": [_row(count)], "projects": [_row(count)]})
    assert set(_payload(producer.collect())["metrics"].values()) == {count}


@pytest.mark.parametrize("value", [True, False, -1, 1.0, float("nan"), float("inf"),
                                   "3", None, 9_007_199_254_740_992, {"email": "synthetic"}])
def test_invalid_counts_suppress_both_values(monkeypatch, value):
    producer, _ = _producer(monkeypatch, {"users": [_row(value)], "projects": [_row(4)]})
    payload = _payload(producer.collect())
    assert payload["coverage"]["completeness"] == "partial"
    assert set(payload["metrics"].values()) == {None}


@pytest.mark.parametrize("rows", [[], [[]], [_row(3) + _row(4)], [_row(3), _row(4)],
                                  [_row(3, alias="email")], [{"total": 3}]])
def test_invalid_response_shape_is_not_a_count(monkeypatch, rows):
    producer, _ = _producer(monkeypatch, {"users": rows, "projects": [_row(4)]})
    assert set(_payload(producer.collect())["metrics"].values()) == {None}


def test_aggregate_response_consumption_is_bounded_and_closed(monkeypatch):
    producer, client = _producer(monkeypatch, {"users": [_row(3)] * 100, "projects": [_row(4)]})
    assert _payload(producer.collect())["coverage"]["completeness"] == "partial"
    assert client.streams[0].consumed == 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.parametrize("read_time", [None, "2026-11-08T12:00:00Z", START.replace(tzinfo=None),
                                       START - timedelta(seconds=1), END + timedelta(seconds=1)])
def test_missing_stale_future_provider_time_never_publishes_values(monkeypatch, read_time):
    producer, _ = _producer(monkeypatch, {"users": [_row(3, read_time=read_time)], "projects": [_row(4)]})
    assert set(_payload(producer.collect())["metrics"].values()) == {None}


@pytest.mark.parametrize("failed", ["users", "projects", "both"])
def test_permission_network_failures_are_sanitized_without_zero_or_cache(monkeypatch, failed):
    responses = {"users": [_row(7)], "projects": [_row(12)]}
    for name in ("users", "projects"):
        if failed in (name, "both"):
            responses[name] = RuntimeError("sensitive provider text")
    producer, _ = _producer(monkeypatch, responses)
    snapshot = producer.collect()
    payload = _payload(snapshot)
    assert set(payload["metrics"].values()) == {None}
    assert payload["coverage"]["completeness"] == ("unknown" if failed == "both" else "partial")
    assert b"sensitive" not in snapshot.json_bytes(now=END)


@pytest.mark.parametrize("now", [END + timedelta(seconds=901), START])
def test_export_nulls_stale_or_future_values_without_refreshing_timestamp(now):
    snapshot = inventory.InventorySnapshot(START, END, "complete", 7, 12)
    payload = _payload(snapshot, now=now)
    assert payload["coverage"]["completeness"] == "unknown"
    assert payload["as_of"] == "2026-11-08T12:00:02Z"
    assert set(payload["metrics"].values()) == {None}


def test_freshness_boundary_is_inclusive():
    snapshot = inventory.InventorySnapshot(START, END, "complete", 7, 12)
    assert _payload(snapshot, now=END + timedelta(seconds=900))["metrics"]["active_cloud_accounts"] == 7


def test_overlong_observation_is_unknown(monkeypatch):
    end = START + timedelta(seconds=61)
    producer, _ = _producer(monkeypatch, {"users": [_row(3)], "projects": [_row(4)]}, end=end)
    payload = _payload(producer.collect(), now=end)
    assert payload["coverage"]["completeness"] == "unknown"
    assert set(payload["metrics"].values()) == {None}


def test_monotonic_budget_stops_second_query_and_invalidates_pair(monkeypatch):
    ticks = iter((0.0, 0.0, 61.0, 61.0))
    producer, client = _producer(monkeypatch, {"users": [_row(3)], "projects": [_row(4)]},
                                monotonic=lambda: next(ticks))
    assert _payload(producer.collect())["coverage"]["completeness"] == "unknown"
    assert [call for call in client.calls if call[0] == "collection"] == [("collection", "users")]


def test_backward_clock_fails_structurally(monkeypatch):
    producer, _ = _producer(monkeypatch, {"users": [_row(3)], "projects": [_row(4)]},
                             end=START - timedelta(seconds=1))
    with pytest.raises(inventory.InventoryValidationError, match="CLOCK_REVERSED"):
        producer.collect()


@pytest.mark.parametrize("enabled", [1, "true", None])
def test_enable_requires_explicit_boolean(enabled):
    with pytest.raises(inventory.InventoryValidationError, match="ENABLE_INVALID"):
        inventory.GoogleFirestoreInventoryProducer(enabled=enabled)


def test_partial_snapshot_cannot_carry_values():
    with pytest.raises(inventory.InventoryValidationError, match="INCOMPLETE_VALUES"):
        inventory.InventorySnapshot(START, END, "partial", 7, None)


def test_fixed_helper_rejects_any_other_collection_before_client_access():
    with pytest.raises(inventory.InventoryValidationError, match="COLLECTION_INVALID"):
        inventory._active_root_count(object(), "projects/synthetic/manuscript")


def test_lazy_sdk_filter_is_fixed_status_predicate(monkeypatch):
    seen = []
    module = SimpleNamespace(FieldFilter=lambda *args: seen.append(args) or args)
    monkeypatch.setitem(sys.modules, "google.cloud.firestore_v1.base_query", module)
    assert inventory._active_status_filter() == ("status", "==", "active")
    assert seen == [("status", "==", "active")]


def test_next_failed_collection_does_not_reuse_last_good_values(monkeypatch):
    producer, client = _producer(monkeypatch, {"users": [_row(7)], "projects": [_row(12)]})
    ticks = iter((START, END, START, END))
    producer._utc_now = lambda: next(ticks)
    assert _payload(producer.collect())["metrics"]["active_cloud_accounts"] == 7
    client.responses["users"] = RuntimeError("synthetic failure")
    assert set(_payload(producer.collect())["metrics"].values()) == {None}


def test_disabled_and_missing_client_are_unavailable_not_zero(monkeypatch):
    producer, client = _producer(monkeypatch, {}, enabled=False)
    assert set(_payload(producer.collect())["metrics"].values()) == {None}
    assert client.calls == []
    missing = inventory.GoogleFirestoreInventoryProducer(enabled=True, utc_now=lambda: START)
    assert set(_payload(missing.collect())["metrics"].values()) == {None}
