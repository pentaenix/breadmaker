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

class BackupStateMixin:
    @staticmethod
    def _verified_generation(destination:dict[str,Any]) -> int:
        last=destination.get('last_verified') if isinstance(destination.get('last_verified'),dict) else {}
        return int(last.get('project_generation') or 0)

    def _record_backup_attempt(self, root:Path, destination_id:str, operation:BackupOperation) -> None:
        latest=self._backup_destinations(root)
        for item in latest:
            if str(item.get('id'))!=str(destination_id):continue
            generation=int(operation.source_generation or 0)
            previous=int(item.get('last_attempted_generation') or 0)
            item['last_attempted_generation']=max(previous,generation)
            item['last_attempt_operation']=operation.target_operation
            retry=item.get('retry_state') if isinstance(item.get('retry_state'),dict) else {}
            attempts=int(retry.get('attempts') or 0)+1 if int(retry.get('generation') or 0)==generation else 1
            item['retry_state']={'state':'in_progress','generation':generation,'attempts':attempts}
        self._save_backup_destinations(root,latest)

    def _record_backup_state(self, root:Path, destination_id:str, generation:int, state:str) -> bool:
        """Advance one destination's publication state without allowing stale regression.

        This is operational progress, not authority. Only PUBLISHED advances the
        authoritative recovery generation. Completion time and wall-clock time are
        deliberately irrelevant.
        """
        state=str(state or '').upper()
        if state not in PUBLICATION_STATE_ORDER:raise WorkspaceError('BACKUP_PUBLICATION_STATE_INVALID')
        generation=int(generation or 0)
        if generation<=0:raise WorkspaceError('BACKUP_PUBLICATION_GENERATION_INVALID')
        latest=self._backup_destinations(root);accepted=False
        for item in latest:
            if str(item.get('id'))!=str(destination_id):continue
            published=int(item.get('last_published_generation') or self._verified_generation(item) or 0)
            current_generation=int(item.get('publication_generation') or 0)
            current_state=str(item.get('publication_state') or '')
            if generation<published or generation<current_generation:
                continue
            if generation==current_generation and current_state in PUBLICATION_STATE_ORDER and PUBLICATION_STATE_ORDER[state]<PUBLICATION_STATE_ORDER[current_state]:
                continue
            item['publication_generation']=generation
            item['publication_state']=state
            if state in {'TRANSFERRED','DESTINATION_VERIFIED','PUBLISHED'}:
                item['last_transferred_generation']=max(int(item.get('last_transferred_generation') or 0),generation)
            if state in {'DESTINATION_VERIFIED','PUBLISHED'}:
                item['last_verified_generation']=max(int(item.get('last_verified_generation') or 0),generation)
            if state=='PUBLISHED':
                item['last_published_generation']=max(int(item.get('last_published_generation') or 0),generation)
            accepted=True
        self._save_backup_destinations(root,latest)
        return accepted

    def _record_backup_success(self, root:Path, destination_id:str, meta:dict[str,Any], operation:BackupOperation) -> bool:
        """Record an already-published immutable recovery point.

        Providers must publish their immutable provider record before returning. This
        method only mirrors that authority into the local destination status cache.
        """
        generation=int(meta.get('project_generation') or operation.source_generation or 0)
        latest=self._backup_destinations(root);accepted=False
        for item in latest:
            if str(item.get('id'))!=str(destination_id):continue
            previous=max(self._verified_generation(item),int(item.get('last_published_generation') or 0))
            if previous and generation and generation<previous:
                continue
            normalized=dict(meta);normalized['project_generation']=generation
            normalized['publication_state']='published'
            item['last_verified']=normalized
            item['last_transferred_generation']=max(int(item.get('last_transferred_generation') or 0),generation)
            item['last_verified_generation']=max(int(item.get('last_verified_generation') or 0),generation)
            item['last_published_generation']=max(int(item.get('last_published_generation') or 0),generation)
            item['publication_generation']=generation
            item['publication_state']='PUBLISHED'
            item['last_integrity_scrubbed_at']=datetime.now(timezone.utc).isoformat(timespec='microseconds')
            item['retry_state']={'state':'idle','generation':generation,'attempts':int((item.get('retry_state') or {}).get('attempts') or 1)}
            error_generation=int(item.get('last_error_generation') or 0)
            if not error_generation or generation>=error_generation:
                item.pop('last_error',None);item.pop('last_error_at',None);item.pop('last_error_generation',None)
            accepted=True
        self._save_backup_destinations(root,latest)
        return accepted

    def _record_backup_failure(self, root:Path, destination_id:str, operation:BackupOperation, error_code:str='BACKUP_WRITE_FAILED') -> bool:
        """A delayed failure cannot overwrite a later published success."""
        generation=int(operation.source_generation or 0);latest=self._backup_destinations(root);accepted=False
        for item in latest:
            if str(item.get('id'))!=str(destination_id):continue
            published=max(self._verified_generation(item),int(item.get('last_published_generation') or 0))
            if published and generation and generation<published:
                continue
            # A delayed ordinary failure for the same generation cannot overwrite a
            # completed PUBLISHED success. Integrity failures use the dedicated durable
            # path because they revoke trust in already-published bytes.
            if published and generation==published and str(item.get('publication_state') or '').upper()=='PUBLISHED':
                continue
            previous_error=int(item.get('last_error_generation') or 0)
            if previous_error and generation and generation<previous_error:
                continue
            retry=item.get('retry_state') if isinstance(item.get('retry_state'),dict) else {}
            attempts=int(retry.get('attempts') or 1)
            item['last_error']=error_code;item['last_error_at']=now();item['last_error_generation']=generation
            item['retry_state']={'state':'retry_pending','generation':generation,'attempts':attempts,'last_error':error_code}
            accepted=True
        self._save_backup_destinations(root,latest)
        return accepted
