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

class BackupInventoryMixin:
    def _local_destination_candidates(self, destination:dict[str,Any], project_id:str|None=None) -> list[dict[str,Any]]:
        resolved=self._resolve_local_destination(destination)
        if not resolved.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        path=Path(resolved['path']);published=self._local_publication_rows(path,project_id)
        rows=[]
        for row in published:
            rows.append({k:v for k,v in row.items() if k not in {'path','publication_path','mtime_ns'}})
        rows.sort(key=retention_authority_key,reverse=True)
        return rows

    def _list_local_destination_backups(self, rec:dict[str,Any], destination:dict[str,Any]) -> list[dict[str,Any]]:
        return [row for row in self._local_destination_candidates(destination,str(rec['id'])) if str(row.get('project_id'))==str(rec['id'])]

    def _discover_local_destination_backups(self, destination:dict[str,Any]) -> list[dict[str,Any]]:
        return self._local_destination_candidates(destination,None)

    def _materialize_local_destination_backup(self, destination:dict[str,Any], filename:str, target_dir:Path) -> Path:
        if Path(filename).name!=filename or not filename.endswith('.breadmaker'):raise WorkspaceError('INVALID_BACKUP_NAME')
        resolved=self._resolve_local_destination(destination)
        if not resolved.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        root=Path(resolved['path']).resolve();path=(root/filename).resolve()
        try:path.relative_to(root)
        except ValueError:raise WorkspaceError('INVALID_BACKUP_NAME')
        if not path.is_file():raise WorkspaceError('BACKUP_NOT_FOUND')
        publication=root/publication_filename(filename)
        if not publication.is_file():raise WorkspaceError('BACKUP_NOT_PUBLISHED')
        try:
            row=validate_publication_manifest(json.loads(publication.read_text(encoding='utf-8')))
            if str(row.get('package_file') or '')!=filename:raise WorkspaceError('BACKUP_NOT_PUBLISHED')
            if sha256_file(path,HASH_CHUNK_BYTES).lower()!=str(row.get('package_sha256') or '').lower():raise WorkspaceError('BACKUP_PUBLICATION_HASH_MISMATCH')
        except WorkspaceError:raise
        except Exception as exc:raise WorkspaceError('BACKUP_NOT_PUBLISHED') from exc
        return path

    def list_destination_backups(self, destination_id:str, project_id:str|None=None) -> list[dict[str,Any]]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        status=next((item for item in self.list_backup_destinations(rec['id']) if str(item.get('id'))==str(destination_id)),None)
        if not status:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
        if not status.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        provider=self._backup_provider(status)
        if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        return provider.list_backups(self,rec,status)

    def discover_destination_backups(self, destination_id:str, project_id:str|None=None) -> list[dict[str,Any]]:
        """Discover all Breadmaker project candidates at a configured destination.

        Unlike list_destination_backups this is deliberately not scoped to the active
        project id, which makes a surviving destination usable on a fresh install.
        """
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        status=next((item for item in self.list_backup_destinations(rec['id']) if str(item.get('id'))==str(destination_id)),None)
        if not status:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
        if not status.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        provider=self._backup_provider(status)
        if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        return provider.discover(self,status)

    @contextmanager
    def materialize_destination_backup(self, destination_id:str, filename:str, project_id:str|None=None):
        """Yield a provider package through one provider-independent recovery seam."""
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        status=next((item for item in self.list_backup_destinations(rec['id']) if str(item.get('id'))==str(destination_id)),None)
        if not status:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
        if not status.get('available'):raise WorkspaceError('EXTERNAL_BACKUP_DESTINATION_UNAVAILABLE')
        provider=self._backup_provider(status)
        if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
        with tempfile.TemporaryDirectory(prefix='breadmaker-backup-materialize-') as td:
            path=Path(provider.materialize(self,status,filename,Path(td))).resolve()
            if not path.is_file():raise WorkspaceError('BACKUP_NOT_FOUND')
            yield path
