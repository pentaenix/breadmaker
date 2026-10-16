from __future__ import annotations
import hashlib, json, os, shutil, sqlite3, tempfile, uuid, zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backups.frozen_snapshot import capture_frozen_snapshot, verify_frozen_package_payload, FrozenSnapshotError
from revision_store.local_store import canonical_state_hash
from storage import schema_contracts as storage_schema_contracts
from security import content_security
from workspace.constants import APP_SCHEMA_VERSION, PACKAGE_VERSION, MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, MAX_MANIFEST_BYTES, HASH_CHUNK_BYTES, PORTABLE_PROJECT_MANIFEST_KEYS
from workspace.models import ProjectRecord, WorkspaceError
from workspace.filesystem import _database_summary, _durable_replace, _recovery_metadata, _sha256_zip_entry, now, safe_name

class ProjectTrashMixin:
    def delete_project(self, project_id: str, *, permanent: bool=False) -> None:
        with self._lock:
            idx=next((i for i,p in enumerate(self._registry["projects"]) if p["id"]==project_id),None)
            if idx is None: raise WorkspaceError("PROJECT_NOT_FOUND")
            item=self._registry["projects"][idx]
            root=Path(item["path"])
            # A permanent deletion must either remove the project directory or fail.
            # Never drop the registry entry first and then hide an rmtree failure.
            if permanent:
                if root.exists(): shutil.rmtree(root)
            else:
                trash=self.root/"trash"; trash.mkdir(exist_ok=True)
                if root.exists(): shutil.move(str(root),str(trash/f"{project_id}-{int(datetime.now().timestamp())}"))
            self._registry["projects"].pop(idx)
            if self._registry.get('active_project_id')==project_id:
                active_user=str(self._profiles_index_locked()['active_user_id']);remaining=[]
                for p in self._registry['projects']:
                    if self._project_membership_locked(Path(p['path']),active_user,default_owner_user_id=str(p.get('owner_user_id') or active_user)):remaining.append(p)
                remaining.sort(key=lambda p:str(p.get('last_opened_at') or ''),reverse=True)
                self._registry['active_project_id']=remaining[0]['id'] if remaining else None
            self._save_locked()

    def list_trash(self,user_id:str|None=None) -> list[dict[str,Any]]:
        with self._lock:
            trash=self.root/'trash';out=[]
            if not trash.exists():return out
            active_user=str(user_id or self._profiles_index_locked()['active_user_id'])
            for root in sorted(trash.iterdir(),key=lambda p:p.stat().st_mtime if p.exists() else 0,reverse=True):
                if not root.is_dir():continue
                summary=_database_summary(root/'project.sqlite3')
                if not summary:continue
                owner=self._project_owner_locked(root,default_user_id=active_user)
                if owner!=active_user:continue
                out.append({'trash_id':root.name,'name':summary['name'],'path':str(root),'deleted_at':datetime.fromtimestamp(root.stat().st_mtime,timezone.utc).isoformat(timespec='seconds')})
            return out

    def restore_from_trash(self, trash_id:str, *, user_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            if Path(trash_id).name!=trash_id: raise WorkspaceError('INVALID_TRASH_ID')
            src=(self.root/'trash'/trash_id).resolve(); trash=(self.root/'trash').resolve()
            try: src.relative_to(trash)
            except ValueError: raise WorkspaceError('INVALID_TRASH_ID')
            if not src.is_dir(): raise WorkspaceError('TRASH_PROJECT_NOT_FOUND')

            # Validate the discarded project before moving or rewriting anything.
            # Trash is not a migration boundary: unsupported pre-alpha bytes remain
            # exactly where they are so Restore cannot relabel them as current.
            summary=_database_summary(src/'project.sqlite3')
            if not summary: raise WorkspaceError('PROJECT_DATABASE_CORRUPT')
            requesting_user=str(user_id or self._profiles_index_locked()['active_user_id'])
            trash_owner=self._project_owner_locked(src,default_user_id=requesting_user)
            if trash_owner!=requesting_user:raise WorkspaceError('PROJECT_MEMBERSHIP_REQUIRED')
            try:
                manifest=self.read_manifest(src)
            except Exception as exc:
                raise WorkspaceError('PROJECT_MANIFEST_UNREADABLE') from exc
            if (int(summary.get('schema_version') or 0)!=APP_SCHEMA_VERSION
                or str(manifest.get('package_format') or '')!='breadmaker-project'
                or int(manifest.get('package_version') or 0)!=PACKAGE_VERSION
                or int(manifest.get('schema_version') or 0)!=APP_SCHEMA_VERSION):
                raise WorkspaceError('PREALPHA_PROJECT_INCOMPATIBLE')

            project_id=trash_id.split('-',1)[0]
            source_project_uid=str(summary.get('project_uid') or '')
            if not project_id or source_project_uid!=project_id or str(manifest.get('project_id') or '')!=project_id:
                raise WorkspaceError('PROJECT_IDENTITY_MISMATCH')
            if (self.projects_dir/project_id).exists():
                raise WorkspaceError('PROJECT_IDENTITY_COLLISION')
            dest=self.projects_dir/project_id
            shutil.move(str(src),str(dest))
            manifest=dict(manifest)
            manifest.update({'project_id':project_id,'name':summary['name'],'updated_at':now()})
            manifest.setdefault('created_at',summary['created_at']); self._write_manifest(dest,manifest)
            owner=self._project_owner_locked(dest,default_user_id=self._profiles_index_locked()['active_user_id'])
            rec=asdict(ProjectRecord(project_id,summary['name'],str(dest),summary['created_at'],summary['updated_at'],now()));rec['owner_user_id']=owner
            self._registry['projects'].append(rec)
            if owner==str(self._profiles_index_locked()['active_user_id']):
                self._registry['active_project_id']=project_id;self._set_profile_last_project_locked(owner,project_id)
            self._save_locked();return dict(rec)
