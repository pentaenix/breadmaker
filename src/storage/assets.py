from __future__ import annotations

"""Checkpoint/project asset capture, cleanup, and durable publication."""

import os
import re
import sqlite3
import uuid
from pathlib import Path

from storage import context as storage_context
from storage import database as storage_database
from storage import undo as storage_undo
from storage import coordination as storage_coordination
from storage import values as storage_values

def _checkpoint_asset_paths(snapshot: dict) -> list[str]:
    paths=[]
    for block in snapshot.get('blocks',[]) or []:
        if str(block.get('block_type') or '')!='image': continue
        api_path=str(block.get('image_path') or '').strip()
        if not api_path.startswith('/api/assets/'): continue
        name=api_path.removeprefix('/api/assets/')
        if name and Path(name).name==name:
            paths.append('/api/assets/'+name)
    return sorted(set(paths))


def capture_checkpoint_assets(con: sqlite3.Connection, checkpoint_id: int, snapshot: dict) -> int:
    """Copy every image referenced by a Checkpoint into its dedicated immutable store."""
    captured=0
    for api_path in _checkpoint_asset_paths(snapshot):
        name=api_path.removeprefix('/api/assets/')
        source=storage_context.current_asset_dir()/name
        if not source.is_file():
            raise ValueError(f'CHECKPOINT_ASSET_MISSING:{api_path}')
        payload=source.read_bytes(); digest=storage_values.sha(payload); size=len(payload)
        row=con.execute("SELECT payload,byte_size FROM checkpoint_assets WHERE hash=?",(digest,)).fetchone()
        if row is not None:
            existing=bytes(row['payload'])
            if int(row['byte_size'])!=size or storage_values.sha(existing)!=digest:
                raise ValueError('CHECKPOINT_ASSET_STORE_CORRUPT')
        else:
            con.execute("INSERT INTO checkpoint_assets(hash,payload,byte_size,created_at) VALUES(?,?,?,?)",(digest,payload,size,storage_values.now()))
        con.execute("INSERT INTO checkpoint_asset_refs(checkpoint_id,asset_path,asset_hash) VALUES(?,?,?)",(int(checkpoint_id),api_path,digest))
        captured+=1
    return captured


def ensure_checkpoint_assets(con: sqlite3.Connection, checkpoint_id: int) -> int:
    """Verify and republish missing project image files needed by one Checkpoint."""
    restored=0
    rows=con.execute("""SELECT r.asset_path,r.asset_hash,a.payload,a.byte_size
        FROM checkpoint_asset_refs r JOIN checkpoint_assets a ON a.hash=r.asset_hash
        WHERE r.checkpoint_id=? ORDER BY r.asset_path""",(int(checkpoint_id),)).fetchall()
    for row in rows:
        api_path=str(row['asset_path']); name=api_path.removeprefix('/api/assets/')
        if not name or Path(name).name!=name: raise ValueError('CHECKPOINT_ASSET_PATH_INVALID')
        payload=bytes(row['payload']); digest=str(row['asset_hash'])
        if int(row['byte_size'])!=len(payload) or storage_values.sha(payload)!=digest:
            raise ValueError('CHECKPOINT_ASSET_CORRUPT')
        target=storage_context.current_asset_dir()/name
        if target.exists():
            if not target.is_file() or storage_values.sha_file(target)!=digest:
                raise ValueError(f'CHECKPOINT_ASSET_CONFLICT:{api_path}')
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        tmp=target.with_name(target.name+'.checkpoint-'+uuid.uuid4().hex+'.tmp')
        with tmp.open('wb') as fh:
            fh.write(payload);fh.flush();__import__('os').fsync(fh.fileno())
        tmp.replace(target)
        restored+=1
    return restored


def cleanup_checkpoint_assets(con: sqlite3.Connection) -> int:
    """Delete only Checkpoint blobs no longer referenced by any Checkpoint row."""
    cur=con.execute("DELETE FROM checkpoint_assets WHERE NOT EXISTS(SELECT 1 FROM checkpoint_asset_refs r WHERE r.asset_hash=checkpoint_assets.hash)")
    return int(cur.rowcount or 0)


