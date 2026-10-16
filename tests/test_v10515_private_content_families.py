"""M12F regression contracts for opt-in encrypted Reviews, Canvas and Maps.

Human-owned verifier only. No agent execution of these tests.
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
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from hosted.firestore import (HostedProjectRepository, HostedStructuredRepository,
                              HostedUserRepository, MemoryDocumentStore, ProjectUnavailable)
from hosted.firestore.contracts import HostedValidationError
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields, PRIVATE_FIELD_NAME
from hosted.storage.memory import MemoryObjectStore
from hosted.packages import HostedProjectPackageService

OWNER = "usr_" + "a" * 32
STRANGER = "usr_" + "b" * 32
RESOURCE = "projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data"
SECRET = "Unpublished novel: final plot twist"


class OfflineKeyWrapper:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)
        self.available = True
        self.unwrap_count = 0

    def wrap(self, key):
        if not self.available:
            raise RuntimeError("KMS_UNAVAILABLE")
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, key, b"m12f-wrap")

    def unwrap(self, wrapped):
        self.unwrap_count += 1
        if not self.available:
            raise RuntimeError("KMS_UNAVAILABLE")
        return AESGCM(self.key).decrypt(wrapped[:12], wrapped[12:], b"m12f-wrap")


def setup():
    docs, objects = MemoryDocumentStore(), MemoryObjectStore()
    ensure_hosted_user(docs, user_id=OWNER, display_name="Owner")
    ensure_hosted_user(docs, user_id=STRANGER, display_name="Stranger")
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    wrapper = OfflineKeyWrapper()
    fields = HostedPrivateFirestoreFields(HostedEnvelopeCodec(wrapper))
    projects = HostedCloudProjectRepository(docs, objects, authority, users,
                                             lambda: OWNER, encrypted_fields=fields)
    pid = projects.create("Private Story")['id']
    return docs, wrapper, fields, projects, pid


@pytest.mark.parametrize("entity,parents,record,field", [
    ("review", {}, "review_a", "name"),
    ("canvas", {}, "canvas_a", "name"),
    ("story_map", {}, "map_a", "title"),
])
def test_v10515_top_level_private_entities_use_ciphertext_only(entity, parents, record, field):
    docs, _wrapper, _fields, projects, pid = setup()
    added = projects.domain.create(entity, {"project_id": pid, **parents},
        {field: SECRET, "details": {"private": SECRET}, "order": 1},
        user_id=OWNER, entity_id=record)
    assert added[field] == SECRET
    path_prefix = {"review": "reviews", "canvas": "canvases", "story_map": "story_maps"}[entity]
    raw = docs.get(("projects", pid, path_prefix, record)).data
    assert SECRET not in json.dumps(raw)
    assert field not in raw and "details" not in raw
    assert set(raw) <= {"id", {"review": "review_id", "canvas": "canvas_id", "story_map": "map_id"}[entity],
                        "generation", "created_at", "updated_at", "_bm_create_operation",
                        "_bm_create_fingerprint", PRIVATE_FIELD_NAME}
    again = projects.domain.get(entity, {"project_id": pid, {"review": "review_id", "canvas": "canvas_id", "story_map": "map_id"}[entity]: record}, user_id=OWNER)
    assert again['details']['private'] == SECRET
    assert projects.domain.list(entity, {"project_id": pid}, user_id=OWNER, field=field, value=SECRET)


def test_v10515_nested_review_comment_encryption_binds_review_path():
    docs, _wrapper, _fields, projects, pid = setup()
    left = projects.domain.create("review", {"project_id": pid}, {"name": "Left"}, user_id=OWNER, entity_id="review_left")
    right = projects.domain.create("review", {"project_id": pid}, {"name": "Right"}, user_id=OWNER, entity_id="review_right")
    for review in (left, right):
        projects.domain.create("review_comment", {"project_id": pid, "review_id": review['id']},
            {"message": SECRET if review is left else "Other"}, user_id=OWNER, entity_id="comment_shared")
    source = ("projects", pid, "reviews", "review_left", "comments", "comment_shared")
    target = ("projects", pid, "reviews", "review_right", "comments", "comment_shared")
    assert SECRET not in json.dumps(docs.get(source).data)
    def tamper(tx):
        row = dict(tx.get(target).data)
        row[PRIVATE_FIELD_NAME] = copy.deepcopy(tx.get(source).data[PRIVATE_FIELD_NAME])
        tx.set(target, row)
    docs.run_transaction(tamper)
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED"):
        projects.domain.get("review_comment", {"project_id": pid, "review_id": "review_right", "comment_id": "comment_shared"}, user_id=OWNER)


def test_v10515_nested_canvas_and_map_children_use_exact_ancestor_ids():
    docs, _wrapper, _fields, projects, pid = setup()
    for name, child, root_id, parent_name, collection, child_id_field, child_collection in (
        ("canvas", "canvas_node", "canvas_left", "canvas_id", "canvases", "node_id", "nodes"),
        ("story_map", "map_line", "map_left", "map_id", "story_maps", "line_id", "lines"),
    ):
        first = projects.domain.create(name, {"project_id": pid}, {"title": "First"}, user_id=OWNER, entity_id=root_id)
        other = projects.domain.create(name, {"project_id": pid}, {"title": "Other"}, user_id=OWNER, entity_id=root_id + "_other")
        for parent in (first, other):
            projects.domain.create(child, {"project_id": pid, parent_name: parent['id']},
                {"label": SECRET if parent is first else "Other"}, user_id=OWNER, entity_id="shared_child")
        src = ("projects", pid, collection, first['id'], child_collection, "shared_child")
        dst = ("projects", pid, collection, other['id'], child_collection, "shared_child")
        assert SECRET not in json.dumps(docs.get(src).data)
        def swap(tx):
            row = dict(tx.get(dst).data)
            row[PRIVATE_FIELD_NAME] = copy.deepcopy(tx.get(src).data[PRIVATE_FIELD_NAME])
            tx.set(dst, row)
        docs.run_transaction(swap)
        with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED"):
            projects.domain.get(child, {"project_id": pid, parent_name: other['id'], child_id_field: "shared_child"}, user_id=OWNER)


def test_v10515_authorization_precedes_decryption_and_kms_errors_fail_closed():
    _docs, wrapper, _fields, projects, pid = setup()
    review = projects.domain.create("review", {"project_id": pid}, {"name": SECRET}, user_id=OWNER)
    wrapper.available = False
    before = wrapper.unwrap_count
    with pytest.raises(ProjectUnavailable):
        projects.domain.get("review", {"project_id": pid, "review_id": review['id']}, user_id=STRANGER)
    assert wrapper.unwrap_count == before
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED"):
        projects.domain.get("review", {"project_id": pid, "review_id": review['id']}, user_id=OWNER)


def test_v10515_no_plaintext_downgrade_and_nested_unknown_fields_stay_encrypted():
    docs, _wrapper, _fields, projects, pid = setup()
    canvas = projects.domain.create("canvas", {"project_id": pid}, {"name": "Canvas"}, user_id=OWNER)
    node = projects.domain.create("canvas_node", {"project_id": pid, "canvas_id": canvas['id']},
        {"unknown_user_field": {"text": SECRET}}, user_id=OWNER, entity_id="secret_node")
    path = ("projects", pid, "canvases", canvas['id'], "nodes", node['id'])
    stored = docs.get(path).data
    assert SECRET not in json.dumps(stored)
    assert "unknown_user_field" not in stored
    def leak(tx):
        row = dict(tx.get(path).data)
        row["unknown_user_field"] = SECRET
        tx.set(path, row)
    docs.run_transaction(leak)
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_PLAINTEXT_FORBIDDEN"):
        projects.domain.get("canvas_node", {"project_id": pid, "canvas_id": canvas['id'], "node_id": node['id']}, user_id=OWNER)


def test_v10515_export_and_batch_still_fail_closed_and_local_independent():
    _docs, _wrapper, _fields, projects, pid = setup()
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_BATCH_NOT_READY"):
        projects.domain.create_batch_atomic("canvas_node", {"project_id": pid, "canvas_id": "canvas_x"},
            [{"name": SECRET}], user_id=OWNER)
    with pytest.raises(ValueError, match="HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY"):
        HostedProjectPackageService(projects).export_bytes(pid, OWNER)
    local = (ROOT / "src/runtime/local.py").read_text(encoding="utf-8")
    assert "HostedPrivateFirestoreFields" not in local and "cloud_kms" not in local
    assert "google-cloud-kms" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    hosted = (ROOT / "src/runtime/hosted.py").read_text(encoding="utf-8")
    assert "encrypted_fields=" not in hosted


def test_v10515_review_reply_context_binds_review_and_comment_ancestors():
    docs, _wrapper, _fields, projects, pid = setup()
    review = projects.domain.create("review", {"project_id": pid}, {"name": "Peer notes"},
                                   user_id=OWNER, entity_id="review_reply")
    for comment in ("comment_a", "comment_b"):
        projects.domain.create("review_comment", {"project_id": pid, "review_id": review['id']},
            {"text": "Parent"}, user_id=OWNER, entity_id=comment)
        projects.domain.create("review_comment_reply",
            {"project_id": pid, "review_id": review['id'], "comment_id": comment},
            {"text": SECRET if comment == "comment_a" else "Other"},
            user_id=OWNER, entity_id="reply_same")
    src = ("projects", pid, "reviews", review['id'], "comments", "comment_a", "replies", "reply_same")
    dst = ("projects", pid, "reviews", review['id'], "comments", "comment_b", "replies", "reply_same")
    assert SECRET not in json.dumps(docs.get(src).data)
    def tamper(tx):
        target = dict(tx.get(dst).data)
        target[PRIVATE_FIELD_NAME] = copy.deepcopy(tx.get(src).data[PRIVATE_FIELD_NAME])
        tx.set(dst, target)
    docs.run_transaction(tamper)
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED"):
        projects.domain.get("review_comment_reply", {
            "project_id": pid, "review_id": review['id'], "comment_id": "comment_b",
            "reply_id": "reply_same"}, user_id=OWNER)


def test_v10515_map_item_binds_full_three_level_identity_and_filters_locally():
    docs, _wrapper, _fields, projects, pid = setup()
    story_map = projects.domain.create("story_map", {"project_id": pid}, {"name": SECRET},
                                      user_id=OWNER, entity_id="map_nested")
    line = projects.domain.create("map_line", {"project_id": pid, "map_id": story_map['id']},
                                  {"label": SECRET}, user_id=OWNER, entity_id="line_nested")
    parents = {"project_id": pid, "map_id": story_map['id'], "line_id": line['id']}
    projects.domain.create("map_item", parents, {"title": SECRET, "position": 2},
                           user_id=OWNER, entity_id="item_secret")
    raw = docs.get(("projects", pid, "story_maps", story_map['id'], "lines", line['id'], "items", "item_secret")).data
    assert SECRET not in json.dumps(raw)
    assert projects.domain.list("map_item", parents, user_id=OWNER, field="title", value=SECRET)[0]['title'] == SECRET
    assert projects.domain.list("map_item", parents, user_id=OWNER, field="title", value="not-the-title") == []


def test_v10515_parent_identity_cannot_be_forged_inside_private_payload():
    _docs, _wrapper, _fields, projects, pid = setup()
    review = projects.domain.create("review", {"project_id": pid}, {"name": "Owner"}, user_id=OWNER)
    parents = {"project_id": pid, "review_id": review['id']}
    with pytest.raises(HostedValidationError, match="HOSTED_PRIVATE_FIRESTORE_IDENTITY_MISMATCH"):
        projects.domain.create("review_comment", parents,
            {"review_id": "review_forged", "message": SECRET}, user_id=OWNER,
            entity_id="comment_rejected")
    created = projects.domain.create("review_comment", parents,
        {"review_id": review['id'], "message": SECRET}, user_id=OWNER,
        entity_id="comment_accepted")
    assert "review_id" not in created
    assert created["message"] == SECRET
