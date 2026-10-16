from __future__ import annotations
import concurrent.futures, json, os, tempfile, threading, uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from revision_store.remote_history import RemoteHistoryRepository
from revision_store.remote_transports import WebDAVHistoryTransport, GoogleDriveHistoryTransport
from revision_store.history_gc import DEFAULT_HISTORY_RETENTION, normalize_history_retention, run_local_history_gc, run_remote_history_gc
from revision_store.history_gc_transports import WebDAVHistoryGCTransport, GoogleDriveHistoryGCTransport
from .models import WorkspaceError
from .filesystem import _durable_replace, now

class RemoteHistoryWorkspaceMixin:
    def _remote_history_writer_id(self) -> str:
        # This installation identity is part of remote single-writer authority.
        # Creation therefore has to be serialized: two concurrent first syncs must
        # never manufacture different writer IDs and race them to the repository.
        # Once the identity file exists, corruption/read failure is fail-closed.
        # Silently rotating it would abandon this installation's existing remote claim.
        with self._lock:
            if self.remote_history_writer_path.exists():
                try:
                    raw=json.loads(self.remote_history_writer_path.read_text(encoding='utf-8'))
                except (OSError,json.JSONDecodeError) as exc:
                    raise WorkspaceError('REMOTE_HISTORY_WRITER_ID_CORRUPT') from exc
                writer=str(raw.get('writer_id') or '') if isinstance(raw,dict) else ''
                if len(writer)!=32 or any(ch not in '0123456789abcdef' for ch in writer.lower()):
                    raise WorkspaceError('REMOTE_HISTORY_WRITER_ID_CORRUPT')
                return writer.lower()
            writer=uuid.uuid4().hex;self.root.mkdir(parents=True,exist_ok=True)
            fd,tmp_name=tempfile.mkstemp(prefix='remote-history-writer-',suffix='.json',dir=self.root)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as fh:
                    json.dump({'version':1,'writer_id':writer},fh,sort_keys=True);fh.flush();os.fsync(fh.fileno())
                _durable_replace(Path(tmp_name),self.remote_history_writer_path)
                try:os.chmod(self.remote_history_writer_path,0o600)
                except OSError:pass
            finally:
                try:os.unlink(tmp_name)
                except FileNotFoundError:pass
            return writer

    def _remote_history_transport(self,destination:dict[str,Any],workspace_project_id:str):
        provider=str(destination.get('provider') or '')
        if provider=='webdav':
            p=self._backup_providers['webdav'];client=p._client(self,destination)
            return WebDAVHistoryTransport(client)
        if provider=='google_drive':
            p=self._backup_providers['google_drive'];client=p._client(self,destination)
            client.check_root(str(destination.get('folder_id') or ''),str(destination.get('location_id') or ''))
            return GoogleDriveHistoryTransport(client,str(destination.get('folder_id') or ''),workspace_project_id)
        raise WorkspaceError('REMOTE_HISTORY_PROVIDER_UNSUPPORTED')

    def _manifest_lock_for(self, root:Path) -> threading.RLock:
        key=str(Path(root).resolve())
        with self._manifest_locks_guard:
            return self._manifest_locks.setdefault(key,threading.RLock())

    def _remote_history_lock_for(self, destination_id:str) -> threading.RLock:
        key=str(destination_id or '')
        with self._remote_history_locks_guard:
            return self._remote_history_locks.setdefault(key,threading.RLock())

    @contextmanager
    def backup_privacy_barrier(self):
        """Serialize destructive local reset against Recovery destination publication."""
        with self._backup_lock:
            yield

    @contextmanager
    def remote_history_activity(self):
        """Shared gate for normal Remote History mutation/maintenance.

        This gate is intentionally *not* a mutex: independent sync/GC calls may
        overlap and are subsequently serialized only by their destination locks.
        Privacy erasure takes the exclusive side of the gate.
        """
        condition=self._remote_history_activity_condition
        with condition:
            while self._remote_history_privacy_erasing:
                condition.wait()
            self._remote_history_active_operations+=1
        try:
            yield
        finally:
            with condition:
                self._remote_history_active_operations=max(0,self._remote_history_active_operations-1)
                if self._remote_history_active_operations==0:condition.notify_all()

    @contextmanager
    def remote_history_privacy_barrier(self):
        """Exclusive barrier for external privacy erasure.

        Once entered, no new Remote History mutation can begin; the caller waits
        until all already-active operations finish. This prevents a sync from
        recreating bytes after Delete All has started erasing the destination.
        """
        condition=self._remote_history_activity_condition
        with condition:
            while self._remote_history_privacy_erasing:
                condition.wait()
            self._remote_history_privacy_erasing=True
            while self._remote_history_active_operations>0:
                condition.wait()
        try:
            yield
        finally:
            with condition:
                self._remote_history_privacy_erasing=False
                condition.notify_all()

    def _remote_history_gc_transport(self,destination:dict[str,Any],workspace_project_id:str):
        provider=str(destination.get('provider') or '')
        if provider=='webdav':
            p=self._backup_providers['webdav'];client=p._client(self,destination)
            return WebDAVHistoryGCTransport(client,workspace_project_id)
        if provider=='google_drive':
            p=self._backup_providers['google_drive'];client=p._client(self,destination)
            client.check_root(str(destination.get('folder_id') or ''),str(destination.get('location_id') or ''))
            return GoogleDriveHistoryGCTransport(client,str(destination.get('folder_id') or ''),workspace_project_id)
        raise WorkspaceError('REMOTE_HISTORY_PROVIDER_UNSUPPORTED')

    def sync_remote_history(self, project_id:str|None=None, *, revision_id:str|None=None, destination_ids:list[str]|None=None) -> dict[str,Any]:
        with self.remote_history_activity():
            return self._sync_remote_history_impl(project_id,revision_id=revision_id,destination_ids=destination_ids)

    def _sync_remote_history_impl(self, project_id:str|None=None, *, revision_id:str|None=None, destination_ids:list[str]|None=None) -> dict[str,Any]:
        from storage import history as storage_history
        from storage import schema_contracts as storage_schema_contracts
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        root=Path(rec['path']).resolve();manifest=self.read_manifest(root);workspace_id=str(manifest.get('project_id') or rec['id'])
        store=storage_history.local_revision_store_for(root);writer=self._remote_history_writer_id();wanted={str(x) for x in (destination_ids or [])}
        destinations=self._backup_destinations(root,manifest,persist_normalization=False)
        remote=[d for d in destinations if str(d.get('provider') or '') in {'webdav','google_drive'} and (not wanted or str(d.get('id')) in wanted)]

        def _sync_one(destination:dict[str,Any]):
            did=str(destination.get('id') or '')
            with self._remote_history_lock_for(did):
                resolution=self._backup_provider(destination).resolve(self,destination)
                if not resolution.get('available'):raise WorkspaceError(str(resolution.get('reason') or 'REMOTE_HISTORY_DESTINATION_UNAVAILABLE'))
                transport=self._remote_history_transport(destination,workspace_id)
                initialized=destination.get('remote_history_repository_initialized')
                if initialized is True:
                    allow_initialize=False
                elif initialized is False:
                    allow_initialize=True
                else:
                    # Pre-7.8 destinations have no explicit genesis bit. Existing
                    # direct authority records prove initialization; absence proves
                    # nothing on an eventually-consistent provider, so fail closed.
                    probe=RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=None,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES)
                    if not probe.authority_present():
                        raise WorkspaceError('REMOTE_HISTORY_REPOSITORY_INITIALIZATION_REQUIRED')
                    allow_initialize=False
                repo=RemoteHistoryRepository(transport,workspace_project_id=workspace_id,writer_id=writer,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,allow_initialize=allow_initialize)
                # Repository genesis is explicit and independent of whether local
                # History happens to contain a revision yet. This prevents the local
                # destination flag from claiming initialization without durable
                # remote writer authority.
                if allow_initialize:repo.claim_writer()
                rows=[repo.publish(store,revision_id).as_dict()] if revision_id else repo.publish_all(store)
                if initialized is not True:self._mark_remote_history_initialized(root,did)
                return {'destination_id':did,'provider':str(destination.get('provider') or ''),'published':rows}

        results=[];failed=[]
        if remote:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4,len(remote)),thread_name_prefix='bm-history-sync') as pool:
                future_map={pool.submit(_sync_one,d):d for d in remote}
                for future,destination in future_map.items():
                    did=str(destination.get('id') or '')
                    try:results.append(future.result())
                    except Exception as exc:failed.append({'destination_id':did,'provider':str(destination.get('provider') or ''),'error':str(exc)})
        results.sort(key=lambda row:str(row.get('destination_id') or ''));failed.sort(key=lambda row:str(row.get('destination_id') or ''))
        return {'ok':not failed,'project_id':str(rec['id']),'revision_id':revision_id,'destinations':results,'failed':failed}

    def repair_local_history_from_destination(self,destination_id:str,*,project_id:str|None=None,revision_id:str|None=None)->dict[str,Any]:
        from storage import history as storage_history
        from storage import schema_contracts as storage_schema_contracts
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        root=Path(rec['path']).resolve();manifest=self.read_manifest(root);workspace_id=str(manifest.get('project_id') or rec['id'])
        destination=next((d for d in self._backup_destinations(root,manifest,persist_normalization=False) if str(d.get('id'))==str(destination_id)),None)
        if not destination:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
        with self._remote_history_lock_for(str(destination_id)):
            # Read/verify/local-repair does not require this installation's writer ID.
            # Corruption of remote_history_writer.json must stop publication/GC, not
            # prevent recovery from a healthy remote repository.
            repo=RemoteHistoryRepository(self._remote_history_transport(destination,workspace_id),workspace_project_id=workspace_id,writer_id=None,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES)
            return repo.repair_local(storage_history.local_revision_store_for(root),revision_id).as_dict()

    def _project_history_retention(self, root:Path, manifest:dict[str,Any]|None=None, *, persist_normalization:bool=True) -> str:
        manifest=dict(manifest or self.read_manifest(root))
        raw=manifest.get('revision_history_retention',DEFAULT_HISTORY_RETENTION)
        try:return normalize_history_retention(raw)
        except ValueError as exc:raise WorkspaceError('HISTORY_RETENTION_INVALID') from exc

    def history_retention_settings(self, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        root=Path(rec['path']);manifest=self.read_manifest(root);policy=self._project_history_retention(root,manifest,persist_normalization=True)
        return {'project_id':str(rec['id']),'local_revision_history_retention':policy}

    def update_history_retention(self, value:str, project_id:str|None=None) -> dict[str,Any]:
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        try:policy=normalize_history_retention(value)
        except ValueError as exc:raise WorkspaceError('HISTORY_RETENTION_INVALID') from exc
        with self._lock:
            root=Path(rec['path'])
            def _update(manifest):
                manifest['revision_history_retention']=policy;manifest['updated_at']=now();return manifest
            self._mutate_manifest(root,_update)
        return {'project_id':str(rec['id']),'local_revision_history_retention':policy}

    def gc_local_history(self, project_id:str|None=None, *, grace_seconds:float|None=None) -> dict[str,Any]:
        from storage import history as storage_history
        from storage import schema_contracts as storage_schema_contracts
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        root=Path(rec['path']).resolve();policy=self._project_history_retention(root,persist_normalization=True)
        store=storage_history.local_revision_store_for(root)
        kwargs={} if grace_seconds is None else {'grace_seconds':float(grace_seconds)}
        return run_local_history_gc(store,policy,**kwargs).as_dict()

    def gc_remote_history(self, project_id:str|None=None, *, destination_ids:list[str]|None=None, grace_seconds:float|None=None) -> dict[str,Any]:
        with self.remote_history_activity():
            return self._gc_remote_history_impl(project_id,destination_ids=destination_ids,grace_seconds=grace_seconds)

    def _gc_remote_history_impl(self, project_id:str|None=None, *, destination_ids:list[str]|None=None, grace_seconds:float|None=None) -> dict[str,Any]:
        from storage import history as storage_history
        from storage import schema_contracts as storage_schema_contracts
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        root=Path(rec['path']).resolve();manifest=self.read_manifest(root);workspace_id=str(manifest.get('project_id') or rec['id']);writer=self._remote_history_writer_id()
        wanted={str(x) for x in (destination_ids or [])};destinations=self._backup_destinations(root,manifest,persist_normalization=False)
        remote=[d for d in destinations if str(d.get('provider') or '') in {'webdav','google_drive'} and (not wanted or str(d.get('id')) in wanted)]

        def _gc_one(destination:dict[str,Any]):
            did=str(destination.get('id') or '')
            if destination.get('remote_history_repository_initialized') is not True:
                raise WorkspaceError('REMOTE_HISTORY_GC_REPOSITORY_NOT_INITIALIZED')
            with self._remote_history_lock_for(did):
                resolution=self._backup_provider(destination).resolve(self,destination)
                if not resolution.get('available'):raise WorkspaceError(str(resolution.get('reason') or 'REMOTE_HISTORY_DESTINATION_UNAVAILABLE'))
                repo=RemoteHistoryRepository(self._remote_history_transport(destination,workspace_id),workspace_project_id=workspace_id,writer_id=writer,canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,allow_initialize=False)
                policy=normalize_history_retention(destination.get('remote_history_retention',DEFAULT_HISTORY_RETENTION))
                delete_cap=self._remote_history_gc_transport(destination,workspace_id)
                state_path=storage_history.local_revision_store_for(root).root/'gc'/f'remote-{did}.json'
                kwargs={} if grace_seconds is None else {'grace_seconds':float(grace_seconds)}
                result=run_remote_history_gc(repository=repo,delete_capability=delete_cap,state_path=state_path,policy=policy,**kwargs)
                return {'destination_id':did,'provider':str(destination.get('provider') or ''),'gc':result.as_dict()}

        completed=[];failed=[]
        if remote:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4,len(remote)),thread_name_prefix='bm-history-gc') as pool:
                future_map={pool.submit(_gc_one,d):d for d in remote}
                for future,destination in future_map.items():
                    did=str(destination.get('id') or '')
                    try:completed.append(future.result())
                    except Exception as exc:failed.append({'destination_id':did,'provider':str(destination.get('provider') or ''),'error':str(exc)})
        completed.sort(key=lambda row:str(row.get('destination_id') or ''));failed.sort(key=lambda row:str(row.get('destination_id') or ''))
        return {'ok':not failed,'project_id':str(rec['id']),'destinations':completed,'failed':failed}

    def run_history_maintenance(self, project_id:str|None=None) -> dict[str,Any]:
        """Background-only History maintenance. Current never waits for this path."""
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        result={'project_id':str(rec['id']),'local':None,'remote':None,'failed':[]}
        try:result['local']=self.gc_local_history(str(rec['id']))
        except Exception as exc:result['failed'].append({'scope':'local','error':str(exc)})
        try:
            remote=self.gc_remote_history(str(rec['id']));result['remote']=remote
            for row in remote.get('failed') or []:result['failed'].append({'scope':'remote',**row})
        except Exception as exc:result['failed'].append({'scope':'remote','error':str(exc)})
        result['ok']=not result['failed']
        return result
