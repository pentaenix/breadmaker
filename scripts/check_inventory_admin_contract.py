#!/usr/bin/env python3
"""Synthetic-only producer -> latest object -> actual Admin reader contract.

Requires explicit local Admin checkout and exact commit. No SDK client,
credentials, real account/project data, HTTP request or provider mutation.
All provider and HTTP transport dependencies below are in-memory fakes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hosted.security.inventory import GoogleFirestoreInventoryProducer
from hosted.security.inventory_publication import InventoryPublication, InventoryWriteConflict, VersionedInventory


class MemoryLatest:
    current = None

    def read_latest(self, *, timeout):
        assert 0 < timeout <= 5
        return self.current

    def replace_latest(self, payload, *, if_generation_match, timeout):
        assert 0 < timeout <= 5
        generation = self.current.generation if self.current else 0
        if generation != if_generation_match:
            raise InventoryWriteConflict("SYNTHETIC_CONFLICT")
        self.current = VersionedInventory(generation + 1, payload)


class SyntheticCounts:
    def __init__(self, clock):
        self.clock, self.values, self.fail = clock, {"users": 7, "projects": 12}, False

    def collection(self, name):
        assert name in self.values
        source = self

        class Query:
            def where(self, *, filter):
                assert (filter.field_path, filter.op_string, filter.value) == ("status", "==", "active")
                return self

            def count(self, *, alias):
                assert alias == "total"

                class Aggregate:
                    def stream(self, *, retry, timeout):
                        assert retry is None and timeout == 20
                        if source.fail:
                            raise RuntimeError("synthetic provider failure")
                        yield [SimpleNamespace(alias="total", value=source.values[name],
                            read_time=source.clock.value - timedelta(seconds=1))]

                return Aggregate()

        return Query()


class Clock:
    value = datetime(2026, 11, 10, 12, 0, 10, tzinfo=timezone.utc)
    calls = 0

    def observation(self):
        self.calls += 1
        return self.value - timedelta(seconds=2 if self.calls % 2 else 0)


class MemoryHTTP:
    def __init__(self, store, resource):
        self.store, self.resource, self.calls = store, resource, 0

    def open(self, request, *, timeout):
        assert request.full_url == self.resource.url
        assert request.method == "GET" and 0 < timeout <= 3
        assert request.get_header("Authorization") == "Bearer synthetic-test-token"
        self.calls += 1
        payload = self.store.current.payload

        class Response(BytesIO):
            status = 200
            headers = Message()

            def read1(self, size):
                return self.read(size)

        response = Response(payload)
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Length"] = str(len(payload))
        return response


def run_contract(admin_source: Path, expected_head: str):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("Exact Admin commit required")
    actual = subprocess.run(["git", "-C", str(admin_source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    if actual != expected_head:
        raise ValueError("Admin checkout is not the expected commit")
    dirty = subprocess.run(["git", "-C", str(admin_source), "status", "--porcelain=v1"],
        check=True, capture_output=True, text=True).stdout
    if dirty:
        raise ValueError("Admin checkout must be clean for reproducible contract verification")
    sys.path.insert(0, str(admin_source))
    from admin_portal.cloud_usage import CloudUsage
    from admin_portal.cloud_usage_http import CloudUsageHTTP, SnapshotResource

    clock, store = Clock(), MemoryLatest()
    counts = SyntheticCounts(clock)
    producer = GoogleFirestoreInventoryProducer(client=counts, enabled=True, utc_now=clock.observation)
    publisher = InventoryPublication(producer, store, enabled=True, now=lambda: clock.value)
    resource = SnapshotResource("breadmaker-admin-usage-synthetic")
    http = MemoryHTTP(store, resource)
    reader = CloudUsageHTTP(resource, lambda **_: "synthetic-test-token", opener=http)
    usage = CloudUsage(reader, enabled=True, now=lambda: clock.value)

    assert publisher.publish_once() == "published"
    result = usage.snapshot()
    assert result["state"] == "available" and result["live_connected"] is True
    assert result["metrics"]["active_cloud_accounts"]["value"] == 7
    assert result["metrics"]["current_cloud_projects"]["value"] == 12
    assert all(result["metrics"][name]["value"] is None for name in
               ("words", "characters", "canvases", "concurrent_cloud_accounts", "projects_created"))

    clock.value += timedelta(seconds=3)
    counts.values = {"users": 0, "projects": 0}
    assert publisher.publish_once() == "published"
    assert usage.snapshot()["metrics"]["active_cloud_accounts"]["value"] == 0

    clock.value += timedelta(seconds=3)
    counts.fail = True
    assert publisher.publish_once() == "published"
    result = usage.snapshot()
    assert result["state"] == "incomplete" and result["live_connected"] is False
    assert all(item["value"] is None for item in result["metrics"].values())

    clock.value += timedelta(seconds=901)
    assert usage.snapshot()["state"] == "stale"
    assert http.calls == 4
    print("Synthetic producer/publication/Admin contract passed: counts, zero, unknown, expiry.")
    print("Admin source:", actual)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-source", type=Path, required=True)
    parser.add_argument("--expected-admin-head", required=True)
    args = parser.parse_args()
    run_contract(args.admin_source.resolve(), args.expected_admin_head)
