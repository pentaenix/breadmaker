from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

import asyncio
import hashlib
import base64
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]


def _limits(**overrides):
    from security.hosted_guardrails import HostedLimits
    values=dict(
        max_projects_per_owner=3,
        max_owner_bytes=1024,
        max_project_bytes=512,
        generic_body_bytes=5,
        project_package_bytes=64,
        project_package_expanded_bytes=128,
        breadreview_bytes=48,
        document_bytes=40,
        document_expanded_bytes=80,
        image_bytes=32,
        cuts_bytes=56,
        writes_per_minute=2,
        heavy_per_minute=1,
    )
    values.update(overrides)
    return HostedLimits(**values)


def test_v9611_release_identity_and_high_security_contracts_are_synchronized():
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,6,11)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    docker=(ROOT/'deploy/Dockerfile.hosted-dev').read_text(encoding='utf-8')
    verifier=(ROOT/'scripts/verify_release.py').read_text(encoding='utf-8')
    assert 'uvicorn", "src.server:app"' in docker
    assert 'test_v9611.py' in verifier


def test_v9611_production_can_never_enable_unauthenticated_hosted_developer(monkeypatch,tmp_path):
    from runtime import build_runtime
    from workspace import Workspace

    monkeypatch.setenv('BREADMAKER_RUNTIME','hosted_dev')
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','developer')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    monkeypatch.setenv('BREADMAKER_ENV','production')
    with pytest.raises(RuntimeError,match='HOSTED_DEV_UNAUTHENTICATED_FORBIDDEN_IN_PRODUCTION'):
        build_runtime(Workspace(tmp_path/'hosted-production'))


def test_v9611_imported_external_image_references_are_rejected_on_every_rendered_surface():
    from security import content_security
    con=sqlite3.connect(':memory:')
    con.execute('CREATE TABLE manuscript_blocks(id TEXT,block_type TEXT,content_html TEXT,text_content TEXT,image_path TEXT)')
    con.execute('CREATE TABLE review_snapshot_blocks(id TEXT,block_type TEXT,content_html TEXT,text_content TEXT,image_path TEXT)')
    con.execute('CREATE TABLE canvas_nodes(id TEXT,image_path TEXT)')
    con.execute('CREATE TABLE cuts(id TEXT,content_html TEXT)')

    con.execute("INSERT INTO manuscript_blocks VALUES('m1','image','','','https://tracker.example/pixel.png')")
    finding=content_security.find_unsafe_project_html(con)
    assert finding and finding.surface=='manuscript' and finding.row_id=='m1'
    con.execute('DELETE FROM manuscript_blocks')

    con.execute("INSERT INTO review_snapshot_blocks VALUES('r1','image','','','//tracker.example/review.png')")
    finding=content_security.find_unsafe_project_html(con)
    assert finding and finding.surface=='review_snapshot' and finding.row_id=='r1'
    con.execute('DELETE FROM review_snapshot_blocks')

    con.execute("INSERT INTO canvas_nodes VALUES('c1','data:image/svg+xml,<svg onload=alert(1)>')")
    finding=content_security.find_unsafe_project_html(con)
    assert finding and finding.surface=='canvas_image' and finding.row_id=='c1'
    con.execute('DELETE FROM canvas_nodes')

    con.execute("INSERT INTO cuts VALUES('cut1','<img src=\"https://tracker.example/cut.png\" alt=\"x\">')")
    finding=content_security.find_unsafe_project_html(con)
    assert finding and finding.surface=='cut' and finding.row_id=='cut1'

    assert content_security.find_unsafe_review_snapshot_html({'blocks':[{
        'source_block_id':'portable1','block_type':'image','content_html':'','text_content':'',
        'image_path':'https://tracker.example/portable.png',
    }]}).row_id=='portable1'
    assert content_security.find_unsafe_review_snapshot_html({'blocks':[{
        'source_block_id':'portable2','block_type':'image','content_html':'','text_content':'',
        'image_path':'/api/portable-review/assets/image_deadbeef.png',
    }]}) is None


def test_v9611_browser_asset_allowlist_and_security_headers_block_tracking_and_script_blast_radius(client):
    frontend=frontend_source_from_app_path(ROOT/'static/app.js')
    start=frontend.index('function projectAssetDisplayUrl(')
    end=frontend.index('function crashDraftEntry',start)
    fn=frontend[start:end]
    assert "raw.startsWith('/api/assets/')" in fn
    assert "raw.startsWith('/api/portable-review/assets/')" in fn
    assert "return ''" in fn
    assert 'http://' not in fn and 'https://' not in fn

    response=client.get('/')
    assert response.status_code==200
    csp=response.headers.get('content-security-policy','')
    assert "default-src 'self'" in csp
    assert "script-src 'self' 'sha256-" in csp
    assert "img-src 'self' data: blob:" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert response.headers.get('x-frame-options')=='DENY'
    assert response.headers.get('x-content-type-options')=='nosniff'
    assert response.headers.get('referrer-policy')=='no-referrer'
    assert 'camera=()' in response.headers.get('permissions-policy','')
    app_source=backend_source(ROOT)
    assert "Strict-Transport-Security" in app_source and "proto=='https'" in app_source

    # The CSP hash must actually authorize the one intentional inline bootstrap script.
    source=(ROOT/'static/index.html').read_text(encoding='utf-8')
    inline=source.split('<script>',1)[1].split('</script>',1)[0]
    expected='sha256-'+base64.b64encode(hashlib.sha256(inline.encode('utf-8')).digest()).decode('ascii')
    assert expected in csp


