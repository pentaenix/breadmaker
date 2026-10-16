from __future__ import annotations
import json, os, shutil, tempfile, threading, uuid
from pathlib import Path
from typing import Any
from .models import BackupProvider, WorkspaceError
from .providers import LocalFolderBackupProvider, WebDAVBackupProvider, GoogleDriveBackupProvider
from .filesystem import _durable_replace
from .profiles import ProfileWorkspaceMixin
from .remote_history import RemoteHistoryWorkspaceMixin
from .credentials import CredentialWorkspaceMixin
from .memberships import MembershipWorkspaceMixin
from .projects import ProjectWorkspaceMixin
from .backups import BackupWorkspaceMixin
from .erasure import BackupErasureWorkspaceMixin

class Workspace(ProfileWorkspaceMixin, RemoteHistoryWorkspaceMixin, CredentialWorkspaceMixin, MembershipWorkspaceMixin, ProjectWorkspaceMixin, BackupWorkspaceMixin, BackupErasureWorkspaceMixin):
    """Owns the app-level project registry; project content never lives in the app folder."""
    def __init__(self, root: Path | None = None):
        configured = os.environ.get("BREADMAKER_HOME")
        self.root = Path(root or configured or (Path.home() / ".breadmaker-editor")).expanduser().resolve()
        self.projects_dir = self.root / "projects"
        self.registry_path = self.root / "registry.json"
        self.backup_credentials_path = self.root / "backup_credentials.json"
        self.credential_encryption_key_path = self.root / "credential_encryption.key"
        self.google_oauth_config_path = self.root / "google_oauth_client.json"
        self.remote_history_writer_path = self.root / "remote_history_writer.json"
        # Installation-level local identities. These deliberately live outside every
        # project package: portable projects are content, while profiles are people on
        # this Breadmaker installation. 9.1 keeps one active profile at a time and
        # stores each avatar under that profile's stable usr_ identity.
        self.profiles_dir = self.root / "profiles"
        self.profiles_index_path = self.profiles_dir / "index.json"
        # Installation-level privacy inventory. This is intentionally independent of
        # project schema/manifest readability so Delete All can still erase known
        # Breadmaker-managed backup copies after a project becomes corrupt or unsupported.
        self.backup_erasure_inventory_path = self.root / "backup_erasure_inventory.json"
        self._google_oauth_flows: dict[str,dict[str,Any]] = {}
        self._lock = threading.RLock()
        self._backup_lock = threading.RLock()
        self._backup_in_progress: set[str] = set()
        self._remote_history_locks_guard = threading.Lock()
        self._remote_history_locks: dict[str, threading.RLock] = {}
        # Normal Remote History operations may proceed concurrently (their actual
        # mutation authority is per destination). Privacy erasure is exclusive: it
        # waits for active remote mutations, blocks new ones, erases, then reopens
        # the gate. This avoids the old global I/O lock bottleneck without weakening
        # Delete All's no-republication barrier.
        self._remote_history_activity_condition = threading.Condition(threading.Lock())
        self._remote_history_active_operations = 0
        self._remote_history_privacy_erasing = False
        self._manifest_locks_guard = threading.Lock()
        self._manifest_locks: dict[str, threading.RLock] = {}
        self._backup_providers: dict[str, BackupProvider] = {'local_folder':LocalFolderBackupProvider(),'webdav':WebDAVBackupProvider(),'google_drive':GoogleDriveBackupProvider()}
        self._registry: dict[str, Any] = {"version": 1, "active_project_id": None, "projects": []}
        # Local Breadmaker may reclaim installation-local membership metadata when a
        # project folder is copied from another Breadmaker home. Account-hosted runtimes
        # must fail closed instead: a damaged/missing sidecar can never change ownership.
        self._membership_recovery_mode = 'reclaim'

    def set_membership_recovery_mode(self, mode:str) -> None:
        value=str(mode or '').strip().lower()
        if value not in {'reclaim','fail_closed'}:raise WorkspaceError('MEMBERSHIP_RECOVERY_MODE_INVALID')
        with self._lock:self._membership_recovery_mode=value

    def init(self) -> None:
        with self._lock:
            self.projects_dir.mkdir(parents=True, exist_ok=True)
            loaded=False
            if self.registry_path.exists():
                try:
                    raw=json.loads(self.registry_path.read_text(encoding="utf-8"))
                    if isinstance(raw,dict) and isinstance(raw.get("projects"),list):
                        self._registry=raw; loaded=True
                except (OSError,json.JSONDecodeError):
                    # Registry metadata is reconstructible. Preserve the broken bytes for diagnostics,
                    # then rediscover projects from their independently portable folders.
                    broken=self.registry_path.with_name(f"registry.broken.{uuid.uuid4().hex[:8]}.json")
                    try: shutil.copy2(self.registry_path,broken)
                    except OSError: pass
            if not loaded:
                self._registry={"version":1,"active_project_id":None,"projects":[]}
            self._reconcile_projects_locked()
            self._save_locked()
            # Rebuild privacy-only destination discovery from raw JSON when possible.
            # This does not open/migrate project databases and never broadens delete authority.
            try:self._refresh_backup_erasure_inventory_from_raw_manifests()
            except Exception:pass

    def _save_locked(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="registry-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._registry, fh, indent=2, ensure_ascii=False)
                fh.flush(); os.fsync(fh.fileno())
            _durable_replace(Path(tmp_name), self.registry_path)
        finally:
            try: os.unlink(tmp_name)
            except FileNotFoundError: pass
    def read_manifest(self, root: Path) -> dict[str, Any]:
        root=Path(root).resolve()
        with self._manifest_lock_for(root):
            return json.loads((root/"manifest.json").read_text(encoding="utf-8"))

    def _write_manifest(self, root: Path, manifest: dict[str, Any]) -> None:
        root=Path(root).resolve()
        with self._manifest_lock_for(root):
            path=root/"manifest.json"; tmp=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
            data=json.dumps(manifest,indent=2,ensure_ascii=False)
            with tmp.open("w",encoding="utf-8") as fh:
                fh.write(data); fh.flush(); os.fsync(fh.fileno())
            _durable_replace(tmp,path)

    def _mutate_manifest(self, root:Path, mutator) -> dict[str,Any]:
        """Serialize manifest read-modify-write as one durability transaction.

        Atomic rename prevents torn JSON; this lock additionally prevents unrelated
        configuration writers from losing each other's fields.
        """
        root=Path(root).resolve()
        with self._manifest_lock_for(root):
            current=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
            updated=mutator(dict(current))
            value=dict(updated if updated is not None else current)
            self._write_manifest(root,value)
            return value
