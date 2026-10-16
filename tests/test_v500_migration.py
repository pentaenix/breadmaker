from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import sqlite3
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT.parent/'BreadmakerEditor_4195_baseline'


def _run(root:Path, home:Path, code:str, *args:str) -> dict:
    env=os.environ.copy(); env['BREADMAKER_HOME']=str(home); source_root=(root/'src') if (root/'src').is_dir() else root; env['PYTHONPATH']=str(source_root)
    result=subprocess.run([sys.executable,'-c',code,*args],cwd=root,env=env,text=True,capture_output=True,timeout=90)
    assert result.returncode==0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    line=next((line for line in reversed(result.stdout.splitlines()) if line.startswith('RESULT=')),None)
    assert line, result.stdout
    return json.loads(line.removeprefix('RESULT='))


def test_v500_real_v4195_project_migrates_with_recovery_backup_and_preserves_history_checkpoint_bookmark():
    if not BASELINE.is_dir():
        pytest.skip('optional untouched 4.19.5 source-tree fixture is not bundled')
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v500-migration-'))
    create=r'''
import json,sys
from fastapi.testclient import TestClient
import app,storage
from workspace import workspace
with TestClient(app.app) as c:
    rec=c.post('/api/projects',json={'name':'Legacy 4.19.5 migration fixture'}).json()['project']
    boot=c.get('/api/bootstrap').json(); tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=next(x for x in boot['chapters'] if x['tab_id']==tab['id'])
    old=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    text='Alpha target Omega'
    c.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':old['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}]}).raise_for_status()
    block=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    scene=c.post('/api/scenes',json={'title':'Legacy Scene','block_ids':[block['id']]}).json()
    start=text.index('target'); c.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':start+6}).raise_for_status()
    marker=c.post('/api/labels',json={'name':'Legacy Marker','color':'#aa7733','description':'legacy'}).json()
    c.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}).raise_for_status()
    note=c.post('/api/comments',json={'body':'Legacy note','items':[{'block_id':block['id'],'start_offset':13,'end_offset':18,'selected_text':'Omega'}]}).json()
    cp=c.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Legacy checkpoint'}).json()
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Legacy History','auto',False)
    print('RESULT='+json.dumps({'path':rec['path'],'tab_id':tab['id'],'chapter_id':ch['id'],'block_id':block['id'],'scene_id':scene['id'],'bookmark_start':start,'checkpoint_id':cp['id'],'history_id':rid,'note_id':note['id']}))
'''
    legacy=_run(BASELINE,home,create)

    verify=r'''
import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
import app,storage
from workspace import workspace
with TestClient(app.app) as c:
    active=workspace.active(); root=Path(active['path'])
    boot=c.get('/api/bootstrap').json()
    preflight=c.post(f"/api/checkpoints/{sys.argv[1]}/preflight",json={'target_chapter_id':int(sys.argv[2])})
    assert preflight.status_code==200, preflight.text
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        cols={row[1] for row in con.execute('PRAGMA table_info(bookmarks)').fetchall()}
        required={'start_selected_text','start_prefix_context','start_suffix_context','end_selected_text','end_prefix_context','end_suffix_context','stale'}
        assert required<=cols
        bm=dict(con.execute('SELECT * FROM bookmarks WHERE project_id=?',(pid,)).fetchone())
        assert bm['start_selected_text']=='target' and bm['end_selected_text']=='target'
        assert bm['start_block_id']==sys.argv[3] and bm['end_block_id']==sys.argv[3]
        cp=dict(con.execute('SELECT * FROM checkpoints WHERE id=?',(int(sys.argv[1]),)).fetchone())
        assert cp['include_scenes']==1 and cp['include_markup']==1 and cp['include_notes']==1
        snap=storage.materialize_checkpoint_snapshot(con,cp['root_hash'])
        assert snap['blocks'] and snap['scenes'] and snap['annotations']
        rev=con.execute('SELECT * FROM revisions WHERE id=?',(int(sys.argv[4]),)).fetchone(); assert rev
        import json as _json
        tree=storage.materialize_revision_tree(con,_json.loads(rev['tree_json'])); storage.validate_revision_tree(con,tree)
        integrity=storage.validate_project_invariants(con,pid); assert integrity['ok'], integrity
        mains=con.execute('SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1',(pid,)).fetchall(); assert len(mains)==1
    backups=[p.name for p in (root/'backups').glob('*before_v5_migration*.sqlite3')]
    assert backups, list((root/'backups').iterdir())
    print('RESULT='+json.dumps({'bookmark':boot['bookmark'],'checkpoint':preflight.json(),'backup_count':len(backups),'version':app.APP_VERSION}))
'''
    migrated=_run(ROOT,home,verify,str(legacy['checkpoint_id']),str(legacy['chapter_id']),legacy['block_id'],str(legacy['history_id']))
    assert migrated['bookmark']['target_type']=='range'
    assert migrated['bookmark']['start_block_id']==legacy['block_id']
    assert migrated['checkpoint']['can_apply'] is True
    assert migrated['backup_count']>=1


