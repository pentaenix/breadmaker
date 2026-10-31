"""Build-only public binary manifest and missing-only immutable publisher.

No Google import/authentication during normal local Editor startup. The caller
supplies an object store only in the explicitly authorized release publisher.
Never put the generated large binary assets into the final app image.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from hosted.narration.models import (POOL_BYTES, PREFIX, PinnedModelCatalog, chunk_name, encode_manifest)
from hosted.narration.sealed_parts import PART_BYTES
from hosted.storage.contracts import ObjectAlreadyExists, ObjectIntegrityError


def _safe_file(root, relative):
    root = Path(root).resolve(strict=True)
    path = root / relative
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and root in parent.parents):
        raise ValueError('NARRATION_MODEL_SYMLINK_FORBIDDEN')
    if not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('NARRATION_MODEL_FILE_INVALID')
    return path


def build_manifest(root, paths, *, model_pins):
    assets = []
    # Pins for public model/voice bytes come from the existing checked-in
    # checksums/catalog. Runtime/WASM/dictionary bytes come from the locked build.
    for relative in sorted(paths):
        path = _safe_file(root, relative)
        if not 0 < path.stat().st_size <= 400 * 1024**2:
            raise ObjectIntegrityError('NARRATION_MODEL_SIZE_INVALID')
        if relative.startswith('models/') and model_pins.get(relative, {}).get('size') != path.stat().st_size:
            raise ObjectIntegrityError('NARRATION_MODEL_PIN_MISMATCH')
        digest, chunks, size = hashlib.sha256(), [], 0
        with path.open('rb') as stream:
            while payload := stream.read(PART_BYTES):
                size += len(payload); digest.update(payload)
                chunks.append({'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
        sha = digest.hexdigest()
        if relative.startswith('models/'):
            pin = model_pins.get(relative)
            if pin is None or pin.get('size') != size or pin.get('sha256') != sha:
                raise ObjectIntegrityError('NARRATION_MODEL_PIN_MISMATCH')
        assets.append(dict(id=hashlib.sha256(relative.encode()).hexdigest()[:24],
            path=relative, size=size, sha256=sha, chunks=chunks))
    return PinnedModelCatalog({'v': 1, 'assets': assets})


def publish_missing(objects, catalog, root):
    expected = {}
    for asset in catalog.assets.values():
        for i, part in enumerate(asset['chunks']):
            name = chunk_name(asset['sha256'], i)
            value = (part['size'], part['sha256'])
            if name in expected and expected[name] != value:
                raise ObjectIntegrityError('NARRATION_MODEL_MANIFEST_CONFLICT')
            expected[name] = value
    inventory = objects.list_prefix(PREFIX)
    existing = {item.name: item for item in inventory}
    # Include old/rollback/unknown objects in the capacity bound. Never delete or
    # overwrite them to make a new release fit; refuse before the first upload.
    required = sum(item.size for item in inventory) + sum(value[0] for name, value in expected.items() if name not in existing)
    if required > POOL_BYTES:
        raise ValueError('NARRATION_MODEL_POOL_CAPACITY')
    uploaded, reused = 0, 0
    for asset in catalog.assets.values():
        # Recheck the COMPLETE pinned file before writing ANY of its chunks.
        path = _safe_file(root, asset['path'])
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while payload := stream.read(PART_BYTES):
                digest.update(payload)
        if path.stat().st_size != asset['size'] or digest.hexdigest() != asset['sha256']:
            raise ObjectIntegrityError('NARRATION_MODEL_PIN_MISMATCH')
        with path.open('rb') as stream:
            for i, part in enumerate(asset['chunks']):
                payload = stream.read(PART_BYTES)
                if len(payload) != part['size'] or hashlib.sha256(payload).hexdigest() != part['sha256']:
                    raise ObjectIntegrityError('NARRATION_MODEL_PIN_MISMATCH')
                name = chunk_name(asset['sha256'], i)
                metadata = objects.stat(name)
                if metadata is None:
                    try:
                        metadata = objects.write_chunks(name, [payload], max_bytes=PART_BYTES,
                            content_type='application/octet-stream', if_generation_match=0)
                        uploaded += 1
                    except ObjectAlreadyExists:
                        metadata = objects.stat(name)  # concurrent identical publisher
                else:
                    reused += 1
                if metadata is None or (metadata.size, metadata.sha256) != (part['size'], part['sha256']):
                    raise ObjectIntegrityError('NARRATION_MODEL_ORIGIN_CONFLICT')
                readback = b''.join(objects.read_chunks(name, max_bytes=part['size'], if_generation_match=metadata.generation))
                if readback != payload:
                    raise ObjectIntegrityError('NARRATION_MODEL_ORIGIN_INTEGRITY_FAILED')
    return {'manifest': catalog.digest, 'uploaded_chunks': uploaded, 'reused_chunks': reused}


def build_release(root):
    """Only this checkout's reviewed model/voice pins authorize release bytes."""
    repo = Path(__file__).resolve().parents[2]
    pins = json.loads((repo / 'tools/narration/model-checksums.json').read_text())
    voices = json.loads((repo / 'static/narration-voices.json').read_text())
    model = 'onnx-community/Kokoro-82M-v1.0-ONNX-timestamped'
    if voices['model'] != model or voices['revision'] != 'dd4401a9add81ac692d20e240d22ec9dda82cc29' or len(voices['voices']) != 44:
        raise ValueError('NARRATION_MODEL_CATALOG_CHANGED')
    model_pins = {'models/' + model + '/' + path: pin for path, pin in pins.items()}
    model_pins.update({'models/' + model + '/voices/' + voice['id'] + '.bin': voice for voice in voices['voices']})
    wasm = sorted(path.name for path in (Path(root) / 'transformers').glob('ort-wasm*.wasm'))
    if not wasm:
        raise ValueError('NARRATION_WASM_REQUIRED')
    return build_manifest(root, [*model_pins, *('transformers/' + name for name in wasm), 'dictionaries/en-us.txt'], model_pins=model_pins)


