from __future__ import annotations

import base64
import json
import http.client
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class WebDAVError(RuntimeError):
    def __init__(self, code: str, message: str = '', status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_endpoint(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        raise WebDAVError('WEBDAV_ENDPOINT_REQUIRED')
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise WebDAVError('WEBDAV_ENDPOINT_INVALID')
    if parsed.username is not None or parsed.password is not None:
        raise WebDAVError('WEBDAV_URL_CREDENTIALS_FORBIDDEN')
    if parsed.query or parsed.fragment:
        raise WebDAVError('WEBDAV_ENDPOINT_INVALID')
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' and host not in {'localhost', '127.0.0.1', '::1'}:
        raise WebDAVError('WEBDAV_HTTPS_REQUIRED')
    path = parsed.path or '/'
    if not path.endswith('/'):
        path += '/'
    clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
    return clean


@dataclass
class WebDAVResponse:
    status: int
    headers: object
    data: bytes


class WebDAVClient:
    def __init__(self, endpoint: str, username: str = '', password: str = '', timeout: float = 25.0):
        self.endpoint = normalize_endpoint(endpoint)
        self.username = str(username or '')
        self.password = str(password or '')
        self.timeout = timeout
        handlers = [_NoRedirect()]
        if self.endpoint.startswith('https://'):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self._opener = urllib.request.build_opener(*handlers)

    def _url(self, relative: str = '') -> str:
        rel = str(relative or '').lstrip('/')
        # Relative names are provider-created filenames; encode path segments and do
        # not allow traversal to escape the configured WebDAV collection.
        pieces = [urllib.parse.quote(p, safe='') for p in rel.split('/') if p not in {'', '.'}]
        if any(p == '..' for p in rel.split('/')):
            raise WebDAVError('WEBDAV_PATH_INVALID')
        return self.endpoint + '/'.join(pieces)

    def request(self, method: str, relative: str = '', data: bytes | None = None, headers: dict[str, str] | None = None, ok: tuple[int, ...] = (200, 201, 204)) -> WebDAVResponse:
        req_headers = {'User-Agent': 'Breadmaker-Editor/7.6'}
        if self.username or self.password:
            token = base64.b64encode(f'{self.username}:{self.password}'.encode('utf-8')).decode('ascii')
            req_headers['Authorization'] = f'Basic {token}'
        req_headers.update(headers or {})
        req = urllib.request.Request(self._url(relative), data=data, headers=req_headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                body = response.read()
                status = int(response.getcode())
                if status not in ok:
                    raise WebDAVError('WEBDAV_HTTP_ERROR', f'HTTP {status}', status)
                return WebDAVResponse(status, response.headers, body)
        except urllib.error.HTTPError as exc:
            # Redirects are intentionally surfaced as errors so an Authorization
            # header can never be forwarded to an unexpected host.
            if exc.code in {301, 302, 303, 307, 308}:
                raise WebDAVError('WEBDAV_REDIRECT_FORBIDDEN', f'HTTP {exc.code}', exc.code) from exc
            if exc.code in {401, 403}:
                raise WebDAVError('WEBDAV_AUTH_FAILED', f'HTTP {exc.code}', exc.code) from exc
            if exc.code == 404:
                raise WebDAVError('WEBDAV_NOT_FOUND', 'HTTP 404', 404) from exc
            if exc.code in {507}:
                raise WebDAVError('WEBDAV_STORAGE_FULL', f'HTTP {exc.code}', exc.code) from exc
            raise WebDAVError('WEBDAV_HTTP_ERROR', f'HTTP {exc.code}', exc.code) from exc
        except ssl.SSLError as exc:
            raise WebDAVError('WEBDAV_TLS_FAILED', str(exc)) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(reason, ssl.SSLError):
                raise WebDAVError('WEBDAV_TLS_FAILED', str(reason)) from exc
            raise WebDAVError('WEBDAV_UNAVAILABLE', str(reason)) from exc

    def _stream_connection(self):
        parsed=urllib.parse.urlsplit(self.endpoint)
        port=parsed.port
        if parsed.scheme=='https':return http.client.HTTPSConnection(parsed.hostname,port or 443,timeout=self.timeout,context=ssl.create_default_context())
        return http.client.HTTPConnection(parsed.hostname,port or 80,timeout=self.timeout)

    def _stream_path(self, relative: str) -> str:
        parsed=urllib.parse.urlsplit(self._url(relative));return urllib.parse.urlunsplit(('', '', parsed.path, parsed.query, ''))

    def _auth_headers(self) -> dict[str,str]:
        headers={'User-Agent':'Breadmaker-Editor/7.6'}
        if self.username or self.password:
            token=base64.b64encode(f'{self.username}:{self.password}'.encode('utf-8')).decode('ascii');headers['Authorization']=f'Basic {token}'
        return headers

    def put_file(self, relative: str, source: Path) -> None:
        source=Path(source);headers=self._auth_headers();headers.update({'Content-Type':'application/octet-stream','Content-Length':str(source.stat().st_size)})
        conn=self._stream_connection()
        try:
            conn.putrequest('PUT',self._stream_path(relative))
            for key,value in headers.items():conn.putheader(key,value)
            conn.endheaders()
            with source.open('rb') as fh:
                for chunk in iter(lambda:fh.read(1024*1024),b''):conn.send(chunk)
            response=conn.getresponse();status=response.status;response.read()
            if status in {200,201,204}:return
            if status in {401,403}:raise WebDAVError('WEBDAV_AUTH_FAILED',f'HTTP {status}',status)
            if status==507:raise WebDAVError('WEBDAV_STORAGE_FULL',f'HTTP {status}',status)
            if status in {301,302,303,307,308}:raise WebDAVError('WEBDAV_REDIRECT_FORBIDDEN',f'HTTP {status}',status)
            raise WebDAVError('WEBDAV_HTTP_ERROR',f'HTTP {status}',status)
        except (ssl.SSLError,socket.timeout,OSError,http.client.HTTPException) as exc:
            if isinstance(exc,WebDAVError):raise
            raise WebDAVError('WEBDAV_UNAVAILABLE',str(exc)) from exc
        finally:conn.close()

    def get_file(self, relative: str, target: Path) -> None:
        conn=self._stream_connection();headers=self._auth_headers()
        try:
            conn.request('GET',self._stream_path(relative),headers=headers);response=conn.getresponse();status=response.status
            if status!=200:
                response.read()
                if status in {401,403}:raise WebDAVError('WEBDAV_AUTH_FAILED',f'HTTP {status}',status)
                if status==404:raise WebDAVError('WEBDAV_NOT_FOUND','HTTP 404',404)
                if status in {301,302,303,307,308}:raise WebDAVError('WEBDAV_REDIRECT_FORBIDDEN',f'HTTP {status}',status)
                raise WebDAVError('WEBDAV_HTTP_ERROR',f'HTTP {status}',status)
            target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('wb') as fh:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    fh.write(chunk)
                fh.flush()
        except (ssl.SSLError,socket.timeout,OSError,http.client.HTTPException) as exc:
            if isinstance(exc,WebDAVError):raise
            raise WebDAVError('WEBDAV_UNAVAILABLE',str(exc)) from exc
        finally:conn.close()

    def health(self) -> None:
        self.request('PROPFIND', '', headers={'Depth': '0'}, ok=(200, 207))

    def ensure_collection(self, relative: str) -> None:
        current=[]
        for part in [p for p in str(relative or '').strip('/').split('/') if p]:
            current.append(part); rel='/'.join(current)
            try:self.request('MKCOL',rel,ok=(201,405))
            except WebDAVError as exc:
                if exc.status not in {405}:raise

    def list_children(self, relative: str = '') -> list[str]:
        """Return direct child names using standard WebDAV PROPFIND Depth: 1."""
        response=self.request('PROPFIND',relative,headers={'Depth':'1'},ok=(200,207))
        if not response.data:return []
        try:root=ET.fromstring(response.data)
        except ET.ParseError as exc:raise WebDAVError('WEBDAV_METADATA_INVALID') from exc
        out=[];base_path=urllib.parse.urlsplit(self._url(relative)).path.rstrip('/')+'/'
        for elem in root.iter():
            if not str(elem.tag).endswith('href') or not elem.text:continue
            href=urllib.parse.unquote(urllib.parse.urlsplit(elem.text).path)
            if not href.startswith(base_path):continue
            rel=href[len(base_path):].strip('/')
            if rel and '/' not in rel and rel not in out:out.append(rel)
        return out

    def get(self, relative: str) -> bytes:
        return self.request('GET', relative, ok=(200,)).data

    def put(self, relative: str, data: bytes, content_type: str = 'application/octet-stream') -> None:
        self.request('PUT', relative, data=data, headers={'Content-Type': content_type}, ok=(200, 201, 204))

    def delete(self, relative: str, missing_ok: bool = False) -> None:
        try:
            self.request('DELETE', relative, ok=(200, 202, 204))
        except WebDAVError as exc:
            if missing_ok and exc.code == 'WEBDAV_NOT_FOUND':
                return
            raise

    def move(self, source: str, target: str, overwrite: bool = True) -> None:
        destination = self._url(target)
        self.request('MOVE', source, headers={'Destination': destination, 'Overwrite': 'T' if overwrite else 'F'}, ok=(201, 204))

    def get_json(self, relative: str) -> dict:
        try:
            raw = self.get(relative)
        except WebDAVError as exc:
            if exc.code == 'WEBDAV_NOT_FOUND':
                return {}
            raise
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebDAVError('WEBDAV_METADATA_INVALID') from exc
        if not isinstance(value, dict):
            raise WebDAVError('WEBDAV_METADATA_INVALID')
        return value

    def put_json(self, relative: str, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
        self.put(relative, data, 'application/json; charset=utf-8')
