from __future__ import annotations

"""SQLite connection lifecycle, instance locking, and schema initialization."""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from storage import context as storage_context
from storage import invariants as storage_invariants
from storage.schema import SCHEMA_VERSION, SCHEMA_SQL
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

class UnsupportedProjectVersion(RuntimeError):
    """The project belongs to an intentionally unsupported pre-alpha storage format."""


_INSTANCE_LOCKS_GUARD = threading.Lock()


_INSTANCE_LOCK_HANDLES: dict[str, object] = {}


def ensure_dirs():
    for p in (storage_context.current_data_dir(), storage_context.current_backup_dir(), storage_context.current_asset_dir()):
        p.mkdir(parents=True, exist_ok=True)


def _unlock_handle(handle):
    if handle is None:
        return
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try: handle.close()
    except Exception: pass


def acquire_instance_lock():
    """Acquire and retain the OS lock for the request-bound/local project.

    A Breadmaker process may storage_values.now serve requests bound to more than one local project,
    so opening Project B must not release Project A's process lock. Locks remain held
    until that project is deleted or the process shuts down.
    """
    root=storage_context.current_project_root();db=storage_context.current_db_path()
    if root is None:
        raise RuntimeError('PROJECT_LOCK_CONTEXT_REQUIRED')
    root=Path(root).resolve()
    if db.is_file():
        version=storage_context.inspect_project_schema(root)
        if version!=SCHEMA_VERSION:raise UnsupportedProjectVersion(f'PREALPHA_PROJECT_INCOMPATIBLE:{version}')
    ensure_dirs()
    lock_path=(root / "breadmaker_editor.lock").resolve()
    key=str(lock_path)
    with _INSTANCE_LOCKS_GUARD:
        if key in _INSTANCE_LOCK_HANDLES:
            return False
        handle=lock_path.open("a+")
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError("Another Breadmaker Editor process is already using this project folder.")
        except ImportError:
            # Breadmaker currently targets macOS/Linux for the explicit process lock.
            pass
        handle.seek(0); handle.truncate(); handle.write(str(__import__('os').getpid())); handle.flush()
        _INSTANCE_LOCK_HANDLES[key]=handle
        return True


def release_instance_lock(root: Path | None = None):
    """Release the OS lock for one project, defaulting to the current context."""
    target=Path(root).resolve() if root is not None else storage_context.current_project_root()
    if target is None:return
    key=str((Path(target).resolve()/"breadmaker_editor.lock").resolve())
    with _INSTANCE_LOCKS_GUARD:
        handle=_INSTANCE_LOCK_HANDLES.pop(key,None)
    _unlock_handle(handle)


def release_all_instance_locks():
    """Release every project lock retained by this local process."""
    with _INSTANCE_LOCKS_GUARD:
        handles=list(_INSTANCE_LOCK_HANDLES.values())
        _INSTANCE_LOCK_HANDLES.clear()
    for handle in handles:_unlock_handle(handle)


def connect() -> sqlite3.Connection:
    """Open a normal writable connection without renegotiating persistent DB mode.

    WAL is database-persistent and is configured once by init_db(). Reissuing
    PRAGMA journal_mode=WAL on every HTTP request can wait on concurrent readers or
    writers and turned otherwise tiny request-scoped operations into multi-second
    stalls once Breadmaker became multi-user/request scoped.
    """
    ensure_dirs()
    con = sqlite3.connect(storage_context.current_db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA wal_autocheckpoint=500")
    return con


def read_connect() -> sqlite3.Connection:
    """Open one read-only WAL connection for latency-sensitive query endpoints.

    Reads do not need Breadmaker's mutation coordinator or BEGIN IMMEDIATE. WAL lets
    these readers proceed concurrently with canonical writes and background History.
    """
    db=storage_context.current_db_path().resolve()
    con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True,timeout=10,check_same_thread=False)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=3000")
    return con


@contextmanager
def read_tx():
    con=read_connect()
    try:
        yield con
    finally:
        con.close()


def database_schema_version() -> int:
    """Return the exact current storage schema, or 0 for unsupported/unknown data."""
    db=storage_context.current_db_path()
    if not db.exists() or db.stat().st_size == 0:
        return 0
    try:
        con=sqlite3.connect(db)
        try:
            tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'storage_meta' not in tables:
                return 0
            row=con.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone()
            return int(row[0]) if row else 0
        finally: con.close()
    except (sqlite3.Error,TypeError,ValueError,OSError):
        return 0


