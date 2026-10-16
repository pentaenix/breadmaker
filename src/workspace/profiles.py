from __future__ import annotations
import json, os, shutil, tempfile, uuid
from pathlib import Path
from typing import Any
from .models import WorkspaceError
from .filesystem import _durable_replace, _fsync_dir, _webp_dimensions, now

class ProfileWorkspaceMixin:
    def _default_local_profile(self, display_name:str='Writer') -> dict[str,Any]:
        stamp=now();name=str(display_name or '').strip()[:80] or 'Writer'
        return {
            'user_id':f'usr_{uuid.uuid4().hex}',
            'display_name':name,
            'kind':'local',
            'has_avatar':False,
            'avatar_revision':'',
            'last_project_id':'',
            'created_at':stamp,
            'updated_at':stamp,
        }

    def _profile_avatar_path(self,user_id:str) -> Path:
        return self.profiles_dir / str(user_id) / 'avatar.webp'

    def _normalize_local_profile_locked(self,profile:dict[str,Any]) -> dict[str,Any]:
        user_id=str(profile.get('user_id') or '')
        if not user_id.startswith('usr_'):raise WorkspaceError('PROFILE_INVALID')
        avatar=self._profile_avatar_path(user_id);revision=str(profile.get('avatar_revision') or '')[:80]
        if avatar.is_file() and not revision:
            try:revision=str(avatar.stat().st_mtime_ns)
            except OSError:revision='1'
        return {
            'user_id':user_id,
            'display_name':str(profile.get('display_name') or 'Writer').strip()[:80] or 'Writer',
            'kind':'local',
            'has_avatar':avatar.is_file(),
            'avatar_revision':revision if avatar.is_file() else '',
            'last_project_id':str(profile.get('last_project_id') or ''),
            'created_at':str(profile.get('created_at') or now()),
            'updated_at':str(profile.get('updated_at') or now()),
        }

    def _write_profiles_index_locked(self,index:dict[str,Any]) -> dict[str,Any]:
        profiles=[self._normalize_local_profile_locked(dict(item)) for item in (index.get('profiles') or []) if isinstance(item,dict)]
        if not profiles:profiles=[self._default_local_profile()]
        ids={item['user_id'] for item in profiles};active=str(index.get('active_user_id') or '')
        if active not in ids:active=profiles[0]['user_id']
        value={'version':1,'active_user_id':active,'profiles':profiles}
        self.profiles_dir.mkdir(parents=True,exist_ok=True)
        fd,tmp_name=tempfile.mkstemp(prefix='profiles-',suffix='.json',dir=self.profiles_dir)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                json.dump(value,fh,indent=2,ensure_ascii=False);fh.flush();os.fsync(fh.fileno())
            _durable_replace(Path(tmp_name),self.profiles_index_path);_fsync_dir(self.profiles_dir)
            try:os.chmod(self.profiles_index_path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass
        return value

    def _profiles_index_locked(self) -> dict[str,Any]:
        index=None
        if self.profiles_index_path.is_file():
            try:
                raw=json.loads(self.profiles_index_path.read_text(encoding='utf-8'))
                if isinstance(raw,dict) and int(raw.get('version') or 0)==1 and isinstance(raw.get('profiles'),list):index=raw
            except (OSError,json.JSONDecodeError,ValueError,TypeError):
                broken=self.profiles_index_path.with_name(f'index.broken.{uuid.uuid4().hex[:8]}.json')
                try:shutil.copy2(self.profiles_index_path,broken)
                except OSError:pass
        if index is None:
            # Pre-alpha storage is schema-exact. The phase-one single-profile folder is
            # intentionally not migrated; 9.1 starts the multi-profile store cleanly.
            obsolete=self.root/'profile'
            if obsolete.exists():
                try:shutil.rmtree(obsolete)
                except OSError:pass
            index={'version':1,'active_user_id':'','profiles':[self._default_local_profile()]}
            index['active_user_id']=index['profiles'][0]['user_id']
            return self._write_profiles_index_locked(index)
        profiles=[self._normalize_local_profile_locked(dict(item)) for item in index.get('profiles',[]) if isinstance(item,dict)]
        if not profiles:profiles=[self._default_local_profile()]
        ids={item['user_id'] for item in profiles};active=str(index.get('active_user_id') or '')
        if active not in ids:active=profiles[0]['user_id']
        normalized={'version':1,'active_user_id':active,'profiles':profiles}
        return self._write_profiles_index_locked(normalized) if normalized!=index else normalized

    def local_profiles(self) -> list[dict[str,Any]]:
        with self._lock:return [dict(item) for item in self._profiles_index_locked()['profiles']]

    def active_profile_id(self) -> str:
        with self._lock:return str(self._profiles_index_locked()['active_user_id'])

    def local_profile(self,user_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            index=self._profiles_index_locked();wanted=str(user_id or index['active_user_id'])
            profile=next((dict(item) for item in index['profiles'] if str(item.get('user_id'))==wanted),None)
            if profile is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            return profile

    def create_local_profile(self,display_name:str) -> dict[str,Any]:
        name=str(display_name or '').strip()[:80]
        if not name:raise WorkspaceError('PROFILE_NAME_REQUIRED')
        with self._lock:
            index=self._profiles_index_locked();profile=self._default_local_profile(name)
            index['profiles'].append(profile);saved=self._write_profiles_index_locked(index)
            return dict(next(item for item in saved['profiles'] if item['user_id']==profile['user_id']))

    def set_active_profile(self,user_id:str) -> dict[str,Any]:
        with self._lock:
            index=self._profiles_index_locked();wanted=str(user_id or '')
            profile=next((dict(item) for item in index['profiles'] if str(item.get('user_id'))==wanted),None)
            if profile is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            index['active_user_id']=wanted;self._write_profiles_index_locked(index);return profile

    def profile_project_target(self,user_id:str) -> str|None:
        with self._lock:
            index=self._profiles_index_locked();profile=next((item for item in index['profiles'] if str(item.get('user_id'))==str(user_id)),None)
            if profile is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            owned=[]
            for item in self._registry.get('projects',[]):
                rec=dict(item);root=Path(rec['path'])
                membership=self._project_membership_locked(root,str(user_id),default_owner_user_id=str(rec.get('owner_user_id') or self._profiles_index_locked()['active_user_id']))
                if membership:
                    rec['membership_role']=str(membership.get('role') or '');owned.append(rec)
            last=str(profile.get('last_project_id') or '')
            if last and any(str(item.get('id'))==last for item in owned):return last
            owned.sort(key=lambda item:str(item.get('last_opened_at') or ''),reverse=True)
            return str(owned[0]['id']) if owned else None

    def _set_profile_last_project_locked(self,user_id:str,project_id:str) -> None:
        index=self._profiles_index_locked();changed=False
        for item in index['profiles']:
            if str(item.get('user_id'))==str(user_id):
                if str(item.get('last_project_id') or '')!=str(project_id):item['last_project_id']=str(project_id);item['updated_at']=now();changed=True
                break
        if changed:self._write_profiles_index_locked(index)

    def update_local_profile(self,display_name:str,user_id:str|None=None) -> dict[str,Any]:
        name=str(display_name or '').strip()[:80]
        if not name:raise WorkspaceError('PROFILE_NAME_REQUIRED')
        with self._lock:
            index=self._profiles_index_locked();active=str(user_id or index['active_user_id']);current=None
            for item in index['profiles']:
                if str(item.get('user_id'))==active:item['display_name']=name;item['updated_at']=now();current=item;break
            if current is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            saved=self._write_profiles_index_locked(index)
            return dict(next(item for item in saved['profiles'] if item['user_id']==active))

    def save_local_profile_avatar(self,payload:bytes,user_id:str|None=None) -> dict[str,Any]:
        """Persist an already-normalized 512px-or-smaller WebP avatar atomically."""
        if not payload or len(payload)>2*1024*1024:raise WorkspaceError('PROFILE_AVATAR_SIZE')
        if len(payload)<12 or payload[:4]!=b'RIFF' or payload[8:12]!=b'WEBP':raise WorkspaceError('PROFILE_AVATAR_FORMAT')
        dimensions=_webp_dimensions(payload)
        if not dimensions:raise WorkspaceError('PROFILE_AVATAR_FORMAT')
        if max(dimensions)>512:raise WorkspaceError('PROFILE_AVATAR_DIMENSIONS')
        with self._lock:
            index=self._profiles_index_locked();active=str(user_id or index['active_user_id']);current=next((item for item in index['profiles'] if str(item.get('user_id'))==active),None)
            if current is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            avatar=self._profile_avatar_path(active);avatar.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp_name=tempfile.mkstemp(prefix='avatar-',suffix='.webp',dir=avatar.parent)
            try:
                with os.fdopen(fd,'wb') as fh:fh.write(payload);fh.flush();os.fsync(fh.fileno())
                _durable_replace(Path(tmp_name),avatar);_fsync_dir(avatar.parent)
                try:os.chmod(avatar,0o600)
                except OSError:pass
            finally:
                try:os.unlink(tmp_name)
                except FileNotFoundError:pass
            current['has_avatar']=True;current['avatar_revision']=uuid.uuid4().hex[:16];current['updated_at']=now()
            saved=self._write_profiles_index_locked(index);return dict(next(item for item in saved['profiles'] if item['user_id']==active))

    def remove_local_profile_avatar(self,user_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            index=self._profiles_index_locked();active=str(user_id or index['active_user_id']);current=next((item for item in index['profiles'] if str(item.get('user_id'))==active),None)
            if current is None:raise WorkspaceError('PROFILE_NOT_FOUND')
            avatar=self._profile_avatar_path(active)
            try:avatar.unlink()
            except FileNotFoundError:pass
            current['has_avatar']=False;current['avatar_revision']='';current['updated_at']=now()
            saved=self._write_profiles_index_locked(index);return dict(next(item for item in saved['profiles'] if item['user_id']==active))
