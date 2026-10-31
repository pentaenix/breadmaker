"""Synthetic signed machine tokens, exact boundary, bounded job and durable GC."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from hosted.firestore.contracts import DocumentConflict
from hosted.narration.maintenance import CONTROL, NarrationMaintenanceStore
from hosted.narration.maintenance_composition import configured, reserve_batch, run_scoped_batch, DAILY_BATCHES
from hosted.narration.maintenance_http import MaintenanceOperation
from hosted.narration.maintenance_identity import CERTIFICATES, ISSUER, MachineIdentity, PATH
from hosted.narration.io_limits import deadline
from tests.test_v10569_narration_gc import expired_delete
from tests.test_v10569_narration_gc_job import Queue, inventory
from tests.test_v10569_narration_recordings import env, PID, UID

ORIGIN = 'https://app.breadmaker.studio'
PROJECT = 'breadmaker-prod-511902'
EMAIL = 'breadmaker-narration-gc@' + PROJECT + '.iam.gserviceaccount.com'
SUBJECT = '123456789012345678901'
RESULT = dict(collected=0, held=0, tracked=0, fences=0, orphans=0, partial=False)


@pytest.fixture(scope='module')
def signing():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth.crypt import RSASigner
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'disposable-test-only')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1)).sign(key, hashes.SHA256()))
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return RSASigner.from_string(private, key_id='synthetic'), cert.public_bytes(serialization.Encoding.PEM).decode()


def identity(signing):
    value = MachineIdentity(audience=ORIGIN + PATH, email=EMAIL, subject=SUBJECT)
    # Replace ONLY Google's certificate transport; JWT signature/issuer/audience/
    # lifetime verification still executes in the real pinned google-auth library.
    def certs(url, **kwargs):
        assert url == CERTIFICATES
        return SimpleNamespace(status=200, data=json.dumps({'synthetic': signing[1]}).encode())
    value._request = certs
    return value


def token(signing, **changes):
    from google.auth import jwt
    now = int(time.time())
    claims = dict(iss=ISSUER, aud=ORIGIN + PATH, email=EMAIL, email_verified=True, sub=SUBJECT, iat=now, exp=now+3600)
    claims.update(changes)
    return jwt.encode(signing[0], claims).decode()


def test_real_signed_machine_identity_accepts_only_the_pinned_service_account(signing):
    assert identity(signing).verify(token(signing)) is True


@pytest.mark.parametrize('changes', [
    {'aud': ORIGIN}, {'aud': [ORIGIN + PATH, ORIGIN]}, {'iss': 'https://securetoken.google.com/' + PROJECT},
    {'iss': 'accounts.google.com'}, {'email': 'someone@gmail.com'}, {'email_verified': False},
    {'email_verified': 'true'}, {'sub': '987654321098765432109'}, {'exp': 1},
    {'iat': int(time.time())+100}, {'exp': int(time.time())+8000}, {'exp': True},
])
def test_signed_wrong_audience_issuer_identity_expiry_and_types_fail(signing, changes):
    with pytest.raises(Exception):
        identity(signing).verify(token(signing, **changes))


def test_tampered_signature_and_unsigned_token_fail(signing):
    raw = token(signing)
    for candidate in (raw[:-16] + 'A'*16, 'eyJhbGciOiJub25lIn0.e30.c2ln', 'not-token'):
        with pytest.raises(Exception):
            identity(signing).verify(candidate)


def http(monkeypatch, operation):
    from hosted import http_api as owner
    # Accessing any ordinary session/project port would be a test failure. The
    # exact machine owner must authenticate instead, not mark this a public API.
    monkeypatch.setattr(owner, 'runtime', SimpleNamespace(narration=SimpleNamespace(maintenance=operation)))
    app = FastAPI()
    app.middleware('http')(owner.hosted_request_boundary)
    return TestClient(app, base_url=ORIGIN)


def test_real_hosted_boundary_requires_machine_identity_not_browser_session(monkeypatch, signing):
    calls = []
    operation = MaintenanceOperation(identity(signing), lambda: calls.append(1) or dict(RESULT))
    try:
        client = http(monkeypatch, operation)
        assert client.post(PATH).status_code == 401
        assert client.post(PATH, headers={'Cookie': 'breadmaker_session=fake'}).status_code == 400
        assert client.post(PATH, headers={'Authorization': 'Bearer ' + token(signing, sub='0'*21)}).status_code == 401
        assert not calls
        response = client.post(PATH, headers={'Authorization': 'Bearer ' + token(signing)})
        assert response.status_code == 200 and response.json() == {'ok': True, **RESULT}
        assert response.headers['Cache-Control'] == 'private, no-store' and calls == [1]
    finally:
        operation.workers.shutdown(wait=True)


def test_machine_route_rejects_targets_bodies_duplicates_methods_and_encoded_paths(monkeypatch, signing):
    calls = []
    operation = MaintenanceOperation(identity(signing), lambda: calls.append(1) or dict(RESULT))
    try:
        client = http(monkeypatch, operation)
        headers = {'Authorization': 'Bearer ' + token(signing)}
        assert client.get(PATH, headers=headers).status_code == 405
        for url in (PATH+'?bucket=private', PATH+'?project_id='+PID):
            assert client.post(url, headers=headers).status_code == 400
        assert client.post(PATH, headers=headers, json={'targets': []}).status_code == 400
        for name in ('Origin', 'Cookie', 'X-Breadmaker-Project', 'X-Breadmaker-User', 'X-Breadmaker-Action', 'Idempotency-Key'):
            assert client.post(PATH, headers={**headers, name: 'fake'}).status_code == 400
        assert client.post(PATH, headers=list(headers.items())*2).status_code == 401
        assert client.post(PATH, headers=[*headers.items(), ('Content-Length','0'), ('Content-Length','0')]).status_code == 400
        assert client.post(PATH.replace('_internal', '%5finternal'), headers=headers).status_code == 404
        assert not calls
    finally:
        operation.workers.shutdown(wait=True)


def test_disabled_route_never_authenticates_or_reads_providers(monkeypatch):
    response = http(monkeypatch, None).post(PATH, content=b'no provider work')
    assert response.status_code == 404
    assert configured({}, project=PROJECT, objects=None) is None


def test_enabled_configuration_pins_email_and_numeric_subject_before_provider_use():
    values = dict(BREADMAKER_NARRATION_GC_ENABLED='true', BREADMAKER_PUBLIC_ORIGIN=ORIGIN,
        BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT=EMAIL, BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT_ID=SUBJECT)
    def never(*args): raise AssertionError('No startup ADC or data I/O')
    operation = configured(values, project=PROJECT, objects=None, provider_factory=never)
    assert operation.identity.audience == ORIGIN + PATH
    for name, value in [('BREADMAKER_NARRATION_GC_ENABLED','yes'),
            ('BREADMAKER_PUBLIC_ORIGIN','https://attacker.example/path'),
            ('BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT',EMAIL.replace('breadmaker-narration-gc','breadmaker-runtime')),
            ('BREADMAKER_NARRATION_GC_SERVICE_ACCOUNT_ID','not-a-subject')]:
        with pytest.raises(ValueError):
            configured({**values,name:value}, project=PROJECT, objects=None, provider_factory=never)


def test_server_daily_budget_cannot_be_overridden_or_refunded_and_resets_in_utc(env):
    store = NarrationMaintenanceStore(env[2]); stamp = env[5][0]
    for _ in range(DAILY_BATCHES): reserve_batch(store, now=lambda:stamp)
    with pytest.raises(DocumentConflict, match='DAILY_LIMIT'):
        reserve_batch(store, now=lambda:stamp)
    with pytest.raises(DocumentConflict, match='CLOCK_REVERSED'):
        reserve_batch(store, now=lambda:stamp-86400)
    reserve_batch(store, now=lambda:stamp+86400)
    assert env[2].get((CONTROL,'invocations')).data['attempts'] == 1


def test_short_app_batch_collects_disposable_data_without_touching_canonical_state(env):
    from hosted.narration.recordings import _intent
    made, _, _ = expired_delete(env)
    generation = env[1].project_generation(PID,UID)
    inventory(env)
    checks = []
    env[3].verify_gc_bucket = lambda: checks.append(deadline.get())
    result = run_scoped_batch(env[2], env[3], Queue(env[2]), now=lambda:env[5][0])
    assert result['collected'] == 1 and result['fences'] == 1 and not result['partial']
    assert env[2].get(_intent(PID,made['publication'])) is None
    assert env[1].project_generation(PID,UID) == generation
    assert 0 < checks[0] - time.monotonic() <= 40 and deadline.get() is None


def test_failed_bucket_protection_check_is_charged_but_never_touches_payloads(env):
    def held(): raise ValueError('Synthetic protection drift')
    env[3].verify_gc_bucket = held
    with pytest.raises(ValueError):
        run_scoped_batch(env[2], env[3], Queue(env[2]))
    assert env[2].get((CONTROL,'invocations')).data['attempts'] == 1
    assert deadline.get() is None


def test_provider_failure_body_is_sanitized_and_lease_can_recover(monkeypatch, signing):
    def failed(): raise RuntimeError('secret-token private-object manuscript')
    operation = MaintenanceOperation(identity(signing), failed)
    try:
        response = http(monkeypatch,operation).post(PATH,headers={'Authorization':'Bearer '+token(signing)})
        assert response.status_code == 503 and response.json() == {'detail':'NARRATION_GC_FAILED_RESUMABLE'}
    finally:
        operation.workers.shutdown(wait=True)


def test_cancelled_http_does_not_release_slot_while_provider_still_runs():
    import asyncio
    from threading import Event
    from starlette.requests import Request
    started, finish = Event(), Event()
    def running():
        started.set()
        assert finish.wait(timeout=2)
        return dict(RESULT)
    operation = MaintenanceOperation(SimpleNamespace(verify=lambda _:True),running)
    def request():
        async def receive(): return {'type':'http.request','body':b''}
        return Request(dict(type='http',method='POST',path=PATH,raw_path=PATH.encode(),
            query_string=b'',headers=[(b'authorization',b'Bearer synthetic')]),receive)
    async def exercise():
        task = asyncio.create_task(operation.handle(request()))
        for _ in range(200):
            if started.is_set(): break
            await asyncio.sleep(.001)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert (await operation.handle(request())).status_code==429
        finish.set()
        for _ in range(200):
            if operation.capacity.acquire(blocking=False):
                operation.capacity.release()
                return
            await asyncio.sleep(.001)
        raise AssertionError('Actual provider completion must release its slot')
    try:
        asyncio.run(exercise())
    finally:
        finish.set()
        operation.workers.shutdown(wait=True)


def test_certificate_transport_is_fixed_bounded_public_only_and_cached(signing):
    from tests.test_v10569_narration_gc_client import Reply, Session
    class Certificates(Session):
        def get(self,url,**kwargs):
            self.calls.append((url,kwargs))
            return Reply(json.dumps({'synthetic':signing[1]}).encode(),headers={'Cache-Control':'public, max-age=86400'})
    session=Certificates([])
    value=MachineIdentity(audience=ORIGIN+PATH,email=EMAIL,subject=SUBJECT,session_factory=lambda:session)
    assert value.verify(token(signing)) and value.verify(token(signing))
    assert len(session.calls)==1 and session.calls[0][0]==CERTIFICATES
    assert session.calls[0][1]==dict(timeout=(1,3),allow_redirects=False,stream=True)
    assert 0 < value.until-time.time() <= 3600
    with pytest.raises(ValueError,match='CERTIFICATE_ORIGIN'):
        value._request('https://attacker.example/certificates')
