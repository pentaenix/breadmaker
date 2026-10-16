from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore import (
    DocumentAlreadyExists,
    DocumentConflict,
    DocumentNotFound,
    HostedAuthorizationError,
    HostedIdentityBindingRepository,
    HostedProjectEntityRepository,
    HostedProjectRepository,
    HostedStructuredRepository,
    HostedUserRepository,
    MemoryDocumentStore,
    ProjectUnavailable,
)
from hosted.firestore.contracts import HostedValidationError

OWNER = "usr_" + "a" * 32
EDITOR = "usr_" + "b" * 32
OUTSIDER = "usr_" + "c" * 32
PROJECT = "1" * 32
MISSING = "2" * 32


class Clock:
    value = 0
    def now_iso(self):
        self.value += 1
        return f"2026-10-05T00:00:{self.value:02d}Z"


def repositories():
    store = MemoryDocumentStore()
    state = HostedStructuredRepository(store, clock=Clock())
    users = HostedUserRepository(state)
    projects = HostedProjectRepository(store, clock=state.clock)
    return store, state, users, projects


def test_v10347_release_and_milestone2_wording_are_synchronized():
    assert tuple(map(int, (ROOT / "VERSION").read_text().strip().split("."))) >= (10, 3, 47)
    model = (ROOT / "docs/HOSTED_DATA_MODEL_V1.json").read_text(encoding="utf-8")
    assert "user filenames" in model


def test_v10347_hosted_model_registry_is_contract_driven_not_local_engine_driven():
    source = (ROOT / "src/hosted/firestore/model_registry.py").read_text(encoding="utf-8").lower()
    assert "sqlite" not in source
    generated = (ROOT / "scripts/generate_hosted_firestore_registry.py").read_text(encoding="utf-8")
    assert "HOSTED_DATA_MODEL_V1.json" in generated
    spec = importlib.util.spec_from_file_location("registry_generator", ROOT / "scripts/generate_hosted_firestore_registry.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.render() == (ROOT / "src/hosted/firestore/model_registry.py").read_text(encoding="utf-8")


def test_v10347_local_remains_cloud_free_and_hosted_sdk_is_isolated():
    local = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    hosted = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8").lower()
    assert "google-cloud-firestore" not in local
    assert "google-cloud-firestore==2.34.0" in hosted
    assert "hosted.firestore" not in (ROOT / "src/runtime/local.py").read_text(encoding="utf-8")
    package = "\n".join(path.read_text(encoding="utf-8").lower() for path in (ROOT / "src/hosted/firestore").glob("*.py"))
    assert "sqlite" not in package
    google = (ROOT / "src/hosted/firestore/google_driver.py").read_text(encoding="utf-8")
    assert "from google.cloud import firestore" in google


def test_v10347_user_project_membership_and_enumeration_resistance():
    _, _, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    users.create(display_name="Editor", operation_id="create_editor", user_id=EDITOR)
    users.create(display_name="Outsider", operation_id="create_outsider", user_id=OUTSIDER)
    created = projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    assert created["generation"] == 1
    assert projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)["generation"] == 1
    with pytest.raises(ProjectUnavailable) as forbidden:
        projects.get_for_user(PROJECT, OUTSIDER)
    with pytest.raises(ProjectUnavailable) as missing:
        projects.get_for_user(MISSING, OUTSIDER)
    assert str(forbidden.value) == str(missing.value) == "PROJECT_NOT_FOUND"
    member = projects.add_member(PROJECT, OWNER, EDITOR, "editor", expected_project_generation=1, operation_id="add_editor")
    assert member["role"] == "editor"
    assert projects.get_for_user(PROJECT, EDITOR)["project_id"] == PROJECT
    assert [row["project_id"] for row in projects.list_for_user(EDITOR)] == [PROJECT]


def test_v10347_generation_cas_and_idempotent_retry_allow_only_one_logical_write():
    _, _, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    first = projects.update(PROJECT, OWNER, {"title": "A"}, expected_generation=1, operation_id="rename_a")
    assert first["generation"] == 2
    retry = projects.update(PROJECT, OWNER, {"title": "A"}, expected_generation=1, operation_id="rename_a")
    assert retry["generation"] == 2
    with pytest.raises(DocumentConflict):
        projects.update(PROJECT, OWNER, {"title": "B"}, expected_generation=1, operation_id="rename_b")



def test_v10347_transaction_callback_retry_is_safe_and_commits_once():
    store = MemoryDocumentStore(retry_once=True)
    state = HostedStructuredRepository(store, clock=Clock())
    users = HostedUserRepository(state)
    projects = HostedProjectRepository(store, clock=state.clock)
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    created = projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    assert created["generation"] == 1
    assert len(store.query_collection(("projects",))) == 1
    assert len(store.query_collection(("projects", PROJECT, "members"))) == 1

def test_v10347_identity_binding_is_immutable_and_requires_real_user():
    _, state, users, _ = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    users.create(display_name="Editor", operation_id="create_editor", user_id=EDITOR)
    bindings = HostedIdentityBindingRepository(state)
    row = bindings.bind("provider-uid-a", OWNER, operation_id="bind_a")
    assert row["user_id"] == OWNER
    assert bindings.resolve("provider-uid-a") == OWNER
    with pytest.raises(HostedAuthorizationError):
        bindings.bind("provider-uid-a", EDITOR, operation_id="bind_other")


