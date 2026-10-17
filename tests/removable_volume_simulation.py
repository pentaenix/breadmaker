"""Deterministic removable-volume fixture for portable backup tests.

Renaming a directory on Linux doesn't unmount the filesystem: findmnt still
reports the original volume UUID and Breadmaker should flag a missing folder
on a mounted volume as needing attention. These tests instead model a real USB
unmount/mount without relying on OS privileges or host mount configuration.
"""
from pathlib import Path
from workspace import filesystem as workspace_fs


def simulate_removable_volume(monkeypatch, mount_root: Path):
    mount_root = Path(mount_root).resolve()
    metadata = workspace_fs._volume_metadata
    roots = workspace_fs._mounted_volume_roots
    fake_id = "test-removable:stable-volume-id"

    def get_metadata(path):
        path = Path(path).resolve()
        if mount_root == path or mount_root in path.parents:
            return {"id": fake_id, "root": str(mount_root), "label": "Test removable drive"}
        return metadata(path)

    def get_roots():
        # The test's fake removable mount vanishes when the folder is parked.
        # Leave other host volumes visible to detect accidental identity matches.
        existing = [p for p in roots() if Path(p).resolve() != mount_root]
        return [*existing, mount_root] if mount_root.is_dir() else existing

    monkeypatch.setattr(workspace_fs, "_volume_metadata", get_metadata)
    monkeypatch.setattr(workspace_fs, "_mounted_volume_roots", get_roots)
