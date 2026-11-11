"""Public versioned legal copy only. No identity, project, cookie or credential I/O.

The only upstream targets are the two owner-published signup notices. Neither a
request URL nor its headers can choose the destination or enter the upstream call.
"""
from __future__ import annotations

from html.parser import HTMLParser
from threading import Lock
import re
import time

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

PATH = '/api/auth/signup-notice'
VERSION = '9'
ORIGIN = 'https://breadmaker.studio'
MAX_BYTES = 48 * 1024
MAX_SECONDS = 5
CACHE_SECONDS = 300
RETRY_SECONDS = 15


class NoticeUnavailable(ValueError):
    def __init__(self):
        super().__init__('SIGNUP_NOTICE_UNAVAILABLE')


def links(locale):
    suffix = 'es/' if locale == 'es' else ''
    return {'mailto:privacy@breadmaker.studio', ORIGIN + '/privacy/' + suffix,
            ORIGIN + '/age-requirements/' + suffix}


class _NoticeParser(HTMLParser):
    """Parse the published v9 section, emitting text/inline tokens, never HTML."""
    def __init__(self, locale):
        super().__init__(convert_charrefs=True)
        self.locale, self.sections, self.stack = locale, 0, []
        self.title, self.paragraphs, self.seen_links = '', [], set()
        self.strong_count = 0
        self.complete = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if not self.stack:
            if tag != 'section':
                return  # The static document wrapper is not rendered in the App.
            self.sections += 1
            if self.sections != 1 or attrs_dict != {
                    'lang': self.locale, 'aria-labelledby': 'signup-privacy-title',
                    'data-notice-version': VERSION} or len(attrs_dict) != len(attrs):
                raise NoticeUnavailable()
            self.stack.append(tag)
            return
        if len(attrs_dict) != len(attrs):
            raise NoticeUnavailable()
        parent = self.stack[-1]
        if tag == 'h2' and parent == 'section' and not self.title and not self.paragraphs:
            if attrs_dict != {'id': 'signup-privacy-title'}:
                raise NoticeUnavailable()
        elif tag == 'p' and parent == 'section' and self.title.strip() and len(self.paragraphs) < 4:
            if attrs:
                raise NoticeUnavailable()
            self.paragraphs.append([])
        elif tag in {'a', 'strong'} and parent == 'p':
            if tag == 'a':
                if set(attrs_dict) != {'href'} or attrs_dict['href'] not in links(self.locale):
                    raise NoticeUnavailable()
                self.seen_links.add(attrs_dict['href'])
                token = {'tag': tag, 'href': attrs_dict['href'], 'text': ''}
            else:
                if attrs or len(self.paragraphs) != 1:
                    raise NoticeUnavailable()
                self.strong_count += 1
                token = {'tag': tag, 'text': ''}
            self.paragraphs[-1].append(token)
        else:
            raise NoticeUnavailable()
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if self.stack or tag == 'section':
            raise NoticeUnavailable()

    def handle_endtag(self, tag):
        if not self.stack:
            if tag == 'section':
                raise NoticeUnavailable()
            return
        if tag != self.stack[-1]:
            raise NoticeUnavailable()
        self.stack.pop()
        if tag == 'section':
            self.complete = True

    def handle_data(self, data):
        if not self.stack:
            return
        parent = self.stack[-1]
        if parent == 'h2':
            self.title += data
        elif parent == 'p':
            self.paragraphs[-1].append(data)
        elif parent in {'a', 'strong'}:
            self.paragraphs[-1][-1]['text'] += data
        elif data.strip():
            raise NoticeUnavailable()

    def handle_comment(self, data):
        if self.stack:
            raise NoticeUnavailable()

    def payload(self):
        if (self.stack or not self.complete or self.sections != 1 or not self.title.strip()
                or len(self.paragraphs) != 4 or self.strong_count != 1
                or self.seen_links != links(self.locale)):
            raise NoticeUnavailable()
        for paragraph in self.paragraphs:
            if not paragraph or any(isinstance(part, dict) and not part['text'].strip() for part in paragraph):
                raise NoticeUnavailable()
            if not ''.join(part if isinstance(part, str) else part['text'] for part in paragraph).strip():
                raise NoticeUnavailable()
        return {'version': VERSION, 'locale': self.locale, 'title': self.title.strip(),
                'paragraphs': self.paragraphs}


