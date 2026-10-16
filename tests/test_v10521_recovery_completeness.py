"""M12K: encrypted recovery must never claim success after silent record loss.

These tests are authored for the owner's verifier. Agent must not run them.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.encrypted_recovery import HostedEncryptedRecoveryWriter
from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore
from hosted.firestore.contracts import HostedValidationError
from hosted.packages import HostedProjectPackageService
from hosted.recovery_coverage import RecoveryCoverageError, _scan_registered_project_documents
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
from hosted.storage.memory import MemoryObjectStore

OWNER = 'usr_' + 'a' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'


class FakeKms:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)

    def wrap(self, key):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, key, b'coverage-test')

    def unwrap(self, wrapped):
        return AESGCM(self.key).decrypt(wrapped[:12], wrapped[12:], b'coverage-test')


def private_project():
    docs, live, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    ensure_hosted_user(docs, user_id=OWNER, display_name='Writer')
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    codec = HostedEnvelopeCodec(FakeKms())
    fields = HostedPrivateFirestoreFields(codec)
    objects = HostedEncryptedObjectStore(live, codec, authorize=lambda pid, uid: authority.get_for_user(pid, uid))
    projects = HostedCloudProjectRepository(docs, live, authority, users, lambda: OWNER,
        encrypted_fields=fields, encrypted_objects=objects)
    pid = projects.create('A private novel')['id']
    return docs, recovery, projects, pid, HostedEncryptedRecoveryWriter(projects, recovery, codec)


def insert_direct(docs, path, data):
    docs.run_transaction(lambda tx: tx.create(path, data))


def test_v10521_supported_ciphertext_models_still_produce_restorable_archive():
    docs, recovery, projects, pid, writer = private_project()
    projects.domain.create('task', {'project_id': pid}, {'title': 'Private milestone'},
                           user_id=OWNER, entity_id='task_safe')
    projects.domain.create('chapter', {'project_id': pid}, {'title': 'Secret chapter title'},
                           user_id=OWNER, entity_id='chapter_safe')
    # New projects seed b_1, not b_safe. Create the additional block via the
    # real encrypted domain path before updating it, so the recovery inventory
    # exercises two distinct authenticated manuscript block pointers.
    projects.domain.create('manuscript_block', {'project_id': pid},
                           {'tab_id': 'tab_manuscript', 'chapter_id': 'chapter_1',
                            'position': 1, 'text_content': '', 'content_html': ''},
                           user_id=OWNER, entity_id='b_safe', operation='secure_block_create')
    projects.domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_safe'},
                           {'text_content': 'Unpublished plot', 'content_html': '<p>Unpublished plot</p>'}, user_id=OWNER,
                           operation='secure_block_update')
    result = writer.create(pid, OWNER, operation='coverage_pass')
    assert result.chunk_count > 0
    assert recovery.stat(result.manifest_name) is not None
    catalog = _scan_registered_project_documents(docs, pid)
    assert ('projects', pid, 'tasks', 'task_safe') in catalog['task']
    assert ('projects', pid, 'blocks', 'b_safe') in catalog['manuscript_block']
    # Manifest publication alone is not restoration. Read the sealed archive
    # back through authenticated object decryption and require its source data.
    import io
    import json
    import zipfile
    recovered = writer._reader.read_offline(pid, OWNER, operation='coverage_pass')
    with zipfile.ZipFile(io.BytesIO(recovered)) as archive:
        entities = json.loads(archive.read('project.json'))['entities']
    assert any(row['data']['text_content'] == 'Unpublished plot'
               for row in entities['manuscript_block'] if row['ids']['block_id'] == 'b_safe')
    assert any(row['data']['title'] == 'Secret chapter title'
               for row in entities['chapter'] if row['ids']['chapter_id'] == 'chapter_safe')


@pytest.mark.parametrize('collection,idfield', [
    ('cuts', 'cut_id'),
    ('revisions', 'revision_id'),
    ('assets', 'asset_id'),
    ('backup_destinations', 'destination_id'),
])
def test_v10521_unsupported_project_content_blocks_committed_archive(collection, idfield):
    docs, recovery, projects, pid, writer = private_project()
    # Cuts, revisions and assets became supported private models in M12N,
    # but an unsealed, hand-inserted row must still fail closed. Backup
    # destinations are intentionally unsupported even in private mode.
    insert_direct(docs, ('projects', pid, collection, 'hidden'), {idfield: 'hidden', 'generation': 1})
    expected = ('HOSTED_RECOVERY_UNSUPPORTED_SOURCE_MODEL' if collection == 'backup_destinations'
                else 'HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH')
    with pytest.raises((RecoveryCoverageError, HostedValidationError), match=expected):
        writer.create(pid, OWNER, operation='no_partial_snapshot')
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10521_unsupported_nested_review_participant_is_not_silently_omitted():
    docs, recovery, projects, pid, writer = private_project()
    projects.domain.create('review', {'project_id': pid}, {'name': 'Alpha'},
                           user_id=OWNER, entity_id='review_alpha')
    insert_direct(docs, ('projects', pid, 'reviews', 'review_alpha', 'participants', 'participant_hidden'),
                  {'participant_id': 'participant_hidden', 'detail': 'private reviewer note'})
    # This participant family is now privately supported, but direct
    # plaintext injection bypassing its codec must still be refused.
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_PLAINTEXT_FORBIDDEN'):
        writer.create(pid, OWNER, operation='nested_gap')
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10521_exporter_missing_a_record_is_detected_even_if_archive_is_valid_zip():
    docs, recovery, projects, pid, writer = private_project()
    for suffix in ('a', 'b'):
        projects.domain.create('task', {'project_id': pid}, {'title': 'Task ' + suffix},
                               user_id=OWNER, entity_id='task_' + suffix)
    original = projects.domain.list

    def incomplete(logical, ids, **kw):
        rows = original(logical, ids, **kw)
        return rows[:1] if logical == 'task' else rows

    projects.domain.list = incomplete
    with pytest.raises(RecoveryCoverageError, match='HOSTED_RECOVERY_CATALOG_MISMATCH'):
        writer.create(pid, OWNER, operation='hidden_task')
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10521_registered_catalog_never_scans_other_projects_or_groups():
    docs, _recovery, _projects, pid, _writer = private_project()
    other = 'f' * 32
    insert_direct(docs, ('projects', other, 'annotations', 'invisible'),
                  {'annotation_id': 'invisible', 'text': 'Other tenant secret'})
    real_query = docs.query_collection

    def checked_query(path, **kw):
        assert tuple(path)[:2] == ('projects', pid)
        return real_query(path, **kw)

    docs.query_collection = checked_query
    inventory = _scan_registered_project_documents(docs, pid)
    assert not inventory['annotation']
    assert inventory['project_membership']


def test_v10521_unencrypted_task_row_still_fails_closed():
    docs, recovery, _projects, pid, writer = private_project()
    insert_direct(docs, ('projects', pid, 'tasks', 'legacy_task'),
                  {'task_id': 'legacy_task', 'title': 'Unencrypted secret'})
    with pytest.raises(Exception):
        writer.create(pid, OWNER, operation='legacy_forbidden')
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10521_local_runtime_never_imports_catalog_or_cloud_security():
    local = (ROOT / 'src/runtime/local.py').read_text(encoding='utf-8')
    assert 'hosted.recovery_coverage' not in local
    assert 'hosted.encrypted_recovery' not in local
    assert 'google.cloud' not in local


@pytest.mark.parametrize('model,ids', [
    ('chapter', {'chapter_id': 'chapter_a'}),
    ('annotation', {'annotation_id': 'annotation_a'}),
    ('review_anchor', {'review_id': 'review_a', 'snapshot_id': 'snapshot_a', 'anchor_id': 'anchor_a'}),
    ('review_event', {'review_id': 'review_a', 'event_id': 'event_a'}),
    ('review_import_receipt', {'review_id': 'review_a', 'import_id': 'import_a'}),
    ('review_marker_assignment', {'review_id': 'review_a', 'review_marker_id': 'marker_a'}),
    ('review_question_answer', {'review_id': 'review_a', 'answer_id': 'answer_a'}),
    ('review_questionnaire_assignment', {'review_id': 'review_a', 'questionnaire_id': 'questionnaire_a'}),
    ('review_scope_chapter', {'review_id': 'review_a', 'chapter_id': 'chapter_a'}),
    ('review_scope_scene', {'review_id': 'review_a', 'scene_id': 'scene_a'}),
])
def test_v10521_new_private_family_seals_content_and_authenticates_full_path(model, ids):
    codec = HostedPrivateFirestoreFields(HostedEnvelopeCodec(FakeKms()))
    original_ids = {'project_id': 'a' * 32, **ids}
    sealed = codec.seal(model, original_ids,
                        {'name': 'Never expose this unpublished secret', 'custom_nested': {'notes': 'Scene spoiler'}})
    assert 'Never expose' not in str(sealed)
    assert 'Scene spoiler' not in str(sealed)
    opened = codec.open(model, original_ids, sealed)
    assert opened['name'] == 'Never expose this unpublished secret'
    assert opened['custom_nested'] == {'notes': 'Scene spoiler'}
    changed_ids = dict(original_ids)
    changed_field = next(k for k in ids if k != 'review_id')
    changed_ids[changed_field] = 'different_path'
    with pytest.raises(Exception):
        codec.open(model, changed_ids, sealed)


def test_v10521_new_private_family_cannot_store_conflicting_parent():
    codec = HostedPrivateFirestoreFields(HostedEnvelopeCodec(FakeKms()))
    ids = {'project_id': 'a' * 32, 'review_id': 'review_a', 'event_id': 'event_a'}
    from hosted.firestore.contracts import HostedValidationError
    with pytest.raises(HostedValidationError, match='IDENTITY_MISMATCH'):
        codec.seal('review_event', ids,
                   {'review_id': 'wrong_review', 'description': 'spoiler'})


def test_v10521_model_inventory_never_silently_loses_new_registered_family():
    import json
    from hosted.firestore.model_registry import MODELS
    coverage = json.loads((ROOT / 'docs/HOSTED_PRIVACY_COVERAGE_M12B.json').read_text(encoding='utf-8'))
    assert set(coverage['firestore_models']) == set(MODELS)
    for model_name, model in MODELS.items():
        assert coverage['firestore_models'][model_name]['path_template'] == model.path_template
        if HostedPrivateFirestoreFields.protects(model_name):
            assert coverage['firestore_models'][model_name]['encryption_coverage'] == 'NOT_INTEGRATED'
