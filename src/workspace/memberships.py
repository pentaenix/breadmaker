from __future__ import annotations
import json, os, tempfile, uuid
from pathlib import Path
from typing import Any
from .constants import APP_SCHEMA_VERSION, PACKAGE_VERSION
from .models import WorkspaceError
from .filesystem import _database_summary, _durable_replace, _fsync_dir, now, safe_name

class MembershipWorkspaceMixin:
    def _project_local_state_path(self,root:Path) -> Path:
        return Path(root) / 'local.json'

    def _new_project_membership_locked(self,user_id:str,role:str='owner') -> dict[str,Any]:
        user_id=str(user_id or '');role=str(role or '')
        if not user_id.startswith('usr_'):raise WorkspaceError('PROFILE_INVALID')
        if role not in {'owner','editor'}:raise WorkspaceError('PROJECT_MEMBER_ROLE_INVALID')
        stamp=now()
        return {'member_id':f'member_{uuid.uuid4().hex}','user_id':user_id,'role':role,'created_at':stamp,'updated_at':stamp}

    def _write_project_memberships_locked(self,root:Path,memberships:list[dict[str,Any]]) -> list[dict[str,Any]]:
        profiles={str(item.get('user_id')) for item in self._profiles_index_locked()['profiles']}
        normalized=[];seen_users=set();seen_members=set();owner_user_id=''
        for raw in memberships:
            if not isinstance(raw,dict):continue
            user_id=str(raw.get('user_id') or '');member_id=str(raw.get('member_id') or '');role=str(raw.get('role') or '')
            if user_id not in profiles or not user_id.startswith('usr_'):raise WorkspaceError('PROFILE_NOT_FOUND')
            if role not in {'owner','editor'}:raise WorkspaceError('PROJECT_MEMBER_ROLE_INVALID')
            if not member_id.startswith('member_'):member_id=f'member_{uuid.uuid4().hex}'
            if user_id in seen_users or member_id in seen_members:raise WorkspaceError('PROJECT_MEMBER_DUPLICATE')
            if role=='owner':
                if owner_user_id:raise WorkspaceError('PROJECT_OWNER_INVALID')
                owner_user_id=user_id
            stamp=str(raw.get('created_at') or now())
            normalized.append({'member_id':member_id,'user_id':user_id,'role':role,'created_at':stamp,'updated_at':str(raw.get('updated_at') or stamp)})
            seen_users.add(user_id);seen_members.add(member_id)
        if not owner_user_id:raise WorkspaceError('PROJECT_OWNER_REQUIRED')
        path=self._project_local_state_path(root);path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp_name=tempfile.mkstemp(prefix='local-',suffix='.json',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                # owner_user_id remains a derived convenience field for diagnostics and
                # registry recovery; memberships are authoritative from 9.2 onward.
                json.dump({'version':2,'owner_user_id':owner_user_id,'memberships':normalized},fh,indent=2,ensure_ascii=False);fh.flush();os.fsync(fh.fileno())
            _durable_replace(Path(tmp_name),path);_fsync_dir(path.parent)
            try:os.chmod(path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass
        return normalized

    def _project_memberships_locked(self,root:Path,*,default_owner_user_id:str|None=None) -> list[dict[str,Any]]:
        path=self._project_local_state_path(root);raw=None
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value,dict):raw=value
        except (OSError,json.JSONDecodeError,ValueError,TypeError):pass
        if raw and int(raw.get('version') or 0)==2 and isinstance(raw.get('memberships'),list):
            profiles={str(item.get('user_id')) for item in self._profiles_index_locked()['profiles']};members=[];seen_users=set();seen_members=set();owners=0
            for item in raw['memberships']:
                if not isinstance(item,dict):raise WorkspaceError('PROJECT_MEMBERSHIP_INVALID')
                user_id=str(item.get('user_id') or '');member_id=str(item.get('member_id') or '');role=str(item.get('role') or '')
                if user_id not in profiles or not member_id.startswith('member_') or role not in {'owner','editor'} or user_id in seen_users or member_id in seen_members:
                    raise WorkspaceError('PROJECT_MEMBERSHIP_INVALID')
                if role=='owner':owners+=1
                members.append({'member_id':member_id,'user_id':user_id,'role':role,'created_at':str(item.get('created_at') or ''),'updated_at':str(item.get('updated_at') or '')})
                seen_users.add(user_id);seen_members.add(member_id)
            if owners!=1:raise WorkspaceError('PROJECT_MEMBERSHIP_INVALID')
            return members
        # 9.1's owner-only local.json is installation metadata, not a project schema.
        # Local Breadmaker may normalize/reclaim it because every profile belongs to one
        # trusted installation. In account-hosted mode missing/legacy/invalid membership
        # metadata is an authorization failure and must never silently change ownership.
        if self._membership_recovery_mode=='fail_closed':
            raise WorkspaceError('PROJECT_MEMBERSHIP_INVALID')
        owner=str((raw or {}).get('owner_user_id') or default_owner_user_id or self._profiles_index_locked()['active_user_id'])
        return self._write_project_memberships_locked(root,[self._new_project_membership_locked(owner,'owner')])

    def _write_project_owner_locked(self,root:Path,user_id:str) -> None:
        user_id=str(user_id or '')
        # Kept as the creation primitive name so older call sites remain simple; the
        # bytes written are the 9.2 membership model, not an owner-only model.
        self._write_project_memberships_locked(root,[self._new_project_membership_locked(user_id,'owner')])

    def _repair_project_memberships_locked(self,root:Path,*,default_owner_user_id:str|None=None) -> list[dict[str,Any]]:
        """Repair installation-local membership metadata without touching project content.

        local.json is deliberately non-portable. Copying an otherwise-valid project
        folder into another Breadmaker home can therefore bring along usr_ ids that
        do not exist in the destination installation. That must not make the project
        unrecoverable. Preserve any unambiguous memberships that still reference
        profiles in this installation; otherwise reclaim the project for the current
        (or supplied valid) local profile.
        """
        index=self._profiles_index_locked();profile_ids={str(item.get('user_id')) for item in index['profiles']}
        fallback=str(default_owner_user_id or '')
        if fallback not in profile_ids:fallback=str(index['active_user_id'])
        raw=None
        try:
            value=json.loads(self._project_local_state_path(root).read_text(encoding='utf-8'))
            if isinstance(value,dict):raw=value
        except (OSError,json.JSONDecodeError,ValueError,TypeError):
            raw=None
        salvage=[];seen_users=set();seen_members=set();owners=0
        if raw and int(raw.get('version') or 0)==2 and isinstance(raw.get('memberships'),list):
            for item in raw['memberships']:
                if not isinstance(item,dict):continue
                user_id=str(item.get('user_id') or '');member_id=str(item.get('member_id') or '');role=str(item.get('role') or '')
                if user_id not in profile_ids or role not in {'owner','editor'} or user_id in seen_users:continue
                if not member_id.startswith('member_') or member_id in seen_members:member_id=f'member_{uuid.uuid4().hex}'
                if role=='owner':
                    if owners:continue
                    owners+=1
                stamp=str(item.get('created_at') or now())
                salvage.append({'member_id':member_id,'user_id':user_id,'role':role,'created_at':stamp,'updated_at':str(item.get('updated_at') or stamp)})
                seen_users.add(user_id);seen_members.add(member_id)
        if owners==1:
            return self._write_project_memberships_locked(root,salvage)
        return self._write_project_memberships_locked(root,[self._new_project_membership_locked(fallback,'owner')])

    def _project_owner_locked(self,root:Path,*,default_user_id:str|None=None) -> str:
        memberships=self._project_memberships_locked(root,default_owner_user_id=default_user_id)
        owner=next((item for item in memberships if str(item.get('role'))=='owner'),None)
        if not owner:raise WorkspaceError('PROJECT_OWNER_REQUIRED')
        return str(owner['user_id'])

    def _project_membership_locked(self,root:Path,user_id:str,*,default_owner_user_id:str|None=None) -> dict[str,Any]|None:
        wanted=str(user_id or '')
        return next((dict(item) for item in self._project_memberships_locked(root,default_owner_user_id=default_owner_user_id) if str(item.get('user_id'))==wanted),None)

    def project_members(self,project_id:str) -> list[dict[str,Any]]:
        with self._lock:
            rec=self.get(project_id);members=self._project_memberships_locked(Path(rec['path']),default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))
            profiles={str(item.get('user_id')):item for item in self._profiles_index_locked()['profiles']}
            return [{**dict(member),'display_name':str((profiles.get(str(member.get('user_id'))) or {}).get('display_name') or 'Writer')} for member in members]

    def project_membership(self,project_id:str,user_id:str|None=None) -> dict[str,Any]|None:
        if not project_id:return None
        with self._lock:
            rec=self.get(project_id);wanted=str(user_id or self._profiles_index_locked()['active_user_id'])
            return self._project_membership_locked(Path(rec['path']),wanted,default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))

    def add_project_member(self,project_id:str,user_id:str,role:str='editor') -> dict[str,Any]:
        role=str(role or 'editor')
        if role!='editor':raise WorkspaceError('PROJECT_MEMBER_ROLE_INVALID')
        with self._lock:
            rec=self.get(project_id);root=Path(rec['path']);members=self._project_memberships_locked(root,default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))
            if any(str(item.get('user_id'))==str(user_id) for item in members):raise WorkspaceError('PROJECT_MEMBER_EXISTS')
            members.append(self._new_project_membership_locked(user_id,role));saved=self._write_project_memberships_locked(root,members)
            member=next(dict(item) for item in saved if str(item.get('user_id'))==str(user_id));self._reconcile_projects_locked();self._save_locked();return member

    def remove_project_member(self,project_id:str,user_id:str) -> None:
        with self._lock:
            rec=self.get(project_id);root=Path(rec['path']);members=self._project_memberships_locked(root,default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))
            target=next((item for item in members if str(item.get('user_id'))==str(user_id)),None)
            if not target:raise WorkspaceError('PROJECT_MEMBER_NOT_FOUND')
            if str(target.get('role'))=='owner':raise WorkspaceError('PROJECT_OWNER_REMOVE_FORBIDDEN')
            self._write_project_memberships_locked(root,[item for item in members if str(item.get('user_id'))!=str(user_id)]);self._reconcile_projects_locked();self._save_locked()

    def _reconcile_projects_locked(self) -> None:
        """Make registry.json a cache: valid project folders are authoritative/discoverable."""
        current={str(item.get('id')):dict(item) for item in self._registry.get('projects',[]) if isinstance(item,dict) and item.get('id')}
        active_user_id=str(self._profiles_index_locked()['active_user_id'])
        recovered=[]
        for root in sorted(self.projects_dir.iterdir()) if self.projects_dir.exists() else []:
            if not root.is_dir(): continue
            project_id=root.name
            manifest=None
            try:
                raw=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
                if isinstance(raw,dict): manifest=raw
            except (OSError,json.JSONDecodeError):
                manifest=None
            summary=_database_summary(root/'project.sqlite3')
            if summary is None:
                # Never hide/delete an unreadable folder. Recovery mode can surface it.
                continue
            created=str((manifest or {}).get('created_at') or summary['created_at'])
            updated=str((manifest or {}).get('updated_at') or summary['updated_at'])
            name=safe_name(str((manifest or {}).get('name') or summary['name']))
            manifest_schema=int((manifest or {}).get('schema_version') or 0)
            database_schema=int(summary.get('schema_version') or 0)
            current_manifest=(
                manifest is not None
                and str(manifest.get('package_format') or '')=='breadmaker-project'
                and int(manifest.get('package_version') or 0)==PACKAGE_VERSION
                and manifest_schema==APP_SCHEMA_VERSION
                and database_schema==APP_SCHEMA_VERSION
                and str(manifest.get('project_id') or '')==project_id
                and str(summary.get('project_uid') or '')==project_id
            )
            if not current_manifest:
                # The registry is only a cache for current-format projects. Unsupported,
                # mismatched, or unreadable pre-alpha folders remain untouched and are
                # surfaced through Recovery candidates instead of being relabeled current.
                continue
            old=current.get(project_id,{})
            profile_ids={str(item.get('user_id')) for item in self._profiles_index_locked()['profiles']}
            default_owner=str(old.get('owner_user_id') or '')
            if default_owner not in profile_ids:default_owner=active_user_id
            try:
                memberships=self._project_memberships_locked(root,default_owner_user_id=default_owner)
            except WorkspaceError as exc:
                if str(exc)!='PROJECT_MEMBERSHIP_INVALID':raise
                if self._membership_recovery_mode=='fail_closed':
                    # Keep the bytes untouched and omit the project from the normal registry.
                    # Hosted operators can repair the sidecar deliberately; no account gains
                    # ownership merely because its profile happened to be globally active.
                    continue
                memberships=self._repair_project_memberships_locked(root,default_owner_user_id=default_owner)
            owner=next((str(item.get('user_id')) for item in memberships if str(item.get('role'))=='owner'),'')
            if not owner:raise WorkspaceError('PROJECT_OWNER_REQUIRED')
            recovered.append({
                'id':project_id,'name':name,'path':str(root.resolve()),'created_at':created,'updated_at':updated,
                'last_opened_at':str(old.get('last_opened_at') or updated or created),'owner_user_id':owner,
            })
        self._registry['projects']=recovered
        visible=[]
        for p in recovered:
            membership=self._project_membership_locked(Path(p['path']),active_user_id,default_owner_user_id=str(p.get('owner_user_id') or active_user_id))
            if membership:visible.append(p)
        ids={p['id'] for p in visible}
        if self._registry.get('active_project_id') not in ids:
            visible.sort(key=lambda p:str(p.get('last_opened_at') or ''),reverse=True)
            self._registry['active_project_id']=visible[0]['id'] if visible else None
