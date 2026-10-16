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

class BackupCatchUpMixin:
    def backup_destinations_catch_up(self, project_id:str|None=None, *, destination_ids:list[str]|None=None, reason:str='automatic', force:bool=False, quiet_seconds:float=4.0) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:return {'ok':True,'backed_up':[],'failed':[],'destinations':[]}
        root=Path(rec['path']);source_token=_project_change_token(root);source_generation=_project_generation(root);age=_project_change_age_seconds(root)
        operation=BackupOperation(str(rec['id']),source_generation,str(reason or 'automatic')[:120],source_generation,source_token)
        if not force and age<quiet_seconds:return {'ok':True,'backed_up':[],'failed':[],'destinations':self.list_backup_destinations(rec['id']),'waiting_for_quiet':True,'operation':asdict(operation)}
        wanted={str(x) for x in (destination_ids or [])}
        with self._backup_lock:
            destinations=self._backup_destinations(root);statuses=[self._destination_status(rec,item,source_token) for item in destinations]
            scrub_blocked=set();pre_failed=[]
            by_id={str(item.get('id') or ''):item for item in destinations}
            for status in list(statuses):
                did=str(status.get('id') or '')
                if wanted and did not in wanted:continue
                config=by_id.get(did,status)
                generation=int(config.get('last_published_generation') or 0)
                # If Current is newer than the published backup, the replacement artifact
                # will itself be fully read-back verified before publication. Scrubbing the
                # superseded generation first only doubles remote I/O and can violate the
                # hard per-test/interactive latency budget. Still scrub when the destination
                # is already at/above Current, or when live authority is itself in doubt.
                authority_in_doubt=bool(status.get('attention') or status.get('reason'))
                scrub_relevant=bool(generation>0 and (source_generation<=generation or authority_in_doubt))
                if not status.get('available') or not scrub_relevant or not self._scrub_due(config,force=force):continue
                try:
                    observed=self._observe_destination_authority(rec,config,full_verify=True)
                    if int(observed.get('observed_generation') or 0)<generation:
                        missing_rows=[row for row in observed.get('rows') or [] if int(row.get('project_generation') or 0)==generation and row.get('_payload_missing')]
                        if missing_rows:
                            self._revoke_corrupt_destination_authority(rec,config,generation)
                        # If the immutable publication itself is gone there is nothing
                        # to revoke; leave the stale cache untrusted and let ordinary
                        # idempotent reconciliation publish a fresh authority record.
                    else:
                        self._record_backup_scrub_success(root,did)
                except Exception as exc:
                    code=str(exc) or exc.__class__.__name__
                    proven_corruption=code in {'BACKUP_PUBLICATION_HASH_MISMATCH','BACKUP_PUBLICATION_PROJECT_MISMATCH','BACKUP_PUBLICATION_GENERATION_MISMATCH','BACKUP_PUBLICATION_STATE_MISMATCH','BACKUP_PUBLISHED_PAYLOAD_MISSING'}
                    if proven_corruption:
                        try:self._revoke_corrupt_destination_authority(rec,config,generation)
                        except Exception:
                            self._record_integrity_failure(root,did,generation,'BACKUP_INTEGRITY_REVOKE_FAILED')
                            scrub_blocked.add(did);pre_failed.append({'id':did,'error':'BACKUP_INTEGRITY_REVOKE_FAILED','project_generation':generation});continue
                    else:
                        self._record_integrity_failure(root,did,generation,'BACKUP_INTEGRITY_SCRUB_FAILED')
                        scrub_blocked.add(did);pre_failed.append({'id':did,'error':code,'project_generation':generation});continue
            statuses=[self._destination_status(rec,item,source_token) for item in self._backup_destinations(root)]
            eligible=[s for s in statuses if (not wanted or str(s.get('id')) in wanted) and str(s.get('id')) not in scrub_blocked and s.get('available') and (force or not s.get('up_to_date'))]
            if not eligible:return {'ok':not pre_failed,'backed_up':[],'failed':pre_failed,'destinations':statuses,'operation':asdict(operation)}
            ids={str(s['id']) for s in eligible};self._backup_in_progress.update(ids)
            for did in ids:self._record_backup_attempt(root,did,operation)
            backed=[];failed=list(pre_failed)
            try:
                with tempfile.TemporaryDirectory(prefix='breadmaker-destination-backup-') as td:
                    package=Path(td)/'backup.breadmaker';self.export_project(rec['id'],package,recovery_reason=reason);package_info=self.verify_package(package)
                    artifact_generation=int(package_info.get('project_generation') or source_generation or 0)
                    # 7.7.2 formalizes common pre-transfer states. The frozen snapshot
                    # and staged package were already validated by export_project; each
                    # destination records that same immutable generation before transport.
                    for status in eligible:
                        did=str(status['id'])
                        for state in ('SNAPSHOTTED','PACKAGED','LOCAL_VALIDATED'):
                            self._record_backup_state(root,did,artifact_generation,state)
                    # The immutable artifact may legitimately include commits that landed
                    # after scheduling but before SQLite snapshot capture. Publication uses
                    # the artifact's actual generation; callback authority never uses time.
                    artifact_operation=BackupOperation(operation.project_id,artifact_generation,operation.target_operation,operation.expected_authoritative_generation,source_token)
                    for status in eligible:
                        did=str(status['id'])
                        try:
                            provider=self._backup_provider(status)
                            if provider is None:raise WorkspaceError('BACKUP_PROVIDER_UNSUPPORTED')
                            meta=provider.store(self,package,rec,status,source_token)
                            meta.setdefault('project_generation',artifact_generation)
                            accepted=self._record_backup_success(root,did,meta,artifact_operation)
                            backed.append({'id':did,**meta,'published':accepted})
                        except Exception as exc:
                            error_code=str(getattr(exc,'code','') or 'BACKUP_WRITE_FAILED')[:160]
                            failed.append({'id':did,'error':str(exc),'project_generation':artifact_generation})
                            self._record_backup_failure(root,did,artifact_operation,error_code)
            finally:self._backup_in_progress.difference_update(ids)
        return {'ok':not failed,'backed_up':backed,'failed':failed,'destinations':self.list_backup_destinations(rec['id']),'operation':asdict(operation)}
