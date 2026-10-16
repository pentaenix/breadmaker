from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auth import identity as identity_service
from storage import assets as storage_assets
from storage import context as storage_context
from storage import coordination as storage_coordination
from storage import database as storage_database
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import values as storage_values
from .contracts import RuntimeServices, ProjectRequestContext, StoredAsset
from auth.sessions import NullSessionService
from auth.accounts import NullAccountIdentityService


@dataclass(frozen=True)
class LocalProjectRequestContext(ProjectRequestContext):
    """Local extension carrying the filesystem root without leaking it into the runtime contract."""
    root: Path


def _context_project_id(project_id=None):
    value=str(project_id or storage_context.current_workspace_project_id() or '').strip()
    return value or None


class LocalProjectContextProvider:
    """Bind one request/task to one project without changing the local UI's active book."""
    def __init__(self, workspace):
        self.workspace=workspace
        self._current: ContextVar[LocalProjectRequestContext | None]=ContextVar('breadmaker_runtime_project_context',default=None)

    def current(self):
        return self._current.get()

    @contextmanager
    def bind(self,project_id=None,*,user_id=None,require_membership=True):
        pid=str(project_id or self.workspace.active_id() or '')
        if not pid:raise RuntimeError('PROJECT_CONTEXT_REQUIRED')
        rec=self.workspace.get(pid)
        if not rec:raise RuntimeError('PROJECT_CONTEXT_NOT_FOUND')
        parent=self._current.get()
        resolved_user_id=user_id or (parent.actor.user_id if parent is not None else None)
        actor=identity_service.current_actor(self.workspace,pid,require_membership=require_membership,user_id=resolved_user_id)
        context=LocalProjectRequestContext(pid,actor,Path(str(rec['path'])).resolve())
        token=self._current.set(context)
        try:
            with storage_context.project_context(context.root,pid):
                # A request may address a project that has not been selected in the
                # local shell during this process lifetime. Retain its OS lock on first
                # access so request scoping never weakens single-process ownership.
                storage_database.acquire_instance_lock()
                yield context
        finally:
            self._current.reset(token)


class LocalIdentityProvider:
    def __init__(self, workspace, context): self.workspace=workspace; self.context=context
    def request_user_id(self,explicit_user_id=None):return str(explicit_user_id or self.workspace.active_profile_id())
    def active_profile_id(self): return self.workspace.active_profile_id()
    def current_profile(self,user_id=None): return self.workspace.local_profile(user_id)
    def profiles(self): return self.workspace.local_profiles()
    def create_profile(self,display_name): return self.workspace.create_local_profile(display_name)
    def activate_profile(self,user_id): return self.workspace.set_active_profile(user_id)
    def update_profile(self,display_name,user_id=None): return self.workspace.update_local_profile(display_name,user_id=user_id)
    def avatar_bytes(self,user_id):
        path=self.workspace._profile_avatar_path(user_id)
        return path.read_bytes() if path.is_file() else None
    def save_avatar(self,payload,user_id=None): return self.workspace.save_local_profile_avatar(payload,user_id=user_id)
    def remove_avatar(self,user_id=None): return self.workspace.remove_local_profile_avatar(user_id=user_id)
    def current_actor(self,project_id=None,*,require_membership=True,user_id=None):
        pid=_context_project_id(project_id)
        current=self.context.current()
        if current is not None and (not pid or pid==current.project_id):
            actor=current.actor
            if require_membership and not actor.member_id:raise identity_service.AuthorizationError('PROJECT_MEMBERSHIP_REQUIRED')
            return actor
        resolved_user_id=user_id or (current.actor.user_id if current is not None else None)
        return identity_service.current_actor(self.workspace,pid,require_membership=require_membership,user_id=resolved_user_id)


class LocalAuthorizationService:
    def __init__(self, workspace, context): self.workspace=workspace; self.context=context
    def _actor(self,project_id=None):
        pid=_context_project_id(project_id);current=self.context.current()
        if current is not None and (not pid or pid==current.project_id):return current.actor
        return identity_service.current_actor(self.workspace,pid,user_id=current.actor.user_id if current is not None else None)
    def can(self,permission,project_id=None):
        try:return str(permission) in self._actor(project_id).permissions
        except Exception:return False
    def require(self,permission,project_id=None):
        actor=self._actor(project_id)
        if str(permission) not in actor.permissions:raise identity_service.AuthorizationError('PROJECT_PERMISSION_DENIED')
        return actor


