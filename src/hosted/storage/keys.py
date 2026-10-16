from __future__ import annotations

import re
from typing import Mapping

from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id

from .contracts import ObjectNameInvalid

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ASSET_EXTENSIONS = {"png", "jpg", "webp"}
_SAFE_EXPORT_EXTENSIONS = {"breadmaker", "json", "docx", "pdf", "breadreview", "breadcuts"}
_SAFE_IMPORT_FILENAMES = {
    "breadmaker": "project.breadmaker",
    "json": "project.json",
    "docx": "document.docx",
    "breadreview": "review.breadreview",
    "breadcuts": "cuts.breadcuts",
}


def require_sha256(value: str) -> str:
    digest = str(value or "").lower()
    if not _SHA256.fullmatch(digest):
        raise ObjectNameInvalid("HOSTED_OBJECT_SHA256_INVALID")
    return digest


def require_object_name(value: str) -> str:
    name = str(value or "")
    if not name or name.startswith("/") or name.endswith("/") or "\\" in name or "//" in name:
        raise ObjectNameInvalid("HOSTED_OBJECT_NAME_INVALID")
    parts = name.split("/")
    if any(not part or part in {".", ".."} or len(part) > 512 for part in parts):
        raise ObjectNameInvalid("HOSTED_OBJECT_NAME_INVALID")
    if len(name.encode("utf-8")) > 1024:
        raise ObjectNameInvalid("HOSTED_OBJECT_NAME_TOO_LONG")
    return name


def _opaque(value: str, label: str) -> str:
    return require_opaque_id(str(value), label=label)


def _asset_ext(value: str) -> str:
    ext = str(value or "").strip().lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in _SAFE_ASSET_EXTENSIONS:
        raise ObjectNameInvalid("HOSTED_ASSET_EXTENSION_INVALID")
    return ext


def project_asset_key(project_id: str, digest: str, extension: str) -> str:
    return require_object_name(
        f"projects/{require_project_id(project_id)}/assets/sha256/{require_sha256(digest)}.{_asset_ext(extension)}"
    )


def block_payload_key(project_id: str, block_id: str, digest: str) -> str:
    return require_object_name(
        f"projects/{require_project_id(project_id)}/block-payloads/{_opaque(block_id, 'block_id')}/{require_sha256(digest)}.json.gz"
    )


def cut_payload_key(project_id: str, cut_id: str, digest: str) -> str:
    return require_object_name(
        f"projects/{require_project_id(project_id)}/cuts/{_opaque(cut_id, 'cut_id')}/{require_sha256(digest)}.json.gz"
    )


def revision_key(project_id: str, project_generation: int, revision_id: str) -> str:
    generation = int(project_generation)
    if generation < 1 or generation > 99_999_999_999_999_999_999:
        raise ObjectNameInvalid("HOSTED_REVISION_GENERATION_INVALID")
    return require_object_name(
        f"projects/{require_project_id(project_id)}/revisions/{generation:020d}/{_opaque(revision_id, 'revision_id')}.revision.json.gz"
    )


def checkpoint_key(project_id: str, checkpoint_id: str, digest: str) -> str:
    return require_object_name(
        f"projects/{require_project_id(project_id)}/checkpoints/{_opaque(checkpoint_id, 'checkpoint_id')}/{require_sha256(digest)}.checkpoint.json.gz"
    )


def checkpoint_asset_key(project_id: str, digest: str) -> str:
    return require_object_name(
        f"projects/{require_project_id(project_id)}/checkpoint-assets/sha256/{require_sha256(digest)}"
    )


def review_snapshot_key(project_id: str, review_id: str, snapshot_id: str, digest: str) -> str:
    return require_object_name(
        "projects/"
        f"{require_project_id(project_id)}/reviews/{_opaque(review_id, 'review_id')}/snapshots/"
        f"{_opaque(snapshot_id, 'snapshot_id')}/{require_sha256(digest)}.snapshot.json.gz"
    )


def user_avatar_key(user_id: str, avatar_revision: str) -> str:
    return require_object_name(
        f"users/{require_user_id(user_id)}/avatars/{_opaque(avatar_revision, 'avatar_revision')}.webp"
    )


