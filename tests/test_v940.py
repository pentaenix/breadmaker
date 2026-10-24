from __future__ import annotations
from source_contracts import backend_source, storage_source,frontend_source_from_app_path

from pathlib import Path

from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]


def test_v940_request_context_contract_is_project_and_actor_scoped():
    version=tuple(map(int,ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip().split('.')))
    assert version >= (9,4,0)
    contracts=ROOT.joinpath('src','runtime','contracts.py').read_text(encoding='utf-8')
    local=ROOT.joinpath('src','runtime','local.py').read_text(encoding='utf-8')
    storage=storage_source(ROOT)
    frontend=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))

    assert 'class ProjectRequestContext' in contracts
    assert 'project_id: str' in contracts and 'actor: Actor' in contracts
    # Filesystem roots are a local adapter detail, not part of the environment-neutral request contract.
    request_contract=contracts.split('class ProjectRequestContext',1)[1].split('class ProjectContextProvider',1)[0]
    assert 'root: Path' not in request_contract
    assert 'class LocalProjectRequestContext(ProjectRequestContext)' in local
    assert 'resolved_user_id=user_id or (parent.actor.user_id if parent is not None else None)' in local
    assert 'storage_database.acquire_instance_lock()' in local
    assert 'ContextVar' in storage and 'def project_context(' in storage
    assert 'current_db_path()' in storage
    assert "'X-Breadmaker-Project'" in frontend
    assert "'X-Breadmaker-User'" in frontend
    assert 'projectAssetDisplayUrl' in frontend
    # Compare startup ordering inside boot(), not against the earlier heartbeatLease
    # helper definition which naturally contains the same lease call string.
    boot=frontend[frontend.index('async function boot()'):]
    assert "const workspacePromise=api('/api/workspace')" in boot
    assert 'state.workspace=workspace' in boot
    assert "const leasePromise=api('/api/lease',{method:'POST'})" in boot
    assert boot.index('state.workspace=workspace') < boot.index("const leasePromise=api('/api/lease',{method:'POST'})")


def test_v940_request_pipeline_binds_resolved_project_and_actor_per_request(monkeypatch):
    import asyncio
    import json
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    import backend.request_pipeline as pipeline
    from hosted.runtime_services import _DEV_USER_ID as owner
    from tests.test_v10364 import _replicas

    one,_,_,first,_=_replicas(monkeypatch)
    second=one.projects.create('Second synthetic request scope')['id']
    monkeypatch.setattr(pipeline,'runtime',one)
    monkeypatch.setattr(pipeline,'_HOSTED_LIMITS',None)
    monkeypatch.setattr(pipeline,'_prune_expired_temporary_portable_reviews',lambda:None)
    async def exercise():
        async def dispatch(pid):
            req=Request({'type':'http','method':'GET','path':'/api/bootstrap','query_string':b'',
                'headers':[(b'x-breadmaker-project',pid.encode()),
                           (b'x-breadmaker-user',('usr_'+'f'*32).encode())]})
            async def downstream(request):
                await asyncio.sleep(.001) # Interleave requests, not provider state.
                context=one.context.current()
                assert context.project_id==pid and context.actor.user_id==owner
                assert request.state.breadmaker_project_id==pid
                assert request.state.breadmaker_user_id==owner
                return JSONResponse({'project_id':context.project_id,'user_id':context.actor.user_id})
            response=await pipeline.local_origin_guard(req,downstream)
            assert response.status_code==200
            assert json.loads(response.body)=={'project_id':pid,'user_id':owner}
            assert one.context.current() is None
        await asyncio.gather(dispatch(first),dispatch(second))
    asyncio.run(exercise())


def test_v940_two_local_profiles_can_address_their_projects_without_global_active_cross_talk(client):
    profile_a=assert_ok(client.get('/api/profile'))
    workspace_a=assert_ok(client.get('/api/workspace'))
    project_a=workspace_a['active_project_id']
    assert project_a
    assert_ok(client.patch(f'/api/projects/{project_a}',json={'name':'Project A'},headers={
        'X-Breadmaker-Project':project_a,'X-Breadmaker-User':profile_a['id'],
    }))

    profile_b=assert_ok(client.post('/api/profiles',json={'display_name':'Second Writer'}))['profile']
    switched=assert_ok(client.post(f"/api/profiles/{profile_b['id']}/activate"))
    project_b=switched['project']['id']
    assert project_b!=project_a
    assert_ok(client.patch(f'/api/projects/{project_b}',json={'name':'Project B'},headers={
        'X-Breadmaker-Project':project_b,'X-Breadmaker-User':profile_b['id'],
    }))

    # The process-global local shell now points at B. Explicit request context for A
    # must still resolve A's database and actor rather than following that global.
    meta_a=assert_ok(client.get('/api/project/metadata',headers={
        'X-Breadmaker-Project':project_a,'X-Breadmaker-User':profile_a['id'],
    }))
    meta_b=assert_ok(client.get('/api/project/metadata',headers={
        'X-Breadmaker-Project':project_b,'X-Breadmaker-User':profile_b['id'],
    }))
    assert meta_a['project']['name']=='Project A'
    assert meta_b['project']['name']=='Project B'


def test_v940_recovery_and_new_project_import_paths_bind_their_actual_project():
    app=backend_source(ROOT)
    storage=storage_source(ROOT)
    assert '_RECOVERY_BACKUP_SCHEDULERS: dict[str,RecoveryBackupScheduler]' in app
    assert 'def _recovery_scheduler_for(' in app
    assert 'for project_id in _tracked_recovery_project_ids()' in app
    assert 'def _forget_recovery_project(' in app
    assert "with runtime.context.bind(rec[\"id\"],user_id=owner_user_id):" in app
    assert "with runtime.context.bind(rec['id'],user_id=owner_user_id):" in app
    assert 'def clear_session_undo_journal(project_root:Path|None=None, *, all_projects:bool=False)' in storage
    assert '_SESSION_UNDO_JOURNAL.clear();return' in storage
