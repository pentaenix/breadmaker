"""M12C: encrypted GCS object boundary; tests run only by the human operator."""
from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hosted.security.envelope import HostedEnvelopeCodec, EnvelopeError
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore, EncryptedObjectReference
from hosted.storage.memory import MemoryObjectStore
from hosted.storage.contracts import ObjectIntegrityError, ObjectLimitExceeded

PID = 'a' * 32
UID = 'usr_' + 'b' * 32
OTHER = 'usr_' + 'c' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
SECRET = b'Unpublished manuscript: the purple room is forbidden.'


class FakeKms:
    key_resource = RESOURCE

    def __init__(self):
        self._key = AESGCM.generate_key(bit_length=256)

    def wrap(self, raw_key):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._key).encrypt(nonce, raw_key, b'keywrap')

    def unwrap(self, ciphertext):
        return AESGCM(self._key).decrypt(ciphertext[:12], ciphertext[12:], b'keywrap')


def sample(*, authorizer=None, objects=None, kms=None):
    objects = objects or MemoryObjectStore()
    kms = kms or FakeKms()
    if authorizer is None:
        def authorizer(pid, uid):
            if pid != PID or uid != UID:
                raise PermissionError('HOSTED_NOT_A_MEMBER')
            return True
    service = HostedEncryptedObjectStore(objects, HostedEnvelopeCodec(kms), authorize=authorizer)
    return service, objects


def inputs(**overrides):
    return dict(project_id=PID, user_id=UID, entity='manuscript_block',
                record_id='block_1', operation_id='update_1', **overrides)


def test_v1059_ciphertext_is_real_storage_and_no_private_hash_or_content_leaks():
    service, objects = sample()
    ref = service.publish(**inputs(), plaintext=SECRET)
    assert service.read(**inputs(), reference=ref) == SECRET
    raw = b''.join(objects.read_chunks(ref.name, max_bytes=24 * 1024 * 1024))
    assert SECRET not in raw
    assert SECRET.decode() not in ref.name
    assert hashlib.sha256(SECRET).hexdigest() not in ref.name
    assert 'sha256/' not in ref.name
    assert 'Unpublished' not in str(objects.stat(ref.name).metadata)
    assert ref.ciphertext_sha256 == hashlib.sha256(raw).hexdigest()
    assert ref.ciphertext_sha256 != hashlib.sha256(SECRET).hexdigest()


def test_v1059_identical_retry_returns_original_generation_and_different_replay_rejected():
    service, objects = sample()
    first = service.publish(**inputs(), plaintext=SECRET)
    again = service.publish(**inputs(), plaintext=SECRET)
    assert again == first
    assert len(objects.list_prefix('projects/')) == 1
    with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_OPERATION_REUSED'):
        service.publish(**inputs(), plaintext=SECRET + b' changed')
    assert service.read(**inputs(), reference=first) == SECRET


def test_v1059_rejects_other_users_before_any_storage_access():
    class GuardedStore(MemoryObjectStore):
        def stat(self, name):
            raise AssertionError('storage accessed before authorization')
    service, _ = sample(objects=GuardedStore())
    fields = inputs()
    fields['user_id'] = OTHER
    with pytest.raises(PermissionError):
        service.publish(**fields, plaintext=SECRET)
    with pytest.raises(PermissionError):
        service.read(**fields, reference=EncryptedObjectReference('bogus', 1, '0'*64, 1))
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_AUTHORIZER_REQUIRED'):
        HostedEncryptedObjectStore(MemoryObjectStore(), HostedEnvelopeCodec(FakeKms()), authorize=None)
    denied, _ = sample(authorizer=lambda *_: False, objects=GuardedStore())
    with pytest.raises(PermissionError, match='HOSTED_ENCRYPTED_NOT_AUTHORIZED'):
        denied.publish(**inputs(), plaintext=SECRET)


def test_v1059_swapped_reference_project_record_or_operation_fails_closed():
    service, _ = sample(authorizer=lambda *_: True)
    ref = service.publish(**inputs(), plaintext=SECRET)
    for changed in (
        {'project_id': 'd'*32}, {'record_id': 'block_2'},
        {'operation_id': 'update_2'}, {'entity': 'comment_message'},
    ):
        args = inputs()
        args.update(changed)
        with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_REFERENCE_SCOPE_MISMATCH'):
            service.read(**args, reference=ref)


def test_v1059_ciphertext_tamper_and_generation_substitution_rejected():
    service, objects = sample()
    ref = service.publish(**inputs(), plaintext=SECRET)
    wrong = EncryptedObjectReference(ref.name, ref.generation, '0'*64, ref.ciphertext_size)
    with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_CIPHERTEXT_MISMATCH'):
        service.read(**inputs(), reference=wrong)
    wrong = EncryptedObjectReference(ref.name, ref.generation+1, ref.ciphertext_sha256, ref.ciphertext_size)
    with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_OBJECT_CHANGED'):
        service.read(**inputs(), reference=wrong)
    wrong = EncryptedObjectReference(ref.name, ref.generation, ref.ciphertext_sha256, ref.ciphertext_size+1)
    with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_OBJECT_CHANGED'):
        service.read(**inputs(), reference=wrong)


def test_v1059_wrong_wrapping_key_and_kms_failure_never_downgrade_to_plaintext():
    objects = MemoryObjectStore()
    service, _ = sample(objects=objects)
    ref = service.publish(**inputs(), plaintext=SECRET)
    wrong_kms, _ = sample(objects=objects)
    with pytest.raises(ObjectIntegrityError, match='HOSTED_ENCRYPTED_AUTHENTICATION_FAILED'):
        wrong_kms.read(**inputs(), reference=ref)

    class Unavailable(FakeKms):
        def wrap(self, key):
            raise RuntimeError('private content from provider')
    failing, _ = sample(objects=MemoryObjectStore(), kms=Unavailable())
    with pytest.raises(EnvelopeError, match='HOSTED_ENVELOPE_SEAL_FAILED') as error:
        failing.publish(**inputs(), plaintext=SECRET)
    assert 'private content' not in str(error.value)


def test_v1059_limits_and_invalid_identifiers_rejected_before_publish():
    service, objects = sample()
    with pytest.raises(ObjectLimitExceeded, match='HOSTED_ENCRYPTED_PAYLOAD_LIMIT'):
        service.publish(**inputs(), plaintext=b'x' * (16*1024*1024+1))
    with pytest.raises(Exception):
        service.publish(**inputs(), plaintext='not bytes')
    with pytest.raises(Exception):
        service.publish(**{**inputs(), 'record_id': 'illegal/path'}, plaintext=SECRET)
    assert not objects.list_prefix('projects/')


def test_v1059_private_pilot_not_enabled_and_local_runtime_unchanged():
    import json
    coverage = json.loads((ROOT / 'docs/HOSTED_PRIVACY_COVERAGE_M12B.json').read_text())
    assert coverage['auto_activate'] is False
    assert all(v['encryption_coverage'] == 'NOT_INTEGRATED' for v in coverage['firestore_models'].values())
    assert coverage['encrypted_object_pilot']['status'] == 'OPT_IN_NOT_WIRED'
    assert 'hosted.storage.encrypted_objects' not in (ROOT/'src/runtime/local.py').read_text()
    assert 'google-cloud-kms' not in (ROOT/'requirements.txt').read_text()
