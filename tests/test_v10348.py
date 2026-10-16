from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore import (
    DocumentAlreadyExists,
    DocumentConflict,
    HostedProjectRepository,
    HostedStructuredRepository,
    HostedUserRepository,
    HostedValidationError,
    MemoryDocumentStore,
    ProjectUnavailable,
)
from hosted.storage import (
    HostedAssetStore,
    HostedHybridObjectPublisher,
    ImmutableObjectPublisher,
    MemoryObjectStore,
    ObjectAlreadyExists,
    ObjectGenerationConflict,
    ObjectIntegrityError,
    delete_confirmed_orphan,
    discover_orphans,
)
from hosted.storage.keys import project_asset_key, revision_key

OWNER = "usr_" + "a" * 32
PROJECT = "1" * 32
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==")


class Clock:
    value = 0
    def now_iso(self):
        self.value += 1
        return f"2026-10-05T01:00:{self.value:02d}Z"


def hosted_state():
    documents = MemoryDocumentStore()
    state = HostedStructuredRepository(documents, clock=Clock())
    users = HostedUserRepository(state)
    projects = HostedProjectRepository(documents, clock=state.clock)
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    return documents, state.clock


def test_v10348_release_dependency_and_milestone4_boundary_are_synchronized():
    assert tuple(map(int, (ROOT / "VERSION").read_text(encoding="utf-8").strip().split("."))) >= (10, 3, 48)
    local = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    hosted = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8").lower()
    assert "google-cloud-storage" not in local
    assert "google-cloud-storage==3.16.0" in hosted
    hosted_runtime = (ROOT / "src/runtime/hosted.py").read_text(encoding="utf-8")
    runtime_services = (ROOT / "src/hosted/runtime_services.py").read_text(encoding="utf-8")
    # Milestone 4 froze the raw adapter; Milestone 5 intentionally wires the committed
    # Firestore+GCS wrapper, which delegates byte validation/publication to that adapter.
    assert "HostedCommittedAssetStore" in hosted_runtime
    assert "GoogleCloudObjectStore" in hosted_runtime
    assert "from hosted.storage.assets import HostedAssetStore" in runtime_services
    assert "self.raw=HostedAssetStore" in runtime_services
    assert "LocalAssetStore" not in hosted_runtime


def test_v10348_memory_object_store_matches_generation_preconditions():
    store = MemoryObjectStore()
    first = store.write_chunks("objects/a", [b"one"], max_bytes=16, content_type="text/plain", if_generation_match=0)
    with pytest.raises(ObjectAlreadyExists):
        store.write_chunks("objects/a", [b"one"], max_bytes=16, content_type="text/plain", if_generation_match=0)
    second = store.write_chunks("objects/a", [b"two"], max_bytes=16, content_type="text/plain", if_generation_match=first.generation)
    assert second.generation > first.generation
    with pytest.raises(ObjectGenerationConflict):
        store.write_chunks("objects/a", [b"three"], max_bytes=16, content_type="text/plain", if_generation_match=first.generation)
    with pytest.raises(ObjectGenerationConflict):
        store.delete("objects/a", if_generation_match=first.generation)
    assert b"".join(store.read_chunks("objects/a", max_bytes=16, if_generation_match=second.generation)) == b"two"


def test_v10348_immutable_retry_requires_exact_remote_hash_and_size():
    store = MemoryObjectStore()
    publisher = ImmutableObjectPublisher(store)
    first = publisher.publish_bytes("objects/content", b"alpha", max_bytes=32)
    retry = publisher.publish_bytes("objects/content", b"alpha", max_bytes=32)
    assert first.created is True and retry.created is False
    replaced = store.write_chunks(
        "objects/content", [b"bravo"], max_bytes=32, content_type="application/octet-stream",
        if_generation_match=first.object.generation,
    )
    assert replaced.generation != first.object.generation
    with pytest.raises(ObjectIntegrityError):
        publisher.publish_bytes("objects/content", b"alpha", max_bytes=32)


