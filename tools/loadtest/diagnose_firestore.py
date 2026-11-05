"""Bounded synthetic SDK/adapter controls; all traffic targets an owned emulator."""
from pathlib import Path
import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.loadtest.emulator import Emulator, check_installation, create_client
from tools.loadtest.run import clean_environment, stop

CASES = ('sdk_point', 'adapter_point', 'adapter_batchget', 'adapter_query',
         'adapter_query_unique', 'sdk_query', 'sdk_query_unique',
         'sdk_query_unique_value', 'sdk_query_outside')


def worker_case(config):
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.api_core.exceptions import GoogleAPICallError
    from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
    if os.environ.get('BREADMAKER_LOAD_PROBE') != 'owned-emulator-control-v1':
        raise SystemExit('Start diagnostic controls through the supervisor.')
    client = create_client(config['endpoint'], config['project'])
    store = GoogleFirestoreDocumentStore(client=client)
    mode = config['case']
    workers = 5
    batch = client.batch()
    for index in range(workers):
        group = f'g{index}'
        blocks = 'blocks_' + group if mode in ('adapter_query_unique', 'sdk_query_unique') else 'blocks'
        chapter = 'chapter_' + group if mode == 'sdk_query_unique_value' else 'chapter_1'
        batch.create(client.document(f'probe_groups/{group}'), {'generation': 1})
        for number in range(2):
            batch.create(client.document(f'probe_groups/{group}/{blocks}/b{number}'),
                         {'chapter_id': chapter, 'value': 0})
    batch.commit(timeout=5, retry=None)
    native_commit_rpc = client._firestore_api.commit
    def commit_rpc(*args, **kwargs):
        kwargs.update(timeout=3, retry=None)
        return native_commit_rpc(*args, **kwargs)
    client._firestore_api.commit = commit_rpc
    local = threading.local()
    native_query_rpc = client._firestore_api.run_query
    def query_rpc(*args, **kwargs):
        request = kwargs.get('request', args[0] if args else None)
        parent = request.get('parent') if isinstance(request,dict) else request.parent
        local.event.setdefault('wire_query_parents', []).append(parent)
        return native_query_rpc(*args, **kwargs)
    client._firestore_api.run_query = query_rpc
    native_factory = client.transaction
    def factory(*args, **kwargs):
        transaction = native_factory(*args, **kwargs)
        native_commit = transaction._commit
        def commit(*a, **kw):
            start = time.perf_counter()
            try:
                return native_commit(*a, **kw)
            finally:
                local.event['commit_ms'] = (time.perf_counter() - start) * 1000
        transaction._commit = commit
        return transaction
    client.transaction = factory
    barrier = threading.Barrier(workers)
    def run(index):
        group = f'g{index}'
        blocks = 'blocks_' + group if mode in ('adapter_query_unique', 'sdk_query_unique') else 'blocks'
        chapter = 'chapter_' + group if mode == 'sdk_query_unique_value' else 'chapter_1'
        root = ('probe_groups', group)
        collection = (*root, blocks)
        key = (*collection, 'b0')
        events = []
        barrier.wait(timeout=5)
        for iteration in range(4):
            event = dict(worker=index, iteration=iteration, error=None, read_paths=[], write_paths=[], probe_valid=True)
            local.event = event
            start = time.perf_counter()
            try:
                if mode.startswith('sdk_'):
                    if mode == 'sdk_query_outside':
                        list(client.collection('/'.join(collection)).where(filter=FieldFilter('chapter_id','==',chapter)).stream(timeout=3,retry=None))
                    @firestore.transactional
                    def change(tx):
                        parent = client.document('/'.join(root)).get(transaction=tx, timeout=3, retry=None)
                        if mode in ('sdk_query','sdk_query_unique','sdk_query_unique_value'):
                            children=list(client.collection('/'.join(collection)).where(filter=FieldFilter('chapter_id','==',chapter)).stream(transaction=tx,timeout=3,retry=None))
                        else:
                            children=[client.document('/'.join(key)).get(transaction=tx, timeout=3, retry=None)]
                        event['read_paths'] = [parent.reference.path, *[child.reference.path for child in children]]
                        tx.set(parent.reference, {'generation': iteration + 2})
                        tx.set(client.document('/'.join(key)), {'chapter_id':chapter,'value': iteration + 1})
                        event['write_paths'] = [parent.reference.path, '/'.join(key)]
                    change(client.transaction(max_attempts=1))
                else:
                    def change(tx):
                        parent = tx.get(root)
                        if mode in ('adapter_query', 'adapter_query_unique'):
                            children = tx.query_collection(collection, field='chapter_id', value=chapter)
                        elif mode == 'adapter_batchget':
                            children = tx.get_many([key, (*collection, 'b1')])
                        else:
                            children = [tx.get(key)]
                        event['read_paths'] = ['/'.join(parent.path), *['/'.join(row.path) for row in children]]
                        tx.set(root, {'generation':iteration + 2})
                        tx.set(key, {'chapter_id':chapter,'value':iteration + 1})
                        event['write_paths'] = ['/'.join(root), '/'.join(key)]
                    store.run_transaction(change, max_attempts=1)
            except Exception as exc:
                chain=[]
                current=exc
                rpc_error=False
                while current is not None and len(chain)<4:
                    rpc_error |= isinstance(current, GoogleAPICallError)
                    chain.append(f'{type(current).__name__}: {str(current)[:500]}')
                    current=current.__cause__
                event['error'] = chain
                event['probe_valid'] = rpc_error
            event['elapsed_ms'] = (time.perf_counter() - start) * 1000
            events.append(event)
            if event['error']:
                break # Never overlap a retried action after a write timeout.
        return events
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        events = sum(list(pool.map(run, range(workers))), [])
    overlap=[]
    for one in range(workers):
        own=set(p for e in events if e['worker']==one for p in e['write_paths'])
        for other in range(workers):
            if other==one:continue
            read=set(p for e in events if e['worker']==other for p in e['read_paths'])
            if own & read:overlap.append([one,other,sorted(own & read)])
    result=dict(case=mode, workers=workers, max_iterations_per_worker=4, transaction_attempt_limit=1,
                commit_timeout_seconds=3, elapsed_seconds=time.perf_counter()-start,
                completed=sum(not e['error'] for e in events), failed=sum(bool(e['error']) for e in events),
                probe_valid=all(e['probe_valid'] for e in events),
                cross_worker_document_overlap=overlap, events=events)
    result['unexpected_read_paths']=[path for event in events for path in event['read_paths']
        if path != f"probe_groups/g{event['worker']}" and not path.startswith(f"probe_groups/g{event['worker']}/")]
    Path(config['output']).write_text(json.dumps(result,indent=2))
    client.close()


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        worker_case(json.loads(Path(sys.argv[2]).read_text()))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator-jar', required=True, type=Path)
    parser.add_argument('--java', default='java')
    parser.add_argument('--cases', choices=CASES, nargs='+', default=list(CASES))
    parser.add_argument('--results', type=Path, default=ROOT / '.loadtest-results' /
                        ('controls-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')))
    args = parser.parse_args()
    if len(args.cases) > len(CASES) or len(set(args.cases)) != len(args.cases):
        parser.error('Select at most nine distinct diagnostic cases')
    jar, java = args.emulator_jar.resolve(), args.java
    metadata=check_installation(jar,java)
    metadata.update(source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    diagnostic_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    selected_cases=args.cases, workers_per_case=5, max_iterations_per_worker=4,
                    child_deadline_seconds=40, transaction_attempt_limit=1, commit_timeout_seconds=3)
    destination = args.results.resolve()
    destination.mkdir(parents=True,exist_ok=False)
    (destination/'emulator-installation.json').write_text(json.dumps(metadata,indent=2))
    failed = False
    for case in args.cases:
        result_dir=destination/case
        result_dir.mkdir()
        status=dict(case=case, worker_exit=None, error=None)
        with tempfile.TemporaryDirectory(prefix='breadmaker-contention-') as directory:
            sandbox=Path(directory)
            env=clean_environment()
            env['BREADMAKER_LOAD_PROBE']='owned-emulator-control-v1'
            project='demo-bm-k6-'+os.urandom(8).hex()
            emulator=Emulator(jar=jar,java=java,sandbox=sandbox,log_path=result_dir/'emulator.log',project=project,env=env)
            child=None
            try:
                endpoint=emulator.start()
                env['FIRESTORE_EMULATOR_HOST']=endpoint
                config=sandbox/'worker.json'
                config.write_text(json.dumps(dict(case=case,endpoint=endpoint,project=project,output=str(result_dir/'results.json'))))
                with (result_dir/'worker.log').open('w') as log:
                    child=subprocess.Popen([sys.executable,__file__,'--worker',str(config)],env=env,stdout=log,stderr=subprocess.STDOUT)
                    status['worker_exit']=child.wait(timeout=40)
                code,_=emulator.call(f'/emulator/v1/projects/{project}/databases/(default)/documents',method='DELETE',timeout=5)
                code2,body=emulator.call(f'/v1/projects/{project}/databases/(default)/documents/probe_groups')
                status['probe_groups_empty']=code==code2==200 and not body.get('documents')
            except (Exception, KeyboardInterrupt) as exc:
                status['error']=f'{type(exc).__name__}: {exc}'
            finally:
                if child is not None:stop(child)
                status['cleanup']=emulator.cleanup()
        status['temporary_data_removed']=not sandbox.exists()
        (result_dir/'status.json').write_text(json.dumps(status,indent=2))
        results=json.loads((result_dir/'results.json').read_text()) if (result_dir/'results.json').exists() else {}
        complete = results.get('probe_valid') is True and status['worker_exit'] == 0
        cleaned = status.get('probe_groups_empty') is True and status['temporary_data_removed'] is True and all(
            status['cleanup'].get(key) is True for key in ('data_cleared','empty_verified','endpoint_closed'))
        failed |= not complete or not cleaned or bool(status['error']) or bool(results.get('failed'))
        print(json.dumps(dict(case=case, completed=results.get('completed'),failed=results.get('failed'),
                             probe_valid=results.get('probe_valid'),
                             overlap=results.get('cross_worker_document_overlap'),status=status)),flush=True)
        if status['error']:
            break
    print(f'Evidence: {destination}',flush=True)
    return 1 if failed else 0


if __name__=='__main__':
    import signal
    def terminate_owned_control(signum, _frame):
        raise InterruptedError(f'Local control interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, terminate_owned_control)
    raise SystemExit(main())
