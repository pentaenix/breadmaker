from __future__ import annotations
from source_contracts import backend_source,frontend_styles_source_from_styles_path

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]


def _function_body(source:str,name:str) -> str:
    marker=f"def {name}("
    start=source.index(marker)
    next_def=source.find("\ndef ",start+len(marker))
    next_route=source.find("\n@app.",start+len(marker))
    ends=[value for value in (next_def,next_route) if value!=-1]
    end=min(ends) if ends else len(source)
    return source[start:end]




def test_v9613_hosted_project_administration_routes_are_owner_gated():
    source=backend_source(ROOT)
    assert "return _require_project_permission('project.manage',pid)" in source
    admin_functions={
        'history_v2_restore','history_remote_sync','history_retention_get','history_retention_put',
        'history_gc_run','history_remote_repair','recovery_status','backup_destination_list',
        'backup_destination_add','backup_destination_google_drive_config',
        'backup_destination_google_drive_oauth_start','backup_destination_google_drive_oauth_callback',
        'backup_destination_google_drive_oauth_status','backup_destination_add_webdav',
        'backup_destination_reconnect_webdav','backup_destination_update','backup_destination_remove',
        'backup_destination_now','backup_destination_catch_up','recovery_backups_update',
        'backup_destination_backups','backup_destination_discover','backup_destination_restore',
        'manual_backup','list_backups','restore_backup_as_project',
    }
    for name in admin_functions:
        body=_function_body(source,name)
        assert '_require_hosted_project_management()' in body, name


def test_v9613_hosted_management_gate_denies_non_manager(monkeypatch):
    from backend import request_context
    fake_runtime=SimpleNamespace(sessions=SimpleNamespace(requires_authentication=True))
    monkeypatch.setattr(request_context,'runtime',fake_runtime)
    monkeypatch.setattr(request_context,'_bound_project_id',lambda:'project_a')
    def deny(permission,project_id=None):
        assert permission=='project.manage'
        assert project_id=='project_a'
        raise HTTPException(403,detail='denied')
    monkeypatch.setattr(request_context,'_require_project_permission',deny)
    with pytest.raises(HTTPException) as exc:
        request_context._require_hosted_project_management()
    assert exc.value.status_code==403


def test_v9613_public_hosted_project_records_do_not_expose_owner_account_id(monkeypatch):
    from backend import request_context
    class Caps:
        def supports(self,name):return False
    hosted=SimpleNamespace(sessions=SimpleNamespace(requires_authentication=True),capabilities=Caps())
    monkeypatch.setattr(request_context,'runtime',hosted)
    row={'id':'project_a','name':'Book','path':'/secret/path','owner_user_id':'usr_owner'}
    public=request_context._public_project_record(row)
    assert public=={'id':'project_a','name':'Book'}

    local=SimpleNamespace(sessions=SimpleNamespace(requires_authentication=False),capabilities=SimpleNamespace(supports=lambda _name:True))
    monkeypatch.setattr(request_context,'runtime',local)
    local_public=request_context._public_project_record(row)
    assert local_public['owner_user_id']=='usr_owner'


def test_v9613_expired_opaque_sessions_are_absent_from_active_session_ids(monkeypatch):
    from auth.sessions import OpaqueSessionService
    monkeypatch.setenv('BREADMAKER_SESSION_COOKIE_SECURE','0')
    service=OpaqueSessionService(auth_mode='test',environment='test',test_secret='x'*40)
    token,principal=service.issue('usr_test',auth_method='test_fixture')
    assert principal.session_id in service.active_session_ids()
    key=service._digest(token)
    service._records[key].idle_expires_at=time.time()-1
    assert principal.session_id not in service.active_session_ids()


def test_v9613_expired_session_pruning_erases_detached_portable_review_state(monkeypatch,tmp_path):
    import app as app_module
    from api.reviews import portable as portable_reviews
    live_root=tmp_path/'live';dead_root=tmp_path/'dead'
    live_root.mkdir();dead_root.mkdir()
    (live_root/'live.breadreview').write_bytes(b'live')
    (dead_root/'dead.breadreview').write_bytes(b'dead')
    portable_reviews._TEMP_PORTABLE_REVIEWS.clear()
    portable_reviews._TEMP_PORTABLE_REVIEWS['session:live']=portable_reviews._TemporaryPortableReviewState(live_root/'live.breadreview',live_root,'live.breadreview')
    portable_reviews._TEMP_PORTABLE_REVIEWS['session:dead']=portable_reviews._TemporaryPortableReviewState(dead_root/'dead.breadreview',dead_root,'dead.breadreview',passphrase='secret')
    fake=SimpleNamespace(sessions=SimpleNamespace(requires_authentication=True,active_session_ids=lambda:{'live'}))
    monkeypatch.setattr(portable_reviews,'runtime',fake)
    portable_reviews._prune_expired_temporary_portable_reviews()
    assert 'session:live' in portable_reviews._TEMP_PORTABLE_REVIEWS
    assert 'session:dead' not in portable_reviews._TEMP_PORTABLE_REVIEWS
    assert live_root.exists()
    assert not dead_root.exists()
    portable_reviews._TEMP_PORTABLE_REVIEWS.clear()