def test_v10348_revision_pointer_is_firestore_last_and_retry_is_idempotent():
    documents, clock = hosted_state()
    objects = MemoryObjectStore()
    publisher = HostedHybridObjectPublisher(objects, documents, clock=clock)
    row = publisher.publish_revision(
        project_id=PROJECT,
        user_id=OWNER,
        revision_id="rev_one",
        expected_project_generation=1,
        envelope=b"immutable-revision-envelope",
        revision_metadata={"state_hash": "s" * 64, "content_hash": "c" * 64},
        operation_id="publish_rev_one",
        max_bytes=1024,
    )
    assert row["project_generation"] == 2
    obj = objects.stat(str(row["object_name"]))
    assert obj is not None
    assert obj.generation == row["object_generation"]
    assert obj.sha256 == row["object_sha256"]
    project = documents.get(("projects", PROJECT))
    assert project is not None
    assert project.data["generation"] == 2
    assert project.data["current_revision_id"] == "rev_one"
    assert project.data["current_revision"]["object_name"] == row["object_name"]
    replay = publisher.publish_revision(
        project_id=PROJECT,
        user_id=OWNER,
        revision_id="rev_one",
        expected_project_generation=1,
        envelope=b"immutable-revision-envelope",
        revision_metadata={"state_hash": "s" * 64, "content_hash": "c" * 64},
        operation_id="publish_rev_one",
        max_bytes=1024,
    )
    assert replay["object_generation"] == row["object_generation"]
    assert documents.get(("projects", PROJECT)).data["generation"] == 2


def test_v10348_blob_success_plus_pointer_cas_failure_leaves_discoverable_orphan_only():
    documents, clock = hosted_state()
    objects = MemoryObjectStore()
    publisher = HostedHybridObjectPublisher(objects, documents, clock=clock)
    with pytest.raises(DocumentConflict):
        publisher.publish_revision(
            project_id=PROJECT,
            user_id=OWNER,
            revision_id="rev_stale",
            expected_project_generation=2,
            envelope=b"orphan-after-cas-failure",
            revision_metadata={"state_hash": "s" * 64, "content_hash": "c" * 64},
            operation_id="publish_stale",
            max_bytes=1024,
        )
    expected_name = revision_key(PROJECT, 3, "rev_stale")
    orphan = objects.stat(expected_name)
    assert orphan is not None
    assert documents.get(("projects", PROJECT, "revisions", "rev_stale")) is None
    found = discover_orphans(objects, prefix=f"projects/{PROJECT}/revisions/", referenced_object_names=[])
    assert [row.name for row in found] == [expected_name]
    with pytest.raises(ObjectGenerationConflict):
        objects.delete(expected_name, if_generation_match=orphan.generation + 1)
    delete_confirmed_orphan(objects, orphan)
    assert objects.stat(expected_name) is None



def test_v10348_hybrid_project_asset_publication_records_verified_object_identity_last():
    documents, clock = hosted_state()
    objects = MemoryObjectStore()
    publisher = HostedHybridObjectPublisher(objects, documents, clock=clock)
    digest = hashlib.sha256(PNG).hexdigest()
    object_name = project_asset_key(PROJECT, digest, ".png")
    row = publisher.publish_new_entity(
        "project_asset",
        {"project_id": PROJECT, "asset_id": "asset_one"},
        {"api_path": f"/api/assets/img_{digest[:24]}.png", "content_hash": digest},
        user_id=OWNER,
        operation_id="publish_asset_one",
        object_name=object_name,
        object_payload=PNG,
        max_bytes=1024 * 1024,
        content_type="image/png",
    )
    stored = objects.stat(object_name)
    assert stored is not None
    assert row["object_name"] == stored.name
    assert row["object_generation"] == stored.generation
    assert row["object_sha256"] == stored.sha256
    assert documents.get(("projects", PROJECT, "assets", "asset_one")) is not None

    wrong_name = project_asset_key(PROJECT, "0" * 64, ".png")
    with pytest.raises(ObjectIntegrityError):
        publisher.publish_new_entity(
            "project_asset",
            {"project_id": PROJECT, "asset_id": "asset_wrong_hash"},
            {"api_path": "/api/assets/img_wrong.png"},
            user_id=OWNER,
            operation_id="publish_asset_wrong_hash",
            object_name=wrong_name,
            object_payload=PNG,
            max_bytes=1024 * 1024,
            content_type="image/png",
        )
    assert objects.stat(wrong_name) is None


def test_v10348_revision_authorization_and_metadata_are_rejected_before_new_blob_creation():
    documents, clock = hosted_state()
    objects = MemoryObjectStore()
    publisher = HostedHybridObjectPublisher(objects, documents, clock=clock)
    intruder = "usr_" + "b" * 32
    with pytest.raises(ProjectUnavailable):
        publisher.publish_revision(
            project_id=PROJECT,
            user_id=intruder,
            revision_id="rev_intruder",
            expected_project_generation=1,
            envelope=b"must-not-be-created",
            revision_metadata={"state_hash": "s" * 64},
            operation_id="intruder_publish",
            max_bytes=1024,
        )
    assert objects.stat(revision_key(PROJECT, 2, "rev_intruder")) is None

    with pytest.raises(HostedValidationError):
        publisher.publish_revision(
            project_id=PROJECT,
            user_id=OWNER,
            revision_id="rev_secret",
            expected_project_generation=1,
            envelope=b"must-not-be-created-either",
            revision_metadata={"refresh_token": "forbidden"},
            operation_id="secret_publish",
            max_bytes=1024,
        )
    assert objects.stat(revision_key(PROJECT, 2, "rev_secret")) is None


