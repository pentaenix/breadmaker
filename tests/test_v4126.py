from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import json
import os
import subprocess
import sys
from pathlib import Path


def test_v4126_contract_and_metadata(client, fresh_project):
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text().strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text()
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'id="metadataAction"' in js
    assert 'Danger Zone' in js and 'deleteProjectAction' in js and 'deleteAllDataAction' in js
    assert '/api/danger/challenge' in js and 'dangerConfirmInput' in js
    assert '.danger-zone-title' in css and '.metadata-sections' in css
    meta=client.get('/api/project/metadata')
    assert meta.status_code==200, meta.text
    data=meta.json()
    assert {'words','characters','characters_no_spaces','chapters','scenes','blocks','images'} <= set(data['manuscript'])
    assert {'nodes','connections','canvases'} <= set(data['canvas'])
    assert {'checkpoints','cuts','history','undo'} <= set(data['preservation'])


def test_v4126_challenges_are_lowercase_single_use(client):
    response=client.post('/api/danger/challenge',json={'action':'delete_project'})
    assert response.status_code==200
    challenge=response.json()
    code=challenge['code']
    assert len(code)==10 and all(ch.isdigit() or 'a'<=ch<='z' for ch in code)
    # Wrong code consumes the challenge rather than allowing brute-force retries.
    active=client.get('/api/workspace').json()['active_project_id']
    wrong=client.request('DELETE',f'/api/projects/{active}',json={'challenge_id':challenge['challenge_id'],'code':'0'*10})
    assert wrong.status_code==400
    retry=client.request('DELETE',f'/api/projects/{active}',json={'challenge_id':challenge['challenge_id'],'code':code})
    assert retry.status_code==400


def test_v4126_delete_project_requires_code_and_keeps_workspace_usable(client):
    first=client.get('/api/workspace').json()['active_project_id']
    created=client.post('/api/projects',json={'name':'Delete Me'}).json()['project']
    assert created['id']!=first
    challenge=client.post('/api/danger/challenge',json={'action':'delete_project'}).json()
    result=client.request('DELETE',f"/api/projects/{created['id']}",json={'challenge_id':challenge['challenge_id'],'code':challenge['code']})
    assert result.status_code==200,result.text
    workspace=client.get('/api/workspace').json()
    assert all(p['id']!=created['id'] for p in workspace['projects'])
    assert workspace['active_project_id'] is not None
    assert client.get('/api/bootstrap').status_code==200


def test_v4126_delete_all_data_isolated_subprocess(tmp_path):
    root=Path(__file__).resolve().parents[1]
    home=tmp_path/'breadmaker-home'
    script=r'''
from fastapi.testclient import TestClient
import app
with TestClient(app.app) as client:
    client.post('/api/projects',json={'name':'Disposable'})
    before=client.get('/api/workspace').json()
    assert len(before['projects'])>=2
    challenge=client.post('/api/danger/challenge',json={'action':'delete_all_data'}).json()
    response=client.post('/api/danger/delete-all-data',json={'challenge_id':challenge['challenge_id'],'code':challenge['code']})
    assert response.status_code==200,response.text
    after=client.get('/api/workspace').json()
    assert len(after['projects'])==1,after
    assert after['projects'][0]['name']=='Untitled Book'
    assert client.get('/api/bootstrap').status_code==200
'''
    env=os.environ.copy();env['BREADMAKER_HOME']=str(home)
    proc=subprocess.run([sys.executable,'-c',script],cwd=root,env=env,text=True,capture_output=True,timeout=60)
    assert proc.returncode==0,proc.stdout+'\n'+proc.stderr
