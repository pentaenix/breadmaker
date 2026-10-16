from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore


def test_v10346_hosted_model_registry_is_contract_driven_not_sqlite_driven():
    source = (ROOT / "src/hosted/firestore/model_registry.py").read_text(encoding="utf-8").lower()
    assert "sqlite" not in source
    assert "hosted data-model contract" in source


def test_v10346_firestore_adapter_uses_deterministic_release_test_driver_without_credentials():
    store = MemoryDocumentStore()
    state = HostedStructuredRepository(store)
    users = HostedUserRepository(state)
    projects = HostedProjectRepository(store)
    uid = "usr_" + "a" * 32
    pid = "1" * 32
    users.create(display_name="Writer", operation_id="create_user", user_id=uid)
    project = projects.create("Book", owner_user_id=uid, operation_id="create_project", project_id=pid)
    assert project["project_id"] == pid
    assert projects.get_for_user(pid, uid)["title"] == "Book"


def test_v10346_local_requirements_remain_google_free_while_hosted_requirements_are_explicit():
    local = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    hosted = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8").lower()
    assert "google-cloud-firestore" not in local
    assert "google-cloud-firestore==2.34.0" in hosted


def test_v10346_firestore_adapter_static_contract_agrees_with_frozen_model():
    spec = importlib.util.spec_from_file_location("adapter_validator_v10346", ROOT / "scripts/validate_hosted_firestore_adapter_contract.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.validate() == []
