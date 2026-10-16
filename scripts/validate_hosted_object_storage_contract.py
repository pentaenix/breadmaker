from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate() -> list[str]:
    errors: list[str] = []
    contract = json.loads((ROOT / "docs/HOSTED_OBJECT_STORAGE_ADAPTER_V1.json").read_text(encoding="utf-8"))
    frozen = json.loads((ROOT / "docs/HOSTED_DATA_MODEL_V1.json").read_text(encoding="utf-8"))
    local_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    hosted_requirements = (ROOT / "requirements-hosted.txt").read_text(encoding="utf-8").lower()
    google_driver = (ROOT / "src/hosted/storage/google_driver.py").read_text(encoding="utf-8")
    keys = (ROOT / "src/hosted/storage/keys.py").read_text(encoding="utf-8")
    publication = (ROOT / "src/hosted/storage/publication.py").read_text(encoding="utf-8")
    local_runtime = (ROOT / "src/runtime/local.py").read_text(encoding="utf-8")
    storage_init = (ROOT / "src/hosted/storage/__init__.py").read_text(encoding="utf-8")

    if contract.get("milestone") != 4:
        errors.append("contract milestone must be 4")
    if frozen.get("target", {}).get("object_state") != "Cloud Storage":
        errors.append("frozen model no longer targets Cloud Storage")
    if "google-cloud-storage" in local_requirements:
        errors.append("local requirements must remain Cloud Storage free")
    if contract["dependency"]["pin"].lower() not in hosted_requirements:
        errors.append("hosted Cloud Storage dependency pin missing")
    if "from google.cloud import storage" not in google_driver:
        errors.append("Google SDK boundary missing")
    if "if_generation_match=expected" not in google_driver or "if_generation_match=0" not in publication:
        errors.append("generation precondition contract missing")
    if "read_chunks" not in publication or "HOSTED_OBJECT_HASH_MISMATCH" not in publication:
        errors.append("object readback/hash verification missing")
    if "require_hybrid_entity_object_key" not in publication or "HOSTED_OBJECT_KEY_HASH_MISMATCH" not in publication:
        errors.append("hybrid entity canonical key/content-hash binding missing")
    if "discover_orphans" not in publication or "delete_confirmed_orphan" not in publication:
        errors.append("orphan discovery/cleanup separation missing")
    if "runtime.hosted" in storage_init:
        errors.append("object adapter must not import hosted runtime composition")
    if "HostedAssetStore" not in storage_init:
        errors.append("hosted asset adapter export missing")
    if "hosted.storage" in local_runtime:
        errors.append("local runtime must not depend on hosted object adapter")
    for token in (
        "assets/sha256",
        "block-payloads",
        "revisions/",
        "checkpoints/",
        "checkpoint-assets/sha256",
        "snapshots/",
        "exports/",
        "imports/",
    ):
        if token not in keys:
            errors.append(f"server-generated key family missing: {token}")
    return errors


if __name__ == "__main__":
    failures = validate()
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        raise SystemExit(1)
    print("Hosted object storage adapter contract: static consistency OK")
