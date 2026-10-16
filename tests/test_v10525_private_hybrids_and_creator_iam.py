"""M12 source-level private hybrid and creator-only GCS regressions.

User-owned ./verify-release only; never executed by the assistant.
"""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.encrypted_recovery import HostedEncryptedRecoveryWriter, HostedEncryptedRecoveryReader
from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer
from hosted.firestore.repositories import HostedProjectRepository
from hosted.firestore.contracts import HostedValidationError
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.memory import MemoryObjectStore
from test_v10524_private_live_restore import OWNER, DEST, private_store


def test_v10525_private_project_root_title_sealed_and_fail_closed_without_codec():
    docs, _objects, projects, _codec = private_store(OWNER)
    pid = projects.create('Top secret draft')['id']
    root = docs.get(('projects', pid)).data
    assert root['title'] == 'Private project'
    assert b'Top secret draft' not in str(root).encode()
    assert isinstance(root['_bm_private_title'], dict)
    assert projects.authority.get_for_user(pid, OWNER)['title'] == 'Top secret draft'
    outsider = HostedProjectRepository(docs)
    with pytest.raises(HostedValidationError, match='TITLE_CODEC_REQUIRED'):
        outsider.get_for_user(pid, OWNER)
    projects.rename(pid, 'Still secret')
    changed = docs.get(('projects', pid)).data
    assert b'Still secret' not in str(changed).encode()
    assert projects.authority.get_for_user(pid, OWNER)['title'] == 'Still secret'


def test_v10525_private_cuts_checkpoints_review_snapshots_and_participants_restore():
    _docs, live, source, codec = private_store(OWNER)
    pid = source.create('An unpublished novel')['id']
    source.domain.create('cut', {'project_id': pid}, {'content_html': '<p>Secret cut</p>'},
                         user_id=OWNER, entity_id='cut_private')
    source.domain.create('checkpoint', {'project_id': pid}, {'snapshot': 'Hidden checkpoint'},
                         user_id=OWNER, entity_id='checkpoint_private')
    source.domain.create('review', {'project_id': pid}, {'title': 'My private review'},
                         user_id=OWNER, entity_id='review_private')
    source.domain.create('review_snapshot', {'project_id': pid, 'review_id': 'review_private'},
                         {'snapshot': 'Hidden review content'}, user_id=OWNER,
                         entity_id='snapshot_private')
    source.domain.create('review_participant', {'project_id': pid, 'review_id': 'review_private'},
                         {'name': 'Secret reviewer', 'notes': 'Unpublished feedback'},
                         user_id=OWNER, entity_id='participant_private')
    assert source.domain.get('cut', {'project_id': pid, 'cut_id': 'cut_private'}, user_id=OWNER)['content_html'] == '<p>Secret cut</p>'
    assert source.domain.get('checkpoint', {'project_id': pid, 'checkpoint_id': 'checkpoint_private'}, user_id=OWNER)['snapshot'] == 'Hidden checkpoint'
    assert source.domain.get('review_snapshot', {'project_id': pid, 'review_id': 'review_private',
        'snapshot_id': 'snapshot_private'}, user_id=OWNER)['snapshot'] == 'Hidden review content'
    assert source.domain.get('review_participant', {'project_id': pid, 'review_id': 'review_private',
        'participant_id': 'participant_private'}, user_id=OWNER)['name'] == 'Secret reviewer'
    for meta in live.list_prefix(f'projects/{pid}/'):
        assert '/private/v1/' in meta.name
        assert meta.content_type == 'application/vnd.breadmaker.encrypted+json'
        ciphertext = b''.join(live.read_chunks(meta.name, max_bytes=20 * 1024 * 1024))
        assert b'Secret' not in ciphertext and b'Hidden' not in ciphertext
    bucket = MemoryObjectStore()
    HostedEncryptedRecoveryWriter(source, bucket, codec).create(pid, OWNER, operation='private_models')
    reader = HostedEncryptedRecoveryReader(bucket, codec, authorize=lambda p, u, sid: p == pid and u == OWNER)
    _docs2, _live2, dest, _codec2 = private_store(DEST)
    restored = HostedEncryptedLiveRestorer(reader, dest, authorize_destination=lambda u: u == DEST).restore(
        pid, OWNER, DEST, operation='private_models', restore_operation='private_models_restore')
    other = restored.destination_project_id
    assert dest.domain.get('cut', {'project_id': other, 'cut_id': 'cut_private'}, user_id=DEST)['content_html'] == '<p>Secret cut</p>'
    assert dest.domain.get('checkpoint', {'project_id': other, 'checkpoint_id': 'checkpoint_private'}, user_id=DEST)['snapshot'] == 'Hidden checkpoint'
    assert dest.domain.get('review_snapshot', {'project_id': other, 'review_id': 'review_private',
        'snapshot_id': 'snapshot_private'}, user_id=DEST)['snapshot'] == 'Hidden review content'
    assert dest.domain.get('review_participant', {'project_id': other, 'review_id': 'review_private',
        'participant_id': 'participant_private'}, user_id=DEST)['name'] == 'Secret reviewer'


