from __future__ import annotations
import base64, json, os, secrets, tempfile, time, uuid
from pathlib import Path
from typing import Any
from backups.providers.google_drive import GoogleDriveClient, GoogleDriveError, authorization_url as google_authorization_url, exchange_authorization_code, new_pkce_pair, GOOGLE_BACKUP_FOLDER_NAME
from .models import WorkspaceError
from .filesystem import _durable_replace, _project_change_token, now

class CredentialWorkspaceMixin:
    def _seal_backup_credential_record(self, credential_id:str, value:dict[str,Any]) -> str:
        """Seal one local backup credential record with authenticated encryption.

        The per-installation key is owner-only and deliberately lives outside project
        packages/backups.  This prevents backup_credentials.json, copied home folders,
        and support bundles from exposing WebDAV passwords or OAuth refresh tokens as
        plaintext.  It is an at-rest boundary, not a claim that Breadmaker can protect
        secrets from a compromised logged-in OS account that can run Breadmaker itself.
        """
        key=self._local_credential_encryption_key();nonce=secrets.token_bytes(12)
        plaintext=json.dumps({str(k):str(v) for k,v in dict(value).items() if v is not None},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        aad=('breadmaker-backup-credential-v2\0'+str(credential_id)).encode('utf-8')
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ciphertext=AESGCM(key).encrypt(nonce,plaintext,aad)
        return 'sealed-v2:'+base64.urlsafe_b64encode(nonce+ciphertext).decode('ascii')

    def _open_backup_credential_record(self, credential_id:str, sealed:str) -> dict[str,str]:
        text=str(sealed or '')
        if not text.startswith('sealed-v2:'):raise WorkspaceError('BACKUP_CREDENTIALS_INVALID')
        try:
            payload=base64.urlsafe_b64decode(text.split(':',1)[1].encode('ascii'));nonce,ciphertext=payload[:12],payload[12:]
            if len(nonce)!=12 or len(ciphertext)<16:raise ValueError
            aad=('breadmaker-backup-credential-v2\0'+str(credential_id)).encode('utf-8')
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw=AESGCM(self._local_credential_encryption_key()).decrypt(nonce,ciphertext,aad)
            value=json.loads(raw.decode('utf-8'))
            if not isinstance(value,dict):raise ValueError
            return {str(k):str(v) for k,v in value.items() if v is not None}
        except Exception as exc:
            raise WorkspaceError('BACKUP_CREDENTIALS_INVALID') from exc

    def _load_backup_credentials(self) -> dict[str,dict[str,str]]:
        try:
            raw=json.loads(self.backup_credentials_path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):
            return {}
        # Pre-alpha: do not continue accepting the old plaintext credential-store
        # format. Remote destinations simply need to be reconnected once.
        if not isinstance(raw,dict) or int(raw.get('version') or 0)!=2:return {}
        values=raw.get('credentials')
        if not isinstance(values,dict):return {}
        out={}
        for cid,item in values.items():
            if not isinstance(item,dict) or not item.get('sealed'):continue
            out[str(cid)]=self._open_backup_credential_record(str(cid),str(item['sealed']))
        return out

    def _save_backup_credentials(self, values:dict[str,dict[str,str]]) -> None:
        self.root.mkdir(parents=True,exist_ok=True)
        sealed={str(cid):{'sealed':self._seal_backup_credential_record(str(cid),dict(value))} for cid,value in values.items() if isinstance(value,dict)}
        fd,tmp_name=tempfile.mkstemp(prefix='backup-credentials-',suffix='.json',dir=self.root)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                json.dump({'version':2,'credentials':sealed},fh,indent=2,ensure_ascii=False);fh.flush();os.fsync(fh.fileno())
            try:os.chmod(tmp_name,0o600)
            except OSError:pass
            _durable_replace(Path(tmp_name),self.backup_credentials_path)
            try:os.chmod(self.backup_credentials_path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass

    def _local_credential_encryption_key(self) -> bytes:
        """Return the owner-only per-installation key for local credential sealing.

        The key is intentionally outside project packages and exports. This is an
        at-rest disclosure boundary for copied files/support bundles, not protection
        against compromise of the logged-in OS account that can execute Breadmaker.
        """
        if self.credential_encryption_key_path.exists():
            try:
                encoded=self.credential_encryption_key_path.read_bytes().strip()
                key=base64.urlsafe_b64decode(encoded)
                if len(key)==32:return key
            except (OSError,ValueError):
                pass
            raise WorkspaceError('BACKUP_CREDENTIALS_INVALID')
        self.root.mkdir(parents=True,exist_ok=True)
        key=secrets.token_bytes(32);encoded=base64.urlsafe_b64encode(key)
        fd,tmp_name=tempfile.mkstemp(prefix='credential-key-',suffix='.tmp',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as fh:
                fh.write(encoded);fh.flush();os.fsync(fh.fileno())
            try:os.chmod(tmp_name,0o600)
            except OSError:pass
            _durable_replace(Path(tmp_name),self.credential_encryption_key_path)
            try:os.chmod(self.credential_encryption_key_path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass
        return key

    def _backup_credential(self, credential_id:str) -> dict[str,str]|None:
        value=self._load_backup_credentials().get(str(credential_id or ''))
        return dict(value) if value else None

    def _store_backup_credential_data(self, data:dict[str,Any], credential_id:str|None=None) -> str:
        values=self._load_backup_credentials();cid=str(credential_id or uuid.uuid4().hex)
        values[cid]={str(k):str(v) for k,v in dict(data).items() if v is not None};self._save_backup_credentials(values);return cid

    def _store_backup_credential(self, username:str, password:str, credential_id:str|None=None) -> str:
        return self._store_backup_credential_data({'username':str(username or ''),'password':str(password or '')},credential_id)

    def _delete_backup_credential(self, credential_id:str) -> None:
        values=self._load_backup_credentials();cid=str(credential_id or '')
        if cid in values:
            values.pop(cid,None);self._save_backup_credentials(values)

    def google_oauth_config(self) -> dict[str,Any]:
        """Return this installation's user-supplied Google Desktop OAuth Client ID.

        Breadmaker is open source and deliberately does not ship a shared Google OAuth
        application identity. Writers who want direct Google Drive backups enter their
        own Desktop OAuth Client ID in the Google Drive connection modal; Breadmaker
        remembers that public application identifier in the installation-local
        ``google_oauth_client.json`` file. The environment override exists for tests and
        development. User refresh tokens remain separately protected in the credential
        store.
        """
        env_id=str(os.environ.get('BREADMAKER_GOOGLE_CLIENT_ID') or '').strip()
        if env_id:return {'configured':True,'client_id':env_id,'source':'environment'}
        try:
            raw=json.loads(self.google_oauth_config_path.read_text(encoding='utf-8'))
            local_id=str(raw.get('client_id') or '').strip() if isinstance(raw,dict) else ''
        except (OSError,json.JSONDecodeError):local_id=''
        return {'configured':bool(local_id),'client_id':local_id,'source':'local' if local_id else 'none'}

    def save_google_oauth_client_id(self, client_id:str) -> str:
        cid=str(client_id or '').strip()
        if not cid or len(cid)>512 or any(ch.isspace() for ch in cid):
            raise WorkspaceError('GOOGLE_CLIENT_ID_INVALID')
        self.root.mkdir(parents=True,exist_ok=True)
        fd,tmp_name=tempfile.mkstemp(prefix='google-oauth-',suffix='.json',dir=self.root)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                json.dump({'version':1,'client_id':cid},fh,indent=2,ensure_ascii=False);fh.flush();os.fsync(fh.fileno())
            try:os.chmod(tmp_name,0o600)
            except OSError:pass
            _durable_replace(Path(tmp_name),self.google_oauth_config_path)
            try:os.chmod(self.google_oauth_config_path,0o600)
            except OSError:pass
        finally:
            try:os.unlink(tmp_name)
            except FileNotFoundError:pass
        return cid

    def begin_google_drive_oauth(self, redirect_uri:str, *, destination_id:str|None=None, project_id:str|None=None, client_id:str|None=None, client_secret:str|None=None) -> dict[str,Any]:
        supplied=str(client_id or '').strip()
        if supplied:
            cid=self.save_google_oauth_client_id(supplied)
        else:
            config=self.google_oauth_config();cid=str(config.get('client_id') or '')
        secret=str(client_secret or '').strip()
        if secret and (len(secret)>2048 or any(ch in secret for ch in ('\r','\n','\0'))):
            raise WorkspaceError('GOOGLE_CLIENT_SECRET_INVALID')
        rec=self.get(project_id) if project_id else self.active()
        if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
        if destination_id:
            existing=next((item for item in self._backup_destinations(Path(rec['path'])) if str(item.get('id'))==str(destination_id)),None)
            if not existing:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
            if str(existing.get('provider') or '')!='google_drive':raise WorkspaceError('BACKUP_PROVIDER_MISMATCH')
            credential=self._backup_credential(str(existing.get('credential_id') or ''))
            if credential and str(credential.get('kind') or '')=='google_oauth':
                if not cid:cid=str(credential.get('client_id') or '').strip()
                if not secret:secret=str(credential.get('client_secret') or '')
        if not cid:raise WorkspaceError('GOOGLE_CLIENT_ID_REQUIRED')
        if not secret:raise WorkspaceError('GOOGLE_CLIENT_SECRET_REQUIRED')
        verifier,challenge=new_pkce_pair();state=uuid.uuid4().hex+uuid.uuid4().hex
        flow={'state':state,'created_mono':time.monotonic(),'project_id':rec['id'],'destination_id':str(destination_id or ''),'client_id':cid,'client_secret':secret,'redirect_uri':str(redirect_uri),'code_verifier':verifier,'status':'pending'}
        with self._lock:
            # OAuth flows are short-lived. Client secrets and PKCE verifiers exist only
            # in memory until Google completes the exchange, then are scrubbed from the
            # retained status record. Expire abandoned flows after fifteen minutes.
            expired=[key for key,value in self._google_oauth_flows.items() if time.monotonic()-float(value.get('created_mono') or 0)>900]
            for key in expired:self._google_oauth_flows.pop(key,None)
            self._google_oauth_flows[state]=flow
        try:url=google_authorization_url(cid,str(redirect_uri),state,challenge)
        except GoogleDriveError as exc:raise WorkspaceError(exc.code) from exc
        return {'state':state,'authorization_url':url,'configured':True}

    def complete_google_drive_oauth(self, state:str, code:str) -> dict[str,Any]:
        state=str(state or '')
        with self._lock:flow=self._google_oauth_flows.get(state)
        if not flow or flow.get('status')!='pending' or time.monotonic()-float(flow.get('created_mono') or 0)>900:
            raise WorkspaceError('GOOGLE_OAUTH_STATE_INVALID')
        try:
            secret=str(flow.get('client_secret') or '')
            token=exchange_authorization_code(str(flow['client_id']),secret,str(code or ''),str(flow['code_verifier']),str(flow['redirect_uri']))
            refresh_token=str(token.get('refresh_token') or '')
            client=GoogleDriveClient(str(flow['client_id']),refresh_token,secret)
            destination_id=str(flow.get('destination_id') or '')
            if destination_id:
                destination=self._finish_google_drive_reconnect(destination_id,client,str(flow['client_id']),secret,refresh_token,project_id=str(flow['project_id']))
            else:
                destination=self._finish_google_drive_add(client,str(flow['client_id']),secret,refresh_token,project_id=str(flow['project_id']))
            result={'ok':True,'destination':destination,'project_id':str(flow['project_id'])}
            safe_flow={key:value for key,value in flow.items() if key not in {'client_secret','code_verifier'}}
            with self._lock:self._google_oauth_flows[state]={**safe_flow,'status':'success','result':result,'completed_mono':time.monotonic()}
            return result
        except (GoogleDriveError,WorkspaceError,OSError) as exc:
            code_value=getattr(exc,'code',None) or str(exc)
            safe_flow={key:value for key,value in flow.items() if key not in {'client_secret','code_verifier'}}
            with self._lock:self._google_oauth_flows[state]={**safe_flow,'status':'error','error':str(code_value),'completed_mono':time.monotonic()}
            if isinstance(exc,WorkspaceError):raise
            raise WorkspaceError(str(code_value)) from exc

    def fail_google_drive_oauth(self, state:str, error:str) -> None:
        state=str(state or '')
        with self._lock:
            flow=self._google_oauth_flows.get(state)
            if flow:
                safe_flow={key:value for key,value in flow.items() if key not in {'client_secret','code_verifier'}}
                self._google_oauth_flows[state]={**safe_flow,'status':'error','error':str(error or 'GOOGLE_OAUTH_DENIED'),'completed_mono':time.monotonic()}

    def google_drive_oauth_status(self, state:str) -> dict[str,Any]:
        with self._lock:flow=dict(self._google_oauth_flows.get(str(state or '')) or {})
        if not flow:return {'status':'missing'}
        status=str(flow.get('status') or 'pending');result={'status':status}
        if status=='success':result.update(flow.get('result') or {})
        elif status=='error':result['error']=str(flow.get('error') or 'GOOGLE_OAUTH_FAILED')
        return result

    def _finish_google_drive_add(self, client:GoogleDriveClient, client_id:str, client_secret:str, refresh_token:str, *, project_id:str|None=None) -> dict[str,Any]:
        try:folder_id,location_id=client.discover_or_create_root();client.check_root(folder_id,location_id)
        except GoogleDriveError as exc:raise WorkspaceError(exc.code) from exc
        credential_id=self._store_backup_credential_data({'kind':'google_oauth','client_id':client_id,'client_secret':client_secret,'refresh_token':refresh_token})
        try:
            with self._lock:
                rec=self.get(project_id) if project_id else self.active()
                if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
                root=Path(rec['path']);destinations=self._backup_destinations(root)
                if any(str(item.get('provider'))=='google_drive' and str(item.get('location_id') or '')==location_id for item in destinations):
                    raise WorkspaceError('BACKUP_DESTINATION_DUPLICATE')
                dest={'id':uuid.uuid4().hex,'provider':'google_drive','destination_kind':'remote','name':'Google Drive','folder_id':folder_id,'folder_name':GOOGLE_BACKUP_FOLDER_NAME,'credential_id':credential_id,'location_id':location_id,'created_at':now(),'last_verified':None,'remote_history_repository_initialized':False}
                destinations.append(dest);self._save_backup_destinations(root,destinations)
            return self._destination_status(rec,dest,_project_change_token(root))
        except Exception:
            self._delete_backup_credential(credential_id);raise

    def _finish_google_drive_reconnect(self, destination_id:str, client:GoogleDriveClient, client_id:str, client_secret:str, refresh_token:str, *, project_id:str|None=None) -> dict[str,Any]:
        with self._lock:
            rec=self.get(project_id) if project_id else self.active()
            if not rec:raise WorkspaceError('NO_ACTIVE_PROJECT')
            root=Path(rec['path']);destinations=self._backup_destinations(root);idx=next((i for i,item in enumerate(destinations) if str(item.get('id'))==str(destination_id)),None)
            if idx is None:raise WorkspaceError('BACKUP_DESTINATION_NOT_FOUND')
            current=dict(destinations[idx])
            if str(current.get('provider') or '')!='google_drive':raise WorkspaceError('BACKUP_PROVIDER_MISMATCH')
            try:client.check_root(str(current.get('folder_id') or ''),str(current.get('location_id') or ''))
            except GoogleDriveError as exc:raise WorkspaceError(exc.code) from exc
            old_credential=str(current.get('credential_id') or '');new_credential=self._store_backup_credential_data({'kind':'google_oauth','client_id':client_id,'client_secret':client_secret,'refresh_token':refresh_token})
            current['credential_id']=new_credential;current.pop('last_error',None);current.pop('last_error_at',None)
            destinations[idx]=current;self._save_backup_destinations(root,destinations)
            if old_credential and old_credential!=new_credential:self._delete_backup_credential(old_credential)
        return self._destination_status(rec,current,_project_change_token(root))
