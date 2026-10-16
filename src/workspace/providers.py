from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backups.providers.webdav import WebDAVClient, WebDAVError
from backups.providers.google_drive import GoogleDriveClient, GoogleDriveError, GOOGLE_BACKUP_FOLDER_NAME, GOOGLE_FOLDER_MIME
from backups.publication import PUBLICATION_STATES, PUBLICATION_STATE_ORDER, PUBLICATION_SUFFIX, build_publication_manifest, validate_publication_manifest, publication_candidate, publication_filename, sha256_file, json_bytes as publication_json_bytes
from backups.retention import recovery_retention_partition, retention_authority_key, DEFAULT_RECOVERY_RETENTION_PROFILE
from .constants import HASH_CHUNK_BYTES
from .models import BackupProvider, WorkspaceError
from .filesystem import now
class LocalFolderBackupProvider(BackupProvider):
    provider_type='local_folder'
    def resolve(self, workspace, destination): return workspace._resolve_local_destination(destination)
    def store(self, workspace, source, rec, destination, source_token): return workspace._store_package_in_local_destination(source,rec,destination,source_token)
    def list_backups(self, workspace, rec, destination): return workspace._list_local_destination_backups(rec,destination)
    def discover(self, workspace, destination): return workspace._discover_local_destination_backups(destination)
    def materialize(self, workspace, destination, filename, target_dir): return workspace._materialize_local_destination_backup(destination,filename,target_dir)
    def erase_managed(self, workspace, destination, project_ids):
        resolved=workspace._resolve_local_destination(destination)
        if not resolved.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_ERASE_DESTINATION_UNAVAILABLE')
        return workspace._erase_local_destination(Path(resolved['path']),project_ids)

