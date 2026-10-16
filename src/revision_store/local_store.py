from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from backups.frozen_snapshot import FrozenCanonicalSnapshot, FrozenSnapshotError, capture_frozen_snapshot

STORE_FORMAT = 'breadmaker-local-revision-store'
STORE_VERSION = 1
STORE_METADATA_FILE = 'repository.json'
SNAPSHOT_FORMAT = 'breadmaker-local-revision-snapshot'
SNAPSHOT_VERSION = 1
REVISION_FORMAT = 'breadmaker-local-revision'
REVISION_VERSION = 1
CHECKPOINT_FORMAT = 'breadmaker-local-revision-checkpoint'
CHECKPOINT_VERSION = 1
DEFAULT_CHECKPOINT_INTERVAL = 10
HASH_CHUNK_BYTES = 1024 * 1024

_REVISION_FILE = re.compile(r'^g(?P<generation>\d{20})-(?P<id>[0-9a-f]{32})\.revision\.json$')
_CHECKPOINT_FILE = re.compile(r'^g(?P<generation>\d{20})-(?P<id>[0-9a-f]{32})\.checkpoint\.json$')
_STORE_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[str, threading.RLock] = {}


class RevisionStoreError(RuntimeError):
    pass


class StaleRevisionPublication(RevisionStoreError):
    pass


class RevisionStoreCorruption(RevisionStoreError):
    pass


@dataclass(frozen=True)
class _PublishedRevision:
    manifest: dict[str, Any]
    path: Path


