from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

REMOTE_FORMAT='breadmaker-remote-revision-publication'
REMOTE_VERSION=1
REPOSITORY_FORMAT='breadmaker-remote-history-repository'
REPOSITORY_VERSION=1
AUTHORITY_FORMAT='breadmaker-remote-history-writer-authority'
AUTHORITY_VERSION=1
HASH_CHUNK_BYTES=1024*1024
MAX_REMOTE_HISTORY_FILES=50_000
MAX_REMOTE_HISTORY_EXPANDED_BYTES=4*1024*1024*1024

class RemoteHistoryError(RuntimeError): pass
class RemoteHistoryConflict(RemoteHistoryError): pass
class RemoteHistoryCorruption(RemoteHistoryError): pass

class RemoteHistoryTransport(Protocol):
    # Intentionally no delete API. 7.7.6 replication cannot delete History or Recovery.
    def put_immutable(self,path:str,data:bytes)->None: ...
    def read(self,path:str)->bytes|None: ...
    def put_file_immutable(self,path:str,source:Path)->None: ...
    def get_file(self,path:str,target:Path)->bool: ...
    def list_paths(self,prefix:str)->list[str]: ...

@dataclass(frozen=True)
class SyncResult:
    revision_id:str
    generation:int
    idempotent:bool=False
    repaired:bool=False
    def as_dict(self):return {'revision_id':self.revision_id,'generation':self.generation,'idempotent':self.idempotent,'repaired':self.repaired}

def _canon(v:Any)->bytes:return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
def _sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def _sha_file(path:Path)->str:
    digest=hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda:fh.read(HASH_CHUNK_BYTES),b''):digest.update(chunk)
    return digest.hexdigest()
