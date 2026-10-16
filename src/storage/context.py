from __future__ import annotations

"""Project-bound storage paths and request/task-local storage context."""

import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]


ROOT = SOURCE_ROOT.parent


PROJECT_ROOT: Path | None = None


DATA_DIR = ROOT / "_unconfigured"


BACKUP_DIR = ROOT / "_unconfigured" / "backups"


ASSET_DIR = ROOT / "_unconfigured" / "assets"


DB_PATH = ROOT / "_unconfigured" / "project.sqlite3"


@dataclass(frozen=True)
class ProjectStorageContext:
    """Request/task-local binding for one Breadmaker project.

    ``configure_project`` remains the local UI's default-project selector, but
    canonical storage code resolves paths through this ContextVar first. That keeps
    one local active book while allowing concurrent requests/background tasks to bind
    different projects without mutating process-global storage paths.
    """
    root: Path
    workspace_project_id: str = ''


_PROJECT_CONTEXT: ContextVar[ProjectStorageContext | None] = ContextVar(
    'breadmaker_project_storage_context', default=None
)


_PROJECT_DB_LOCKS_GUARD = threading.Lock()


_PROJECT_DB_LOCKS: dict[str, threading.RLock] = {}


def current_project_context() -> ProjectStorageContext | None:
    return _PROJECT_CONTEXT.get()


def current_project_root() -> Path | None:
    context=_PROJECT_CONTEXT.get()
    return context.root if context is not None else PROJECT_ROOT


def current_data_dir() -> Path:
    root=current_project_root()
    return Path(root).resolve() if root is not None else ROOT / "_unconfigured"


def current_backup_dir() -> Path:
    return current_data_dir() / "backups"


def current_asset_dir() -> Path:
    return current_data_dir() / "assets"


def current_db_path() -> Path:
    return current_data_dir() / "project.sqlite3"


def current_workspace_project_id() -> str:
    context=_PROJECT_CONTEXT.get()
    return str(context.workspace_project_id or '') if context is not None else ''


def _project_db_lock(root: Path | None = None) -> threading.RLock:
    resolved=Path(root or current_data_dir()).resolve()
    key=str(resolved)
    with _PROJECT_DB_LOCKS_GUARD:
        lock=_PROJECT_DB_LOCKS.get(key)
        if lock is None:
            lock=threading.RLock();_PROJECT_DB_LOCKS[key]=lock
        return lock


@contextmanager
def project_context(root: Path, workspace_project_id: str = ''):
    """Bind storage paths to one project for only the current request/task context."""
    binding=ProjectStorageContext(Path(root).resolve(),str(workspace_project_id or ''))
    token=_PROJECT_CONTEXT.set(binding)
    try:
        yield binding
    finally:
        _PROJECT_CONTEXT.reset(token)


PORTABLE_PROJECT_MANIFEST_KEYS = {
    'package_format','package_version','schema_version','project_id',
    'name','created_at','updated_at',
}


def inspect_project_schema(root: Path) -> int:
    """Read the configured schema version without mutating project bytes."""
    db=Path(root).resolve()/'project.sqlite3'
    if not db.is_file():
        return 0
    try:
        con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)
        try:
            tables={str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'storage_meta' not in tables:return 0
            row=con.execute("SELECT value FROM storage_meta WHERE key='schema_version'").fetchone()
            return int(row[0]) if row else 0
        finally:con.close()
    except (sqlite3.Error,OSError,TypeError,ValueError):
        return 0


def clear_project():
    """Detach process-global local storage from the previously active project.

    Privacy reset calls this before deleting the Breadmaker home. Background work that
    did not capture an explicit request/task project can therefore never recreate the
    just-erased project through a stale global path while the replacement project is
    being initialized. Request/task-local stale work remains protected by the existing
    History/Recovery/Backup privacy barriers and their explicit existence checks.
    """
    global PROJECT_ROOT, DATA_DIR, BACKUP_DIR, ASSET_DIR, DB_PATH
    PROJECT_ROOT = None
    DATA_DIR = ROOT / "_unconfigured"
    BACKUP_DIR = DATA_DIR / "backups"
    ASSET_DIR = DATA_DIR / "assets"
    DB_PATH = DATA_DIR / "project.sqlite3"


def configure_project(root: Path):
    """Point storage at one independently portable Breadmaker project."""
    global PROJECT_ROOT, DATA_DIR, BACKUP_DIR, ASSET_DIR, DB_PATH
    PROJECT_ROOT = Path(root).resolve()
    DATA_DIR = PROJECT_ROOT
    BACKUP_DIR = PROJECT_ROOT / "backups"
    ASSET_DIR = PROJECT_ROOT / "assets"
    DB_PATH = PROJECT_ROOT / "project.sqlite3"
