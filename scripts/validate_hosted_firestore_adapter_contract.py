from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_registry():
    src = ROOT / "src"
    import sys
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from hosted.firestore.model_registry import MODELS
    return MODELS


def validate() -> list[str]:
    errors: list[str] = []
    model = json.loads((ROOT / "docs/HOSTED_DATA_MODEL_V1.json").read_text(encoding="utf-8"))
    adapter = json.loads((ROOT / "docs/HOSTED_FIRESTORE_ADAPTER_V1.json").read_text(encoding="utf-8"))
    registry = _load_registry()
    expected: dict[str, tuple[str, str, str, str]] = {}
    claimed_paths: set[str] = set()
    for row in [*model["schema42_mappings"], *model["non_sql_state_mappings"]]:
        if row.get("hosted_store") not in {"firestore", "hybrid"}:
            continue
        path = row.get("firestore_path") or row.get("path")
        if not path or "+" in path or ".status=" in path or path in claimed_paths:
            continue
        claimed_paths.add(path)
        expected.setdefault(row["logical_entity"], (
            path,
            row["authorization_policy"],
            row["concurrency_policy"],
            row["hosted_store"],
        ))
    actual = {
        key: (value.path_template, value.authorization_policy, value.concurrency_policy, value.hosted_store)
        for key, value in registry.items()
    }
    if actual != expected:
        errors.append("generated hosted model registry differs from frozen data-model contract")
    registry_source = (ROOT / "src/hosted/firestore/model_registry.py").read_text(encoding="utf-8").lower()
    if "sqlite" in registry_source:
        errors.append("hosted model registry contains local-engine implementation terminology")
    local_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    hosted_requirements = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8").lower()
    if "google-cloud-firestore" in local_requirements:
        errors.append("local requirements include Firestore SDK")
    if "google-cloud-firestore==2.34.0" not in hosted_requirements:
        errors.append("hosted requirements do not pin the reviewed Firestore SDK")
    if adapter["runtime_integration"] != "deferred_to_milestone_5":
        errors.append("Milestone 3 must not claim hosted application integration")
    invariants = adapter.get("invariants", {})
    if invariants.get("review_participant_record_is_user_authority") is not False:
        errors.append("review participant records must not become Breadmaker user authority")
    if invariants.get("external_reviewer_requires_server_issued_session") is not True:
        errors.append("external reviewer access must remain bound to server-issued reviewer sessions")
    repositories = (ROOT / "src/hosted/firestore/repositories.py").read_text(encoding="utf-8")
    if 'field="user_id"' in repositories and '"participants"' in repositories:
        errors.append("hosted repository appears to derive reviewer authority from participant user_id")
    if "_reject_managed_create_fields(public)" not in repositories:
        errors.append("hosted create paths do not reserve generation/timestamp fields")
    return errors


if __name__ == "__main__":
    problems = validate()
    if problems:
        raise SystemExit("\n".join(problems))
    print("Hosted Firestore adapter contract: static consistency OK")
