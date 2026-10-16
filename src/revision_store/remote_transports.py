from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

from backups.providers.webdav import WebDAVClient, WebDAVError
from backups.providers.google_drive import GoogleDriveClient
from revision_store.remote_history import RemoteHistoryConflict, RemoteHistoryError, _sha_file

class WebDAVHistoryTransport:
    def __init__(self,client:WebDAVClient):self.client=client
    def _ensure_parent(self,path:str):
        parent=str(Path(path).parent).replace('\\','/')
        if parent not in {'','.','/'}:self.client.ensure_collection(parent)
    def put_immutable(self,path:str,data:bytes)->None:
        self._ensure_parent(path)
        try:existing=self.client.get(path)
        except WebDAVError as exc:
            if exc.code!='WEBDAV_NOT_FOUND':raise
            existing=None
        if existing is not None:
            if existing!=data:raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
            return
        tmp=path+'.'+uuid.uuid4().hex+'.publishing';self.client.put(tmp,data)
        try:self.client.move(tmp,path,overwrite=False)
        except WebDAVError as exc:
            try:self.client.delete(tmp,missing_ok=True)
            except Exception:pass
            try:current=self.client.get(path)
            except Exception:raise
            if current!=data:raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT') from exc
    def read(self,path:str)->bytes|None:
        try:return self.client.get(path)
        except WebDAVError as exc:
            if exc.code=='WEBDAV_NOT_FOUND':return None
            raise
    def put_file_immutable(self,path:str,source:Path)->None:
        self._ensure_parent(path);source=Path(source)
        with tempfile.TemporaryDirectory(prefix='bm-history-webdav-existing-') as td:
            existing=Path(td)/'existing'
            try:self.client.get_file(path,existing);exists=True
            except WebDAVError as exc:
                if exc.code!='WEBDAV_NOT_FOUND':raise
                exists=False
            if exists:
                if _sha_file(existing)!=_sha_file(source):raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
                return
        tmp=path+'.'+uuid.uuid4().hex+'.publishing';self.client.put_file(tmp,source)
        try:self.client.move(tmp,path,overwrite=False)
        except WebDAVError as exc:
            try:self.client.delete(tmp,missing_ok=True)
            except Exception:pass
            with tempfile.TemporaryDirectory(prefix='bm-history-webdav-race-') as td:
                current=Path(td)/'current';self.client.get_file(path,current)
                if _sha_file(current)!=_sha_file(source):raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT') from exc
    def get_file(self,path:str,target:Path)->bool:
        try:self.client.get_file(path,target);return True
        except WebDAVError as exc:
            if exc.code=='WEBDAV_NOT_FOUND':return False
            raise
    def list_paths(self,prefix:str)->list[str]:
        prefix=str(prefix).rstrip('/')+'/'
        try:names=self.client.list_children(prefix.rstrip('/'))
        except WebDAVError as exc:
            if exc.code=='WEBDAV_NOT_FOUND':return []
            raise
        return sorted(set(prefix+name for name in names))