def project_asset_reference_count(con: sqlite3.Connection, api_path: str) -> int:
    """Derive reachability for one project asset instead of maintaining a fragile counter."""
    api_path=str(api_path or '').strip()
    if not api_path.startswith('/api/assets/'): return 0
    count=0
    count+=int(con.execute("SELECT COUNT(*) FROM manuscript_blocks WHERE image_path=?",(api_path,)).fetchone()[0])
    count+=int(con.execute("SELECT COUNT(*) FROM canvas_nodes WHERE image_path=?",(api_path,)).fetchone()[0])
    count+=int(con.execute("SELECT COUNT(*) FROM review_snapshot_blocks WHERE image_path=?",(api_path,)).fetchone()[0])
    count+=int(con.execute("SELECT COUNT(*) FROM checkpoint_asset_refs WHERE asset_path=?",(api_path,)).fetchone()[0])
    # Cuts intentionally store portable block HTML. Match the exact quoted src forms
    # emitted by Breadmaker rather than interpreting arbitrary HTML here.
    quoted_a='src="'+api_path+'"'; quoted_b="src='"+api_path+"'"
    count+=int(con.execute("SELECT COUNT(*) FROM cuts WHERE instr(content_html,?)>0 OR instr(content_html,?)>0",(quoted_a,quoted_b)).fetchone()[0])
    return count


def delete_project_asset_if_unreferenced(con: sqlite3.Connection, api_path: str) -> bool:
    """Delete one managed asset only after every durable owner releases it."""
    api_path=str(api_path or '').strip()
    if project_asset_reference_count(con,api_path): return False
    prefix='/api/assets/'
    if not api_path.startswith(prefix): return False
    name=api_path.removeprefix(prefix)
    if not name or Path(name).name!=name: return False
    # GC owns only schema-40 content-addressed image files. Never guess that an
    # unrelated/legacy file in assets belongs to this lifecycle.
    if not re.fullmatch(r'img_[0-9a-f]{24}\.(?:png|jpg|webp|gif|avif|bmp)',name,re.I): return False
    target=storage_context.current_asset_dir()/name
    if not target.is_file(): return False
    target.unlink()
    try:
        fd=os.open(str(storage_context.current_asset_dir()),os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except (OSError,AttributeError): pass
    return True


def cleanup_unreferenced_project_images() -> int:
    """Collect orphaned schema-40 images only when Session Undo cannot need them.

    Removal/replacement deliberately does not collect synchronously: Session Undo
    stores logical DB deltas, not duplicate image bytes. Project activation clears
    that journal, making it a safe deterministic GC boundary.
    """
    with storage_coordination._LOCK:
        root=str(storage_context.current_project_root() or '')
        if any(entry.get('project_root')==root for entry in storage_undo._SESSION_UNDO_JOURNAL.values()):
            return 0
    asset_dir=storage_context.current_asset_dir()
    if not asset_dir.is_dir(): return 0
    deleted=0
    con=storage_database.connect()
    try:
        for target in list(asset_dir.iterdir()):
            if not target.is_file(): continue
            api_path='/api/assets/'+target.name
            if delete_project_asset_if_unreferenced(con,api_path): deleted+=1
    finally:
        con.close()
    return deleted


def checkpoint_storage_report(con: sqlite3.Connection) -> dict:
    row=con.execute("SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM checkpoint_assets").fetchone()
    refs=con.execute("SELECT COUNT(*) FROM checkpoint_asset_refs").fetchone()[0]
    return {'assets':int(row[0]),'asset_bytes':int(row[1]),'asset_refs':int(refs)}


def _fsync_parent(path:Path) -> None:
    try:
        import os
        fd=os.open(str(Path(path).parent),os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    except (OSError,AttributeError): pass


def durable_write_project_asset(path:Path, data:bytes, *, allow_existing_same:bool=False) -> Path:
    """Publish project-asset bytes atomically; never expose a partial final file.

    Asset filenames are immutable after publication.  Writers stage into the same
    directory, flush the bytes, then atomically rename.  A differing existing target
    is treated as corruption/name collision rather than overwritten.
    """
    import os
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if allow_existing_same and path.is_file() and storage_values.sha_file(path)==storage_values.sha(data):
            return path
        raise RuntimeError(f'PROJECT_ASSET_ALREADY_EXISTS:{path.name}')
    tmp=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.asset-tmp')
    try:
        with tmp.open('xb') as fh:
            fh.write(data);fh.flush();os.fsync(fh.fileno())
        os.replace(tmp,path);_fsync_parent(path)
        return path
    finally:
        tmp.unlink(missing_ok=True)
