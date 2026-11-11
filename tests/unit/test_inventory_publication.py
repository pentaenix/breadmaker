"""Synthetic publication and SDK-shape tests; no credentials or provider calls."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hosted.security.inventory import InventorySnapshot, MAX_PAYLOAD_BYTES
from hosted.security.inventory_publication import (
    InventoryPublication, InventoryPublicationError, InventoryResource, InventoryWriteConflict,
    InventoryWriteUncertain, VersionedInventory, decode_inventory, OBJECT_NAME,
)
from hosted.security.inventory_gcs import GoogleInventoryLatestStore
from hosted.security import inventory_gcs

NOW = datetime(2026, 11, 10, 12, 0, 10, tzinfo=timezone.utc)
RESOURCE = InventoryResource("breadmaker-admin-usage-synthetic")


def snapshot(seconds=0, *, completeness="complete", accounts=7, projects=12):
    end = NOW + timedelta(seconds=seconds)
    return InventorySnapshot(end - timedelta(seconds=2), end, completeness, accounts, projects)


def encoded(seconds=0, **kwargs):
    return snapshot(seconds, **kwargs).json_bytes(now=NOW + timedelta(seconds=max(seconds, 0)))


class Producer:
    def __init__(self, value=None):
        self.value, self.calls = value or snapshot(), 0

    def collect(self):
        self.calls += 1
        return self.value


class MemoryStore:
    def __init__(self, current=None):
        self.current, self.writes, self.reads = current, [], 0

    def read_latest(self, *, timeout):
        assert 0 < timeout <= 5
        self.reads += 1
        return self.current

    def replace_latest(self, payload, *, if_generation_match, timeout):
        assert 0 < timeout <= 5
        self.writes.append((payload, if_generation_match))
        expected = self.current.generation if self.current else 0
        if if_generation_match != expected:
            raise InventoryWriteConflict("SYNTHETIC_CONFLICT")
        self.current = VersionedInventory(expected + 1, payload)


def publish(store, producer=None, **kwargs):
    return InventoryPublication(producer or Producer(), store, enabled=True, now=lambda: NOW, **kwargs).publish_once()


def test_default_disabled_touches_nothing():
    assert InventoryPublication(object(), object()).publish_once() == "disabled"


def test_initial_publication_is_create_only_and_exact_schema():
    store = MemoryStore()
    assert publish(store) == "published"
    assert store.writes == [(encoded(), 0)]
    assert decode_inventory(store.current.payload, now=NOW).active_cloud_accounts == 7


def test_newer_snapshot_replaces_exact_observed_generation():
    store = MemoryStore(VersionedInventory(19, encoded(-3)))
    assert publish(store) == "published"
    assert store.writes == [(encoded(), 19)]


@pytest.mark.parametrize("seconds", [0, 1, 2])
def test_equal_or_newer_current_snapshot_is_not_overwritten(seconds):
    store = MemoryStore(VersionedInventory(19, encoded(-seconds)))
    assert publish(store, Producer(snapshot(-2))) == "not_newer"
    assert store.writes == []


def test_concurrent_generation_change_returns_conflict_without_retry():
    class RaceStore(MemoryStore):
        def replace_latest(self, *args, **kwargs):
            self.current = VersionedInventory(20, encoded())
            return super().replace_latest(*args, **kwargs)

    store = RaceStore(VersionedInventory(19, encoded(-3)))
    producer = Producer()
    assert publish(store, producer) == "conflict"
    assert store.reads == producer.calls == len(store.writes) == 1
    assert store.current.generation == 20


def test_unknown_observation_invalidates_previous_good_counts():
    store = MemoryStore(VersionedInventory(19, encoded(-3)))
    assert publish(store, Producer(snapshot(completeness="unknown", accounts=None, projects=None))) == "published"
    data = json.loads(store.current.payload)
    assert data["coverage"]["completeness"] == "unknown"
    assert set(data["metrics"].values()) == {None}


@pytest.mark.parametrize("seconds", [-901, 1])
def test_stale_or_future_candidate_never_writes(seconds):
    store = MemoryStore()
    assert publish(store, Producer(snapshot(seconds))) == "unavailable"
    assert store.writes == []


def test_unparseable_current_object_blocks_collection_and_write():
    store, producer = MemoryStore(VersionedInventory(1, b'{"email":"synthetic"}')), Producer()
    assert publish(store, producer) == "unavailable"
    assert producer.calls == 0 and store.writes == []


def test_provider_read_failure_does_not_collect_or_expose_error():
    class Broken(MemoryStore):
        def read_latest(self, **kwargs):
            raise RuntimeError("sensitive provider payload")

    producer = Producer()
    assert publish(Broken(), producer) == "unavailable"
    assert producer.calls == 0


def test_write_timeout_is_uncertain_and_not_retried():
    class Broken(MemoryStore):
        def replace_latest(self, *args, **kwargs):
            super().replace_latest(*args, **kwargs)
            raise RuntimeError("sensitive provider payload")

    store = Broken()
    assert publish(store) == "uncertain"
    assert len(store.writes) == 1 and store.current is not None


def test_expired_callback_does_not_start_write():
    ticks = iter((0, 0, 0, 76))
    store = MemoryStore()
    assert publish(store, monotonic=lambda: next(ticks)) == "unavailable"
    assert store.writes == []


@pytest.mark.parametrize("transform", [
    lambda data: {**data, "email": "synthetic"},
    lambda data: {**data, "schema_version": True},
    lambda data: {**data, "metrics": {**data["metrics"], "words": 50}},
    lambda data: {**data, "metrics": {"active_cloud_accounts": True, "current_cloud_projects": 1}},
    lambda data: {**data, "metrics": {"active_cloud_accounts": 1.0, "current_cloud_projects": 1}},
    lambda data: {**data, "metrics": {"active_cloud_accounts": -1, "current_cloud_projects": 1}},
    lambda data: {**data, "coverage": {**data["coverage"], "completeness": "partial"}},
    lambda data: {**data, "as_of": "2026-11-10T12:00:10+00:00"},
    lambda data: {**data, "coverage": {**data["coverage"], "consistency": "atomic"}},
])
def test_closed_wire_schema_rejects_extras_and_invalid_types(transform):
    raw = json.dumps(transform(json.loads(encoded()))).encode()
    with pytest.raises(InventoryPublicationError, match="PAYLOAD_INVALID"):
        decode_inventory(raw, now=NOW)


@pytest.mark.parametrize("raw", [b"{}", b'{"source":1,"source":2}', b"x" * (MAX_PAYLOAD_BYTES + 1)])
def test_malformed_duplicate_and_oversize_bytes_rejected(raw):
    with pytest.raises(InventoryPublicationError, match="PAYLOAD_INVALID"):
        decode_inventory(raw, now=NOW)


class FakePrecondition(Exception):
    pass


def md5(data):
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode("ascii")


class Bucket:
    name = RESOURCE.bucket
    client = SimpleNamespace(_connection=SimpleNamespace(API_BASE_URL="https://storage.googleapis.com"))

    def __init__(self, payload=None):
        self.payload, self.generation, self.calls = payload, 19 if payload else 0, []
        self.fail_upload = False

    def get_blob(self, name, **kwargs):
        self.calls.append(("get", name, kwargs))
        return Blob(self, name) if self.payload is not None else None

    def blob(self, name):
        self.calls.append(("blob", name))
        return Blob(self, name)


class Blob:
    content_type = "application/json"
    content_encoding = None
    metadata = None

    def __init__(self, bucket, name):
        self.bucket, self.name, self.generation = bucket, name, bucket.generation
        self.size = len(bucket.payload) if bucket.payload is not None else 0
        self.md5_hash = md5(bucket.payload) if bucket.payload is not None else None

    def download_as_bytes(self, **kwargs):
        self.bucket.calls.append(("download", self.name, kwargs))
        if kwargs["if_generation_match"] != self.bucket.generation:
            raise FakePrecondition()
        return self.bucket.payload

    def upload_from_string(self, payload, **kwargs):
        self.bucket.calls.append(("upload", self.name, kwargs, self.cache_control, self.metadata, self.md5_hash))
        if kwargs["if_generation_match"] != self.bucket.generation:
            raise FakePrecondition()
        self.bucket.generation += 1
        self.bucket.payload = payload
        self.generation, self.size = self.bucket.generation, len(payload)
        if self.bucket.fail_upload:
            raise RuntimeError("sensitive upload payload")


def adapter(bucket, **kwargs):
    return GoogleInventoryLatestStore(RESOURCE, bucket=bucket, now=lambda: NOW, **kwargs)


def test_sdk_read_is_fixed_bounded_generation_pinned_raw_and_retry_disabled():
    bucket = Bucket(encoded())
    result = adapter(bucket).read_latest(timeout=5)
    assert result == VersionedInventory(19, encoded())
    assert bucket.calls[0][0:2] == ("get", OBJECT_NAME)
    call = bucket.calls[1]
    assert call[:2] == ("download", OBJECT_NAME)
    assert call[2]["if_generation_match"] == 19
    assert call[2]["start"] == 0 and call[2]["end"] == len(encoded())
    assert call[2]["raw_download"] is True and call[2]["single_shot_download"] is True
    assert call[2]["retry"] is None and call[2]["checksum"] is None
    assert 0 < call[2]["timeout"] <= 5


def test_sdk_write_is_fixed_conditional_noncacheable_and_has_no_custom_metadata():
    bucket = Bucket()
    assert publish(adapter(bucket)) == "published"
    call = next(call for call in bucket.calls if call[0] == "upload")
    assert call[1] == OBJECT_NAME
    assert call[2]["if_generation_match"] == 0 and call[2]["retry"] is None
    assert call[2]["content_type"] == "application/json" and call[2]["checksum"] is None
    assert call[3:] == ("no-store, max-age=0", None, md5(encoded()))


def test_sdk_race_and_uncertain_upload_are_distinct_sanitized(monkeypatch):
    monkeypatch.setattr(inventory_gcs, "_is_conflict", lambda error: isinstance(error, FakePrecondition))
    bucket = Bucket(encoded(-3))
    with pytest.raises(InventoryWriteConflict, match="WRITE_CONFLICT"):
        adapter(bucket).replace_latest(encoded(), if_generation_match=18, timeout=5)
    bucket.fail_upload = True
    with pytest.raises(InventoryWriteUncertain, match="WRITE_UNCERTAIN") as caught:
        adapter(bucket).replace_latest(encoded(), if_generation_match=19, timeout=5)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("attribute,value", [("size", MAX_PAYLOAD_BYTES + 1), ("generation", 0),
    ("content_encoding", "gzip"), ("metadata", {"email": "synthetic"}), ("content_type", "text/plain")])
def test_bad_metadata_blocks_download(monkeypatch, attribute, value):
    bucket = Bucket(encoded())
    blob = Blob(bucket, OBJECT_NAME)
    setattr(blob, attribute, value)
    monkeypatch.setattr(bucket, "get_blob", lambda *args, **kwargs: blob)
    with pytest.raises(InventoryPublicationError, match="STORE_UNAVAILABLE"):
        adapter(bucket).read_latest(timeout=5)
    assert bucket.calls == []


def test_metadata_budget_expiry_blocks_download(monkeypatch):
    bucket = Bucket(encoded())
    ticks = iter((0.0, 0.0, 6.0))
    with pytest.raises(InventoryPublicationError, match="STORE_UNAVAILABLE"):
        adapter(bucket, monotonic=lambda: next(ticks)).read_latest(timeout=5)
    assert len(bucket.calls) == 1


@pytest.mark.parametrize("name", ["manuscripts", "breadmaker-admin-usage-", "breadmaker-admin-usage-a/path"])
def test_unreviewed_resource_names_rejected(name):
    with pytest.raises(InventoryPublicationError, match="RESOURCE_INVALID"):
        InventoryResource(name)


def test_wrong_bucket_or_nonstandard_endpoint_rejected():
    for bucket in (SimpleNamespace(name="wrong"), SimpleNamespace(name=RESOURCE.bucket,
            client=SimpleNamespace(_connection=SimpleNamespace(API_BASE_URL="https://example.com")))):
        with pytest.raises(InventoryPublicationError, match="CONFIGURATION_INVALID"):
            adapter(bucket)


def test_stale_current_snapshot_still_fences_generation_and_is_replaced():
    store = MemoryStore(VersionedInventory(19, encoded(-901)))
    assert publish(store) == "published"
    assert store.writes[0][1] == 19


def test_sdk_read_race_never_falls_back_to_unpinned_latest(monkeypatch):
    bucket = Bucket(encoded())
    blob = Blob(bucket, OBJECT_NAME)
    monkeypatch.setattr(bucket, "get_blob", lambda *args, **kwargs: blob)
    bucket.generation += 1
    with pytest.raises(InventoryPublicationError, match="STORE_UNAVAILABLE"):
        adapter(bucket).read_latest(timeout=5)
    assert len(bucket.calls) == 1 and bucket.calls[0][0] == "download"


def test_checksum_mismatch_never_returns_provider_bytes(monkeypatch):
    bucket = Bucket(encoded())
    blob = Blob(bucket, OBJECT_NAME)
    blob.md5_hash = "invalid"
    monkeypatch.setattr(bucket, "get_blob", lambda *args, **kwargs: blob)
    with pytest.raises(InventoryPublicationError, match="STORE_UNAVAILABLE"):
        adapter(bucket).read_latest(timeout=5)
