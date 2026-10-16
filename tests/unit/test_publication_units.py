from __future__ import annotations

import pytest

from backups.publication import (
    PUBLICATION_FORMAT,
    PUBLICATION_STATE,
    PUBLICATION_VERSION,
    build_publication_manifest,
    json_bytes,
    publication_candidate,
    publication_filename,
    validate_publication_manifest,
)


def _manifest(**overrides):
    info = {
        "project_id": "project-abc",
        "project_generation": 7,
        "name": "Novel",
        "project_fingerprint": "fp",
        "canonical_state_hash": "state",
        "created_at": "2026-09-20T12:00:00Z",
        "recovery_metadata": {"workday": "2026-09-20"},
    }
    info.update(overrides.pop("package_info", {}))
    return build_publication_manifest(
        package_file=overrides.pop("package_file", "novel.breadmaker"),
        package_sha256=overrides.pop("package_sha256", "a" * 64),
        package_bytes=overrides.pop("package_bytes", 123),
        package_info=info,
        package_locator=overrides.pop("package_locator", "/backup/novel.breadmaker"),
        published_at=overrides.pop("published_at", "2026-09-20T12:01:00Z"),
    )


@pytest.mark.parametrize("name", ["book.breadmaker", "A-Book_2.breadmaker"])
def test_publication_filename_is_sidecar_of_valid_package(name):
    assert publication_filename(name) == name + ".publication.json"


@pytest.mark.parametrize("name", ["book.zip", "../book.breadmaker", "/tmp/book.breadmaker", ""])
def test_publication_filename_rejects_non_package_or_path(name):
    with pytest.raises(ValueError):
        publication_filename(name)


def test_build_publication_manifest_carries_canonical_identity():
    row = _manifest()
    assert row["publication_format"] == PUBLICATION_FORMAT
    assert row["publication_version"] == PUBLICATION_VERSION
    assert row["state"] == PUBLICATION_STATE
    assert row["project_id"] == "project-abc"
    assert row["project_generation"] == 7
    assert row["package_sha256"] == "a" * 64


@pytest.mark.parametrize("package_info", [
    {"project_id": "", "project_generation": 7},
    {"project_id": "project-abc", "project_generation": 0},
])
def test_build_publication_requires_canonical_identity(package_info):
    with pytest.raises(ValueError, match="project id and canonical generation"):
        _manifest(package_info=package_info)


def test_build_publication_requires_sha256_shape():
    with pytest.raises(ValueError, match="sha256"):
        _manifest(package_sha256="short")


def test_validate_publication_normalizes_integer_fields():
    row = _manifest()
    row["project_generation"] = "7"
    row["package_bytes"] = "123"
    validated = validate_publication_manifest(row)
    assert validated["project_generation"] == 7
    assert validated["package_bytes"] == 123


@pytest.mark.parametrize("field,value", [
    ("state", "TRANSFERRED"),
    ("project_id", ""),
    ("project_generation", 0),
    ("package_file", "../novel.breadmaker"),
    ("package_sha256", "z" * 64),
    ("package_bytes", -1),
])
def test_validate_publication_rejects_invalid_authority(field, value):
    row = _manifest()
    row[field] = value
    with pytest.raises(ValueError):
        validate_publication_manifest(row)


def test_publication_candidate_preserves_generation_and_workday():
    candidate = publication_candidate(_manifest())
    assert candidate["candidate"] is True
    assert candidate["verified"] is False
    assert candidate["project_generation"] == 7
    assert candidate["workday"] == "2026-09-20"


def test_json_bytes_are_canonical_across_key_order():
    assert json_bytes({"b": 2, "a": 1}) == json_bytes({"a": 1, "b": 2}) == b'{"a":1,"b":2}'
