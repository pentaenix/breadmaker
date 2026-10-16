from __future__ import annotations
import json, os, shutil, tempfile, uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backups.providers.webdav import WebDAVClient, WebDAVError, normalize_endpoint
from backups.retention import recovery_retention_partition, retention_authority_key, normalize_recovery_retention_profile, DEFAULT_RECOVERY_RETENTION_PROFILE
from backups.publication import PUBLICATION_STATE_ORDER, PUBLICATION_SUFFIX, build_publication_manifest, validate_publication_manifest, publication_candidate, publication_filename, sha256_file, json_bytes as publication_json_bytes
from revision_store.history_gc import DEFAULT_HISTORY_RETENTION, normalize_history_retention
from workspace.constants import BACKUP_INTEGRITY_SCRUB_SECONDS, EXTERNAL_BACKUP_MARKER, HASH_CHUNK_BYTES
from workspace.models import BackupOperation, BackupProvider, WorkspaceError, BACKUP_DESTINATION_KINDS, _normalize_backup_destination_kind
from workspace.providers import WebDAVBackupProvider
from workspace import filesystem as workspace_fs
from workspace.filesystem import _destination_default_name, _durable_replace, _physical_disk_identity, _project_change_age_seconds, _project_change_token, _project_generation, _read_external_backup_marker, _same_physical_disk, _write_external_backup_marker, now