def package_runtime(root, destination, catalog):
    """Explicit small-runtime allowlist; NEVER copy models/dictionaries/WASM."""
    root, destination = Path(root).resolve(strict=True), Path(destination)
    if destination.exists():
        raise ValueError('NARRATION_RUNTIME_DESTINATION_MUST_BE_NEW')
    paths = [path.relative_to(root).as_posix() for folder in ('modules', 'transformers')
             for path in (root / folder).glob('*.mjs')]
    paths += ['transformers/transformers.web.min.js', 'spanish.mjs', 'languages.mjs',
              'HEADTTS-LICENSE', 'TRANSFORMERS-LICENSE', 'SPANISH-G2P-LICENSE', 'SPANISH-NUMBERS-LICENSE']
    if not {'modules/headtts.mjs', 'modules/worker-tts.mjs', 'modules/language-en-us.mjs'} <= set(paths):
        raise ValueError('NARRATION_RUNTIME_MODULES_REQUIRED')
    # Validate the complete input set before creating the output directory.
    files = [(path, _safe_file(root, path)) for path in paths]
    if sum(path.stat().st_size for _, path in files) > 20 * 1024**2:
        raise ValueError('NARRATION_SMALL_RUNTIME_SIZE_INVALID')
    for relative, source in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    tools = Path(__file__).resolve().parent
    shutil.copyfile(tools / 'hosted-assets.mjs', destination / 'hosted-assets.mjs')
    shutil.copyfile(tools / 'hosted-store.mjs', destination / 'hosted-store.mjs')
    shutil.copyfile(tools / 'hosted-device.mjs', destination / 'hosted-device.mjs')
    shutil.copyfile(tools / 'THIRD_PARTY_NOTICES.md', destination / 'THIRD_PARTY_NOTICES.md')
    (destination / 'hosted-manifest.json').write_bytes(encode_manifest(catalog.manifest))
    wrapper = """// Generated from the verified release manifest; loaded only on explicit narration.
import {env} from './transformers/transformers.web.min.js';
import {HostedPublicAssets} from './hosted-assets.mjs';
// Fragment is worker-local: account/project context NEVER enters a static URL's
// network/access log or HTTP cache key. Private headers go only to authorized APIs.
const context=new URLSearchParams(new URL(import.meta.url).hash.slice(1));
const assets=await HostedPublicAssets.open({project:context.get('project'),account:context.get('account'),client:context.get('client')||'',expectedDigest:__DIGEST__,onContextLost:()=>globalThis.postMessage({type:'breadmaker-narration-context-invalid'})});
assets.installFetchInterceptor();
env.fetch=globalThis.fetch;
env.allowRemoteModels=false;env.allowLocalModels=true;
env.useBrowserCache=false;env.useCustomCache=false;env.useFSCache=false;
env.localModelPath='/static/vendor/narration/models/';
env.backends.onnx.wasm.numThreads=globalThis.crossOriginIsolated?Math.min(4,Math.max(1,Math.floor((navigator.hardwareConcurrency||2)/2))):1;
env.backends.onnx.wasm.proxy=false;
env.backends.onnx.wasm.wasmPaths='/static/vendor/narration/transformers/';
export * from './transformers/transformers.web.min.js';
""".replace('__DIGEST__', json.dumps(catalog.digest))
    (destination / 'transformers-hosted.mjs').write_text(wrapper)
    (destination / 'installation.json').write_text(json.dumps(dict(model='onnx-community/Kokoro-82M-v1.0-ONNX-timestamped',
        revision='dd4401a9add81ac692d20e240d22ec9dda82cc29', headtts='1.3.0', transformers='4.3.2',
        hosted=True, manifest=catalog.digest, gpu_available=True, gpu=False)))
    return {'manifest': catalog.digest, 'runtime_files': len(files) + 7}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build')
    build.add_argument('--root', required=True)
    build.add_argument('--runtime-output', required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--root', required=True)
    publish.add_argument('--manifest', required=True)
    publish.add_argument('--project', required=True)
    publish.add_argument('--bucket', required=True)
    publish.add_argument('--confirm', required=True, choices=['PUBLISH_PINNED_MODELS'])
    args = parser.parse_args()
    catalog = build_release(args.root)
    if args.command == 'build':
        result = package_runtime(args.root, args.runtime_output, catalog)
    else:
        trusted = PinnedModelCatalog(json.loads(Path(args.manifest).read_bytes()))
        if trusted.digest != catalog.digest or args.bucket != args.project + '-breadmaker-models':
            raise ValueError('NARRATION_MODEL_PUBLICATION_SCOPE_INVALID')
        from hosted.storage.google_driver import GoogleCloudObjectStore
        result = publish_missing(GoogleCloudObjectStore(bucket=args.bucket, project=args.project), catalog, args.root)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