def test_v500_legacy_v419_shape_migrates_repeatably_without_external_source_tree():
    """Exercise the 5.0 migration from the pre-5 schema features it hardens."""
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v500-legacy-shape-'))
    create=r'''
import json
from fastapi.testclient import TestClient
import app,storage
with TestClient(app.app) as c:
    rec=c.post('/api/projects',json={'name':'Legacy-shape migration fixture'}).json()['project']
    boot=c.get('/api/bootstrap').json(); tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=next(x for x in boot['chapters'] if x['tab_id']==tab['id'])
    old=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    text='Alpha target Omega'
    c.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':old['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}]}).raise_for_status()
    block=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    scene=c.post('/api/scenes',json={'title':'Legacy Scene','block_ids':[block['id']]}).json()
    start=text.index('target'); c.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':start+6}).raise_for_status()
    marker=c.post('/api/labels',json={'name':'Legacy Marker','color':'#aa7733','description':'legacy'}).json()
    c.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}).raise_for_status()
    c.post('/api/comments',json={'body':'Legacy note','items':[{'block_id':block['id'],'start_offset':13,'end_offset':18,'selected_text':'Omega'}]}).raise_for_status()
    cp=c.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Legacy checkpoint'}).json()
    with storage.tx() as con: pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Legacy History','auto',False)
    print('RESULT='+json.dumps({'path':rec['path'],'chapter_id':ch['id'],'block_id':block['id'],'checkpoint_id':cp['id'],'history_id':rid}))
'''
    legacy=_run(ROOT,home,create)
    db=Path(legacy['path'])/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('DROP INDEX IF EXISTS ux_document_tabs_one_main')
        con.execute('ALTER TABLE bookmarks RENAME TO bookmarks_current')
        con.execute("""CREATE TABLE bookmarks(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK(target_type IN ('chapter','scene','range')),
            chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
            scene_id INTEGER REFERENCES scenes(id) ON DELETE CASCADE,
            start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            start_offset INTEGER NOT NULL DEFAULT 0,
            end_offset INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""INSERT INTO bookmarks(project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,created_at,updated_at)
            SELECT project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,created_at,updated_at FROM bookmarks_current""")
        con.execute('DROP TABLE bookmarks_current')
        con.execute('ALTER TABLE checkpoints RENAME TO checkpoints_current')
        con.execute("""CREATE TABLE checkpoints(
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            scope TEXT NOT NULL CHECK(scope IN ('chapter','book')),
            name TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            source_tab_id INTEGER, source_tab_title TEXT NOT NULL DEFAULT '', source_tab_created_at TEXT NOT NULL DEFAULT '',
            source_chapter_id INTEGER, source_chapter_title TEXT NOT NULL DEFAULT '', source_chapter_created_at TEXT NOT NULL DEFAULT '', source_chapter_position INTEGER,
            current_source_chapter_id INTEGER, current_source_chapter_created_at TEXT NOT NULL DEFAULT '',
            snapshot_blob BLOB NOT NULL, snapshot_hash TEXT NOT NULL, root_hash TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0, is_safety INTEGER NOT NULL DEFAULT 0 CHECK(is_safety IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        con.execute("""INSERT INTO checkpoints(id,project_id,scope,name,description,source_tab_id,source_tab_title,source_tab_created_at,source_chapter_id,source_chapter_title,source_chapter_created_at,source_chapter_position,current_source_chapter_id,current_source_chapter_created_at,snapshot_blob,snapshot_hash,root_hash,word_count,is_safety,created_at,updated_at)
            SELECT id,project_id,scope,name,description,source_tab_id,source_tab_title,source_tab_created_at,source_chapter_id,source_chapter_title,source_chapter_created_at,source_chapter_position,current_source_chapter_id,current_source_chapter_created_at,snapshot_blob,snapshot_hash,root_hash,word_count,is_safety,created_at,updated_at FROM checkpoints_current""")
        con.execute('DROP TABLE checkpoints_current')
        con.execute('CREATE INDEX idx_checkpoints_project ON checkpoints(project_id,created_at DESC,id DESC)')
        con.execute('CREATE INDEX idx_checkpoints_source_chapter ON checkpoints(project_id,source_chapter_id,created_at DESC,id DESC)')
        con.execute('CREATE INDEX idx_checkpoints_current_source_chapter ON checkpoints(project_id,current_source_chapter_id,created_at DESC,id DESC)')
        con.commit()
    finally:
        con.close()

    verify=r'''
import json,sys
from pathlib import Path
from fastapi.testclient import TestClient
import app,storage
from workspace import workspace
with TestClient(app.app) as c:
    root=Path(workspace.active()['path']); cp_id=int(sys.argv[1]); chapter_id=int(sys.argv[2]); block_id=sys.argv[3]; history_id=int(sys.argv[4])
    pre=c.post(f'/api/checkpoints/{cp_id}/preflight',json={'target_chapter_id':chapter_id}); assert pre.status_code==200,pre.text
    boot=c.get('/api/bootstrap').json()
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert storage.needs_v5_migration() is False
        bookmark_cols={row[1] for row in con.execute('PRAGMA table_info(bookmarks)').fetchall()}
        assert {'start_selected_text','start_prefix_context','start_suffix_context','end_selected_text','end_prefix_context','end_suffix_context','stale'} <= bookmark_cols
        fks=con.execute('PRAGMA foreign_key_list(bookmarks)').fetchall(); block_fks=[r for r in fks if r['table']=='manuscript_blocks']
        assert block_fks and all(str(r['on_delete']).upper()=='SET NULL' for r in block_fks)
        bm=dict(con.execute('SELECT * FROM bookmarks WHERE project_id=?',(pid,)).fetchone())
        assert bm['start_selected_text']=='target' and bm['end_selected_text']=='target'
        assert bm['start_block_id']==block_id and bm['end_block_id']==block_id and bm['stale']==0
        cp=dict(con.execute('SELECT * FROM checkpoints WHERE id=?',(cp_id,)).fetchone())
        assert cp['include_scenes']==1 and cp['include_markup']==1 and cp['include_notes']==1
        snap=storage.materialize_checkpoint_snapshot(con,cp['root_hash']); assert snap['blocks'] and snap['scenes'] and snap['annotations']
        rev=con.execute('SELECT * FROM revisions WHERE id=?',(history_id,)).fetchone(); assert rev
        tree=storage.materialize_revision_tree(con,json.loads(rev['tree_json'])); storage.validate_revision_tree(con,tree)
        integrity=storage.validate_project_invariants(con,pid); assert integrity['ok'],integrity
        mains=con.execute('SELECT id FROM document_tabs WHERE project_id=? AND is_manuscript=1',(pid,)).fetchall(); assert len(mains)==1
        indexes={r[1] for r in con.execute('PRAGMA index_list(document_tabs)').fetchall()}; assert 'ux_document_tabs_one_main' in indexes
    backups=list((root/'backups').glob('*before_v5_migration*.sqlite3')); assert backups
    print('RESULT='+json.dumps({'bookmark':boot['bookmark'],'backup_count':len(backups),'can_apply':pre.json()['can_apply']}))
'''
    migrated=_run(ROOT,home,verify,str(legacy['checkpoint_id']),str(legacy['chapter_id']),legacy['block_id'],str(legacy['history_id']))
    assert migrated['bookmark']['target_type']=='range'
    assert migrated['bookmark']['start_block_id']==legacy['block_id']
    assert migrated['backup_count']>=1 and migrated['can_apply'] is True


def test_v500_legacy_long_same_block_bookmark_migrates_boundary_context_correctly():
    """Long legacy selections keep context around each stored 160-char excerpt."""
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v500-long-bookmark-'))
    create=r'''
import json
from fastapi.testclient import TestClient
import app
with TestClient(app.app) as c:
    rec=c.post('/api/projects',json={'name':'Long bookmark migration fixture'}).json()['project']
    boot=c.get('/api/bootstrap').json(); tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=next(x for x in boot['chapters'] if x['tab_id']==tab['id'])
    old=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    text='p'*50+'S'*160+'X'*50+'Y'*50+'E'*160+'z'*50
    c.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':old['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}]}).raise_for_status()
    block=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    start=50; end=50+160+50+50+160
    c.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':end}).raise_for_status()
    print('RESULT='+json.dumps({'path':rec['path'],'block_id':block['id']}))
'''
    legacy=_run(ROOT,home,create)
    db=Path(legacy['path'])/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('ALTER TABLE bookmarks RENAME TO bookmarks_current')
        con.execute("""CREATE TABLE bookmarks(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK(target_type IN ('chapter','scene','range')),
            chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
            scene_id INTEGER REFERENCES scenes(id) ON DELETE CASCADE,
            start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            start_offset INTEGER NOT NULL DEFAULT 0,
            end_offset INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""INSERT INTO bookmarks(project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,created_at,updated_at)
            SELECT project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,created_at,updated_at FROM bookmarks_current""")
        con.execute('DROP TABLE bookmarks_current')
        con.commit()
    finally:
        con.close()

    verify=r'''
import json
from fastapi.testclient import TestClient
import app,storage
with TestClient(app.app) as c:
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        bm=dict(con.execute('SELECT * FROM bookmarks WHERE project_id=?',(pid,)).fetchone())
        assert bm['start_selected_text']=='S'*160
        assert bm['start_prefix_context']=='p'*45
        assert bm['start_suffix_context']=='X'*45
        assert bm['end_selected_text']=='E'*160
        assert bm['end_prefix_context']=='Y'*45
        assert bm['end_suffix_context']=='z'*45
    print('RESULT='+json.dumps({'ok':True}))
'''
    assert _run(ROOT,home,verify)['ok'] is True


def test_v500_legacy_invalid_bookmark_offsets_are_normalized_stale_instead_of_blocking_migration():
    """Malformed legacy offsets are preserved safely instead of making 5.0 unopenable."""
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v500-invalid-bookmark-'))
    create=r'''
import json
from fastapi.testclient import TestClient
import app
with TestClient(app.app) as c:
    rec=c.post('/api/projects',json={'name':'Invalid bookmark migration fixture'}).json()['project']
    boot=c.get('/api/bootstrap').json(); tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=next(x for x in boot['chapters'] if x['tab_id']==tab['id'])
    old=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    text='Legacy prose'
    c.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':old['id'],'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}]}).raise_for_status()
    block=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    c.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':0,'end_offset':6}).raise_for_status()
    print('RESULT='+json.dumps({'path':rec['path'],'block_id':block['id'],'length':len(text)}))
'''
    legacy=_run(ROOT,home,create)
    db=Path(legacy['path'])/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('ALTER TABLE bookmarks RENAME TO bookmarks_current')
        con.execute("""CREATE TABLE bookmarks(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK(target_type IN ('chapter','scene','range')),
            chapter_id INTEGER REFERENCES chapters(id) ON DELETE CASCADE,
            scene_id INTEGER REFERENCES scenes(id) ON DELETE CASCADE,
            start_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            end_block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            start_offset INTEGER NOT NULL DEFAULT 0,
            end_offset INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""INSERT INTO bookmarks(project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,start_offset,end_offset,created_at,updated_at)
            SELECT project_id,target_type,chapter_id,scene_id,start_block_id,end_block_id,999,-25,created_at,updated_at FROM bookmarks_current""")
        con.execute('DROP TABLE bookmarks_current')
        con.commit()
    finally:
        con.close()

    verify=r'''
import json,sys
from fastapi.testclient import TestClient
import app,storage
with TestClient(app.app) as c:
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        bm=dict(con.execute('SELECT * FROM bookmarks WHERE project_id=?',(pid,)).fetchone())
        assert bm['start_block_id']==sys.argv[1] and bm['end_block_id']==sys.argv[1]
        assert bm['start_offset']==int(sys.argv[2])
        assert bm['end_offset']==int(sys.argv[2])
        assert bm['stale']==1
        assert storage.validate_project_invariants(con,pid)['ok']
    boot=c.get('/api/bootstrap').json(); assert int(boot['bookmark']['stale'])==1
    print('RESULT='+json.dumps({'ok':True}))
'''
    assert _run(ROOT,home,verify,legacy['block_id'],str(legacy['length']))['ok'] is True


def test_v504_annotations_chapter_anchor_migration_orders_alter_before_index():
    """A 5.0.3-era annotations table can start 5.0.4+ without idx-before-column failure."""
    home=Path(tempfile.mkdtemp(prefix='breadmaker-v504-annotation-migration-'))
    create=r'''
import json
from fastapi.testclient import TestClient
import app
with TestClient(app.app) as c:
    rec=c.post('/api/projects',json={'name':'Pre-5.0.4 annotation schema'}).json()['project']
    boot=c.get('/api/bootstrap').json(); tab=next(t for t in boot['tabs'] if t['is_manuscript']); ch=next(x for x in boot['chapters'] if x['tab_id']==tab['id'])
    block=c.get(f"/api/chapters/{ch['id']}").json()['blocks'][0]
    marker=c.post('/api/labels',json={'name':'Legacy highlight','color':'#aa7733','description':''}).json()
    c.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':0,'selected_text':''}]}).raise_for_status()
    print('RESULT='+json.dumps({'path':rec['path']}))
'''
    legacy=_run(ROOT,home,create)
    db=Path(legacy['path'])/'project.sqlite3'
    con=sqlite3.connect(db)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('DROP INDEX IF EXISTS idx_annotations_chapter')
        con.execute('ALTER TABLE annotations RENAME TO annotations_v504')
        con.execute('''CREATE TABLE annotations(
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            block_id TEXT REFERENCES manuscript_blocks(id) ON DELETE CASCADE,
            annotation_type TEXT NOT NULL DEFAULT 'highlight',
            label_id INTEGER REFERENCES markup_labels(id) ON DELETE SET NULL,
            start_offset INTEGER NOT NULL DEFAULT 0,
            end_offset INTEGER NOT NULL DEFAULT 0,
            selected_text TEXT NOT NULL DEFAULT '',
            prefix_context TEXT NOT NULL DEFAULT '',
            suffix_context TEXT NOT NULL DEFAULT '',
            thread_id INTEGER,
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            stale INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )''')
        con.execute('''INSERT INTO annotations(id,project_id,block_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at)
            SELECT id,project_id,block_id,annotation_type,label_id,start_offset,end_offset,selected_text,prefix_context,suffix_context,thread_id,body,status,stale,created_at,updated_at FROM annotations_v504''')
        con.execute('DROP TABLE annotations_v504')
        con.execute('CREATE INDEX idx_annotations_block ON annotations(block_id)')
        con.commit()
    finally:
        con.close()

    verify=r'''
import json
from fastapi.testclient import TestClient
import app,storage
with TestClient(app.app) as c:
    boot=c.get('/api/bootstrap').json()
    with storage.tx() as con:
        cols={row[1] for row in con.execute('PRAGMA table_info(annotations)').fetchall()}
        indexes={row[1] for row in con.execute('PRAGMA index_list(annotations)').fetchall()}
        assert 'chapter_id' in cols
        assert 'idx_annotations_chapter' in indexes
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        integrity=storage.validate_project_invariants(con,pid); assert integrity['ok'],integrity
        annotation_count=con.execute('SELECT COUNT(*) FROM annotations').fetchone()[0]
    print('RESULT='+json.dumps({'version':app.APP_VERSION,'annotations':annotation_count}))
'''
    migrated=_run(ROOT,home,verify)
    assert migrated['annotations']>=1
