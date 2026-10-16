"""M12G optional hosted structured-content encryption regression contracts.

Human-owned ./verify-release only. No production encryption activation.
"""
from __future__ import annotations

import copy
import json
import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.firestore import (HostedProjectRepository, HostedStructuredRepository,
                              HostedUserRepository, MemoryDocumentStore, ProjectUnavailable)
from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.model_registry import model_for
from hosted.firestore.repositories import render_model_path
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields, PRIVATE_FIELD_NAME
from hosted.storage.memory import MemoryObjectStore
from hosted.packages import HostedProjectPackageService

OWNER = 'usr_' + 'a' * 32
STRANGER = 'usr_' + 'b' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
PRIVATE = 'UNPUBLISHED: unreleased chapter twist and private reader notes'
# For nested paths, parent IDs are opaque canonical identifiers.
FAMILIES = [
    ('document_tab', 'tab_id', {}),
    ('scene', 'scene_id', {}),
    ('comment_thread', 'thread_id', {}),
    ('comment_message', 'message_id', {'thread_id': 'thread_parent'}),
    ('questionnaire', 'questionnaire_id', {}),
    ('questionnaire_question', 'question_id', {'questionnaire_id': 'questionnaire_parent'}),
    ('cross_feature_reference', 'reference_id', {}),
    ('markup_label', 'label_id', {}),
    ('icon_marker', 'icon_marker_id', {}),
    ('review_marker_label', 'marker_label_id', {}),
    ('canvas_version', 'version_id', {'canvas_id': 'canvas_parent'}),
    ('canvas_version_item', 'item_id', {'canvas_id': 'canvas_parent', 'version_id': 'version_parent'}),
]


class OfflineKeyWrapper:
    key_resource = RESOURCE

    def __init__(self):
        self._key = AESGCM.generate_key(bit_length=256)
        self.available = True
        self.unwrap_count = 0

    def wrap(self, plaintext):
        if not self.available:
            raise RuntimeError('KMS_UNAVAILABLE')
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._key).encrypt(nonce, plaintext, b'm12g-wrap')

    def unwrap(self, ciphertext):
        self.unwrap_count += 1
        if not self.available:
            raise RuntimeError('KMS_UNAVAILABLE')
        return AESGCM(self._key).decrypt(ciphertext[:12], ciphertext[12:], b'm12g-wrap')


def offline_project():
    docs = MemoryDocumentStore()
    objects = MemoryObjectStore()
    ensure_hosted_user(docs, user_id=OWNER, display_name='Owner')
    ensure_hosted_user(docs, user_id=STRANGER, display_name='Stranger')
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    wrapper = OfflineKeyWrapper()
    fields = HostedPrivateFirestoreFields(HostedEnvelopeCodec(wrapper))
    projects = HostedCloudProjectRepository(docs, objects, authority, users,
                                             lambda: OWNER, encrypted_fields=fields)
    project_id = projects.create('Writer Workspace')['id']
    return docs, wrapper, projects, project_id


@pytest.mark.parametrize('entity,id_field,parents', FAMILIES)
def test_v10516_new_families_store_ciphertext_only_and_roundtrip(entity, id_field, parents):
    docs, _wrapper, projects, pid = offline_project()
    # Parent existence is not an authorization shortcut; the full document
    # path and canonical project membership are still checked separately.
    ids = {'project_id': pid, **parents, id_field: 'sample_record'}
    written = projects.domain.create(entity, {'project_id': pid, **parents},
        {'title': PRIVATE, 'custom_payload': {'secret': PRIVATE}},
        user_id=OWNER, entity_id='sample_record')
    assert written['title'] == PRIVATE
    path = render_model_path(model_for(entity), **ids)
    stored = docs.get(path).data
    assert PRIVATE not in json.dumps(stored)
    assert 'title' not in stored and 'custom_payload' not in stored
    assert PRIVATE_FIELD_NAME in stored
    again = projects.domain.get(entity, ids, user_id=OWNER)
    assert again['custom_payload']['secret'] == PRIVATE
    assert projects.domain.list(entity, {'project_id': pid, **parents},
                                user_id=OWNER, field='title', value=PRIVATE)
    with pytest.raises(ProjectUnavailable):
        projects.domain.get(entity, ids, user_id=STRANGER)


