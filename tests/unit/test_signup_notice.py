"""Public legal-copy security boundary, exclusively synthetic/no-network I/O."""
import ast
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from hosted import signup_notice as notice

ROOT = Path(__file__).resolve().parents[2]


def fixture(locale='en'):
    return (ROOT / f'tests/fixtures/signup-notice-v9.{locale}.html').read_bytes()


@pytest.mark.parametrize('locale', ['en', 'es'])
def test_exact_reviewed_notice_fixture_is_renderable_without_remote_html(locale):
    payload = notice.parse_notice(fixture(locale), locale)
    assert payload['version'] == '9' and payload['locale'] == locale
    assert len(payload['paragraphs']) == 4
    assert 'Synthetic test-only notification address' in str(payload)
    assert '<section' not in str(payload)


@pytest.mark.parametrize('old,new', [
    (b'data-notice-version="9"', b'data-notice-version="8"'),
    (b'lang="en"', b'lang="es"'),
    (b'<p>', b'<p onclick="alert(1)">'),
    (b'<strong>', b'<strong style="display:none">'),
    (b'</strong>', b'<script>alert(1)</script></strong>'),
    (b'</strong>', b'<iframe src="https://evil.invalid"></iframe></strong>'),
    (b'</strong>', b'<img src="https://evil.invalid"></strong>'),
    (b'</strong>', b'<form action="/"></form></strong>'),
    (b'https://breadmaker.studio/privacy/', b'javascript:alert(1)'),
    (b'Synthetic test-only notification address', b'{{PRIVACY_NOTICES_ADDRESS}}'),
    (b'Synthetic test-only notification address', b'&#123;&#123;ADDRESS&#125;&#125;'),
    (b'Synthetic test-only notification address', b''),
    (b'</p>', b'<!-- hidden legal copy --></p>'),
    (b'<h2 id="signup-privacy-title">', b'<h2 id="signup-privacy-title" id="duplicate">'),
    (b'</section>', b''),
])
def test_unapproved_unsafe_or_incomplete_copy_fails_closed(old, new):
    with pytest.raises(notice.NoticeUnavailable, match='^SIGNUP_NOTICE_UNAVAILABLE$'):
        notice.parse_notice(fixture().replace(old, new), 'en')


def test_extra_section_and_large_or_non_utf8_bodies_are_rejected():
    for raw in (fixture() * 2, b' ' * (notice.MAX_BYTES + 1), fixture() + b'\xff'):
        with pytest.raises(notice.NoticeUnavailable):
            notice.parse_notice(raw, 'en')


class Response:
    status_code = 200
    headers = {'Content-Type': 'text/html; charset=utf-8'}
    def __init__(self, body=None): self.body = fixture() if body is None else body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, chunk_size):
        assert chunk_size == 1
        for byte in self.body:
            yield bytes([byte])


class Session:
    def __init__(self, response): self.response, self.calls = response, []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_fetch_is_fixed_no_redirect_no_forwarding_and_bounded():
    session = Session(Response())
    notice.fetch_notice('en', session_factory=lambda: session)
    assert session.calls == [(notice.ORIGIN + '/privacy/signup/en/', {
        'headers': {'Accept': 'text/html', 'Accept-Encoding': 'identity'},
        'timeout': (1, 2), 'allow_redirects': False, 'stream': True})]
    with notice._session() as actual:
        assert actual.trust_env is False and not actual.cookies
        assert actual.adapters['https://'].max_retries.total == 0
    with pytest.raises(notice.NoticeUnavailable):
        notice.fetch_notice('../private', session_factory=lambda: session)
    assert len(session.calls) == 1