def test_v9611_hosted_rate_and_path_limits_are_real_and_breadreview_route_is_exact():
    from security.hosted_guardrails import HostedRateLimiter,body_limit_for_path,is_heavy_request

    limits=_limits()
    limiter=HostedRateLimiter()
    assert limiter.allow('usr_a','write',limit=2)[0] is True
    assert limiter.allow('usr_a','write',limit=2)[0] is True
    allowed,retry=limiter.allow('usr_a','write',limit=2)
    assert allowed is False and retry>=1
    assert limiter.allow('usr_b','write',limit=2)[0] is True

    assert is_heavy_request('/api/reviews/import-breadreview','POST') is True
    assert body_limit_for_path('/api/reviews/import-breadreview',limits)==limits.breadreview_bytes+1024*1024
    assert body_limit_for_path('/api/assets',limits)==limits.image_bytes+1024*1024
    assert body_limit_for_path('/api/ordinary-edit',limits)==limits.generic_body_bytes


def test_v9611_outer_asgi_limit_rejects_chunked_body_without_content_length(monkeypatch):
    import src.server as server

    limits=_limits(generic_body_bytes=5)
    monkeypatch.setattr(server.environment,'_HOSTED_LIMITS',limits)

    async def fake_app(scope,receive,send):
        while True:
            message=await receive()
            if message.get('type')=='http.disconnect':break
            if not message.get('more_body'):break
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'ok'})

    monkeypatch.setattr(server.core,'app',fake_app)
    received=[
        {'type':'http.request','body':b'abcd','more_body':True},
        {'type':'http.request','body':b'efgh','more_body':False},
    ]
    sent=[]

    async def receive():
        return received.pop(0) if received else {'type':'http.disconnect'}
    async def send(message):sent.append(message)

    scope={'type':'http','method':'POST','path':'/api/ordinary-edit','headers':[]}
    asyncio.run(server.app(scope,receive,send))
    starts=[message for message in sent if message.get('type')=='http.response.start']
    assert len(starts)==1 and starts[0]['status']==413
    assert any(b'HOSTED_REQUEST_TOO_LARGE' in message.get('body',b'') for message in sent)


def test_v9611_hosted_project_and_account_quotas_fail_closed(app_module_fixture,monkeypatch,tmp_path):
    from backend import request_context

    owned=tmp_path/'owned';owned.mkdir();(owned/'payload.bin').write_bytes(b'x'*60)
    limits=_limits(max_projects_per_owner=3,max_project_bytes=64,max_owner_bytes=80)
    monkeypatch.setattr(request_context,'_HOSTED_LIMITS',limits)
    monkeypatch.setattr(request_context.runtime.usage,'owner_project_count',lambda uid:1)
    monkeypatch.setattr(request_context.runtime.usage,'owner_storage_bytes',lambda uid,stop_after=None:60)

    with pytest.raises(HTTPException) as exc:
        request_context._enforce_hosted_new_project_quota('usr_owner',estimated_bytes=25)
    assert exc.value.status_code==413
    assert exc.value.detail['code']=='HOSTED_ACCOUNT_STORAGE_QUOTA'

    monkeypatch.setattr(request_context.runtime.projects,'get',lambda pid:{'id':pid,'path':str(owned),'owner_user_id':'usr_owner'})
    monkeypatch.setattr(request_context.runtime.usage,'project_storage_bytes',lambda pid,stop_after=None:60)
    with pytest.raises(HTTPException) as exc:
        request_context._enforce_hosted_project_storage(project_id='p1',additional_bytes=5)
    assert exc.value.status_code==413
    assert exc.value.detail['code']=='HOSTED_PROJECT_SIZE_QUOTA'

    monkeypatch.setattr(request_context,'_HOSTED_LIMITS',_limits(max_projects_per_owner=1,max_project_bytes=1024,max_owner_bytes=2048))
    monkeypatch.setattr(request_context.runtime.usage,'owner_project_count',lambda uid:1)
    with pytest.raises(HTTPException) as exc:
        request_context._enforce_hosted_new_project_quota('usr_owner')
    assert exc.value.status_code==429
    assert exc.value.detail['code']=='HOSTED_PROJECT_QUOTA'
