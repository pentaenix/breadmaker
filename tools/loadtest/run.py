#!/usr/bin/env python3
"""Bounded supervisor for a disposable local Breadmaker/k6 experiment."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def bounded(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f'must be between {low} and {high}')
        return number
    return parse


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--profile', choices=['smoke', 'concurrency', 'arrival'], default='smoke')
    result.add_argument('--adapter', choices=['memory', 'firestore-emulator'], default='memory')
    result.add_argument('--workload', choices=['mixed', 'read-only', 'single-writer'], default='mixed')
    result.add_argument('--emulator-jar', type=Path, help='Explicit locally installed, pinned official emulator JAR')
    result.add_argument('--java', default='java', help='Java 21+ executable; used only for the emulator')
    result.add_argument('--lifecycle-fence', action='store_true', help='Enable the same account lifecycle fence on memory comparisons')
    result.add_argument('--vus', nargs='+', type=bounded(1, 20), default=[1])
    result.add_argument('--users', type=bounded(1, 100), default=20)
    result.add_argument('--chapters', type=bounded(1, 8), default=2)
    result.add_argument('--blocks', type=bounded(1, 128), default=20)
    result.add_argument('--text-bytes', type=bounded(80, 2000), default=240)
    result.add_argument('--seconds', type=bounded(5, 120), default=30)
    result.add_argument('--rate', type=bounded(1, 10), default=5, help='actions/s for arrival profile')
    result.add_argument('--results', type=Path, default=ROOT / '.loadtest-results' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    result.add_argument('--plan', action='store_true', help='print bounds without starting processes')
    return result


def clean_environment():
    # Explicit allowlist excludes ADC, provider keys, proxy routing, app options,
    # k6 cloud outputs/config, remote metric sinks and user shell startup code.
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'LANG', 'LC_ALL', 'TMPDIR') if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', NO_PROXY='127.0.0.1',
               no_proxy='127.0.0.1')
    return env


def plan(args):
    if args.users < max(args.vus):
        raise ValueError('--users must cover every VU with a distinct account')
    if args.users * args.chapters * args.blocks > 20000:
        raise ValueError('Dataset exceeds 20,000 manuscript blocks; split the experiment')
    if len(args.vus) > 5 or len(set(args.vus)) != len(args.vus):
        raise ValueError('Choose at most five distinct VU levels')
    if args.adapter == 'firestore-emulator' and args.emulator_jar is None:
        raise ValueError('--adapter firestore-emulator requires an explicit --emulator-jar')
    return dict(profile=args.profile, vus=args.vus, adapter=args.adapter, workload=args.workload,
        account_lifecycle_fence=args.adapter == 'firestore-emulator' or args.lifecycle_fence,
        dataset=dict(users=args.users, chapters=args.chapters, blocks=args.blocks, text_bytes=args.text_bytes),
        seconds=args.seconds, arrival_actions_per_second=args.rate if args.profile == 'arrival' else None,
        max_workload_requests_per_second=3 * max(args.vus),
        max_workload_requests_per_run=(30 * max(args.vus) if args.profile == 'smoke'
            else 3 * max(args.vus) * (args.seconds + 1)),
        provider_cost_usd=0, target='runner-owned IPv4 loopback socket only',
        persistence=('GoogleFirestoreDocumentStore + owned local emulator + MemoryObjectStore'
            if args.adapter == 'firestore-emulator' else 'MemoryDocumentStore + MemoryObjectStore') + '; disposed after each VU level')


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_ready(process, manifest, base_url, timeout=120):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Fixture stopped before ready; inspect server.log')
        if manifest.exists():
            try:
                with opener.open(base_url + '/api/health', timeout=1) as response:
                    if response.status == 200 and json.load(response).get('runtime') == 'hosted_dev':
                        return
            except (OSError, ValueError):
                pass
        time.sleep(.1)
    raise TimeoutError('Fixture startup/seed exceeded 120 seconds')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run_one(args, vus, destination):
    destination.mkdir(parents=True, exist_ok=False)
    env = clean_environment()
    status = dict(vus=vus, k6_exit_code=None, error=None)
    with tempfile.TemporaryDirectory(prefix='breadmaker-k6-') as temp:
        sandbox = Path(temp)
        # An explicit empty k6 config excludes automatic personal/cloud settings.
        k6_config = sandbox / 'k6-config.json'
        k6_config.write_text('{}')
        env.update(BREADMAKER_HOME=str(sandbox / 'app'), BREADMAKER_LOAD_FIXTURE='local-load-v2',
                   BREADMAKER_RUNTIME='hosted_dev', BREADMAKER_ENV='test', BREADMAKER_AUTH_MODE='test',
                   BREADMAKER_TEST_AUTH_SECRET=secrets.token_urlsafe(40), BREADMAKER_SESSION_COOKIE_SECURE='0')
        manifest = sandbox / 'manifest.json'
        summary = destination / 'k6-summary.json'
        run_id = secrets.token_hex(8)
        config = dict(manifest=str(manifest), observations=str(destination / 'server-observations.json'),
                      dataset=plan(args)['dataset'], run_id=run_id, adapter=args.adapter,
                      lifecycle_fence=args.lifecycle_fence)
        try:
            with ExitStack() as owned:
                if args.adapter == 'firestore-emulator':
                    from tools.loadtest.emulator import Emulator
                    emulator = Emulator(jar=args.emulator_jar, java=args.java, sandbox=sandbox,
                        log_path=destination / 'emulator.log', project='demo-bm-k6-' + run_id, env=env.copy())
                    def cleanup_emulator():
                        status['emulator_cleanup'] = result = emulator.cleanup()
                        if not all(result[key] for key in ('data_cleared', 'empty_verified', 'endpoint_closed')) or result['error']:
                            status['error'] = status['error'] or 'Emulator cleanup was not fully verified'
                    owned.callback(cleanup_emulator)
                    endpoint = emulator.start()
                    env['FIRESTORE_EMULATOR_HOST'] = endpoint
                    config.update(emulator_endpoint=endpoint, emulator_project=emulator.project)
                with socket.socket() as sock, (destination / 'server.log').open('w') as server_log:
                    sock.bind(('127.0.0.1', 0))
                    sock.listen(128)
                    config['socket_fd'] = sock.fileno()
                    config_path = sandbox / 'fixture.json'
                    config_path.write_text(json.dumps(config))
                    fixture = subprocess.Popen([sys.executable, '-m', 'tools.loadtest.fixture', str(config_path)],
                        cwd=ROOT, env=env, pass_fds=(sock.fileno(),), stdout=server_log, stderr=subprocess.STDOUT)
                    k6 = None
                    try:
                        base_url = f'http://127.0.0.1:{sock.getsockname()[1]}'
                        wait_ready(fixture, manifest, base_url)
                        env.update(BREADMAKER_LOAD_MANIFEST=str(manifest), BREADMAKER_LOAD_SUMMARY=str(summary),
                            BREADMAKER_LOAD_VUS=str(vus), BREADMAKER_LOAD_SECONDS=str(args.seconds),
                            BREADMAKER_LOAD_PROFILE=args.profile, BREADMAKER_LOAD_RATE=str(args.rate))
                        env['BREADMAKER_LOAD_WORKLOAD'] = args.workload
                        with (destination / 'k6.log').open('w') as log:
                            k6 = subprocess.Popen(['k6', 'run', '--address', '', '--quiet', '--config', str(k6_config),
                                str(ROOT / 'tools/loadtest/workload.js')], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                            status['k6_exit_code'] = k6.wait(timeout=(75 if args.profile == 'smoke' else args.seconds + 25))
                    finally:
                        if k6 is not None:
                            stop(k6)
                        stop(fixture)
                        status['fixture_exit_code'] = fixture.returncode
                        if fixture.returncode not in (0, -signal.SIGTERM):
                            status['error'] = f'Unexpected fixture exit: {fixture.returncode}'
                        if not (destination / 'server-observations.json').exists():
                            status['error'] = status['error'] or 'Fixture did not persist shutdown observations'
        except (Exception, KeyboardInterrupt) as exc:
            status['error'] = f'{type(exc).__name__}: {exc}'
    status['temporary_data_removed'] = not sandbox.exists()
    (destination / 'status.json').write_text(json.dumps(status, indent=2))
    return status


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        bounds = plan(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(bounds, indent=2), flush=True)
    if args.plan:
        return 0
    if shutil.which('k6') is None:
        raise SystemExit('Install OSS k6 locally first; no automatic downloads/cloud execution.')
    emulator_installation = None
    if args.adapter == 'firestore-emulator':
        from tools.loadtest.emulator import check_installation
        try:
            emulator_installation = check_installation(args.emulator_jar, args.java)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise SystemExit(str(exc)) from exc
    args.results = args.results.resolve()
    args.results.mkdir(parents=True, exist_ok=False)
    metadata = dict(bounds, started_at=datetime.now(timezone.utc).isoformat(),
        source_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)),
        k6_version=subprocess.check_output(['k6', 'version'], text=True).strip(),
        python_version=platform.python_version(), platform=platform.platform(), cpu_count=os.cpu_count())
    if emulator_installation:
        metadata['emulator'] = emulator_installation
    import hashlib
    metadata['harness_sha256'] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / 'tools/loadtest').glob('*')) if path.suffix in ('.py', '.js')}
    (args.results / 'experiment.json').write_text(json.dumps(metadata, indent=2))
    failed = False
    for vus in args.vus:
        print(f'Running {args.profile}: {vus} VUs, {args.users} synthetic users', flush=True)
        status = run_one(args, vus, args.results / f'vus-{vus}')
        print(json.dumps(status), flush=True)
        failed |= status['k6_exit_code'] != 0 or bool(status['error']) or not status['temporary_data_removed']
        if status['error']:
            break
    print(f'Evidence: {args.results}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    def terminate_owned_run(signum, _frame):
        raise InterruptedError(f'Local load test interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, terminate_owned_run)
    raise SystemExit(main())
