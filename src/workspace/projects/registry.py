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

class ProjectRegistryMixin:
    def list_projects(self,user_id:str|None=None) -> list[dict[str, Any]]:
        with self._lock:
            wanted=str(user_id or self._profiles_index_locked()['active_user_id']);rows=[]
            for item in self._registry['projects']:
                rec=dict(item);membership=self._project_membership_locked(Path(rec['path']),wanted,default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))
                if not membership:continue
                rec['membership_role']=str(membership.get('role') or '');rec['member_id']=str(membership.get('member_id') or '');rows.append(rec)
            return sorted(rows,key=lambda p:p.get('last_opened_at',''),reverse=True)

    def list_all_projects(self) -> list[dict[str,Any]]:
        with self._lock:return sorted((dict(p) for p in self._registry['projects']),key=lambda p:p.get('last_opened_at',''),reverse=True)

    def active_id(self) -> str | None:
        with self._lock: return self._registry.get("active_project_id")

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            item=next((dict(p) for p in self._registry["projects"] if p["id"]==project_id), None)
        if not item: raise WorkspaceError("PROJECT_NOT_FOUND")
        return item

    def active(self) -> dict[str, Any] | None:
        pid=self.active_id()
        return self.get(pid) if pid else None

    def create_project(self,name:str,*,activate:bool=True,owner_user_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            owner=str(owner_user_id or self._profiles_index_locked()['active_user_id'])
            if not any(str(item.get('user_id'))==owner for item in self._profiles_index_locked()['profiles']):raise WorkspaceError('PROFILE_NOT_FOUND')
            project_id=uuid.uuid4().hex
            created=now(); clean=safe_name(name)
            root=self.projects_dir / project_id
            root.mkdir(parents=True, exist_ok=False)
            for child in ("assets","backups","exports","logs"):
                (root/child).mkdir()
            manifest={
                "package_format":"breadmaker-project",
                "package_version":PACKAGE_VERSION,
                "schema_version":APP_SCHEMA_VERSION,
                "project_id":project_id,
                "name":clean,
                "created_at":created,
                "updated_at":created,
            }
            self._write_manifest(root,manifest)
            self._write_project_owner_locked(root,owner)
            # 7.8 safety initialization lives outside reconstructible manifest.json.
            # Only this pristine marker is allowed to adopt the first blank-project
            # Current generation as a baseline; losing it later fails toward backup.
            self._write_initial_recovery_state(root,project_id,pristine_new_project=True)
            rec=asdict(ProjectRecord(project_id,clean,str(root),created,created,created));rec['owner_user_id']=owner
            self._registry['projects'].append(rec)
            if activate:
                self._registry['active_project_id']=project_id;self._set_profile_last_project_locked(owner,project_id)
            self._save_locked();return dict(rec)

    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        clean=safe_name(name)
        with self._lock:
            item=next((p for p in self._registry["projects"] if p["id"]==project_id), None)
            if not item: raise WorkspaceError("PROJECT_NOT_FOUND")
            item["name"]=clean; item["updated_at"]=now()
            root=Path(item["path"])
            def _rename_manifest(manifest):
                manifest["name"]=clean;manifest["updated_at"]=item["updated_at"];return manifest
            self._mutate_manifest(root,_rename_manifest);self._save_locked();return dict(item)

    def activate(self,project_id:str,*,user_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            item=next((p for p in self._registry['projects'] if p['id']==project_id),None)
            if not item:raise WorkspaceError('PROJECT_NOT_FOUND')
            active_user=str(user_id or self._profiles_index_locked()['active_user_id']);membership=self._project_membership_locked(Path(item['path']),active_user,default_owner_user_id=str(item.get('owner_user_id') or active_user))
            if not membership:raise WorkspaceError('PROJECT_MEMBERSHIP_REQUIRED')
            item['last_opened_at']=now();self._registry['active_project_id']=project_id;self._set_profile_last_project_locked(active_user,project_id);self._save_locked();out=dict(item);out['membership_role']=str(membership.get('role') or '');out['member_id']=str(membership.get('member_id') or '');return out

    def project_root(self, project_id: str | None=None) -> Path:
        item=self.get(project_id) if project_id else self.active()
        if not item: raise WorkspaceError("NO_ACTIVE_PROJECT")
        return Path(item["path"]).resolve()
