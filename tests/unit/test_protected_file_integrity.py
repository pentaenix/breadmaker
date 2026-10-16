"""Regression contracts for early, single-source protected-file validation."""
from __future__ import annotations

import hashlib
import json

import pytest

from scripts import protected_file_integrity as firewall


def _tree(tmp_path):
    paths = {}
    for i, relative in enumerate(sorted(firewall.PROTECTED_PATHS)):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        value = f"reviewed-source-{i}".encode()
        path.write_bytes(value)
        paths[relative] = hashlib.sha256(value).hexdigest()
    manifest = tmp_path / firewall.MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema": 1, "protected_files": paths}), encoding="utf-8")
    return manifest


def test_pinned_integrity_accepts_a_complete_reviewed_tree(tmp_path):
    _tree(tmp_path)
    assert set(firewall.validate(tmp_path)) == firewall.PROTECTED_PATHS


def test_pinned_integrity_rejects_modified_source_without_updating_digest(tmp_path):
    _tree(tmp_path)
    protected = tmp_path / "scripts/verify_release.py"
    protected.write_bytes(protected.read_bytes() + b"\nunaudited mutation")
    with pytest.raises(ValueError, match="PROTECTED_FILE_INTEGRITY_MISMATCH.*scripts/verify_release.py"):
        firewall.validate(tmp_path)


def test_pinned_integrity_rejects_removed_source_or_manifest_entry(tmp_path):
    manifest = _tree(tmp_path)
    data = json.loads(manifest.read_text())
    del data["protected_files"]["src/runtime/local.py"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest coverage changed"):
        firewall.validate(tmp_path)


def test_pinned_integrity_rejects_added_unreviewed_manifest_entry(tmp_path):
    manifest = _tree(tmp_path)
    data = json.loads(manifest.read_text())
    data["protected_files"]["other/file.py"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest coverage changed"):
        firewall.validate(tmp_path)


def test_pinned_integrity_rejects_duplicate_manifest_keys(tmp_path):
    manifest = _tree(tmp_path)
    manifest.write_text('{"schema":1,"schema":1,"protected_files":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate protected manifest key"):
        firewall.validate(tmp_path)
