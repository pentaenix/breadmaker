"""M12B conformance contracts. Human user runs the verification suite."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))

from hosted.security.envelope import HostedEnvelopeCodec, EnvelopeError, FORMAT
from hosted.security.metrics import DailyUsageMetrics, MetricsValidationError
from hosted.firestore.model_registry import MODELS

RESOURCE='projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
A={'project_id':'a'*32,'entity':'manuscript_block','record_id':'b'*32}

class FakeWrapper:
    """Deterministic in-process key-service stand-in; never persisted/used in prod."""
    key_resource=RESOURCE
    def __init__(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._key=secrets.token_bytes(32)
    def wrap(self,dek):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce=secrets.token_bytes(12)
        return nonce+AESGCM(self._key).encrypt(nonce,dek,b'bm-test-wrap-v1')
    def unwrap(self,value):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(self._key).decrypt(value[:12],value[12:],b'bm-test-wrap-v1')


def test_v1057_envelope_round_trip_and_independent_nonces():
    codec=HostedEnvelopeCodec(FakeWrapper())
    one=codec.seal(b'unpublished secret',context=A)
    two=codec.seal(b'unpublished secret',context=A)
    assert one['ciphertext']!=two['ciphertext'] and one['wrapped_key']!=two['wrapped_key']
    assert one['format']==FORMAT and 'unpublished secret' not in json.dumps(one)
    assert codec.open(one,context=A)==b'unpublished secret'


def test_v1057_rejects_tamper_swapped_project_and_wrong_key():
    codec=HostedEnvelopeCodec(FakeWrapper()); blob=codec.seal(b'Secret title',context=A)
    with pytest.raises(EnvelopeError):codec.open(blob,context={**A,'project_id':'c'*32})
    with pytest.raises(EnvelopeError):codec.open(blob,context={**A,'record_id':'d'*32})
    with pytest.raises(EnvelopeError):HostedEnvelopeCodec(FakeWrapper()).open(blob,context=A)
    with pytest.raises(EnvelopeError):codec.open({**blob,'ciphertext':'AAAA'},context=A)
    with pytest.raises(EnvelopeError):codec.open({**blob,'format':'plaintext'},context=A)
    with pytest.raises(EnvelopeError):codec.open({**blob,'unexpected':'secret'},context=A)


def test_v1057_kms_outage_fails_closed_and_error_is_structural():
    class Down(FakeWrapper):
        def wrap(self,raw_key):raise RuntimeError('manuscript secret from provider')
    with pytest.raises(EnvelopeError,match='HOSTED_ENVELOPE_SEAL_FAILED') as err:
        HostedEnvelopeCodec(Down()).seal(b'Manifesto',context=A)
    assert 'secret from provider' not in str(err.value)


def test_v1057_metrics_reject_content_and_invalid_numbers():
    counts={'words':1200,'characters':7500,'estimated_pages':5,'projects':2,'active_minutes':34}
    m=DailyUsageMetrics.from_counts(user_id='usr_'+'a'*32,date_utc='2026-10-12',counts=counts)
    assert m.document_path()==('users','usr_'+'a'*32,'usage_daily','2026-10-12')
    assert 'title' not in m.public() and m.public()['words']==1200
    for changed in ({**counts,'manuscript':'secret'},{**counts,'words':True},{**counts,'words':-1}):
        with pytest.raises(MetricsValidationError):
            DailyUsageMetrics.from_counts(user_id=m.user_id,date_utc=m.date_utc,counts=changed)
    with pytest.raises(MetricsValidationError):
        DailyUsageMetrics.from_counts(user_id=m.user_id,date_utc='2026-02-30',counts=counts)


def test_v1057_every_hosted_model_is_classified_and_activation_off():
    m=json.loads((ROOT/'docs/HOSTED_PRIVACY_COVERAGE_M12B.json').read_text())
    assert set(m['firestore_models'])==set(MODELS)
    assert all(v['encryption_coverage']=='NOT_INTEGRATED' for v in m['firestore_models'].values())
    assert m['auto_activate'] is False and m['collection_enabled'] is False
    assert m['metrics']['content_fields_allowed'] is False


def test_v1057_nested_lookup_is_project_scoped_not_collection_group():
    src=(ROOT/'src/hosted/http_compat.py').read_text()
    begin=src.index('def _locate(');end=src.index('def _all_blocks(',begin)
    locate=src[begin:end]
    assert 'query_collection_group(' not in locate
    assert 'query_collection(' in locate
    assert '_MAX_NESTED_LOOKUP_ROWS' in locate
    assert '_get(logical, candidate)' in locate


def test_v1057_local_editor_has_no_hosted_kms_or_telemetry_dependency():
    req=(ROOT/'requirements.txt').read_text()
    assert 'google-cloud-kms' not in req and 'google-crc32c' not in req
    assert 'google-cloud-kms==3.17.0' in (ROOT/'requirements-hosted.txt').read_text()
    local=(ROOT/'src/runtime/local.py').read_text()
    assert 'hosted.security' not in local and 'cloud_kms' not in local
