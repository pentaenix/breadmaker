from __future__ import annotations

"""Static Milestone-2 hosted-data-model contract validation.

This is deliberately *not* the Breadmaker verifier/test suite. It performs only
source/JSON consistency checks so an implementation model can catch contract drift
without executing pytest, browser/E2E, or ./verify-release.
"""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "HOSTED_DATA_MODEL_V1.json"
SOURCE_INVENTORY = ROOT / "docs" / "HOSTED_SCHEMA42_SOURCE_INVENTORY.json"
INDEX_PLAN = ROOT / "docs" / "HOSTED_FIRESTORE_INDEXES_V1.json"
SCHEMA = ROOT / "src" / "storage" / "schema.py"


def _schema_inventory() -> dict[str, Any]:
    source = SCHEMA.read_text(encoding="utf-8")
    match = re.search(r"SCHEMA_SQL\s*=\s*r'''(.*)'''", source, re.S)
    if not match:
        raise RuntimeError("HOSTED_MODEL_SCHEMA_SQL_NOT_FOUND")
    version_match = re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", source)
    if not version_match:
        raise RuntimeError("HOSTED_MODEL_SCHEMA_VERSION_NOT_FOUND")
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(match.group(1))
        tables = [
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        payload: dict[str, Any] = {}
        for table in tables:
            payload[table] = {
                "columns": [
                    {
                        "name": row[1],
                        "type": row[2],
                        "not_null": bool(row[3]),
                        "default": row[4],
                        "pk_position": int(row[5]),
                    }
                    for row in con.execute(f"PRAGMA table_info('{table}')")
                ],
                "foreign_keys": [
                    {
                        "from": row[3],
                        "to_table": row[2],
                        "to_column": row[4],
                        "on_delete": row[6],
                    }
                    for row in con.execute(f"PRAGMA foreign_key_list('{table}')")
                ],
            }
    finally:
        con.close()
    return {
        "format": 1,
        "schema_version": int(version_match.group(1)),
        "table_count": len(payload),
        "tables": payload,
    }


def validate() -> list[str]:
    errors: list[str] = []
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected_source = json.loads(SOURCE_INVENTORY.read_text(encoding="utf-8"))
    actual_source = _schema_inventory()
    indexes = json.loads(INDEX_PLAN.read_text(encoding="utf-8"))

    if actual_source != expected_source:
        errors.append("Schema-42 source inventory does not match src/storage/schema.py")

    mappings = list(contract.get("schema42_mappings") or [])
    mapped = [str(item.get("source_table") or "") for item in mappings]
    actual_tables = sorted(actual_source["tables"])
    if sorted(mapped) != actual_tables:
        errors.append("Schema-42 mapping set is not exactly the current table set")
    if len(mapped) != len(set(mapped)):
        errors.append("One or more Schema-42 tables are mapped more than once")

    required = {
        "source_table", "logical_entity", "hosted_store", "id_policy", "ownership",
        "authorization_policy", "expected_max_size", "concurrency_policy",
        "delete_policy", "recovery_policy", "representation_notes", "source_columns",
    }
    policy_groups = contract.get("policies") or {}
    for item in mappings:
        table = str(item.get("source_table") or "")
        missing = sorted(required - set(item))
        if missing:
            errors.append(f"{table}: missing mapping fields {missing}")
        for field, group in (
            ("authorization_policy", "authorization"),
            ("concurrency_policy", "concurrency"),
            ("delete_policy", "delete"),
            ("recovery_policy", "recovery"),
        ):
            value = str(item.get(field) or "")
            if value not in (policy_groups.get(group) or {}):
                errors.append(f"{table}: unknown {field} {value!r}")
        expected_columns = [row["name"] for row in actual_source["tables"].get(table, {}).get("columns", [])]
        if list(item.get("source_columns") or []) != expected_columns:
            errors.append(f"{table}: source_columns drifted from Schema 42")
        store = str(item.get("hosted_store") or "")
        if store in {"firestore", "hybrid"} and not item.get("firestore_path"):
            errors.append(f"{table}: Firestore/hybrid mapping has no Firestore path")
        if store in {"gcs", "hybrid"} and not item.get("gcs_path"):
            errors.append(f"{table}: GCS/hybrid mapping has no GCS path")

    identity = contract.get("identity_model") or {}
    if identity.get("email_is_durable_key") is not False:
        errors.append("Email must not be a durable identity key")
    if "not a capability" not in str(identity.get("project_id") or ""):
        errors.append("Project-ID authorization-neutral invariant is missing")
    if "usr_" not in str(identity.get("durable_user_key") or ""):
        errors.append("Provider-independent internal Breadmaker user ID decision is missing")

    guards = contract.get("provider_guards") or {}
    hard = int(guards.get("firestore_document_hard_limit_bytes") or 0)
    soft = int(guards.get("firestore_document_soft_limit_bytes") or 0)
    inline = int(guards.get("normal_inline_block_payload_soft_limit_bytes") or 0)
    if not (0 < inline < soft < hard):
        errors.append("Firestore inline/soft/hard size guards are not strictly ordered")
    if hard != 1048576:
        errors.append("Milestone-2 contract must explicitly record the current 1 MiB Firestore hard limit")

    block = next((item for item in mappings if item.get("source_table") == "manuscript_blocks"), None)
    if not block or block.get("hosted_store") != "hybrid" or "block-payloads" not in str(block.get("gcs_path") or ""):
        errors.append("Manuscript blocks must have bounded Firestore state plus a GCS overflow path")

    snapshot_tables = {
        "review_snapshot_checkpoints", "review_snapshot_markers", "review_snapshot_chapters",
        "review_snapshot_scenes", "review_snapshot_blocks", "review_snapshot_questions",
    }
    snapshot_rows = [item for item in mappings if item.get("source_table") in snapshot_tables]
    if len(snapshot_rows) != len(snapshot_tables) or any(item.get("hosted_store") != "gcs" for item in snapshot_rows):
        errors.append("Review snapshot projection tables must map to immutable GCS sections")
    if len({item.get("object_section") for item in snapshot_rows}) != len(snapshot_tables):
        errors.append("Review snapshot GCS sections are not unique")

    mutation_classes = contract.get("revision_envelope", {}).get("publication_order") or []
    if not any("ifGenerationMatch=0" in str(step) for step in mutation_classes):
        errors.append("Revision publication is missing create-only GCS precondition")
    orphan = contract.get("orphan_policy") or {}
    if "missing" not in str(orphan.get("forbidden_failure") or ""):
        errors.append("Orphan policy does not forbid pointers to missing objects")

    non_sql = list(contract.get("non_sql_state_mappings") or [])
    required_sources = {
        "workspace/profiles/index.json",
        "workspace/projects/{project}/local.json",
        "projects/{project}/assets/*",
        "revision_history/v1/*",
        "projects/{project}/backups/recovery_state.json",
        "workspace trash directory",
    }
    found_sources = {str(row.get("source") or "") for row in non_sql}
    if not required_sources.issubset(found_sources):
        errors.append("Non-SQL hosted state inventory is incomplete")

    if not isinstance(indexes.get("indexes"), list) or not indexes["indexes"]:
        errors.append("Firestore composite index plan is empty")
    if not isinstance(indexes.get("fieldOverrides"), list) or not indexes["fieldOverrides"]:
        errors.append("Firestore field-index exemption plan is empty")
    block_overrides = {
        row.get("fieldPath")
        for row in indexes.get("fieldOverrides", [])
        if row.get("collectionGroup") == "blocks" and row.get("indexes") == []
    }
    if not {"content_html", "text_content"}.issubset(block_overrides):
        errors.append("Large manuscript block text fields must be exempted from Firestore indexing")

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for forbidden in ("google-cloud-firestore", "google-cloud-storage", "firebase"):
        if forbidden in requirements:
            errors.append(f"Local requirements unexpectedly contain {forbidden}")

    return errors


if __name__ == "__main__":
    problems = validate()
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}")
        raise SystemExit(1)
    print("Hosted data model contract: static consistency OK")
