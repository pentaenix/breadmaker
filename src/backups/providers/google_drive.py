from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GOOGLE_AUTHORIZATION_ENDPOINT = os.environ.get('BREADMAKER_GOOGLE_AUTHORIZATION_ENDPOINT', 'https://accounts.google.com/o/oauth2/v2/auth')
GOOGLE_TOKEN_ENDPOINT = os.environ.get('BREADMAKER_GOOGLE_TOKEN_ENDPOINT', 'https://oauth2.googleapis.com/token')
GOOGLE_DRIVE_API_ROOT = os.environ.get('BREADMAKER_GOOGLE_DRIVE_API_ROOT', 'https://www.googleapis.com/drive/v3')
GOOGLE_DRIVE_UPLOAD_ROOT = os.environ.get('BREADMAKER_GOOGLE_DRIVE_UPLOAD_ROOT', 'https://www.googleapis.com/upload/drive/v3')
GOOGLE_DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
GOOGLE_FOLDER_MIME = 'application/vnd.google-apps.folder'
GOOGLE_BACKUP_FOLDER_NAME = 'Breadmaker Backups'
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


class GoogleDriveError(RuntimeError):
    def __init__(self, code: str, message: str = '', status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_TLS_CONTEXT = ssl.create_default_context()


def _opener():
    # Build a fresh opener per request, but reuse the immutable trust context.
    # Re-loading the OS CA bundle for every Drive call is expensive and can serialize
    # concurrent backup/history workers; the trust roots do not change during one
    # Breadmaker process.
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=_TLS_CONTEXT))


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _parse_json_bytes(raw: bytes, code: str = 'GOOGLE_RESPONSE_INVALID') -> dict[str, Any]:
    try:
        value = json.loads(raw.decode('utf-8')) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleDriveError(code) from exc
    if not isinstance(value, dict):
        raise GoogleDriveError(code)
    return value


def _error_reason(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode('utf-8'))
        error = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error, dict):
            errors = error.get('errors')
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return str(errors[0].get('reason') or '')
            return str(error.get('status') or error.get('message') or '')
        return str(error or '')
    except Exception:
        return ''


def _map_http_error(status: int, raw: bytes = b'') -> GoogleDriveError:
    reason = _error_reason(raw)
    lowered = reason.lower()
    if status == 401:
        return GoogleDriveError('GOOGLE_AUTH_FAILED', reason or f'HTTP {status}', status)
    if status == 404:
        return GoogleDriveError('GOOGLE_NOT_FOUND', reason or 'HTTP 404', status)
    if status == 403 and any(token in lowered for token in ('storagequota', 'quotaexceeded', 'storage quota')):
        return GoogleDriveError('GOOGLE_STORAGE_FULL', reason or f'HTTP {status}', status)
    if status in {408, 429, 500, 502, 503, 504}:
        return GoogleDriveError('GOOGLE_UNAVAILABLE', reason or f'HTTP {status}', status)
    if status in {301, 302, 303, 307, 308}:
        return GoogleDriveError('GOOGLE_REDIRECT_FORBIDDEN', f'HTTP {status}', status)
    if status == 403:
        return GoogleDriveError('GOOGLE_PERMISSION_DENIED', reason or f'HTTP {status}', status)
    return GoogleDriveError('GOOGLE_HTTP_ERROR', reason or f'HTTP {status}', status)


def _request(method: str, url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
             ok: tuple[int, ...] = (200,), timeout: float = 30.0) -> tuple[int, Any, bytes]:
    req_headers = {'User-Agent': 'Breadmaker-Editor/7.7.6'}
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with _opener().open(req, timeout=timeout) as response:
            raw = response.read()
            status = int(response.getcode())
            if status not in ok:
                raise _map_http_error(status, raw)
            return status, response.headers, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if exc.code in ok:
            return int(exc.code), exc.headers, raw
        raise _map_http_error(int(exc.code), raw) from exc
    except ssl.SSLError as exc:
        raise GoogleDriveError('GOOGLE_TLS_FAILED', str(exc)) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, ssl.SSLError):
            raise GoogleDriveError('GOOGLE_TLS_FAILED', str(reason)) from exc
        raise GoogleDriveError('GOOGLE_UNAVAILABLE', str(reason)) from exc


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return verifier, challenge