def init_db():
    """Open or create the sole supported pre-alpha project schema.

    Existing unsupported databases are inspected read-only and rejected before
    Breadmaker enables WAL or performs any write-capable SQLite initialization.
    """
    with storage_context._project_db_lock():
        db=storage_context.current_db_path()
        existed=db.exists() and db.stat().st_size>0
        if existed:
            try:
                ro=sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True)
                try:
                    tables={str(r[0]) for r in ro.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                    if 'storage_meta' not in tables:
                        raise UnsupportedProjectVersion('PREALPHA_PROJECT_INCOMPATIBLE')
                    row=ro.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone()
                    try:version=int(row[0]) if row else 0
                    except (TypeError,ValueError):version=0
                    if version!=SCHEMA_VERSION:
                        raise UnsupportedProjectVersion(f'PREALPHA_PROJECT_INCOMPATIBLE:{version}')
                finally:ro.close()
            except UnsupportedProjectVersion:
                raise
            except Exception as exc:
                raise UnsupportedProjectVersion('PREALPHA_PROJECT_INCOMPATIBLE') from exc
        # Only current or genuinely new project shells may create writable directories.
        ensure_dirs()
        con=connect()
        try:
            # WAL is persistent database configuration. Do it once per activation/init,
            # never on every request connection.
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA wal_autocheckpoint=500")
            tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables:
                workspace_project_id=storage_context.current_workspace_project_id()
                identity_rows=con.execute("SELECT project_uid FROM projects ORDER BY id").fetchall() if 'projects' in tables else []
                if len(identity_rows)!=1:raise UnsupportedProjectVersion('PROJECT_IDENTITY_MISSING')
                database_project_uid=str(identity_rows[0][0] or '')
                if not re.fullmatch(r'[0-9a-f]{32}',database_project_uid):raise UnsupportedProjectVersion('PROJECT_IDENTITY_INVALID')
                if workspace_project_id and database_project_uid!=workspace_project_id:raise UnsupportedProjectVersion('PROJECT_IDENTITY_MISMATCH')
                classification=storage_schema_contracts.history_schema_classification(con)
                generation_classification=storage_schema_contracts.generation_schema_classification(con)
                if classification['unclassified'] or generation_classification['unclassified']:
                    raise UnsupportedProjectVersion('PREALPHA_PROJECT_SCHEMA_DRIFT')
                for prow in con.execute("SELECT id FROM projects").fetchall():storage_invariants.validate_project_invariants(con,int(prow[0]))
                return
            if existed:raise UnsupportedProjectVersion('PREALPHA_PROJECT_INCOMPATIBLE')
            con.executescript(SCHEMA_SQL)
            t=storage_values.now()
            con.execute("INSERT INTO storage_meta(key,value) VALUES('schema_version',?)",(str(SCHEMA_VERSION),))
            workspace_project_id=storage_context.current_workspace_project_id()
            if not re.fullmatch(r'[0-9a-f]{32}',workspace_project_id):raise RuntimeError('PROJECT_IDENTITY_REQUIRED')
            cur=con.execute("INSERT INTO projects(project_uid,title,created_at,updated_at) VALUES(?,?,?,?)",(workspace_project_id,'Untitled Book',t,t))
            pid=int(cur.lastrowid)
            tab=int(con.execute("INSERT INTO document_tabs(project_id,position,title,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,?,?)",(pid,0,'Manuscript',1,t,t)).lastrowid)
            chapter=int(con.execute("INSERT INTO chapters(project_id,tab_id,position,title,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pid,tab,0,'Chapter 1','','',t,t)).lastrowid)
            bid=storage_values.uid('b_')
            con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(bid,pid,tab,chapter,0,'paragraph','body','','','',t,t))
            con.commit()
            classification=storage_schema_contracts.history_schema_classification(con);generation_classification=storage_schema_contracts.generation_schema_classification(con)
            if classification['unclassified'] or generation_classification['unclassified']:raise RuntimeError('CURRENT_SCHEMA_CLASSIFICATION_INCOMPLETE')
            storage_invariants.validate_project_invariants(con,pid)
        except Exception:
            try:con.rollback()
            except Exception:pass
            raise
        finally:con.close()


def _rotate_backup_files(paths, max_count: int, max_total_bytes: int, min_keep: int):
    """Bound backup growth by both count and disk usage, never deleting newest safety copies."""
    paths=sorted(paths,key=lambda p:p.stat().st_mtime,reverse=True)
    total=sum(p.stat().st_size for p in paths if p.exists())
    while len(paths)>min_keep and (len(paths)>max_count or total>max_total_bytes):
        old=paths.pop()
        try:
            size=old.stat().st_size; old.unlink(); total=max(0,total-size)
        except OSError:
            pass
