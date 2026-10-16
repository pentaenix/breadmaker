from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

import io
import json
import sqlite3
import zipfile
from pathlib import Path


def test_v4127_contract_and_danger_zone_order():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    html=(root/'static/index.html').read_text(encoding='utf-8')
    js=frontend_source_from_app_path(root/'static/app.js')
    css=frontend_styles_source_from_styles_path((root/'static/styles.css'))
    assert 'styles.css?v=5.0.2' in html and 'app.js?v=5.0.2' in html
    assert 'Download Data ZIP' in js and '/api/data/export' in js
    # Project rows/list are rendered before the terminal Danger Zone section.
    assert js.index("${recent || '<p class=\"project-list-empty\">No other recent projects.</p>'}") < js.index('<section class="project-group danger-zone">')
    assert "input.addEventListener('paste',e=>e.preventDefault())" in js
    assert "'copy','cut','contextmenu','dragstart','selectstart'" in js
    assert 'user-select:none' in css and '.danger-confirm-code::selection' in css


def test_v4127_full_data_zip_uses_consistent_project_snapshot(client, fresh_project):
    workspace=client.get('/api/workspace').json()
    active=workspace['active_project_id']
    active_record=next(p for p in workspace['projects'] if p['id']==active)
    project_root=Path(active_record['path'])
    marker=project_root/'logs'/'data-export-marker.txt'
    marker.write_text('included',encoding='utf-8')

    response=client.get('/api/data/export')
    assert response.status_code==200,response.text
    assert response.headers['content-type'].startswith('application/zip')
    assert 'attachment;' in response.headers.get('content-disposition','').lower()
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names=set(zf.namelist())
        assert 'registry.json' in names
        prefix=f'projects/{active}/'
        assert prefix+'manifest.json' in names
        assert prefix+'project.sqlite3' in names
        assert prefix+'logs/data-export-marker.txt' in names
        assert prefix+'breadmaker_editor.lock' not in names
        assert prefix+'project.sqlite3-wal' not in names
        assert prefix+'project.sqlite3-shm' not in names
        manifest=json.loads(zf.read(prefix+'manifest.json'))
        assert manifest['project_id']==active
        db_bytes=zf.read(prefix+'project.sqlite3')
    tmp=project_root/'exports'/'v4127-export-check.sqlite3'
    tmp.write_bytes(db_bytes)
    try:
        con=sqlite3.connect(tmp)
        assert con.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert con.execute('PRAGMA foreign_key_check').fetchone() is None
        con.close()
    finally:
        tmp.unlink(missing_ok=True)
