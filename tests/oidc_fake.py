from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _jwk(private_key, kid: str) -> dict[str, str]:
    numbers = private_key.public_key().public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')
    e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big')
    return {'kty': 'RSA', 'use': 'sig', 'alg': 'RS256', 'kid': kid, 'n': _b64url(n), 'e': _b64url(e)}


def _jwt(private_key, kid: str, claims: dict[str, Any]) -> str:
    header = {'alg': 'RS256', 'kid': kid, 'typ': 'JWT'}
    head = _b64url(json.dumps(header, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    body = _b64url(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    signing_input = f'{head}.{body}'.encode('ascii')
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f'{head}.{body}.{_b64url(signature)}'


@dataclass
class _Code:
    challenge: str
    nonce: str
    claims: dict[str, Any]
    kid: str
    signing_kid: str
    token_override: str | None


class FakeOidcIssuer:
    """Deterministic loopback OIDC issuer for protocol tests only."""

    def __init__(self, *, client_id: str = 'breadmaker-test-client', client_secret: str = 'test-secret'):
        self.client_id = client_id
        self.client_secret = client_secret
        self._lock = threading.RLock()
        self._keys = {
            'key-a': rsa.generate_private_key(public_exponent=65537, key_size=2048),
            'key-b': rsa.generate_private_key(public_exponent=65537, key_size=2048),
        }
        self.current_kid = 'key-a'
        self.exposed_kids = {'key-a'}
        self.codes: dict[str, _Code] = {}
        self.token_requests = 0
        self.jwks_requests = 0
        self.discovery_requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _json(self, status: int, payload: dict[str, Any]):
                raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path == '/.well-known/openid-configuration':
                    owner.discovery_requests += 1
                    self._json(200, {
                        'issuer': owner.issuer,
                        'authorization_endpoint': owner.issuer.rstrip('/') + '/authorize',
                        'token_endpoint': owner.issuer.rstrip('/') + '/token',
                        'jwks_uri': owner.issuer.rstrip('/') + '/jwks',
                        'response_types_supported': ['code'],
                        'id_token_signing_alg_values_supported': ['RS256'],
                        'code_challenge_methods_supported': ['S256'],
                        'token_endpoint_auth_methods_supported': ['client_secret_basic'],
                    })
                    return
                if parsed.path == '/jwks':
                    owner.jwks_requests += 1
                    with owner._lock:
                        keys = [_jwk(owner._keys[kid], kid) for kid in sorted(owner.exposed_kids)]
                    self._json(200, {'keys': keys})
                    return
                self._json(404, {'error': 'not_found'})

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != '/token':
                    self._json(404, {'error': 'not_found'})
                    return
                owner.token_requests += 1
                auth = str(self.headers.get('Authorization') or '')
                expected = 'Basic ' + base64.b64encode(
                    f'{urllib.parse.quote(owner.client_id, safe="")}:{urllib.parse.quote(owner.client_secret, safe="")}'.encode('utf-8')
                ).decode('ascii')
                if auth != expected:
                    self._json(401, {'error': 'invalid_client'})
                    return
                try:
                    length = min(64 * 1024, int(self.headers.get('Content-Length') or '0'))
                except ValueError:
                    length = 0
                form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
                code = (form.get('code') or [''])[0]
                verifier = (form.get('code_verifier') or [''])[0]
                grant = (form.get('grant_type') or [''])[0]
                redirect_uri = (form.get('redirect_uri') or [''])[0]
                client_id = (form.get('client_id') or [''])[0]
                with owner._lock:
                    record = owner.codes.pop(code, None)
                if not record or grant != 'authorization_code' or client_id != owner.client_id or redirect_uri != owner.redirect_uri:
                    self._json(400, {'error': 'invalid_grant'})
                    return
                challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest()) if verifier else ''
                if challenge != record.challenge:
                    self._json(400, {'error': 'invalid_grant'})
                    return
                now = int(time.time())
                claims = {
                    'iss': owner.issuer,
                    'sub': 'subject-123',
                    'aud': owner.client_id,
                    'iat': now,
                    'exp': now + 300,
                    'nonce': record.nonce,
                    'auth_time': now - 5,
                    'name': 'OIDC Test Writer',
                    'email': 'writer@example.test',
                    'email_verified': True,
                }
                claims.update(record.claims)
                token = record.token_override
                if token is None:
                    token = _jwt(owner._keys[record.signing_kid], record.kid, claims)
                self._json(200, {'token_type': 'Bearer', 'access_token': 'fake-access-token', 'expires_in': 300, 'id_token': token})

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        host, port = self._server.server_address
        self.issuer = f'http://{host}:{port}/'
        self.redirect_uri = self.issuer.rstrip('/') + '/auth/callback'

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def issue_code(
        self,
        request,
        *,
        claims: dict[str, Any] | None = None,
        kid: str | None = None,
        signing_kid: str | None = None,
        token_override: str | None = None,
    ) -> str:
        code = 'code-' + _b64url(hashlib.sha256(f'{request.state}:{time.time_ns()}'.encode()).digest())[:20]
        header_kid = kid or self.current_kid
        signer = signing_kid or header_kid
        record = _Code(
            request.code_challenge,
            request.nonce,
            dict(claims or {}),
            header_kid,
            signer,
            token_override,
        )
        with self._lock:
            self.codes[code] = record
        return code

    def rotate_to_second_key(self) -> None:
        with self._lock:
            self.current_kid = 'key-b'
            self.exposed_kids = {'key-a', 'key-b'}
