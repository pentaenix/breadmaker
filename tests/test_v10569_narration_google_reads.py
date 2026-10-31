"""Bounded read-only model/KMS RPCs using fake SDKs, no Google credentials."""
import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from hosted.narration.google_models import GoogleNarrationModels
from hosted.narration.google_keys import GoogleNarrationKeys
from hosted.narration.io_limits import deadline, io_timeout
from hosted.narration.http import run_provider
from tests.test_v10569_narration_google_objects import Bucket

PROJECT='breadmaker-prod-511902'
KEY=f'projects/{PROJECT}/locations/us-central1/keyRings/breadmaker-production-narration/cryptoKeys/derivative-deks-v1'
NAME='narration-public/v1/'+'a'*64+'/0000.bin'


def test_model_provider_scope_readonly_and_exact_generation_bounded_raw_download():
    bucket=Bucket()
    objects=GoogleNarrationModels(project=PROJECT,bucket=PROJECT+'-breadmaker-models',client=SimpleNamespace(bucket=lambda _:bucket))
    payload=b'Synthetic public model'
    item=bucket.memory.write_chunks(NAME,[payload],max_bytes=len(payload),content_type='application/octet-stream',
        if_generation_match=0,metadata={'breadmaker-sha256':hashlib.sha256(payload).hexdigest()})
    assert objects.stat(NAME).generation==item.generation
    assert b''.join(objects.read_chunks(NAME,max_bytes=len(payload),if_generation_match=item.generation))==payload
    call=bucket.calls[-1][1]
    assert call['end']==len(payload) and call['retry'] is None and 0<call['timeout']<=10 and call['raw_download'] is True
    for operation in (objects.delete,objects.write_chunks,objects.list_prefix):
        with pytest.raises(ValueError,match='READ_ONLY'):operation(NAME)
    for path in ('projects/private/body.bin','narration-public/../private',NAME+'/unknown'):
        with pytest.raises(ValueError,match='SCOPE_INVALID'):objects.stat(path)
    with pytest.raises(ValueError,match='SCOPE_INVALID'):
        GoogleNarrationModels(project=PROJECT,bucket=PROJECT+'-breadmaker-narration')


def test_kms_timeout_and_no_retry_are_shared_with_http_worker_context(monkeypatch):
    from hosted.narration import io_limits
    monkeypatch.setattr(io_limits,'time',SimpleNamespace(monotonic=lambda:100))
    calls=[]
    def rpc(**kwargs):calls.append(kwargs);return object()
    key=GoogleNarrationKeys(KEY,client=SimpleNamespace(encrypt=rpc,decrypt=rpc))
    token=deadline.set(103)
    try:
        key._client.encrypt(request={'name':KEY})
        key._client.decrypt(request={'name':KEY})
        assert all(call['retry'] is None and call['timeout']==3 for call in calls)
        assert asyncio.run(run_provider(lambda:io_timeout(20)))==3
        expired=deadline.set(99)
        try:
            with pytest.raises(TimeoutError,match='PROVIDER_DEADLINE'):key._client.encrypt(request={})
            assert len(calls)==2
        finally:deadline.reset(expired)
    finally:deadline.reset(token)