class LocalProjectRepository:
    def __init__(self, workspace, context): self.workspace=workspace; self.context=context
    @property
    def root(self): return self.workspace.root
    def init(self): return self.workspace.init()
    def list(self,user_id=None):
        current=self.context.current();uid=user_id or (current.actor.user_id if current is not None else None)
        return self.workspace.list_projects(uid)
    def active_id(self): return self.workspace.active_id()
    def active(self): return self.workspace.active()
    def get(self,project_id): return self.workspace.get(project_id)
    def create(self,name,*,activate=True,owner_user_id=None): return self.workspace.create_project(name,activate=activate,owner_user_id=owner_user_id)
    def activate(self,project_id,*,user_id=None):
        current=self.context.current();uid=user_id or (current.actor.user_id if current is not None else None)
        return self.workspace.activate(project_id,user_id=uid)
    def rename(self,project_id,name):
        rec=self.workspace.rename(project_id,name)
        # Keep the portable project database title synchronized inside the adapter.
        # HTTP/domain callers should not need to know that local persistence is SQLite.
        with self.context.bind(project_id):
            storage_database.acquire_instance_lock()
            with storage_mutations.tx() as con:
                row=con.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
                if row:
                    con.execute("UPDATE projects SET title=?,updated_at=? WHERE id=?",(rec["name"],storage_values.now(),int(row["id"])))
        return rec
    def delete(self,project_id,*,permanent=False): return self.workspace.delete_project(project_id,permanent=permanent)
    def import_package(self,path,*,name_override=None,owner_user_id=None):
        current=self.context.current();uid=owner_user_id or (current.actor.user_id if current is not None else None)
        return self.workspace.import_project(path,name_override=name_override,owner_user_id=uid)
    def export_package(self,project_id,out): return self.workspace.export_project(project_id,out)
    def export_all_data(self,out): return self.workspace.export_all_data(out)
    def verify_package(self,path): return self.workspace.verify_package(path)
    def inspect_package_metadata(self,path): return self.workspace.inspect_package_metadata(path)
    def read_manifest(self,root): return self.workspace.read_manifest(root)
    def members(self,project_id): return self.workspace.project_members(project_id)
    def membership(self,project_id,user_id): return self.workspace.project_membership(project_id,user_id)
    def add_member(self,project_id,user_id,role): return self.workspace.add_project_member(project_id,user_id,role)
    def remove_member(self,project_id,user_id): return self.workspace.remove_project_member(project_id,user_id)
    def profile_project_target(self,user_id): return self.workspace.profile_project_target(user_id)
    def recovery_candidates(self): return self.workspace.recovery_candidates()
    def list_trash(self,user_id=None):
        current=self.context.current();uid=user_id or (current.actor.user_id if current is not None else None)
        return self.workspace.list_trash(uid)
    def restore_from_trash(self,trash_id,*,user_id=None):
        current=self.context.current();uid=user_id or (current.actor.user_id if current is not None else None)
        return self.workspace.restore_from_trash(trash_id,user_id=uid)


