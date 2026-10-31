"""Google-signed machine identity ONLY; never a Firebase/user/session verifier.

Public signing certificates may live briefly in RAM. Tokens and private data may
not. The fixed certificate URL is not selected by a JWT header or caller input.
"""
import json
import re
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

CERTIFICATES = 'https://www.googleapis.com/oauth2/v1/certs'
PATH = '/_internal/narration/collect'
ISSUER = 'https://accounts.google.com'


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.netloc != parsed.hostname
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('NARRATION_GC_ORIGIN_INVALID')
    return value.rstrip('/')


class MachineIdentity:
    def __init__(self, *, audience, email, subject, now=time.time, session_factory=None):
        if (not audience.endswith(PATH) or audience != origin(audience[:-len(PATH)]) + PATH
                or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com', email)
                or not re.fullmatch(r'[0-9]{21}', subject)):
            raise ValueError('NARRATION_GC_IDENTITY_CONFIGURATION_INVALID')
        self.audience, self.email, self.subject, self.now = audience, email, subject, now
        self.session_factory = session_factory
        self.certificates, self.until = None, 0

    def _request(self, url, method='GET', **kwargs):
        if url != CERTIFICATES or method != 'GET':
            raise ValueError('NARRATION_GC_CERTIFICATE_ORIGIN_INVALID')
        if self.certificates is None or self.now() >= self.until:
            from hosted.narration.pronunciation_client import _session
            with (self.session_factory or _session)() as session:
                with session.get(CERTIFICATES, timeout=(1, 3), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise ValueError('NARRATION_GC_CERTIFICATES_UNAVAILABLE')
                    chunks, size, deadline = [], 0, time.monotonic() + 3
                    # At most 64 KiB; one-byte reads prevent a dribbling peer
                    # hiding indefinitely inside requests' buffered chunk read.
                    for part in response.iter_content(chunk_size=1):
                        size += len(part)
                        if size > 64 * 1024 or time.monotonic() >= deadline:
                            raise ValueError('NARRATION_GC_CERTIFICATES_UNAVAILABLE')
                        chunks.append(part)
                    payload = b''.join(chunks)
                    certs = json.loads(payload)
                    if not isinstance(certs, dict) or not 1 <= len(certs) <= 16 or any(
                            not isinstance(key, str) or not isinstance(value, str) for key, value in certs.items()):
                        raise ValueError('NARRATION_GC_CERTIFICATES_UNAVAILABLE')
                    age = re.search(r'(?:^|,)\s*max-age=([0-9]+)(?:,|$)', response.headers.get('Cache-Control', ''))
                    self.certificates = payload
                    self.until = self.now() + min(int(age[1]) if age else 0, 3600)
        return SimpleNamespace(status=200, data=self.certificates)

    def verify(self, token):
        from google.auth import jwt
        from google.oauth2.id_token import verify_oauth2_token
        if not isinstance(token, str) or len(token) > 8192 or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
            raise ValueError('NARRATION_GC_IDENTITY_REQUIRED')
        if jwt.decode_header(token).get('alg') != 'RS256':
            raise ValueError('NARRATION_GC_IDENTITY_REQUIRED')
        claims = verify_oauth2_token(token, self._request, audience=self.audience, clock_skew_in_seconds=0)
        now = self.now()
        if (claims.get('iss') != ISSUER or claims.get('aud') != self.audience
                or claims.get('email') != self.email or claims.get('email_verified') is not True
                or claims.get('sub') != self.subject
                or type(claims.get('iat')) is not int or type(claims.get('exp')) is not int
                or not claims['iat'] <= now < claims['exp'] or not 0 < claims['exp'] - claims['iat'] <= 3700):
            raise ValueError('NARRATION_GC_IDENTITY_REQUIRED')
        return True
