from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .local_store import RevisionStoreCorruption, RevisionStoreError, StaleRevisionPublication

_RESTORE_RECEIPT_KEY = 'history_restore_receipt_v1'
_REVISION_ID = re.compile(r'^[0-9a-f]{32}$')
_HASH_CHUNK_BYTES = 1024 * 1024

# These are deliberately the same writer-owned tables as Breadmaker's established
# History boundary. Independent writer safety domains (Checkpoints, Cuts, Recovery
# metadata, UI state, and project/workspace identity) are excluded.
_RESTORE_TABLES = (
    'document_tabs',
    'chapters',
    'scenes',
    'bookmarks',
    'markup_labels',
    'icon_markers',
    'manuscript_blocks',
    'comment_threads',
    'annotations',
    'comment_messages',
    'tasks',
    'task_milestones',
    'canvases',
    'canvas_nodes',
    'canvas_edges',
    'canvas_versions',
    'canvas_version_items',
    'story_maps',
    'map_lines',
    'map_items',
    'map_junctions',
    'references_map',
)

# Child-first deletion avoids relying on cascading side effects for correctness.
_DELETE_ORDER = (
    'references_map',
    'map_junctions',
    'map_items',
    'map_lines',
    'story_maps',
    'canvas_version_items',
    'canvas_versions',
    'canvas_edges',
    'canvas_nodes',
    'canvases',
    'task_milestones',
    'tasks',
    'annotations',
    'comment_messages',
    'comment_threads',
    'bookmarks',
    'manuscript_blocks',
    'scenes',
    'chapters',
    'document_tabs',
    'icon_markers',
    'markup_labels',
)

# Parent-first insertion. Foreign keys are also explicitly deferred so self-parented
# document/canvas rows can be copied exactly regardless of SQLite's row scan order.
_INSERT_ORDER = (
    'markup_labels',
    'icon_markers',
    'document_tabs',
    'chapters',
    'scenes',
    'manuscript_blocks',
    'bookmarks',
    'comment_threads',
    'comment_messages',
    'annotations',
    'tasks',
    'task_milestones',
    'canvases',
    'canvas_nodes',
    'canvas_edges',
    'canvas_versions',
    'canvas_version_items',
    'story_maps',
    'map_lines',
    'map_items',
    'map_junctions',
    'references_map',
)


class HistoryRestoreError(RuntimeError):
    code = 'HISTORY_RESTORE_FAILED'

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)


class HistoryRestoreStale(HistoryRestoreError):
    code = 'HISTORY_RESTORE_STALE'


class HistoryRestoreCorruption(HistoryRestoreError):
    code = 'HISTORY_RESTORE_CORRUPT'


@dataclass(frozen=True)
class HistoryRestoreResult:
    revision_id: str
    source_revision_generation: int
    previous_generation: int
    current_generation: int
    idempotent: bool
    recovery_backup: str | None
    safety_revision_id: str | None
    restored_history_revision_id: str | None
    history_landmark_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'ok': True,
            'revision_id': self.revision_id,
            'source_revision_generation': self.source_revision_generation,
            'previous_generation': self.previous_generation,
            'current_generation': self.current_generation,
            'idempotent': self.idempotent,
            'recovery_backup': self.recovery_backup,
            'safety_revision_id': self.safety_revision_id,
            'restored_history_revision_id': self.restored_history_revision_id,
            'history_landmark_error': self.history_landmark_error,
        }


