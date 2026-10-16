from __future__ import annotations

"""Verified local Recovery package generation, retention, and scheduler authority."""

import json
import os
import re
import uuid
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from backups.frozen_snapshot import capture_frozen_snapshot, verify_frozen_package_payload, FrozenSnapshotError
from backups.retention import recovery_retention_partition, DEFAULT_RECOVERY_RETENTION_PROFILE
from revision_store.local_store import canonical_state_hash
from storage import assets as storage_assets
from storage import context as storage_context
from storage import database as storage_database
from storage import coordination as storage_coordination
from storage.schema import SCHEMA_VERSION
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

_RECOVERY_TRACKING_FILE = 'recovery_state.json'


_RECOVERY_TRACKING_VERSION = 2


_RECOVERY_SCRUB_INTERVAL_SECONDS = 24 * 60 * 60


def _canonical_workday(value: object) -> str:
    raw=str(value or '').strip()
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00')) if raw else datetime.now(timezone.utc)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date().isoformat()
    except ValueError:
        return datetime.now().astimezone().date().isoformat()


def _recovery_metadata(reason: str, source_updated_at: object) -> dict:
    local=datetime.now().astimezone()
    offset=local.utcoffset()
    minutes=int(offset.total_seconds()//60) if offset else 0
    return {
        'version':1,
        'trigger':str(reason or 'automatic')[:80],
        'workday':_canonical_workday(source_updated_at),
        'timezone_offset_minutes':minutes,
        'source_updated_at':str(source_updated_at or ''),
        'created_at':storage_values.now(),
    }


def _backup_manifest_info(path:Path) -> dict | None:
    """Return verified retention metadata or None. Uncertain packages are retained."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest=json.loads(z.read("manifest.json"))
        hashes=manifest.get("file_hashes") or {}
        generation=max(0,int(manifest.get('project_generation') or 0))
        if generation>0 and hashes:
            verify_frozen_package_payload(path,dict(hashes),generation)
        # A package eligible for automatic deletion must also prove its SQLite payload
        # is readable. Retention uncertainty always leaks storage rather than data.
        with tempfile.TemporaryDirectory(prefix='breadmaker-retention-verify-') as td:
            db=Path(td)/'project.sqlite3'
            with zipfile.ZipFile(path) as z:z.extract('project.sqlite3',Path(td))
            con=sqlite3.connect(f'file:{db}?mode=ro&immutable=1',uri=True)
            try:
                if str(con.execute('PRAGMA quick_check').fetchone()[0]).lower()!='ok':return None
                if con.execute('PRAGMA foreign_key_check').fetchone() is not None:return None
                cols={str(r[1]) for r in con.execute('PRAGMA table_info(projects)')}
                if 'project_generation' in cols:
                    row=con.execute('SELECT project_generation FROM projects ORDER BY id LIMIT 1').fetchone()
                    if row and int(row[0] or 0)!=generation:return None
            finally:con.close()
            expected_canonical=str(manifest.get('canonical_state_hash') or '')
            if expected_canonical and canonical_state_hash(db,storage_schema_contracts._GENERATION_CANONICAL_TABLES,dict(hashes))!=expected_canonical:return None
        fingerprint=str(manifest.get("project_fingerprint") or storage_values.sha(json.dumps(hashes,sort_keys=True,separators=(",",":"))))
        created=str(manifest.get("backup_created_at") or manifest.get('exported_at') or '')
        dt=datetime.fromisoformat(created.replace('Z','+00:00')) if created else datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        recovery=manifest.get('recovery_metadata') if isinstance(manifest.get('recovery_metadata'),dict) else {}
        return {
            "path":path,"file":path.name,"fingerprint":fingerprint,"canonical_state_hash":str(manifest.get('canonical_state_hash') or ''),"created":dt.astimezone(timezone.utc),
            "created_at":dt.astimezone(timezone.utc).isoformat(),"project_generation":generation,
            "workday":str(recovery.get('workday') or _canonical_workday(manifest.get('updated_at') or created)),
            "recovery_metadata":dict(recovery),
        }
    except Exception:
        return None


def _retain_recovery_backups(profile: str = DEFAULT_RECOVERY_RETENTION_PROFILE, *, backup_dir: Path | None = None) -> dict:
    """7.7.5 generation-first, workday-aware local Recovery retention.

    Only fully verified packages are candidates for deletion. History is a separate
    namespace and is never visible to this function.
    """
    repository=Path(backup_dir or storage_context.current_backup_dir())
    paths=sorted(repository.glob("breadmaker_*.breadmaker"),key=lambda p:p.stat().st_mtime,reverse=True)
    parsed=[];unparsed=[]
    for path in paths:
        info=_backup_manifest_info(path)
        (parsed if info else unparsed).append(info if info else path)
    def authority(info):
        return (int(info.get('project_generation') or 0),info['created'],info['path'].name)
    by_fingerprint={}
    for info in parsed:
        existing=by_fingerprint.get(info['fingerprint'])
        if existing is None or authority(info)>authority(existing):by_fingerprint[info['fingerprint']]=info
    unique=sorted(by_fingerprint.values(),key=authority,reverse=True)
    keep,prune=recovery_retention_partition(unique,profile)
    keep_paths={row['path'] for row in keep}
    removed=0
    # Verified duplicate states not selected as authority are also safe to prune.
    prune_paths={row['path'] for row in prune}
    for info in parsed:
        if info['path'] not in keep_paths:
            prune_paths.add(info['path'])
    for path in prune_paths:
        try:path.unlink();removed+=1
        except OSError:pass
    return {"kept":len(keep_paths)+len(unparsed),"removed":removed,"unverified_retained":len(unparsed),"profile":profile}


def _durable_json_replace(path: Path, value: dict) -> None:
    """Durably replace small rebuildable metadata without sharing temp filenames."""
    import os
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        data=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        with tmp.open('x',encoding='utf-8') as fh:
            fh.write(data);fh.flush();os.fsync(fh.fileno())
        os.replace(tmp,path);storage_assets._fsync_parent(path)
    finally:
        tmp.unlink(missing_ok=True)


def _workspace_manifest_for(root: Path) -> tuple[dict,str]:
    root=Path(root).resolve()
    try:
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError('RECOVERY_TRACKING_MANIFEST_INVALID') from exc
    workspace_id=str(manifest.get('project_id') or '').strip()
    if not re.fullmatch(r'[0-9a-f]{32}',workspace_id):
        raise RuntimeError('RECOVERY_TRACKING_PROJECT_ID_INVALID')
    return manifest,workspace_id


def _recovery_state_path(root:Path) -> Path:
    return Path(root).resolve()/'backups'/_RECOVERY_TRACKING_FILE


def _read_recovery_state(root:Path, workspace_id:str) -> dict:
    path=_recovery_state_path(root)
    try:
        state=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state,dict):raise ValueError('state format')
        version=int(state.get('version') or 0)
        if version!=_RECOVERY_TRACKING_VERSION:raise ValueError('state version')
        if str(state.get('workspace_project_id') or '')!=workspace_id:raise ValueError('workspace mismatch')
        return dict(state)
    except Exception:
        return {}


def _write_recovery_state(root:Path, state:dict) -> None:
    _durable_json_replace(_recovery_state_path(root),state)


def recovery_scheduler_protected_generation(project_root: Path, current_generation: int, *, initialize: bool=False) -> int:
    """Return the highest generation known to have local Recovery protection.

    7.8 moves the tracking baseline out of ``manifest.json``. The baseline is scheduler
    policy, not package authority, and a missing/corrupt safety record fails toward
    *more* backup work. New 7.8 projects receive an explicit uninitialized safety
    record at project creation; only that record may adopt Current as its first
    baseline. Losing it later can therefore never manufacture protection.
    """
    root=Path(project_root).resolve();current=max(0,int(current_generation or 0))
    manifest,workspace_id=_workspace_manifest_for(root)
    state=_read_recovery_state(root,workspace_id)
    version=int(state.get('version') or 0) if state else 0

    if version==0:
        # Missing/corrupt safety metadata is uncertainty, never evidence that Current
        # is protected. Recreate an uninitialized current-format state only.
        state={'version':_RECOVERY_TRACKING_VERSION,'workspace_project_id':workspace_id,'tracking_initialized':False,'baseline_generation':0}
        version=_RECOVERY_TRACKING_VERSION

    tracking_initialized=bool(state.get('tracking_initialized'))
    try:baseline=max(0,int(state.get('baseline_generation') or 0))
    except (TypeError,ValueError):baseline=0
    if not tracking_initialized and initialize:
        # Only the explicit pristine marker created with a new 7.8 project may adopt
        # Current. A reconstructed/missing marker above has baseline=0 but is still
        # indistinguishable from data loss, so it must not silently claim protection.
        pristine=bool(state.get('created_with_project'))
        if pristine:
            baseline=current
        else:
            baseline=0
        state['tracking_initialized']=True;state['baseline_generation']=baseline;state.pop('created_with_project',None)
    baseline=min(current,baseline) if baseline>0 else 0

    last_success=0;changed=False
    try:
        generation=max(0,int(state.get('last_success_generation') or 0))
        filename=Path(str(state.get('package_file') or '')).name
        if not filename or filename!=str(state.get('package_file') or ''):raise ValueError('package filename')
        package=(root/'backups'/filename).resolve();package.relative_to((root/'backups').resolve())
        if not package.is_file() or int(package.stat().st_size)!=int(state.get('package_size') or -1):raise ValueError('package missing or changed')
        with zipfile.ZipFile(package) as zf:package_manifest=json.loads(zf.read('manifest.json'))
        if str(package_manifest.get('project_id') or '')!=workspace_id:raise ValueError('package workspace mismatch')
        if int(package_manifest.get('project_generation') or 0)!=generation:raise ValueError('package generation mismatch')
        if str(package_manifest.get('project_fingerprint') or '')!=str(state.get('project_fingerprint') or ''):raise ValueError('package fingerprint mismatch')
        expected_canonical=str(state.get('canonical_state_hash') or '')
        if expected_canonical and str(package_manifest.get('canonical_state_hash') or '')!=expected_canonical:raise ValueError('canonical state mismatch')

        # Presence/manifest checks are cheap and run frequently. Full package-byte
        # verification is periodic so latent same-size corruption cannot remain
        # "protected" forever without hashing multi-GB archives every scheduler poll.
        scrubbed_raw=str(state.get('last_scrubbed_at') or '')
        try:
            scrubbed=datetime.fromisoformat(scrubbed_raw.replace('Z','+00:00')) if scrubbed_raw else None
            if scrubbed and scrubbed.tzinfo is None:scrubbed=scrubbed.replace(tzinfo=timezone.utc)
        except ValueError:scrubbed=None
        due=scrubbed is None or (datetime.now(timezone.utc)-scrubbed.astimezone(timezone.utc)).total_seconds()>=_RECOVERY_SCRUB_INTERVAL_SECONDS
        expected_package_sha=str(state.get('package_sha256') or '').lower()
        # Once a verified Recovery package has been recorded, its SHA-256 is authority.
        # Missing/malformed authority is uncertainty; never bless whatever bytes happen
        # to be present by computing a replacement hash from them.
        if len(expected_package_sha)!=64 or any(ch not in '0123456789abcdef' for ch in expected_package_sha):
            raise ValueError('package hash authority missing')
        if due:
            actual_sha=storage_values.sha_file(package).lower()
            if actual_sha!=expected_package_sha:raise ValueError('package hash mismatch')
            state['last_scrubbed_at']=datetime.now(timezone.utc).isoformat(timespec='microseconds');changed=True
        last_success=min(current,generation)
    except Exception:
        last_success=0

    state['version']=_RECOVERY_TRACKING_VERSION;state['workspace_project_id']=workspace_id
    state['tracking_initialized']=bool(state.get('tracking_initialized'));state['baseline_generation']=baseline
    if initialize or changed or version!=_RECOVERY_TRACKING_VERSION:
        _write_recovery_state(root,state)
    return max(baseline,last_success)


def _record_recovery_scheduler_success(project_root: Path, package: Path, generation: int, fingerprint: str, canonical_hash: str='') -> None:
    root=Path(project_root).resolve();package=Path(package).resolve()
    _manifest,workspace_id=_workspace_manifest_for(root)
    backups=(root/'backups').resolve()
    try:package.relative_to(backups)
    except ValueError as exc:raise RuntimeError('RECOVERY_TRACKING_PACKAGE_OUTSIDE_REPOSITORY') from exc
    if not package.is_file():raise RuntimeError('RECOVERY_TRACKING_PACKAGE_MISSING')
    state=_read_recovery_state(root,workspace_id)
    # The one-time baseline exists only until Breadmaker establishes its first real
    # verified Recovery package. From this point onward package authority is the sole
    # protection high-water; if that authority is later lost/corrupt, protection falls
    # to zero rather than surviving through a historical no-package baseline.
    package_sha=storage_values.sha_file(package)
    _write_recovery_state(root,{
        'version':_RECOVERY_TRACKING_VERSION,'workspace_project_id':workspace_id,
        'tracking_initialized':True,'baseline_generation':0,
        'last_success_generation':int(generation),'project_fingerprint':str(fingerprint or ''),
        'canonical_state_hash':str(canonical_hash or ''),'package_file':package.name,
        'package_size':int(package.stat().st_size),'package_sha256':package_sha,
        'last_scrubbed_at':datetime.now(timezone.utc).isoformat(timespec='microseconds'),
        'recorded_at':storage_values.now(),
    })


def _existing_recovery_backup(backup_dir: Path, generation: int, fingerprint: str, canonical_hash: str='') -> Path | None:
    """Return an already-verified local Recovery point for one logical state.

    Canonical state identity is preferred over physical SQLite/package fingerprint.
    This keeps derived non-canonical metadata from manufacturing duplicate Recovery points
    for the same authoritative generation.
    """
    matches=[]
    for path in Path(backup_dir).glob("breadmaker_*.breadmaker"):
        info=_backup_manifest_info(path)
        if not info:continue
        if int(info.get('project_generation') or 0)!=int(generation):continue
        if canonical_hash:
            if str(info.get('canonical_state_hash') or '')!=str(canonical_hash):continue
        elif str(info.get('fingerprint') or '')!=str(fingerprint or ''):
            continue
        matches.append(info)
    if not matches:
        return None
    matches.sort(key=lambda row:(int(row.get('project_generation') or 0),row['created'],row['path'].name),reverse=True)
    return Path(matches[0]['path'])


def backup_project(reason: str = "manual", *, project_root: Path | None = None) -> Path | None:
    """Create one verified local Recovery point from one frozen generation.

    Backup work is serialized independently from Current. This makes concurrent
    manual/background/retried requests idempotent for an identical canonical
    generation+fingerprint while canonical editing remains completely unblocked.
    All project paths are captured before I/O so a later project switch cannot make
    retention or packaging operate on a different repository.
    """
    if project_root is None:
        storage_database.ensure_dirs()
    # Background scheduling may finish an operation after the user switches books.
    # Accept an explicit project root so that operation remains bound to the project
    # it captured instead of following mutable module-level storage globals.
    bound=storage_context.current_project_root()
    captured_root=Path(project_root).resolve() if project_root is not None else (Path(bound).resolve() if bound is not None else None)
    if captured_root is None:
        return None
    db_path=captured_root/'project.sqlite3'
    asset_dir=captured_root/'assets'
    backup_dir=captured_root/'backups'
    # Never create directories for a stale queued job. Delete All may remove the old
    # project while this request is waiting for the Recovery boundary.
    if not db_path.is_file():
        return None

    with storage_coordination._RECOVERY_BACKUP_LOCK:
        # Re-check before *any* mkdir. A queued pre-reset Recovery job must not be able
        # to recreate the erased project tree after Delete All commits.
        if not db_path.is_file():
            return None
        asset_dir.mkdir(parents=True,exist_ok=True);backup_dir.mkdir(parents=True,exist_ok=True)
        manifest_path=captured_root / "manifest.json"
        base_manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
            "package_format":"breadmaker-project","package_version":2,"schema_version":SCHEMA_VERSION,"project_id":"recovery","name":"Recovered Project"
        }
        with tempfile.TemporaryDirectory(prefix='breadmaker-frozen-recovery-') as td:
            try:
                frozen=capture_frozen_snapshot(db_path,asset_dir,Path(td))
            except FrozenSnapshotError as exc:
                raise RuntimeError(str(exc)) from exc

            canonical_hash=canonical_state_hash(frozen.database,storage_schema_contracts._GENERATION_CANONICAL_TABLES,frozen.file_hashes)
            existing=_existing_recovery_backup(backup_dir,frozen.project_generation,frozen.project_fingerprint,canonical_hash)
            if existing is not None:
                # Idempotent retries/concurrent requests converge on one immutable
                # package. Retention still runs in case older verified points became
                # eligible while this request waited for the backup boundary.
                _retain_recovery_backups(backup_dir=backup_dir)
                if existing.is_file():
                    _record_recovery_scheduler_success(captured_root,existing,frozen.project_generation,frozen.project_fingerprint,canonical_hash)
                return existing

            stamp=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe="".join(c for c in reason if c.isalnum() or c in "-_")[:30] or "backup"
            dest=backup_dir / f"breadmaker_{stamp}_{safe}.breadmaker"
            manifest={key:base_manifest[key] for key in storage_context.PORTABLE_PROJECT_MANIFEST_KEYS if key in base_manifest}
            manifest.setdefault('package_format','breadmaker-project')
            manifest.setdefault('package_version',2)
            manifest['schema_version']=int(frozen.schema_version or SCHEMA_VERSION)
            manifest.setdefault('project_id',str(base_manifest.get('project_id') or 'recovery'))
            manifest['name']=frozen.project_title or str(base_manifest.get('name') or 'Recovered Project')
            manifest.update({
                "backup_created_at":storage_values.now(),"backup_reason":reason,
                "recovery_metadata":_recovery_metadata(reason,frozen.project_updated_at or base_manifest.get('updated_at')),
                "project_generation":frozen.project_generation,
                "snapshot_source":"frozen-canonical-generation",
                "file_hashes":dict(frozen.file_hashes),
                "project_fingerprint":frozen.project_fingerprint,
                "canonical_state_hash":canonical_hash,
            })
            tmp=dest.with_suffix(dest.suffix+".tmp")
            try:
                with zipfile.ZipFile(tmp,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                    z.write(frozen.database,"project.sqlite3")
                    z.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
                    for asset,rel in frozen.assets:
                        z.write(asset,rel)
                verify_frozen_package_payload(tmp,dict(frozen.file_hashes),frozen.project_generation)
                tmp.replace(dest); storage_assets._fsync_parent(dest)
            finally:
                tmp.unlink(missing_ok=True)
        _retain_recovery_backups(backup_dir=backup_dir)
        if not dest.is_file():
            raise RuntimeError('RECOVERY_BACKUP_RETAINED_PACKAGE_MISSING')
        _record_recovery_scheduler_success(captured_root,dest,frozen.project_generation,frozen.project_fingerprint,canonical_hash)
        return dest