class WebDAVBackupProvider(BackupProvider):
    provider_type='webdav'
    MARKER='.breadmaker-remote-location.json'
    INDEX='.breadmaker-backups-index.json'

    def _client(self,workspace,destination):
        cred=workspace._backup_credential(str(destination.get('credential_id') or ''))
        if not cred:raise WorkspaceError('BACKUP_CREDENTIALS_MISSING')
        try:return WebDAVClient(str(destination.get('endpoint') or ''),str(cred.get('username') or ''),str(cred.get('password') or ''))
        except WebDAVError as exc:raise WorkspaceError(exc.code) from exc

    def _error_resolution(self,exc):
        code=getattr(exc,'code','')
        if code=='WEBDAV_UNAVAILABLE':return {'available':False,'attention':False,'reason':'not_connected'}
        return {'available':False,'attention':True,'reason':code.lower() or 'provider_error'}

    def resolve(self,workspace,destination):
        try:
            client=self._client(workspace,destination);client.health()
            try:marker=client.get_json(self.MARKER)
            except WebDAVError as exc:return self._error_resolution(exc)
            expected=str(destination.get('location_id') or '');actual=str(marker.get('location_id') or '')
            if not actual or actual!=expected:return {'available':False,'attention':True,'reason':'identity_mismatch'}
            return {'available':True,'attention':False,'reason':'','endpoint':client.endpoint}
        except WebDAVError as exc:return self._error_resolution(exc)
        except WorkspaceError as exc:
            code=str(exc);return {'available':False,'attention':True,'reason':code.lower()}

    def _index(self,client):
        raw=client.get_json(self.INDEX);rows=raw.get('backups') if isinstance(raw,dict) else None
        return [dict(x) for x in rows if isinstance(x,dict)] if isinstance(rows,list) else []

    def _write_index(self,client,rows):
        client.put_json(self.INDEX,{'version':2,'updated_at':now(),'backups':rows})

    def _publication_rows(self,client,project_id:str|None=None, *, strict:bool=False) -> list[dict[str,Any]]:
        """Read immutable publication authority; mutable index is cache only."""
        try:index=self._index(client)
        except WebDAVError:index=[]
        names=set()
        try:names.update(name for name in client.list_children() if name.endswith(PUBLICATION_SUFFIX))
        except WebDAVError as exc:
            if strict:raise WorkspaceError(getattr(exc,'code','BACKUP_AUTHORITY_UNAVAILABLE')) from exc
        names.update(str(row.get('publication_file') or '') for row in index if str(row.get('publication_file') or '').endswith(PUBLICATION_SUFFIX))
        published=[]
        prefix=f'breadmaker-{project_id}-' if project_id else ''
        for name in sorted(n for n in names if n):
            try:
                publication=validate_publication_manifest(client.get_json(name));candidate=publication_candidate(publication)
                if project_id and str(candidate.get('project_id') or '')!=str(project_id):continue
                candidate['publication_file']=name;published.append(candidate)
            except Exception as exc:
                if strict and (not project_id or str(name).startswith(prefix)):
                    raise WorkspaceError('BACKUP_PUBLICATION_AUTHORITY_CORRUPT') from exc
        return published


    @staticmethod
    def _cache_row(candidate:dict[str,Any], publication_file:str='') -> dict[str,Any]:
        return {
            'file':str(candidate.get('file') or ''),'project_id':str(candidate.get('project_id') or ''),
            'name':str(candidate.get('name') or 'Untitled Book'),'created_at':str(candidate.get('created_at') or ''),
            'bytes':int(candidate.get('bytes') or 0),'fingerprint':str(candidate.get('fingerprint') or ''),
            'project_generation':int(candidate.get('project_generation') or 0),'publication_file':str(publication_file or ''),
        }

    def _delete_candidate(self,client,row:dict[str,Any]) -> None:
        publication_file=str(row.get('publication_file') or '')
        if publication_file:
            # Remove authority first. A crash can leave an orphan payload, never a
            # manifest pointing at bytes that were physically removed.
            client.delete(publication_file,missing_ok=True)
        client.delete(str(row.get('file') or ''),missing_ok=True)

    def store(self,workspace,source,rec,destination,source_token):
        client=self._client(workspace,destination);root=Path(rec['path']);did=str(destination.get('id') or '')
        source_info=workspace.verify_package(Path(source));generation=int(source_info.get('project_generation') or 0)
        published=self._publication_rows(client);existing=published
        same=[row for row in existing if str(row.get('project_id'))==str(rec['id']) and int(row.get('project_generation') or 0)==generation]
        if same:
            source_state=str(source_info.get('canonical_state_hash') or '')
            known={str(row.get('canonical_state_hash') or '') for row in same}
            if not source_state or '' in known or any(value!=source_state for value in known):
                raise WorkspaceError('BACKUP_SAME_GENERATION_IDENTITY_UNCERTAIN')
            # Explicit retry may converge an ambiguous remote commit, but only after
            # fully re-verifying the already-published artifact bytes.
            workspace._observe_destination_authority(rec,destination,full_verify=True)
            winner=max(same,key=retention_authority_key)
            return {'file':str(winner.get('file') or ''),'verified':True,'created_at':str(winner.get('created_at') or now()),
                    'bytes':int(winner.get('bytes') or Path(source).stat().st_size),'fingerprint':str(winner.get('fingerprint') or ''),
                    'project_generation':generation,'source_token':source_token,'remote':True,'idempotent':True,'publication_state':'published'}

        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f');filename=f"breadmaker-{rec['id']}-{stamp}.breadmaker";tmp=filename+'.uploading'
        size=Path(source).stat().st_size;publication_file=publication_filename(filename);publication_tmp=publication_file+'.publishing'
        try:
            client.put_file(tmp,Path(source));client.move(tmp,filename,overwrite=False)
            workspace._record_backup_state(root,did,generation,'TRANSFERRED')
            with tempfile.TemporaryDirectory(prefix='breadmaker-webdav-verify-') as td:
                verify_path=Path(td)/filename;client.get_file(filename,verify_path);info=workspace.verify_package(verify_path)
                package_hash=sha256_file(verify_path,HASH_CHUNK_BYTES)
            workspace._record_backup_state(root,did,generation,'DESTINATION_VERIFIED')

            published=self._publication_rows(client);existing=published
            higher=[row for row in existing if str(row.get('project_id'))==str(rec['id']) and int(row.get('project_generation') or 0)>generation]
            if generation and higher:
                client.delete(filename,missing_ok=True);winner=max(higher,key=retention_authority_key)
                return {'file':str(winner.get('file') or filename),'verified':True,'created_at':str(winner.get('created_at') or info.get('created_at') or now()),
                        'bytes':int(winner.get('bytes') or size),'fingerprint':str(winner.get('fingerprint') or ''),
                        'project_generation':int(winner.get('project_generation') or generation),'source_token':source_token,
                        'remote':True,'discarded_stale':True,'publication_state':'published'}

            publication=build_publication_manifest(package_file=filename,package_sha256=package_hash,package_bytes=size,
                                                   package_info=info,package_locator=filename,published_at=now())
            # Immutable publication manifest LAST. MOVE with Overwrite:F makes retry
            # races idempotent rather than last-writer-wins.
            client.put_json(publication_tmp,publication)
            try:client.move(publication_tmp,publication_file,overwrite=False)
            except WebDAVError as exc:
                if getattr(exc,'status',None)!=412:raise
                current=validate_publication_manifest(client.get_json(publication_file))
                if int(current.get('project_generation') or 0)!=generation or str(current.get('package_sha256') or '')!=package_hash:raise
                client.delete(publication_tmp,missing_ok=True)

            candidate=publication_candidate(publication);candidate['publication_file']=publication_file
            # Index is a rebuildable cache only; publication success must not depend on
            # a cache write succeeding after the immutable manifest already exists.
            try:
                index=self._index(client);index=[r for r in index if str(r.get('file') or '')!=filename]
                index.insert(0,self._cache_row(candidate,publication_file));self._write_index(client,index)
            except WebDAVError:pass

            published=self._publication_rows(client);rows=published
            project_rows=[row for row in rows if str(row.get('project_id'))==str(rec['id'])]
            # Generation-aware exact-state dedupe and retention only touch published
            # candidates. Manifest is deleted before payload for every new record.
            duplicates=[row for row in project_rows if str(row.get('file') or '')!=filename and str(row.get('fingerprint') or '')==str(candidate.get('fingerprint') or '') and int(row.get('project_generation') or 0)<=generation]
            for row in duplicates:
                try:self._delete_candidate(client,row)
                except WebDAVError:pass
            published=self._publication_rows(client);project_rows=[row for row in published if str(row.get('project_id'))==str(rec['id'])]
            _,prune=recovery_retention_partition(project_rows,str(destination.get('recovery_retention_profile') or DEFAULT_RECOVERY_RETENTION_PROFILE))
            for row in prune:
                if str(row.get('file') or '')==filename:continue
                try:self._delete_candidate(client,row)
                except WebDAVError:pass
            try:
                published=self._publication_rows(client);self._write_index(client,[self._cache_row(row,str(row.get('publication_file') or '')) for row in published])
            except WebDAVError:pass
            return {'file':filename,'verified':True,'created_at':candidate['created_at'],'bytes':size,'fingerprint':candidate['fingerprint'],
                    'project_generation':generation,'source_token':source_token,'remote':True,'publication_state':'published'}
        except Exception:
            try:client.delete(tmp,missing_ok=True)
            except Exception:pass
            try:client.delete(publication_tmp,missing_ok=True)
            except Exception:pass
            # If publication exists, never delete its payload from an error cleanup.
            try:
                published=self._publication_rows(client)
                if not any(str(row.get('file') or '')==filename for row in published):client.delete(filename,missing_ok=True)
            except Exception:pass
            raise

    def discover(self,workspace,destination):
        client=self._client(workspace,destination);published=self._publication_rows(client)
        published.sort(key=retention_authority_key,reverse=True);return published

    def list_backups(self,workspace,rec,destination):
        return [r for r in self.discover(workspace,destination) if str(r.get('project_id'))==str(rec['id'])]

    def materialize(self,workspace,destination,filename,target_dir):
        if Path(filename).name!=filename or not filename.endswith('.breadmaker'):raise WorkspaceError('INVALID_BACKUP_NAME')
        client=self._client(workspace,destination);published=self._publication_rows(client)
        row=next((row for row in published if str(row.get('file') or '')==filename),None)
        if not row:raise WorkspaceError('BACKUP_NOT_PUBLISHED')
        target=Path(target_dir)/filename
        try:client.get_file(filename,target)
        except WebDAVError as exc:raise WorkspaceError(exc.code) from exc
        expected=str(row.get('package_sha256') or '')
        if expected and sha256_file(target,HASH_CHUNK_BYTES).lower()!=expected.lower():raise WorkspaceError('BACKUP_PUBLICATION_HASH_MISMATCH')
        return target

    def erase_managed(self,workspace,destination,project_ids):
        client=self._client(workspace,destination);published=self._publication_rows(client);removed=0;deleted_names=set()
        for row in published:
            if str(row.get('project_id') or '') in project_ids:
                publication_file=str(row.get('publication_file') or '');package_file=str(row.get('file') or '')
                self._delete_candidate(client,row);deleted_names.update(x for x in (publication_file,package_file) if x);removed+=1
        # Explicit privacy erasure also removes provider-created staging/orphan bytes
        # that were never published. Their project identity is encoded in the managed
        # filename even when a crash happened before a publication manifest existed.
        try:
            names=client.list_children()
            for pid in project_ids:
                prefix=f'breadmaker-{pid}-'
                for name in names:
                    if name in deleted_names or not name.startswith(prefix):continue
                    if '.breadmaker' not in name:continue
                    client.delete(name,missing_ok=True);deleted_names.add(name);removed+=1
        except WebDAVError:
            # Listing is required for orphan cleanup. Fail closed rather than claim an
            # external erase succeeded while unknown provider-created bytes may remain.
            raise WorkspaceError('EXTERNAL_BACKUP_ERASE_FAILED')
        # Explicit privacy erasure is intentionally outside the Remote History
        # replication transport (which has no delete capability). Delete the remote
        # History workspace collection only from this provider privacy boundary.
        for pid in project_ids:
            base=f'history/{pid}'
            try:
                # Root repository/writer authority first. If deletion is interrupted,
                # the remaining bytes cannot be mistaken for a writable repository.
                client.delete(f'{base}/writer-authority.json',missing_ok=True)
                client.delete(f'{base}/repository.json',missing_ok=True)
                for kind in ('revisions','checkpoints','objects'):
                    collection=f'{base}/{kind}'
                    try:names=client.list_children(collection)
                    except WebDAVError as exc:
                        if exc.code=='WEBDAV_NOT_FOUND':names=[]
                        else:raise
                    for name in names:
                        client.delete(f'{collection}/{name}',missing_ok=True);removed+=1
                    client.delete(collection,missing_ok=True)
                client.delete(base,missing_ok=True)
                # Collection scaffolding is not counted as a removed managed object.
                # Keep the shared history root for other projects.
            except WebDAVError as exc:
                if exc.code!='WEBDAV_NOT_FOUND':raise WorkspaceError('EXTERNAL_BACKUP_ERASE_FAILED') from exc
        try:
            remaining=[row for row in published if str(row.get('project_id') or '') not in project_ids]
            self._write_index(client,[self._cache_row(row,str(row.get('publication_file') or '')) for row in remaining])
        except WebDAVError:pass
        return removed


