from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from storage.schema import SCHEMA_VERSION

HASH_CHUNK_BYTES = 1024 * 1024


class FrozenSnapshotError(RuntimeError):
    """A committed project generation could not be captured as a stable snapshot."""


@dataclass(frozen=True)
class FrozenCanonicalSnapshot:
    """Private immutable source state for one committed canonical generation.

    Package/export code must consume only these copied files after capture.  It must
    never return to the live project database/assets while constructing an artifact.
    """

    root: Path
    database: Path
    assets: tuple[tuple[Path, str], ...]
    project_generation: int
    project_id: int
    project_title: str
    project_created_at: str
    project_updated_at: str
    schema_version: int
    file_hashes: dict[str, str]
    project_fingerprint: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, AttributeError):
        pass


def _copy_finalized_asset(source: Path, destination: Path) -> str:
    """Copy one immutable finalized asset and prove it did not change mid-copy.

    Breadmaker-owned asset writers use temp+fsync+atomic-rename, so a live asset is
    immutable after publication.  The before/after fstat check is an additional
    fail-closed guard against manual/external modification while a snapshot is taken.
    """

    if source.is_symlink():
        raise FrozenSnapshotError(f"SNAPSHOT_ASSET_SYMLINK:{source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".snapshot-tmp")
    tmp.unlink(missing_ok=True)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, tmp.open("xb") as dst:
            before = os.fstat(src.fileno())
            while True:
                chunk = src.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                dst.write(chunk)
                digest.update(chunk)
            dst.flush()
            os.fsync(dst.fileno())
            after = os.fstat(src.fileno())
        if (
            before.st_ino != after.st_ino
            or before.st_dev != after.st_dev
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise FrozenSnapshotError(f"SNAPSHOT_ASSET_CHANGED_DURING_CAPTURE:{source.name}")
        if tmp.stat().st_size != before.st_size:
            raise FrozenSnapshotError(f"SNAPSHOT_ASSET_SIZE_MISMATCH:{source.name}")
        os.replace(tmp, destination)
        _fsync_parent(destination)
        return digest.hexdigest()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise



def _asset_archive_path(api_path: str) -> str | None:
    if not api_path.startswith('/api/assets/'):
        return None
    raw=api_path.removeprefix('/api/assets/')
    rel=Path(raw)
    if not raw or rel.is_absolute() or '..' in rel.parts or any(part in {'','.','..'} for part in rel.parts):
        return None
    return (Path('assets')/rel).as_posix()

def capture_frozen_snapshot(db_path: Path, asset_dir: Path, target_root: Path) -> FrozenCanonicalSnapshot:
    """Capture one committed canonical SQLite generation plus finalized assets.

    SQLite's backup API gives a transactionally consistent database image.  Asset
    files are copied into the same private snapshot root before this function returns.
    Once returned, consumers must use only the returned paths/hashes.
    """

    db_path = Path(db_path).resolve()
    asset_dir = Path(asset_dir).resolve()
    target_root = Path(target_root).resolve()
    if not db_path.is_file():
        raise FrozenSnapshotError("SNAPSHOT_PROJECT_DATABASE_MISSING")

    # Reject unsupported pre-alpha storage before creating even a private copy.
    # The frozen boundary is not a migration/export escape hatch: callers may only
    # snapshot the one schema this build can safely interpret. The copied database
    # is checked again below so this read-only preflight is not trusted as authority.
    try:
        ro = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {str(row[0]) for row in ro.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            schema_row = ro.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone() if "storage_meta" in tables else None
            source_schema = int(schema_row[0]) if schema_row and str(schema_row[0]).isdigit() else 0
        finally:
            ro.close()
    except sqlite3.Error as exc:
        raise FrozenSnapshotError("SNAPSHOT_SOURCE_UNREADABLE") from exc
    if source_schema != SCHEMA_VERSION:
        raise FrozenSnapshotError(f"SNAPSHOT_SCHEMA_UNSUPPORTED:{source_schema}")

    target_root.mkdir(parents=True, exist_ok=True)
    database = target_root / "project.sqlite3"
    assets_root = target_root / "assets"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(database)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(database)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise FrozenSnapshotError("SNAPSHOT_DATABASE_CORRUPT")
        if check.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise FrozenSnapshotError("SNAPSHOT_FOREIGN_KEYS_INVALID")
        tables = {str(row[0]) for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "projects" not in tables:
            raise FrozenSnapshotError("SNAPSHOT_PROJECT_ROW_MISSING")
        cols = {str(row[1]) for row in check.execute("PRAGMA table_info(projects)")}
        select = "SELECT id,project_uid,title,created_at,updated_at" + (",project_generation" if "project_generation" in cols else "") + " FROM projects ORDER BY id LIMIT 1"
        row = check.execute(select).fetchone()
        if row is None:
            raise FrozenSnapshotError("SNAPSHOT_PROJECT_ROW_MISSING")
        project_id = int(row[0])
        project_uid = str(row[1] or "")
        if not re.fullmatch(r'[0-9a-f]{32}',project_uid):
            raise FrozenSnapshotError("SNAPSHOT_PROJECT_IDENTITY_INVALID")
        project_title = str(row[2] or "Untitled Book")
        project_created_at = str(row[3] or "")
        project_updated_at = str(row[4] or "")
        generation = int(row[5] or 0) if len(row) > 5 else 0
        schema_row = check.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone() if "storage_meta" in tables else None
        schema = int(schema_row[0]) if schema_row and str(schema_row[0]).isdigit() else 0
        if schema != SCHEMA_VERSION:
            raise FrozenSnapshotError(f"SNAPSHOT_SCHEMA_UNSUPPORTED:{schema}")
        if generation < 1:
            raise FrozenSnapshotError("SNAPSHOT_GENERATION_INVALID")
        refs: set[str] = set()
        if "manuscript_blocks" in tables:
            refs.update(str(row[0]) for row in check.execute("SELECT image_path FROM manuscript_blocks WHERE COALESCE(image_path,'')<>''"))
        if "canvas_nodes" in tables:
            refs.update(str(row[0]) for row in check.execute("SELECT image_path FROM canvas_nodes WHERE COALESCE(image_path,'')<>''"))
        if "review_snapshot_blocks" in tables:
            refs.update(str(row[0]) for row in check.execute("SELECT image_path FROM review_snapshot_blocks WHERE COALESCE(image_path,'')<>''"))
        if "cuts" in tables:
            for cut_row in check.execute("SELECT content_html FROM cuts WHERE COALESCE(content_html,'')<>''"):
                refs.update('/api/assets/'+name for name in re.findall(r'/api/assets/([A-Za-z0-9._/\-]+)',str(cut_row[0] or '')))
    finally:
        check.close()

    file_hashes: dict[str, str] = {"project.sqlite3": _sha256_file(database)}
    frozen_assets: list[tuple[Path, str]] = []
    for value in sorted(refs):
        if not value.startswith('/api/assets/'):
            continue
        archive_path=_asset_archive_path(value)
        if not archive_path:
            raise FrozenSnapshotError(f"SNAPSHOT_REFERENCED_ASSET_MISSING:{value}")
        relative=Path(archive_path).relative_to('assets')
        source=(asset_dir/relative).resolve()
        try:source.relative_to(asset_dir)
        except ValueError as exc:raise FrozenSnapshotError("SNAPSHOT_ASSET_ESCAPED_ROOT") from exc
        if not source.is_file():
            raise FrozenSnapshotError(f"SNAPSHOT_REFERENCED_ASSET_MISSING:{value}")
        destination=assets_root/relative
        file_hashes[archive_path]=_copy_finalized_asset(source,destination)
        frozen_assets.append((destination,archive_path))

    fingerprint = hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FrozenCanonicalSnapshot(
        root=target_root,
        database=database,
        assets=tuple(frozen_assets),
        project_generation=generation,
        project_id=project_id,
        project_title=project_title,
        project_created_at=project_created_at,
        project_updated_at=project_updated_at,
        schema_version=schema,
        file_hashes=file_hashes,
        project_fingerprint=fingerprint,
    )


def verify_frozen_package_payload(package: Path, expected_hashes: dict[str,str], expected_generation: int) -> None:
    """Re-read a staging ZIP and prove it contains the frozen source bytes exactly."""
    import zipfile
    package=Path(package)
    try:
        with zipfile.ZipFile(package) as zf:
            names={info.filename for info in zf.infolist() if not info.is_dir()}
            if 'manifest.json' not in names or set(expected_hashes)!=(names-{'manifest.json'}):
                raise FrozenSnapshotError('SNAPSHOT_PACKAGE_FILE_SET_MISMATCH')
            try:manifest=json.loads(zf.read('manifest.json'))
            except Exception as exc:raise FrozenSnapshotError('SNAPSHOT_PACKAGE_MANIFEST_INVALID') from exc
            if int(manifest.get('project_generation') or 0)!=int(expected_generation):
                raise FrozenSnapshotError('SNAPSHOT_PACKAGE_GENERATION_MISMATCH')
            hashes=manifest.get('file_hashes')
            if hashes!=expected_hashes:
                raise FrozenSnapshotError('SNAPSHOT_PACKAGE_HASH_MANIFEST_MISMATCH')
            for rel,expected in expected_hashes.items():
                h=hashlib.sha256()
                with zf.open(rel) as fh:
                    for chunk in iter(lambda:fh.read(HASH_CHUNK_BYTES),b''):
                        h.update(chunk)
                if h.hexdigest()!=expected:
                    raise FrozenSnapshotError(f'SNAPSHOT_PACKAGE_PAYLOAD_MISMATCH:{rel}')
    except zipfile.BadZipFile as exc:
        raise FrozenSnapshotError('SNAPSHOT_PACKAGE_ZIP_INVALID') from exc
