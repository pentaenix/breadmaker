"""Default-off activation, exact resource scope, isolated ports and narrow CSP."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from hosted.narration.composition import configured, compose
from hosted.narration.http import build_recording_router
from hosted.narration.models import PinnedModelCatalog, encode_manifest
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage import MemoryObjectStore
from tests.test_v10569_narration_recordings import env, Wrapper, UID, OTHER, PID, wav

ROOT = Path(__file__).resolve().parents[1]


def never(**kwargs):
    raise AssertionError('No provider initialization permitted')


def configuration(tmp_path):
    project = 'breadmaker-prod-511902'
    voices = json.loads((ROOT / 'static/narration-voices.json').read_text())
    prefix = 'models/' + voices['model'] + '/'
    assets = []
    for path in ['onnx/model.onnx', 'onnx/model_quantized.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json']:
        assets.append((prefix + path, 1, '0' * 64))
    assets += [('dictionaries/en-us.txt', 1, '0' * 64), ('transformers/ort-wasm-simd-threaded.wasm', 1, '0' * 64)]
    assets += [(prefix + 'voices/' + voice['id'] + '.bin', voice['size'], voice['sha256']) for voice in voices['voices']]
    manifest = {'v': 1, 'assets': [dict(path=path, id=hashlib.sha256(path.encode()).hexdigest()[:24],
        size=size, sha256=digest, chunks=[dict(size=size, sha256=digest)]) for path, size, digest in assets]}
    target = tmp_path / 'vendor/narration/hosted-manifest.json'
    target.parent.mkdir(parents=True)
    target.write_bytes(encode_manifest(manifest))
    (tmp_path / 'narration-voices.json').write_text(json.dumps(voices))
    values = dict(BREADMAKER_NARRATION_ENABLED='true', BREADMAKER_GCP_PROJECT_ID=project,
        BREADMAKER_NARRATION_BUCKET=project + '-breadmaker-narration',
        BREADMAKER_NARRATION_MODELS_BUCKET=project + '-breadmaker-models',
        BREADMAKER_NARRATION_KMS_KEY=f'projects/{project}/locations/us-central1/keyRings/breadmaker-production-narration/cryptoKeys/derivative-deks-v1',
        BREADMAKER_NARRATION_AUTHORITY_EPOCH='synthetic_epoch',
        BREADMAKER_NARRATION_MANIFEST_SHA256=hashlib.sha256(encode_manifest(manifest)).hexdigest(),
        BREADMAKER_NARRATION_HELPER_URL='https://synthetic-helper-abc123-uc.a.run.app')
    return dict(mode='hosted', auth_mode='identity_platform', environment='production',env=values,static=tmp_path)


def test_disabled_composition_requires_no_credentials_models_or_provider_io(env):
    for flag in ('', 'false', '0'):
        assert configured(env[1], mode='hosted', auth_mode='identity_platform', environment='production',
            env={'BREADMAKER_NARRATION_ENABLED': flag}, object_factory=never, model_object_factory=never, key_factory=never) is None


def test_invalid_scope_and_unverified_manifest_fail_before_provider_initialization(env, tmp_path):
    options = configuration(tmp_path)
    checks = {'BREADMAKER_NARRATION_ENABLED': 'yes', 'BREADMAKER_NARRATION_BUCKET': 'canonical',
        'BREADMAKER_NARRATION_MODELS_BUCKET': 'recovery', 'BREADMAKER_NARRATION_KMS_KEY': 'canonical-key',
        'BREADMAKER_NARRATION_HELPER_URL': 'https://attacker.example', 'BREADMAKER_NARRATION_MANIFEST_SHA256': '1' * 64}
    for key, value in checks.items():
        with pytest.raises(ValueError):
            configured(env[1], **{**options,'env':{**options['env'],key:value}},object_factory=never,model_object_factory=never,key_factory=never)
    with pytest.raises(ValueError, match='PRODUCTION_CONFIGURATION_REQUIRED'):
        configured(env[1], **{**options,'mode':'hosted_dev'},object_factory=never,model_object_factory=never,key_factory=never)


def test_configured_bundle_keeps_dedicated_ports_and_current_production_authority(env, tmp_path):
    options = configuration(tmp_path); calls = []
    def objects(**kwargs):
        calls.append(kwargs);return MemoryObjectStore()
    def key(resource):
        wrapper=Wrapper();wrapper.key_resource=resource;return wrapper
    bundle=configured(env[1],**options,object_factory=objects,model_object_factory=objects,key_factory=key)
    assert bundle.recordings.domain is env[1]
    assert not bundle.recordings.authority.development
    assert bundle.models.documents is env[2] and bundle.pronunciation.budget.documents is env[2]
    assert calls[0]['bucket'].endswith('-narration') and calls[1]['bucket'].endswith('-models')
    assert bundle.models.objects is not bundle.recordings.parts.objects
    # A valid provider cookie alone cannot bypass current server admission.
    with pytest.raises(Exception, match='NARRATION_ACCESS_DENIED'):
        bundle.models.manifest(UID,PID)


def test_explicit_bundle_router_publishes_only_derivatives_and_catalog_uses_current_authority(env):
    manifest=PinnedModelCatalog({'v':1,'assets':[dict(path='dictionaries/en-us.txt',id=hashlib.sha256(b'dictionaries/en-us.txt').hexdigest()[:24],size=1,sha256='0'*64,chunks=[dict(size=1,sha256='0'*64)])]})
    bundle=compose(env[1],envelope=HostedEnvelopeCodec(Wrapper()),objects=env[3],model_objects=MemoryObjectStore(),
        model_catalog=manifest,voice_catalog=env[0].catalog,authority=env[0].authority,epoch='synthetic_epoch',pronunciation_client=SimpleNamespace())
    app=FastAPI();actor=[UID]
    app.include_router(build_recording_router(provider=lambda:bundle.recordings,model_provider=lambda:bundle.models,
        pronunciation_provider=lambda:bundle.pronunciation,identity=lambda:actor[0],project=lambda _:PID))
    http=TestClient(app,headers={'X-Breadmaker-Narration-Account':UID})
    generation=env[1].project_generation(PID,UID)
    assert http.get('/api/narration-voices').json()['voices'][0]['installed'] is False
    made=http.post('/api/narrations',json=dict(metadata=env[4],audio_bytes=len(wav()),operation='bundle')).json()
    assert http.put(f"/api/narrations/uploads/{made['publication']}/parts/0",content=wav()).is_success
    assert http.post(f"/api/narrations/uploads/{made['publication']}/finish").is_success
    assert env[1].project_generation(PID,UID)==generation
    actor[0]=OTHER
    assert http.get('/api/narration-voices',headers={'X-Breadmaker-Narration-Account':OTHER}).status_code==404


def test_hosted_wasm_csp_is_capability_and_surface_scoped_without_changing_auth_isolation(monkeypatch):
    from backend import http_boundary as owner
    enabled=[False]
    runtime=SimpleNamespace(mode='hosted',sessions=SimpleNamespace(metadata=lambda:{'auth_mode':'identity_platform'}),
        capabilities=SimpleNamespace(supports=lambda _:enabled[0]))
    monkeypatch.setattr(owner,'runtime',runtime)
    def headers(path):
        return owner._security_headers(Request(dict(type='http',scheme='https',path=path,headers=[],server=('synthetic.test',443),query_string=b'')))
    assert 'wasm-unsafe-eval' not in headers('/p/'+PID)['Content-Security-Policy']
    enabled[0]=True
    for path in ('/p/'+PID,'/static/vendor/narration/modules/worker-tts.mjs'):
        actual=headers(path)
        assert "'wasm-unsafe-eval'" in actual['Content-Security-Policy']
        assert "'unsafe-eval'" not in actual['Content-Security-Policy']
        assert 'Cross-Origin-Opener-Policy' not in actual and 'Cross-Origin-Embedder-Policy' not in actual
    assert 'wasm-unsafe-eval' not in headers('/auth/login')['Content-Security-Policy']
