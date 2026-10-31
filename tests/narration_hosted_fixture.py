"""Explicit test-only in-memory bundle; never production config or user data."""
import hashlib
import json
from pathlib import Path

PAYLOAD = b'Synthetic public voice bytes'
VOICE_PATH = 'models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/voices/af_heart.bin'


def public_manifest():
    digest=hashlib.sha256(PAYLOAD).hexdigest()
    return {'v':1,'assets':[dict(path=VOICE_PATH,id=hashlib.sha256(VOICE_PATH.encode()).hexdigest()[:24],
        size=len(PAYLOAD),sha256=digest,chunks=[dict(size=len(PAYLOAD),sha256=digest)])]}


def create_app():
    from app import app
    from backend.environment import runtime
    from hosted.narration.authority import NarrationAuthority
    from hosted.narration.composition import compose
    from hosted.narration.models import PinnedModelCatalog, chunk_name
    from hosted.security.envelope import HostedEnvelopeCodec
    from hosted.storage import MemoryObjectStore
    from tests.test_v10569_narration_recordings import Wrapper
    import time
    assert runtime.mode=='hosted_dev' and runtime.capabilities.metadata().get('auth_mode')=='developer'
    catalog=json.loads((Path(__file__).resolve().parents[1]/'static/narration-voices.json').read_text())
    voice=next(item for item in catalog['voices'] if item['id']=='af_heart')
    voice.update(size=len(PAYLOAD),sha256=hashlib.sha256(PAYLOAD).hexdigest())
    catalog['voices']=[voice]
    models=PinnedModelCatalog(public_manifest());model_objects=MemoryObjectStore()
    model_objects.write_chunks(chunk_name(voice['sha256'],0),[PAYLOAD],max_bytes=len(PAYLOAD),
        content_type='application/octet-stream',if_generation_match=0)
    bundle=compose(runtime.projects.domain,envelope=HostedEnvelopeCodec(Wrapper()),
        objects=MemoryObjectStore(),model_objects=model_objects,model_catalog=models,voice_catalog=catalog,
        authority=NarrationAuthority(runtime.projects.domain,issuer='',now=time.time,development=True),
        epoch='synthetic_browser_epoch',pronunciation_client=object())
    # Intentional explicit test injection; production configuration forbids this
    # developer mode and never imports this module. Frozen runtime API stays so.
    object.__setattr__(runtime,'narration',bundle)
    runtime.capabilities._capabilities['narration']=True
    return app