def test_v10348_revision_replay_requires_same_operation_and_metadata_not_only_same_blob():
    documents, clock = hosted_state()
    objects = MemoryObjectStore()
    publisher = HostedHybridObjectPublisher(objects, documents, clock=clock)
    common = dict(
        project_id=PROJECT,
        user_id=OWNER,
        revision_id="rev_identity",
        expected_project_generation=1,
        envelope=b"same-envelope",
        operation_id="publish_identity",
        max_bytes=1024,
    )
    publisher.publish_revision(
        **common,
        revision_metadata={"state_hash": "a" * 64, "content_hash": "c" * 64},
    )
    with pytest.raises(DocumentAlreadyExists):
        publisher.publish_revision(
            **common,
            revision_metadata={"state_hash": "b" * 64, "content_hash": "c" * 64},
        )
    assert documents.get(("projects", PROJECT)).data["generation"] == 2


def test_v10348_server_generated_keys_reject_path_traversal_and_user_path_material():
    digest = "a" * 64
    assert project_asset_key(PROJECT, digest, ".png") == f"projects/{PROJECT}/assets/sha256/{digest}.png"
    assert revision_key(PROJECT, 7, "rev_safe").endswith("/00000000000000000007/rev_safe.revision.json.gz")
    for bad in ("../escape", "a/b", "a\\b"):
        with pytest.raises(Exception):
            revision_key(PROJECT, 7, bad)


def test_v10348_hosted_asset_port_preserves_decoder_free_image_boundary():
    store = MemoryObjectStore()
    assets = HostedAssetStore(store, project_id=PROJECT, image_byte_limit=1024 * 1024)
    saved = assets.put_blob(PNG, ".png")
    assert saved.api_path.startswith("/api/assets/img_")
    assert saved.object_name and saved.object_generation
    assert assets.read_blob(saved.api_path) == PNG
    assert assets.put_blob(PNG, ".png").created is False
    with pytest.raises(ValueError):
        assets.put_blob(b"not-an-image", ".png")
    # Hosted cleanup is retention-aware/orphan-driven, never request-local deletion.
    assets.discard_uncommitted(saved)
    assert assets.exists(saved.api_path)

    # The short logical API digest is lookup-only. Reads re-hash the exact object
    # generation and require the full content-addressed GCS key to match.
    poisoned = MemoryObjectStore()
    real_digest = hashlib.sha256(PNG).hexdigest()
    fake_digest = real_digest[:24] + ("0" * 40 if real_digest[24:] != "0" * 40 else "1" * 40)
    poisoned.write_chunks(
        project_asset_key(PROJECT, fake_digest, ".png"),
        [PNG],
        max_bytes=1024 * 1024,
        content_type="image/png",
        if_generation_match=0,
    )
    poisoned_assets = HostedAssetStore(poisoned, project_id=PROJECT, image_byte_limit=1024 * 1024)
    with pytest.raises(ValueError, match="ASSET_CONTENT_IDENTITY_MISMATCH"):
        poisoned_assets.read_blob(f"/api/assets/img_{real_digest[:24]}.png")


def test_v10348_google_sdk_and_streaming_are_isolated_to_hosted_storage_boundary():
    google = (ROOT / "src/hosted/storage/google_driver.py").read_text(encoding="utf-8")
    package = "\n".join(path.read_text(encoding="utf-8").lower() for path in (ROOT / "src/hosted/storage").glob("*.py"))
    local = (ROOT / "src/runtime/local.py").read_text(encoding="utf-8")
    assert "from google.cloud import storage" in google
    assert 'blob.open(' in google and '"wb"' in google and '"rb"' in google
    assert "if_generation_match=expected" in google
    assert "require_hybrid_entity_object_key" in (ROOT / "src/hosted/storage/publication.py").read_text(encoding="utf-8")
    assert "HOSTED_OBJECT_KEY_HASH_MISMATCH" in (ROOT / "src/hosted/storage/publication.py").read_text(encoding="utf-8")
    assert "SpooledTemporaryFile(max_size=8 * 1024 * 1024)" in google
    assert "blob.metadata =" in google and "blob.patch(" not in google
    assert "sqlite" not in package
    assert "hosted.storage" not in local


def test_v10348_static_object_adapter_validator_requires_no_credentials():
    spec = importlib.util.spec_from_file_location(
        "object_adapter_validator", ROOT / "scripts/validate_hosted_object_storage_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.validate() == []
