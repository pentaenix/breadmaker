"""Tiny synthetic model binaries; no actual model download or cloud mutation."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hosted.firestore.contracts import HostedValidationError, ProjectUnavailable

from hosted.narration.http import build_recording_router
from hosted.narration.models import PinnedModelCatalog, NarrationModelReads, chunk_name, POOL_BYTES
from hosted.narration.transfers import TransferDenied, TransferPolicy
from hosted.storage.contracts import ObjectIntegrityError, ObjectMetadata
from hosted.storage.memory import MemoryObjectStore
from tests.test_v10569_narration_recordings import env, UID, PID, OTHER

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'narration'))
from hosted_manifest import build_manifest, publish_missing, package_runtime, build_release

PATH = 'models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/onnx/model_quantized.onnx'


@pytest.fixture
def models(tmp_path, env):
    import hashlib
    payload = b'Synthetic public model binary' * 32
    path = tmp_path / PATH
    path.parent.mkdir(parents=True); path.write_bytes(payload)
    pin = {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    catalog = build_manifest(tmp_path, [PATH], model_pins={PATH: pin})
    objects = MemoryObjectStore()
    service = NarrationModelReads(env[2], objects, catalog, authority=env[0].authority)
    return service, objects, catalog, tmp_path, payload


def test_only_fixed_public_paths_and_exact_chunk_lengths_are_accepted(models):
    catalog = models[2]
    assert catalog.get(next(iter(catalog.assets)), 0)[0]['path'] == PATH
    for path in ['https://example.test/private', '../private', 'models/../../private',
                 '/api/assets/private.png', 'transformers/arbitrary.wasm', 'dictionaries/unknown.txt']:
        manifest = deepcopy(catalog.manifest)
        manifest['assets'][0]['path'] = path
        with pytest.raises(HostedValidationError, match='MANIFEST_INVALID'):
            PinnedModelCatalog(manifest)
    manifest = deepcopy(catalog.manifest)
    manifest['assets'][0]['chunks'][0]['size'] -= 1
    with pytest.raises(HostedValidationError, match='MANIFEST_INVALID'):
        PinnedModelCatalog(manifest)


def test_wrong_public_model_pin_rejected_before_publication(tmp_path):
    path = tmp_path / PATH
    path.parent.mkdir(parents=True); path.write_bytes(b'Synthetic public model')
    with pytest.raises(ObjectIntegrityError, match='PIN_MISMATCH'):
        build_manifest(tmp_path, [PATH], model_pins={PATH: {'size': path.stat().st_size, 'sha256': '0' * 64}})


def test_missing_only_publisher_is_resumable_and_never_overwrites(models):
    _, objects, catalog, root, _ = models
    assert publish_missing(objects, catalog, root)['uploaded_chunks'] == 1
    row = objects.list_prefix('narration-public/')[0]
    assert publish_missing(objects, catalog, root) == {
        'manifest': catalog.digest, 'uploaded_chunks': 0, 'reused_chunks': 1}
    assert objects.list_prefix('narration-public/')[0].generation == row.generation


def test_publisher_detects_wrong_existing_object_without_overwriting(models):
    _, objects, catalog, root, payload = models
    asset = next(iter(catalog.assets.values()))
    name = chunk_name(asset['sha256'], 0)
    objects.write_chunks(name, [b'x' * len(payload)], max_bytes=len(payload),
        content_type='application/octet-stream', if_generation_match=0)
    original = objects.stat(name)
    with pytest.raises(ObjectIntegrityError, match='ORIGIN_CONFLICT'):
        publish_missing(objects, catalog, root)
    assert objects.stat(name) == original


def test_publisher_refuses_pool_overflow_without_deleting_old_rollback_assets(models):
    _, objects, catalog, root, _ = models
    original = objects.list_prefix
    objects.list_prefix = lambda prefix: [ObjectMetadata('narration-public/v1/old', 1, POOL_BYTES, '0' * 64, 'application/octet-stream')]
    with pytest.raises(ValueError, match='POOL_CAPACITY'):
        publish_missing(objects, catalog, root)
    objects.list_prefix = original
    assert not objects.list_prefix('narration-public/')


def test_binary_parts_are_charged_before_object_access_and_lease_released_by_caller(models, env):
    service, objects, catalog, root, payload = models
    publish_missing(objects, catalog, root)
    body, permit, budget = service.read_part(UID, PID, next(iter(catalog.assets)), 0)
    assert body == payload and permit.kind == 'model' and permit.response_bytes == len(payload)
    assert budget.finish(permit)
    usage = next(row.data for row in env[2].query_collection(('narration_transfer_usage_v1',)) if row.path[-1].startswith('account_'))
    assert usage['model_bytes'] == len(payload) and not usage['active']


def test_model_quota_denial_precedes_even_origin_metadata_access(models):
    service, objects, catalog, _, _ = models
    service.policy = TransferPolicy(model_bytes=1)
    def prohibited(*args, **kwargs):
        raise AssertionError('origin access before admission')
    objects.stat = prohibited
    with pytest.raises(TransferDenied):
        service.read_part(UID, PID, next(iter(catalog.assets)), 0)


def test_integrity_failure_is_charged_but_does_not_leak_concurrency(models, env):
    service, objects, catalog, root, _ = models
    publish_missing(objects, catalog, root)
    original = objects.read_chunks
    objects.read_chunks = lambda *args, **kwargs: [b'corrupt']
    with pytest.raises(ObjectIntegrityError):
        service.read_part(UID, PID, next(iter(catalog.assets)), 0)
    usage = next(row.data for row in env[2].query_collection(('narration_transfer_usage_v1',)) if row.path[-1].startswith('account_'))
    assert usage['model_bytes'] > 0 and not usage['active']
    objects.read_chunks = original


def test_model_manifest_and_chunks_use_real_project_authority(models):
    service, _, catalog, _, _ = models
    with pytest.raises(ProjectUnavailable):
        service.manifest(OTHER, PID)
    with pytest.raises(ProjectUnavailable):
        service.read_part(OTHER, PID, next(iter(catalog.assets)), 0)
    assert service.manifest(UID, PID)['digest'] == catalog.digest


def test_http_model_delivery_is_private_and_uses_bounded_transfer_response(models, env):
    service, objects, catalog, root, payload = models
    publish_missing(objects, catalog, root)
    app = FastAPI()
    app.include_router(build_recording_router(provider=lambda: env[0], identity=lambda: UID,
        project=lambda request: PID, model_provider=lambda: service))
    http = TestClient(app, headers={'X-Breadmaker-Narration-Account': UID})
    assert http.get('/api/narration-models').json()['digest'] == catalog.digest
    response = http.get('/api/narration-models/' + next(iter(catalog.assets)) + '?part=0')
    assert response.status_code == 200 and response.content == payload
    assert response.headers['cache-control'] == 'private, no-store'
    assert http.get('/api/narration-models/arbitrary?part=0').status_code == 400
    assert http.get('/api/narration-models/' + next(iter(catalog.assets)) + '?part=1').status_code == 400


def test_symlinked_model_file_is_rejected_before_read(tmp_path):
    path = tmp_path / PATH
    path.parent.mkdir(parents=True)
    target = tmp_path / 'other'
    target.write_bytes(b'Synthetic')
    path.symlink_to(target)
    with pytest.raises(ValueError, match='SYMLINK_FORBIDDEN'):
        build_manifest(tmp_path, [PATH], model_pins={})


def runtime_inputs(root):
    files = ['modules/headtts.mjs', 'modules/worker-tts.mjs', 'modules/language-en-us.mjs',
             'transformers/ort-wasm-simd-threaded.mjs', 'transformers/transformers.web.min.js',
             'spanish.mjs', 'languages.mjs', 'HEADTTS-LICENSE', 'TRANSFORMERS-LICENSE',
             'SPANISH-G2P-LICENSE', 'SPANISH-NUMBERS-LICENSE']
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('// Synthetic public runtime')
    for name in ('transformers/ort-wasm-simd-threaded.wasm', 'dictionaries/en-us.txt',
                 'models/private-do-not-copy', '.env', 'credentials.json'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Synthetic not part of runtime')


def test_small_hosted_runtime_excludes_all_binaries_dictionaries_and_unlisted_files(models, tmp_path):
    import json
    _, _, catalog, root, _ = models
    runtime_inputs(root)
    output = tmp_path / 'packaged'
    result = package_runtime(root, output, catalog)
    assert result['manifest'] == catalog.digest
    files = [path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()]
    assert len(files) == result['runtime_files']
    assert not any(path.endswith(('.onnx', '.wasm', '.bin', '.txt')) or path.startswith(('models/', 'dictionaries/')) for path in files)
    assert '.env' not in files and 'credentials.json' not in files
    assert json.loads((output / 'hosted-manifest.json').read_text()) == catalog.manifest
    assert json.loads((output / 'installation.json').read_text())['gpu'] is False


def test_generated_worker_loader_pins_manifest_and_preserves_native_backend_rules(models, tmp_path):
    _, _, catalog, root, _ = models
    runtime_inputs(root)
    output = tmp_path / 'packaged'
    package_runtime(root, output, catalog)
    wrapper = (output / 'transformers-hosted.mjs').read_text()
    assert catalog.digest in wrapper and 'HostedPublicAssets.open' in wrapper
    assert 'env.fetch=globalThis.fetch' in wrapper and 'env.allowRemoteModels=false' in wrapper
    assert 'env.useBrowserCache=false' in wrapper and 'env.useCustomCache=false' in wrapper
    assert 'crossOriginIsolated' in wrapper and 'wasm.proxy=false' in wrapper
    assert 'context.get(\'project\')' in wrapper and 'context.get(\'client\')' in wrapper


def test_packaging_refuses_missing_modules_symlinks_and_existing_destination(models, tmp_path):
    _, _, catalog, root, _ = models
    with pytest.raises(ValueError, match='MODULES_REQUIRED'):
        package_runtime(root, tmp_path / 'missing', catalog)
    runtime_inputs(root)
    file = root / 'modules/headtts.mjs'
    file.unlink(); file.symlink_to(root / 'modules/worker-tts.mjs')
    with pytest.raises(ValueError, match='SYMLINK_FORBIDDEN'):
        package_runtime(root, tmp_path / 'symlink', catalog)
    assert not (tmp_path / 'symlink').exists()
    with pytest.raises(ValueError, match='DESTINATION_MUST_BE_NEW'):
        package_runtime(root, root, catalog)


def test_release_manifest_requires_complete_pinned_models_and_wasm(tmp_path):
    with pytest.raises(ValueError, match='WASM_REQUIRED'):
        build_release(tmp_path)
    path = tmp_path / 'transformers/ort-wasm-simd-threaded.wasm'
    path.parent.mkdir(); path.write_bytes(b'Synthetic')
    # A tiny valid q8 file is not enough: fp32/config/tokenizer and ALL curated
    # voice pins must be available and verified before a release pack is built.
    with pytest.raises(ValueError, match='FILE_INVALID'):
        build_release(tmp_path)