StageHook = Callable[[str, dict[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_parent(path: Path) -> None:
    try:
        fd = os.open(str(Path(path).parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, AttributeError):
        pass


def _durable_write_new(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RevisionStoreError(f'HISTORY_IMMUTABLE_PATH_EXISTS:{path.name}')
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.publishing')
    try:
        with tmp.open('xb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_parent(path)
    finally:
        tmp.unlink(missing_ok=True)


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _hash_value(digest: 'hashlib._Hash', value: Any) -> None:
    if value is None:
        payload = b'N'
    elif isinstance(value, bool):
        payload = b'I1' if value else b'I0'
    elif isinstance(value, int):
        payload = b'I' + str(value).encode('ascii')
    elif isinstance(value, float):
        payload = b'F' + value.hex().encode('ascii')
    elif isinstance(value, (bytes, bytearray, memoryview)):
        payload = b'B' + bytes(value)
    else:
        payload = b'T' + str(value).encode('utf-8')
    digest.update(len(payload).to_bytes(8, 'big'))
    digest.update(payload)


def canonical_state_hash(database: Path, canonical_tables: Iterable[str], asset_hashes: dict[str, str]) -> str:
    """Hash canonical logical state, excluding derived History/UI implementation tables.

    The hash is intentionally independent of SQLite page layout and of the old History
    object store.  It binds table schemas + ordered row values + referenced finalized
    assets, so one canonical generation has one logical identity even if derived local
    metadata changes afterward.
    """
    database = Path(database)
    digest = hashlib.sha256()
    con = sqlite3.connect(f'file:{database.as_posix()}?mode=ro&immutable=1', uri=True)
    try:
        tables = {str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(set(canonical_tables)):
            if table not in tables:
                continue
            schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            digest.update(b'TABLE\0' + table.encode('utf-8') + b'\0')
            digest.update(str(schema[0] or '').encode('utf-8'))
            info = list(con.execute(f'PRAGMA table_info({_quoted(table)})'))
            columns = [str(row[1]) for row in info]
            for col in columns:
                digest.update(b'COL\0' + col.encode('utf-8') + b'\0')
            pk = [str(row[1]) for row in sorted((row for row in info if int(row[5] or 0) > 0), key=lambda row: int(row[5]))]
            order = ','.join(_quoted(col) for col in pk) if pk else 'rowid'
            select = ','.join(_quoted(col) for col in columns)
            for row in con.execute(f'SELECT {select} FROM {_quoted(table)} ORDER BY {order}'):
                digest.update(b'ROW\0')
                for value in row:
                    _hash_value(digest, value)
                digest.update(b'\0ENDROW\0')
    finally:
        con.close()
    digest.update(b'ASSETS\0')
    for rel, value in sorted((k, v) for k, v in asset_hashes.items() if k != 'project.sqlite3'):
        digest.update(rel.encode('utf-8') + b'\0' + str(value).encode('ascii') + b'\0')
    return digest.hexdigest()


def _read_project_identity(database: Path) -> tuple[int, int]:
    con = sqlite3.connect(f'file:{Path(database).as_posix()}?mode=ro', uri=True)
    try:
        cols = {str(r[1]) for r in con.execute('PRAGMA table_info(projects)')}
        if 'project_generation' not in cols:
            raise RevisionStoreError('HISTORY_PROJECT_GENERATION_MISSING')
        row = con.execute('SELECT id,project_generation FROM projects ORDER BY id LIMIT 1').fetchone()
        if row is None:
            raise RevisionStoreError('HISTORY_PROJECT_ROW_MISSING')
        project_id, generation = int(row[0]), int(row[1] or 0)
        if project_id < 1 or generation < 1:
            raise RevisionStoreError('HISTORY_PROJECT_IDENTITY_INVALID')
        return project_id, generation
    finally:
        con.close()


def _lock_for(root: Path) -> threading.RLock:
    key = str(Path(root).resolve())
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class LocalRevisionStore:
    """Append-only local Revision History repository for canonical project generations.

    7.7.3 deliberately stores a complete frozen canonical snapshot for each revision.
    This is boring by design: no deltas, deduplication, retention, deletion, or GC.
    Checkpoint manifests bound the integrity lineage even though v1 revisions are
    independently materializable. Future formats may optimize the payload without
    changing revision/generation semantics.
    """

    def __init__(self, root: Path, database: Path, asset_dir: Path, *, canonical_tables: Iterable[str], checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL, workspace_project_id: str | None = None):
        self.root = Path(root).resolve()
        self.database = Path(database).resolve()
        self.asset_dir = Path(asset_dir).resolve()
        self.workspace_project_id = str(workspace_project_id or '').strip()
        if not re.fullmatch(r'[0-9a-f]{32}', self.workspace_project_id):
            raise RevisionStoreCorruption('HISTORY_WORKSPACE_PROJECT_ID_REQUIRED')
        self.canonical_tables = tuple(sorted(set(str(x) for x in canonical_tables)))
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self.objects_dir = self.root / 'objects'
        self.revisions_dir = self.root / 'revisions'
        self.checkpoints_dir = self.root / 'checkpoints'
        self.staging_dir = self.root / 'staging'
        self._lock = _lock_for(self.root)
        self._ensure_dirs()
        self._ensure_store_contract()

    def _ensure_dirs(self) -> None:
        for path in (self.root, self.objects_dir, self.revisions_dir, self.checkpoints_dir, self.staging_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def store_metadata_path(self) -> Path:
        return self.root / STORE_METADATA_FILE

    def _store_contract_payload(self) -> dict[str, Any]:
        return {
            'store_format': STORE_FORMAT,
            'store_version': STORE_VERSION,
            'snapshot_version': SNAPSHOT_VERSION,
            'revision_version': REVISION_VERSION,
            'checkpoint_version': CHECKPOINT_VERSION,
            'checkpoint_interval': int(self.checkpoint_interval),
            'workspace_project_id': self.workspace_project_id,
        }

    def _validate_store_contract(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RevisionStoreCorruption('HISTORY_STORE_FORMAT_INVALID')
        if value.get('store_format') != STORE_FORMAT:
            raise RevisionStoreCorruption('HISTORY_STORE_FORMAT_INVALID')
        version = int(value.get('store_version') or 0)
        if version != STORE_VERSION:
            # 7.7.9 freezes v1. In particular, a future v2 repository must never be
            # opened for writes by an older Breadmaker that merely happens to
            # understand some of its filenames.
            raise RevisionStoreCorruption(f'HISTORY_STORE_VERSION_UNSUPPORTED:{version}')
        expected_versions = {
            'snapshot_version': SNAPSHOT_VERSION,
            'revision_version': REVISION_VERSION,
            'checkpoint_version': CHECKPOINT_VERSION,
        }
        for key, expected in expected_versions.items():
            if int(value.get(key) or 0) != int(expected):
                raise RevisionStoreCorruption(f'HISTORY_STORE_COMPONENT_VERSION_UNSUPPORTED:{key}')
        interval = int(value.get('checkpoint_interval') or 0)
        if interval < 1:
            raise RevisionStoreCorruption('HISTORY_STORE_CHECKPOINT_INTERVAL_INVALID')
        if interval != int(self.checkpoint_interval):
            raise RevisionStoreCorruption(
                f'HISTORY_STORE_CHECKPOINT_INTERVAL_MISMATCH:{interval}!={int(self.checkpoint_interval)}'
            )
        stored_workspace = str(value.get('workspace_project_id') or '').strip()
        if not re.fullmatch(r'[0-9a-f]{32}', stored_workspace):
            raise RevisionStoreCorruption('HISTORY_STORE_WORKSPACE_PROJECT_ID_INVALID')
        if stored_workspace != self.workspace_project_id:
            raise RevisionStoreCorruption('HISTORY_STORE_WORKSPACE_PROJECT_ID_MISMATCH')
        return dict(value)

    def _ensure_store_contract(self) -> None:
        """Persist and validate the frozen v1 local History repository contract.

        Every non-empty current repository must already carry this identity marker.
        Missing metadata beside immutable History bytes is unsupported/corrupt pre-alpha
        state and is never repaired or rewritten. The marker is also the downgrade/write
        fence for future incompatible repository versions.
        """
        path = self.store_metadata_path
        with self._lock:
            if path.exists():
                try:
                    value = json.loads(path.read_text(encoding='utf-8'))
                except Exception as exc:
                    raise RevisionStoreCorruption('HISTORY_STORE_METADATA_UNREADABLE') from exc
                self._validate_store_contract(value)
                return

            # 7.8 is current-format-only. A missing marker is valid only for a
            # genuinely empty repository being initialized now. Existing immutable
            # bytes without repository identity are unsupported pre-alpha data.
            if any(self.revisions_dir.iterdir()) or any(self.checkpoints_dir.iterdir()) or any(self.objects_dir.iterdir()):
                raise RevisionStoreCorruption('HISTORY_STORE_METADATA_MISSING_WITH_DATA')

            _durable_write_new(path, _canonical_json(self._store_contract_payload()))
            persisted = json.loads(path.read_text(encoding='utf-8'))
            self._validate_store_contract(persisted)

    def _stage(self, hook: StageHook | None, stage: str, context: dict[str, Any]) -> None:
        if hook is not None:
            hook(stage, dict(context))

    def _revision_path(self, generation: int, revision_id: str) -> Path:
        return self.revisions_dir / f'g{int(generation):020d}-{revision_id}.revision.json'

    def _checkpoint_path(self, generation: int, checkpoint_id: str) -> Path:
        return self.checkpoints_dir / f'g{int(generation):020d}-{checkpoint_id}.checkpoint.json'

    def _validated_workspace_project_id(self, value: Any) -> str:
        raw=value.get('workspace_project_id') if isinstance(value,dict) else None
        workspace_id=str(raw or '').strip()
        if not re.fullmatch(r'[0-9a-f]{32}',workspace_id):
            raise RevisionStoreCorruption('HISTORY_WORKSPACE_PROJECT_ID_REQUIRED')
        if workspace_id!=self.workspace_project_id:
            raise RevisionStoreCorruption('HISTORY_WORKSPACE_PROJECT_ID_MISMATCH')
        return workspace_id

    def _validate_revision_manifest(self, value: Any, path: Path | None = None) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RevisionStoreCorruption('HISTORY_REVISION_MANIFEST_NOT_OBJECT')
        if value.get('revision_format') != REVISION_FORMAT or int(value.get('revision_version') or 0) != REVISION_VERSION:
            raise RevisionStoreCorruption('HISTORY_REVISION_FORMAT_INVALID')
        rid = str(value.get('revision_id') or '')
        if not re.fullmatch(r'[0-9a-f]{32}', rid):
            raise RevisionStoreCorruption('HISTORY_REVISION_ID_INVALID')
        workspace_project_id = self._validated_workspace_project_id(value)
        project_id = int(value.get('project_id') or 0)
        start = int(value.get('generation_start') or 0)
        end = int(value.get('generation_end') or 0)
        if project_id < 1 or start < 1 or end < start:
            raise RevisionStoreCorruption('HISTORY_REVISION_GENERATION_INVALID')
        checkpoint_id = str(value.get('checkpoint_id') or '')
        snapshot_id = str(value.get('snapshot_object_id') or '')
        state_hash = str(value.get('expected_state_hash') or '')
        snapshot_hash = str(value.get('snapshot_manifest_sha256') or '')
        if not re.fullmatch(r'[0-9a-f]{32}', checkpoint_id) or not re.fullmatch(r'g\d{20}-[0-9a-f]{32}', snapshot_id):
            raise RevisionStoreCorruption('HISTORY_REVISION_REFERENCE_INVALID')
        if not re.fullmatch(r'[0-9a-f]{64}', state_hash) or not re.fullmatch(r'[0-9a-f]{64}', snapshot_hash):
            raise RevisionStoreCorruption('HISTORY_REVISION_HASH_INVALID')
        parent = value.get('parent_revision_id')
        if parent is not None and not re.fullmatch(r'[0-9a-f]{32}', str(parent)):
            raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_INVALID')
        distance = int(value.get('distance_from_checkpoint') or 0)
        checkpoint_generation = int(value.get('checkpoint_generation') or 0)
        if checkpoint_generation < 1 or checkpoint_generation > end:
            raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_GENERATION_INVALID')
        if distance < 0 or distance >= self.checkpoint_interval:
            raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_DISTANCE_INVALID')
        if path is not None:
            match = _REVISION_FILE.match(path.name)
            if not match or int(match.group('generation')) != end or match.group('id') != rid:
                raise RevisionStoreCorruption('HISTORY_REVISION_FILENAME_IDENTITY_MISMATCH')
        out = dict(value)
        out['project_id'] = project_id
        if workspace_project_id:
            out['workspace_project_id'] = workspace_project_id
        out['generation_start'] = start
        out['generation_end'] = end
        out['distance_from_checkpoint'] = distance
        out['checkpoint_generation'] = checkpoint_generation
        return out

    def _validate_checkpoint_manifest(self, value: Any, path: Path | None = None) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_MANIFEST_NOT_OBJECT')
        if value.get('checkpoint_format') != CHECKPOINT_FORMAT or int(value.get('checkpoint_version') or 0) != CHECKPOINT_VERSION:
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_FORMAT_INVALID')
        workspace_project_id = self._validated_workspace_project_id(value)
        cid = str(value.get('checkpoint_id') or '')
        anchor = str(value.get('anchor_revision_id') or '')
        snapshot_id = str(value.get('snapshot_object_id') or '')
        generation = int(value.get('project_generation') or 0)
        if not re.fullmatch(r'[0-9a-f]{32}', cid) or not re.fullmatch(r'[0-9a-f]{32}', anchor):
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ID_INVALID')
        if not re.fullmatch(r'g\d{20}-[0-9a-f]{32}', snapshot_id) or generation < 1:
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_REFERENCE_INVALID')
        for key in ('expected_state_hash', 'snapshot_manifest_sha256'):
            if not re.fullmatch(r'[0-9a-f]{64}', str(value.get(key) or '')):
                raise RevisionStoreCorruption('HISTORY_CHECKPOINT_HASH_INVALID')
        if path is not None:
            match = _CHECKPOINT_FILE.match(path.name)
            if not match or int(match.group('generation')) != generation or match.group('id') != cid:
                raise RevisionStoreCorruption('HISTORY_CHECKPOINT_FILENAME_IDENTITY_MISMATCH')
        out = dict(value)
        out['project_generation'] = generation
        out['project_id'] = int(value.get('project_id') or 0)
        if workspace_project_id:
            out['workspace_project_id'] = workspace_project_id
        return out

    def _load_json(self, path: Path, *, kind: str) -> dict[str, Any]:
        try:
            value = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception as exc:
            raise RevisionStoreCorruption(f'HISTORY_{kind}_MANIFEST_UNREADABLE:{path.name}') from exc
        return self._validate_revision_manifest(value, path) if kind == 'REVISION' else self._validate_checkpoint_manifest(value, path)

    def _published_revisions(self, *, fail_on_uncertain_head: bool = True) -> list[_PublishedRevision]:
        valid: list[_PublishedRevision] = []
        corrupt_generations: list[int] = []
        for path in self.revisions_dir.glob('*.revision.json'):
            match = _REVISION_FILE.match(path.name)
            hinted = int(match.group('generation')) if match else 0
            try:
                manifest = self._load_json(path, kind='REVISION')
                valid.append(_PublishedRevision(manifest, path))
            except RevisionStoreCorruption:
                corrupt_generations.append(hinted)
        by_generation: dict[int, list[_PublishedRevision]] = {}
        for row in valid:
            by_generation.setdefault(int(row.manifest['generation_end']), []).append(row)
        normalized: list[_PublishedRevision] = []
        for generation, rows in by_generation.items():
            # A valid local publisher is serialized and writes exactly one immutable
            # manifest per canonical generation.  Retries discover that manifest
            # before publishing.  Multiple manifests for one generation therefore
            # mean filesystem tampering/corruption, not a harmless race to resolve
            # by filename or mtime.  Fail closed instead of guessing an authority.
            if len(rows) > 1:
                raise RevisionStoreCorruption(f'HISTORY_DUPLICATE_GENERATION_PUBLICATION:{generation}')
            normalized.append(rows[0])
        normalized.sort(key=lambda r: (int(r.manifest['generation_end']), r.manifest['revision_id']))
        highest_valid = int(normalized[-1].manifest['generation_end']) if normalized else 0
        if fail_on_uncertain_head and any(g >= highest_valid for g in corrupt_generations):
            raise RevisionStoreCorruption('HISTORY_PUBLICATION_HEAD_UNCERTAIN')
        return normalized

    def list_revisions(self) -> list[dict[str, Any]]:
        rows = self._published_revisions(fail_on_uncertain_head=False)
        return [dict(row.manifest) for row in reversed(rows)]

    def _find_revision(self, revision_id: str) -> _PublishedRevision:
        revision_id=str(revision_id or '')
        # If the requested immutable file is physically present, validate it first.
        # _published_revisions() intentionally ignores malformed non-head objects while
        # discovering history, but a targeted verify must never turn corruption or a
        # transplanted workspace identity into the misleading result "not found".
        candidates=list(self.revisions_dir.glob(f'g*-{revision_id}.revision.json')) if re.fullmatch(r'[0-9a-f]{32}', revision_id) else []
        if len(candidates)>1:
            raise RevisionStoreCorruption('HISTORY_REVISION_ID_AMBIGUOUS')
        if candidates:
            self._load_json(candidates[0],kind='REVISION')
        matches = []
        for row in self._published_revisions(fail_on_uncertain_head=False):
            if row.manifest['revision_id'] == revision_id:
                matches.append(row)
        if len(matches) != 1:
            raise RevisionStoreError('HISTORY_REVISION_NOT_FOUND')
        return matches[0]

    def _find_checkpoint(self, checkpoint_id: str) -> tuple[dict[str, Any], Path]:
        matches = []
        for path in self.checkpoints_dir.glob('*.checkpoint.json'):
            try:
                row = self._load_json(path, kind='CHECKPOINT')
            except RevisionStoreCorruption:
                continue
            if row['checkpoint_id'] == checkpoint_id:
                matches.append((row, path))
        if len(matches) != 1:
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_NOT_FOUND_OR_AMBIGUOUS')
        return matches[0]

    def _snapshot_manifest(self, snapshot_id: str) -> tuple[dict[str, Any], str, Path]:
        root = (self.objects_dir / snapshot_id).resolve()
        try:
            root.relative_to(self.objects_dir.resolve())
        except ValueError as exc:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_PATH_ESCAPED') from exc
        manifest_path = root / 'snapshot_manifest.json'
        try:
            raw = manifest_path.read_bytes()
            value = json.loads(raw)
        except Exception as exc:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_MANIFEST_UNREADABLE') from exc
        if not isinstance(value, dict) or value.get('snapshot_format') != SNAPSHOT_FORMAT or int(value.get('snapshot_version') or 0) != SNAPSHOT_VERSION:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_FORMAT_INVALID')
        self._validated_workspace_project_id(value)
        digest = _sha256_bytes(_canonical_json(value))
        return value, digest, root

    def _verify_snapshot(self, snapshot_id: str, *, expected_project_id: int, expected_generation: int, expected_state_hash: str, expected_manifest_hash: str, expected_workspace_project_id: str = '') -> dict[str, Any]:
        manifest, manifest_hash, root = self._snapshot_manifest(snapshot_id)
        if manifest_hash != expected_manifest_hash:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_MANIFEST_HASH_MISMATCH')
        if int(manifest.get('project_id') or 0) != int(expected_project_id) or int(manifest.get('project_generation') or 0) != int(expected_generation):
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_GENERATION_MISMATCH')
        snapshot_workspace_id = str(manifest.get('workspace_project_id') or '')
        if expected_workspace_project_id and snapshot_workspace_id and snapshot_workspace_id != str(expected_workspace_project_id):
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_WORKSPACE_PROJECT_MISMATCH')
        if str(manifest.get('canonical_state_hash') or '') != expected_state_hash:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_STATE_HASH_MISMATCH')
        file_hashes = manifest.get('file_hashes')
        if not isinstance(file_hashes, dict) or 'project.sqlite3' not in file_hashes:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_FILE_HASHES_INVALID')
        actual_files = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'snapshot_manifest.json'}
        if actual_files != set(file_hashes):
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_FILE_SET_MISMATCH')
        for rel, expected in file_hashes.items():
            path = (root / rel).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise RevisionStoreCorruption('HISTORY_SNAPSHOT_FILE_ESCAPED') from exc
            if not path.is_file() or _sha256_file(path) != str(expected):
                raise RevisionStoreCorruption(f'HISTORY_SNAPSHOT_FILE_HASH_MISMATCH:{rel}')
        database = root / 'project.sqlite3'
        con = sqlite3.connect(f'file:{database.as_posix()}?mode=ro&immutable=1', uri=True)
        try:
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RevisionStoreCorruption('HISTORY_SNAPSHOT_DATABASE_CORRUPT')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise RevisionStoreCorruption('HISTORY_SNAPSHOT_FOREIGN_KEYS_INVALID')
        finally:
            con.close()
        logical = canonical_state_hash(database, self.canonical_tables, file_hashes)
        if logical != expected_state_hash:
            raise RevisionStoreCorruption('HISTORY_SNAPSHOT_CANONICAL_HASH_MISMATCH')
        return {'manifest': manifest, 'root': root, 'canonical_state_hash': logical}

    def _verify_lineage(self, revision: dict[str, Any]) -> dict[str, Any]:
        checkpoint, _ = self._find_checkpoint(str(revision['checkpoint_id']))
        revision_workspace_id = str(revision.get('workspace_project_id') or '')
        checkpoint_workspace_id = str(checkpoint.get('workspace_project_id') or '')
        if revision_workspace_id and checkpoint_workspace_id and revision_workspace_id != checkpoint_workspace_id:
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_WORKSPACE_PROJECT_MISMATCH')
        if int(checkpoint['project_id']) != int(revision['project_id']):
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_PROJECT_MISMATCH')
        if int(checkpoint['project_generation']) != int(revision['checkpoint_generation']):
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_GENERATION_MISMATCH')
        if int(checkpoint['project_generation']) > int(revision['generation_end']):
            raise RevisionStoreCorruption('HISTORY_CHECKPOINT_AFTER_REVISION')
        self._verify_snapshot(
            str(checkpoint['snapshot_object_id']),
            expected_project_id=int(checkpoint['project_id']),
            expected_generation=int(checkpoint['project_generation']),
            expected_state_hash=str(checkpoint['expected_state_hash']),
            expected_manifest_hash=str(checkpoint['snapshot_manifest_sha256']),
            expected_workspace_project_id=checkpoint_workspace_id,
        )
        anchor = str(checkpoint['anchor_revision_id'])
        current = revision
        seen: set[str] = set()
        steps = 0
        while True:
            rid = str(current['revision_id'])
            if rid in seen:
                raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_CYCLE')
            seen.add(rid)
            if int(current['project_id']) != int(revision['project_id']):
                raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_PROJECT_MISMATCH')
            current_workspace_id = str(current.get('workspace_project_id') or '')
            if revision_workspace_id and current_workspace_id and current_workspace_id != revision_workspace_id:
                raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_WORKSPACE_PROJECT_MISMATCH')
            if str(current['checkpoint_id']) != str(revision['checkpoint_id']):
                raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_CHAIN_BROKEN')
            if rid == anchor:
                if int(current['generation_end']) != int(checkpoint['project_generation']):
                    raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ANCHOR_GENERATION_MISMATCH')
                if int(current['distance_from_checkpoint']) != 0:
                    raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ANCHOR_DISTANCE_INVALID')
                if str(current['snapshot_object_id']) != str(checkpoint['snapshot_object_id']):
                    raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ANCHOR_SNAPSHOT_MISMATCH')
                if str(current['snapshot_manifest_sha256']) != str(checkpoint['snapshot_manifest_sha256']) or str(current['expected_state_hash']) != str(checkpoint['expected_state_hash']):
                    raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ANCHOR_HASH_MISMATCH')
                break
            parent_id = current.get('parent_revision_id')
            if not parent_id:
                raise RevisionStoreCorruption('HISTORY_CHECKPOINT_ANCHOR_UNREACHABLE')
            try:
                parent = self._find_revision(str(parent_id)).manifest
            except RevisionStoreError as exc:
                raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_MISSING') from exc
            if int(parent['generation_end']) >= int(current['generation_end']):
                raise RevisionStoreCorruption('HISTORY_REVISION_PARENT_ORDER_INVALID')
            if int(current['generation_start']) != int(parent['generation_end']) + 1:
                raise RevisionStoreCorruption('HISTORY_REVISION_GENERATION_RANGE_DISCONTINUITY')
            if int(parent['distance_from_checkpoint']) + 1 != int(current['distance_from_checkpoint']):
                raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_DISTANCE_DISCONTINUITY')
            current = parent
            steps += 1
            if steps >= self.checkpoint_interval:
                raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_CHAIN_TOO_LONG')
        if steps != int(revision['distance_from_checkpoint']):
            raise RevisionStoreCorruption('HISTORY_REVISION_CHECKPOINT_DISTANCE_MISMATCH')
        return checkpoint

    def verify_revision(self, revision_id: str) -> dict[str, Any]:
        row = self._find_revision(str(revision_id))
        revision = row.manifest
        self._verify_lineage(revision)
        snapshot = self._verify_snapshot(
            str(revision['snapshot_object_id']),
            expected_project_id=int(revision['project_id']),
            expected_generation=int(revision['generation_end']),
            expected_state_hash=str(revision['expected_state_hash']),
            expected_manifest_hash=str(revision['snapshot_manifest_sha256']),
            expected_workspace_project_id=str(revision.get('workspace_project_id') or ''),
        )
        return {'revision': dict(revision), **snapshot}

    def materialize_revision(self, revision_id: str, target_root: Path) -> dict[str, Any]:
        verified = self.verify_revision(revision_id)
        source = Path(verified['root'])
        target = Path(target_root).resolve()
        if target.exists():
            raise RevisionStoreError('HISTORY_MATERIALIZE_TARGET_EXISTS')
        tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.materializing')
        try:
            shutil.copytree(source, tmp)
            os.replace(tmp, target)
            _fsync_parent(target)
            revision = verified['revision']
            manifest_path = target / 'snapshot_manifest.json'
            local_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            file_hashes = dict(local_manifest['file_hashes'])
            logical = canonical_state_hash(target / 'project.sqlite3', self.canonical_tables, file_hashes)
            if logical != revision['expected_state_hash']:
                raise RevisionStoreCorruption('HISTORY_MATERIALIZED_STATE_HASH_MISMATCH')
            return {'root': target, 'database': target / 'project.sqlite3', 'revision': dict(revision), 'canonical_state_hash': logical}
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def _finalize_snapshot_object(self, frozen: FrozenCanonicalSnapshot, staging: Path, *, created_at: str) -> tuple[str, str, str]:
        # A frozen SQLite backup is complete in its main database file.  Never let
        # SQLite connection sidecars become part of immutable History identity.
        for suffix in ('-wal', '-shm', '-journal'):
            frozen.database.with_name(frozen.database.name + suffix).unlink(missing_ok=True)
        if _sha256_file(frozen.database) != str(frozen.file_hashes.get('project.sqlite3') or ''):
            raise RevisionStoreCorruption('HISTORY_FROZEN_DATABASE_CHANGED_BEFORE_PUBLICATION')
        state_hash = canonical_state_hash(frozen.database, self.canonical_tables, frozen.file_hashes)
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = frozen.database.with_name(frozen.database.name + suffix)
            if sidecar.exists():
                raise RevisionStoreCorruption('HISTORY_SNAPSHOT_SQLITE_SIDECAR_CREATED')
        snapshot_manifest = {
            'snapshot_format': SNAPSHOT_FORMAT,
            'snapshot_version': SNAPSHOT_VERSION,
            'project_id': int(frozen.project_id),
            'workspace_project_id': self.workspace_project_id,
            'project_generation': int(frozen.project_generation),
            'schema_version': int(frozen.schema_version),
            'created_at': created_at,
            'canonical_state_hash': state_hash,
            'project_fingerprint': str(frozen.project_fingerprint),
            'file_hashes': dict(frozen.file_hashes),
        }
        manifest_bytes = _canonical_json(snapshot_manifest)
        manifest_hash = _sha256_bytes(manifest_bytes)
        _durable_write_new(staging / 'snapshot_manifest.json', manifest_bytes)
        object_id = f'g{int(frozen.project_generation):020d}-{uuid.uuid4().hex}'
        final = self.objects_dir / object_id
        if final.exists():
            raise RevisionStoreError('HISTORY_SNAPSHOT_OBJECT_COLLISION')
        os.replace(staging, final)
        _fsync_parent(final)
        return object_id, manifest_hash, state_hash

    def capture(self, *, label: str = 'Automatic History', creation_reason: str = 'automatic', summary: dict[str, Any] | None = None, force_checkpoint: bool = False, expected_generation: int | None = None, stage_hook: StageHook | None = None) -> dict[str, Any]:
        """Capture and publish the current committed generation without mutating Current.

        Publication is manifest-last. A finalized snapshot object or checkpoint without
        a revision manifest is harmless orphan data and is never returned by list or
        materialization. Duplicate capture of an already-published generation is
        idempotent. A stale capture finishing after a newer revision is rejected.
        """
        project_id, live_generation = _read_project_identity(self.database)
        if expected_generation is not None and int(live_generation) != int(expected_generation):
            raise StaleRevisionPublication(f'HISTORY_CAPTURE_EXPECTED_GENERATION_STALE:{live_generation}!={int(expected_generation)}')
        with self._lock:
            rows = self._published_revisions()
            if rows:
                head = rows[-1].manifest
                head_workspace_id = str(head.get('workspace_project_id') or '')
                if self.workspace_project_id and head_workspace_id and head_workspace_id != self.workspace_project_id:
                    raise RevisionStoreCorruption('HISTORY_WORKSPACE_PROJECT_ID_MISMATCH')
                if int(head['project_id']) != project_id:
                    raise RevisionStoreCorruption('HISTORY_PROJECT_ID_MISMATCH')
                if int(head['generation_end']) == live_generation:
                    verified = self.verify_revision(str(head['revision_id']))
                    return {**verified['revision'], 'idempotent': True}
                if int(head['generation_end']) > live_generation:
                    raise RevisionStoreCorruption('HISTORY_HEAD_AHEAD_OF_CURRENT')

        capture_id = uuid.uuid4().hex
        staging = self.staging_dir / f'{capture_id}.capture-tmp'
        shutil.rmtree(staging, ignore_errors=True)
        created_at = _utc_now()
        try:
            frozen = capture_frozen_snapshot(self.database, self.asset_dir, staging)
            if expected_generation is not None and int(frozen.project_generation) != int(expected_generation):
                raise StaleRevisionPublication(f'HISTORY_CAPTURE_EXPECTED_GENERATION_STALE:{frozen.project_generation}!={int(expected_generation)}')
            if int(frozen.project_id) != project_id:
                raise RevisionStoreError('HISTORY_SNAPSHOT_PROJECT_CHANGED')
            object_id, snapshot_manifest_hash, state_hash = self._finalize_snapshot_object(frozen, staging, created_at=created_at)
            snapshot_context = {'project_id': project_id, 'generation': frozen.project_generation, 'snapshot_object_id': object_id}
            self._stage(stage_hook, 'SNAPSHOT_OBJECT_FINALIZED', snapshot_context)
            self._verify_snapshot(
                object_id,
                expected_project_id=int(frozen.project_id),
                expected_generation=int(frozen.project_generation),
                expected_state_hash=state_hash,
                expected_manifest_hash=snapshot_manifest_hash,
                expected_workspace_project_id=self.workspace_project_id,
            )
            self._stage(stage_hook, 'SNAPSHOT_VERIFIED', snapshot_context)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        generation = int(frozen.project_generation)
        revision_id = uuid.uuid4().hex
        with self._lock:
            rows = self._published_revisions()
            head = rows[-1].manifest if rows else None
            if head:
                head_workspace_id = str(head.get('workspace_project_id') or '')
                if self.workspace_project_id and head_workspace_id and head_workspace_id != self.workspace_project_id:
                    raise RevisionStoreCorruption('HISTORY_WORKSPACE_PROJECT_ID_MISMATCH')
            if head and int(head['project_id']) != project_id:
                raise RevisionStoreCorruption('HISTORY_PROJECT_ID_MISMATCH')
            if head and int(head['generation_end']) > generation:
                raise StaleRevisionPublication(f'HISTORY_STALE_PUBLICATION:{generation}<{head["generation_end"]}')
            if head and int(head['generation_end']) == generation:
                if str(head['expected_state_hash']) != state_hash:
                    raise RevisionStoreCorruption(f'HISTORY_GENERATION_IDENTITY_CONFLICT:{generation}')
                verified = self.verify_revision(str(head['revision_id']))
                return {**verified['revision'], 'idempotent': True}

            parent_id = str(head['revision_id']) if head else None
            parent_generation = int(head['generation_end']) if head else None
            generation_start = (parent_generation + 1) if parent_generation is not None else generation
            if generation_start > generation:
                raise RevisionStoreCorruption('HISTORY_GENERATION_RANGE_INVALID')

            new_checkpoint = bool(force_checkpoint or head is None or int(head.get('distance_from_checkpoint') or 0) + 1 >= self.checkpoint_interval)
            if new_checkpoint:
                checkpoint_id = uuid.uuid4().hex
                checkpoint_generation = generation
                distance = 0
                checkpoint_manifest = {
                    'checkpoint_format': CHECKPOINT_FORMAT,
                    'checkpoint_version': CHECKPOINT_VERSION,
                    'checkpoint_id': checkpoint_id,
                    'anchor_revision_id': revision_id,
                    'project_id': project_id,
                    'workspace_project_id': self.workspace_project_id,
                    'project_generation': generation,
                    'snapshot_object_id': object_id,
                    'snapshot_manifest_sha256': snapshot_manifest_hash,
                    'expected_state_hash': state_hash,
                    'created_at': created_at,
                }
                checkpoint_path = self._checkpoint_path(generation, checkpoint_id)
                self._stage(stage_hook, 'BEFORE_CHECKPOINT_PUBLICATION', {'project_id': project_id, 'generation': generation, 'checkpoint_id': checkpoint_id})
                _durable_write_new(checkpoint_path, _canonical_json(checkpoint_manifest))
                checkpoint_context = {'project_id': project_id, 'generation': generation, 'checkpoint_id': checkpoint_id}
                self._stage(stage_hook, 'CHECKPOINT_PUBLISHED', checkpoint_context)
                persisted_checkpoint = self._load_json(checkpoint_path, kind='CHECKPOINT')
                if _canonical_json(persisted_checkpoint) != _canonical_json(checkpoint_manifest):
                    raise RevisionStoreCorruption('HISTORY_CHECKPOINT_PUBLICATION_MISMATCH')
                self._stage(stage_hook, 'CHECKPOINT_VERIFIED', checkpoint_context)
            else:
                checkpoint_id = str(head['checkpoint_id'])
                checkpoint_generation = int(head['checkpoint_generation'])
                distance = int(head['distance_from_checkpoint']) + 1

            clean_summary = dict(summary or {})
            revision_manifest = {
                'revision_format': REVISION_FORMAT,
                'revision_version': REVISION_VERSION,
                'revision_id': revision_id,
                'project_id': project_id,
                'workspace_project_id': self.workspace_project_id,
                'generation_start': generation_start,
                'generation_end': generation,
                'parent_revision_id': parent_id,
                'checkpoint_id': checkpoint_id,
                'checkpoint_generation': checkpoint_generation,
                'distance_from_checkpoint': distance,
                'snapshot_object_id': object_id,
                'snapshot_manifest_sha256': snapshot_manifest_hash,
                'expected_state_hash': state_hash,
                'created_at': created_at,
                'creation_reason': str(creation_reason or 'automatic')[:80],
                'summary': {**clean_summary, 'label': str(label or 'Automatic History')[:180]},
            }
            revision_path = self._revision_path(generation, revision_id)
            self._stage(stage_hook, 'BEFORE_REVISION_PUBLICATION', {'project_id': project_id, 'generation': generation, 'revision_id': revision_id})
            _durable_write_new(revision_path, _canonical_json(revision_manifest))
            # The manifest write is the commit point. Re-read the complete published
            # lineage before reporting success so a successful return always means
            # the new History state is reconstructable from bytes currently on disk.
            verified = self.verify_revision(revision_id)
            self._stage(stage_hook, 'REVISION_PUBLISHED', {'project_id': project_id, 'generation': generation, 'revision_id': revision_id})
            return {**verified['revision'], 'idempotent': False}
