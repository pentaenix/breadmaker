"""App-to-private-helper boundary. No credentials/prose in errors or persistence.

Fixed HTTPS run.app audience, metadata identity only, no redirects/proxies or
automatic retries. Model inference stays on the device. Not runtime activated.
"""
import json
import math
import re
import time
from urllib.parse import urlsplit

from narration.pronunciation import PronunciationError, validate_input

MAX_RESULT = 2 * 1024**2
MAX_BODY = 256 * 1024
METADATA_IDENTITY = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity'


def _bounded(response, limit, deadline):
    raw, size = [], 0
    for part in response.iter_content(chunk_size=16 * 1024):
        size += len(part)
        if time.monotonic() >= deadline or size > limit:
            raise PronunciationError('NARRATION_PRONUNCIATION_RESPONSE_INVALID')
        raw.append(part)
    return b''.join(raw)


def _session():
    import requests
    session = requests.Session()
    session.trust_env = False  # never forward private text/identity through env proxies
    return session  # requests' default HTTPAdapter does NOT retry


class PrivatePronunciationClient:
    def __init__(self, url, *, session_factory=_session):
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,110}\.(?:[a-z0-9-]+\.)?run\.app', parts.netloc)
                or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError('NARRATION_HELPER_URL_INVALID')
        self.url = url.rstrip('/')
        self.session_factory = session_factory

    def convert(self, language, text, words):
        validate_input(language, text, words)
        body = json.dumps(dict(language=language, text=text, words=words), ensure_ascii=False,
                          allow_nan=False, separators=(',', ':')).encode()
        if len(body) > MAX_BODY:
            raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')
        deadline = time.monotonic() + 38
        try:
            with self.session_factory() as session:
                with session.get(METADATA_IDENTITY, params={'audience': self.url, 'format': 'full'},
                        headers={'Metadata-Flavor': 'Google'}, timeout=(1, 2), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200 or response.headers.get('Metadata-Flavor') != 'Google':
                        raise ValueError()
                    token = _bounded(response, 16 * 1024, deadline).decode('ascii')
                    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
                        raise ValueError()
                with session.post(self.url + '/pronounce', data=body, headers={
                        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                        timeout=(2, 32), allow_redirects=False, stream=True) as response:
                    if response.status_code == 429:
                        raise PronunciationError('NARRATION_PRONUNCIATION_BUSY')
                    if response.status_code != 200:
                        raise ValueError()
                    result = json.loads(_bounded(response, MAX_RESULT, deadline))
                    _result(result, language)
                    return result
        except PronunciationError:
            raise
        except Exception:
            # Never forward requests exception bodies, URLs with tokens, or raw
            # dictionary errors. No fallback to English or unbounded retry.
            raise PronunciationError('NARRATION_PRONUNCIATION_UNAVAILABLE') from None


def _result(value, language):
    field = 'phonemes' if language == 'it' else 'items'
    if not isinstance(value, dict) or set(value) != {field} or not isinstance(value[field], list) or len(value[field]) > 20_000:
        raise ValueError()
    for item in value[field]:
        if language == 'it':
            if not isinstance(item, str) or not 1 <= len(item) < 350:
                raise ValueError()
        elif (not isinstance(item, dict) or set(item) != {'type', 'value', 'subtitles'} or item['type'] != 'phonetic'
                or any(not isinstance(item[key], str) or len(item[key]) > 30_000 for key in ('value', 'subtitles'))):
            raise ValueError()


class HostedPronunciation:
    def __init__(self, budget, client, *, monotonic=time.monotonic):
        self.budget, self.client, self.monotonic = budget, client, monotonic

    def convert(self, uid, pid, data):
        if not isinstance(data, dict) or set(data) - {'language', 'text', 'words'}:
            raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')
        language, text, words = data.get('language'), data.get('text', ''), data.get('words', [])
        validate_input(language, text, words)
        permit = self.budget.reserve(uid, pid, characters=sum(map(len, words)) if language == 'it' else len(text))
        elapsed = None
        start = self.monotonic()
        try:
            result = self.client.convert(language, text, words)
            duration = self.monotonic() - start
            if not math.isfinite(duration) or not 0 <= duration < 40:
                raise PronunciationError('NARRATION_PRONUNCIATION_TIMEOUT')
            # Revocation/admission must still pass AFTER helper I/O. Authority
            # failure is conservatively charged, and never returns private data.
            def check(tx):
                if self.budget.authorize(tx, uid, pid, 'read') is not True:
                    raise PronunciationError('NARRATION_ACCESS_DENIED')
            self.budget.documents.run_transaction(check)
            elapsed = math.ceil(duration * 1000)
            return result
        finally:
            self.budget.finish(permit, confirmed_elapsed_ms=elapsed)
