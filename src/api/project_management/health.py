from __future__ import annotations

"""Deep project health and storage-accounting HTTP surface."""

from pathlib import Path
from fastapi import APIRouter

from backend.data_access import project
from backend.environment import runtime
from runtime.native import require_native_projects, require_native_assets, require_native_backups
from storage import assets as storage_assets
from storage import context as storage_context
from storage import integrity as storage_integrity
from storage import mutations as storage_mutations
from storage import undo as storage_undo

router=APIRouter()

@router.get("/api/project/health")
def project_health():
    """Deep project verification plus complete on-disk/storage accounting.

    The UI opens its verification modal *before* calling this endpoint so this
    intentionally thorough check never makes the Verify button feel dead.
    """
    project_root=Path(storage_context.current_project_root()) if storage_context.current_project_root() else None
    def tree_bytes(path:Path|None) -> int:
        if not path or not path.exists(): return 0
        if path.is_file(): return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    assets_dir=require_native_assets(runtime).root
    backups_dir=require_native_backups(runtime).recovery_dir
    exports_dir=(project_root/'exports') if project_root else None
    logs_dir=(project_root/'logs') if project_root else None
    db_path=storage_context.current_db_path()
    db_wal=Path(str(db_path)+'-wal')
    db_shm=Path(str(db_path)+'-shm')
    deep=storage_integrity.deep_integrity_check();integrity={'ok':bool(deep.get('ok')),'deep_ok':bool(deep.get('ok')),'deep_errors':deep.get('errors',[]),'history_v1':deep.get('history_v1',{}),'checkpoints':deep.get('checkpoints',{}),'disk':deep.get('disk',{})}
    if not deep.get('ok',False): integrity['error']='; '.join(deep.get('errors') or ['Deep integrity verification failed'])
    with storage_mutations.tx(operation='project_health_counts',track_generation=False) as con:
        p=project(con);pid=int(p['id']) if p else 0
        storage_report={'history_revisions':len(runtime.history.list_revisions()),'checkpoints':int(con.execute('SELECT COUNT(*) FROM checkpoints WHERE project_id=?',(pid,)).fetchone()[0]) if pid else 0,'cuts':int(con.execute('SELECT COUNT(*) FROM cuts WHERE project_id=?',(pid,)).fetchone()[0]) if pid else 0,**storage_assets.checkpoint_storage_report(con)}
    integrity['disk']=deep.get('disk',{})
    backups=sorted(backups_dir.glob("*.breadmaker"),key=lambda p:p.stat().st_mtime,reverse=True) if backups_dir and backups_dir.exists() else []
    latest=None
    if backups:
        try: latest={**require_native_projects(runtime).verify_package(backups[0]),"file":backups[0].name,"verified":True,"bytes":backups[0].stat().st_size}
        except Exception as exc: latest={"file":backups[0].name,"verified":False,"error":str(exc),"bytes":backups[0].stat().st_size}
    asset_files=[p for p in assets_dir.rglob('*') if p.is_file()] if assets_dir.exists() else []
    export_files=[p for p in exports_dir.rglob('*') if p.is_file()] if exports_dir and exports_dir.exists() else []
    recovery_files=[p for p in backups_dir.rglob('*') if p.is_file()] if backups_dir and backups_dir.exists() else []
    project_total=tree_bytes(project_root)
    return {
        "database":integrity,
        "project_root":str(project_root or '') if runtime.capabilities.supports('native_filesystem') else None,
        "sizes":{
            "project_total":project_total,
            "database":db_path.stat().st_size if db_path.exists() else 0,
            "wal":db_wal.stat().st_size if db_wal.exists() else 0,
            "shm":db_shm.stat().st_size if db_shm.exists() else 0,
            "assets":tree_bytes(assets_dir),
            "recovery":tree_bytes(backups_dir),
            "exports":tree_bytes(exports_dir),
            "logs":tree_bytes(logs_dir),
        },
        "counts":{
            "assets":len(asset_files),
            "recovery_files":len(recovery_files),
            "recovery_packages":len(backups),
            "exports":len(export_files),
            "history":int(storage_report.get('history_revisions') or 0),
            "undo":storage_undo.session_undo_count(),
            "checkpoints":int(storage_report.get('checkpoints') or 0),
            "cuts":int(storage_report.get('cuts') or 0),
            "checkpoint_assets":int(storage_report.get('assets') or 0),
        },
        "storage":storage_report,
        "latest_backup":latest,
    }
