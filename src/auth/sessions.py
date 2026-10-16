from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ContextManager, Protocol


class AuthenticationError(RuntimeError):
    pass


def _bounded_int(value, *, default: int, minimum: int, maximum: int, code: str) -> int:
    raw=str(value or '').strip()
    if not raw:return int(default)
    try:number=int(raw)
    except ValueError as exc:raise AuthenticationError(code) from exc
    if number < minimum or number > maximum:raise AuthenticationError(code)
    return number


@dataclass(frozen=True)
class SessionPrincipal:
    # Keep the historical five positional fields stable; authenticated_at was added by
    # 10.3.12 and is deliberately optional for older test/runtime adapters.
    session_id: str
    user_id: str
    auth_method: str
    issued_at: float
    absolute_expires_at: float
    authenticated_at: float = 0.0


@dataclass
class _SessionRecord:
    session_id: str
    user_id: str
    auth_method: str
    authenticated_at: float
    issued_at: float
    last_seen_at: float
    absolute_expires_at: float
    idle_expires_at: float


class SessionService(Protocol):
    cookie_name: str
    cookie_secure: bool
    max_age_seconds: int
    requires_authentication: bool
    test_bootstrap_enabled: bool
    def current(self) -> SessionPrincipal | None: ...
    def bind(self, token: str | None) -> ContextManager[SessionPrincipal | None]: ...
    def issue(self, user_id: str, *, auth_method: str, external_subject: str | None = None, authenticated_at: float | None = None) -> tuple[str, SessionPrincipal]: ...
    def replace(self, token: str | None, user_id: str, *, auth_method: str, authenticated_at: float | None = None) -> tuple[str, SessionPrincipal]: ...
    def revoke(self, token: str | None) -> None: ...
    def revoke_session_id(self, user_id: str, session_id: str) -> bool: ...
    def revoke_user(self, user_id: str, *, except_session_id: str | None = None) -> int: ...
    def sessions_for_user(self, user_id: str) -> list[dict]: ...
    def active_session_ids(self) -> set[str]: ...
    def verify_test_secret(self, candidate: str) -> bool: ...
    def metadata(self) -> dict: ...


class NullSessionService:
    cookie_name='breadmaker_session'
    cookie_secure=False
    max_age_seconds=0
    requires_authentication=False
    test_bootstrap_enabled=False
    def current(self):return None
    @contextmanager
    def bind(self,token):yield None
    def issue(self,user_id,*,auth_method,external_subject=None,authenticated_at=None):raise AuthenticationError('SESSION_ISSUANCE_DISABLED')
    def replace(self,token,user_id,*,auth_method,authenticated_at=None):raise AuthenticationError('SESSION_ISSUANCE_DISABLED')
    def revoke(self,token):return None
    def revoke_session_id(self,user_id,session_id):return False
    def revoke_user(self,user_id,*,except_session_id=None):return 0
    def sessions_for_user(self,user_id):return []
    def active_session_ids(self):return set()
    def verify_test_secret(self,candidate):return False
    def metadata(self):return {'enabled':False,'auth_mode':'none','server_side':False}