def export_key(project_id: str, export_id: str, extension: str) -> str:
    ext = str(extension or "").strip().lower().lstrip(".")
    if ext not in _SAFE_EXPORT_EXTENSIONS:
        raise ObjectNameInvalid("HOSTED_EXPORT_EXTENSION_INVALID")
    return require_object_name(
        f"projects/{require_project_id(project_id)}/exports/{_opaque(export_id, 'export_id')}/export.{ext}"
    )


def import_key(user_id: str, upload_id: str, kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    filename = _SAFE_IMPORT_FILENAMES.get(normalized)
    if filename is None:
        raise ObjectNameInvalid("HOSTED_IMPORT_KIND_INVALID")
    return require_object_name(
        f"imports/{require_user_id(user_id)}/{_opaque(upload_id, 'upload_id')}/{filename}"
    )

_ENTITY_OBJECT_PATTERNS = {
    "project_asset": re.compile(r"^projects/(?P<project>[0-9a-f]{32})/assets/sha256/(?P<digest>[0-9a-f]{64})\.(?P<ext>png|jpg|webp)$"),
    "manuscript_block": re.compile(r"^projects/(?P<project>[0-9a-f]{32})/block-payloads/(?P<block>[A-Za-z0-9_-]{1,160})/(?P<digest>[0-9a-f]{64})\.json\.gz$"),
    "cut": re.compile(r"^projects/(?P<project>[0-9a-f]{32})/cuts/(?P<cut>[A-Za-z0-9_-]{1,160})/(?P<digest>[0-9a-f]{64})\.json\.gz$"),
    "checkpoint": re.compile(r"^projects/(?P<project>[0-9a-f]{32})/checkpoints/(?P<checkpoint>[A-Za-z0-9_-]{1,160})/(?P<digest>[0-9a-f]{64})\.checkpoint\.json\.gz$"),
    "review_snapshot": re.compile(r"^projects/(?P<project>[0-9a-f]{32})/reviews/(?P<review>[A-Za-z0-9_-]{1,160})/snapshots/(?P<snapshot>[A-Za-z0-9_-]{1,160})/(?P<digest>[0-9a-f]{64})\.snapshot\.json\.gz$"),
}


def require_hybrid_entity_object_key(logical_entity: str, ids: Mapping[str, str], name: str) -> str:
    """Validate that a hybrid entity points only at its canonical content-addressed key family.

    The caller still constructs the key with the helpers above.  This validator is
    a defensive publication boundary so a later HTTP/runtime integration cannot
    substitute an arbitrary traversal-safe browser string for a server key.
    """
    entity = str(logical_entity or "")
    pattern = _ENTITY_OBJECT_PATTERNS.get(entity)
    if pattern is None:
        raise ObjectNameInvalid("HOSTED_OBJECT_ENTITY_UNSUPPORTED")
    try:
        values = dict(ids)
    except (TypeError, ValueError) as exc:
        raise ObjectNameInvalid("HOSTED_OBJECT_ENTITY_IDS_INVALID") from exc
    key = require_object_name(name)
    match = pattern.fullmatch(key)
    if match is None:
        raise ObjectNameInvalid("HOSTED_OBJECT_ENTITY_KEY_INVALID")
    pid = require_project_id(str(values.get("project_id") or ""))
    if match.group("project") != pid:
        raise ObjectNameInvalid("HOSTED_OBJECT_PROJECT_MISMATCH")
    expected_ids = {
        "manuscript_block": (("block", "block_id"),),
        "cut": (("cut", "cut_id"),),
        "checkpoint": (("checkpoint", "checkpoint_id"),),
        "review_snapshot": (("review", "review_id"), ("snapshot", "snapshot_id")),
    }.get(entity, ())
    for group, field in expected_ids:
        expected = _opaque(str(values.get(field) or ""), field)
        if match.group(group) != expected:
            raise ObjectNameInvalid("HOSTED_OBJECT_ENTITY_ID_MISMATCH")
    return require_sha256(match.group("digest"))