class BackupDestinationMixin:
    def _validate_backup_destination_path(self, path:Path) -> Path:
        """Reject destinations inside Breadmaker's own durable data tree.

        Project exports include project assets. Allowing a destination beneath the
        workspace (especially beneath a project's assets directory) can therefore
        make a later package contain earlier backup packages recursively. It also
        creates a misleading backup that Delete All would remove with local data.
        """
        resolved=Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            return resolved
        raise WorkspaceError('BACKUP_DESTINATION_INSIDE_BREADMAKER_HOME')

    def _backup_destinations(self, root: Path, manifest: dict[str,Any] | None=None, *, persist_normalization: bool=True) -> list[dict[str,Any]]:
        """Return current-format Backup Destination configuration.

        7.8 is intentionally current-format-only. We do not infer state from pre-7.8
        fields, old verification records, or pre-publication backup metadata. Missing
        current operational fields are safe defaults only; authority remains the
        immutable publication records themselves.
        """
        manifest=dict(manifest or self.read_manifest(root))
        raw=manifest.get('backup_destinations')
        if raw is None:
            return []
        if not isinstance(raw,list):
            raise WorkspaceError('BACKUP_DESTINATIONS_INVALID')
        if int(manifest.get('backup_destinations_version') or 0)!=3:
            raise WorkspaceError('BACKUP_DESTINATIONS_VERSION_UNSUPPORTED')
        destinations=[]
        changed=False
        for raw_item in raw:
            if not isinstance(raw_item,dict):
                raise WorkspaceError('BACKUP_DESTINATION_INVALID')
            item=dict(raw_item)
            did=str(item.get('id') or '').strip();provider=str(item.get('provider') or '').strip()
            if not did or provider not in self._backup_providers:
                raise WorkspaceError('BACKUP_DESTINATION_INVALID')
            default_kind='remote' if provider in {'webdav','google_drive'} else 'local'
            kind=str(item.get('destination_kind') or '').strip()
            if not kind:
                item['destination_kind']=default_kind;kind=default_kind;changed=True
            # local_folder deliberately supports both ordinary/removable storage and
            # user-classified synced folders. Remote providers own only remote kind.
            # Unknown future kinds remain visible so status/privacy code can fail
            # closed with a destination-specific diagnostic instead of hiding the
            # whole configuration behind a generic parse error.
            if kind in BACKUP_DESTINATION_KINDS:
                if provider in {'webdav','google_drive'} and kind!='remote':
                    raise WorkspaceError('BACKUP_DESTINATION_KIND_INVALID')
                if provider=='local_folder' and kind not in {'local','synced_folder'}:
                    raise WorkspaceError('BACKUP_DESTINATION_KIND_INVALID')
            defaults={
                'last_attempted_generation':0,
                'last_transferred_generation':0,
                'last_verified_generation':0,
                'last_published_generation':0,
                'publication_generation':0,
                'publication_state':'',
                'retry_state':{'state':'idle','generation':0,'attempts':0},
                'recovery_retention_profile':DEFAULT_RECOVERY_RETENTION_PROFILE,
            }
            if provider in {'webdav','google_drive'}:
                defaults['remote_history_retention']=DEFAULT_HISTORY_RETENTION
                defaults['remote_history_repository_initialized']=False
            for key,value in defaults.items():
                if key not in item:
                    item[key]=value;changed=True
            try:
                item['recovery_retention_profile']=normalize_recovery_retention_profile(item.get('recovery_retention_profile'))
            except ValueError as exc:
                raise WorkspaceError('RECOVERY_RETENTION_PROFILE_INVALID') from exc
            if provider in {'webdav','google_drive'}:
                try:item['remote_history_retention']=normalize_history_retention(item.get('remote_history_retention'))
                except ValueError as exc:raise WorkspaceError('HISTORY_RETENTION_INVALID') from exc
            destinations.append(item)
        if changed and persist_normalization:
            # Normalize only current-format operational defaults. Old destination
            # manifest versions are rejected above and are never rewritten in place.
            self._save_backup_destinations(root,destinations)
        return destinations

    def _save_backup_destinations(self, root:Path, destinations:list[dict[str,Any]]) -> None:
        snapshot=[dict(item) for item in destinations]
        project_id=''
        def _update(manifest):
            nonlocal project_id
            project_id=str(manifest.get('project_id') or Path(root).name).strip()
            manifest['backup_destinations_version']=3
            manifest['backup_destinations']=snapshot
            manifest['updated_at']=now()
            return manifest
        self._mutate_manifest(root,_update)
        self._remember_backup_erasure_destinations(project_id,snapshot)

    def _mark_remote_history_initialized(self, root:Path, destination_id:str) -> None:
        """Persist explicit repository-genesis state after verified initialization."""
        with self._backup_lock:
            destinations=self._backup_destinations(root,persist_normalization=False)
            changed=False
            for item in destinations:
                if str(item.get('id') or '')==str(destination_id) and item.get('remote_history_repository_initialized') is not True:
                    item['remote_history_repository_initialized']=True;changed=True
            if changed:self._save_backup_destinations(root,destinations)

    def _resolve_local_destination(self, destination:dict[str,Any]) -> dict[str,Any]:
        configured=Path(str(destination.get('path') or '')).expanduser()
        location_id=str(destination.get('location_id') or '').strip()
        volume_id=str(destination.get('volume_id') or '').strip()
        path=configured.resolve() if str(configured) else configured
        configured_exists=bool(str(configured) and path.is_dir())
        if configured_exists:
            marker=_read_external_backup_marker(path)
            marker_ok=bool(marker and str(marker.get('location_id') or '')==location_id)
            volume_ok=True
            if volume_id:
                volume_ok=str(workspace_fs._volume_metadata(path).get('id') or '')==volume_id
            if marker_ok and volume_ok:
                return {'path':path,'available':True,'attention':False,'reason':''}
            if marker or (path/EXTERNAL_BACKUP_MARKER).exists() or volume_id:
                return {'path':path,'available':False,'attention':True,'reason':'identity_mismatch'}
        relative=str(destination.get('volume_relative_path') or '').strip().strip('/')
        rel_path=Path(relative) if relative else Path()
        if rel_path.is_absolute() or '..' in rel_path.parts:
            return {'path':path,'available':False,'attention':True,'reason':'identity_metadata_invalid'}
        if volume_id:
            for root in workspace_fs._mounted_volume_roots():
                meta=workspace_fs._volume_metadata(root)
                if str(meta.get('id') or '')!=volume_id: continue
                mount=Path(root).resolve();candidate=(mount/rel_path).resolve() if relative else mount
                try:candidate.relative_to(mount)
                except ValueError:return {'path':candidate,'available':False,'attention':True,'reason':'identity_metadata_invalid'}
                if candidate.is_dir():
                    marker=_read_external_backup_marker(candidate)
                    if marker and str(marker.get('location_id') or '')==location_id:
                        return {'path':candidate,'available':True,'attention':False,'reason':'','relocated':candidate!=path}
                    if (candidate/EXTERNAL_BACKUP_MARKER).exists():
                        return {'path':candidate,'available':False,'attention':True,'reason':'identity_mismatch'}
                return {'path':candidate,'available':False,'attention':True,'reason':'folder_missing'}
        return {'path':path,'available':False,'attention':False,'reason':'not_connected'}

    def _backup_provider(self, destination:dict[str,Any]) -> BackupProvider | None:
        return self._backup_providers.get(str(destination.get('provider') or ''))

    def list_backup_destinations(self, project_id:str|None=None) -> list[dict[str,Any]]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:return []
        root=Path(rec['path']);manifest=self.read_manifest(root);destinations=self._backup_destinations(root,manifest)
        token=_project_change_token(root)
        return [self._destination_status(rec,item,token) for item in destinations]

    def add_backup_destination(self, path_value:str, project_id:str|None=None, *, destination_kind:str='local') -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        kind=_normalize_backup_destination_kind(destination_kind)
        if kind=='remote':raise WorkspaceError('BACKUP_DESTINATION_REMOTE_REQUIRES_PROVIDER_CONNECT')
        with self._backup_lock:
            root=Path(rec['path']);manifest=self.read_manifest(root);destinations=self._backup_destinations(root,manifest)
            path=Path(str(path_value or '').strip()).expanduser().resolve()
            if not str(path_value or '').strip():raise WorkspaceError('BACKUP_DESTINATION_REQUIRED')
            path=self._validate_backup_destination_path(path)
            path.mkdir(parents=True,exist_ok=True)
            probe=path/f'.breadmaker-write-test-{uuid.uuid4().hex}'
            try:
                with probe.open('w',encoding='utf-8') as fh:
                    fh.write('Breadmaker backup destination verification');fh.flush();os.fsync(fh.fileno())
            finally:probe.unlink(missing_ok=True)
            marker_path=path/EXTERNAL_BACKUP_MARKER;marker=_read_external_backup_marker(path)
            if marker_path.exists() and not marker:raise WorkspaceError('EXTERNAL_BACKUP_LOCATION_MARKER_INVALID')
            location_id=str(marker.get('location_id')) if marker else uuid.uuid4().hex
            if not marker:_write_external_backup_marker(path,location_id)
            for item in destinations:
                if str(item.get('location_id') or '')==location_id:raise WorkspaceError('BACKUP_DESTINATION_ALREADY_ADDED')
            volume=workspace_fs._volume_metadata(path);volume_root=Path(volume.get('root')).resolve() if volume.get('root') else Path(path.anchor or '/')
            try:relative=path.relative_to(volume_root).as_posix()
            except ValueError:relative=''
            dest={
                'id':uuid.uuid4().hex,'provider':'local_folder','destination_kind':kind,'name':_destination_default_name(path,volume,destination_kind=kind),
                'path':str(path),'location_id':location_id,'device_identity':_physical_disk_identity(path) or f'filesystem:{os.stat(path).st_dev}',
                'volume_id':str(volume.get('id') or ''),'volume_relative_path':relative,'created_at':now(),'last_verified':None,
            }
            destinations.append(dest);self._save_backup_destinations(root,destinations)
        return self._destination_status(rec,dest)

    def add_webdav_backup_destination(self, endpoint:str, username:str='', password:str='', name:str|None=None, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        try:normalized=normalize_endpoint(endpoint)
        except WebDAVError as exc:raise WorkspaceError(exc.code) from exc
        credential_id=self._store_backup_credential(username,password)
        try:
            client=WebDAVClient(normalized,username,password);client.health()
            marker=client.get_json(WebDAVBackupProvider.MARKER)
            if marker:
                location_id=str(marker.get('location_id') or '').strip()
                if not location_id:raise WorkspaceError('WEBDAV_IDENTITY_INVALID')
            else:
                location_id=uuid.uuid4().hex;client.put_json(WebDAVBackupProvider.MARKER,{'format':'breadmaker-backup-location','version':1,'location_id':location_id,'created_at':now()})
                check=client.get_json(WebDAVBackupProvider.MARKER)
                if str(check.get('location_id') or '')!=location_id:raise WorkspaceError('WEBDAV_IDENTITY_INVALID')
            with self._backup_lock:
                root=Path(rec['path']);destinations=self._backup_destinations(root)
                for item in destinations:
                    if str(item.get('provider'))=='webdav' and str(item.get('location_id') or '')==location_id:
                        raise WorkspaceError('BACKUP_DESTINATION_ALREADY_ADDED')
                host=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(normalized).hostname or 'WebDAV'
                dest={'id':uuid.uuid4().hex,'provider':'webdav','destination_kind':'remote','name':(str(name or '').strip()[:120] or f'WebDAV — {host}'),'endpoint':normalized,'credential_id':credential_id,'location_id':location_id,'created_at':now(),'last_verified':None,'remote_history_repository_initialized':False}
                destinations.append(dest);self._save_backup_destinations(root,destinations)
            return self._destination_status(rec,dest)
        except (WebDAVError,WorkspaceError) as exc:
            self._delete_backup_credential(credential_id)
            if isinstance(exc,WebDAVError):raise WorkspaceError(exc.code) from exc
            raise

    def reconnect_webdav_backup_destination(self, destination_id:str, endpoint:str, username:str='', password:str='', name:str|None=None, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        try:normalized=normalize_endpoint(endpoint)
        except WebDAVError as exc:raise WorkspaceError(exc.code) from exc
        client=WebDAVClient(normalized,username,password)
        try:client.health();marker=client.get_json(WebDAVBackupProvider.MARKER)
        except WebDAVError as exc:raise WorkspaceError(exc.code) from exc
        location_id=str(marker.get('location_id') or '').strip()
        if not location_id:raise WorkspaceError('WEBDAV_IDENTITY_INVALID')
        with self._backup_lock:
            root=Path(rec['path']);destinations=self._backup_destinations(root);idx=next((i for i,item in enumerate(destinations) if str(item.get('id'))==str(destination_id)),None)
            if idx is None:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
            current=dict(destinations[idx])
            if str(current.get('provider'))!='webdav':raise WorkspaceError('BACKUP_PROVIDER_MISMATCH')
            expected=str(current.get('location_id') or '')
            if expected and location_id!=expected:raise WorkspaceError('BACKUP_DESTINATION_IDENTITY_MISMATCH')
            old_credential=str(current.get('credential_id') or '');old_location=str(current.get('location_id') or '')
            new_credential=self._store_backup_credential(username,password)
            current.update({'endpoint':normalized,'credential_id':new_credential,'location_id':location_id})
            if old_location and old_location!=location_id:current['remote_history_repository_initialized']=False
            current.pop('last_error',None);current.pop('last_error_at',None)
            if name is not None and str(name).strip():current['name']=str(name).strip()[:120]
            destinations[idx]=current;self._save_backup_destinations(root,destinations)
            if old_credential and old_credential!=new_credential:self._delete_backup_credential(old_credential)
        return self._destination_status(rec,current)

    def update_backup_destination(self, destination_id:str, *, path_value:str|None=None, name:str|None=None, destination_kind:str|None=None, recovery_retention_profile:str|None=None, remote_history_retention:str|None=None, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        with self._backup_lock:
            root=Path(rec['path']);destinations=self._backup_destinations(root);idx=next((i for i,item in enumerate(destinations) if str(item.get('id'))==str(destination_id)),None)
            if idx is None:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
            current=dict(destinations[idx])
            if str(current.get('provider') or '')!='local_folder' and (path_value is not None or destination_kind is not None):raise WorkspaceError('BACKUP_PROVIDER_UPDATE_REQUIRES_RECONNECT')
            if destination_kind is not None:
                old_kind=str(current.get('destination_kind') or 'local')
                new_kind=_normalize_backup_destination_kind(destination_kind)
                if new_kind=='remote':raise WorkspaceError('BACKUP_DESTINATION_REMOTE_REQUIRES_PROVIDER_CONNECT')
                # If the name is still the auto-generated name for the previous
                # classification, refresh it for the new classification. Preserve
                # any custom/user-authored name verbatim.
                if path_value is None and name is None:
                    current_path=Path(str(current.get('path') or '')).expanduser().resolve()
                    old_auto=_destination_default_name(current_path,workspace_fs._volume_metadata(current_path),destination_kind=old_kind)
                    if str(current.get('name') or '')==old_auto:
                        current['name']=_destination_default_name(current_path,workspace_fs._volume_metadata(current_path),destination_kind=new_kind)
                current['destination_kind']=new_kind
            if path_value is not None:
                # Reusing the normal add validation keeps folder identity and marker rules identical.
                path=Path(str(path_value or '').strip()).expanduser().resolve()
                if not str(path_value or '').strip():raise WorkspaceError('BACKUP_DESTINATION_REQUIRED')
                path=self._validate_backup_destination_path(path)
                path.mkdir(parents=True,exist_ok=True);probe=path/f'.breadmaker-write-test-{uuid.uuid4().hex}'
                try:
                    with probe.open('w',encoding='utf-8') as fh:fh.write('Breadmaker backup destination verification');fh.flush();os.fsync(fh.fileno())
                finally:probe.unlink(missing_ok=True)
                marker_path=path/EXTERNAL_BACKUP_MARKER;marker=_read_external_backup_marker(path)
                if marker_path.exists() and not marker:raise WorkspaceError('EXTERNAL_BACKUP_LOCATION_MARKER_INVALID')
                location_id=str(marker.get('location_id')) if marker else uuid.uuid4().hex
                if not marker:_write_external_backup_marker(path,location_id)
                for item in destinations:
                    if item is destinations[idx]:continue
                    if str(item.get('location_id') or '')==location_id:raise WorkspaceError('BACKUP_DESTINATION_ALREADY_ADDED')
                volume=workspace_fs._volume_metadata(path);volume_root=Path(volume.get('root')).resolve() if volume.get('root') else Path(path.anchor or '/')
                try:relative=path.relative_to(volume_root).as_posix()
                except ValueError:relative=''
                current.update({'path':str(path),'location_id':location_id,'device_identity':_physical_disk_identity(path) or f'filesystem:{os.stat(path).st_dev}','volume_id':str(volume.get('id') or ''),'volume_relative_path':relative,'last_verified':None})
                # A folder change establishes a new destination identity. No publication
                # authority from the old physical location may leak into the replacement.
                for key in ('last_transferred_generation','last_verified_generation','last_published_generation','publication_generation','publication_state','retry_state'):
                    current.pop(key,None)
                # A write error from the previous folder must not make the newly validated
                # folder look broken before its first coordinator catch-up.
                current.pop('last_error',None);current.pop('last_error_at',None);current.pop('last_error_generation',None)
                if name is None:current['name']=_destination_default_name(path,volume,destination_kind=str(current.get('destination_kind') or 'local'))
            if name is not None:
                value=str(name or '').strip()[:120]
                if value:current['name']=value
            if recovery_retention_profile is not None:
                try: current['recovery_retention_profile']=normalize_recovery_retention_profile(recovery_retention_profile)
                except ValueError as exc: raise WorkspaceError('BACKUP_RETENTION_PROFILE_INVALID') from exc
            if remote_history_retention is not None:
                try:value=normalize_history_retention(remote_history_retention)
                except ValueError as exc:raise WorkspaceError('HISTORY_RETENTION_INVALID') from exc
                if str(current.get('provider') or '') not in {'webdav','google_drive'}:
                    raise WorkspaceError('HISTORY_RETENTION_REMOTE_DESTINATION_REQUIRED')
                current['remote_history_retention']=value
            destinations[idx]=current;self._save_backup_destinations(root,destinations)
        return self._destination_status(rec,current)

    def remove_backup_destination(self, destination_id:str, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        with self._backup_lock:
            root=Path(rec['path']);destinations=self._backup_destinations(root);removed=next((dict(item) for item in destinations if str(item.get('id'))==str(destination_id)),None);kept=[item for item in destinations if str(item.get('id'))!=str(destination_id)]
            if len(kept)==len(destinations):raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
            self._save_backup_destinations(root,kept)
            if removed and str(removed.get('provider')) in {'webdav','google_drive'}:self._delete_backup_credential(str(removed.get('credential_id') or ''))
        return {'ok':True}
