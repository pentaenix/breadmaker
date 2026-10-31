"""Fixed public binary manifest, private origin, precharged authenticated reads.

The manifest is produced from the locked release build, NOT from request JSON.
This module never fetches arbitrary URLs, models from the internet, or private
project content. Public binary hashes are suitable for immutable object names.
"""
from __future__ import annotations

import hashlib
import json
import re

from hosted.firestore.contracts import HostedValidationError
from hosted.narration.sealed_parts import PART_BYTES
from hosted.narration.transfers import NarrationTransferBudget
from hosted.storage.contracts import ObjectIntegrityError
from hosted.narration.authority import require_authority

PREFIX = 'narration-public/v1/'
POOL_BYTES = 2 * 1024**3
_SHA = re.compile(r'[0-9a-f]{64}')
_PATH = re.compile(r'(?:models/onnx-community/Kokoro-82M-v1\.0-ONNX-timestamped/'
                   r'(?:config\.json|tokenizer\.json|tokenizer_config\.json|README\.md|'
                   r'onnx/model(?:_quantized)?\.onnx|voices/[a-z][a-z]_[a-z0-9_]+\.bin)'
                   r'|transformers/ort-wasm[a-zA-Z0-9_.-]*\.wasm|dictionaries/en-us\.txt)')


def chunk_name(digest, ordinal):
    if not isinstance(digest, str) or not _SHA.fullmatch(digest) or type(ordinal) is not int or ordinal < 0:
        raise ValueError('NARRATION_MODEL_REFERENCE_INVALID')
    return f'{PREFIX}{digest}/{ordinal:04d}.bin'


def encode_manifest(manifest):
    return json.dumps(manifest, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()


class PinnedModelCatalog:
    def __init__(self, manifest):
        if (not isinstance(manifest, dict) or set(manifest) != {'v', 'assets'}
                or type(manifest.get('v')) is not int or manifest['v'] != 1
                or not isinstance(manifest.get('assets'), list) or not 1 <= len(manifest['assets']) <= 80):
            raise HostedValidationError('NARRATION_MODEL_MANIFEST_INVALID')
        self.assets, self.paths = {}, set()
        total = 0
        for asset in manifest['assets']:
            if (not isinstance(asset, dict) or set(asset) != {'id', 'path', 'size', 'sha256', 'chunks'}
                    or not isinstance(asset['path'], str) or not _PATH.fullmatch(asset['path'])
                    or asset['id'] != hashlib.sha256(asset['path'].encode()).hexdigest()[:24]
                    or asset['id'] in self.assets or asset['path'] in self.paths
                    or type(asset['size']) is not int or not 0 < asset['size'] <= 400 * 1024**2
                    or not isinstance(asset['sha256'], str) or not _SHA.fullmatch(asset['sha256'])
                    or not isinstance(asset['chunks'], list)
                    or len(asset['chunks']) != (asset['size'] + PART_BYTES - 1) // PART_BYTES):
                raise HostedValidationError('NARRATION_MODEL_MANIFEST_INVALID')
            for i, part in enumerate(asset['chunks']):
                if (not isinstance(part, dict) or set(part) != {'size', 'sha256'}
                        or type(part['size']) is not int or part['size'] != min(PART_BYTES, asset['size'] - i * PART_BYTES)
                        or not isinstance(part['sha256'], str) or not _SHA.fullmatch(part['sha256'])):
                    raise HostedValidationError('NARRATION_MODEL_MANIFEST_INVALID')
            total += asset['size']
            self.assets[asset['id']] = json.loads(json.dumps(asset))
            self.paths.add(asset['path'])
        if total > POOL_BYTES or len(encode_manifest(manifest)) > 256 * 1024:
            raise HostedValidationError('NARRATION_MODEL_MANIFEST_TOO_LARGE')
        self.manifest = json.loads(json.dumps(manifest))
        self.digest = hashlib.sha256(encode_manifest(manifest)).hexdigest()

    def get(self, asset_id, ordinal):
        asset = self.assets.get(asset_id) if isinstance(asset_id, str) else None
        if asset is None or type(ordinal) is not int or not 0 <= ordinal < len(asset['chunks']):
            raise HostedValidationError('NARRATION_MODEL_ASSET_INVALID')
        return asset, asset['chunks'][ordinal]


class NarrationModelReads:
    def __init__(self, documents, objects, catalog, *, authority, policy=None, now=None):
        self.documents, self.objects, self.catalog = documents, objects, catalog
        self.authority, self.policy, self.now = authority, policy, now

    def manifest(self, uid, pid):
        self.documents.run_transaction(lambda tx: require_authority(self.authority, tx, uid, pid, 'model'))
        return {'digest': self.catalog.digest, **self.catalog.manifest}

    def read_part(self, uid, pid, asset_id, ordinal):
        asset, part = self.catalog.get(asset_id, ordinal)
        name = chunk_name(asset['sha256'], ordinal)
        budget = NarrationTransferBudget(self.documents, authorize=self.authority, policy=self.policy, now=self.now)
        permit = budget.reserve(account_id=uid, project_id=pid, kind='model', response_bytes=part['size'])
        try:
            metadata = self.objects.stat(name)
            if (metadata is None or metadata.size != part['size'] or metadata.sha256 != part['sha256']):
                raise ObjectIntegrityError('NARRATION_MODEL_NOT_PUBLISHED_OR_INVALID')
            payload = b''.join(self.objects.read_chunks(name, max_bytes=part['size'], if_generation_match=metadata.generation))
            if len(payload) != part['size'] or hashlib.sha256(payload).hexdigest() != part['sha256']:
                raise ObjectIntegrityError('NARRATION_MODEL_INTEGRITY_FAILED')
            self.documents.run_transaction(lambda tx: require_authority(self.authority, tx, uid, pid, 'model'))
            return payload, permit, budget
        except BaseException:
            budget.finish(permit)
            raise
