#!/usr/bin/env python3
"""Synthetic publication benchmark, NOT a production upload-speed prediction.

Each object retains four fresh authority checks, one conditional write and one
verified read. Injected latency models RPC wait; fixed-wave scheduling is the
previous implementation. No provider credentials or real project are used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import argparse
import json
from pathlib import Path
import sys
from threading import Lock
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hosted.package_import import _parallel, PREPARE_WORKERS
from hosted.storage.memory import MemoryObjectStore
from hosted.storage.publication import ImmutableObjectPublisher
from hosted.package_import import _GuardedObjects


def waves(fn, items):
    result = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for start in range(0, len(items), 8):
            pending = [pool.submit(copy_context().run, fn, item) for item in items[start:start + 8]]
            result.extend(f.result() for f in pending)
    return result


def measure(scheduler, count, latency, straggler_every, straggler_factor):
    counts = {'authority_checks': 0, 'writes': 0, 'verified_reads': 0}
    lock = Lock()
    def record(kind):
        with lock: counts[kind] += 1
        time.sleep(latency)
    class Store(MemoryObjectStore):
        def write_chunks(self, *args, **kwargs):
            record('writes')
            return super().write_chunks(*args, **kwargs)
        def read_verified_bytes(self, name, *, if_generation_match, expected_size, expected_sha256):
            record('verified_reads')
            return b''.join(super().read_chunks(name, max_bytes=expected_size,
                                               if_generation_match=if_generation_match))
    publisher = ImmutableObjectPublisher(_GuardedObjects(Store(), lambda: record('authority_checks')))
    def publish(index):
        # A bounded straggler illustrates why fixed waves leave slots idle.
        if straggler_every and index % straggler_every == 0:
            time.sleep(latency * straggler_factor)
        body = ('Synthetic paragraph %d' % index).encode()
        return publisher.publish_bytes('projects/' + 'a' * 32 + '/block-payloads/b_%d/body.json.gz' % index,
                                       body, max_bytes=1024).object.sha256
    started = time.monotonic()
    # Keep real import's 64-object request bound, including pool lifecycle.
    result = []
    for start in range(0, count, 64):
        result.extend(scheduler(publish, list(range(start, min(count, start + 64)))))
    return {'seconds': round(time.monotonic() - started, 4), **counts}, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--objects', type=int, default=1745)
    parser.add_argument('--latency-ms', type=float, default=1)
    parser.add_argument('--straggler-every', type=int, default=8, help='0 disables stragglers')
    parser.add_argument('--straggler-factor', type=float, default=6)
    args = parser.parse_args()
    if (not 1 <= args.objects <= 10000 or not 0 <= args.latency_ms <= 100
            or not 0 <= args.straggler_every <= 10000 or not 0 <= args.straggler_factor <= 100):
        parser.error('bounded synthetic inputs required')
    outputs = {};reference = None
    for name, scheduler in [('previous_fixed_8', waves), ('refill_8', lambda fn, items: _parallel(fn, items)),
                            ('refill_16', lambda fn, items: _parallel(fn, items, workers=PREPARE_WORKERS))]:
        row, values = measure(scheduler, args.objects, args.latency_ms / 1000,
                              args.straggler_every, args.straggler_factor)
        if reference is None: reference = values
        assert values == reference
        assert row['authority_checks'] == 4 * args.objects
        assert row['writes'] == row['verified_reads'] == args.objects
        outputs[name] = row
    print(json.dumps({'kind': 'synthetic_injected_latency_not_production', 'objects': args.objects,
                      'latency_ms': args.latency_ms, 'straggler_every': args.straggler_every,
                      'straggler_factor': args.straggler_factor, 'results': outputs}, indent=2))


if __name__ == '__main__': main()
