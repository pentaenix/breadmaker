"""Build-only public binary manifest and missing-only immutable publisher.

No Google import/authentication during normal local Editor startup. The caller
supplies an object store only in the explicitly authorized release publisher.
Never put the generated large binary assets into the final app image.
"""
import hashlib
import json
from pathlib import Path

from hosted.narration.models import (POOL_BYTES, PREFIX, PinnedModelCatalog, chunk_name)
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