class OpaqueSessionService:
    """Opaque server-side sessions shared by test auth and future external IdPs.

    The browser receives only a random bearer cookie. The server stores a SHA-256
    digest of that cookie, never the raw token. Authentication providers are expected
    to prove identity first, resolve/create the Breadmaker user, and then call replace()
    so any pre-authentication cookie is invalidated before the authenticated session is
    established.
    """
    cookie_name='breadmaker_session'
    requires_authentication=True

    def __init__(self, *, auth_mode: str, environment: str, test_secret: str = '', max_sessions_per_user: int | None = None, max_sessions_total: int | None = None):
        self.auth_mode=str(auth_mode or 'session')
        self.environment=str(environment or 'development')
        self.test_bootstrap_enabled=self.auth_mode=='test'
        self._test_secret=str(test_secret or '')
        self.absolute_seconds=max(300,int(os.environ.get('BREADMAKER_SESSION_ABSOLUTE_SECONDS') or 7*24*60*60))
        self.idle_seconds=max(60,min(self.absolute_seconds,int(os.environ.get('BREADMAKER_SESSION_IDLE_SECONDS') or 12*60*60)))
        self.max_age_seconds=self.absolute_seconds
        self.max_sessions_per_user=_bounded_int(
            max_sessions_per_user if max_sessions_per_user is not None else os.environ.get('BREADMAKER_MAX_SESSIONS_PER_USER'),
            default=128,minimum=8,maximum=10000,code='SESSION_PER_USER_CAPACITY_INVALID',
        )
        self.max_sessions_total=_bounded_int(
            max_sessions_total if max_sessions_total is not None else os.environ.get('BREADMAKER_MAX_SESSIONS_TOTAL'),
            default=20000,minimum=256,maximum=1000000,code='SESSION_TOTAL_CAPACITY_INVALID',
        )
        if self.max_sessions_total < self.max_sessions_per_user:
            raise AuthenticationError('SESSION_CAPACITY_INVALID')
        explicit_secure=str(os.environ.get('BREADMAKER_SESSION_COOKIE_SECURE') or '').strip().lower()
        if explicit_secure in {'1','true','yes'}:self.cookie_secure=True
        elif explicit_secure in {'0','false','no'}:self.cookie_secure=False
        else:self.cookie_secure=self.environment not in {'test','development','dev','local'}
        if self.environment=='production' and not self.cookie_secure:
            raise RuntimeError('SESSION_COOKIE_SECURE_REQUIRED')
        # Secure hosted deployments use the browser-enforced __Host- prefix: Secure,
        # path=/, and no Domain attribute. HTTP test/dev harnesses use a plain name.
        self.cookie_name='__Host-breadmaker_session' if self.cookie_secure else 'breadmaker_session'
        self._records:dict[str,_SessionRecord]={}
        self._lock=threading.RLock()
        self._current:ContextVar[SessionPrincipal|None]=ContextVar('breadmaker_session_principal',default=None)

    @staticmethod
    def _digest(token:str) -> str:
        return hashlib.sha256(str(token).encode('utf-8')).hexdigest()

    def _prune_locked(self,now:float) -> None:
        expired=[key for key,row in self._records.items() if now>=row.absolute_expires_at or now>=row.idle_expires_at]
        for key in expired:self._records.pop(key,None)

    @staticmethod
    def _principal(row:_SessionRecord) -> SessionPrincipal:
        return SessionPrincipal(row.session_id,row.user_id,row.auth_method,row.issued_at,row.absolute_expires_at,authenticated_at=row.authenticated_at)

    def current(self):return self._current.get()

    def _resolve(self,token:str|None) -> SessionPrincipal|None:
        value=str(token or '')
        if not value:return None
        now=time.time();key=self._digest(value)
        with self._lock:
            self._prune_locked(now);row=self._records.get(key)
            if row is None:return None
            row.last_seen_at=now;row.idle_expires_at=min(row.absolute_expires_at,now+self.idle_seconds)
            return self._principal(row)

    @contextmanager
    def bind(self,token):
        principal=self._resolve(token);handle=self._current.set(principal)
        try:yield principal
        finally:self._current.reset(handle)

    def issue(self,user_id,*,auth_method,external_subject=None,authenticated_at=None):
        uid=str(user_id or '').strip()
        if not uid:raise AuthenticationError('AUTH_USER_REQUIRED')
        method=str(auth_method or '').strip()[:80]
        if not method:raise AuthenticationError('AUTH_METHOD_REQUIRED')
        now=time.time();auth_time=float(authenticated_at if authenticated_at is not None else now)
        if auth_time<=0 or auth_time>now+60:raise AuthenticationError('AUTH_TIME_INVALID')
        token=secrets.token_urlsafe(32);session_id='ses_'+secrets.token_hex(16)
        row=_SessionRecord(session_id,uid,method,auth_time,now,now,now+self.absolute_seconds,min(now+self.absolute_seconds,now+self.idle_seconds))
        with self._lock:
            self._prune_locked(now)
            # A single account may not grow the in-memory bearer-session registry
            # without bound. Retire that user's oldest sessions first; never evict a
            # different user's session to make room for an attacker.
            owned=sorted(
                ((key,item) for key,item in self._records.items() if item.user_id==uid),
                key=lambda pair:(pair[1].last_seen_at,pair[1].issued_at,pair[1].session_id),
            )
            while len(owned) >= self.max_sessions_per_user:
                old_key,_old=owned.pop(0);self._records.pop(old_key,None)
            if len(self._records) >= self.max_sessions_total:
                raise AuthenticationError('SESSION_CAPACITY_EXCEEDED')
            self._records[self._digest(token)]=row
        return token,self._principal(row)

    def replace(self,token,user_id,*,auth_method,authenticated_at=None):
        """Rotate authentication state without trusting a pre-login cookie.

        Any supplied cookie is revoked first, even when it is stale or belongs to a
        different user. A fresh random token/session id is then issued from the trusted
        identity result supplied by the authentication adapter.
        """
        self.revoke(token)
        return self.issue(user_id,auth_method=auth_method,authenticated_at=authenticated_at)

    def revoke(self,token):
        value=str(token or '')
        if not value:return
        with self._lock:self._records.pop(self._digest(value),None)

    def revoke_session_id(self,user_id,session_id):
        uid=str(user_id or '').strip();sid=str(session_id or '').strip()
        if not uid or not sid:return False
        now=time.time()
        with self._lock:
            self._prune_locked(now)
            for key,row in list(self._records.items()):
                if row.user_id==uid and row.session_id==sid:
                    self._records.pop(key,None);return True
        return False

    def revoke_user(self,user_id,*,except_session_id=None):
        uid=str(user_id or '').strip();keep=str(except_session_id or '').strip()
        if not uid:return 0
        now=time.time();removed=0
        with self._lock:
            self._prune_locked(now)
            for key,row in list(self._records.items()):
                if row.user_id==uid and (not keep or row.session_id!=keep):
                    self._records.pop(key,None);removed+=1
        return removed

    def sessions_for_user(self,user_id):
        uid=str(user_id or '').strip()
        if not uid:return []
        now=time.time()
        with self._lock:
            self._prune_locked(now)
            rows=[row for row in self._records.values() if row.user_id==uid]
            rows.sort(key=lambda row:row.last_seen_at,reverse=True)
            return [{
                'session_id':row.session_id,
                'auth_method':row.auth_method,
                'authenticated_at':row.authenticated_at,
                'issued_at':row.issued_at,
                'last_seen_at':row.last_seen_at,
                'absolute_expires_at':row.absolute_expires_at,
                'idle_expires_at':row.idle_expires_at,
            } for row in rows]

    def active_session_ids(self):
        now=time.time()
        with self._lock:
            self._prune_locked(now)
            return {row.session_id for row in self._records.values()}

    def verify_test_secret(self,candidate):
        if not self.test_bootstrap_enabled:return False
        return bool(self._test_secret) and secrets.compare_digest(self._test_secret,str(candidate or ''))

    def metadata(self):
        return {
            'enabled':True,
            'auth_mode':self.auth_mode,
            'server_side':True,
            'opaque_cookie':True,
            'cookie_http_only':True,
            'cookie_secure':bool(self.cookie_secure),
            'cookie_same_site':'lax',
            'idle_seconds':self.idle_seconds,
            'absolute_seconds':self.absolute_seconds,
            'session_rotation':True,
            'user_revocation':True,
            'max_sessions_per_user':self.max_sessions_per_user,
            'max_sessions_total':self.max_sessions_total,
            'bounded_session_registry':True,
            'test_bootstrap':bool(self.test_bootstrap_enabled),
        }


def build_hosted_session_service(auth_mode:str) -> OpaqueSessionService:
    mode=str(auth_mode or 'session').strip().lower()
    environment=str(os.environ.get('BREADMAKER_ENV') or 'development').strip().lower()
    if mode not in {'test','session'}:raise RuntimeError(f'AUTH_MODE_UNSUPPORTED:{mode}')
    secret=str(os.environ.get('BREADMAKER_TEST_AUTH_SECRET') or '')
    if mode=='test':
        if environment=='production':raise RuntimeError('TEST_AUTH_FORBIDDEN_IN_PRODUCTION')
        if environment!='test':raise RuntimeError('TEST_AUTH_REQUIRES_TEST_ENVIRONMENT')
        if len(secret)<32:raise RuntimeError('TEST_AUTH_SECRET_TOO_SHORT')
    return OpaqueSessionService(auth_mode=mode,environment=environment,test_secret=secret)