def authorization_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    client_id = str(client_id or '').strip()
    if not client_id:
        raise GoogleDriveError('GOOGLE_CLIENT_ID_REQUIRED')
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': GOOGLE_DRIVE_SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return GOOGLE_AUTHORIZATION_ENDPOINT + '?' + urllib.parse.urlencode(params)


def exchange_authorization_code(client_id: str, client_secret: str, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
    fields = {
        'client_id': str(client_id or '').strip(),
        'code': str(code or '').strip(),
        'code_verifier': str(code_verifier or ''),
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }
    secret = str(client_secret or '')
    if secret:
        fields['client_secret'] = secret
    body = urllib.parse.urlencode(fields).encode('ascii')
    try:
        _, _, raw = _request('POST', GOOGLE_TOKEN_ENDPOINT, data=body,
                             headers={'Content-Type': 'application/x-www-form-urlencoded'}, ok=(200,))
    except GoogleDriveError as exc:
        if exc.status in {400, 401}:
            raise GoogleDriveError('GOOGLE_AUTH_FAILED', str(exc), exc.status) from exc
        raise
    token = _parse_json_bytes(raw, 'GOOGLE_TOKEN_RESPONSE_INVALID')
    if not str(token.get('access_token') or ''):
        raise GoogleDriveError('GOOGLE_AUTH_FAILED')
    # Google may omit a refresh token unless consent was actually granted. Breadmaker
    # needs offline refresh for background backups, so a connection without one is not
    # considered durable enough to save.
    if not str(token.get('refresh_token') or ''):
        raise GoogleDriveError('GOOGLE_REFRESH_TOKEN_MISSING')
    return token


def _truncate_utf8(value: object, max_bytes: int) -> str:
    raw = str(value or '').encode('utf-8')
    if len(raw) <= max_bytes:
        return raw.decode('utf-8')
    raw = raw[:max_bytes]
    while raw:
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ''


def _q(value: str) -> str:
    return str(value).replace('\\', '\\\\').replace("'", "\\'")


@dataclass
class GoogleFile:
    id: str
    name: str
    size: int
    created_time: str
    modified_time: str
    app_properties: dict[str, str]


class GoogleDriveClient:
    def __init__(self, client_id: str, refresh_token: str, client_secret: str = '', *, timeout: float = 30.0):
        self.client_id = str(client_id or '').strip()
        self.client_secret = str(client_secret or '')
        self.refresh_token = str(refresh_token or '').strip()
        self.timeout = timeout
        if not self.client_id:
            raise GoogleDriveError('GOOGLE_CLIENT_ID_REQUIRED')
        if not self.refresh_token:
            raise GoogleDriveError('GOOGLE_AUTH_FAILED')
        self._access_token_value = ''
        self._access_token_expiry = 0.0

    def _refresh_access_token(self, force: bool = False) -> str:
        if not force and self._access_token_value and time.monotonic() < self._access_token_expiry - 30:
            return self._access_token_value
        fields = {
            'client_id': self.client_id,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        }
        if self.client_secret:
            fields['client_secret'] = self.client_secret
        body = urllib.parse.urlencode(fields).encode('ascii')
        try:
            _, _, raw = _request('POST', GOOGLE_TOKEN_ENDPOINT, data=body,
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'}, ok=(200,), timeout=self.timeout)
        except GoogleDriveError as exc:
            if exc.status in {400, 401}:
                raise GoogleDriveError('GOOGLE_AUTH_FAILED', str(exc), exc.status) from exc
            raise
        payload = _parse_json_bytes(raw, 'GOOGLE_TOKEN_RESPONSE_INVALID')
        token = str(payload.get('access_token') or '')
        if not token:
            raise GoogleDriveError('GOOGLE_AUTH_FAILED')
        try:
            expires = max(60, int(payload.get('expires_in') or 3600))
        except (TypeError, ValueError):
            expires = 3600
        self._access_token_value = token
        self._access_token_expiry = time.monotonic() + expires
        return token

    def _api(self, method: str, path: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None, ok: tuple[int, ...] = (200,)) -> dict[str, Any]:
        url = GOOGLE_DRIVE_API_ROOT.rstrip('/') + '/' + path.lstrip('/')
        if params:
            url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = _json_bytes(body) if body is not None else None
        token = self._refresh_access_token()
        headers = {'Authorization': f'Bearer {token}'}
        if data is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        try:
            _, _, raw = _request(method, url, data=data, headers=headers, ok=ok, timeout=self.timeout)
        except GoogleDriveError as exc:
            if exc.code == 'GOOGLE_AUTH_FAILED':
                token = self._refresh_access_token(force=True)
                headers['Authorization'] = f'Bearer {token}'
                _, _, raw = _request(method, url, data=data, headers=headers, ok=ok, timeout=self.timeout)
            else:
                raise
        return _parse_json_bytes(raw) if raw else {}

    def list_files(self, query: str, *, page_size: int = 1000) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page_token = ''
        while True:
            payload = self._api('GET', 'files', params={
                'q': query,
                'spaces': 'drive',
                'pageSize': min(1000, max(1, int(page_size))),
                'pageToken': page_token or None,
                'fields': 'nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,trashed,appProperties)',
            })
            rows = payload.get('files')
            if isinstance(rows, list):
                out.extend(dict(row) for row in rows if isinstance(row, dict))
            page_token = str(payload.get('nextPageToken') or '')
            if not page_token:
                break
        return out

    def discover_or_create_root(self) -> tuple[str, str]:
        query = (
            f"mimeType='{GOOGLE_FOLDER_MIME}' and trashed=false and "
            "appProperties has { key='breadmaker_kind' and value='backup_root' }"
        )
        rows = self.list_files(query)
        for row in rows:
            props = row.get('appProperties') if isinstance(row.get('appProperties'), dict) else {}
            location_id = str(props.get('breadmaker_location_id') or '')
            if str(row.get('id') or '') and location_id:
                return str(row['id']), location_id
        location_id = secrets.token_hex(16)
        created = self._api('POST', 'files', params={'fields': 'id,name,appProperties'}, body={
            'name': GOOGLE_BACKUP_FOLDER_NAME,
            'mimeType': GOOGLE_FOLDER_MIME,
            'appProperties': {
                'breadmaker_kind': 'backup_root',
                'breadmaker_location_id': location_id,
            },
        })
        folder_id = str(created.get('id') or '')
        props = created.get('appProperties') if isinstance(created.get('appProperties'), dict) else {}
        actual_location = str(props.get('breadmaker_location_id') or location_id)
        if not folder_id or not actual_location:
            raise GoogleDriveError('GOOGLE_FOLDER_CREATE_FAILED')
        return folder_id, actual_location

    def check_root(self, folder_id: str, expected_location_id: str) -> dict[str, Any]:
        try:
            row = self._api('GET', f'files/{urllib.parse.quote(str(folder_id), safe="")}', params={
                'fields': 'id,name,mimeType,trashed,appProperties'
            })
        except GoogleDriveError as exc:
            if exc.code == 'GOOGLE_NOT_FOUND':
                raise GoogleDriveError('GOOGLE_IDENTITY_MISMATCH') from exc
            raise
        props = row.get('appProperties') if isinstance(row.get('appProperties'), dict) else {}
        if row.get('trashed') or str(row.get('mimeType') or '') != GOOGLE_FOLDER_MIME:
            raise GoogleDriveError('GOOGLE_IDENTITY_MISMATCH')
        if str(props.get('breadmaker_kind') or '') != 'backup_root':
            raise GoogleDriveError('GOOGLE_IDENTITY_MISMATCH')
        if str(props.get('breadmaker_location_id') or '') != str(expected_location_id or ''):
            raise GoogleDriveError('GOOGLE_IDENTITY_MISMATCH')
        return row

    def _package_query(self, folder_id: str, project_id: str | None = None) -> str:
        clauses = [
            f"'{_q(folder_id)}' in parents",
            'trashed=false',
            "appProperties has { key='breadmaker_kind' and value='backup_package' }",
        ]
        if project_id:
            clauses.append(
                "appProperties has { key='breadmaker_project_id' and value='" + _q(project_id) + "' }"
            )
        return ' and '.join(clauses)

    def list_packages(self, folder_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.list_files(self._package_query(folder_id, project_id))
        def stamp(row):
            props = row.get('appProperties') if isinstance(row.get('appProperties'), dict) else {}
            return str(props.get('breadmaker_created_at') or row.get('createdTime') or '')
        rows.sort(key=stamp, reverse=True)
        return rows

    def _publication_query(self, folder_id: str, project_id: str | None = None) -> str:
        clauses = [
            f"'{_q(folder_id)}' in parents",
            'trashed=false',
            "appProperties has { key='breadmaker_kind' and value='backup_publication' }",
        ]
        if project_id:
            clauses.append("appProperties has { key='breadmaker_project_id' and value='" + _q(project_id) + "' }")
        return ' and '.join(clauses)

    def list_publications(self, folder_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows=self.list_files(self._publication_query(folder_id,project_id))
        def authority(row):
            props=row.get('appProperties') if isinstance(row.get('appProperties'),dict) else {}
            try:generation=int(props.get('breadmaker_generation') or 0)
            except (TypeError,ValueError):generation=0
            return generation,str(props.get('breadmaker_created_at') or row.get('createdTime') or ''),str(row.get('id') or '')
        rows.sort(key=authority,reverse=True);return rows

    def list_managed_project_files(self, folder_id: str, project_id: str) -> list[dict[str, Any]]:
        query=(f"'{_q(folder_id)}' in parents and trashed=false and "
               "appProperties has { key='breadmaker_project_id' and value='"+_q(project_id)+"' }")
        return self.list_files(query)

    def create_publication_record(self, folder_id: str, *, publication_name: str, project_id: str, project_name: str,
                                  package_id: str, package_name: str, fingerprint: str, created_at: str,
                                  project_generation: int, package_sha256: str, package_bytes: int, workday: str = '', canonical_state_hash: str = '') -> dict[str, Any]:
        props={
            'breadmaker_kind':'backup_publication',
            'breadmaker_project_id':_truncate_utf8(project_id,80),
            'breadmaker_project_name':_truncate_utf8(project_name,88),
            'breadmaker_package_id':_truncate_utf8(package_id,120),
            'breadmaker_package_name':_truncate_utf8(package_name,120),
            'breadmaker_fingerprint':_truncate_utf8(fingerprint,80),
            'breadmaker_created_at':_truncate_utf8(created_at,80),
            'breadmaker_generation':str(max(0,int(project_generation))),
            'breadmaker_package_sha256':_truncate_utf8(package_sha256,80),
            'breadmaker_canonical_state_hash':_truncate_utf8(canonical_state_hash,80),
            'breadmaker_package_bytes':str(max(0,int(package_bytes))),
            'breadmaker_workday':_truncate_utf8(workday,24),
        }
        return self._api('POST','files',params={'fields':'id,name,size,createdTime,modifiedTime,appProperties'},body={
            'name':_truncate_utf8(publication_name,120),
            'mimeType':'application/json',
            'parents':[folder_id],
            'appProperties':props,
        })

    def _initiate_resumable_upload(self, source: Path, folder_id: str, filename: str, app_properties: dict[str,str] | None = None, mime_type: str = 'application/zip') -> str:
        props={'breadmaker_kind':'uploading'}
        props.update({str(k):_truncate_utf8(v,120) for k,v in (app_properties or {}).items()})
        metadata = {
            'name': filename,
            'parents': [folder_id],
            'mimeType': str(mime_type or 'application/octet-stream'),
            'appProperties': props,
        }
        url = GOOGLE_DRIVE_UPLOAD_ROOT.rstrip('/') + '/files?' + urllib.parse.urlencode({
            'uploadType': 'resumable',
            'fields': 'id,name,size,createdTime,modifiedTime,appProperties',
        })
        token = self._refresh_access_token()
        _, headers, _ = _request('POST', url, data=_json_bytes(metadata), headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
            'X-Upload-Content-Type': str(mime_type or 'application/octet-stream'),
            'X-Upload-Content-Length': str(Path(source).stat().st_size),
        }, ok=(200, 201), timeout=self.timeout)
        session = str(headers.get('Location') or '')
        if not session:
            raise GoogleDriveError('GOOGLE_UPLOAD_SESSION_INVALID')
        parsed = urllib.parse.urlsplit(session)
        if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise GoogleDriveError('GOOGLE_UPLOAD_SESSION_INVALID')
        return session

    def _upload_status(self, session_url: str, total: int) -> tuple[int, dict[str, Any] | None]:
        token = self._refresh_access_token()
        try:
            status, headers, raw = _request('PUT', session_url, data=b'', headers={
                'Authorization': f'Bearer {token}',
                'Content-Length': '0',
                'Content-Range': f'bytes */{total}',
            }, ok=(200, 201, 308), timeout=self.timeout)
        except GoogleDriveError:
            raise
        if status in {200, 201}:
            return total, _parse_json_bytes(raw)
        range_value = str(headers.get('Range') or '')
        if '-' in range_value:
            try:
                return int(range_value.rsplit('-', 1)[1]) + 1, None
            except ValueError:
                pass
        return 0, None

    def upload_file(self, source: Path, folder_id: str, filename: str, app_properties: dict[str,str] | None = None, mime_type: str = 'application/zip') -> dict[str, Any]:
        source = Path(source)
        total = source.stat().st_size
        session = self._initiate_resumable_upload(source, folder_id, filename, app_properties, mime_type=mime_type)
        offset = 0
        with source.open('rb') as fh:
            while offset < total:
                fh.seek(offset)
                chunk = fh.read(min(UPLOAD_CHUNK_BYTES, total - offset))
                if not chunk:
                    raise GoogleDriveError('GOOGLE_UPLOAD_INCOMPLETE')
                end = offset + len(chunk) - 1
                attempts = 0
                while True:
                    token = self._refresh_access_token()
                    try:
                        status, _, raw = _request('PUT', session, data=chunk, headers={
                            'Authorization': f'Bearer {token}',
                            'Content-Type': 'application/zip',
                            'Content-Length': str(len(chunk)),
                            'Content-Range': f'bytes {offset}-{end}/{total}',
                        }, ok=(200, 201, 308), timeout=max(self.timeout, 60.0))
                        if status in {200, 201}:
                            return _parse_json_bytes(raw)
                        offset = end + 1
                        break
                    except GoogleDriveError as exc:
                        if exc.code != 'GOOGLE_UNAVAILABLE' or attempts >= 3:
                            raise
                        attempts += 1
                        time.sleep(0.25 * (2 ** (attempts - 1)))
                        confirmed, completed = self._upload_status(session, total)
                        if completed is not None:
                            return completed
                        offset = confirmed
                        break
        # Zero-byte files are not expected for Breadmaker packages, but query the
        # session defensively rather than claiming success without final metadata.
        confirmed, completed = self._upload_status(session, total)
        if completed is not None:
            return completed
        raise GoogleDriveError('GOOGLE_UPLOAD_INCOMPLETE', f'Uploaded {confirmed} of {total} bytes')


    def ensure_child_folder(self, parent_id: str, name: str, *, kind: str = 'history_folder', workspace_id: str = '') -> str:
        clauses=[f"'{_q(parent_id)}' in parents",'trashed=false',f"mimeType='{GOOGLE_FOLDER_MIME}'",
                 "name='"+_q(name)+"'"]
        rows=self.list_files(' and '.join(clauses))
        for row in rows:
            if str(row.get('id') or ''):return str(row['id'])
        props={'breadmaker_kind':_truncate_utf8(kind,80)}
        if workspace_id:props['breadmaker_history_workspace_id']=_truncate_utf8(workspace_id,80)
        created=self._api('POST','files',params={'fields':'id,name,mimeType,appProperties'},body={'name':name,'mimeType':GOOGLE_FOLDER_MIME,'parents':[parent_id],'appProperties':props})
        folder_id=str(created.get('id') or '')
        if not folder_id:raise GoogleDriveError('GOOGLE_FOLDER_CREATE_FAILED')
        return folder_id

    def list_children_named(self, folder_id: str) -> list[dict[str,Any]]:
        return self.list_files(f"'{_q(folder_id)}' in parents and trashed=false")

    def find_child_by_name(self, folder_id: str, name: str) -> dict[str,Any]|None:
        rows=self.list_files(f"'{_q(folder_id)}' in parents and trashed=false and name='"+_q(name)+"'")
        return rows[0] if rows else None

    def update_package_metadata(self, file_id: str, *, project_id: str, project_name: str,
                                fingerprint: str, created_at: str, reason: str, project_generation: int = 0,
                                kind: str = 'backup_package') -> dict[str, Any]:
        props = {
            'breadmaker_kind': str(kind or 'backup_package'),
            'breadmaker_project_id': _truncate_utf8(project_id, 80),
            'breadmaker_project_name': _truncate_utf8(project_name, 88),
            'breadmaker_fingerprint': _truncate_utf8(fingerprint, 80),
            'breadmaker_created_at': _truncate_utf8(created_at, 80),
            'breadmaker_reason': _truncate_utf8(reason, 80),
            'breadmaker_generation': str(max(0,int(project_generation))),
        }
        return self._api('PATCH', f'files/{urllib.parse.quote(str(file_id), safe="")}', params={
            'fields': 'id,name,size,createdTime,modifiedTime,appProperties'
        }, body={'appProperties': props})

    def download_file(self, file_id: str, target: Path) -> None:
        token = self._refresh_access_token()
        url = GOOGLE_DRIVE_API_ROOT.rstrip('/') + '/files/' + urllib.parse.quote(str(file_id), safe='') + '?alt=media'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'User-Agent': 'Breadmaker-Editor/7.7.6'}, method='GET')
        try:
            with _opener().open(req, timeout=max(self.timeout, 60.0)) as response:
                target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('wb') as fh:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                    fh.flush()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise _map_http_error(int(exc.code), raw) from exc
        except ssl.SSLError as exc:
            raise GoogleDriveError('GOOGLE_TLS_FAILED', str(exc)) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise GoogleDriveError('GOOGLE_UNAVAILABLE', str(getattr(exc, 'reason', exc))) from exc

    def delete_file(self, file_id: str, *, missing_ok: bool = False) -> None:
        try:
            self._api('DELETE', f'files/{urllib.parse.quote(str(file_id), safe="")}', ok=(204,))
        except GoogleDriveError as exc:
            if missing_ok and exc.code == 'GOOGLE_NOT_FOUND':
                return
            raise

    def list_history_files(self, folder_id: str, workspace_id: str, *, kind: str | None = None, key: str | None = None) -> list[dict[str,Any]]:
        clauses=[f"'{_q(folder_id)}' in parents",'trashed=false',
                 "appProperties has { key='breadmaker_kind' and value='history_object' }",
                 "appProperties has { key='breadmaker_history_workspace_id' and value='"+_q(workspace_id)+"' }"]
        if kind:clauses.append("appProperties has { key='breadmaker_history_kind' and value='"+_q(kind)+"' }")
        if key:clauses.append("appProperties has { key='breadmaker_history_key' and value='"+_q(key)+"' }")
        return self.list_files(' and '.join(clauses))

    def delete_tree(self, folder_id: str, *, missing_ok: bool = False) -> int:
        """Delete one managed folder tree for explicit privacy erasure only."""
        removed=0
        try:children=self.list_children_named(folder_id)
        except GoogleDriveError as exc:
            if missing_ok and exc.code=='GOOGLE_NOT_FOUND':return 0
            raise
        for row in children:
            rid=str(row.get('id') or '')
            if not rid:continue
            if str(row.get('mimeType') or '')==GOOGLE_FOLDER_MIME:
                removed+=self.delete_tree(rid,missing_ok=True)
            else:
                self.delete_file(rid,missing_ok=True);removed+=1
        self.delete_file(folder_id,missing_ok=missing_ok);return removed+1

    def find_package_by_name(self, folder_id: str, filename: str) -> dict[str, Any] | None:
        if Path(filename).name != filename or not filename.endswith('.breadmaker'):
            raise GoogleDriveError('GOOGLE_BACKUP_NAME_INVALID')
        query = self._package_query(folder_id) + " and name='" + _q(filename) + "'"
        rows = self.list_files(query)
        return rows[0] if rows else None
