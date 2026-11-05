"""Owned, pinned, credential-free Firestore emulator lifecycle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request

VERSION = '1.22.0'
JAR_SIZE = 136707194
JAR_SHA256 = '9b6498b7f62714d67f48f59b3818883cd682dbcd46b9f59511de81c97bb5166c'
JAR_URL = f'https://storage.googleapis.com/firebase-preview-drop/emulator/cloud-firestore-emulator-v{VERSION}.jar'
MANIFEST_URL = 'https://github.com/firebase/firebase-tools/blob/master/src/emulator/downloadableEmulatorInfo.json'


def validate_target(endpoint, project):
    match = re.fullmatch(r'127\.0\.0\.1:([0-9]{1,5})', str(endpoint))
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ValueError('Emulator endpoint must be an exact IPv4 loopback host:port')
    if not re.fullmatch(r'demo-bm-k6-[0-9a-f]{16}', str(project)):
        raise ValueError('Emulator requires a disposable demo-bm-k6 project')


def check_installation(jar, java):
    jar = Path(jar).resolve()
    if not jar.is_file() or jar.stat().st_size != JAR_SIZE or hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA256:
        raise ValueError(f'Expected official Firestore emulator {VERSION}, SHA-256 {JAR_SHA256}')
    result = subprocess.run([str(java), '-version'], capture_output=True, text=True, timeout=10)
    version = result.stderr + result.stdout
    match = re.search(r'version "([0-9]+)', version)
    if result.returncode or not match or int(match.group(1)) < 21:
        raise ValueError('Firestore emulator requires an explicitly selected Java 21 or later')
    return dict(version=VERSION, sha256=JAR_SHA256, bytes=JAR_SIZE,
                java_version=version.splitlines()[0], java=str(java), heap_limit_mib=512,
                active_processor_count=2)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Emulator:
    def __init__(self, *, jar, java, sandbox, log_path, project, env):
        self.jar, self.java = Path(jar).resolve(), str(java)
        self.sandbox, self.log_path = Path(sandbox), Path(log_path)
        self.project, self.env = project, env
        self.process = self.log = None
        self.endpoint = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, *, method='GET', timeout=3):
        validate_target(self.endpoint, self.project)
        request = urllib.request.Request('http://' + self.endpoint + path, method=method,
                                         headers={'Authorization': 'Bearer owner'})
        with self.opener.open(request, timeout=timeout) as response:
            return response.status, json.load(response)

    def start(self):
        # The Java CLI prints the requested port rather than the resolved port
        # when passed 0. Reserve a random loopback port, then require our child to
        # remain alive and announce readiness before using its fresh demo project.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.endpoint = f'127.0.0.1:{port}'
        validate_target(self.endpoint, self.project)
        rules = self.sandbox / 'firestore.rules'
        rules.write_text("rules_version = '2'; service cloud.firestore { match /databases/{database}/documents { match /{document=**} { allow read, write: if false; } } }")
        self.log = self.log_path.open('w')
        command = [self.java, '-Xms64m', '-Xmx512m', '-XX:ActiveProcessorCount=2',
            '-Djava.net.preferIPv4Stack=true', '-Duser.home=' + str(self.sandbox),
            '-Djava.io.tmpdir=' + str(self.sandbox), '-jar', str(self.jar),
            '--host', '127.0.0.1', '--port', str(port), '--webchannel_port', '0', '--websocket_port', '0',
            '--project_id', self.project, '--single_project_mode', 'true', '--single_project_mode_error', 'true',
            '--database-edition', 'standard', '--rules', str(rules)]
        self.process = subprocess.Popen(command, cwd=self.sandbox, env=self.env,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Owned emulator exited before startup; inspect emulator.log')
            if 'Dev App Server is now running.' in self.log_path.read_text():
                try:
                    status, body = self.call(f'/v1/projects/{self.project}/databases/(default)/documents/users')
                    if status == 200 and not body.get('documents'):
                        return self.endpoint
                    raise RuntimeError('Owned emulator was not empty before seeding')
                except OSError:
                    pass
            time.sleep(.1)
        raise TimeoutError('Owned emulator startup exceeded 30 seconds')

    def cleanup(self):
        result = dict(project=self.project, data_cleared=False, empty_verified=False,
                      endpoint_closed=False, exit_code=None, error=None)
        try:
            if self.process is not None and self.process.poll() is None and self.endpoint:
                status, _ = self.call(f'/emulator/v1/projects/{self.project}/databases/(default)/documents',
                                      method='DELETE', timeout=5)
                result['data_cleared'] = status == 200
                for collection in ('users', 'projects'):
                    status, body = self.call(f'/v1/projects/{self.project}/databases/(default)/documents/{collection}')
                    if status != 200 or body.get('documents'):
                        raise RuntimeError('Emulator cleanup did not verify empty collections')
                result['empty_verified'] = True
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                result['exit_code'] = self.process.returncode
                if result['exit_code'] not in (0, -15, 143) and result['error'] is None:
                    result['error'] = 'Unexpected emulator exit'
            if self.log is not None:
                self.log.close()
            if self.endpoint:
                try:
                    with socket.create_connection(('127.0.0.1', int(self.endpoint.split(':')[1])), timeout=.3):
                        pass
                except OSError:
                    result['endpoint_closed'] = True
        return result


def create_client(endpoint, project):
    """Validate first, then explicitly avoid ADC and force the SDK emulator path."""
    import os
    validate_target(endpoint, project)
    if os.environ.get('FIRESTORE_EMULATOR_HOST') != endpoint:
        raise ValueError('Explicit emulator environment and owned endpoint must agree')
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore
    client = firestore.Client(project=project, database='(default)', credentials=AnonymousCredentials(),
                              client_options={'api_endpoint': endpoint})
    if client._emulator_host != endpoint or client._target != endpoint:
        raise RuntimeError('Firestore SDK did not select the owned emulator transport')
    return client
