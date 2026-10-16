from __future__ import annotations
import hashlib, json, os, shutil, sqlite3, tempfile, uuid, zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backups.frozen_snapshot import capture_frozen_snapshot, verify_frozen_package_payload, FrozenSnapshotError
from revision_store.local_store import canonical_state_hash
from storage import schema_contracts as storage_schema_contracts
from security import content_security
from workspace.constants import APP_SCHEMA_VERSION, PACKAGE_VERSION, MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES, MAX_MANIFEST_BYTES, HASH_CHUNK_BYTES, PORTABLE_PROJECT_MANIFEST_KEYS
from workspace.models import ProjectRecord, WorkspaceError
from workspace.filesystem import _database_summary, _durable_replace, _recovery_metadata, _sha256_zip_entry, now, safe_name

class ProjectPackageMixin:
    def export_project(self, project_id: str, destination: Path, *, recovery_reason: str='export') -> Path:
        """Export one immutable canonical project generation as a portable package.

        7.7.1 never lets the ZIP builder read the live database/assets.  Capture is
        completed into a private frozen directory first; packaging consumes only that
        directory afterward, so edits that happen during compression cannot leak into
        the artifact.
        """
        from storage import schema_contracts as storage_schema_contracts
        item=self.get(project_id); root=Path(item["path"]); base_manifest=self.read_manifest(root)
        destination=destination.resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
        tmp=destination.with_suffix(destination.suffix+".tmp")
        db_src=root/"project.sqlite3"
        if not db_src.exists(): raise WorkspaceError("PROJECT_DATABASE_MISSING")
        try:
            with tempfile.TemporaryDirectory(prefix='breadmaker-frozen-snapshot-') as td:
                try:
                    frozen=capture_frozen_snapshot(db_src,root/"assets",Path(td))
                except FrozenSnapshotError as exc:
                    raise WorkspaceError(str(exc)) from exc
                manifest={key:base_manifest[key] for key in PORTABLE_PROJECT_MANIFEST_KEYS if key in base_manifest}
                manifest.update({
                    'package_format':'breadmaker-project','package_version':PACKAGE_VERSION,
                    'schema_version':int(frozen.schema_version or base_manifest.get('schema_version') or APP_SCHEMA_VERSION),
                    'project_id':str(base_manifest.get('project_id') or project_id),
                    'name':frozen.project_title or str(base_manifest.get('name') or item.get('name') or 'Untitled Book'),
                    'created_at':str(frozen.project_created_at or base_manifest.get('created_at') or item.get('created_at') or now()),
                    'updated_at':str(frozen.project_updated_at or base_manifest.get('updated_at') or item.get('updated_at') or now()),
                    'recovery_metadata':_recovery_metadata(recovery_reason,frozen.project_updated_at or base_manifest.get('updated_at') or item.get('updated_at')),
                    'project_generation':frozen.project_generation,
                    'snapshot_source':'frozen-canonical-generation',
                    "exported_at":now(),"file_hashes":dict(frozen.file_hashes),"project_fingerprint":frozen.project_fingerprint,
                    "canonical_state_hash":canonical_state_hash(frozen.database,storage_schema_contracts._GENERATION_CANONICAL_TABLES,frozen.file_hashes),
                })
                with zipfile.ZipFile(tmp,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as zf:
                    zf.writestr("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False))
                    zf.write(frozen.database,"project.sqlite3")
                    for path,rel in frozen.assets: zf.write(path,rel)
                verify_frozen_package_payload(tmp,dict(frozen.file_hashes),frozen.project_generation)
                _durable_replace(tmp,destination)
        finally:
            tmp.unlink(missing_ok=True)
        return destination

    def export_all_data(self, destination: Path) -> Path:
        """Create a consistent ZIP of the complete Breadmaker data home.

        Project SQLite databases are copied through SQLite's backup API so committed WAL
        pages are included without relying on a raw file copy. WAL/SHM files and the
        live process-lock file are intentionally omitted because the database snapshots
        already contain the durable state they represent. Everything else beneath the
        configured Breadmaker home is preserved at the same relative path.
        """
        destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
        root=self.root.resolve()
        tmp=destination.with_suffix(destination.suffix+'.tmp')
        with self._lock, tempfile.TemporaryDirectory(prefix='breadmaker-data-export-') as td:
            snapshots: dict[Path, Path] = {}
            for project_dir in sorted(self.projects_dir.glob('*')) if self.projects_dir.exists() else []:
                db_src=project_dir/'project.sqlite3'
                if not db_src.is_file():
                    continue
                snap=Path(td)/f'{project_dir.name}.sqlite3'
                src=sqlite3.connect(db_src,timeout=30);dst=sqlite3.connect(snap)
                try:
                    src.backup(dst);dst.commit()
                finally:
                    dst.close();src.close()
                verify=sqlite3.connect(snap)
                try:
                    if verify.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise WorkspaceError(f'DATA_EXPORT_DATABASE_CORRUPT:{project_dir.name}')
                    if verify.execute('PRAGMA foreign_key_check').fetchone() is not None:
                        raise WorkspaceError(f'DATA_EXPORT_FOREIGN_KEYS_INVALID:{project_dir.name}')
                finally:
                    verify.close()
                snapshots[db_src.resolve()]=snap
            try:
                with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as zf:
                    for path in sorted(root.rglob('*')):
                        if not path.is_file():
                            continue
                        resolved=path.resolve();name=path.name
                        if name in {'project.sqlite3-wal','project.sqlite3-shm','breadmaker_editor.lock','backup_credentials.json','credential_obfuscation.key','credential_encryption.key','google_oauth_client.json'}:
                            continue
                        if name=='project.sqlite3' and resolved in snapshots:
                            zf.write(snapshots[resolved],path.relative_to(root).as_posix())
                        else:
                            zf.write(path,path.relative_to(root).as_posix())
                _durable_replace(tmp,destination)
            finally:
                tmp.unlink(missing_ok=True)
        return destination

    def verify_package(self, package_path: Path) -> dict[str, Any]:
        """Validate an export/backup without importing it."""
        package_path=Path(package_path).resolve()
        if not zipfile.is_zipfile(package_path): raise WorkspaceError("INVALID_PROJECT_PACKAGE")
        with zipfile.ZipFile(package_path) as zf:
            info=[i for i in zf.infolist() if not i.is_dir()]
            names={i.filename for i in info}
            if len(names)!=len(info): raise WorkspaceError("PROJECT_PACKAGE_DUPLICATE_PATH")
            if "manifest.json" not in names or "project.sqlite3" not in names: raise WorkspaceError("INVALID_PROJECT_PACKAGE")
            if any(name not in {"manifest.json","project.sqlite3"} and not name.startswith("assets/") for name in names): raise WorkspaceError("PROJECT_PACKAGE_UNEXPECTED_FILE")
            if len(info)>MAX_PACKAGE_FILES: raise WorkspaceError("PROJECT_PACKAGE_TOO_MANY_FILES")
            total=sum(i.file_size for i in info)
            if total>MAX_PACKAGE_BYTES: raise WorkspaceError("PROJECT_PACKAGE_TOO_LARGE")
            manifest_info=zf.getinfo("manifest.json")
            if manifest_info.file_size>MAX_MANIFEST_BYTES: raise WorkspaceError("PROJECT_MANIFEST_TOO_LARGE")
            for i in info:
                path=Path(i.filename)
                if path.is_absolute() or ".." in path.parts: raise WorkspaceError("UNSAFE_PROJECT_PACKAGE")
            try:
                with zf.open("manifest.json") as manifest_fh:
                    manifest=json.loads(manifest_fh.read(MAX_MANIFEST_BYTES+1))
            except Exception as exc: raise WorkspaceError("INVALID_PROJECT_MANIFEST") from exc
            if manifest.get("package_format")!="breadmaker-project": raise WorkspaceError("INVALID_PROJECT_MANIFEST")
            if int(manifest.get("package_version",0))!=PACKAGE_VERSION: raise WorkspaceError("UNSUPPORTED_PACKAGE_VERSION")
            if int(manifest.get("schema_version",0)) != APP_SCHEMA_VERSION: raise WorkspaceError("UNSUPPORTED_PROJECT_VERSION")
            hashes=manifest.get("file_hashes")
            if not isinstance(hashes,dict) or set(hashes)!=(names-{"manifest.json"}): raise WorkspaceError("PROJECT_PACKAGE_HASH_SET_MISMATCH")
            for rel,expected in hashes.items():
                if not isinstance(expected,str) or len(expected)!=64: raise WorkspaceError("INVALID_PROJECT_HASH")
                if _sha256_zip_entry(zf,rel)!=expected: raise WorkspaceError("PROJECT_PACKAGE_HASH_MISMATCH")
            # SQLite verification is done on a temporary extracted database so the
            # archive's structural integrity is checked, not merely its SHA hash.
            with tempfile.TemporaryDirectory(prefix="breadmaker-verify-") as td:
                db=Path(td)/"project.sqlite3"
                with zf.open("project.sqlite3") as src, db.open("wb") as dst:
                    shutil.copyfileobj(src,dst,length=HASH_CHUNK_BYTES)
                check=sqlite3.connect(db)
                try:
                    quick=check.execute("PRAGMA quick_check").fetchone()[0]
                    foreign=check.execute("PRAGMA foreign_key_check").fetchone()
                    if quick!="ok": raise WorkspaceError("PROJECT_DATABASE_CORRUPT")
                    if foreign is not None: raise WorkspaceError("PROJECT_FOREIGN_KEYS_INVALID")
                    unsafe_html=content_security.find_unsafe_project_html(check)
                    if unsafe_html is not None:
                        raise WorkspaceError("PROJECT_PACKAGE_UNSAFE_HTML")
                    tables={str(r[0]) for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    db_generation=0
                    if 'projects' in tables:
                        project_cols={str(r[1]) for r in check.execute("PRAGMA table_info(projects)")}
                        if 'project_uid' not in project_cols:raise WorkspaceError("PROJECT_PACKAGE_IDENTITY_MISSING")
                        identity_row=check.execute("SELECT project_uid FROM projects ORDER BY id LIMIT 1").fetchone()
                        db_project_uid=str(identity_row[0] or '') if identity_row else ''
                        if db_project_uid!=str(manifest.get('project_id') or ''):raise WorkspaceError("PROJECT_PACKAGE_IDENTITY_MISMATCH")
                        if 'project_generation' in project_cols:
                            grow=check.execute("SELECT project_generation FROM projects ORDER BY id LIMIT 1").fetchone()
                            db_generation=int(grow[0] or 0) if grow else 0
                    package_schema=int(manifest.get('schema_version') or 0)
                    manifest_generation=int(manifest.get('project_generation') or 0)
                    if package_schema>=20:
                        if db_generation<1 or manifest_generation!=db_generation:
                            raise WorkspaceError("PROJECT_PACKAGE_GENERATION_MISMATCH")
                    asset_refs=[]
                    if 'manuscript_blocks' in tables:
                        asset_refs.extend(str(row[0] or '') for row in check.execute("SELECT image_path FROM manuscript_blocks WHERE COALESCE(image_path,'')<>''"))
                    if 'canvas_nodes' in tables:
                        asset_refs.extend(str(row[0] or '') for row in check.execute("SELECT image_path FROM canvas_nodes WHERE COALESCE(image_path,'')<>''"))
                    if 'review_snapshot_blocks' in tables:
                        asset_refs.extend(str(row[0] or '') for row in check.execute("SELECT image_path FROM review_snapshot_blocks WHERE COALESCE(image_path,'')<>''"))
                    if 'cuts' in tables:
                        import re as _re
                        for row in check.execute("SELECT content_html FROM cuts WHERE COALESCE(content_html,'')<>''"):
                            asset_refs.extend('/api/assets/'+name for name in _re.findall(r'/api/assets/([A-Za-z0-9._-]+)',str(row[0] or '')))
                    for value in asset_refs:
                        if value.startswith('/api/assets/') and ('assets/'+value.removeprefix('/api/assets/')) not in names:
                            raise WorkspaceError("PROJECT_PACKAGE_CURRENT_ASSET_MISSING")
                    computed_canonical=canonical_state_hash(db,storage_schema_contracts._GENERATION_CANONICAL_TABLES,hashes)
                    expected_canonical=str(manifest.get('canonical_state_hash') or '')
                    if expected_canonical and computed_canonical!=expected_canonical:
                        raise WorkspaceError('PROJECT_PACKAGE_CANONICAL_STATE_MISMATCH')
                finally: check.close()
            return {
                "ok":True,"name":manifest.get("name") or "Recovered Project",
                "project_id":manifest.get("project_id") or "",
                "schema_version":manifest.get("schema_version"),"package_version":manifest.get("package_version"),
                "asset_count":sum(1 for n in names if n.startswith("assets/") and not n.endswith("/")),
                "bytes":package_path.stat().st_size,"created_at":manifest.get("backup_created_at") or manifest.get("exported_at"),
                "reason":manifest.get("backup_reason") or "export",
                "project_generation":int(manifest.get("project_generation") or 0),
                "recovery_metadata":dict(manifest.get("recovery_metadata") or {}) if isinstance(manifest.get("recovery_metadata"),dict) else {},
                "project_fingerprint":manifest.get("project_fingerprint") or hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                "canonical_state_hash":str(manifest.get("canonical_state_hash") or computed_canonical),
            }

    def inspect_package_metadata(self, package_path:Path) -> dict[str,Any]:
        """Read bounded package metadata without hashing/extracting the payload.

        This is intentionally *not* an integrity verdict. It is safe-enough discovery
        metadata for an interactive list. Restore/import still calls verify_package.
        """
        package_path=Path(package_path).resolve()
        if not zipfile.is_zipfile(package_path):raise WorkspaceError('INVALID_PROJECT_PACKAGE')
        with zipfile.ZipFile(package_path) as zf:
            info=[i for i in zf.infolist() if not i.is_dir()]
            names={i.filename for i in info}
            if len(names)!=len(info):raise WorkspaceError('PROJECT_PACKAGE_DUPLICATE_PATH')
            if 'manifest.json' not in names or 'project.sqlite3' not in names:raise WorkspaceError('INVALID_PROJECT_PACKAGE')
            if len(info)>MAX_PACKAGE_FILES:raise WorkspaceError('PROJECT_PACKAGE_TOO_MANY_FILES')
            manifest_info=zf.getinfo('manifest.json')
            if manifest_info.file_size>MAX_MANIFEST_BYTES:raise WorkspaceError('PROJECT_MANIFEST_TOO_LARGE')
            try:
                with zf.open('manifest.json') as manifest_fh:
                    raw=manifest_fh.read(MAX_MANIFEST_BYTES+1)
                if len(raw)>MAX_MANIFEST_BYTES:raise WorkspaceError('PROJECT_MANIFEST_TOO_LARGE')
                manifest=json.loads(raw)
            except WorkspaceError:raise
            except Exception as exc:raise WorkspaceError('INVALID_PROJECT_MANIFEST') from exc
            if manifest.get('package_format')!='breadmaker-project':raise WorkspaceError('INVALID_PROJECT_MANIFEST')
            if int(manifest.get('package_version',0))!=PACKAGE_VERSION:raise WorkspaceError('UNSUPPORTED_PACKAGE_VERSION')
            if int(manifest.get('schema_version',0)) != APP_SCHEMA_VERSION:raise WorkspaceError('UNSUPPORTED_PROJECT_VERSION')
            stat=package_path.stat()
            return {
                'file':package_path.name,'candidate':True,'verified':False,
                'verification_status':'not_verified','name':manifest.get('name') or 'Recovered Project',
                'project_id':str(manifest.get('project_id') or ''),
                'schema_version':manifest.get('schema_version'),'package_version':manifest.get('package_version'),
                'created_at':manifest.get('backup_created_at') or manifest.get('exported_at'),
                'reason':manifest.get('backup_reason') or 'export','bytes':stat.st_size,
                'modified_ns':stat.st_mtime_ns,
                'project_generation':int(manifest.get('project_generation') or 0),
                'project_fingerprint':str(manifest.get('project_fingerprint') or ''),
            }

    def import_project(self, package_path: Path, name_override: str | None=None, *, owner_user_id:str|None=None) -> dict[str, Any]:
        package_path=package_path.resolve()
        self.verify_package(package_path)
        if not zipfile.is_zipfile(package_path): raise WorkspaceError("INVALID_PROJECT_PACKAGE")
        with zipfile.ZipFile(package_path) as zf:
            info=[i for i in zf.infolist() if not i.is_dir()]
            names={i.filename for i in info}
            if len(names)!=len(info): raise WorkspaceError("PROJECT_PACKAGE_DUPLICATE_PATH")
            if "manifest.json" not in names or "project.sqlite3" not in names: raise WorkspaceError("INVALID_PROJECT_PACKAGE")
            if any(name not in {"manifest.json","project.sqlite3"} and not name.startswith("assets/") for name in names): raise WorkspaceError("PROJECT_PACKAGE_UNEXPECTED_FILE")
            if len(info)>MAX_PACKAGE_FILES: raise WorkspaceError("PROJECT_PACKAGE_TOO_MANY_FILES")
            total=sum(i.file_size for i in info)
            if total>MAX_PACKAGE_BYTES: raise WorkspaceError("PROJECT_PACKAGE_TOO_LARGE")
            if zf.getinfo("manifest.json").file_size>MAX_MANIFEST_BYTES: raise WorkspaceError("PROJECT_MANIFEST_TOO_LARGE")
            for i in info:
                p=Path(i.filename)
                if p.is_absolute() or ".." in p.parts: raise WorkspaceError("UNSAFE_PROJECT_PACKAGE")
            try:
                with zf.open("manifest.json") as manifest_fh:
                    manifest=json.loads(manifest_fh.read(MAX_MANIFEST_BYTES+1))
            except Exception as exc: raise WorkspaceError("INVALID_PROJECT_MANIFEST") from exc
            if manifest.get("package_format")!="breadmaker-project": raise WorkspaceError("INVALID_PROJECT_MANIFEST")
            if int(manifest.get("package_version",0))!=PACKAGE_VERSION: raise WorkspaceError("UNSUPPORTED_PACKAGE_VERSION")
            if int(manifest.get("schema_version",0)) != APP_SCHEMA_VERSION: raise WorkspaceError("UNSUPPORTED_PROJECT_VERSION")
            rec=self.create_project(name_override or manifest.get("name") or "Imported Book",activate=False,owner_user_id=owner_user_id)
            root=Path(rec["path"])
            try:
                for i in info:
                    if i.filename=="manifest.json": continue
                    target=root/i.filename; target.parent.mkdir(parents=True,exist_ok=True)
                    with zf.open(i) as src, target.open("wb") as dst: shutil.copyfileobj(src,dst,length=1024*1024)
                check=sqlite3.connect(root/"project.sqlite3")
                try:
                    if check.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise WorkspaceError("PROJECT_DATABASE_CORRUPT")
                    if check.execute("PRAGMA foreign_key_check").fetchone() is not None: raise WorkspaceError("PROJECT_FOREIGN_KEYS_INVALID")
                    row=check.execute("SELECT project_uid FROM projects ORDER BY id LIMIT 1").fetchone()
                    if not row or str(row[0] or '')!=str(manifest.get('project_id') or ''):raise WorkspaceError("PROJECT_PACKAGE_IDENTITY_MISMATCH")
                    check.execute("UPDATE projects SET project_uid=?",(rec["id"],));check.commit()
                finally: check.close()
                # Defensive portability boundary: older packages may contain the source
                # machine's backup/provider configuration. Never install those fields.
                new_manifest={key:manifest[key] for key in PORTABLE_PROJECT_MANIFEST_KEYS if key in manifest}
                new_manifest.update({"project_id":rec["id"],"name":rec["name"],"updated_at":now()})
                self._write_manifest(root,new_manifest)
                # The blank shell created above is not evidence that this imported
                # canonical generation has a local Recovery point. Force scheduler
                # state to unknown/unprotected until a package is actually verified.
                self._write_initial_recovery_state(root,rec['id'],pristine_new_project=False)
            except Exception:
                self.delete_project(rec["id"], permanent=True)
                raise
            return rec
