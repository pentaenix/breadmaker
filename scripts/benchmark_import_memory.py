"""Synthetic, provider-free multipart/import memory smoke for the hosted image.

Generate fixtures outside the measured container. Run with a kernel cgroup cap
and tmpfs to account for multipart spooling. This is not a production benchmark.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import sqlite3
import sys
import tempfile
import time
import zlib
import zipfile

ROOT = Path(os.environ.get('BREADMAKER_BENCH_ROOT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def generate(directory):
    from storage.schema import SCHEMA_SQL, SCHEMA_VERSION
    from storage.schema_contracts import _GENERATION_CANONICAL_TABLES
    from revision_store.local_store import canonical_state_hash
    directory.mkdir(parents=True, exist_ok=True)
    for kind in ('text', 'assets'):
        db = directory / (kind + '.sqlite3')
        if db.exists():
            raise ValueError('Fixture output already exists')
        con = sqlite3.connect(db)
        con.executescript(SCHEMA_SQL)
        stamp = '2026-01-01T00:00:00Z'
        def insert(table, **values):
            columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            for field in ('created_at', 'updated_at'):
                if field in columns: values.setdefault(field, stamp)
            names = ','.join('"' + key + '"' for key in values)
            con.execute(f'INSERT INTO "{table}"({names}) VALUES({",".join("?" for _ in values)})', list(values.values()))
        insert('storage_meta', key='schema_version', value=str(SCHEMA_VERSION))
        insert('projects', id=1, project_uid='c' * 32, title='Synthetic import memory probe')
        if kind == 'text':
            insert('document_tabs', id=1, project_id=1, title='Manuscript', is_manuscript=1)
            insert('chapters', id=1, project_id=1, tab_id=1, title='Synthetic chapter', position=0)
            rng = random.Random(20261110)
            for index in range(28935):
                value = ''.join(rng.choices('abcdefghijklmnopqrstuvwxyz ', k=650))
                insert('manuscript_blocks', id=f'probe_block_{index:05d}', project_id=1,
                       tab_id=1, chapter_id=1, position=index, text_content=value,
                       content_html='<p>' + value + '</p>')
        con.commit()
        con.close()
        hashes = {'project.sqlite3': hashlib.sha256(db.read_bytes()).hexdigest()}
        target = directory / (kind + '.breadmaker')
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            def add(name, content):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
            add('project.sqlite3', db.read_bytes())
            if kind == 'assets':
                def chunk(name, body):
                    return len(body).to_bytes(4, 'big') + name + body + (zlib.crc32(name + body) & 0xffffffff).to_bytes(4, 'big')
                header = chunk(b'IHDR', b'\0\0\0\1\0\0\0\1\x08\x02\0\0\0')
                pixels = chunk(b'IDAT', zlib.compress(b'\0\x01\x02\x03'))
                png = b'\x89PNG\r\n\x1a\n' + header + pixels
                text = b'probe\0' + b'X' * (10 * 1024**2 - len(png) - 30)
                asset = png + chunk(b'tEXt', text) + chunk(b'IEND', b'')
                from security.image_security import inspect_hosted_image_bytes
                inspect_hosted_image_bytes(asset, '.png', byte_limit=12 * 1024**2)
                for index in range(24):
                    name = f'assets/probe_{index:02d}.png'
                    add(name, asset)
                    hashes[name] = hashlib.sha256(asset).hexdigest()
            manifest = {'package_format': 'breadmaker-project', 'package_version': 2,
                'schema_version': SCHEMA_VERSION, 'project_id': 'c' * 32,
                'project_generation': 1, 'file_hashes': hashes,
                'project_fingerprint': hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'canonical_state_hash': canonical_state_hash(db, _GENERATION_CANONICAL_TABLES, hashes)}
            add('manifest.json', json.dumps(manifest))
        db.unlink()
        print(json.dumps({'fixture': kind, 'bytes': target.stat().st_size,
                          'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


def cgroup_value(name):
    path = Path('/sys/fs/cgroup') / name
    return path.read_text().strip() if path.exists() else None


def measure(fixture, source_sha, require_memory_mib):
    os.environ['BREADMAKER_HOME'] = tempfile.mkdtemp(prefix='synthetic-import-')
    # The container uses actual vendor modules; all providers below are synthetic.
    import google.cloud.firestore
    import google.cloud.storage
    import google.cloud.kms
    import fastapi
    import src.server as server
    from hosted.package_import import PackageImport
    from hosted.firestore.memory import MemoryDocumentStore
    from hosted.storage.memory import MemoryObjectStore
    from hosted.http_api import _bounded_upload, _package_import_call
    from security.hosted_guardrails import HostedLimits
    from types import SimpleNamespace
    if require_memory_mib and cgroup_value('memory.max') != str(require_memory_mib * 1024**2):
        raise RuntimeError('Required kernel memory cap is not active')
    if require_memory_mib and cgroup_value('memory.swap.max') != '0':
        raise RuntimeError('Swap must be disabled for the memory smoke')
    server.environment._HOSTED_LIMITS = HostedLimits.from_environment()
    server._expected_real_auth_host = lambda: ''
    api = fastapi.FastAPI()
    @api.post('/api/projects/import')
    async def capture(request: fastapi.Request, file: fastapi.UploadFile = fastapi.File(...)):
        raw = await _bounded_upload(file, max_bytes=64 * 1024**2)
        docs, objects = MemoryDocumentStore(), MemoryObjectStore()
        actor = 'usr_' + '1' * 32
        docs.run_transaction(lambda tx: tx.create(('users', actor), {'status': 'active'}))
        projects = SimpleNamespace(documents=docs, objects=objects, domain=SimpleNamespace(
            encrypted_objects=None, encrypted_fields=None,
            clock=SimpleNamespace(now_iso=lambda: '2026-01-01T00:00:00Z')))
        return await _package_import_call(PackageImport(projects).begin, raw, actor,
            operation='op_memory_' + request.headers['x-probe'], durable=True)
    server.core.app = api
    async def run():
        async def request(index):
            boundary = b'synthetic-memory-boundary'
            prefix = b'--' + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="synthetic.breadmaker"\r\nContent-Type: application/zip\r\n\r\n'
            suffix = b'\r\n--' + boundary + b'--\r\n'
            sent, read_bytes = [], 0
            with fixture.open('rb') as source:
                parts = iter((prefix,))
                state = 0
                async def receive():
                    nonlocal state, read_bytes
                    await asyncio.sleep(0)
                    if state == 0:
                        body = next(parts); state = 1
                    elif state == 1:
                        body = source.read(65536)
                        if not body: body = suffix; state = 2
                    else:
                        return {'type': 'http.disconnect'}
                    read_bytes += len(body)
                    return {'type': 'http.request', 'body': body, 'more_body': state != 2}
                async def send(message): sent.append(message)
                scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                    'method': 'POST', 'scheme': 'http', 'path': '/api/projects/import',
                    'raw_path': b'/api/projects/import', 'query_string': b'', 'root_path': '',
                    'headers': [(b'host', b'testserver'), (b'x-probe', str(index).encode()),
                                (b'content-type', b'multipart/form-data; boundary=' + boundary)],
                    'client': ('127.0.0.1', 1), 'server': ('testserver', 80)}
                start = time.perf_counter()
                await server.app(scope, receive, send)
            status = next(row['status'] for row in sent if row['type'] == 'http.response.start')
            body = json.loads(b''.join(row.get('body', b'') for row in sent if row['type'] == 'http.response.body'))
            return {'status': status, 'seconds': time.perf_counter() - start, 'read_bytes': read_bytes,
                    'records': body.get('total_records'), 'images': body.get('total_images')}
        pair = await asyncio.gather(request(0), request(1))
        assert sorted(row['status'] for row in pair) == [200, 503], pair
        assert next(row for row in pair if row['status'] == 503)['read_bytes'] == 0
        for index in (2, 3):
            row = await request(index)
            assert row['status'] == 200, row
            pair.append(row)
        expected = (28937, 0) if fixture.stem == 'text' else (0, 24)
        assert all((row['records'], row['images']) == expected for row in pair if row['status'] == 200)
        return pair
    started = time.perf_counter()
    results = asyncio.run(run())
    events = dict(line.split() for line in (cgroup_value('memory.events') or '').splitlines())
    if require_memory_mib and any(int(events.get(key, 0)) for key in ('oom', 'oom_kill')):
        raise RuntimeError('Kernel reported an out-of-memory event')
    print(json.dumps({'kind': 'synthetic_import_memory_not_production', 'source_sha': source_sha,
        'fixture': fixture.name, 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
        'python': sys.version.split()[0], 'wall_seconds': time.perf_counter() - started,
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'cgroup_memory_max': cgroup_value('memory.max'), 'cgroup_memory_peak': cgroup_value('memory.peak'),
        'cgroup_swap_max': cgroup_value('memory.swap.max'), 'cgroup_memory_events': events,
        'results': results, 'limits': 'ASGI and multipart parser with SDK imports; runtime and cgroup reported above. Synthetic API handler and memory providers. No live auth/provider/RPC or end-to-end project publication.'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generate', type=Path)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--source-sha', default='unrecorded')
    parser.add_argument('--require-memory-mib', type=int, default=0)
    args = parser.parse_args()
    if args.generate:
        generate(args.generate)
    elif args.fixture:
        measure(args.fixture, args.source_sha, args.require_memory_mib)
    else:
        parser.error('Choose --generate or --fixture')
