from __future__ import annotations

import ast
from pathlib import Path

from starlette.requests import Request

ROOT=Path(__file__).resolve().parents[1]

EXPECTED_SECURITY_PINS={
    'fastapi':'0.141.1',
    'starlette':'1.7.0',
    'uvicorn':'0.54.0',
    'pydantic':'2.13.5',
    'anyio':'4.15.1',
    'python-multipart':'0.0.32',
    'reportlab':'5.0.1',
    'Pillow':'13.0.0',
    'cryptography':'50.0.1',
    'Authlib':'1.8.0',
    'joserfc':'1.7.5',
}


def _requirements(path:Path) -> dict[str,str]:
    rows={}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or line.startswith('-r '):continue
        name,version=line.split('==',1)
        rows[name]=version
    return rows


def test_v10314_security_patch_pins_patched_framework_crypto_and_parser_stack():
    requirements=_requirements(ROOT/'requirements.txt')
    for package,version in EXPECTED_SECURITY_PINS.items():
        assert requirements.get(package)==version,(package,requirements.get(package))
    test_requirements=_requirements(ROOT/'requirements-test.txt')
    assert test_requirements.get('pip-audit')=='2.10.1'


def test_v10314_security_routing_uses_canonical_asgi_scope_path_not_reconstructed_url():
    from security.http_request import canonical_request_path

    scope={
        'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
        'scheme':'http','path':'/api/private','raw_path':b'/api/private','query_string':b'',
        'headers':[(b'host',b'example.test/api/runtime')],
        'client':('127.0.0.1',12345),'server':('testserver',80),
    }
    request=Request(scope)
    assert canonical_request_path(request)=='/api/private'
    request_pipeline=(ROOT/'src/backend/request_pipeline.py').read_text(encoding='utf-8')
    http_boundary=(ROOT/'src/backend/http_boundary.py').read_text(encoding='utf-8')
    assert 'request.url.path' not in request_pipeline
    assert 'request.url.path' not in http_boundary
    assert 'canonical_request_path(request)' in request_pipeline


def test_v10314_streamed_hosted_body_limit_remains_independent_of_content_length():
    server=(ROOT/'src/server.py').read_text(encoding='utf-8')
    assert 'total+=len(message.get(\'body\') or b\'\')' in server
    assert 'if total>limit:' in server
    assert "return {'type':'http.disconnect'}" in server
    assert '_send_too_large(send,limit)' in server


def test_v10314_release_verifier_requires_exact_versions_and_live_dependency_audit():
    verifier=(ROOT/'verify-release').read_text(encoding='utf-8')
    assert 'm.version(match.group(1)) != match.group(2)' in verifier
    assert 'python -m pip_audit' not in verifier  # invocation is through the selected pinned interpreter
    assert '"$TEST_PYTHON" -m pip_audit' in verifier
    assert '--strict' in verifier
    assert '--progress-spinner=off' in verifier
    assert '-r requirements.txt' in verifier
    assert 'Breadmaker dependency security audit FAILED.' in verifier


def test_v10314_runtime_source_has_no_direct_code_execution_or_shell_true_primitives():
    forbidden_imports={'pickle'}
    for path in (ROOT/'src').rglob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                assert not ({alias.name.split('.')[0] for alias in node.names}&forbidden_imports),path
            if isinstance(node,ast.ImportFrom):
                assert (node.module or '').split('.')[0] not in forbidden_imports,path
            if not isinstance(node,ast.Call):continue
            if isinstance(node.func,ast.Name):
                assert node.func.id not in {'eval','exec'},(path,node.lineno,node.func.id)
            if isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name):
                assert not (node.func.value.id=='os' and node.func.attr=='system'),(path,node.lineno)
            for keyword in node.keywords:
                if keyword.arg=='shell' and isinstance(keyword.value,ast.Constant):
                    assert keyword.value.value is not True,(path,node.lineno)


def test_v10314_security_document_records_native_parser_and_dependency_boundaries():
    security=(ROOT/'SECURITY.md').read_text(encoding='utf-8')
    assert 'Known-vulnerability release gate' in security
    assert 'ASGI `scope["path"]`' in security
    assert 'Legacy `.doc` conversion is disabled in hosted mode' in security
    assert 'OpenTofu/bundle-root tooling is not part of the deployed Editor runtime' in security
