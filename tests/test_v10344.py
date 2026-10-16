from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_json(path: str):
    return json.loads(read(path))


def schema_tables() -> list[str]:
    source = read("src/storage/schema.py")
    sql = re.search(r"SCHEMA_SQL\s*=\s*r'''(.*)'''", source, re.S)
    assert sql
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(sql.group(1))
        return [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
    finally:
        con.close()


def test_v10344_release_is_specification_only_and_local_schema_stays_42():
    assert tuple(map(int, read("VERSION").strip().split("."))) >= (10, 3, 44)
    assert "SCHEMA_VERSION = 42" in read("src/storage/schema.py")
    assert "APP_SCHEMA_VERSION = 42" in read("src/workspace/constants.py")
    requirements = read("requirements.txt").lower()
    assert "google-cloud-firestore" not in requirements
    assert "google-cloud-storage" not in requirements
    assert "firebase" not in requirements


def test_v10344_every_schema42_table_is_mapped_exactly_once():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    source = load_json("docs/HOSTED_SCHEMA42_SOURCE_INVENTORY.json")
    actual = schema_tables()
    mapped = [row["source_table"] for row in model["schema42_mappings"]]
    assert source["schema_version"] == 42
    assert source["table_count"] == 54
    assert sorted(source["tables"]) == actual
    assert len(mapped) == len(set(mapped)) == 54
    assert sorted(mapped) == actual


def test_v10344_hosted_identity_is_provider_independent_and_project_id_is_not_a_capability():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    identity = model["identity_model"]
    assert identity["email_is_durable_key"] is False
    assert "usr_" in identity["durable_user_key"]
    assert "Identity Platform UID" in identity["auth_key"]
    assert "not a capability" in identity["project_id"]
    assert model["target"]["direct_browser_firestore_access"] is False
    assert "projects/{project_id}/members/{user_id}" in identity["project_membership_authority"]


def test_v10344_firestore_never_becomes_an_unbounded_manuscript_or_blob_store():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    guards = model["provider_guards"]
    assert guards["normal_inline_block_payload_soft_limit_bytes"] < guards["firestore_document_soft_limit_bytes"] < guards["firestore_document_hard_limit_bytes"]
    assert guards["firestore_document_hard_limit_bytes"] == 1024 * 1024
    by_table = {row["source_table"]: row for row in model["schema42_mappings"]}
    blocks = by_table["manuscript_blocks"]
    assert blocks["hosted_store"] == "hybrid"
    assert "block-payloads" in blocks["gcs_path"]
    assert "128 KiB" in blocks["expected_max_size"]
    for table in (
        "review_snapshot_checkpoints", "review_snapshot_markers", "review_snapshot_chapters",
        "review_snapshot_scenes", "review_snapshot_blocks", "review_snapshot_questions",
    ):
        assert by_table[table]["hosted_store"] == "gcs"
        assert by_table[table]["object_section"]


def test_v10344_gcs_publication_is_create_only_pointer_last_and_orphan_safe():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    order = model["revision_envelope"]["publication_order"]
    assert "ifGenerationMatch=0" in order[0]
    assert "verify" in order[1].lower()
    assert "Firestore transaction" in order[2]
    assert "save success" in order[3]
    assert "orphan" in model["orphan_policy"]["allowed_failure"].lower()
    assert "must never reference a missing" in model["orphan_policy"]["forbidden_failure"]
    assert "user filenames" in model["provider_guards"]["gcs_names"]


def test_v10344_large_operations_have_a_durable_resumable_protocol():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    protocol = model["generation_model"]["bulk_mutation_protocol"]
    for phrase in ("durable operation record", "idempotent bounded batches", "partially applied", "resumable", "pre-operation revision"):
        assert phrase in protocol


def test_v10344_non_sql_workspace_history_and_recovery_state_are_not_handwaved():
    model = load_json("docs/HOSTED_DATA_MODEL_V1.json")
    by_source = {row["source"]: row for row in model["non_sql_state_mappings"]}
    for source in (
        "workspace/profiles/index.json",
        "workspace/profiles/{usr}/avatar.webp",
        "workspace/projects/{project}/local.json",
        "workspace/registry.json + project manifest.json",
        "projects/{project}/assets/*",
        "revision_history/v1/*",
        "projects/{project}/backups/recovery_state.json",
        "workspace trash directory",
        "session undo journal / process-local UI editing state",
    ):
        assert source in by_source
    assert "fresh hosted project ID" in by_source["uploaded .breadmaker/.docx/.breadreview/.breadcuts packages"]["notes"]
    assert "Secret Manager" in by_source["project manifest backup_destinations + credential files"]["notes"]


def test_v10344_firestore_index_plan_is_source_controlled_and_large_text_is_unindexed():
    plan = load_json("docs/HOSTED_FIRESTORE_INDEXES_V1.json")
    assert plan["indexes"]
    assert plan["fieldOverrides"]
    assert any(row["collectionGroup"] == "members" and any(field["fieldPath"] == "last_opened_at" for field in row["fields"]) for row in plan["indexes"])
    block_exemptions = {
        row["fieldPath"]
        for row in plan["fieldOverrides"]
        if row["collectionGroup"] == "blocks" and row["indexes"] == []
    }
    assert {"content_html", "text_content"}.issubset(block_exemptions)


def test_v10344_static_contract_validator_agrees_with_source_without_cloud_credentials():
    spec = importlib.util.spec_from_file_location(
        "bm_hosted_model_validator", ROOT / "scripts" / "validate_hosted_data_model_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.validate() == []


def test_v10344_human_spec_records_model_verifier_rule_and_milestone_three_handoff():
    doc = read("docs/HOSTED_DATA_MODEL_V1.md")
    assert "model does not run it" in doc
    assert "Milestone 3" in doc
    assert "No Firestore/GCS SDK is added to local requirements" in doc
    assert "Every Schema-42 table is mapped exactly once" in doc
