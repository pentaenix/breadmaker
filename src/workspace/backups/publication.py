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

class BackupPublicationMixin:
    def _local_publication_rows(self, target:Path, project_id:str|None=None, *, strict:bool=False, require_payload:bool=True) -> list[dict[str,Any]]:
        """Read immutable publication records. Discovery may be tolerant; freshness is strict."""
        target=Path(target);rows=[]
        pattern=f"breadmaker-{project_id}-*.breadmaker{PUBLICATION_SUFFIX}" if project_id else f"breadmaker-*.breadmaker{PUBLICATION_SUFFIX}"
        for manifest_path in target.glob(pattern):
            try:
                publication=validate_publication_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
                if project_id and str(publication.get('project_id') or '')!=str(project_id):continue
                package=target/str(publication['package_file'])
                if require_payload and not package.is_file():continue
                candidate=publication_candidate(publication)
                candidate.update({'path':package,'publication_path':manifest_path,'mtime_ns':manifest_path.stat().st_mtime_ns})
                rows.append(candidate)
            except Exception as exc:
                if strict:raise WorkspaceError('BACKUP_PUBLICATION_AUTHORITY_CORRUPT') from exc
        return rows

    def _publish_local_package(self, target:Path, package:Path, info:dict[str,Any]) -> dict[str,Any]:
        package=Path(package);target=Path(target)
        manifest=build_publication_manifest(
            package_file=package.name,package_sha256=sha256_file(package,HASH_CHUNK_BYTES),
            package_bytes=package.stat().st_size,package_info=info,published_at=now(),
        )
        final=target/publication_filename(package.name);tmp=final.with_name(f'.{final.name}.{uuid.uuid4().hex}.tmp')
        try:
            with tmp.open('wb') as fh:
                fh.write(publication_json_bytes(manifest));fh.flush();os.fsync(fh.fileno())
            _durable_replace(tmp,final)
        finally:
            tmp.unlink(missing_ok=True)
        return manifest

    @staticmethod
    def _delete_local_published_package(row:dict[str,Any]) -> None:
        # Publication record first: after this point a crash can only leak payload bytes,
        # never leave a manifest advertising a package we are about to delete.
        publication_path=row.get('publication_path')
        if publication_path:
            try:Path(publication_path).unlink()
            except OSError:return
        try:Path(row['path']).unlink()
        except OSError:pass

    def _store_package_in_local_destination(self, source:Path, rec:dict[str,Any], destination:dict[str,Any], source_token:str) -> dict[str,Any]:
        resolved=self._resolve_local_destination(destination)
        if not resolved.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        target=Path(resolved['path']);did=str(destination.get('id') or '');root=Path(rec['path'])
        source_info=self.verify_package(Path(source));generation=int(source_info.get('project_generation') or 0)
        published=self._local_publication_rows(target,str(rec['id']))
        higher=[row for row in published if int(row.get('project_generation') or 0)>generation]
        if generation and higher:
            # A legitimate older recovery point may already exist. Retrying that stale
            # artifact after a newer publication must report the retained authority,
            # never the stale generation merely because its immutable record exists.
            winner=max(higher,key=retention_authority_key);path=Path(winner['path'])
            return {'file':path.name,'path':str(path),'verified':True,'created_at':winner.get('created_at') or now(),
                    'bytes':path.stat().st_size,'modified_ns':path.stat().st_mtime_ns,'fingerprint':winner.get('fingerprint') or '',
                    'project_generation':int(winner.get('project_generation') or 0),'source_token':source_token,
                    'discarded_stale':True,'publication_state':'published'}
        same=[row for row in published if int(row.get('project_generation') or 0)==generation]
        if same:
            source_state=str(source_info.get('canonical_state_hash') or '')
            known={str(row.get('canonical_state_hash') or '') for row in same}
            if not source_state or '' in known or any(value!=source_state for value in known):
                raise WorkspaceError('BACKUP_SAME_GENERATION_IDENTITY_UNCERTAIN')
            # Retry/duplicate execution discovers the already-published immutable
            # generation only after a full read-back verification.
            self._observe_destination_authority(rec,destination,full_verify=True)
            winner=max(same,key=retention_authority_key);path=Path(winner['path'])
            return {'file':path.name,'path':str(path),'verified':True,'created_at':winner.get('created_at') or now(),
                    'bytes':path.stat().st_size,'modified_ns':path.stat().st_mtime_ns,'fingerprint':winner.get('fingerprint') or '',
                    'project_generation':generation,'source_token':source_token,'idempotent':True,'publication_state':'published'}

        stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f');dest=target/f"breadmaker-{rec['id']}-{stamp}.breadmaker";tmp=dest.with_suffix(dest.suffix+'.tmp')
        try:
            with Path(source).open('rb') as src,tmp.open('wb') as out:
                shutil.copyfileobj(src,out,length=HASH_CHUNK_BYTES);out.flush();os.fsync(out.fileno())
            _durable_replace(tmp,dest)
            self._record_backup_state(root,did,generation,'TRANSFERRED')
            info=self.verify_package(dest)
            self._record_backup_state(root,did,generation,'DESTINATION_VERIFIED')
        except Exception:
            tmp.unlink(missing_ok=True);dest.unlink(missing_ok=True);raise

        # Authority decisions use only previously published recovery points, never
        # uncommitted payload files that merely happen to exist on disk.
        published=self._local_publication_rows(target,str(rec['id']))
        higher=[row for row in published if int(row.get('project_generation') or 0)>generation]
        if generation and higher:
            dest.unlink(missing_ok=True)
            winner=max(higher,key=retention_authority_key);path=Path(winner['path'])
            return {'file':path.name,'path':str(path),'verified':True,'created_at':winner.get('created_at') or now(),
                    'bytes':path.stat().st_size,'modified_ns':path.stat().st_mtime_ns,'fingerprint':winner.get('fingerprint') or '',
                    'project_generation':int(winner.get('project_generation') or 0),'source_token':source_token,
                    'discarded_stale':True,'publication_state':'published'}

        # Immutable publication record is the commit and is written LAST. A crash
        # before this point can leave only an unpublished payload, never a recovery
        # point that discovery is allowed to advertise.
        self._publish_local_package(target,dest,info)

        published=self._local_publication_rows(target,str(rec['id']))
        rows=published

        # Exact-state dedupe and retention operate only after publication. Delete a
        # publication manifest before its payload so interruption can leak bytes but
        # cannot leave a broken advertised recovery point.
        by_state={}
        for row in rows:
            state_key=str(row.get('canonical_state_hash') or '') or str(row.get('fingerprint') or '')
            by_state.setdefault(state_key,[]).append(row)
        kept=[]
        for _,group in by_state.items():
            ordered=sorted(group,key=retention_authority_key,reverse=True);winner=ordered[0]
            tied=[row for row in ordered if retention_authority_key(row)[0]==retention_authority_key(winner)[0]]
            current=next((row for row in tied if Path(row['path'])==dest),None)
            if current is not None:winner=current
            kept.append(winner)
            for row in group:
                if row is winner:continue
                self._delete_local_published_package(row)
        kept.sort(key=retention_authority_key,reverse=True)
        _,prune=recovery_retention_partition(kept,str(destination.get('recovery_retention_profile') or DEFAULT_RECOVERY_RETENTION_PROFILE))
        for old in prune:
            self._delete_local_published_package(old)
        stat=dest.stat()
        return {'file':dest.name,'path':str(dest),'verified':True,'created_at':info.get('created_at') or now(),'bytes':stat.st_size,
                'modified_ns':stat.st_mtime_ns,'fingerprint':info.get('project_fingerprint') or '','project_generation':generation,
                'source_token':source_token,'publication_state':'published'}

    @staticmethod
    def _scrub_due(destination:dict[str,Any], *, force:bool=False) -> bool:
        if force:return True
        raw=str(destination.get('last_integrity_scrubbed_at') or '')
        if not raw:return True
        try:
            dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()>=BACKUP_INTEGRITY_SCRUB_SECONDS
        except ValueError:return True

    def _record_backup_scrub_success(self, root:Path, destination_id:str) -> None:
        latest=self._backup_destinations(root)
        for item in latest:
            if str(item.get('id') or '')==str(destination_id):
                item['last_integrity_scrubbed_at']=datetime.now(timezone.utc).isoformat(timespec='microseconds')
                if str(item.get('last_error') or '').startswith('BACKUP_INTEGRITY_'):
                    item.pop('last_error',None);item.pop('last_error_at',None);item.pop('last_error_generation',None)
        self._save_backup_destinations(root,latest)

    def _record_integrity_failure(self, root:Path, destination_id:str, generation:int, code:str) -> None:
        latest=self._backup_destinations(root)
        for item in latest:
            if str(item.get('id') or '')!=str(destination_id):continue
            item['last_error']=str(code);item['last_error_at']=now();item['last_error_generation']=int(generation or 0)
            retry=item.get('retry_state') if isinstance(item.get('retry_state'),dict) else {}
            item['retry_state']={'state':'retry_pending','generation':int(generation or 0),'attempts':int(retry.get('attempts') or 1),'last_error':str(code)}
        self._save_backup_destinations(root,latest)