def test_v10525_generic_gcs_writer_does_not_require_patch_or_object_update():
    class Writer(io.BytesIO):
        def __init__(self, blob):
            super().__init__()
            self.blob = blob
        def close(self):
            if not self.closed:
                self.blob.payload = self.getvalue()
                self.blob.size = len(self.blob.payload)
                self.blob.generation = 1
            super().close()
    class Blob:
        name = 'projects/someproject/private/v1/manuscript_block/b_1/op.bme'
        generation = 0
        size = 0
        content_type = 'application/vnd.breadmaker.encrypted+json'
        metadata = {}
        def open(self, mode, **kwargs):
            assert mode == 'wb' and kwargs['if_generation_match'] == 0
            return Writer(self)
        def reload(self, **kwargs):
            assert not kwargs or kwargs.get('if_generation_match') == 1
        def patch(self, **kwargs):
            raise AssertionError('GCS Object Creator must not patch metadata')
    class Bucket:
        def __init__(self):
            self.blob_obj = Blob()
        def blob(self, key):
            return self.blob_obj
    class Client:
        def __init__(self):
            self.bucket_obj = Bucket()
        def bucket(self, name):
            return self.bucket_obj
    client = Client()
    store = GoogleCloudObjectStore(bucket='private-bucket', client=client)
    data = b'example immutable ciphertext'
    meta = store.write_chunks('projects/someproject/private/v1/manuscript_block/b_1/op.bme',
                              [data], max_bytes=10000, content_type=Blob.content_type,
                              if_generation_match=0, metadata={'breadmaker-private-format': 'bm-hosted-envelope-v1'})
    assert meta.sha256 == hashlib.sha256(data).hexdigest()
    assert client.bucket_obj.blob_obj.payload == data


def test_v10525_encrypted_revision_history_survives_cross_project_recovery():
    from hosted.runtime_services import HostedHistoryStore

    docs, live, source, codec = private_store(OWNER)
    pid = source.create('Novel with private history')['id']
    history = HostedHistoryStore(docs, live, lambda: OWNER, lambda: pid,
        encrypted_fields=source.domain.encrypted_fields,
        encrypted_objects=source.domain.encrypted_objects)
    original_bytes = b'private editor revision with unpublished author notes'
    written = history.create_revision(original_bytes, {'summary': 'Private draft saved'})
    rid = written['id']
    assert written['summary'] == 'Private draft saved'
    assert 'envelope_b64' not in written
    assert 'envelope_b64' not in history.list_revisions()[0]
    metadata, body = history.read_revision(rid)
    assert metadata['summary'] == 'Private draft saved' and body == original_bytes
    raw = docs.get(('projects', pid, 'revisions', rid))
    assert '_bm_private_fields' in raw.data
    assert b'Private draft' not in repr(raw.data).encode('utf-8')
    object_names = [meta.name for meta in live.list_prefix(f'projects/{pid}/')]
    assert any('/private/v1/hosted_revision_history/' in name for name in object_names)
    assert not any('/revisions/' in name for name in object_names)
    bucket = MemoryObjectStore()
    HostedEncryptedRecoveryWriter(source, bucket, codec).create(pid, OWNER, operation='history_snapshot')
    reader = HostedEncryptedRecoveryReader(bucket, codec,
        authorize=lambda source_id, owner_id, _sid: source_id == pid and owner_id == OWNER)
    restored_docs, restored_gcs, destination, _ = private_store(DEST)
    new_pid = HostedEncryptedLiveRestorer(reader, destination,
        authorize_destination=lambda owner_id: owner_id == DEST).restore(
            pid, OWNER, DEST, operation='history_snapshot',
            restore_operation='history_restored').destination_project_id
    restored_history = HostedHistoryStore(restored_docs, restored_gcs,
        lambda: DEST, lambda: new_pid,
        encrypted_fields=destination.domain.encrypted_fields,
        encrypted_objects=destination.domain.encrypted_objects)
    restored_meta, restored_bytes = restored_history.read_revision(rid)
    assert restored_meta['summary'] == 'Private draft saved'
    assert restored_bytes == original_bytes
    assert restored_history.list_revisions()[0]['id'] == rid


def test_v10525_history_codec_cannot_be_half_enabled():
    from hosted.runtime_services import HostedHistoryStore
    docs, live, source, _codec = private_store(OWNER)
    with pytest.raises(ValueError, match='BOTH_CODECS_REQUIRED'):
        HostedHistoryStore(docs, live, lambda: OWNER, lambda: '0' * 32,
                           encrypted_fields=source.domain.encrypted_fields)