def _fsync_parent(path:Path)->None:
    try:
        fd=os.open(str(Path(path).parent),os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    except OSError:pass

def _fsync_tree(root:Path)->None:
    root=Path(root)
    for path in sorted((p for p in root.rglob('*') if p.is_file()),key=lambda p:len(p.parts),reverse=True):
        try:
            with path.open('rb') as fh:os.fsync(fh.fileno())
        except OSError:pass
    for directory in sorted((p for p in root.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        try:
            fd=os.open(str(directory),os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        except OSError:pass
    _fsync_parent(root)

def _durable_replace_bytes(target:Path,data:bytes)->None:
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name(f'.{target.name}.{uuid.uuid4().hex}.repairing')
    try:
        with tmp.open('xb') as fh:
            fh.write(data);fh.flush();os.fsync(fh.fileno())
        os.replace(tmp,target);_fsync_parent(target)
    finally:
        try:tmp.unlink()
        except FileNotFoundError:pass
def _safe_id(value:str,label:str)->str:
    value=str(value or '').strip().lower()
    if len(value)!=32 or any(ch not in '0123456789abcdef' for ch in value):raise RemoteHistoryCorruption(f'{label}_INVALID')
    return value
def _load_json(data:bytes|None,code:str)->dict[str,Any]:
    if data is None:raise RemoteHistoryCorruption(code)
    try:value=json.loads(data.decode('utf-8'))
    except Exception as exc:raise RemoteHistoryCorruption(code) from exc
    if not isinstance(value,dict):raise RemoteHistoryCorruption(code)
    return value

def _bundle_snapshot(root:Path,target:Path)->Path:
    root=Path(root).resolve();target=Path(target)
    files=sorted((p for p in root.rglob('*') if p.is_file()),key=lambda p:p.relative_to(root).as_posix())
    if len(files)>MAX_REMOTE_HISTORY_FILES:raise RemoteHistoryError('REMOTE_HISTORY_SNAPSHOT_TOO_MANY_FILES')
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as zf:
        for path in files:
            rel=path.relative_to(root).as_posix();info=zipfile.ZipInfo(rel,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o600<<16
            with path.open('rb') as src,zf.open(info,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,HASH_CHUNK_BYTES)
    return target

def _extract_bundle(bundle:Path,target:Path)->None:
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(bundle) as zf:
            infos=zf.infolist()
            if len(infos)>MAX_REMOTE_HISTORY_FILES or sum(int(i.file_size) for i in infos)>MAX_REMOTE_HISTORY_EXPANDED_BYTES:raise RemoteHistoryCorruption('REMOTE_HISTORY_BUNDLE_LIMIT_EXCEEDED')
            for info in infos:
                rel=Path(info.filename)
                if rel.is_absolute() or '..' in rel.parts:raise RemoteHistoryCorruption('REMOTE_HISTORY_BUNDLE_PATH_ESCAPE')
                dest=(target/rel).resolve()
                try:dest.relative_to(target)
                except ValueError as exc:raise RemoteHistoryCorruption('REMOTE_HISTORY_BUNDLE_PATH_ESCAPE') from exc
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                with zf.open(info) as src,dest.open('wb') as dst:shutil.copyfileobj(src,dst,HASH_CHUNK_BYTES)
    except zipfile.BadZipFile as exc:raise RemoteHistoryCorruption('REMOTE_HISTORY_BUNDLE_INVALID') from exc

def _verify_bundle_file(bundle:Path,*,workspace_id:str,generation:int,expected_state_hash:str,canonical_tables:Iterable[str])->dict[str,Any]:
    from revision_store.local_store import canonical_state_hash
    with tempfile.TemporaryDirectory(prefix='bm-remote-history-verify-') as td:
        root=Path(td)/'snapshot';_extract_bundle(bundle,root)
        manifest_path=root/'snapshot_manifest.json'
        manifest=_load_json(manifest_path.read_bytes() if manifest_path.is_file() else None,'REMOTE_HISTORY_SNAPSHOT_MANIFEST_MISSING')
        manifest_workspace=str(manifest.get('workspace_project_id') or '')
        if manifest_workspace and manifest_workspace!=workspace_id:raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_WORKSPACE_MISMATCH')
        if int(manifest.get('project_generation') or 0)!=int(generation):raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_GENERATION_MISMATCH')
        if str(manifest.get('canonical_state_hash') or '')!=str(expected_state_hash):raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_STATE_HASH_MISMATCH')
        hashes=manifest.get('file_hashes')
        if not isinstance(hashes,dict) or 'project.sqlite3' not in hashes:raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_HASHES_INVALID')
        actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='snapshot_manifest.json'}
        if actual!=set(hashes):raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_FILE_SET_MISMATCH')
        for rel,expected in hashes.items():
            path=(root/rel).resolve()
            try:path.relative_to(root.resolve())
            except ValueError as exc:raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_PATH_ESCAPE') from exc
            if not path.is_file() or _sha_file(path)!=str(expected):raise RemoteHistoryCorruption(f'REMOTE_HISTORY_SNAPSHOT_FILE_HASH_MISMATCH:{rel}')
        db=root/'project.sqlite3';con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro&immutable=1',uri=True)
        try:
            if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_DATABASE_CORRUPT')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_FOREIGN_KEYS_INVALID')
        finally:con.close()
        logical=canonical_state_hash(db,canonical_tables,hashes)
        if logical!=str(expected_state_hash):raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_CANONICAL_HASH_MISMATCH')
        return manifest

class RemoteHistoryRepository:
    def __init__(self,transport:RemoteHistoryTransport,*,workspace_project_id:str,writer_id:str|None=None,canonical_tables:Iterable[str]=(),allow_initialize:bool=True):
        self.transport=transport
        self.canonical_tables=tuple(sorted(set(str(x) for x in canonical_tables)))
        self.workspace_project_id=_safe_id(workspace_project_id,'REMOTE_HISTORY_WORKSPACE_ID')
        self.writer_id=_safe_id(writer_id,'REMOTE_HISTORY_WRITER_ID') if writer_id is not None else None
        self.allow_initialize=bool(allow_initialize)
        self.base=f'history/{self.workspace_project_id}'

    def _require_writer_id(self)->str:
        if not self.writer_id:
            raise RemoteHistoryConflict('REMOTE_HISTORY_WRITER_REQUIRED')
        return self.writer_id

    def authority_present(self)->bool:
        """Return true when immutable repository authority exists.

        This is a direct-read check, never a provider-listing inference. It is safe for
        diagnostics/read-only discovery and deliberately does not establish or mutate authority.
        """
        return self._read_claim() is not None or self._read_authority() is not None

    def _path(self,kind:str,name:str)->str:
        return f'{self.base}/{kind}/{name}'

    def _repo_path(self)->str:
        return f'{self.base}/repository.json'

    def _authority_path(self)->str:
        return f'{self.base}/writer-authority.json'

    def _read_claim(self)->dict[str,Any]|None:
        raw=self.transport.read(self._repo_path())
        if raw is None:return None
        row=_load_json(raw,'REMOTE_HISTORY_REPOSITORY_CORRUPT')
        if row.get('repository_format')!=REPOSITORY_FORMAT or int(row.get('repository_version') or 0)!=REPOSITORY_VERSION or str(row.get('workspace_project_id') or '')!=self.workspace_project_id:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REPOSITORY_IDENTITY_INVALID')
        _safe_id(str(row.get('writer_id') or ''),'REMOTE_HISTORY_REPOSITORY_WRITER_ID')
        return row

    def _read_authority(self)->dict[str,Any]|None:
        raw=self.transport.read(self._authority_path())
        if raw is None:return None
        row=_load_json(raw,'REMOTE_HISTORY_WRITER_AUTHORITY_CORRUPT')
        if row.get('authority_format')!=AUTHORITY_FORMAT or int(row.get('authority_version') or 0)!=AUTHORITY_VERSION or str(row.get('workspace_project_id') or '')!=self.workspace_project_id:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_WRITER_AUTHORITY_IDENTITY_INVALID')
        _safe_id(str(row.get('writer_id') or ''),'REMOTE_HISTORY_WRITER_AUTHORITY_WRITER_ID')
        return row

    def claim_writer(self)->None:
        writer_id=self._require_writer_id()
        repo_claim={'repository_format':REPOSITORY_FORMAT,'repository_version':REPOSITORY_VERSION,'workspace_project_id':self.workspace_project_id,'writer_id':writer_id}
        authority_claim={'authority_format':AUTHORITY_FORMAT,'authority_version':AUTHORITY_VERSION,'workspace_project_id':self.workspace_project_id,'writer_id':writer_id}
        repo=self._read_claim();authority=self._read_authority()
        if (repo is None)!=(authority is None):
            # Current-format repositories require both independent authority records.
            # A partial pair is corruption regardless of which writer is asking: the
            # repository is no longer authoritative enough to classify ownership.
            # Never reconstruct the missing mirror and never use provider listing as
            # evidence that the surviving record is sufficient.
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REPOSITORY_AUTHORITY_INCOMPLETE')
        if repo is not None and authority is not None:
            repo_writer=str(repo.get('writer_id') or '')
            authority_writer=str(authority.get('writer_id') or '')
            if repo_writer!=authority_writer:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_WRITER_AUTHORITY_DIVERGED')
            if repo_writer!=writer_id:
                raise RemoteHistoryConflict('REMOTE_HISTORY_WRITER_CONFLICT')
            return
        # Absence of both authority records is only valid at an explicit destination
        # initialization boundary. Provider listings are never evidence of emptiness.
        if not self.allow_initialize:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REPOSITORY_AUTHORITY_MISSING')
        self.transport.put_immutable(self._authority_path(),_canon(authority_claim))
        self.transport.put_immutable(self._repo_path(),_canon(repo_claim))
        self.assert_writer_authority()

    def assert_writer_authority(self)->None:
        writer_id=self._require_writer_id()
        repo=self._read_claim();authority=self._read_authority()
        if repo is None or authority is None:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_WRITER_AUTHORITY_INCOMPLETE')
        repo_writer=str(repo.get('writer_id') or '');authority_writer=str(authority.get('writer_id') or '')
        if repo_writer!=authority_writer:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_WRITER_AUTHORITY_DIVERGED')
        if repo_writer!=writer_id:
            raise RemoteHistoryConflict('REMOTE_HISTORY_WRITER_CONFLICT')

    def _validate_publication_structure(self,path:str,row:dict[str,Any])->dict[str,Any]:
        if row.get('remote_publication_format')!=REMOTE_FORMAT or int(row.get('remote_publication_version') or 0)!=REMOTE_VERSION:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_PUBLICATION_FORMAT_INVALID')
        if str(row.get('workspace_project_id') or '')!=self.workspace_project_id:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_PUBLICATION_WORKSPACE_MISMATCH')
        rid=_safe_id(str(row.get('revision_id') or ''),'REMOTE_HISTORY_REVISION_ID')
        generation=int(row.get('generation') or 0)
        if generation<=0:raise RemoteHistoryCorruption('REMOTE_HISTORY_GENERATION_INVALID')
        writer=_safe_id(str(row.get('writer_id') or ''),'REMOTE_HISTORY_PUBLICATION_WRITER_ID')
        expected_pub=self._path('revisions',f'g{generation:020d}.remote.json')
        if path!=expected_pub:raise RemoteHistoryCorruption('REMOTE_HISTORY_PUBLICATION_PATH_INVALID')
        revision=row.get('local_revision_manifest')
        if not isinstance(revision,dict):raise RemoteHistoryCorruption('REMOTE_HISTORY_REVISION_MANIFEST_MISSING')
        for key in ('snapshot_bundle_sha256','checkpoint_sha256','revision_manifest_sha256'):
            value=str(row.get(key) or '').lower()
            if len(value)!=64 or any(ch not in '0123456789abcdef' for ch in value):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_HASH_INVALID')
        if _sha(_canon(revision))!=str(row['revision_manifest_sha256']):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REVISION_MANIFEST_HASH_MISMATCH')
        if str(revision.get('revision_id') or '')!=rid or int(revision.get('generation_end') or 0)!=generation:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REVISION_IDENTITY_MISMATCH')
        if str(revision.get('workspace_project_id') or '') and str(revision.get('workspace_project_id'))!=self.workspace_project_id:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_REVISION_WORKSPACE_MISMATCH')
        snapshot_id=str(row.get('snapshot_object_id') or '')
        checkpoint_id=str(row.get('checkpoint_id') or '')
        if str(row.get('snapshot_bundle_path') or '')!=self._path('objects',f'{snapshot_id}.snapshot.zip'):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_PATH_INVALID')
        if str(row.get('checkpoint_path') or '')!=self._path('checkpoints',f'{checkpoint_id}.checkpoint.json'):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_PATH_INVALID')
        out=dict(row);out['revision_id']=rid;out['generation']=generation;out['writer_id']=writer
        return out

    def _publication_candidates(self)->list[tuple[str,dict[str,Any]]]:
        out=[]
        for path in self.transport.list_paths(f'{self.base}/revisions/'):
            if not path.endswith('.remote.json'):continue
            try:
                raw=self.transport.read(path)
                out.append((path,self._validate_publication_structure(path,_load_json(raw,'REMOTE_HISTORY_PUBLICATION_UNREADABLE'))))
            except RemoteHistoryCorruption:
                continue
        out.sort(key=lambda item:(int(item[1]['generation']),str(item[1]['revision_id'])),reverse=True)
        return out

    def publication_exists(self,generation:int)->bool:
        return self.transport.read(self._path('revisions',f'g{int(generation):020d}.remote.json')) is not None

    def _verify_publication_single(self,path:str)->tuple[dict[str,Any],dict[str,Any]]:
        row=self._validate_publication_structure(path,_load_json(self.transport.read(path),'REMOTE_HISTORY_PUBLICATION_UNREADABLE'))
        repo=self._read_claim();authority=self._read_authority()
        if repo is None or authority is None or str(repo.get('writer_id') or '')!=str(row['writer_id']) or str(authority.get('writer_id') or '')!=str(row['writer_id']):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_PUBLICATION_WRITER_NOT_AUTHORITATIVE')
        checkpoint_bytes=self.transport.read(str(row['checkpoint_path']))
        if checkpoint_bytes is None or _sha(checkpoint_bytes)!=str(row['checkpoint_sha256']):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_CORRUPT')
        checkpoint=_load_json(checkpoint_bytes,'REMOTE_HISTORY_CHECKPOINT_CORRUPT')
        if str(checkpoint.get('checkpoint_id') or '')!=str(row.get('checkpoint_id') or ''):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_IDENTITY_MISMATCH')
        with tempfile.TemporaryDirectory(prefix='bm-remote-history-download-') as td:
            bundle=Path(td)/'snapshot.zip'
            if not self.transport.get_file(str(row['snapshot_bundle_path']),bundle):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_MISSING')
            if _sha_file(bundle)!=str(row['snapshot_bundle_sha256']):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_CORRUPT')
            _verify_bundle_file(bundle,workspace_id=self.workspace_project_id,generation=int(row['generation']),expected_state_hash=str(row['local_revision_manifest'].get('expected_state_hash') or ''),canonical_tables=self.canonical_tables)
        return row,checkpoint

    def _verified_lineage_rows(self,row:dict[str,Any],checkpoint:dict[str,Any]|None=None)->list[dict[str,Any]]:
        """Return checkpoint-anchor→target remote publications using direct reads.

        A remote revision is valid only when every immutable publication needed by the
        local v1 lineage verifier is present and independently verified. Provider
        listing order/visibility is irrelevant to this closure.
        """
        target=dict(row)
        current=target
        cp=checkpoint
        chain=[target]
        seen={str(target['revision_id'])}
        while int(current['local_revision_manifest'].get('distance_from_checkpoint') or 0)>0:
            rev=current['local_revision_manifest']
            parent_id=str(rev.get('parent_revision_id') or '')
            parent_generation=int(rev.get('generation_start') or 0)-1
            if not parent_id or parent_generation<=0:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_PARENT_INVALID')
            parent_path=self._path('revisions',f'g{parent_generation:020d}.remote.json')
            parent,parent_cp=self._verify_publication_single(parent_path)
            prev=parent['local_revision_manifest']
            if str(parent['revision_id'])!=parent_id:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_PARENT_ID_MISMATCH')
            if str(prev.get('checkpoint_id') or '')!=str(rev.get('checkpoint_id') or ''):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_CHECKPOINT_MISMATCH')
            if int(prev.get('distance_from_checkpoint',-1))+1!=int(rev.get('distance_from_checkpoint',-1)):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_DISTANCE_MISMATCH')
            if int(prev.get('generation_end') or 0)+1!=int(rev.get('generation_start') or 0):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_GENERATION_DISCONTINUITY')
            if str(parent['revision_id']) in seen:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_CYCLE')
            seen.add(str(parent['revision_id']))
            chain.append(parent)
            current=parent
            cp=cp or parent_cp
            if len(chain)>32:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LINEAGE_TOO_LONG')
        cp=cp or self._verify_publication_single(self._path('revisions',f'g{int(target["generation"]):020d}.remote.json'))[1]
        anchor=current['local_revision_manifest']
        if str(cp.get('anchor_revision_id') or '')!=str(anchor.get('revision_id') or ''):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_ANCHOR_ID_MISMATCH')
        if int(cp.get('project_generation') or 0)!=int(anchor.get('generation_end') or 0):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_ANCHOR_GENERATION_MISMATCH')
        if str(cp.get('snapshot_object_id') or '')!=str(anchor.get('snapshot_object_id') or ''):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_ANCHOR_SNAPSHOT_MISMATCH')
        if str(cp.get('snapshot_manifest_sha256') or '')!=str(anchor.get('snapshot_manifest_sha256') or '') or str(cp.get('expected_state_hash') or '')!=str(anchor.get('expected_state_hash') or ''):
            raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_ANCHOR_HASH_MISMATCH')
        chain.reverse()
        return chain

    def verify_publication(self,path:str)->dict[str,Any]:
        row,checkpoint=self._verify_publication_single(path)
        self._verified_lineage_rows(row,checkpoint)
        return row

    def _publication_rows(self)->list[dict[str,Any]]:
        rows=[]
        for path,_ in self._publication_candidates():
            try:rows.append(self.verify_publication(path))
            except (RemoteHistoryCorruption,RemoteHistoryConflict):continue
        rows.sort(key=lambda r:(int(r['generation']),str(r['revision_id'])),reverse=True)
        return rows

    def gc_verified_chain(self)->list[dict[str,Any]]:
        """Return a GC-safe oldest→newest publication chain.

        Provider listing is used only to discover one visible valid head. From that
        head, every revision needed for GC membership is recovered by immutable direct
        reads following parent generation/id links, including across checkpoint
        boundaries. A stale/incomplete listing can therefore truncate the discovered
        head (causing conservative retention), but it cannot hide a member of a
        checkpoint segment that GC is about to delete.
        """
        head=None
        for path,_ in self._publication_candidates():
            try:
                head=self.verify_publication(path);break
            except (RemoteHistoryCorruption,RemoteHistoryConflict):
                continue
        if head is None:
            return []
        by_generation:dict[int,dict[str,Any]]={}
        seen:set[str]=set()
        current=head
        while True:
            lineage=self._verified_lineage_rows(current)
            for row in lineage:
                rid=str(row['revision_id']);generation=int(row['generation'])
                existing=by_generation.get(generation)
                if existing is not None and str(existing['revision_id'])!=rid:
                    raise RemoteHistoryConflict('REMOTE_HISTORY_GENERATION_CONFLICT')
                by_generation[generation]=row
            anchor=lineage[0]
            anchor_manifest=anchor['local_revision_manifest']
            anchor_id=str(anchor['revision_id'])
            if anchor_id in seen:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_GC_CHAIN_CYCLE')
            seen.add(anchor_id)
            parent_id=str(anchor_manifest.get('parent_revision_id') or '')
            if not parent_id:
                break
            parent_generation=int(anchor_manifest.get('generation_start') or 0)-1
            if parent_generation<=0 or parent_generation>=int(anchor['generation']):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_GC_PARENT_GENERATION_INVALID')
            parent_path=self._path('revisions',f'g{parent_generation:020d}.remote.json')
            # Crossing a checkpoint boundary is not part of that checkpoint's
            # reconstructability contract. If the previous segment is absent (for
            # example because an earlier GC removed it, or the provider temporarily
            # cannot expose it), truncate the GC graph here. Missing data can only
            # leak older storage; it must never authorize deletion by inference.
            if self.transport.read(parent_path) is None:
                break
            parent,_checkpoint=self._verify_publication_single(parent_path)
            if str(parent['revision_id'])!=parent_id:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_GC_PARENT_ID_MISMATCH')
            if str(parent['revision_id']) in seen:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_GC_CHAIN_CYCLE')
            current=parent
        return [by_generation[g] for g in sorted(by_generation)]

    def _local_lineage(self,local_store,revision:dict[str,Any])->list[dict[str,Any]]:
        chain=[dict(revision)]
        current=dict(revision)
        seen={str(current['revision_id'])}
        while int(current.get('distance_from_checkpoint') or 0)>0:
            parent_id=str(current.get('parent_revision_id') or '')
            if not parent_id:raise RemoteHistoryCorruption('REMOTE_HISTORY_LOCAL_LINEAGE_PARENT_MISSING')
            parent=dict(local_store._find_revision(parent_id).manifest)
            if str(parent.get('checkpoint_id') or '')!=str(current.get('checkpoint_id') or ''):
                raise RemoteHistoryCorruption('REMOTE_HISTORY_LOCAL_LINEAGE_CHECKPOINT_MISMATCH')
            if str(parent['revision_id']) in seen:raise RemoteHistoryCorruption('REMOTE_HISTORY_LOCAL_LINEAGE_CYCLE')
            seen.add(str(parent['revision_id']));chain.append(parent);current=parent
        chain.reverse()
        return chain

    def _publish_one(self,local_store,rev:dict[str,Any],stage_hook=None)->SyncResult:
        verified=local_store.verify_revision(str(rev['revision_id']))
        rev=dict(verified['revision'])
        generation=int(rev['generation_end']);rid=_safe_id(str(rev['revision_id']),'REMOTE_HISTORY_REVISION_ID')
        pub_path=self._path('revisions',f'g{generation:020d}.remote.json')
        direct_raw=self.transport.read(pub_path);direct_row=None
        if direct_raw is not None:
            direct_row=self._validate_publication_structure(pub_path,_load_json(direct_raw,'REMOTE_HISTORY_PUBLICATION_UNREADABLE'))
            if str(direct_row['revision_id'])!=rid:raise RemoteHistoryConflict('REMOTE_HISTORY_GENERATION_CONFLICT')
        repaired=False
        checkpoint,_=local_store._find_checkpoint(str(rev['checkpoint_id']))
        checkpoint_bytes=_canon(checkpoint);checkpoint_hash=_sha(checkpoint_bytes);revision_hash=_sha(_canon(rev))
        obj_path=self._path('objects',f'{rev["snapshot_object_id"]}.snapshot.zip')
        chk_path=self._path('checkpoints',f'{rev["checkpoint_id"]}.checkpoint.json')
        with tempfile.TemporaryDirectory(prefix='bm-remote-history-publish-') as td:
            bundle=_bundle_snapshot(Path(verified['root']),Path(td)/'snapshot.zip');bundle_hash=_sha_file(bundle)
            _verify_bundle_file(bundle,workspace_id=self.workspace_project_id,generation=generation,expected_state_hash=str(rev.get('expected_state_hash') or ''),canonical_tables=self.canonical_tables)
            if direct_row is not None:
                probe=Path(td)/'preexisting.zip'
                had_object=self.transport.get_file(obj_path,probe)
                had_checkpoint=self.transport.read(chk_path) is not None
                repaired=not (had_object and had_checkpoint)
            self.transport.put_file_immutable(obj_path,bundle)
            if stage_hook:stage_hook('OBJECT_UPLOADED',{'revision_id':rid,'generation':generation,'path':obj_path})
            self.transport.put_immutable(chk_path,checkpoint_bytes)
            if stage_hook:stage_hook('CHECKPOINT_UPLOADED',{'revision_id':rid,'generation':generation,'checkpoint_id':str(rev['checkpoint_id'])})
            downloaded=Path(td)/'downloaded.zip'
            if not self.transport.get_file(obj_path,downloaded) or _sha_file(downloaded)!=bundle_hash:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_SNAPSHOT_VERIFY_FAILED')
            _verify_bundle_file(downloaded,workspace_id=self.workspace_project_id,generation=generation,expected_state_hash=str(rev.get('expected_state_hash') or ''),canonical_tables=self.canonical_tables)
            if _sha(self.transport.read(chk_path) or b'')!=checkpoint_hash:
                raise RemoteHistoryCorruption('REMOTE_HISTORY_CHECKPOINT_VERIFY_FAILED')
        publication={'remote_publication_format':REMOTE_FORMAT,'remote_publication_version':REMOTE_VERSION,'workspace_project_id':self.workspace_project_id,'writer_id':self._require_writer_id(),'revision_id':rid,'generation':generation,'checkpoint_id':str(rev['checkpoint_id']),'snapshot_object_id':str(rev['snapshot_object_id']),'snapshot_bundle_path':obj_path,'checkpoint_path':chk_path,'snapshot_bundle_sha256':bundle_hash,'checkpoint_sha256':checkpoint_hash,'revision_manifest_sha256':revision_hash,'local_revision_manifest':rev}
        if stage_hook:stage_hook('BEFORE_PUBLICATION',{'revision_id':rid,'generation':generation,'path':pub_path})
        self.transport.put_immutable(pub_path,_canon(publication))
        if stage_hook:stage_hook('PUBLICATION_WRITTEN',{'revision_id':rid,'generation':generation,'path':pub_path})
        self.verify_publication(pub_path)
        if stage_hook:stage_hook('PUBLICATION_VERIFIED',{'revision_id':rid,'generation':generation,'path':pub_path})
        return SyncResult(rid,generation,idempotent=direct_row is not None,repaired=repaired)

    def publish(self,local_store,revision_id:str,stage_hook=None)->SyncResult:
        verified=local_store.verify_revision(revision_id);target=dict(verified['revision'])
        if str(target.get('workspace_project_id') or '') and str(target.get('workspace_project_id'))!=self.workspace_project_id:
            raise RemoteHistoryCorruption('REMOTE_HISTORY_LOCAL_WORKSPACE_MISMATCH')
        self.claim_writer()
        lineage=self._local_lineage(local_store,target)
        result=None
        for rev in lineage:
            result=self._publish_one(local_store,rev,stage_hook=stage_hook)
        assert result is not None
        return result

    def publish_all(self,local_store,stage_hook=None)->list[dict[str,Any]]:
        return [self.publish(local_store,str(rev['revision_id']),stage_hook=stage_hook).as_dict() for rev in reversed(local_store.list_revisions())]

    def newest_valid(self)->dict[str,Any]|None:
        for path,_ in self._publication_candidates():
            try:return self.verify_publication(path)
            except (RemoteHistoryCorruption,RemoteHistoryConflict):continue
        return None

    def _quarantine_file(self,local_store,target:Path)->None:
        if not target.exists():return
        q=local_store.root/'quarantine';q.mkdir(parents=True,exist_ok=True);dest=q/f'{target.name}.{uuid.uuid4().hex}.corrupt'
        if target.is_dir():shutil.copytree(target,dest)
        else:shutil.copy2(target,dest)

    def _install_remote_row(self,local_store,row:dict[str,Any])->bool:
        rid=str(row['revision_id']);gen=int(row['generation'])
        try:local_store.verify_revision(rid);return False
        except Exception:pass
        verified=self.verify_publication(self._path('revisions',f'g{gen:020d}.remote.json'))
        rev=dict(verified['local_revision_manifest']);checkpoint_bytes=self.transport.read(str(verified['checkpoint_path']))
        checkpoint=_load_json(checkpoint_bytes,'REMOTE_HISTORY_REPAIR_CHECKPOINT_INVALID')
        object_id=str(rev['snapshot_object_id']);obj_target=local_store.objects_dir/object_id;rev_target=local_store._revision_path(gen,rid);chk_target=local_store._checkpoint_path(int(checkpoint['project_generation']),str(checkpoint['checkpoint_id']))
        with tempfile.TemporaryDirectory(prefix='bm-remote-history-repair-') as td:
            bundle=Path(td)/'snapshot.zip'
            if not self.transport.get_file(str(verified['snapshot_bundle_path']),bundle):raise RemoteHistoryCorruption('REMOTE_HISTORY_REPAIR_OBJECT_MISSING')
            staged_obj=local_store.staging_dir/f'{uuid.uuid4().hex}.remote-repair';shutil.rmtree(staged_obj,ignore_errors=True);_extract_bundle(bundle,staged_obj)
            object_valid=False
            if obj_target.exists():
                try:
                    local_store._verify_snapshot(object_id,expected_project_id=int(rev['project_id']),expected_generation=gen,expected_state_hash=str(rev['expected_state_hash']),expected_manifest_hash=str(rev['snapshot_manifest_sha256']),expected_workspace_project_id=str(rev.get('workspace_project_id') or ''));object_valid=True
                except Exception:object_valid=False
            if not object_valid:
                if obj_target.exists():self._quarantine_file(local_store,obj_target);shutil.rmtree(obj_target,ignore_errors=True)
                _fsync_tree(staged_obj);os.replace(staged_obj,obj_target);_fsync_parent(obj_target)
            else:shutil.rmtree(staged_obj,ignore_errors=True)
        for target,data in ((chk_target,checkpoint_bytes),(rev_target,_canon(rev))):
            if target.exists() and target.read_bytes()==data:continue
            if target.exists():self._quarantine_file(local_store,target)
            _durable_replace_bytes(target,data)
        return True

    def repair_local(self,local_store,revision_id:str|None=None)->SyncResult:
        with local_store._lock:
            if revision_id is None:
                target=self.newest_valid()
            else:
                target=None
                for path,row in self._publication_candidates():
                    if str(row['revision_id'])==str(revision_id):
                        target=self.verify_publication(path);break
            if target is None:raise RemoteHistoryError('REMOTE_HISTORY_REVISION_NOT_FOUND')
            lineage=self._verified_lineage_rows(target)
            repaired=False
            for row in lineage:
                repaired=self._install_remote_row(local_store,row) or repaired
            local_store.verify_revision(str(target['revision_id']))
            return SyncResult(str(target['revision_id']),int(target['generation']),idempotent=not repaired,repaired=repaired)
