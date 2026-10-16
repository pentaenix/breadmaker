from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from workspace import Workspace

ROOT=Path(__file__).resolve().parents[1]


def test_v921_foreign_local_membership_sidecar_does_not_block_project_recovery(fresh_project,tmp_path):
    source=Path(fresh_project['project']['path'])
    root=tmp_path/'foreign-membership-home';project_id='9'*32
    target=root/'projects'/project_id;target.parent.mkdir(parents=True)
    shutil.copytree(source,target)
    manifest=json.loads((target/'manifest.json').read_text(encoding='utf-8'));manifest['project_id']=project_id
    (target/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with sqlite3.connect(target/'project.sqlite3') as con:con.execute('UPDATE projects SET project_uid=?',(project_id,));con.commit()
    foreign=json.loads((target/'local.json').read_text(encoding='utf-8'))
    assert any(str(row.get('user_id','')).startswith('usr_') for row in foreign['memberships'])

    ws=Workspace(root);ws.init()
    project=ws.get(project_id)
    assert project['id']==project_id
    current=ws.local_profile()
    memberships=ws.project_members(project_id)
    assert len(memberships)==1
    assert memberships[0]['user_id']==current['user_id']
    assert memberships[0]['role']=='owner'
    repaired=json.loads((target/'local.json').read_text(encoding='utf-8'))
    assert repaired['version']==2
    assert repaired['owner_user_id']==current['user_id']