def parse_notice(raw, locale):
    if locale not in {'en', 'es'} or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise NoticeUnavailable()
    try:
        text = raw.decode('utf-8', errors='strict')
        if re.search(r'\{\{|\}\}|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', text):
            raise NoticeUnavailable()
        parser = _NoticeParser(locale)
        parser.feed(text)
        parser.close()
        payload = parser.payload()
        # Reject decoded template markers too, including entity-encoded forms.
        if '{{' in str(payload) or '}}' in str(payload):
            raise NoticeUnavailable()
        return payload
    except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
        raise NoticeUnavailable() from None


def _session():
    import requests
    session = requests.Session()
    session.trust_env = False  # No netrc credentials, environment proxies or retries.
    return session


def fetch_notice(locale, *, session_factory=_session, now=time.monotonic):
    if locale not in {'en', 'es'}:
        raise NoticeUnavailable()
    deadline = now() + MAX_SECONDS
    try:
        with session_factory() as session:
            with session.get(ORIGIN + '/privacy/signup/' + locale + '/',
                    headers={'Accept': 'text/html', 'Accept-Encoding': 'identity'},
                    timeout=(1, 2), allow_redirects=False, stream=True) as response:
                if (response.status_code != 200 or now() >= deadline
                        or response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'text/html'
                        or response.headers.get('Content-Encoding', 'identity').lower() != 'identity'):
                    raise NoticeUnavailable()
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                    raise NoticeUnavailable()
                data = bytearray()
                # One-byte reads expose a dribbling response to the total deadline;
                # each network read separately has a two-second timeout.
                for part in response.iter_content(chunk_size=1):
                    data.extend(part)
                    if len(data) > MAX_BYTES or now() >= deadline:
                        raise NoticeUnavailable()
                return parse_notice(bytes(data), locale)
    except Exception:
        # Never propagate a response, request headers, address or raw exception.
        raise NoticeUnavailable() from None


class NoticeCache:
    def __init__(self, *, fetch=fetch_notice, now=time.monotonic):
        self.fetch, self.now = fetch, now
        self.values = {}
        self.locks = {locale: Lock() for locale in ('en', 'es')}

    def get(self, locale):
        if locale not in self.locks:
            raise NoticeUnavailable()
        value, until = self.values.get(locale, (None, 0))
        if self.now() < until:
            if value is None:
                raise NoticeUnavailable()
            return value
        # Never queue anonymous callers behind network I/O. At most one fetch per
        # locale is active, and a failed publication has a bounded retry interval.
        lock = self.locks[locale]
        if not lock.acquire(blocking=False):
            raise NoticeUnavailable()
        try:
            try:
                value = self.fetch(locale)
            except NoticeUnavailable:
                self.values[locale] = (None, self.now() + RETRY_SECONDS)
                raise
            self.values[locale] = (value, self.now() + CACHE_SECONDS)
            return value
        finally:
            lock.release()


_cache = NoticeCache()


async def dispatch(request):
    headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
               'X-Content-Type-Options': 'nosniff'}
    # Exact wire spelling as well as router path. No duplicate/extra queries,
    # encoded aliases, user-supplied URLs, content, cookies or headers are used.
    if (request.scope.get('path') != PATH or request.scope.get('raw_path') != PATH.encode()
            or request.scope.get('query_string') not in {b'locale=en', b'locale=es'}):
        return JSONResponse({'detail': 'SIGNUP_NOTICE_REQUEST_INVALID'}, status_code=400, headers=headers)
    if request.method not in {'GET', 'HEAD'}:
        return JSONResponse({'detail': 'METHOD_NOT_ALLOWED'}, status_code=405,
                            headers={**headers, 'Allow': 'GET, HEAD'})
    locale = request.scope['query_string'].decode().split('=')[1]
    try:
        payload = await run_in_threadpool(_cache.get, locale)
        response = JSONResponse(payload, headers=headers)
    except NoticeUnavailable:
        response = JSONResponse({'detail': 'SIGNUP_NOTICE_UNAVAILABLE'}, status_code=503,
                                headers={**headers, 'Retry-After': str(RETRY_SECONDS)})
    if request.method == 'HEAD':
        response.body = b''
    return response