def _q(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _table_columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [str(row[1]) for row in con.execute(f'PRAGMA {schema}.table_info({_q(table)})')]


def _read_receipt(con: sqlite3.Connection) -> dict[str, Any] | None:
    row = con.execute('SELECT value FROM storage_meta WHERE key=?', (_RESTORE_RECEIPT_KEY,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(str(row[0]))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _write_receipt(
    con: sqlite3.Connection,
    *,
    revision_id: str,
    source_revision_generation: int,
    previous_generation: int,
    current_generation: int,
    restored_at: str,
) -> None:
    payload = json.dumps(
        {
            'receipt_version': 1,
            'revision_id': revision_id,
            'source_revision_generation': int(source_revision_generation),
            'previous_generation': int(previous_generation),
            'current_generation': int(current_generation),
            'restored_at': str(restored_at),
        },
        sort_keys=True,
        separators=(',', ':'),
    )
    con.execute(
        'INSERT INTO storage_meta(key,value) VALUES(?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (_RESTORE_RECEIPT_KEY, payload),
    )


def _idempotent_receipt(revision_id: str, expected_generation: int | None) -> HistoryRestoreResult | None:
    from storage import assets as storage_assets
    from storage import context as storage_context
    from storage import history as storage_history
    from storage import invariants as storage_invariants
    from storage import mutations as storage_mutations
    from storage import schema_contracts as storage_schema_contracts
    from storage import values as storage_values
    with storage_mutations.tx(operation='history_restore_receipt_read', track_generation=False) as con:
        current = int(storage_mutations.project_generation(con))
        receipt = _read_receipt(con)
    if not receipt or str(receipt.get('revision_id') or '') != revision_id:
        return None
    try:
        previous = int(receipt.get('previous_generation'))
        result_generation = int(receipt.get('current_generation'))
        source_revision_generation = int(receipt.get('source_revision_generation'))
    except (TypeError, ValueError):
        return None
    if result_generation != current:
        return None
    if expected_generation is not None and previous != int(expected_generation):
        return None
    return HistoryRestoreResult(
        revision_id=revision_id,
        source_revision_generation=source_revision_generation,
        previous_generation=previous,
        current_generation=result_generation,
        idempotent=True,
        recovery_backup=None,
        safety_revision_id=None,
        restored_history_revision_id=None,
    )


def _validate_materialized_database(database: Path, project_id: int) -> None:
    from storage import assets as storage_assets
    from storage import context as storage_context
    from storage import history as storage_history
    from storage import invariants as storage_invariants
    from storage import mutations as storage_mutations
    from storage import schema_contracts as storage_schema_contracts
    from storage import values as storage_values
    con = sqlite3.connect(f'file:{Path(database).as_posix()}?mode=ro&immutable=1', uri=True)
    con.row_factory = sqlite3.Row
    try:
        if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise HistoryRestoreCorruption('HISTORY_RESTORE_DATABASE_CORRUPT')
        if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_DATABASE_FOREIGN_KEYS_INVALID')
        row = con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()
        if not row or int(row[0]) != int(project_id):
            raise HistoryRestoreCorruption('HISTORY_RESTORE_PROJECT_ID_MISMATCH')
        classification = storage_schema_contracts.history_schema_classification(con)
        if classification['unclassified']:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_SOURCE_SCHEMA_UNCLASSIFIED')
        missing = [table for table in _RESTORE_TABLES if table not in classification['managed']]
        if missing:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_SOURCE_SCHEMA_INCOMPLETE:' + ','.join(missing))
    finally:
        con.close()


def _publish_materialized_assets(root: Path, *, asset_dir: Path) -> None:
    """Publish referenced historical assets before their DB references can become Current.

    Existing asset paths are immutable identities. A differing existing file is never
    overwritten: uncertainty fails the restore while Current still references the old
    bytes. Missing files are atomically published through Breadmaker's asset writer.
    """
    from storage import assets as storage_assets
    from storage import context as storage_context
    from storage import history as storage_history
    from storage import invariants as storage_invariants
    from storage import mutations as storage_mutations
    from storage import schema_contracts as storage_schema_contracts
    from storage import values as storage_values
    manifest_path = Path(root) / 'snapshot_manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        hashes = dict(manifest['file_hashes'])
    except Exception as exc:
        raise HistoryRestoreCorruption('HISTORY_RESTORE_SNAPSHOT_MANIFEST_INVALID') from exc
    asset_root = (Path(root) / 'assets').resolve()
    destination_root=Path(asset_dir).resolve()
    for rel, expected in sorted(hashes.items()):
        if not str(rel).startswith('assets/'):
            continue
        relative = Path(str(rel)).relative_to('assets')
        source = (asset_root / relative).resolve()
        destination = (destination_root / relative).resolve()
        try:
            source.relative_to(asset_root)
            destination.relative_to(destination_root)
        except ValueError as exc:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_ASSET_PATH_ESCAPED') from exc
        if not source.is_file() or _sha256_file(source) != str(expected):
            raise HistoryRestoreCorruption(f'HISTORY_RESTORE_ASSET_SOURCE_INVALID:{relative.as_posix()}')
        if destination.exists():
            if not destination.is_file() or _sha256_file(destination) != str(expected):
                raise HistoryRestoreCorruption(f'HISTORY_RESTORE_ASSET_IDENTITY_CONFLICT:{relative.as_posix()}')
            continue
        # The existing project writer provides temp+fsync+atomic-rename semantics and
        # rejects a conflicting target if one appears concurrently.
        storage_assets.durable_write_project_asset(destination, source.read_bytes(), allow_existing_same=True)
        if _sha256_file(destination) != str(expected):
            raise HistoryRestoreCorruption(f'HISTORY_RESTORE_ASSET_PUBLISH_VERIFY_FAILED:{relative.as_posix()}')


def _activate_materialized_database(
    database: Path,
    *,
    token: Any,
    revision_id: str,
    source_revision_generation: int,
    stage_hook: Callable[[str], None] | None = None,
) -> int:
    from storage import assets as storage_assets
    from storage import context as storage_context
    from storage import history as storage_history
    from storage import invariants as storage_invariants
    from storage import mutations as storage_mutations
    from storage import schema_contracts as storage_schema_contracts
    from storage import values as storage_values
    expected_result_generation = int(token.expected_authoritative_generation) + 1
    source_uri = str(Path(database).resolve())
    with storage_mutations.tx(token=token, operation='history_restore_activate') as con:
        if stage_hook:
            stage_hook('ACTIVATION_TRANSACTION_OPEN')
        con.execute('PRAGMA defer_foreign_keys=ON')
        con.execute('ATTACH DATABASE ? AS history_src', (source_uri,))

        # Refuse a schema mismatch rather than silently dropping a future column.
        for table in _RESTORE_TABLES:
            live_cols = _table_columns(con, 'main', table)
            source_cols = _table_columns(con, 'history_src', table)
            if not live_cols or live_cols != source_cols:
                raise HistoryRestoreCorruption(f'HISTORY_RESTORE_TABLE_SCHEMA_MISMATCH:{table}')

        # Cuts are an independent writer safety domain. Deleting historical Chapters can
        # legitimately invoke Cuts.source_chapter_id ON DELETE SET NULL. Preserve the
        # current locator and restore it only when the same Chapter identity exists in
        # the selected state; the Cut itself and its preserved prose are never rewound.
        cut_locators = [
            (int(row[0]), int(row[1]))
            for row in con.execute('SELECT id,source_chapter_id FROM cuts WHERE source_chapter_id IS NOT NULL')
        ]

        for table in _DELETE_ORDER:
            con.execute(f'DELETE FROM main.{_q(table)}')
        if stage_hook:
            stage_hook('CURRENT_WRITER_STATE_CLEARED')

        for table in _INSERT_ORDER:
            cols = _table_columns(con, 'main', table)
            col_sql = ','.join(_q(col) for col in cols)
            con.execute(
                f'INSERT INTO main.{_q(table)} ({col_sql}) '
                f'SELECT {col_sql} FROM history_src.{_q(table)}'
            )

        for cut_id, chapter_id in cut_locators:
            exists = con.execute('SELECT 1 FROM chapters WHERE id=? LIMIT 1', (chapter_id,)).fetchone()
            if exists:
                con.execute('UPDATE cuts SET source_chapter_id=? WHERE id=?', (chapter_id, cut_id))

        con.execute('UPDATE projects SET updated_at=? WHERE id=?', (storage_values.now(), int(token.project_id)))

        # INSERT ... SELECT should be exact, but verify the activated writer-owned
        # state before commit rather than trusting that assumption. Every restored
        # table has a primary key, so bidirectional EXCEPT is an exact logical check.
        for table in _RESTORE_TABLES:
            cols = _table_columns(con, 'main', table)
            col_sql = ','.join(_q(col) for col in cols)
            live_extra = con.execute(
                f'SELECT {col_sql} FROM main.{_q(table)} '
                f'EXCEPT SELECT {col_sql} FROM history_src.{_q(table)} LIMIT 1'
            ).fetchone()
            source_extra = con.execute(
                f'SELECT {col_sql} FROM history_src.{_q(table)} '
                f'EXCEPT SELECT {col_sql} FROM main.{_q(table)} LIMIT 1'
            ).fetchone()
            if live_extra is not None or source_extra is not None:
                raise HistoryRestoreCorruption(f'HISTORY_RESTORE_ACTIVATION_MISMATCH:{table}')

        fk_error = con.execute('PRAGMA foreign_key_check').fetchone()
        if fk_error is not None:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_FOREIGN_KEY_CHECK_FAILED:' + ':'.join(str(x) for x in fk_error))
        storage_invariants.validate_project_invariants(con, int(token.project_id))

        _write_receipt(
            con,
            revision_id=revision_id,
            source_revision_generation=int(source_revision_generation),
            previous_generation=int(token.expected_authoritative_generation),
            current_generation=expected_result_generation,
            restored_at=storage_values.now(),
        )
        if stage_hook:
            stage_hook('BEFORE_ACTIVATION_COMMIT')

    if stage_hook:
        stage_hook('ACTIVATION_COMMITTED')
    return expected_result_generation


def restore_local_revision(
    revision_id: str,
    *,
    expected_generation: int | None,
    verified_backup: Callable[[], Path | None],
    label: str,
    stage_hook: Callable[[str], None] | None = None,
    project_root: Path | None = None,
    mutation_token: Any | None = None,
) -> HistoryRestoreResult:
    """Safely restore an immutable local History state as one new forward generation.

    The selected revision is reconstructed and verified outside Current. Before the
    sole activation transaction, Breadmaker creates an independently verified recovery
    package and a local History safety revision for the exact generation being replaced.
    The activation transaction uses the 7.7.0 generation CAS token, so stale dialogs or
    delayed work cannot overwrite newer edits. A durable receipt makes timeout-after-
    commit retries idempotent while refusing the same stale request after later edits.
    """
    from storage import assets as storage_assets
    from storage import context as storage_context
    from storage import history as storage_history
    from storage import invariants as storage_invariants
    from storage import mutations as storage_mutations
    from storage import schema_contracts as storage_schema_contracts
    from storage import values as storage_values
    revision_id = str(revision_id or '').strip().lower()
    if not _REVISION_ID.fullmatch(revision_id):
        raise HistoryRestoreError('HISTORY_RESTORE_REVISION_ID_INVALID')

    completed = _idempotent_receipt(revision_id, expected_generation)
    if completed is not None:
        return completed

    token = mutation_token or storage_mutations.capture_mutation_token('history_restore')
    if expected_generation is not None and int(expected_generation) != int(token.source_generation):
        raise HistoryRestoreStale('HISTORY_RESTORE_CURRENT_CHANGED')

    current_root=storage_context.current_project_root()
    bound_root=Path(project_root).resolve() if project_root is not None else (Path(current_root).resolve() if current_root is not None else None)
    if bound_root is None:
        raise HistoryRestoreError('HISTORY_RESTORE_PROJECT_NOT_CONFIGURED')
    store = storage_history.local_revision_store_for(bound_root)
    try:
        verified = store.verify_revision(revision_id)
    except (RevisionStoreError, RevisionStoreCorruption) as exc:
        raise HistoryRestoreCorruption(str(exc)) from exc
    selected = dict(verified['revision'])
    selected_workspace_id = str(selected.get('workspace_project_id') or '')
    if selected_workspace_id and selected_workspace_id != str(getattr(token, 'workspace_project_id', '') or ''):
        raise HistoryRestoreCorruption('HISTORY_RESTORE_WORKSPACE_PROJECT_ID_MISMATCH')
    if int(selected['project_id']) != int(token.project_id):
        raise HistoryRestoreCorruption('HISTORY_RESTORE_PROJECT_ID_MISMATCH')
    if int(selected['generation_end']) > int(token.source_generation):
        raise HistoryRestoreCorruption('HISTORY_RESTORE_REVISION_FROM_FUTURE')

    safety_revision_id: str | None = None
    restored_history_revision_id: str | None = None
    history_landmark_error: str | None = None
    backup_path: Path | None = None

    with tempfile.TemporaryDirectory(prefix='breadmaker-history-restore-') as td:
        target = Path(td) / 'materialized'
        try:
            materialized = store.materialize_revision(revision_id, target)
        except (RevisionStoreError, RevisionStoreCorruption) as exc:
            raise HistoryRestoreCorruption(str(exc)) from exc
        _validate_materialized_database(Path(materialized['database']), int(token.project_id))
        if stage_hook:
            stage_hook('MATERIALIZED_VERIFIED')

        backup_path = verified_backup()
        if backup_path is None:
            raise HistoryRestoreError('HISTORY_RESTORE_RECOVERY_BACKUP_MISSING')
        if stage_hook:
            stage_hook('RECOVERY_BACKUP_VERIFIED')

        try:
            safety = store.capture(
                label='Before restoring History',
                creation_reason='pre_restore_safety',
                summary={
                    **storage_history.history_current_summary_for(bound_root),
                    'restoring_revision_id': revision_id,
                    'restoring_label': str(label or 'History')[:180],
                },
                force_checkpoint=True,
                expected_generation=int(token.source_generation),
            )
            safety_revision_id = str(safety.get('revision_id') or '') or None
        except StaleRevisionPublication as exc:
            raise HistoryRestoreStale('HISTORY_RESTORE_CURRENT_CHANGED') from exc
        except (RevisionStoreError, RevisionStoreCorruption) as exc:
            raise HistoryRestoreCorruption('HISTORY_RESTORE_SAFETY_REVISION_FAILED:' + str(exc)) from exc
        if stage_hook:
            stage_hook('SAFETY_REVISION_VERIFIED')

        # Publishing a missing immutable asset is safe before activation; orphan bytes
        # after a crash are acceptable, while a DB reference to missing bytes is not.
        # It deliberately happens after both independent pre-restore safety roots exist.
        _publish_materialized_assets(Path(materialized['root']),asset_dir=bound_root/'assets')
        if stage_hook:
            stage_hook('ASSETS_VERIFIED')

        try:
            result_generation = _activate_materialized_database(
                Path(materialized['database']),
                token=token,
                revision_id=revision_id,
                source_revision_generation=int(selected['generation_end']),
                stage_hook=stage_hook,
            )
        except storage_mutations.StaleGenerationError as exc:
            raise HistoryRestoreStale('HISTORY_RESTORE_CURRENT_CHANGED') from exc

    # Current is authoritative from this point forward. Failure to add the optional
    # writer-facing landmark must never make a successful restore appear rolled back.
    try:
        landmark = store.capture(
            label=('Restored History: ' + str(label or 'History'))[:180],
            creation_reason='restore',
            summary={
                **storage_history.history_current_summary_for(bound_root),
                'restored_from_revision_id': revision_id,
                'restored_from_generation': int(selected['generation_end']),
            },
            force_checkpoint=False,
            expected_generation=int(result_generation),
        )
        restored_history_revision_id = str(landmark.get('revision_id') or '') or None
    except Exception as exc:  # post-commit observability only; Current is already safe
        history_landmark_error = str(exc)

    return HistoryRestoreResult(
        revision_id=revision_id,
        source_revision_generation=int(selected['generation_end']),
        previous_generation=int(token.source_generation),
        current_generation=int(result_generation),
        idempotent=False,
        recovery_backup=str(backup_path) if backup_path else None,
        safety_revision_id=safety_revision_id,
        restored_history_revision_id=restored_history_revision_id,
        history_landmark_error=history_landmark_error,
    )