class GoogleDriveBackupProvider(BackupProvider):
    provider_type='google_drive'

    def _client(self,workspace,destination):
        cred=workspace._backup_credential(str(destination.get('credential_id') or ''))
        if not cred or str(cred.get('kind') or '')!='google_oauth':raise WorkspaceError('BACKUP_CREDENTIALS_MISSING')
        client_id=str(cred.get('client_id') or '');refresh_token=str(cred.get('refresh_token') or '');client_secret=str(cred.get('client_secret') or '')
        try:return GoogleDriveClient(client_id,refresh_token,client_secret)
        except GoogleDriveError as exc:raise WorkspaceError(exc.code) from exc

    def _error_resolution(self,exc):
        code=getattr(exc,'code','') or str(exc)
        if code=='GOOGLE_UNAVAILABLE':return {'available':False,'attention':False,'reason':'not_connected'}
        reason={'GOOGLE_AUTH_FAILED':'google_auth_failed','GOOGLE_PERMISSION_DENIED':'google_permission_denied','GOOGLE_TLS_FAILED':'google_tls_failed',
                'GOOGLE_REDIRECT_FORBIDDEN':'google_redirect_forbidden','GOOGLE_IDENTITY_MISMATCH':'identity_mismatch','GOOGLE_NOT_FOUND':'identity_mismatch',
                'BACKUP_CREDENTIALS_MISSING':'backup_credentials_missing'}.get(code,str(code or 'provider_error').lower())
        return {'available':False,'attention':True,'reason':reason}

    def resolve(self,workspace,destination):
        try:
            client=self._client(workspace,destination);client.check_root(str(destination.get('folder_id') or ''),str(destination.get('location_id') or ''))
            return {'available':True,'attention':False,'reason':'','folder_id':str(destination.get('folder_id') or ''),'display_location':f'Google Drive / {GOOGLE_BACKUP_FOLDER_NAME}'}
        except GoogleDriveError as exc:return self._error_resolution(exc)
        except WorkspaceError as exc:return self._error_resolution(exc)


    @staticmethod
    def _publication_candidate(row):
        props=row.get('appProperties') if isinstance(row.get('appProperties'),dict) else {}
        return {'file':str(props.get('breadmaker_package_name') or ''),'provider_file_id':str(props.get('breadmaker_package_id') or ''),
                'candidate':True,'verified':False,'verification_status':'not_verified','publication_state':'published',
                'project_id':str(props.get('breadmaker_project_id') or ''),'name':str(props.get('breadmaker_project_name') or 'Recovered Project'),
                'created_at':str(props.get('breadmaker_created_at') or row.get('createdTime') or ''),'bytes':int(props.get('breadmaker_package_bytes') or 0),
                'fingerprint':str(props.get('breadmaker_fingerprint') or ''),'project_generation':int(props.get('breadmaker_generation') or 0),
                'package_sha256':str(props.get('breadmaker_package_sha256') or ''),'canonical_state_hash':str(props.get('breadmaker_canonical_state_hash') or ''),'_publication_id':str(row.get('id') or ''),
                'publication_file':str(row.get('name') or ''),'workday':str(props.get('breadmaker_workday') or '')}

    def _published_rows(self,client,folder_id:str,project_id:str|None=None, *, strict:bool=False) -> list[dict[str,Any]]:
        publications=client.list_publications(folder_id,project_id)
        published=[]
        for row in publications:
            try:
                candidate=self._publication_candidate(row)
                if project_id and str(candidate.get('project_id') or '')!=str(project_id):continue
                published.append(candidate)
            except Exception as exc:
                if strict:raise WorkspaceError('BACKUP_PUBLICATION_AUTHORITY_CORRUPT') from exc
        return published


    def _delete_candidate(self,client,row:dict[str,Any]) -> None:
        publication_id=str(row.get('_publication_id') or '')
        if publication_id:client.delete_file(publication_id,missing_ok=True)
        payload_id=str(row.get('provider_file_id') or row.get('_id') or '')
        if payload_id:client.delete_file(payload_id,missing_ok=True)

    def store(self,workspace,source,rec,destination,source_token):
        client=self._client(workspace,destination);folder_id=str(destination.get('folder_id') or '');root=Path(rec['path']);did=str(destination.get('id') or '')
        source_info=workspace.verify_package(Path(source));generation=int(source_info.get('project_generation') or 0)
        published=self._published_rows(client,folder_id,str(rec['id']));existing=published
        same=[row for row in existing if int(row.get('project_generation') or 0)==generation]
        if same:
            source_state=str(source_info.get('canonical_state_hash') or '')
            known={str(row.get('canonical_state_hash') or '') for row in same}
            if not source_state or '' in known or any(value!=source_state for value in known):
                raise WorkspaceError('BACKUP_SAME_GENERATION_IDENTITY_UNCERTAIN')
            # Explicit retry may converge an ambiguous remote commit, but only after
            # fully re-verifying the already-published artifact bytes.
            workspace._observe_destination_authority(rec,destination,full_verify=True)
            winner=max(same,key=retention_authority_key)
            return {'file':str(winner.get('file') or ''),'verified':True,'created_at':str(winner.get('created_at') or now()),
                    'bytes':int(winner.get('bytes') or Path(source).stat().st_size),'fingerprint':str(winner.get('fingerprint') or ''),
                    'project_generation':generation,'source_token':source_token,'remote':True,'provider':'google_drive','idempotent':True,'publication_state':'published'}

        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f');filename=f"breadmaker-{rec['id']}-{stamp}.breadmaker";uploaded_id='';publication_id=''
        try:
            uploaded=client.upload_file(Path(source),folder_id,filename,{'breadmaker_project_id':str(rec['id']),'breadmaker_generation':str(generation),'breadmaker_kind':'uploading'});uploaded_id=str(uploaded.get('id') or '')
            if not uploaded_id:raise WorkspaceError('GOOGLE_UPLOAD_INCOMPLETE')
            workspace._record_backup_state(root,did,generation,'TRANSFERRED')
            with tempfile.TemporaryDirectory(prefix='breadmaker-gdrive-verify-') as td:
                verify_path=Path(td)/filename;client.download_file(uploaded_id,verify_path);info=workspace.verify_package(verify_path)
                package_hash=sha256_file(verify_path,HASH_CHUNK_BYTES)
            workspace._record_backup_state(root,did,generation,'DESTINATION_VERIFIED')
            fingerprint=str(info.get('project_fingerprint') or '');created_at=str(info.get('created_at') or now());reason=str(info.get('reason') or 'automatic')
            # Payload metadata is intentionally non-authoritative. Discovery does not
            # advertise backup_payload objects without a separate publication record.
            client.update_package_metadata(uploaded_id,project_id=str(info.get('project_id') or rec['id']),project_name=str(info.get('name') or rec.get('name') or 'Untitled Book'),
                                           fingerprint=fingerprint,created_at=created_at,reason=reason,project_generation=generation,kind='backup_payload')

            published=self._published_rows(client,folder_id,str(rec['id']));existing=published
            higher=[row for row in existing if int(row.get('project_generation') or 0)>generation]
            if generation and higher:
                client.delete_file(uploaded_id,missing_ok=True);uploaded_id='';winner=max(higher,key=retention_authority_key)
                return {'file':str(winner.get('file') or filename),'verified':True,'created_at':str(winner.get('created_at') or created_at),
                        'bytes':int(winner.get('bytes') or Path(source).stat().st_size),'fingerprint':str(winner.get('fingerprint') or fingerprint),
                        'project_generation':int(winner.get('project_generation') or generation),'source_token':source_token,'remote':True,
                        'provider':'google_drive','discarded_stale':True,'publication_state':'published'}

            publication_name=publication_filename(filename)
            publication=client.create_publication_record(folder_id,publication_name=publication_name,project_id=str(info.get('project_id') or rec['id']),
                                                         project_name=str(info.get('name') or rec.get('name') or 'Untitled Book'),package_id=uploaded_id,
                                                         package_name=filename,fingerprint=fingerprint,created_at=created_at,project_generation=generation,
                                                         package_sha256=package_hash,package_bytes=Path(source).stat().st_size,
                                                         workday=str((info.get('recovery_metadata') or {}).get('workday') or ''),
                                                         canonical_state_hash=str(info.get('canonical_state_hash') or ''))
            publication_id=str(publication.get('id') or '')
            if not publication_id:raise WorkspaceError('GOOGLE_PUBLICATION_INCOMPLETE')
            # Provider metadata is cache/display data only after immutable publication succeeds.
            # Recovery authority remains the separate publication record.
            try:
                client.update_package_metadata(uploaded_id,project_id=str(info.get('project_id') or rec['id']),project_name=str(info.get('name') or rec.get('name') or 'Untitled Book'),
                                               fingerprint=fingerprint,created_at=created_at,reason=reason,project_generation=generation,kind='backup_package')
            except GoogleDriveError:
                pass

            # A lower generation may race with a newer publisher between the pre-check
            # and manifest creation. It can be visible briefly, but immutable ordering
            # means it can never become newest; remove it conservatively once detected.
            published=self._published_rows(client,folder_id,str(rec['id']));rows=published
            current=next((row for row in published if str(row.get('_publication_id') or '')==publication_id),None)
            higher=[row for row in rows if int(row.get('project_generation') or 0)>generation]
            if generation and higher:
                if current:self._delete_candidate(client,current)
                else:
                    client.delete_file(publication_id,missing_ok=True);client.delete_file(uploaded_id,missing_ok=True)
                publication_id='';uploaded_id='';winner=max(higher,key=retention_authority_key)
                return {'file':str(winner.get('file') or filename),'verified':True,'created_at':str(winner.get('created_at') or created_at),
                        'bytes':int(winner.get('bytes') or Path(source).stat().st_size),'fingerprint':str(winner.get('fingerprint') or fingerprint),
                        'project_generation':int(winner.get('project_generation') or generation),'source_token':source_token,'remote':True,
                        'provider':'google_drive','discarded_stale':True,'publication_state':'published'}

            current=current or {'file':filename,'provider_file_id':uploaded_id,'_publication_id':publication_id,'fingerprint':fingerprint,
                                'project_generation':generation,'created_at':created_at,'bytes':Path(source).stat().st_size,'project_id':str(rec['id'])}
            duplicates=[row for row in rows if str(row.get('file') or '')!=filename and fingerprint and str(row.get('fingerprint') or '')==fingerprint and int(row.get('project_generation') or 0)<=generation]
            for row in duplicates:
                try:self._delete_candidate(client,row)
                except GoogleDriveError:pass
            published=self._published_rows(client,folder_id,str(rec['id']));project_rows=published
            _,prune=recovery_retention_partition(project_rows,str(destination.get('recovery_retention_profile') or DEFAULT_RECOVERY_RETENTION_PROFILE))
            for row in prune:
                if str(row.get('_publication_id') or '')==publication_id:continue
                try:self._delete_candidate(client,row)
                except GoogleDriveError:pass
            return {'file':filename,'verified':True,'created_at':created_at,'bytes':Path(source).stat().st_size,'fingerprint':fingerprint,
                    'project_generation':generation,'source_token':source_token,'remote':True,'provider':'google_drive','publication_state':'published'}
        except Exception:
            # Never delete a payload if an immutable publication record for it exists.
            if uploaded_id:
                try:
                    published=self._published_rows(client,folder_id,str(rec['id']))
                    if not any(str(row.get('provider_file_id') or '')==uploaded_id for row in published):client.delete_file(uploaded_id,missing_ok=True)
                except Exception:pass
            raise

    def discover(self,workspace,destination):
        client=self._client(workspace,destination);folder_id=str(destination.get('folder_id') or '')
        published=self._published_rows(client,folder_id);published.sort(key=retention_authority_key,reverse=True);return published

    def list_backups(self,workspace,rec,destination):
        return [row for row in self.discover(workspace,destination) if str(row.get('project_id'))==str(rec['id'])]

    def materialize(self,workspace,destination,filename,target_dir):
        if Path(filename).name!=filename or not filename.endswith('.breadmaker'):raise WorkspaceError('INVALID_BACKUP_NAME')
        client=self._client(workspace,destination);folder_id=str(destination.get('folder_id') or '')
        published=self._published_rows(client,folder_id);row=next((r for r in published if str(r.get('file') or '')==filename),None)
        if not row:raise WorkspaceError('BACKUP_NOT_PUBLISHED')
        file_id=str(row.get('provider_file_id') or row.get('_id') or '')
        if not file_id:raise WorkspaceError('BACKUP_NOT_FOUND')
        target=Path(target_dir)/filename
        try:client.download_file(file_id,target)
        except GoogleDriveError as exc:raise WorkspaceError(exc.code) from exc
        expected=str(row.get('package_sha256') or '')
        if expected and sha256_file(target,HASH_CHUNK_BYTES).lower()!=expected.lower():raise WorkspaceError('BACKUP_PUBLICATION_HASH_MISMATCH')
        return target

    def erase_managed(self,workspace,destination,project_ids):
        client=self._client(workspace,destination);folder_id=str(destination.get('folder_id') or '');removed=0;seen=set()
        # Delete publication records/payloads plus any provider-created staging/orphan
        # objects carrying the project id. Publication manifests are removed first by
        # _delete_candidate; leftover managed files are then privacy-cleaned by id.
        published=self._published_rows(client,folder_id)
        for row in published:
            if str(row.get('project_id') or '') in project_ids:
                publication_id=str(row.get('_publication_id') or '')
                payload_id=str(row.get('provider_file_id') or row.get('_id') or '')
                self._delete_candidate(client,row);seen.update(x for x in (publication_id,payload_id) if x);removed+=1
        for pid in project_ids:
            for row in client.list_managed_project_files(folder_id,str(pid)):
                rid=str(row.get('id') or '')
                if rid and rid not in seen:
                    client.delete_file(rid,missing_ok=True);seen.add(rid);removed+=1
        # Remote History uses a separate folder namespace. Normal replication has no
        # delete API; explicit privacy erasure may remove the workspace folder here.
        for pid in project_ids:
            history_rows=client.list_history_files(folder_id,str(pid))
            # Root repository/writer authority first, then revision publication
            # authority, then dependent objects. Interrupted privacy erasure therefore
            # cannot leave a repository Breadmaker may legitimately continue writing.
            def _privacy_order(row):
                kind=str((row.get('appProperties') or {}).get('breadmaker_history_kind') or '')
                if kind in {'repository','writer_authority'}:return 0
                if kind=='revision_publication':return 1
                return 2
            history_rows.sort(key=_privacy_order)
            for row in history_rows:
                rid=str(row.get('id') or '')
                if rid:client.delete_file(rid,missing_ok=True);removed+=1
        return removed
