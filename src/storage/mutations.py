from __future__ import annotations

"""Canonical mutation transactions and project-generation CAS coordination."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from storage import context as storage_context
from storage import database as storage_database
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

_CANONICAL_COMMIT_LISTENER_LOCK = threading.Lock()


_CANONICAL_COMMIT_LISTENERS: list = []


class StaleGenerationError(RuntimeError):
    """A delayed operation tried to commit against a generation that is no longer Current."""


@dataclass(frozen=True)
class MutationToken:
    # project_id is the SQLite-local FK row identity. It is NOT globally unique.
    project_id: int
    source_generation: int
    operation: str
    expected_authoritative_generation: int
    workspace_project_id: str = ''
    project_root: str = ''


def _generation_supported(con: sqlite3.Connection) -> bool:
    try:
        cols={str(r[1]) for r in con.execute("PRAGMA table_info(projects)")}
        return 'project_generation' in cols
    except sqlite3.Error:
        return False


def project_generation(con: sqlite3.Connection, project_id: int | None = None) -> int:
    if not _generation_supported(con):
        return 0
    if project_id is None:
        row=con.execute("SELECT project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
    else:
        row=con.execute("SELECT project_generation FROM projects WHERE id=?",(int(project_id),)).fetchone()
    if not row:
        raise RuntimeError('PROJECT_GENERATION_PROJECT_NOT_FOUND')
    value=int(row[0] or 0)
    if value < 0:
        raise RuntimeError('PROJECT_GENERATION_INVALID')
    return value


def current_project_generation() -> int:
    with storage_context._project_db_lock():
        con=storage_database.connect()
        try:return project_generation(con)
        finally:con.close()


def configured_project_identity() -> tuple[str,str]:
    """Return the immutable workspace UUID + resolved root for configured Current.

    SQLite projects.id is local to each portable database and is commonly ``1``.
    Async/CAS authority must therefore also bind the workspace manifest UUID and
    project root; otherwise two different books at the same generation can look
    identical to the mutation coordinator.
    """
    root=storage_context.current_project_root()
    if root is None:
        raise RuntimeError('PROJECT_IDENTITY_NOT_CONFIGURED')
    root=Path(root).resolve()
    path=root/'manifest.json'
    try:
        manifest=json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError('PROJECT_IDENTITY_MANIFEST_INVALID') from exc
    workspace_id=storage_context.current_workspace_project_id() or str(manifest.get('project_id') or '').strip()
    if not workspace_id:
        raise RuntimeError('PROJECT_IDENTITY_MISSING')
    return workspace_id,str(root)


def capture_mutation_token(operation: str) -> MutationToken:
    """Capture a CAS token before asynchronous/prepared work begins.

    A caller that later wants to mutate Current passes this token back to tx(). If
    another canonical commit happened in between, the stale operation is rejected
    before it can write anything.
    """
    with storage_context._project_db_lock():
        con=storage_database.connect()
        try:
            row=con.execute("SELECT id,project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
            if not row:raise RuntimeError('PROJECT_GENERATION_PROJECT_NOT_FOUND')
            generation=int(row['project_generation'] or 0)
            workspace_id,project_root=configured_project_identity()
            return MutationToken(
                int(row['id']),generation,str(operation or 'mutation')[:120],generation,
                workspace_project_id=workspace_id,project_root=project_root,
            )
        finally:con.close()


def _install_generation_tracking(con: sqlite3.Connection) -> bool:
    if not _generation_supported(con):
        return False
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _breadmaker_generation_dirty(id INTEGER PRIMARY KEY CHECK(id=1), dirty INTEGER NOT NULL DEFAULT 0 CHECK(dirty IN (0,1)))")
    con.execute("INSERT OR IGNORE INTO _breadmaker_generation_dirty(id,dirty) VALUES(1,0)")
    con.execute("UPDATE _breadmaker_generation_dirty SET dirty=0 WHERE id=1")
    tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in sorted(storage_schema_contracts._GENERATION_CANONICAL_TABLES & tables):
        safe=''.join(c if c.isalnum() else '_' for c in table)
        for action in ('INSERT','UPDATE','DELETE'):
            name=f'_bm_generation_{safe}_{action.lower()}'
            sql=(f'CREATE TEMP TRIGGER IF NOT EXISTS "{name}" AFTER {action} ON main."{table}" '
                 'BEGIN UPDATE _breadmaker_generation_dirty SET dirty=1 WHERE id=1; END')
            con.execute(sql)
    return True


def _generation_dirty(con: sqlite3.Connection) -> bool:
    try:
        row=con.execute("SELECT dirty FROM _breadmaker_generation_dirty WHERE id=1").fetchone()
        return bool(row and int(row[0] or 0))
    except sqlite3.Error:return False


def _advance_project_generation(con: sqlite3.Connection) -> int | None:
    if not _generation_dirty(con):
        return None
    row=con.execute("SELECT id,project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
    if not row:raise RuntimeError('PROJECT_GENERATION_PROJECT_NOT_FOUND')
    pid=int(row['id']); current=int(row['project_generation'] or 0); target=current+1
    cur=con.execute("UPDATE projects SET project_generation=?,updated_at=? WHERE id=? AND project_generation=?",(target,storage_values.now(),pid,current))
    if cur.rowcount!=1:
        raise StaleGenerationError('PROJECT_GENERATION_CAS_FAILED')
    return target


def register_canonical_commit_listener(listener) -> None:
    """Register a non-blocking observer of successful canonical commits.

    Listeners are notifications only: they run after SQLite COMMIT and their failures
    are ignored. They must enqueue work rather than perform History/provider I/O.
    """
    with _CANONICAL_COMMIT_LISTENER_LOCK:
        if listener not in _CANONICAL_COMMIT_LISTENERS:_CANONICAL_COMMIT_LISTENERS.append(listener)


def _notify_canonical_commit(event:dict) -> None:
    with _CANONICAL_COMMIT_LISTENER_LOCK:listeners=list(_CANONICAL_COMMIT_LISTENERS)
    for listener in listeners:
        try:listener(dict(event))
        except Exception:pass


class ProjectMutationCoordinator:
    """Serialized commit boundary per request-bound project, not per process."""

    @contextmanager
    def transaction(self, *, expected_generation: int | None = None, token: MutationToken | None = None, operation: str='transaction', track_generation: bool = True):
        commit_event=None
        with storage_context._project_db_lock():
            con=storage_database.connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                tracked=_install_generation_tracking(con) if track_generation else False
                row=con.execute("SELECT id,project_generation FROM projects ORDER BY id LIMIT 1").fetchone() if tracked else None
                if token is not None:
                    if row is None or int(row['id'])!=int(token.project_id):
                        raise StaleGenerationError('PROJECT_GENERATION_PROJECT_CHANGED')
                    current_workspace_id,current_root=configured_project_identity()
                    if token.workspace_project_id and current_workspace_id!=str(token.workspace_project_id):
                        raise StaleGenerationError('PROJECT_GENERATION_PROJECT_CHANGED')
                    if token.project_root and current_root!=str(token.project_root):
                        raise StaleGenerationError('PROJECT_GENERATION_PROJECT_CHANGED')
                    expected=int(token.expected_authoritative_generation)
                    if int(row['project_generation'] or 0)!=expected:
                        raise StaleGenerationError('PROJECT_GENERATION_STALE_OPERATION')
                elif expected_generation is not None and row is not None:
                    if int(row['project_generation'] or 0)!=int(expected_generation):
                        raise StaleGenerationError('PROJECT_GENERATION_STALE_OPERATION')
                yield con
                target=_advance_project_generation(con) if track_generation else None
                con.commit()
                if target is not None:
                    try:
                        workspace_id,project_root=configured_project_identity()
                    except Exception:
                        current_root=storage_context.current_project_root()
                        workspace_id,project_root='',str(current_root.resolve()) if current_root is not None else ''
                    commit_event={'workspace_project_id':workspace_id,'project_root':project_root,'project_id':int(row['id']) if row is not None else 0,'generation':int(target),'operation':str(operation or 'transaction')}
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()
        # Never make Current wait for History/provider work. Observers are required to
        # perform only an in-memory enqueue and cannot affect the already committed DB.
        if commit_event is not None:_notify_canonical_commit(commit_event)


_MUTATION_COORDINATOR=ProjectMutationCoordinator()


@contextmanager
def tx(*, expected_generation: int | None = None, token: MutationToken | None = None, operation: str='transaction', track_generation: bool = True):
    with _MUTATION_COORDINATOR.transaction(expected_generation=expected_generation,token=token,operation=operation,track_generation=track_generation) as con:
        yield con


@contextmanager
def preview_tx():
    """Run an exact Current-state mutation preview and always roll it back.

    Checkpoint 5.0 uses this to execute the real deterministic restore path before
    asking the writer to confirm losses/renames.  Keeping preview and apply on the
    same code path prevents a second, subtly different simulation implementation.
    """
    with storage_context._project_db_lock():
        con=storage_database.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
        finally:
            con.rollback(); con.close()
