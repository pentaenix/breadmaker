from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from typing import Any
from backups.providers.google_drive import GOOGLE_BACKUP_FOLDER_NAME
from backups.publication import PUBLICATION_SUFFIX
from .constants import APP_SCHEMA_VERSION, PACKAGE_VERSION, EXTERNAL_BACKUP_MARKER
from .models import WorkspaceError, _normalize_backup_destination_kind
from .filesystem import _durable_replace, _read_external_backup_marker

class BackupErasureWorkspaceMixin:
    @staticmethod
    def _managed_privacy_project_id(value:Any) -> str:
        pid=str(value or '').strip().lower()
        return pid if len(pid)==32 and all(ch in '0123456789abcdef' for ch in pid) else ''

    def _backup_erasure_destination_snapshot(self, destination:dict[str,Any]) -> dict[str,Any] | None:
        """Return only the fields required to safely identify a managed backup scope.

        No manuscript data or secret values are copied here. Remote credentials remain in
        backup_credentials.json and are referenced only by opaque credential_id.
        """
        if not isinstance(destination,dict): return None
        provider=str(destination.get('provider') or '').strip()
        did=str(destination.get('id') or '').strip()
        location_id=str(destination.get('location_id') or '').strip()
        if provider not in self._backup_providers or not did or not location_id:return None
        common={
            'id':did,'provider':provider,'destination_kind':str(destination.get('destination_kind') or ('remote' if provider!='local_folder' else 'local')) ,
            'location_id':location_id,
        }
        if provider=='local_folder':
            raw=str(destination.get('path') or '').strip()
            if not raw:return None
            common.update({'path':raw,'volume_id':str(destination.get('volume_id') or ''),'volume_relative_path':str(destination.get('volume_relative_path') or '')})
        elif provider=='webdav':
            endpoint=str(destination.get('endpoint') or '').strip();credential_id=str(destination.get('credential_id') or '').strip()
            if not endpoint or not credential_id:return None
            common.update({'endpoint':endpoint,'credential_id':credential_id})
        elif provider=='google_drive':
            folder_id=str(destination.get('folder_id') or '').strip();credential_id=str(destination.get('credential_id') or '').strip()
            if not folder_id or not credential_id:return None
            common.update({'folder_id':folder_id,'folder_name':str(destination.get('folder_name') or GOOGLE_BACKUP_FOLDER_NAME),'credential_id':credential_id})
        return common

    def _load_backup_erasure_inventory(self) -> dict[str,Any]:
        path=self.backup_erasure_inventory_path
        if not path.is_file():return {'version':2,'projects':{}}
        try:
            raw=json.loads(path.read_text(encoding='utf-8'))
            projects=raw.get('projects') if isinstance(raw,dict) and int(raw.get('version') or 0)==2 else None
            if not isinstance(projects,dict):raise ValueError('invalid inventory')
            return {'version':2,'projects':{str(k):[dict(x) for x in v if isinstance(x,dict)] for k,v in projects.items() if isinstance(v,list)}}
        except (OSError,json.JSONDecodeError,TypeError,ValueError):
            # v1 lacked stable removable-volume identity and is not destructive authority.
            return {'version':2,'projects':{}}

    def _save_backup_erasure_inventory(self, data:dict[str,Any]) -> None:
        self.root.mkdir(parents=True,exist_ok=True)
        fd,tmp_name=tempfile.mkstemp(prefix='backup-erasure-inventory-',suffix='.json',dir=self.root)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                json.dump({'version':2,'projects':dict(data.get('projects') or {})},fh,indent=2,ensure_ascii=False);fh.flush();os.fsync(fh.fileno())
            _durable_replace(Path(tmp_name),self.backup_erasure_inventory_path)
            try:os.chmod(self.backup_erasure_inventory_path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass

    @staticmethod
    def _erasure_location_key(row:dict[str,Any]) -> tuple:
        provider=str(row.get('provider') or '')
        if provider=='local_folder':
            return (provider,str(row.get('location_id') or ''),str(row.get('volume_id') or ''),str(row.get('volume_relative_path') or ''),str(row.get('path') or ''))
        if provider=='webdav':return (provider,str(row.get('location_id') or ''),str(row.get('endpoint') or ''))
        if provider=='google_drive':return (provider,str(row.get('location_id') or ''),str(row.get('folder_id') or ''))
        return (provider,str(row.get('id') or ''))

    def _remember_backup_erasure_destinations(self, project_id:str, destinations:list[dict[str,Any]]) -> None:
        """Conservative historical ledger: removing a destination never forgets bytes."""
        pid=self._managed_privacy_project_id(project_id)
        if not pid:return
        with self._lock:
            data=self._load_backup_erasure_inventory();projects=dict(data.get('projects') or {})
            existing=[dict(x) for x in projects.get(pid,[]) if isinstance(x,dict)]
            by_key={self._erasure_location_key(x):x for x in existing}
            for destination in destinations:
                snap=self._backup_erasure_destination_snapshot(destination)
                if snap:by_key[self._erasure_location_key(snap)]=snap
            if by_key:projects[pid]=list(by_key.values())
            self._save_backup_erasure_inventory({'version':2,'projects':projects})

    def _raw_manifest_backup_erasure_rows(self) -> dict[str,list[dict[str,Any]]]:
        """Best-effort extraction that deliberately ignores project/schema compatibility.

        This is used only to locate already-known backup scopes during explicit privacy
        deletion. It never opens or migrates the project database and never grants deletion
        authority by itself; provider/location identity is re-verified before any removal.
        """
        out:dict[str,list[dict[str,Any]]]={}
        roots=[]
        if self.projects_dir.exists():roots.extend(p for p in self.projects_dir.iterdir() if p.is_dir())
        trash=self.root/'trash'
        if trash.exists():roots.extend(p for p in trash.iterdir() if p.is_dir())
        for root in roots:
            pid=self._managed_privacy_project_id(root.name.split('-',1)[0])
            try:
                raw=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
            except (OSError,json.JSONDecodeError):continue
            # Manifest is only a bootstrap source when it is itself a complete current
            # identity record bound to this folder. Corrupt/unsupported project bytes
            # can never manufacture destructive external authority.
            if not pid or not isinstance(raw,dict):continue
            if str(raw.get('package_format') or '')!='breadmaker-project' or int(raw.get('package_version') or 0)!=PACKAGE_VERSION or int(raw.get('schema_version') or 0)!=APP_SCHEMA_VERSION or str(raw.get('project_id') or '')!=pid:
                continue
            rows=[]
            for item in (raw.get('backup_destinations') or []) if isinstance(raw,dict) else []:
                snap=self._backup_erasure_destination_snapshot(item) if isinstance(item,dict) else None
                if snap:rows.append(snap)
            if pid and rows:out.setdefault(pid,[]).extend(rows)
        return out

    def _refresh_backup_erasure_inventory_from_raw_manifests(self) -> None:
        """Best-effort bootstrap for installations created before the privacy inventory."""
        raw=self._raw_manifest_backup_erasure_rows()
        if not raw:return
        with self._lock:
            data=self._load_backup_erasure_inventory();projects=dict(data.get('projects') or {});changed=False
            for pid,rows in raw.items():
                if projects.get(pid):continue
                clean=[]
                for row in rows:
                    snap=self._backup_erasure_destination_snapshot(row)
                    if snap:clean.append(snap)
                if clean:projects[pid]=clean;changed=True
            if changed:self._save_backup_erasure_inventory({'version':2,'projects':projects})

    def _known_backup_erasure_targets(self) -> list[dict[str,Any]]:
        """Return deduplicated destination scopes + project ids for explicit privacy erase."""
        projects:dict[str,list[dict[str,Any]]]={}
        inventory=self._load_backup_erasure_inventory().get('projects') or {}
        for pid,rows in inventory.items():
            safe_pid=self._managed_privacy_project_id(pid)
            if safe_pid:projects.setdefault(safe_pid,[]).extend(dict(x) for x in rows if isinstance(x,dict))
        for pid,rows in self._raw_manifest_backup_erasure_rows().items():
            safe_pid=self._managed_privacy_project_id(pid)
            if safe_pid and not projects.get(safe_pid):projects.setdefault(safe_pid,[]).extend(rows)
        # Folder names remain safe project identities even when manifest/database bytes are corrupt.
        if self.projects_dir.exists():
            for root in self.projects_dir.iterdir():
                if root.is_dir():
                    safe_pid=self._managed_privacy_project_id(root.name)
                    if safe_pid:projects.setdefault(safe_pid,[])
        trash=self.root/'trash'
        if trash.exists():
            for root in trash.iterdir():
                if root.is_dir():
                    safe_pid=self._managed_privacy_project_id(root.name.split('-',1)[0])
                    if safe_pid:projects.setdefault(safe_pid,[])
        grouped:dict[tuple,dict[str,Any]]={}
        for pid,rows in projects.items():
            pid=self._managed_privacy_project_id(pid)
            if not pid:continue
            for raw in rows:
                snap=self._backup_erasure_destination_snapshot(raw)
                if not snap:continue
                provider=snap['provider']
                if provider=='local_folder':key=(provider,str(Path(snap['path']).expanduser().resolve()),snap['location_id'])
                elif provider=='webdav':key=(provider,snap['endpoint'],snap['location_id'],snap['credential_id'])
                else:key=(provider,snap['folder_id'],snap['location_id'],snap['credential_id'])
                entry=grouped.setdefault(key,{'destination':snap,'project_ids':set()});entry['project_ids'].add(str(pid))
        return [{'destination':dict(row['destination']),'project_ids':sorted(row['project_ids'])} for row in grouped.values()]

    def erase_known_managed_backup_data(self) -> dict[str,Any]:
        """Best-effort explicit privacy erase of only positively identified Breadmaker data.

        Unavailable/corrupt destinations are reported but do not block deletion of the local
        Breadmaker home. No destination directory is recursively erased; provider identity
        is verified and provider-specific methods remove only Breadmaker-managed objects.
        """
        removed=0;failures=[];attempted=0
        for target in self._known_backup_erasure_targets():
            destination=dict(target['destination']);project_ids={str(x) for x in target['project_ids'] if self._managed_privacy_project_id(x)}
            attempted+=1
            try:_normalize_backup_destination_kind(destination.get('destination_kind'))
            except WorkspaceError:
                failures.append({'provider':str(destination.get('provider') or ''),'destination_id':str(destination.get('id') or ''),'reason':'unsupported_destination_kind'});continue
            provider=self._backup_provider(destination)
            if provider is None:
                failures.append({'provider':str(destination.get('provider') or ''),'reason':'unsupported_provider'});continue
            try:
                resolved=provider.resolve(self,destination)
                if not resolved.get('available'):
                    failures.append({'provider':str(destination.get('provider') or ''),'destination_id':str(destination.get('id') or ''),'reason':str(resolved.get('reason') or 'unavailable')});continue
                removed+=int(provider.erase_managed(self,destination,project_ids) or 0)
            except Exception as exc:
                failures.append({'provider':str(destination.get('provider') or ''),'destination_id':str(destination.get('id') or ''),'reason':getattr(exc,'code',None) or str(exc) or exc.__class__.__name__})
        return {'removed':removed,'attempted':attempted,'failures':failures}


    def _erase_local_destination(self, path:Path, project_ids:set[str]) -> int:
        removed=0;path=Path(path).expanduser().resolve()
        if not path.is_dir():raise WorkspaceError('EXTERNAL_BACKUP_ERASE_DESTINATION_UNAVAILABLE')
        for pid in project_ids:
            # Publication authority first, then payloads/staging. Explicit privacy
            # erasure removes both published records and any harmless orphan bytes.
            for publication in path.glob(f'breadmaker-{pid}-*.breadmaker{PUBLICATION_SUFFIX}'):
                try:publication.unlink()
                except OSError as exc:raise WorkspaceError('EXTERNAL_BACKUP_ERASE_FAILED') from exc
            for package in path.glob(f'breadmaker-{pid}-*.breadmaker*'):
                if package.name.endswith(PUBLICATION_SUFFIX):continue
                try:package.unlink();removed+=1
                except OSError as exc:raise WorkspaceError('EXTERNAL_BACKUP_ERASE_FAILED') from exc
        marker=path/EXTERNAL_BACKUP_MARKER
        # The marker identifies the shared managed location, not one project. Keep it
        # whenever unrelated bytes or another project's backups remain.
        try:remaining=[x for x in path.iterdir() if x!=marker]
        except OSError:remaining=[]
        if not remaining and _read_external_backup_marker(path):
            try:marker.unlink()
            except OSError as exc:raise WorkspaceError('EXTERNAL_BACKUP_ERASE_FAILED') from exc
        try:
            if not any(path.iterdir()):path.rmdir()
        except OSError:pass
        return removed
