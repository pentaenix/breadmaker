#!/usr/bin/env python3
"""Measure real archive preparation using memory adapters, never live providers.

An explicitly selected local archive is validated, captured, continued and
verified through the real preparation service. No project is published, no
credentials are read, and no content/identity is printed or saved. Timings and
adapter-operation counts are not production latency or physical RPC counts.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from threading import Lock
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hosted import native_package
from hosted.firestore.memory import MemoryDocumentStore
from hosted.package_import import MAX_SOURCE_BYTES, PackageImport, _hash
from hosted.storage.memory import MemoryObjectStore


class CountingObjects(MemoryObjectStore):
    def __init__(self):
        super().__init__()
        self.counts = defaultdict(int)
        self.count_lock = Lock()

    def record(self, name, operation, amount=1):
        kind = next((part for part in ('snapshots', 'sources', 'plans') if f'/{part}/' in name), 'other')
        with self.count_lock:
            self.counts[f'{kind}_{operation}'] += amount

    def write_chunks(self, name, chunks, **kwargs):
        self.record(name, 'writes')
        return super().write_chunks(name, chunks, **kwargs)

    def read_chunks(self, name, **kwargs):
        self.record(name, 'reads')
        for chunk in super().read_chunks(name, **kwargs):
            self.record(name, 'read_bytes', len(chunk))
            yield chunk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    with args.package.open('rb') as source:
        raw = source.read(MAX_SOURCE_BYTES + 1)
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('HOSTED_PACKAGE_TOO_LARGE')
    documents, objects = MemoryDocumentStore(), CountingObjects()
    actor = 'usr_' + '1' * 32
    documents.run_transaction(lambda tx: tx.create(('users', actor), {'status': 'active'}))
    # Preparation uses only these explicit memory ports. It cannot instantiate
    # a cloud runtime, resolve credentials, or perform record/final publication.
    projects = SimpleNamespace(documents=documents, objects=objects,
        domain=SimpleNamespace(encrypted_objects=None, encrypted_fields=None,
                               clock=SimpleNamespace(now_iso=lambda: '2026-01-01T00:00:00Z')))
    planner = native_package.prepare_project_package
    parse_times = []
    def measured(*values, **kwargs):
        started = time.perf_counter()
        result = planner(*values, **kwargs)
        parse_times.append(time.perf_counter() - started)
        return result
    native_package.prepare_project_package = measured
    service = PackageImport(projects)
    started = time.perf_counter()
    try:
        progress = service.begin(raw, actor, operation='op_offline_preparation_measurement', durable=True)
        begin_seconds = time.perf_counter() - started
        job = progress['job_id']
        calls = []
        while progress['phase'] == 'preparing':
            before = (progress['prepared_batches'], progress.get('prepared_source_chunks', 0))
            tick = time.perf_counter()
            # A new service per call prevents reliance on in-process job state.
            progress = PackageImport(projects).prepare(actor, job)
            calls.append(time.perf_counter() - tick)
            after = (progress['prepared_batches'], progress.get('prepared_source_chunks', 0))
            if progress['phase'] == 'preparing' and after == before:
                raise AssertionError('Preparation did not advance')
        preparation_seconds = time.perf_counter() - started
    finally:
        native_package.prepare_project_package = planner
    preparation_counts = dict(objects.counts)
    data = dict(documents.get(service._path(actor, job)).data)
    digest = hashlib.sha256()
    verification_started = time.perf_counter()
    for index in range(data['total_batches']):
        _, entries = service._read_plan(actor, job, data, index)
        digest.update(bytes.fromhex(_hash(entries)))
    if digest.hexdigest() != data['plan_sha256'] or documents.get(('projects', data['project_id'])) is not None:
        raise AssertionError('Preparation identity/visibility contract failed')
    inventory = objects.list_prefix(f"projects/{data['project_id']}/package-imports/{job}/")
    retained = {kind: sum(item.size for item in inventory if f'/{kind}/' in item.name)
                for kind in ('snapshots', 'sources', 'plans')}
    print(json.dumps({'kind': 'offline_real_fixture_memory_adapters_not_production',
        'source_bytes_uploaded_once': len(raw), 'records': data['total_records'],
        'images': data['total_images'], 'plan_batches': data['total_batches'],
        'snapshot_chunks': data['total_source_chunks'], 'native_planner_calls': len(parse_times),
        'native_planner_seconds': [round(value, 6) for value in parse_times],
        'begin_seconds': round(begin_seconds, 6), 'continuation_calls': len(calls),
        'continuation_seconds_total': round(sum(calls), 6),
        'continuation_seconds_max': round(max(calls, default=0), 6),
        'preparation_seconds_total': round(preparation_seconds, 6),
        'verification_seconds': round(time.perf_counter() - verification_started, 6),
        'retained_bytes': retained, 'preparation_adapter_operations': preparation_counts,
        'full_plan_fingerprint_verified': True, 'project_published': False,
        'live_provider_operations': 0}, indent=2))


if __name__ == '__main__':
    main()
