"""Clean verification dependencies and real bounded HTTPS redirect installation."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
from threading import Thread

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT=Path(__file__).resolve().parents[1]


def test_release_preflight_checks_nested_hosted_dependencies_without_running_suites(tmp_path):
    for name in ('requirements.txt','requirements-hosted.txt','requirements-test.txt','VERSION'):
        (tmp_path/name).write_bytes((ROOT/name).read_bytes())
    # Execute the actual dependency-selection function, not the full verifier.
    source=(ROOT/'verify-release').read_text().split("printf 'Preparing Breadmaker release verification",1)[0]
    command=['bash','-c',source+'\nhas_test_deps "$1"\n','preflight',sys.executable]
    good=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,timeout=5)
    assert good.returncode==0,good.stderr
    hosted=tmp_path/'requirements-hosted.txt'
    hosted.write_text(hosted.read_text()+'\nbreadmaker-test-definitely-not-installed==1.0.0\n')
    missing=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,timeout=5)
    assert missing.returncode!=0,'A missing nested SDK dependency passed verification preflight'


@contextmanager
def https_origin(tmp_path):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'disposable-loopback')])
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    cert_path=tmp_path/'synthetic-cert.pem';key_path=tmp_path/'synthetic-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    calls=[];payload=b'{"test":"fixed"}'.ljust(44,b' ')
    assert len(payload)==44
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            calls.append(self.path)
            if self.path=='/redirect':
                self.send_response(307);self.send_header('Location','/config')
                body=b'x'*334
            else:
                self.send_response(200)
                body={'/config':payload,'/wrong-hash':b'x'*44,'/wrong-size':payload+b' '}.get(self.path,b'x'*16385)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert_path,key_path)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True);thread.start()
    try:yield f'https://127.0.0.1:{server.server_port}',cert_path,payload,calls
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_real_curl_redirect_preserves_exact_final_pin_and_previous_file(tmp_path):
    node=shutil.which('node');assert node,'Node is required for narration installer verification'
    owner=(ROOT/'tools/narration/download.mjs').as_uri();target=tmp_path/'config.json'
    with https_origin(tmp_path) as (origin,certificate,payload,calls):
        env={**os.environ,'CURL_CA_BUNDLE':str(certificate),'NO_PROXY':'127.0.0.1'}
        def install(path,digest):
            pin=dict(url=origin+path,target=str(target),size=len(payload),sha256=digest)
            return subprocess.run([node,'--input-type=module','-e',
                'import {downloadPinned} from '+json.dumps(owner)+';await downloadPinned(JSON.parse(process.argv[1]));',
                json.dumps(pin)],env=env,capture_output=True,text=True,timeout=5)
        digest=hashlib.sha256(payload).hexdigest()
        first=install('/redirect',digest)
        assert first.returncode==0,first.stderr
        assert target.read_bytes()==payload and calls==['/redirect','/config']
        assert install('/must-not-be-fetched',digest).returncode==0
        assert calls==['/redirect','/config']
        different=hashlib.sha256(b'z'*44).hexdigest()
        corrupt=install('/wrong-hash',different)
        assert corrupt.returncode!=0 and 'NARRATION_DOWNLOAD_CHECKSUM_INVALID' in corrupt.stderr
        wrong_size=install('/wrong-size',hashlib.sha256(payload+b' ').hexdigest())
        assert wrong_size.returncode!=0 and 'NARRATION_DOWNLOAD_CHECKSUM_INVALID' in wrong_size.stderr
        oversized=install('/oversized',different)
        assert oversized.returncode!=0 and 'Maximum file size exceeded' in oversized.stderr
        assert target.read_bytes()==payload
        assert not list(tmp_path.glob('.download-*'))
