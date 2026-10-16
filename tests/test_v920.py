from __future__ import annotations

from storage import database as storage_database
from source_contracts import backend_source

import json
from pathlib import Path
from conftest import assert_ok

ROOT = Path(__file__).resolve().parents[1]


def test_v920_local_membership_is_shared_project_access_not_portable_identity(client, fresh_project):
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (9,2,0)
    project_id=fresh_project['project']['id']
    owner=assert_ok(client.get('/api/identity/current'))
    assert owner['role']=='owner'
    assert owner['member_id'].startswith('member_')
    assert 'project.members.manage' in owner['permissions']
    assert 'project.delete' in owner['permissions']

    editor=assert_ok(client.post('/api/profiles',json={'display_name':'Local Editor'}))['profile']
    members=assert_ok(client.post(f'/api/projects/{project_id}/members',json={'user_id':editor['id'],'role':'editor'}))['members']
    linked=next(row for row in members if row['user_id']==editor['id'])
    assert linked['role']=='editor' and linked['member_id'].startswith('member_')

    switched=assert_ok(client.post(f"/api/profiles/{editor['id']}/activate"))
    assert switched['project']['id']==project_id
    visible=assert_ok(client.get('/api/workspace'))['projects']
    assert project_id in {row['id'] for row in visible}
    shared=next(row for row in visible if row['id']==project_id)
    assert shared['membership_role']=='editor'

    actor=assert_ok(client.get('/api/identity/current'))
    assert actor['role']=='editor'
    assert actor['member_id']==linked['member_id']
    assert 'project.edit' in actor['permissions']
    assert 'project.delete' not in actor['permissions']
    assert 'project.members.manage' not in actor['permissions']
    denied=client.post(f'/api/projects/{project_id}/members',json={'user_id':owner['id'],'role':'editor'})
    assert denied.status_code==403

    local_state=Path(fresh_project['project']['path'])/'local.json'
    raw=json.loads(local_state.read_text())
    assert raw['version']==2
    assert len(raw['memberships'])==2
    assert raw['owner_user_id']==owner['id']
    # The local membership sidecar is not a portable project manifest field.
    constants_source=ROOT.joinpath('src','workspace','constants.py').read_text(encoding='utf-8')
    portable=constants_source.split('PORTABLE_PROJECT_MANIFEST_KEYS = {',1)[1].split('}',1)[0]
    assert 'owner_user_id' not in portable and 'memberships' not in portable and 'member_id' not in portable


def test_v920_writer_review_events_capture_member_actor_without_local_user_id(client, fresh_project):
    actor=assert_ok(client.get('/api/identity/current'))
    review=assert_ok(client.post('/api/reviews',json={'name':'Actor attribution'}))
    assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    with storage_database.connect() as con:
        row=con.execute("SELECT payload_json FROM review_events WHERE review_id=? AND event_type='REVIEW_ROUND_STARTED' ORDER BY created_at DESC,id DESC LIMIT 1",(review['id'],)).fetchone()
    assert row is not None
    payload=json.loads(row[0])
    saved=payload['actor']
    assert saved['member_id']==actor['member_id']
    assert saved['display_name']==actor['display_name']
    assert saved['role']=='owner'
    assert saved['kind']=='breadmaker_user'
    assert actor['user_id'] not in json.dumps(payload)


def test_v920_identity_layer_is_provider_shaped_and_review_participants_stay_separate():
    identity=ROOT.joinpath('src','auth','identity.py').read_text(encoding='utf-8')
    memberships=ROOT.joinpath('src','workspace','memberships.py').read_text(encoding='utf-8')
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    assert 'ROLE_PERMISSIONS' in identity
    assert 'def current_actor(' in identity and 'def require(' in identity
    assert "'owner': frozenset" in identity and "'editor': frozenset" in identity
    assert "'member_id':f'member_{uuid.uuid4().hex}'" in memberships
    assert "'version':2,'owner_user_id':owner_user_id,'memberships':normalized" in memberships
    assert "@app.get('/api/identity/current')" in app
    assert "@app.post('/api/projects/{project_id}/members')" in app
    # Review Participants remain their own canonical table/domain, not project members.
    participant_table=schema.split('CREATE TABLE review_participants(',1)[1].split(');',1)[0]
    assert 'member_id' not in participant_table and 'user_id' not in participant_table
