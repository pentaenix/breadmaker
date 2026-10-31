"""Default-off production composition; no cloud or data I/O when disabled."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time

from hosted.firestore.ids import require_opaque_id
from hosted.narration.authority import NarrationAuthority
from hosted.narration.models import NarrationModelReads, PinnedModelCatalog, encode_manifest
from hosted.narration.pronunciation_budget import PronunciationBudget
from hosted.narration.pronunciation_client import HostedPronunciation, PrivatePronunciationClient
from hosted.narration.recordings import HostedNarrationRecordings
from hosted.narration.sealed_parts import SealedNarrationParts


@dataclass(frozen=True)
class NarrationBundle:
    recordings: HostedNarrationRecordings
    models: NarrationModelReads
    pronunciation: HostedPronunciation
    maintenance: object | None = None


def compose(domain, *, envelope, objects, model_objects, model_catalog,
            voice_catalog, authority, epoch, pronunciation_client):
    """Explicit ports for deterministic tests; never inferred from browser IDs."""
    recordings = HostedNarrationRecordings(domain, SealedNarrationParts(objects, envelope),
        authority=authority, catalog=voice_catalog, epoch=epoch)
    models = NarrationModelReads(domain.documents, model_objects, model_catalog, authority=authority)
    pronunciation = HostedPronunciation(PronunciationBudget(domain.documents, authorize=authority), pronunciation_client)
    return NarrationBundle(recordings, models, pronunciation)


def _public_json(path, maximum):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('NARRATION_RELEASE_FILE_INVALID')
    with path.open('rb') as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError('NARRATION_RELEASE_FILE_INVALID')
    return json.loads(payload)


def configured(domain, *, mode, auth_mode, environment, env=None, static=None,
               object_factory=None, model_object_factory=None, key_factory=None):
    env = os.environ if env is None else env
    enabled = env.get('BREADMAKER_NARRATION_ENABLED', '')
    if enabled in ('', '0', 'false'):
        return None
    if enabled != 'true' or mode != 'hosted' or auth_mode != 'identity_platform' or environment != 'production':
        raise ValueError('NARRATION_PRODUCTION_CONFIGURATION_REQUIRED')
    project = str(env.get('BREADMAKER_GCP_PROJECT_ID', ''))
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project):
        raise ValueError('NARRATION_PROJECT_CONFIGURATION_INVALID')
    exact = {
        'BREADMAKER_NARRATION_BUCKET': project + '-breadmaker-narration',
        'BREADMAKER_NARRATION_MODELS_BUCKET': project + '-breadmaker-models',
        'BREADMAKER_NARRATION_KMS_KEY': f'projects/{project}/locations/us-central1/keyRings/breadmaker-production-narration/cryptoKeys/derivative-deks-v1',
    }
    if any(env.get(key) != value for key, value in exact.items()):
        raise ValueError('NARRATION_RESOURCE_SCOPE_INVALID')
    epoch = require_opaque_id(env.get('BREADMAKER_NARRATION_AUTHORITY_EPOCH', ''))
    digest = str(env.get('BREADMAKER_NARRATION_MANIFEST_SHA256', ''))
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('NARRATION_RELEASE_MANIFEST_REQUIRED')
    client = PrivatePronunciationClient(str(env.get('BREADMAKER_NARRATION_HELPER_URL', '')))
    root = Path(static) if static is not None else Path(__file__).resolve().parents[3] / 'static'
    catalog = PinnedModelCatalog(_public_json(root / 'vendor/narration/hosted-manifest.json', 256 * 1024))
    if hashlib.sha256(encode_manifest(catalog.manifest)).hexdigest() != digest:
        raise ValueError('NARRATION_RELEASE_MANIFEST_MISMATCH')
    voices = _public_json(root / 'narration-voices.json', 64 * 1024)
    if voices.get('model') != 'onnx-community/Kokoro-82M-v1.0-ONNX-timestamped' or voices.get('revision') != 'dd4401a9add81ac692d20e240d22ec9dda82cc29' or len(voices.get('voices', [])) != 44:
        raise ValueError('NARRATION_VOICE_CATALOG_INVALID')
    paths = {asset['path']: asset for asset in catalog.assets.values()}
    prefix = 'models/' + voices['model'] + '/'
    required = {'onnx/model.onnx', 'onnx/model_quantized.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json'}
    if not {prefix + path for path in required} <= paths.keys() or 'dictionaries/en-us.txt' not in paths or not any(path.startswith('transformers/ort-wasm') for path in paths):
        raise ValueError('NARRATION_RELEASE_ASSETS_INCOMPLETE')
    for voice in voices['voices']:
        asset = paths.get(prefix + 'voices/' + voice['id'] + '.bin')
        if asset is None or (asset['size'], asset['sha256']) != (voice['size'], voice['sha256']):
            raise ValueError('NARRATION_RELEASE_VOICE_MISMATCH')
    from hosted.narration.maintenance_composition import configured as configured_maintenance, validate_configuration
    validate_configuration(env, project=project)
    # Validate ALL configuration and trusted release files before initializing SDKs.
    if object_factory is None:
        from hosted.narration.google_objects import GoogleNarrationObjects
        object_factory = GoogleNarrationObjects
    if model_object_factory is None:
        from hosted.narration.google_models import GoogleNarrationModels
        model_object_factory = GoogleNarrationModels
    if key_factory is None:
        from hosted.narration.google_keys import GoogleNarrationKeys
        key_factory = GoogleNarrationKeys
    from hosted.security.envelope import HostedEnvelopeCodec
    authority = NarrationAuthority(domain, issuer='identity-platform://' + project, now=time.time)
    bundle = compose(domain, envelope=HostedEnvelopeCodec(key_factory(exact['BREADMAKER_NARRATION_KMS_KEY'])),
        objects=object_factory(bucket=exact['BREADMAKER_NARRATION_BUCKET'], project=project),
        model_objects=model_object_factory(bucket=exact['BREADMAKER_NARRATION_MODELS_BUCKET'], project=project),
        model_catalog=catalog, voice_catalog=voices, authority=authority, epoch=epoch, pronunciation_client=client)
    from dataclasses import replace
    return replace(bundle, maintenance=configured_maintenance(env, project=project, objects=bundle.recordings.parts.objects))
