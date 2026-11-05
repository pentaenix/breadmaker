"""Run real hosted HTTP with synthetic accounts and disposable local adapters."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
import os
from pathlib import Path
import resource
import socket
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


class Observations:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}

    def record(self, name, elapsed, *, returned=0, scanned=0):
        with self.lock:
            row = self.rows.setdefault(name, dict(calls=0, elapsed_ms=0, max_ms=0,
                                                 returned=0, scanned=0))
            row['calls'] += 1
            row['elapsed_ms'] += elapsed * 1000
            row['max_ms'] = max(row['max_ms'], elapsed * 1000)
            row['returned'] += returned
            row['scanned'] += scanned

    def clear(self):
        with self.lock:
            self.rows.clear()


class ObservedTransaction:
    def __init__(self, transaction, observations, pending=None):
        self.transaction, self.observations = transaction, observations
        self.pending = pending

    def __getattr__(self, name):
        native = getattr(self.transaction, name)
        def call(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = native(*args, **kwargs)
                if self.pending is not None and name in {'set', 'create', 'delete'}:
                    self.pending[tuple(args[0])] = (None if name == 'delete'
                        else len(json.dumps(args[1], default=str).encode()))
                return result
            finally:
                self.observations.record('tx.' + name, time.perf_counter() - start)
        return call


def make_store(observations):
    from hosted.firestore.memory import MemoryDocumentStore

    class ObservedMemoryStore(MemoryDocumentStore):
        seeding = True

        def get(self, path):
            start = time.perf_counter()
            try:
                return super().get(path)
            finally:
                observations.record('get', time.perf_counter() - start)

        def query_collection(self, path, **kwargs):
            start = time.perf_counter()
            result = super().query_collection(path, **kwargs)
            observations.record('query', time.perf_counter() - start,
                                returned=len(result), scanned=len(self._documents))
            return result

        def query_collection_group(self, collection_id, **kwargs):
            start = time.perf_counter()
            result = super().query_collection_group(collection_id, **kwargs)
            observations.record('query_group', time.perf_counter() - start,
                                returned=len(result), scanned=len(self._documents))
            return result

        def run_transaction(self, fn, **kwargs):
            if self.seeding:
                # No requests are served yet. A failed seed discards this entire
                # process/store, so per-transaction rollback snapshots add only
                # quadratic fixture setup cost. Canonical repositories still
                # validate/write every row. Timed traffic uses the normal driver.
                from hosted.firestore.memory import _MemoryTransaction
                with self._lock:
                    return fn(_MemoryTransaction(self))
            start = time.perf_counter()
            try:
                return super().run_transaction(
                    lambda tx: fn(ObservedTransaction(tx, observations)), **kwargs)
            finally:
                # Includes lock wait, whole-store rollback copy and callback work.
                observations.record('transaction', time.perf_counter() - start)

    return ObservedMemoryStore()


def make_google_store(observations, *, endpoint, project):
    from tools.loadtest.emulator import create_client
    from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
    client = create_client(endpoint, project)

    class ObservedGoogleStore(GoogleFirestoreDocumentStore):
        # Track committed writes in this fresh process-owned project, without
        # adding inventory RPCs to measured traffic or retaining document bodies.
        def __init__(self):
            super().__init__(client=client)
            self.inventory, self.inventory_lock = {}, threading.Lock()

        def get(self, path):
            start = time.perf_counter()
            try:
                return super().get(path)
            finally:
                observations.record('get', time.perf_counter() - start)

        def query_collection(self, path, **kwargs):
            start = time.perf_counter()
            rows = super().query_collection(path, **kwargs)
            observations.record('query', time.perf_counter() - start, returned=len(rows))
            return rows

        def query_collection_group(self, name, **kwargs):
            start = time.perf_counter()
            rows = super().query_collection_group(name, **kwargs)
            observations.record('query_group', time.perf_counter() - start, returned=len(rows))
            return rows

        def run_transaction(self, fn, **kwargs):
            start, final_writes = time.perf_counter(), {}
            def execute(tx):
                pending = {}
                observations.record('transaction_attempt', 0)
                result = fn(ObservedTransaction(tx, observations, pending))
                final_writes.clear()
                final_writes.update(pending)
                return result
            try:
                result = super().run_transaction(execute, **kwargs)
                with self.inventory_lock:
                    for path, size in final_writes.items():
                        if size is None:
                            self.inventory.pop(path, None)
                        else:
                            self.inventory[path] = size
                return result
            finally:
                observations.record('transaction', time.perf_counter() - start)

        def volume(self):
            with self.inventory_lock:
                return dict(documents_at_end=len(self.inventory), stored_json_bytes=sum(self.inventory.values()),
                            inventory_method='tracked committed writes in fresh owned emulator project')

    return ObservedGoogleStore()


def seed(runtime, *, users, chapters, blocks, text_bytes):
    from hosted.chapter_mutations import replace_chapter_blocks
    from hosted.runtime_services import ensure_hosted_user
    accounts = []
    for index in range(users):
        uid = 'usr_' + f'{index + 1:032x}'
        ensure_hosted_user(runtime.projects.documents, user_id=uid,
                           display_name=f'Synthetic load user {index + 1}')
        cookie, _ = runtime.sessions.issue(uid, auth_method='test_fixture')
        with runtime.sessions.bind(cookie):
            project = runtime.projects.create(f'Synthetic load project {index + 1}')
            pid = project['id']
            domain = runtime.projects.domain
            chapter_ids = ['chapter_1']
            for number in range(1, chapters):
                chapter = domain.create('chapter', {'project_id': pid},
                    {'title': f'Synthetic chapter {number + 1}',
                     'tab_id': 'tab_manuscript', 'position': number}, user_id=uid)
                chapter_ids.append(chapter['id'])
            for chapter_index, cid in enumerate(chapter_ids):
                prose = ('Synthetic prose for load testing. ' * ((text_bytes // 34) + 1))[:text_bytes]
                replace_chapter_blocks(domain, project_id=pid, chapter_id=cid, user_id=uid,
                    blocks=[{'id': f'b_load_{chapter_index}_{number}', 'block_type': 'paragraph',
                             'text_content': prose, 'content_html': prose} for number in range(blocks)],
                    expected_revision=domain.project_generation(pid, uid), operation=f'seed_load_{chapter_index}')
            accounts.append(dict(cookie=cookie, user_id=uid, project_id=pid,
                                 chapter_ids=chapter_ids))
    return accounts


def main():
    # A fresh runner child is the only supported entry point. No credential-backed
    # adapters, network targets or environment-selected hosted modes are accepted.
    if os.environ.get('BREADMAKER_LOAD_FIXTURE') != 'local-load-v2':
        raise SystemExit('Use ./load-test; direct fixture startup is disabled.')
    config = json.loads(Path(sys.argv[1]).read_text())
    sock = socket.socket(fileno=int(config['socket_fd']))
    if sock.getsockname()[0] != '127.0.0.1':
        raise SystemExit('Load fixture must bind IPv4 loopback.')
    if (os.environ.get('BREADMAKER_RUNTIME'), os.environ.get('BREADMAKER_ENV'),
        os.environ.get('BREADMAKER_AUTH_MODE')) != ('hosted_dev', 'test', 'test'):
        raise SystemExit('Load fixture requires hosted_dev/test/test.')
    observations = Observations()
    adapter = config.get('adapter', 'memory')
    if adapter == 'firestore-emulator':
        documents = make_google_store(observations, endpoint=config['emulator_endpoint'], project=config['emulator_project'])
    elif adapter == 'memory':
        documents = make_store(observations)
    else:
        raise SystemExit('Unsupported local load adapter')
    from hosted.storage import MemoryObjectStore
    from runtime.hosted import build_hosted_dev_runtime
    runtime_documents = documents
    if adapter == 'memory' and config.get('lifecycle_fence'):
        from hosted.account_lifecycle import AccountLifecycleDocumentStore
        runtime_documents = AccountLifecycleDocumentStore(documents)
    runtime = build_hosted_dev_runtime(documents=runtime_documents, objects=MemoryObjectStore())
    with patch('runtime.build_runtime', return_value=runtime):
        from app import app
    accounts = seed(runtime, **config['dataset'])
    if adapter == 'memory':
        documents.seeding = False
    manifest = dict(base_url=f'http://127.0.0.1:{sock.getsockname()[1]}',
                    fixture='local-firestore-v1' if adapter == 'firestore-emulator' else 'local-memory-v1',
                    cookie_name=runtime.sessions.cookie_name,
                    accounts=accounts, run_id=config['run_id'])
    Path(config['manifest']).write_text(json.dumps(manifest))
    observations.clear()
    start_usage = resource.getrusage(resource.RUSAGE_SELF)
    start = time.perf_counter()
    def write_observations():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_bytes = usage.ru_maxrss if sys.platform == 'darwin' else usage.ru_maxrss * 1024
        volume = (documents.volume() if adapter == 'firestore-emulator' else dict(
            documents_at_end=len(documents._documents),
            stored_json_bytes=sum(len(json.dumps(row[0]).encode()) for row in documents._documents.values()),
            inventory_method='memory driver document inventory'))
        Path(config['observations']).write_text(json.dumps(dict(
            adapter='GoogleFirestoreDocumentStore/emulator' if adapter == 'firestore-emulator' else 'MemoryDocumentStore',
            dataset=config['dataset'], **volume,
            account_lifecycle_fence=runtime.capabilities.supports('account_erasure_fence'),
            elapsed_seconds=time.perf_counter() - start,
            cpu_seconds=(usage.ru_utime + usage.ru_stime - start_usage.ru_utime - start_usage.ru_stime),
            max_rss_bytes=rss_bytes, operations=observations.rows,
            warning='Logical adapter calls, not Firestore billable operations. Timings overlap; GCS and login remain synthetic.'
        ), indent=2))

    # Uvicorn re-raises SIGTERM after shutdown. Persist before signal re-raise,
    # while still composing the application's original lifespan unchanged.
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def observed_lifespan(application):
        try:
            async with original_lifespan(application):
                yield
        finally:
            write_observations()
            if adapter == 'firestore-emulator':
                documents._client.close()
    app.router.lifespan_context = observed_lifespan
    import uvicorn
    uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='warning')).run(sockets=[sock])


if __name__ == '__main__':
    main()