class LocalAssetStore:
    @property
    def root(self): return storage_context.current_asset_dir()
    def _current_root(self):
        root=storage_context.current_project_root()
        if root is None: raise RuntimeError('PROJECT_ASSET_CONTEXT_MISSING')
        return storage_context.current_asset_dir().resolve()
    def _resolve_in_root(self,root,name,*,require_exists=True):
        value=str(name or '')
        if not value or Path(value).name!=value: raise ValueError('ASSET_PATH_INVALID')
        root=Path(root).resolve();target=(root/value).resolve()
        try:target.relative_to(root)
        except ValueError as exc:raise ValueError('ASSET_PATH_INVALID') from exc
        if require_exists and not target.is_file():raise FileNotFoundError(value)
        return target
    def resolve_name(self,name,*,require_exists=True):
        root=self._current_root()
        return self._resolve_in_root(root,name,require_exists=require_exists)
    def resolve_api_path(self,api_path,*,require_exists=True):
        value=str(api_path or '').strip()
        if not value.startswith('/api/assets/'):raise ValueError('ASSET_PATH_INVALID')
        return self.resolve_name(value.removeprefix('/api/assets/'),require_exists=require_exists)
    def _asset_name(self,name_or_api_path):
        value=str(name_or_api_path or '').strip()
        if value.startswith('/api/assets/'):
            value=value.removeprefix('/api/assets/')
        if not value or Path(value).name!=value:raise ValueError('ASSET_PATH_INVALID')
        return value
    def exists(self,name_or_api_path):
        try:return self.resolve_name(self._asset_name(name_or_api_path),require_exists=True).is_file()
        except FileNotFoundError:return False
    def read_blob(self,name_or_api_path):
        return self.resolve_name(self._asset_name(name_or_api_path),require_exists=True).read_bytes()
    def put_blob(self,blob,ext):
        with storage_coordination.project_switch_lock():
            root=self._current_root();root.mkdir(parents=True,exist_ok=True)
            digest=storage_values.sha(blob);name=f'img_{digest[:24]}{ext}';target=self._resolve_in_root(root,name,require_exists=False)
            if target.exists():
                if not target.is_file() or storage_values.sha_file(target)!=digest:raise RuntimeError('PROJECT_ASSET_HASH_COLLISION')
                created=False
            else:
                storage_assets.durable_write_project_asset(target,blob);created=True
            return StoredAsset('/api/assets/'+name,name,digest,created)
    def discard_uncommitted(self,asset):
        if not getattr(asset,'created',False):return
        try:target=self.resolve_name(str(asset.name),require_exists=False)
        except (ValueError,RuntimeError):return
        if not target.is_file():return
        try:
            if storage_values.sha_file(target)!=str(asset.digest):return
            target.unlink()
        except OSError:return
    def stage_upload(self,ext):
        # The parent directory is the upload's project binding. finalize_staged()
        # refuses to cross that boundary if the active project changes meanwhile.
        root=self._current_root();root.mkdir(parents=True,exist_ok=True)
        return root/f'.image-upload-{uuid.uuid4().hex}{ext}.tmp'
    def finalize_staged(self,staged,ext):
        staged=Path(staged).resolve();bound_root=staged.parent.resolve()
        with storage_coordination.project_switch_lock():
            current_root=self._current_root()
            if current_root!=bound_root:raise RuntimeError('PROJECT_ASSET_CONTEXT_CHANGED')
            digest=storage_values.sha_file(staged);name=f'img_{digest[:24]}{ext}';target=self._resolve_in_root(bound_root,name,require_exists=False)
            if target.exists():
                if not target.is_file() or storage_values.sha_file(target)!=digest:raise RuntimeError('PROJECT_ASSET_HASH_COLLISION')
                reused=True
            else:
                os.replace(staged,target);reused=False
                try:
                    fd=os.open(str(bound_root),os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                except (OSError,AttributeError):pass
            return '/api/assets/'+name,target,reused,digest


class LocalHistoryStore:
    def __init__(self,workspace):self.workspace=workspace
    def list_revisions(self):return storage_history.list_local_revisions()
    def revision_store_for(self,project_root):return storage_history.local_revision_store_for(Path(project_root))
    def sync_remote(self,project_id,revision_id=None):return self.workspace.sync_remote_history(project_id,revision_id=revision_id)
    def retention_settings(self):return self.workspace.history_retention_settings(_context_project_id())
    def update_retention(self,value):return self.workspace.update_history_retention(value,_context_project_id())
    def run_maintenance(self,project_id):return self.workspace.run_history_maintenance(project_id)
    def repair_local_from_destination(self,destination_id,*,project_id,revision_id=None):return self.workspace.repair_local_history_from_destination(destination_id,project_id=project_id,revision_id=revision_id)
    def remote_privacy_barrier(self):return self.workspace.remote_history_privacy_barrier()


class LocalBackupStore:
    def __init__(self,workspace):self.workspace=workspace
    @property
    def recovery_dir(self):return storage_context.current_backup_dir()
    def privacy_barrier(self):return self.workspace.backup_privacy_barrier()
    def erase_known_managed_data(self):return self.workspace.erase_known_managed_backup_data()
    def catch_up(self,project_id=None,**kwargs):return self.workspace.backup_destinations_catch_up(_context_project_id(project_id),**kwargs)
    def destinations(self):return self.workspace.list_backup_destinations(_context_project_id())
    def add_destination(self,path_value,*,destination_kind='folder'):return self.workspace.add_backup_destination(path_value,project_id=_context_project_id(),destination_kind=destination_kind)
    def update_destination(self,destination_id,**kwargs):return self.workspace.update_backup_destination(destination_id,project_id=_context_project_id(),**kwargs)
    def remove_destination(self,destination_id):return self.workspace.remove_backup_destination(destination_id,_context_project_id())
    def destination_backups(self,destination_id):return self.workspace.list_destination_backups(destination_id,_context_project_id())
    def discover_destination_backups(self,destination_id):return self.workspace.discover_destination_backups(destination_id,_context_project_id())
    def materialize_destination_backup(self,destination_id,filename):return self.workspace.materialize_destination_backup(destination_id,filename,_context_project_id())
    def google_oauth_config(self):return self.workspace.google_oauth_config()
    def begin_google_oauth(self,redirect_uri,**kwargs):return self.workspace.begin_google_drive_oauth(redirect_uri,project_id=_context_project_id(),**kwargs)
    def complete_google_oauth(self,state,code):return self.workspace.complete_google_drive_oauth(state,code)
    def fail_google_oauth(self,state,error):return self.workspace.fail_google_drive_oauth(state,error)
    def google_oauth_status(self,state):return self.workspace.google_drive_oauth_status(state)
    def add_webdav_destination(self,endpoint,username,password,name):return self.workspace.add_webdav_backup_destination(endpoint,username,password,name,project_id=_context_project_id())
    def reconnect_webdav_destination(self,destination_id,endpoint,username,password,name):return self.workspace.reconnect_webdav_backup_destination(destination_id,endpoint,username,password,name,project_id=_context_project_id())


class LocalProjectUsageService:
    """Filesystem-backed quota projection isolated from API/request code."""
    def __init__(self,workspace):self.workspace=workspace
    def _active_owner_records(self,owner_user_id):
        uid=str(owner_user_id or '')
        return [dict(row) for row in self.workspace.list_all_projects() if str(row.get('owner_user_id') or '')==uid]
    def _owner_records(self,owner_user_id):
        uid=str(owner_user_id or '');rows=self._active_owner_records(uid)
        try:rows.extend(dict(row) for row in self.workspace.list_trash(uid) if str(row.get('owner_user_id') or uid)==uid)
        except Exception:pass
        unique={str(Path(str(row.get('path') or '')).resolve()):row for row in rows if row.get('path')}
        return list(unique.values())
    @staticmethod
    def _directory_size(root,*,stop_after=None):
        root=Path(root);total=0
        if not root.exists():return 0
        if root.is_file():return int(root.stat().st_size)
        for path in root.rglob('*'):
            if not path.is_file():continue
            try:total+=int(path.stat().st_size)
            except OSError:continue
            if stop_after is not None and total>int(stop_after):return total
        return total
    def owner_project_count(self,owner_user_id):return len(self._active_owner_records(owner_user_id))
    def owner_storage_bytes(self,owner_user_id,*,stop_after=None):
        total=0
        for row in self._owner_records(owner_user_id):
            remaining=None if stop_after is None else max(0,int(stop_after)-total)
            total+=self._directory_size(row['path'],stop_after=remaining)
            if stop_after is not None and total>int(stop_after):return total
        return total
    def project_storage_bytes(self,project_id,*,stop_after=None):
        rec=self.workspace.get(project_id)
        return self._directory_size(rec['path'],stop_after=stop_after)


class LocalCapabilityProvider:
    mode='local'
    _CAPABILITIES={
        'offline':True,
        'local_profiles':True,
        'native_filesystem':True,
        'portable_project_export':True,
        'removable_backups':True,
        'direct_remote_backups':True,
        'request_scoped_projects':True,
        'request_scoped_actors':True,
        'request_scoped_recovery':True,
        'project_scoped_editor_leases':True,
        'hosted_projects':False,
        'account_sessions':False,
        'test_auth_bootstrap':False,
        'account_authentication':False,
        'hosted_collaboration':False,
        'realtime_collaboration':False,
    }
    def public(self):return dict(self._CAPABILITIES)
    def metadata(self):return {'persistence':'local_workspace','identity':'local_profiles','production_ready':True}
    def supports(self,capability):return bool(self._CAPABILITIES.get(str(capability),False))


def build_local_runtime(workspace) -> RuntimeServices:
    context=LocalProjectContextProvider(workspace)
    projects=LocalProjectRepository(workspace,context)
    assets=LocalAssetStore()
    history=LocalHistoryStore(workspace)
    backups=LocalBackupStore(workspace)
    return RuntimeServices(
        mode='local',
        context=context,
        identity=LocalIdentityProvider(workspace,context),
        authorization=LocalAuthorizationService(workspace,context),
        projects=projects,
        assets=assets,
        history=history,
        backups=backups,
        usage=LocalProjectUsageService(workspace),
        capabilities=LocalCapabilityProvider(),
        sessions=NullSessionService(),
        accounts=NullAccountIdentityService(),
        native_projects=projects,
        native_assets=assets,
        native_history=history,
        native_backups=backups,
    )
