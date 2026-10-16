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

class BackupAuthorityMixin:
    @staticmethod
    def _validate_publication_set(rows:list[dict[str,Any]]) -> None:
        by_generation:dict[int,set[str]]={}
        for row in rows:
            generation=int(row.get('project_generation') or 0)
            if generation<=0:continue
            identity=str(row.get('canonical_state_hash') or '').strip()
            by_generation.setdefault(generation,set()).add(identity)
        for identities in by_generation.values():
            if len(identities)>1 or '' in identities:
                raise WorkspaceError('BACKUP_SAME_GENERATION_IDENTITY_UNCERTAIN')

    def _observe_destination_authority(self, rec:dict[str,Any], destination:dict[str,Any], *, full_verify:bool=False) -> dict[str,Any]:
        """Read live immutable authority. Local manifest cache is never proof by itself."""
        provider_name=str(destination.get('provider') or '')
        project_id=str(rec.get('id') or '')
        provider=self._backup_provider(destination)
        if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        resolved=provider.resolve(self,destination)
        if not resolved.get('available'):
            return {'available':False,'observed_generation':0,'rows':[],'reason':str(resolved.get('reason') or 'unavailable')}
        rows:list[dict[str,Any]]=[]
        if provider_name=='local_folder':
            target=Path(resolved['path'])
            rows=self._local_publication_rows(target,project_id,strict=True,require_payload=False)
            for row in rows:
                package=Path(row['path'])
                if not package.is_file() or int(package.stat().st_size)!=int(row.get('bytes') or -1):
                    row['_payload_missing']=True
        elif provider_name=='webdav':
            client=provider._client(self,destination)
            rows=provider._publication_rows(client,project_id,strict=True)
            try:children=set(client.list_children())
            except WebDAVError as exc:raise WorkspaceError(getattr(exc,'code','BACKUP_AUTHORITY_UNAVAILABLE')) from exc
            for row in rows:
                if str(row.get('file') or '') not in children:row['_payload_missing']=True
        elif provider_name=='google_drive':
            client=provider._client(self,destination);folder_id=str(destination.get('folder_id') or '')
            rows=provider._published_rows(client,folder_id,project_id,strict=True)
            managed=client.list_managed_project_files(folder_id,project_id);ids={str(x.get('id') or ''):x for x in managed}
            for row in rows:
                payload_id=str(row.get('provider_file_id') or '')
                if not payload_id or payload_id not in ids:
                    row['_payload_missing']=True
                else:
                    try:size=int(ids[payload_id].get('size') or 0)
                    except (TypeError,ValueError):size=-1
                    if size>=0 and size!=int(row.get('bytes') or -1):row['_payload_missing']=True
        else:
            raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        self._validate_publication_set(rows)
        observed=max((int(row.get('project_generation') or 0) for row in rows if not row.get('_payload_missing')),default=0)
        if full_verify and rows:
            target_generation=int(destination.get('last_published_generation') or observed or 0)
            candidates=[row for row in rows if int(row.get('project_generation') or 0)==target_generation]
            if not candidates and observed:candidates=[row for row in rows if int(row.get('project_generation') or 0)==observed]
            for row in candidates:
                if row.get('_payload_missing'):raise WorkspaceError('BACKUP_PUBLISHED_PAYLOAD_MISSING')
                if provider_name=='local_folder':
                    package=Path(row['path']);actual=sha256_file(package,HASH_CHUNK_BYTES)
                    if actual.lower()!=str(row.get('package_sha256') or '').lower():raise WorkspaceError('BACKUP_PUBLICATION_HASH_MISMATCH')
                    info=self.verify_package(package)
                else:
                    with tempfile.TemporaryDirectory(prefix='breadmaker-authority-scrub-') as td:
                        package=Path(td)/Path(str(row.get('file') or 'backup.breadmaker')).name
                        if provider_name=='webdav':provider._client(self,destination).get_file(str(row.get('file') or ''),package)
                        else:provider._client(self,destination).download_file(str(row.get('provider_file_id') or ''),package)
                        actual=sha256_file(package,HASH_CHUNK_BYTES)
                        if actual.lower()!=str(row.get('package_sha256') or '').lower():raise WorkspaceError('BACKUP_PUBLICATION_HASH_MISMATCH')
                        info=self.verify_package(package)
                if str(info.get('project_id') or '')!=project_id:raise WorkspaceError('BACKUP_PUBLICATION_PROJECT_MISMATCH')
                if int(info.get('project_generation') or 0)!=int(row.get('project_generation') or 0):raise WorkspaceError('BACKUP_PUBLICATION_GENERATION_MISMATCH')
                expected_state=str(row.get('canonical_state_hash') or '')
                if expected_state and str(info.get('canonical_state_hash') or '')!=expected_state:raise WorkspaceError('BACKUP_PUBLICATION_STATE_MISMATCH')
        return {'available':True,'observed_generation':observed,'rows':rows,'reason':''}

    def _revoke_corrupt_destination_authority(self, rec:dict[str,Any], destination:dict[str,Any], generation:int) -> int:
        """Remove immutable authority first. Failure is fatal; never publish beside it."""
        provider_name=str(destination.get('provider') or '');project_id=str(rec.get('id') or '')
        provider=self._backup_provider(destination)
        if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        removed=0
        try:
            if provider_name=='local_folder':
                resolved=self._resolve_local_destination(destination)
                rows=self._local_publication_rows(Path(resolved['path']),project_id,strict=True,require_payload=False)
                for row in rows:
                    if int(row.get('project_generation') or 0)!=int(generation):continue
                    pub=Path(row['publication_path'])
                    try:pub.unlink()
                    except OSError as exc:raise WorkspaceError('BACKUP_INTEGRITY_REVOKE_FAILED') from exc
                    removed+=1
                    try:Path(row['path']).unlink()
                    except OSError:pass
            elif provider_name=='webdav':
                client=provider._client(self,destination);rows=provider._publication_rows(client,project_id,strict=True)
                for row in rows:
                    if int(row.get('project_generation') or 0)==int(generation):provider._delete_candidate(client,row);removed+=1
            elif provider_name=='google_drive':
                client=provider._client(self,destination);rows=provider._published_rows(client,str(destination.get('folder_id') or ''),project_id,strict=True)
                for row in rows:
                    if int(row.get('project_generation') or 0)==int(generation):provider._delete_candidate(client,row);removed+=1
            else:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        except WorkspaceError:raise
        except Exception as exc:raise WorkspaceError('BACKUP_INTEGRITY_REVOKE_FAILED') from exc
        if removed<=0:raise WorkspaceError('BACKUP_INTEGRITY_REVOKE_FAILED')
        return removed

    def _destination_status(self, rec:dict[str,Any], destination:dict[str,Any], source_token:str|None=None) -> dict[str,Any]:
        out=dict(destination);out.setdefault('provider','local_folder');out.setdefault('destination_kind','local')
        root=Path(rec['path']);current_generation=_project_generation(root)
        try:_normalize_backup_destination_kind(out.get('destination_kind'))
        except WorkspaceError:
            out.update({'available':False,'attention':True,'status':'needs_attention','same_disk':None,'resolved_path':'','reason':'unsupported_destination_kind','up_to_date':False,'current_generation':current_generation})
            return out
        provider=self._backup_provider(out)
        if provider is None:
            out.update({'available':False,'attention':True,'status':'needs_attention','same_disk':None,'resolved_path':'','reason':'unsupported_provider','current_generation':current_generation})
            return out
        resolved=provider.resolve(self,out);path=resolved.get('path')
        available=bool(resolved.get('available'));attention=bool(resolved.get('attention'))
        last_error=str(out.get('last_error') or '').strip()
        same_disk=_same_physical_disk(path,self.root) if available and path else None
        last=out.get('last_verified') if isinstance(out.get('last_verified'),dict) else None
        verified_generation=int(out.get('last_verified_generation') or 0)
        published_generation=int(out.get('last_published_generation') or 0)
        transferred_generation=int(out.get('last_transferred_generation') or 0)
        observed_generation=0;authority_reason=''
        if available:
            try:
                observed=self._observe_destination_authority(rec,out,full_verify=False)
                observed_generation=int(observed.get('observed_generation') or 0)
            except Exception as exc:
                attention=True;authority_reason=str(getattr(exc,'code','') or str(exc) or 'backup_authority_unavailable').lower()
        generation_ahead=bool(current_generation and max(published_generation,observed_generation)>current_generation)
        if generation_ahead:attention=True;authority_reason='generation_ahead'
        if available and published_generation>0 and observed_generation<published_generation:
            attention=True;authority_reason=authority_reason or 'backup_authority_missing'
        elif available and observed_generation>published_generation and not generation_ahead:
            authority_reason=authority_reason or 'publication_confirmation_pending'
        # Freshness requires agreement between local state-machine cache and live
        # immutable destination authority. Neither side alone can claim protection.
        up_to_date=bool(available and not attention and not last_error and current_generation>0 and published_generation==current_generation and observed_generation==current_generation)
        if attention: status='needs_attention'
        elif not available: status='unavailable'
        elif last_error: status='needs_attention';attention=True
        elif out.get('id') in self._backup_in_progress: status='backing_up'
        elif up_to_date: status='up_to_date'
        else: status='pending'
        out.update({
            'available':available,'attention':attention,'status':status,'same_disk':same_disk,
            'resolved_path':str(path) if path else '',
            'reason':authority_reason or str(resolved.get('reason') or ('backup_failed' if available and last_error else '')),
            'up_to_date':up_to_date,'last_verified':last,
            'current_generation':current_generation,'transferred_generation':transferred_generation,
            'verified_generation':verified_generation,'published_generation':published_generation,
            'observed_published_generation':observed_generation,
            'publication_state':str(out.get('publication_state') or ('PUBLISHED' if published_generation else '')),
            'publication_generation':int(out.get('publication_generation') or published_generation or 0),
            'retry_state':dict(out.get('retry_state') or {}),
        })
        return out
