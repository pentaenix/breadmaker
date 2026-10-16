from __future__ import annotations

"""Provider-neutral server-side OpenID Connect primitives.

Checkpoint 3 deliberately exposes no HTTP login route. The engine is a dormant BFF
component used by deterministic protocol tests until a later checkpoint wires a real
hosted identity broker into hosted mode.

Security boundary:
- Authorization Code only.
- PKCE S256 on every transaction.
- state and nonce are generated server-side and single-use.
- tokens are exchanged and validated only on the server.
- Authlib/JOSERFC owns JOSE/JWT signature and OIDC claim validation.
- issuer/audience/azp/time/nonce claims are checked before identity is returned.
- no token value is returned in public metadata or included in error messages.
"""

import base64
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry


class OidcError(RuntimeError):
    """Stable, non-secret protocol failure suitable for tests/HTTP adapters."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class OidcProviderConfig:
    issuer: str
    client_id: str
    redirect_uri: str
    client_secret: str = field(default='', repr=False)
    scopes: tuple[str, ...] = ('openid', 'profile', 'email')
    token_endpoint_auth_method: str = 'client_secret_basic'
    allow_insecure_loopback: bool = False
    http_timeout_seconds: float = 5.0
    clock_skew_seconds: int = 60
    require_same_origin_endpoints: bool = False

    def __post_init__(self) -> None:
        issuer = _validated_url(self.issuer, 'OIDC_ISSUER_INVALID', allow_loopback=self.allow_insecure_loopback)
        redirect = _validated_url(self.redirect_uri, 'OIDC_REDIRECT_URI_INVALID', allow_loopback=self.allow_insecure_loopback)
        issuer_parts = urllib.parse.urlsplit(issuer)
        if issuer_parts.query or issuer_parts.fragment:
            raise OidcError('OIDC_ISSUER_INVALID')
        if not str(self.client_id or '').strip() or len(str(self.client_id)) > 1024:
            raise OidcError('OIDC_CLIENT_ID_INVALID')
        if self.token_endpoint_auth_method not in {'client_secret_basic', 'none'}:
            raise OidcError('OIDC_TOKEN_AUTH_METHOD_UNSUPPORTED')
        if self.token_endpoint_auth_method == 'client_secret_basic' and not self.client_secret:
            raise OidcError('OIDC_CLIENT_SECRET_REQUIRED')
        if not self.scopes or 'openid' not in self.scopes:
            raise OidcError('OIDC_OPENID_SCOPE_REQUIRED')
        if not (0.5 <= float(self.http_timeout_seconds) <= 30.0):
            raise OidcError('OIDC_HTTP_TIMEOUT_INVALID')
        if not (0 <= int(self.clock_skew_seconds) <= 300):
            raise OidcError('OIDC_CLOCK_SKEW_INVALID')
        object.__setattr__(self, 'issuer', issuer)
        object.__setattr__(self, 'redirect_uri', redirect)
        object.__setattr__(self, 'client_id', str(self.client_id).strip())
        object.__setattr__(self, 'client_secret', str(self.client_secret))
        object.__setattr__(self, 'scopes', tuple(dict.fromkeys(str(scope).strip() for scope in self.scopes if str(scope).strip())))
        object.__setattr__(self, 'require_same_origin_endpoints', bool(self.require_same_origin_endpoints))


@dataclass(frozen=True)
class OidcAuthorizationRequest:
    authorization_url: str
    state: str
    nonce: str
    code_challenge: str
    return_path: str
    expires_at: float


@dataclass(frozen=True)
class VerifiedOidcIdentity:
    issuer: str
    subject: str
    email: str
    email_verified: bool
    display_name: str
    authenticated_at: float | None


@dataclass(frozen=True)
class _OidcTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    return_path: str
    created_at: float
    expires_at: float


class OidcTransactionStore:
    """Single-process, single-use login transaction store.

    Hosted Breadmaker is still explicitly single-worker. A horizontally scaled hosted
    deployment must replace this owner with shared, atomic transaction storage before
    enabling login across workers.
    """

    def __init__(self, *, ttl_seconds: int = 600, max_active: int = 4096, now: Callable[[], float] = time.time):
        ttl = int(ttl_seconds)
        capacity = int(max_active)
        if ttl < 60 or ttl > 900:
            raise OidcError('OIDC_TRANSACTION_TTL_INVALID')
        if capacity < 16 or capacity > 65536:
            raise OidcError('OIDC_TRANSACTION_CAPACITY_INVALID')
        self.ttl_seconds = ttl
        self.max_active = capacity
        self._now = now
        self._items: dict[str, _OidcTransaction] = {}
        self._lock = threading.RLock()

    def begin(self, *, return_path: str = '/') -> _OidcTransaction:
        safe_return = _safe_return_path(return_path)
        now = float(self._now())
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        # token_urlsafe(64) is comfortably inside RFC 7636's 43..128 character range.
        verifier = secrets.token_urlsafe(64)
        if not 43 <= len(verifier) <= 128:
            raise OidcError('OIDC_PKCE_VERIFIER_INVALID')
        challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
        item = _OidcTransaction(state, nonce, verifier, challenge, safe_return, now, now + self.ttl_seconds)
        with self._lock:
            self._prune_locked(now)
            if len(self._items) >= self.max_active:
                raise OidcError('OIDC_TRANSACTION_CAPACITY_EXCEEDED')
            self._items[state] = item
        return item

    def consume(self, state: str) -> _OidcTransaction:
        value = str(state or '').strip()
        if not value:
            raise OidcError('OIDC_STATE_REQUIRED')
        if len(value) > 256 or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for ch in value):
            raise OidcError('OIDC_STATE_INVALID_OR_REPLAYED')
        now = float(self._now())
        with self._lock:
            item = self._items.pop(value, None)
            self._prune_locked(now)
        if item is None:
            raise OidcError('OIDC_STATE_INVALID_OR_REPLAYED')
        if now >= item.expires_at:
            raise OidcError('OIDC_STATE_EXPIRED')
        return item

    def discard(self, state: str) -> None:
        """Retire an abandoned transaction without treating cancellation as replay."""
        value = str(state or '').strip()
        if not value:
            return
        now = float(self._now())
        with self._lock:
            self._items.pop(value, None)
            self._prune_locked(now)

    def active_count(self) -> int:
        now = float(self._now())
        with self._lock:
            self._prune_locked(now)
            return len(self._items)

    def _prune_locked(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if now >= item.expires_at]
        for key in expired:
            self._items.pop(key, None)


class OidcClient:
    """Generic confidential/server OIDC client for the Breadmaker BFF boundary."""

    _MAX_JSON_BYTES = 1024 * 1024

    def __init__(
        self,
        config: OidcProviderConfig,
        *,
        transactions: OidcTransactionStore | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.config = config
        self.transactions = transactions or OidcTransactionStore(now=now)
        self._now = now
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._lock = threading.RLock()

    def metadata(self) -> dict[str, Any]:
        return {
            'issuer': self.config.issuer,
            'flow': 'authorization_code',
            'pkce': 'S256',
            'state': 'single_use_server_side',
            'nonce_required': True,
            'id_token_algorithms': ['RS256'],
            'jwt_validation_library': 'Authlib/JOSERFC',
            'browser_token_exposure': False,
            'same_origin_provider_endpoints': bool(self.config.require_same_origin_endpoints),
        }

    def begin_authorization(
        self,
        *,
        return_path: str = '/',
        force_reauthentication: bool = False,
        extra_authorization_params: dict[str, str] | None = None,
    ) -> OidcAuthorizationRequest:
        tx = self.transactions.begin(return_path=return_path)
        discovery = self._get_discovery()
        endpoint = str(discovery['authorization_endpoint'])
        params = {
            'response_type': 'code',
            'client_id': self.config.client_id,
            'redirect_uri': self.config.redirect_uri,
            'scope': ' '.join(self.config.scopes),
            'state': tx.state,
            'nonce': tx.nonce,
            'code_challenge': tx.code_challenge,
            'code_challenge_method': 'S256',
        }
        if force_reauthentication:
            # Account linking and security step-up flows must not accept the broker's
            # existing SSO session as proof of a second/current identity. ``max_age=0``
            # also requires an ``auth_time`` claim so the caller can verify freshness.
            params['prompt'] = 'login'
            params['max_age'] = '0'
        if extra_authorization_params:
            reserved = {
                'response_type','client_id','redirect_uri','scope','state','nonce',
                'code_challenge','code_challenge_method','prompt','max_age',
            }
            for raw_key, raw_value in extra_authorization_params.items():
                key = str(raw_key or '').strip()
                value = str(raw_value or '').strip()
                if key in reserved:
                    raise OidcError('OIDC_AUTHORIZATION_PARAMETER_RESERVED')
                if (
                    not key or len(key) > 128
                    or any(ord(ch) < 0x21 or ord(ch) > 0x7e for ch in key)
                    or len(value) > 1024 or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value)
                ):
                    raise OidcError('OIDC_AUTHORIZATION_PARAMETER_INVALID')
                params[key] = value
        query = urllib.parse.urlencode(params)
        separator = '&' if urllib.parse.urlsplit(endpoint).query else '?'
        return OidcAuthorizationRequest(
            authorization_url=f'{endpoint}{separator}{query}',
            state=tx.state,
            nonce=tx.nonce,
            code_challenge=tx.code_challenge,
            return_path=tx.return_path,
            expires_at=tx.expires_at,
        )

    def complete_authorization(self, *, code: str, state: str) -> tuple[VerifiedOidcIdentity, str]:
        # Consume state before any network operation: every callback attempt is one-shot,
        # including attempts with a bad/expired authorization code.
        tx = self.transactions.consume(state)
        authorization_code = str(code or '').strip()
        if not authorization_code or len(authorization_code) > 4096:
            raise OidcError('OIDC_AUTHORIZATION_CODE_INVALID')
        discovery = self._get_discovery()
        token_response = self._exchange_code(discovery, authorization_code, tx)
        identity = self._verify_id_token(token_response, nonce=tx.nonce)
        return identity, tx.return_path

    def _get_discovery(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._discovery is not None and not refresh:
                return dict(self._discovery)
        issuer_base = self.config.issuer.rstrip('/')
        url = f'{issuer_base}/.well-known/openid-configuration'
        payload = self._request_json(url, method='GET')
        if str(payload.get('issuer') or '') != self.config.issuer:
            raise OidcError('OIDC_DISCOVERY_ISSUER_MISMATCH')
        for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            endpoint = payload.get(key)
            if not isinstance(endpoint, str) or not endpoint:
                raise OidcError(f'OIDC_DISCOVERY_{key.upper()}_MISSING')
            validated = _validated_url(endpoint, 'OIDC_DISCOVERY_ENDPOINT_INVALID', allow_loopback=self.config.allow_insecure_loopback)
            if self.config.require_same_origin_endpoints and not _same_origin(self.config.issuer, validated):
                # Breadmaker's provider boundary is intentionally one-origin. Do not send
                # authorization codes, PKCE verifiers, or the confidential client secret
                # to an authority supplied by a cross-origin discovery document.
                raise OidcError('OIDC_DISCOVERY_ENDPOINT_ORIGIN_MISMATCH')
        response_types = payload.get('response_types_supported')
        if isinstance(response_types, list) and 'code' not in response_types:
            raise OidcError('OIDC_AUTHORIZATION_CODE_NOT_SUPPORTED')
        algs = payload.get('id_token_signing_alg_values_supported')
        if isinstance(algs, list) and 'RS256' not in algs:
            raise OidcError('OIDC_RS256_NOT_SUPPORTED')
        auth_methods = payload.get('token_endpoint_auth_methods_supported')
        if isinstance(auth_methods, list) and self.config.token_endpoint_auth_method not in auth_methods:
            raise OidcError('OIDC_TOKEN_AUTH_METHOD_NOT_SUPPORTED')
        pkce_methods = payload.get('code_challenge_methods_supported')
        if isinstance(pkce_methods, list) and 'S256' not in pkce_methods:
            raise OidcError('OIDC_PKCE_S256_NOT_SUPPORTED')
        with self._lock:
            self._discovery = dict(payload)
        return dict(payload)

    def _exchange_code(self, discovery: dict[str, Any], code: str, tx: _OidcTransaction) -> dict[str, Any]:
        body = urllib.parse.urlencode({
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.config.redirect_uri,
            'client_id': self.config.client_id,
            'code_verifier': tx.code_verifier,
        }).encode('ascii')
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
        if self.config.token_endpoint_auth_method == 'client_secret_basic':
            client_id = urllib.parse.quote(self.config.client_id, safe='')
            client_secret = urllib.parse.quote(self.config.client_secret, safe='')
            raw = f'{client_id}:{client_secret}'.encode('utf-8')
            headers['Authorization'] = 'Basic ' + base64.b64encode(raw).decode('ascii')
        payload = self._request_json(str(discovery['token_endpoint']), method='POST', data=body, headers=headers)
        if payload.get('error'):
            raise OidcError('OIDC_TOKEN_EXCHANGE_REJECTED')
        if not isinstance(payload.get('id_token'), str) or not payload.get('id_token'):
            raise OidcError('OIDC_ID_TOKEN_MISSING')
        return payload

    def _verify_id_token(self, token_response: dict[str, Any], *, nonce: str) -> VerifiedOidcIdentity:
        id_token = str(token_response.get('id_token') or '')
        if len(id_token) > 128 * 1024:
            raise OidcError('OIDC_JWT_INVALID')
        claims_params: dict[str, Any] = {'nonce': nonce, 'client_id': self.config.client_id}
        access_token = token_response.get('access_token')
        if isinstance(access_token, str) and access_token:
            claims_params['access_token'] = access_token
        claims_options = {'iss': {'values': [self.config.issuer]}}
        try:
            decoded = self._decode_authlib_id_token(id_token, refresh_keys=False)
        except InvalidKeyIdError:
            try:
                decoded = self._decode_authlib_id_token(id_token, refresh_keys=True)
            except Exception as exc:
                raise OidcError('OIDC_SIGNING_KEY_NOT_FOUND') from exc
        except Exception as exc:
            raise OidcError('OIDC_ID_TOKEN_SIGNATURE_INVALID') from exc
        try:
            claims = CodeIDToken(decoded.claims, decoded.header, claims_options, claims_params)
        except Exception as exc:
            raise OidcError('OIDC_ID_TOKEN_CLAIMS_INVALID') from exc

        # Authlib/JOSERFC has already verified the JOSE signature. The explicit checks
        # below provide Breadmaker's stable error taxonomy; Authlib then performs the
        # normative OIDC claim validation as a second validation layer.
        now = float(self._now())
        skew = int(self.config.clock_skew_seconds)
        if str(claims.get('iss') or '') != self.config.issuer:
            raise OidcError('OIDC_ID_TOKEN_ISSUER_MISMATCH')
        subject = str(claims.get('sub') or '').strip()
        if not subject or len(subject) > 1024:
            raise OidcError('OIDC_ID_TOKEN_SUBJECT_INVALID')
        audiences = _audiences(claims.get('aud'))
        if self.config.client_id not in audiences:
            raise OidcError('OIDC_ID_TOKEN_AUDIENCE_MISMATCH')
        azp = claims.get('azp')
        if len(audiences) > 1 and str(azp or '') != self.config.client_id:
            raise OidcError('OIDC_ID_TOKEN_AZP_REQUIRED')
        if azp is not None and str(azp) != self.config.client_id:
            raise OidcError('OIDC_ID_TOKEN_AZP_MISMATCH')
        if str(claims.get('nonce') or '') != nonce:
            raise OidcError('OIDC_ID_TOKEN_NONCE_MISMATCH')
        exp = _finite_numeric_claim(claims, 'exp', required=True)
        iat = _finite_numeric_claim(claims, 'iat', required=True)
        nbf = _finite_numeric_claim(claims, 'nbf', required=False)
        if exp is None or now - skew >= exp:
            raise OidcError('OIDC_ID_TOKEN_EXPIRED')
        if iat is None or iat <= 0 or iat > now + skew:
            raise OidcError('OIDC_ID_TOKEN_IAT_INVALID')
        if exp <= iat - skew:
            raise OidcError('OIDC_ID_TOKEN_TIME_RANGE_INVALID')
        if nbf is not None and now + skew < nbf:
            raise OidcError('OIDC_ID_TOKEN_NOT_YET_VALID')
        auth_time = _finite_numeric_claim(claims, 'auth_time', required=False)
        if auth_time is not None and (auth_time <= 0 or auth_time > now + skew):
            raise OidcError('OIDC_ID_TOKEN_AUTH_TIME_INVALID')
        try:
            claims.validate(leeway=skew)
        except Exception as exc:
            raise OidcError('OIDC_ID_TOKEN_CLAIMS_INVALID') from exc
        email = claims.get('email') if isinstance(claims.get('email'), str) else ''
        email_verified = claims.get('email_verified') is True
        display_name = claims.get('name') if isinstance(claims.get('name'), str) else ''
        return VerifiedOidcIdentity(
            issuer=self.config.issuer,
            subject=subject,
            email=email[:320],
            email_verified=email_verified,
            display_name=display_name.strip()[:160],
            authenticated_at=auth_time,
        )

    def _decode_authlib_id_token(self, id_token: str, *, refresh_keys: bool):
        key_set = KeySet.import_key_set(self._get_jwks(refresh=refresh_keys))
        # Only RS256 is accepted in Checkpoint 3 even if a provider advertises more.
        registry = JWSRegistry(algorithms=['RS256'], strict_check_header=False)
        return jwt.decode(id_token, key=key_set, registry=registry)

    def _get_jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._jwks is not None and not refresh:
                return dict(self._jwks)
        discovery = self._get_discovery()
        payload = self._request_json(str(discovery['jwks_uri']), method='GET')
        keys = payload.get('keys')
        if not isinstance(keys, list) or not any(isinstance(item, dict) and item.get('kid') for item in keys):
            raise OidcError('OIDC_JWKS_INVALID')
        with self._lock:
            self._jwks = dict(payload)
        return dict(payload)

    def _request_json(self, url: str, *, method: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        _validated_url(url, 'OIDC_HTTP_ENDPOINT_INVALID', allow_loopback=self.config.allow_insecure_loopback)
        request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with self._opener.open(request, timeout=float(self.config.http_timeout_seconds)) as response:
                raw = response.read(self._MAX_JSON_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # Do not include remote bodies: they can contain reflected authorization
            # codes/tokens or provider diagnostics that do not belong in app logs.
            raise OidcError('OIDC_HTTP_REJECTED') from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OidcError('OIDC_HTTP_UNAVAILABLE') from exc
        if len(raw) > self._MAX_JSON_BYTES:
            raise OidcError('OIDC_HTTP_RESPONSE_TOO_LARGE')
        try:
            payload = json.loads(raw.decode('utf-8'), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError('non-finite JSON')))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise OidcError('OIDC_HTTP_JSON_INVALID') from exc
        if not isinstance(payload, dict):
            raise OidcError('OIDC_HTTP_JSON_INVALID')
        return payload


def _decoded_url_variants(value: str, *, rounds: int = 4) -> tuple[str, ...]:
    """Return bounded repeated percent-decoding variants for parser-confusion checks."""
    current = str(value)
    variants = [current]
    for _ in range(max(1, min(int(rounds), 8))):
        decoded = urllib.parse.unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def _safe_return_path(value: str) -> str:
    path = str(value or '/').strip() or '/'
    parsed = urllib.parse.urlsplit(path)
    variants = _decoded_url_variants(path)
    if (
        not path.startswith('/')
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or len(path) > 2048
        or any(
            variant.startswith('//')
            or '\\' in variant
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in variant)
            for variant in variants
        )
    ):
        raise OidcError('OIDC_RETURN_PATH_INVALID')
    return path



def _same_origin(left: str, right: str) -> bool:
    def origin(value: str) -> tuple[str, str, int | None]:
        parsed = urllib.parse.urlsplit(str(value or ''))
        scheme = str(parsed.scheme or '').lower()
        host = str(parsed.hostname or '').lower()
        try:
            port = parsed.port
        except ValueError:
            return '', '', None
        if port is None:
            port = 443 if scheme == 'https' else 80 if scheme == 'http' else None
        return scheme, host, port
    return origin(left) == origin(right)

def _validated_url(value: str, code: str, *, allow_loopback: bool) -> str:
    text = str(value or '').strip()
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise OidcError(code) from exc
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise OidcError(code)
    if parsed.scheme == 'https':
        return text
    loopback = (parsed.hostname or '').lower() in {'127.0.0.1', 'localhost', '::1'}
    if parsed.scheme == 'http' and allow_loopback and loopback:
        return text
    raise OidcError(code)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _audiences(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return list(value)
    raise OidcError('OIDC_ID_TOKEN_AUDIENCE_INVALID')


def _finite_numeric_claim(claims: Any, name: str, *, required: bool) -> float | None:
    value = claims.get(name)
    if value is None:
        if required:
            raise OidcError(f'OIDC_ID_TOKEN_{name.upper()}_REQUIRED')
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OidcError(f'OIDC_ID_TOKEN_{name.upper()}_INVALID')
    number = float(value)
    if not math.isfinite(number):
        raise OidcError(f'OIDC_ID_TOKEN_{name.upper()}_INVALID')
    return number