@pytest.mark.parametrize('status,headers,body', [
    (302, {'Content-Type': 'text/html'}, b''),
    (200, {'Content-Type': 'text/plain'}, b''),
    (200, {'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}, b''),
    (200, {'Content-Type': 'text/html', 'Content-Length': '99999999'}, b''),
    (200, {'Content-Type': 'text/html', 'Content-Length': '-1'}, b''),
    (200, {'Content-Type': 'text/html'}, b'x' * (notice.MAX_BYTES + 1)),
])
def test_bad_upstream_is_not_rendered_or_logged(status, headers, body, caplog):
    response = Response(body);response.status_code = status;response.headers = headers
    with pytest.raises(notice.NoticeUnavailable) as error:
        notice.fetch_notice('en', session_factory=lambda: Session(response))
    assert str(error.value) == 'SIGNUP_NOTICE_UNAVAILABLE' and not caplog.records


def test_slow_response_and_raw_provider_errors_are_sanitized(caplog):
    ticks = iter(range(20))
    with pytest.raises(notice.NoticeUnavailable):
        notice.fetch_notice('en', session_factory=lambda: Session(Response()), now=lambda: next(ticks))
    def fail(): raise RuntimeError('private response body or address must not escape')
    with pytest.raises(notice.NoticeUnavailable) as error:
        notice.fetch_notice('en', session_factory=fail)
    assert str(error.value) == 'SIGNUP_NOTICE_UNAVAILABLE' and not caplog.records


def test_cache_is_two_locales_bounded_and_does_not_serve_expired_copy_on_failure():
    clock = [0];calls = [];fail = [False]
    def fetch(locale):
        calls.append(locale)
        if fail[0]: raise notice.NoticeUnavailable()
        return {'locale': locale}
    cache = notice.NoticeCache(fetch=fetch, now=lambda: clock[0])
    assert cache.get('en') == cache.get('en') == {'locale': 'en'}
    assert calls == ['en']
    clock[0] = notice.CACHE_SECONDS + 1;fail[0] = True
    for _ in range(2):
        with pytest.raises(notice.NoticeUnavailable): cache.get('en')
    assert calls == ['en', 'en']
    clock[0] += notice.RETRY_SECONDS + 1;fail[0] = False
    assert cache.get('en') == {'locale': 'en'}
    cache.locks['es'].acquire()
    try:
        with pytest.raises(notice.NoticeUnavailable): cache.get('es')
    finally: cache.locks['es'].release()
    assert calls == ['en', 'en', 'en']


def request(*, path=notice.PATH, raw=None, query=b'locale=en', method='GET'):
    return Request({'type': 'http', 'method': method, 'path': path,
        'raw_path': path.encode() if raw is None else raw, 'query_string': query,
        'scheme': 'https', 'server': ('app.test', 443),
        'headers': [(b'cookie', b'expired=synthetic'), (b'authorization', b'Bearer synthetic')]})


@pytest.mark.parametrize('kwargs,status', [
    ({'raw': b'/api/auth/%73ignup-notice'}, 400),
    ({'path': notice.PATH + '/'}, 400),
    ({'query': b'locale=en&locale=es'}, 400),
    ({'query': b'locale=%65n'}, 400),
    ({'query': b'locale=en&url=https://evil.invalid'}, 400),
    ({'query': b''}, 400),
    ({'query': b'locale=fr'}, 400),
    ({'method': 'POST'}, 405),
    ({'method': 'OPTIONS'}, 405),
])
def test_exact_wire_request_required_before_any_io(monkeypatch, kwargs, status):
    monkeypatch.setattr(notice, '_cache', SimpleNamespace(get=lambda _: pytest.fail('unexpected I/O')))
    assert asyncio.run(notice.dispatch(request(**kwargs))).status_code == status


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_valid_dispatch_uses_only_locale_and_no_cookie_or_auth_header(monkeypatch, method):
    calls = []
    monkeypatch.setattr(notice, '_cache', SimpleNamespace(get=lambda locale: calls.append(locale) or {'locale': locale}))
    response = asyncio.run(notice.dispatch(request(method=method)))
    assert response.status_code == 200 and calls == ['en']
    assert response.headers['cache-control'] == 'no-store'
    assert response.body == (b'' if method == 'HEAD' else b'{"locale":"en"}')


def test_expired_cookie_bypass_is_only_exact_public_legal_copy(monkeypatch):
    # Execute the real middleware owner with an identity boundary that always
    # rejects its expired synthetic cookie. No Firebase or project state exists.
    from auth.sessions import AuthenticationError
    from security.http_request import canonical_request_path
    node = next(n for n in ast.walk(ast.parse((ROOT/'src/hosted/http_api.py').read_text()))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == 'hosted_request_boundary')
    calls = []
    @asynccontextmanager
    async def binding(*args):
        calls.append('session')
        raise AuthenticationError('expired')
        yield
    session = SimpleNamespace(metadata=lambda: {'auth_mode': 'identity_platform'}, cookie_name='expired', cookie_secure=True)
    namespace = {'canonical_request_path': canonical_request_path, 'runtime': SimpleNamespace(sessions=session),
        '_provider_binding': binding, 'AuthenticationError': AuthenticationError, 'JSONResponse': JSONResponse,
        '_SHELL_PATHS': set(), '_SHELL_PROJECT_RE': __import__('re').compile('^$'), 're': __import__('re')}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<real-hosted-boundary>', 'exec'), namespace)
    monkeypatch.setattr(notice, '_cache', SimpleNamespace(get=lambda locale: {'locale': locale}))
    async def next_handler(req): return JSONResponse({'ordinary': True})
    boundary = namespace['hosted_request_boundary']
    assert asyncio.run(boundary(request(), next_handler)).status_code == 200
    assert calls == []
    for path in ('/api/projects', '/api/auth/client-config', notice.PATH + '/'):
        response = asyncio.run(boundary(request(path=path), next_handler))
        assert response.status_code == 401
        assert 'expired=' in response.headers['set-cookie']
    assert calls == ['session'] * 3
