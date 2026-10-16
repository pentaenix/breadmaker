from __future__ import annotations

"""Deep project integrity and disk-health diagnostics."""

import json
import re
from pathlib import Path

from storage import context as storage_context
from storage import database as storage_database
from storage import history as storage_history
from storage import invariants as storage_invariants
from storage import values as storage_values
from security import content_security

def disk_health_report() -> dict:
    import shutil as _shutil
    usage=_shutil.disk_usage(storage_context.current_data_dir())
    free=int(usage.free); total=int(usage.total); ratio=(free/total) if total else 1.0
    return {'total_bytes':total,'free_bytes':free,'free_ratio':ratio,'warning':free < 2*1024**3 or ratio < .05,'critical':free < 512*1024**2 or ratio < .01}


def _current_asset_reference_problem(api_path: object) -> str | None:
    """Return a reason when a Current renderable asset reference is unsafe or missing."""
    value=str(api_path or '').strip()
    if not value:return None
    if not content_security.is_safe_project_asset_path(value):
        return 'invalid asset path'
    raw=value.removeprefix('/api/assets/')
    rel=Path(raw)
    if not raw or rel.is_absolute() or '..' in rel.parts or any(part in {'','.','..'} for part in rel.parts):
        return 'invalid asset path'
    root=Path(storage_context.current_asset_dir()).resolve()
    candidate=(root/rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return 'asset path escaped project root'
    if not candidate.is_file():
        return 'asset file missing'
    return None


def deep_integrity_check() -> dict:
    """Verify Current, v1 History, referenced assets, and Checkpoint asset payloads."""
    errors=[]; history_v1={'ok':True,'revisions':0}; checkpoints={'ok':True,'assets':0,'asset_refs':0}
    with storage_context._project_db_lock():
        con=storage_database.connect()
        try:
            try:
                quick=con.execute('PRAGMA quick_check').fetchone()[0]
                if quick!='ok': errors.append('SQLite quick_check failed')
                fk=con.execute('PRAGMA foreign_key_check').fetchall()
                if fk: errors.append('SQLite foreign key check failed')
                for prow in con.execute('SELECT id FROM projects').fetchall():
                    errors.extend(storage_invariants.project_invariant_errors(con,int(prow[0])))
                # Content hashes are part of Current integrity, not merely an
                # optimization. A non-empty stored hash must agree with the
                # exact persisted block representation. (Brand-new empty
                # placeholder blocks may intentionally have no hash yet.)
                for row in con.execute(
                    "SELECT id,content_html,text_content,image_path,image_width,image_footer,image_description,image_row_id,content_hash FROM manuscript_blocks"
                ).fetchall():
                    stored=str(row['content_hash'] or '')
                    if stored:
                        expected=storage_values.block_content_hash(
                            str(row['content_html'] or ''),
                            str(row['text_content'] or ''),
                            str(row['image_path'] or ''),
                            int(row['image_width'] or 100),
                            str(row['image_footer'] or ''),
                            str(row['image_description'] or ''),
                            str(row['image_row_id'] or ''),
                        )
                        if stored!=expected:
                            errors.append(f"Current block hash mismatch: {row['id']}")
                # Every Current asset reference remains live regardless of which
                # feature owns it. Preserve the owner in diagnostics so a failed
                # integrity check tells us exactly which current feature is damaged.
                current_asset_refs=[]
                current_asset_refs.extend(('manuscript block',str(row['image_path'] or '')) for row in con.execute(
                    "SELECT image_path FROM manuscript_blocks WHERE COALESCE(image_path,'')<>''"
                ).fetchall())
                current_asset_refs.extend(('canvas node',str(row['image_path'] or '')) for row in con.execute(
                    "SELECT image_path FROM canvas_nodes WHERE COALESCE(image_path,'')<>''"
                ).fetchall())
                current_asset_refs.extend(('review snapshot',str(row['image_path'] or '')) for row in con.execute(
                    "SELECT image_path FROM review_snapshot_blocks WHERE COALESCE(image_path,'')<>''"
                ).fetchall())
                for row in con.execute("SELECT content_html FROM cuts WHERE COALESCE(content_html,'')<>''").fetchall():
                    current_asset_refs.extend(('cut','/api/assets/'+name) for name in re.findall(r'/api/assets/([A-Za-z0-9._/\-]+)',str(row['content_html'] or '')))
                for owner,api in sorted(set(current_asset_refs)):
                    problem=_current_asset_reference_problem(api)
                    if problem:errors.append(f'{owner} asset invalid: {api}: {problem}')
                arow=con.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM checkpoint_assets').fetchone()
                refs=con.execute('SELECT checkpoint_id,asset_path,asset_hash FROM checkpoint_asset_refs').fetchall()
                checkpoints={'ok':True,'assets':int(arow[0]),'asset_bytes':int(arow[1]),'asset_refs':len(refs)}
                for ref in refs:
                    asset=con.execute('SELECT payload,byte_size FROM checkpoint_assets WHERE hash=?',(ref['asset_hash'],)).fetchone()
                    if not asset or int(asset['byte_size'])!=len(bytes(asset['payload'])) or storage_values.sha(bytes(asset['payload']))!=str(ref['asset_hash']):
                        errors.append(f'Checkpoint asset corrupt: {ref["asset_path"]}');checkpoints['ok']=False
            except Exception as exc:
                errors.append(f'Current integrity verification failed: {exc}')
        finally:con.close()
    try:
        if storage_context.current_project_root() is not None:
            store=storage_history.local_revision_store()
            physical=list(store.revisions_dir.glob('*.revision.json'))
            for path in physical:
                try:
                    manifest=store._load_json(path,kind='REVISION')
                    store.verify_revision(str(manifest['revision_id']))
                except Exception as exc:
                    errors.append(f'History v1 corrupt: {path.name}: {exc}');history_v1['ok']=False
            history_v1['revisions']=len(physical)
    except Exception as exc:
        errors.append(f'History v1 verification failed: {exc}');history_v1={'ok':False,'revisions':0}
    return {'ok':not errors,'errors':errors,'history_v1':history_v1,'checkpoints':checkpoints,'disk':disk_health_report()}