def test_v10516_nested_messages_are_cryptographically_bound_to_parent():
    docs, _wrapper, projects, pid = offline_project()
    for parent, content in [('thread_alpha', PRIVATE), ('thread_beta', 'not private')]:
        projects.domain.create('comment_thread', {'project_id': pid},
                               {'label': parent}, user_id=OWNER, entity_id=parent)
        projects.domain.create('comment_message', {'project_id': pid, 'thread_id': parent},
                               {'text': content}, user_id=OWNER, entity_id='same_message')
    source = ('projects', pid, 'comment_threads', 'thread_alpha', 'messages', 'same_message')
    destination = ('projects', pid, 'comment_threads', 'thread_beta', 'messages', 'same_message')
    def transplant(tx):
        row = dict(tx.get(destination).data)
        row[PRIVATE_FIELD_NAME] = copy.deepcopy(tx.get(source).data[PRIVATE_FIELD_NAME])
        tx.set(destination, row)
    docs.run_transaction(transplant)
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED'):
        projects.domain.get('comment_message', {'project_id': pid,
            'thread_id': 'thread_beta', 'message_id': 'same_message'}, user_id=OWNER)


def test_v10516_question_parent_mismatch_refused_before_firestore_write():
    docs, _wrapper, projects, pid = offline_project()
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH'):
        projects.domain.create('questionnaire_question',
            {'project_id': pid, 'questionnaire_id': 'questionnaire_one'},
            {'questionnaire_id': 'questionnaire_other', 'text': PRIVATE},
            user_id=OWNER, entity_id='q_one')
    assert docs.get(('projects', pid, 'questionnaires', 'questionnaire_one', 'questions', 'q_one')) is None


def test_v10516_new_families_reject_plaintext_overlay_and_kms_outage():
    docs, wrapper, projects, pid = offline_project()
    projects.domain.create('document_tab', {'project_id': pid},
                           {'name': PRIVATE}, user_id=OWNER, entity_id='tab_one')
    ids = {'project_id': pid, 'tab_id': 'tab_one'}
    wrapper.available = False
    before = wrapper.unwrap_count
    with pytest.raises(ProjectUnavailable):
        projects.domain.get('document_tab', ids, user_id=STRANGER)
    assert wrapper.unwrap_count == before
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED'):
        projects.domain.get('document_tab', ids, user_id=OWNER)
    wrapper.available = True
    def inject(tx):
        path = ('projects', pid, 'tabs', 'tab_one')
        row = dict(tx.get(path).data)
        row['description'] = PRIVATE
        tx.set(path, row)
    docs.run_transaction(inject)
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_PLAINTEXT_FORBIDDEN'):
        projects.domain.get('document_tab', ids, user_id=OWNER)


def test_v10516_batch_and_export_still_refuse_incomplete_encrypted_mode():
    _docs, _wrapper, projects, pid = offline_project()
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_BATCH_NOT_READY'):
        projects.domain.create_batch_atomic('document_tab', {'project_id': pid},
            [{'name': PRIVATE}], user_id=OWNER)
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        HostedProjectPackageService(projects).export_bytes(pid, OWNER)


def test_v10516_specialized_system_and_hybrid_paths_not_auto_enabled():
    # M12K added chapter metadata to the explicitly injected encrypted fields.
    # This does NOT turn on encrypted storage in the normal hosted runtime.
    assert HostedPrivateFirestoreFields.protects('chapter')
    for name in ('checkpoint', 'cut', 'review_snapshot', 'review_participant',
                 'project_asset', 'hosted_revision_history'):
        assert HostedPrivateFirestoreFields.protects(name)
    for name in ('project', 'project_membership',
                 'manuscript_block', 'identity_binding', 'hosted_user_profile'):
        assert not HostedPrivateFirestoreFields.protects(name)
    assert 'google-cloud-kms' not in (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'encrypted_fields=' not in (ROOT / 'src/runtime/hosted.py').read_text(encoding='utf-8')
    assert 'cloud_kms' not in (ROOT / 'src/runtime/local.py').read_text(encoding='utf-8')