def test_v10347_project_child_repository_checks_membership_inside_transactions():
    store, state, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    users.create(display_name="Outsider", operation_id="create_outsider", user_id=OUTSIDER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    entities = HostedProjectEntityRepository(store, clock=state.clock)
    ids = {"project_id": PROJECT, "chapter_id": "chapter_a"}
    chapter = entities.create("chapter", ids, {"title": "One"}, user_id=OWNER, operation_id="chapter_create")
    assert chapter["generation"] == 1
    assert entities.get("chapter", ids, user_id=OWNER)["title"] == "One"
    with pytest.raises(ProjectUnavailable):
        entities.get("chapter", ids, user_id=OUTSIDER)
    updated = entities.update("chapter", ids, {"title": "Two"}, user_id=OWNER, expected_generation=1, operation_id="chapter_update")
    assert updated["generation"] == 2
    with pytest.raises(DocumentConflict):
        entities.update("chapter", ids, {"title": "Three"}, user_id=OWNER, expected_generation=1, operation_id="chapter_stale")


def test_v10347_immutable_entities_reject_update():
    store, state, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    entities = HostedProjectEntityRepository(store, clock=state.clock)
    ids = {"project_id": PROJECT, "checkpoint_id": "checkpoint_a"}
    entities.create("checkpoint", ids, {"label": "A"}, user_id=OWNER, operation_id="checkpoint_create")
    with pytest.raises(HostedValidationError):
        entities.update("checkpoint", ids, {"label": "B"}, user_id=OWNER, expected_generation=1, operation_id="checkpoint_update")


def test_v10347_static_adapter_validator_requires_no_cloud_credentials():
    spec = importlib.util.spec_from_file_location("adapter_validator", ROOT / "scripts/validate_hosted_firestore_adapter_contract.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.validate() == []


def test_v10347_verifier_keeps_heavy_core_contracts_in_one_worker_lane_without_raising_timeout():
    source = (ROOT / "scripts/verify_release.py").read_text(encoding="utf-8")
    assert "NO_CONTENTION_CORE_MODULES" in source
    assert 'run_stage("Core / API serial", serial_core_jobs, 1)' in source
    conftest = (ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    assert "CORE_API_TEST_TIMEOUT_SECONDS = 10.0" in conftest


def test_v10347_review_participant_record_never_becomes_breadmaker_user_authority():
    store, state, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    users.create(display_name="Outsider", operation_id="create_outsider", user_id=OUTSIDER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    entities = HostedProjectEntityRepository(store, clock=state.clock)
    review_ids = {"project_id": PROJECT, "review_id": "review_a"}
    entities.create("review", review_ids, {"title": "Alpha"}, user_id=OWNER, operation_id="review_create")
    participant_ids = {"project_id": PROJECT, "review_id": "review_a", "participant_id": "participant_a"}
    entities.create(
        "review_participant",
        participant_ids,
        {"display_name": "Guest", "user_id": OUTSIDER},
        user_id=OWNER,
        operation_id="participant_create",
    )
    with pytest.raises(ProjectUnavailable) as denied:
        entities.get("review_participant", participant_ids, user_id=OUTSIDER)
    assert str(denied.value) == "PROJECT_NOT_FOUND"


def test_v10347_managed_generation_and_timestamp_fields_are_server_owned():
    store, state, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    entities = HostedProjectEntityRepository(store, clock=state.clock)
    ids = {"project_id": PROJECT, "chapter_id": "chapter_a"}
    with pytest.raises(HostedValidationError):
        entities.create("chapter", ids, {"title": "One", "generation": 99}, user_id=OWNER, operation_id="bad_generation")
    with pytest.raises(HostedValidationError):
        entities.create("chapter", ids, {"title": "One", "created_at": "caller-owned"}, user_id=OWNER, operation_id="bad_created")


def test_v10347_membership_requires_real_hosted_user_and_retry_does_not_duplicate():
    store, state, users, projects = repositories()
    users.create(display_name="Owner", operation_id="create_owner", user_id=OWNER)
    projects.create("Book", owner_user_id=OWNER, operation_id="create_project", project_id=PROJECT)
    with pytest.raises(DocumentNotFound):
        projects.add_member(PROJECT, OWNER, EDITOR, "editor", expected_project_generation=1, operation_id="missing_editor")
    users.create(display_name="Editor", operation_id="create_editor", user_id=EDITOR)
    store.retry_next_transaction()
    member = projects.add_member(PROJECT, OWNER, EDITOR, "editor", expected_project_generation=1, operation_id="add_editor")
    assert member["role"] == "editor"
    assert len(store.query_collection(("projects", PROJECT, "members"), field="user_id", value=EDITOR)) == 1


def test_v10347_generic_project_entity_repository_cannot_bypass_specialized_roots():
    store = MemoryDocumentStore()
    entities = HostedProjectEntityRepository(store, clock=Clock())
    with pytest.raises(HostedValidationError):
        entities._model("project")
    with pytest.raises(HostedValidationError):
        entities._model("project_membership")
    with pytest.raises(HostedValidationError):
        entities._model("recovery_state")