class GoogleDriveHistoryTransport:
    """Drive immutable namespace using appProperties, not filename uniqueness."""
    def __init__(self,client:GoogleDriveClient,root_folder_id:str,workspace_id:str):self.client=client;self.root_folder_id=str(root_folder_id);self.workspace_id=str(workspace_id)
    def _identity(self,path:str)->tuple[str,str]:
        parts=[p for p in str(path).strip('/').split('/') if p]
        if len(parts)<3 or parts[0]!='history' or parts[1]!=self.workspace_id:raise RemoteHistoryError('REMOTE_HISTORY_PATH_INVALID')
        tail=parts[2:]
        if tail==['repository.json']:return 'repository','repo'
        if tail==['writer-authority.json']:return 'writer_authority','authority'
        if len(tail)!=2:raise RemoteHistoryError('REMOTE_HISTORY_PATH_INVALID')
        kind,name=tail
        if kind=='objects' and name.endswith('.snapshot.zip'):return 'object',name.removesuffix('.snapshot.zip')
        if kind=='checkpoints' and name.endswith('.checkpoint.json'):return 'checkpoint',name.removesuffix('.checkpoint.json')
        if kind=='revisions' and name.endswith('.remote.json'):return 'revision_publication',name.removesuffix('.remote.json')
        raise RemoteHistoryError('REMOTE_HISTORY_PATH_INVALID')
    def _logical_path(self,kind:str,key:str)->str:
        base=f'history/{self.workspace_id}'
        return {'repository':f'{base}/repository.json','writer_authority':f'{base}/writer-authority.json','object':f'{base}/objects/{key}.snapshot.zip','checkpoint':f'{base}/checkpoints/{key}.checkpoint.json','revision_publication':f'{base}/revisions/{key}.remote.json'}[kind]
    def _rows(self,kind:str,key:str|None=None):return self.client.list_history_files(self.root_folder_id,self.workspace_id,kind=kind,key=key)
    def _download(self,row,target:Path)->None:self.client.download_file(str(row['id']),target)
    def _verify_duplicate_files(self,rows,expected_hash:str|None=None)->str|None:
        first_hash=None
        with tempfile.TemporaryDirectory(prefix='bm-history-gdrive-duplicates-') as td:
            for index,row in enumerate(rows):
                p=Path(td)/str(index);self._download(row,p);digest=_sha_file(p)
                if first_hash is None:first_hash=digest
                elif digest!=first_hash:raise RemoteHistoryConflict('REMOTE_HISTORY_DUPLICATE_CONFLICT')
            if expected_hash is not None and first_hash is not None and first_hash!=expected_hash:raise RemoteHistoryConflict('REMOTE_HISTORY_IMMUTABLE_CONFLICT')
        return first_hash
    def _upload(self,path:str,source:Path,kind:str,key:str,mime_type:str)->None:
        digest=hashlib.sha256(path.encode('utf-8')).hexdigest()[:12];suffix='.zip' if kind=='object' else '.json';name=f'history-{self.workspace_id[:8]}-{kind}-{digest}{suffix}'
        self.client.upload_file(source,self.root_folder_id,name,{'breadmaker_kind':'history_object','breadmaker_history_workspace_id':self.workspace_id,'breadmaker_history_kind':kind,'breadmaker_history_key':key},mime_type=mime_type)
    def put_immutable(self,path:str,data:bytes)->None:
        kind,key=self._identity(path);rows=self._rows(kind,key);expected=hashlib.sha256(data).hexdigest()
        if rows:self._verify_duplicate_files(rows,expected);return
        with tempfile.TemporaryDirectory(prefix='bm-history-gdrive-json-') as td:
            p=Path(td)/'value';p.write_bytes(data);self._upload(path,p,kind,key,'application/json')
        rows=self._rows(kind,key)
        if not rows:raise RemoteHistoryError('REMOTE_HISTORY_WRITE_NOT_VISIBLE')
        self._verify_duplicate_files(rows,expected)
    def read(self,path:str)->bytes|None:
        kind,key=self._identity(path);rows=self._rows(kind,key)
        if not rows:return None
        self._verify_duplicate_files(rows)
        with tempfile.TemporaryDirectory(prefix='bm-history-gdrive-read-') as td:
            p=Path(td)/'value';self._download(rows[0],p);return p.read_bytes()
    def put_file_immutable(self,path:str,source:Path)->None:
        kind,key=self._identity(path);source=Path(source);expected=_sha_file(source);rows=self._rows(kind,key)
        if rows:self._verify_duplicate_files(rows,expected);return
        self._upload(path,source,kind,key,'application/zip' if kind=='object' else 'application/octet-stream')
        rows=self._rows(kind,key)
        if not rows:raise RemoteHistoryError('REMOTE_HISTORY_WRITE_NOT_VISIBLE')
        self._verify_duplicate_files(rows,expected)
    def get_file(self,path:str,target:Path)->bool:
        kind,key=self._identity(path);rows=self._rows(kind,key)
        if not rows:return False
        expected=self._verify_duplicate_files(rows);self._download(rows[0],target)
        if expected and _sha_file(target)!=expected:raise RemoteHistoryConflict('REMOTE_HISTORY_DUPLICATE_CONFLICT')
        return True
    def list_paths(self,prefix:str)->list[str]:
        raw=str(prefix).rstrip('/')+'/';parts=[p for p in raw.strip('/').split('/') if p]
        if len(parts)<3 or parts[0]!='history' or parts[1]!=self.workspace_id:return []
        kind={'objects':'object','checkpoints':'checkpoint','revisions':'revision_publication'}.get(parts[2])
        if not kind:return []
        rows=self._rows(kind);keys=sorted(set(str((row.get('appProperties') or {}).get('breadmaker_history_key') or '') for row in rows))
        return [self._logical_path(kind,key) for key in keys if key]