def test_v10525_encrypted_image_asset_roundtrips_with_sealed_recovery():
    import base64
    from hosted.runtime_services import HostedCommittedAssetStore

    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')
    docs, live, source, codec = private_store(OWNER)
    pid = source.create('Book with a secret diagram')['id']
    assets = HostedCommittedAssetStore(docs, live, lambda: OWNER, lambda: pid,
        encrypted_fields=source.domain.encrypted_fields,
        encrypted_objects=source.domain.encrypted_objects)
    uploaded = assets.put_blob(png, '.png')
    assert uploaded.name.startswith('img_') and uploaded.name.endswith('.png')
    assert uploaded.name != 'img_' + hashlib.sha256(png).hexdigest()[:24] + '.png'
    assert uploaded.created and assets.exists(uploaded.name)
    assert assets.read_blob(uploaded.api_path) == png
    records = docs.query_collection(('projects', pid, 'assets'))
    assert len(records) == 1 and '_bm_private_fields' in records[0].data
    assert 'sha256' not in records[0].data
    assert not any('/assets/sha256/' in obj.name for obj in live.list_prefix(f'projects/{pid}/'))
    for obj in live.list_prefix(f'projects/{pid}/private/v1/project_asset/'):
        assert obj.content_type == 'application/vnd.breadmaker.encrypted+json'
        assert obj.metadata['breadmaker-private-format'] == 'bm-hosted-envelope-v1'
        assert png not in b''.join(live.read_chunks(obj.name, max_bytes=24 * 1024 * 1024))
    bucket = MemoryObjectStore()
    HostedEncryptedRecoveryWriter(source, bucket, codec).create(pid, OWNER, operation='image_backup')
    reader = HostedEncryptedRecoveryReader(bucket, codec,
        authorize=lambda p, u, _sid: p == pid and u == OWNER)
    new_docs, new_objects, destination, _ = private_store(DEST)
    other = HostedEncryptedLiveRestorer(reader, destination,
        authorize_destination=lambda u: u == DEST).restore(
            pid, OWNER, DEST, operation='image_backup',
            restore_operation='image_recovery').destination_project_id
    restored_assets = HostedCommittedAssetStore(new_docs, new_objects,
        lambda: DEST, lambda: other,
        encrypted_fields=destination.domain.encrypted_fields,
        encrypted_objects=destination.domain.encrypted_objects)
    assert restored_assets.exists(uploaded.name)
    assert restored_assets.read_blob(uploaded.api_path) == png
    restored_asset_docs = new_docs.query_collection(('projects', other, 'assets'))
    assert len(restored_asset_docs) == 1
    assert '_bm_private_fields' in restored_asset_docs[0].data
    assert restored_asset_docs[0].data != records[0].data


def test_v10525_image_asset_codec_cannot_be_half_enabled():
    from hosted.runtime_services import HostedCommittedAssetStore
    docs, live, source, _ = private_store(OWNER)
    with pytest.raises(ValueError, match='BOTH_CODECS_REQUIRED'):
        HostedCommittedAssetStore(docs, live, lambda: OWNER, lambda: '0' * 32,
                                  encrypted_objects=source.domain.encrypted_objects)


def test_v10525_legacy_asset_writer_cannot_leak_private_project_orphans():
    import base64
    from hosted.runtime_services import HostedCommittedAssetStore

    png=base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==')
    docs, live, source, _ = private_store(OWNER)
    pid=source.create('Never persist this as a filename')['id']
    legacy=HostedCommittedAssetStore(docs,live,lambda:OWNER,lambda:pid)
    with pytest.raises(HostedValidationError,match='TITLE_CODEC_REQUIRED'):
        legacy.put_blob(png,'.png')
    assert not live.list_prefix(f'projects/{pid}/assets/')


def test_v10525_private_member_state_refuses_plaintext_bypass():
    # M12T: writes now operate through per-project, per-member sealed fields.
    docs, _objects, projects, _codec = private_store(OWNER)
    pid = projects.create('Manuscript')['id']
    projects.put_member_state(pid, {'notes': 'Unpublished bookmark'}, key='reading')
    projects.put_member_state(pid, {'bookmark': 'Private draft'})
    for path in (('projects', pid, 'members', OWNER, 'ui_state', 'reading'),
                 ('projects', pid, 'members', OWNER, 'state', 'bookmark')):
        stored = docs.get(path).data
        assert '_bm_private_fields' in stored
        assert 'Private draft' not in str(stored)
        assert 'Unpublished bookmark' not in str(stored)
    assert projects.member_state(pid, key='reading')['notes'] == 'Unpublished bookmark'
    assert projects.member_state(pid)['bookmark'] == 'Private draft'
