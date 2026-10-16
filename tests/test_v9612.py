from __future__ import annotations
from source_contracts import backend_source,frontend_source_from_app_path,frontend_styles_source_from_styles_path

import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT=Path(__file__).resolve().parents[1]


def test_v9612_release_identity_and_medium_low_security_contracts():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (9,6,12)
    html=ROOT.joinpath('static','index.html').read_text(encoding='utf-8')
    css=frontend_styles_source_from_styles_path(ROOT.joinpath('static','styles.css'))
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert "breadmaker-manuscript-reviews-ui:${currentUserIdentity()||'user'}:${currentProjectIdentity()||'project'}" in js


def test_v9612_danger_confirmation_is_actor_action_and_target_bound():
    from backend import danger_challenges
    danger_challenges._DANGER_CHALLENGES.clear()
    first=danger_challenges._new_danger_challenge('delete_project',actor_id='usr_a',target_id='project_a')
    with pytest.raises(HTTPException):
        danger_challenges._consume_danger_challenge(first['challenge_id'],first['code'],'delete_project',actor_id='usr_a',target_id='project_b')
    second=danger_challenges._new_danger_challenge('delete_project',actor_id='usr_a',target_id='project_a')
    with pytest.raises(HTTPException):
        danger_challenges._consume_danger_challenge(second['challenge_id'],second['code'],'delete_project',actor_id='usr_b',target_id='project_a')
    third=danger_challenges._new_danger_challenge('delete_project',actor_id='usr_a',target_id='project_a')
    danger_challenges._consume_danger_challenge(third['challenge_id'],third['code'],'delete_project',actor_id='usr_a',target_id='project_a')


def test_v9612_contended_editor_lease_cannot_be_monopolized_forever(monkeypatch):
    from backend import client_leases
    pid='a'*32
    client_leases._CLIENT_LEASES.clear();client_leases._CLIENT_LEASE_WAITERS.clear()
    monkeypatch.setattr(client_leases,'_CLIENT_LEASE_MAX_CONTINUOUS_SECONDS',1.0)
    monkeypatch.setattr(client_leases,'_CLIENT_LEASE_WAIT_GRACE_SECONDS',0.0)
    assert client_leases._claim_client_lease(pid,'client-a',actor_id='usr_a',actor_role='editor')
    client_leases._CLIENT_LEASES[pid]['acquired']=time.monotonic()-2
    assert client_leases._claim_client_lease(pid,'client-b',actor_id='usr_b',actor_role='owner') is True
    assert client_leases._CLIENT_LEASES[pid]['client_id']=='client-b'


def test_v9612_backup_credentials_are_sealed_at_rest(tmp_path):
    from workspace import Workspace
    ws=Workspace(tmp_path/'home');ws.init()
    values={
        'webdav':{'username':'private-writer','password':'very-secret-password'},
        'google':{'kind':'google_oauth','client_id':'public-client','client_secret':'private-secret','refresh_token':'private-refresh'},
    }
    ws._save_backup_credentials(values)
    raw=ws.backup_credentials_path.read_text(encoding='utf-8')
    assert 'very-secret-password' not in raw
    assert 'private-refresh' not in raw
    assert 'private-secret' not in raw
    parsed=json.loads(raw)
    assert parsed['version']==2
    assert all(set(row)=={'sealed'} and str(row['sealed']).startswith('sealed-v2:') for row in parsed['credentials'].values())
    assert ws._load_backup_credentials()==values


def test_v9612_hosted_replicas_and_legacy_doc_boundaries_are_enforced():
    hosted=ROOT.joinpath('src','runtime','hosted.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    docker=ROOT.joinpath('deploy','Dockerfile.hosted-dev').read_text(encoding='utf-8')
    assert '_validate_worker_configuration' in hosted
    assert 'HOSTED_SINGLE_WORKER_REQUIRED' not in hosted
    assert "if runtime.mode!='local':" in app and 'Legacy .doc import is disabled in hosted Breadmaker' in app
    assert 'USER breadmaker' in docker
    assert 'useradd --system' in docker


def test_v9612_hosted_member_listing_redacts_global_ids_for_non_managers():
    app=backend_source(ROOT)
    assert 'def _public_project_members' in app
    assert "if runtime.sessions.requires_authentication:" in app
    assert "item.pop('user_id',None)" in app
    assert "'members':_public_project_members(project_id,runtime.projects.members(project_id))" in app
