from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from api.manuscript import anchors as manuscript_anchors

import sqlite3
from pathlib import Path

import pytest

from conftest import assert_ok
from storage import invariants as storage_invariants

ROOT=Path(__file__).resolve().parents[1]


def _blocks(client, chapter_id):
    return assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']


def _set_blocks(client, chapter_id, rows):
    """rows: [(text, scene_id|None), ...]"""
    existing=_blocks(client,chapter_id)
    payload=[]
    for i,(text,scene_id) in enumerate(rows):
        payload.append({
            'id': existing[i]['id'] if i < len(existing) else None,
            'block_type':'paragraph','semantic_style':'body',
            'content_html':text,'text_content':text,'image_path':'','scene_id':scene_id,
        })
    return assert_ok(client.put(f'/api/chapters/{chapter_id}/blocks',json={'blocks':payload}))


def _add_chapter(client, tab_id, title):
    return assert_ok(client.post('/api/chapters',json={'tab_id':tab_id,'title':title}))


def _bookmark(client):
    return assert_ok(client.get('/api/bookmark'))['bookmark']


def test_v500_history_schema_and_visible_chapter_boundary(client,app_module_fixture):
    storage=app_module_fixture.storage
    with storage.tx() as con:
        classification=storage.history_schema_classification(con)
    assert classification['unclassified']==[]
    assert classification['missing']==[]
    js=frontend_source_from_app_path(ROOT/'static/app.js')
    # Raw Chapter state is intentionally isolated to the accessor itself. Ordinary
    # UI code must not rediscover implicit persistence Chapters.
    occurrences=[line for line in js.splitlines() if 'state.chapters' in line]
    # Two canonical accessors plus the 5.0.8 optimistic Chapter-order helper, which
    # explicitly filters implicit persistence Chapters before touching raw storage.
    assert len(occurrences)==4
    assert any('visibleChapters' in line for line in occurrences)
    assert any('storageChapterById' in line for line in occurrences)
    assert sum('!chapter.is_implicit' in line for line in occurrences)==1
    assert occurrences[0].lstrip().startswith('function visibleChapters(')
    assert occurrences[1].lstrip().startswith('function storageChapterById(')



def test_v500_manuscript_import_rehomes_text_bookmark_and_validates_current(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha target Omega',None)])
    block=_blocks(client,ch['id'])[0]
    start='Alpha target Omega'.index('target'); end=start+len('target')
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':end}))

    # Destructive manuscript replacement must use the stored exact text/context to
    # re-home the ribbon rather than losing it when the old block IDs disappear.
    assert_ok(client.post('/api/import',files={'file':('replacement.txt',b'Intro line\nNew Alpha target Omega ending','text/plain')}))
    bm=_bookmark(client)
    assert bm is not None and not bm['stale']
    boot=assert_ok(client.get('/api/bootstrap')); main=next(t for t in boot['tabs'] if t['is_manuscript'])
    with app_module_fixture.storage.tx() as con:
        row=con.execute('SELECT text_content,tab_id FROM manuscript_blocks WHERE id=?',(bm['start_block_id'],)).fetchone()
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert row is not None and int(row['tab_id'])==int(main['id'])
        assert row['text_content'][bm['start_offset']:bm['end_offset']]=='target'
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v500_manuscript_import_keeps_unresolved_bookmark_visible_and_stale(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha target Omega',None)])
    block=_blocks(client,ch['id'])[0]
    start='Alpha target Omega'.index('target'); end=start+len('target')
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':end}))

    # If replacement prose contains no deterministic destination, the Bookmark must
    # remain a valid, visible stale ribbon. Silent deletion/NULL anchors are forbidden.
    assert_ok(client.post('/api/import',files={'file':('replacement.txt',b'Completely unrelated replacement prose.','text/plain')}))
    bm=_bookmark(client)
    assert bm is not None and bm['stale']
    assert bm['start_block_id'] and bm['end_block_id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        stored=con.execute('SELECT * FROM bookmarks WHERE project_id=?',(pid,)).fetchone()
        assert stored['start_selected_text']=='target' and stored['end_selected_text']=='target'
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']

def test_v500_main_manuscript_is_exactly_one_and_db_enforced(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; first=boot['tabs'][0]
    other=assert_ok(client.post('/api/documents',json={'title':'Other'}))
    assert_ok(client.post(f"/api/documents/{other['id']}/make-manuscript"))
    boot=assert_ok(client.get('/api/bootstrap'))
    mains=[t for t in boot['tabs'] if t['is_manuscript']]
    assert [t['id'] for t in mains]==[other['id']]
    storage=app_module_fixture.storage
    with pytest.raises(sqlite3.IntegrityError):
        with storage.tx() as con:
            con.execute('UPDATE document_tabs SET is_manuscript=1 WHERE id=?',(first['id'],))
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert storage.validate_project_invariants(con,pid)['ok']


def test_v500_chapter_reorder_blocks_scene_split_but_allows_safe_move(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2'); ch3=_add_chapter(client,tab['id'],'Chapter 3')
    _set_blocks(client,ch1['id'],[('A',None)])
    _set_blocks(client,ch2['id'],[('B',None)])
    b1=_blocks(client,ch1['id'])[0]; b2=_blocks(client,ch2['id'])[0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Across chapters','block_ids':[b1['id'],b2['id']]}))
    refused=client.post('/api/chapters/reorder',json={'ids':[ch1['id'],ch3['id'],ch2['id']]})
    assert refused.status_code==409
    assert 'split Scene “Across chapters”' in refused.text
    # Moving an unrelated Chapter before the intact Scene span remains legal.
    assert_ok(client.post('/api/chapters/reorder',json={'ids':[ch3['id'],ch1['id'],ch2['id']]}))
    boot=assert_ok(client.get('/api/bootstrap'))
    assert [c['id'] for c in boot['chapters'] if not c['is_implicit'] and c['tab_id']==tab['id']]==[ch3['id'],ch1['id'],ch2['id']]
    assert next(s for s in boot['scenes'] if s['id']==scene['id'])['chapter_count']==2


def test_v500_scene_boundary_backspace_contract_merges_upward(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Hello',None),('World',None)])
    blocks=_blocks(client,ch['id'])
    above=assert_ok(client.post('/api/scenes',json={'title':'Above','block_ids':[blocks[0]['id']]}))
    below=assert_ok(client.post('/api/scenes',json={'title':'Below','block_ids':[blocks[1]['id']]}))
    # This is the payload produced by native Backspace at the beginning of the lower
    # paragraph: the upper block survives and owns the merged prose.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{
        'id':blocks[0]['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':'HelloWorld','text_content':'HelloWorld','image_path':'','scene_id':above['id'],
    }]}))
    after=_blocks(client,ch['id'])
    assert len(after)==1 and after[0]['text_content']=='HelloWorld' and after[0]['scene_id']==above['id']
    scene_ids={s['id'] for s in assert_ok(client.get('/api/bootstrap'))['scenes']}
    assert above['id'] in scene_ids and below['id'] not in scene_ids


def test_v500_stale_bookmark_repair_is_project_scoped(client,fresh_project,app_module_fixture):
    storage=app_module_fixture.storage
    current_chapter=fresh_project['bootstrap']['chapters'][0]
    with storage.tx() as con:
        t=storage.now()
        # Simulate a legacy/imported database that still contains a second projects row.
        # The stale Bookmark belongs only to that second project. Editing the active
        # project's Chapter must not inspect, move, or clear it.
        foreign_pid=con.execute("INSERT INTO projects(project_uid,title,created_at,updated_at) VALUES(?,?,?,?)",('f'*32,'Legacy second project',t,t)).lastrowid
        foreign_tab=con.execute("INSERT INTO document_tabs(project_id,position,title,is_manuscript,created_at,updated_at) VALUES(?,?,?,?,?,?)",(foreign_pid,0,'Manuscript',1,t,t)).lastrowid
        foreign_chapter=con.execute("INSERT INTO chapters(project_id,tab_id,position,title,description,is_implicit,content_html,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(foreign_pid,foreign_tab,0,'Chapter 1','',0,'','',t,t)).lastrowid
        foreign_block=storage.uid('b_')
        foreign_text='Foreign bookmark target'
        con.execute("INSERT INTO manuscript_blocks(id,project_id,tab_id,chapter_id,position,block_type,semantic_style,content_html,text_content,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(foreign_block,foreign_pid,foreign_tab,foreign_chapter,0,'paragraph','body',foreign_text,foreign_text,storage.sha(foreign_text+'\0'+foreign_text),t,t))
        start=foreign_text.index('bookmark'); end=start+len('bookmark')
        con.execute("INSERT INTO bookmarks(project_id,target_type,start_block_id,end_block_id,start_offset,end_offset,start_selected_text,start_prefix_context,start_suffix_context,end_selected_text,end_prefix_context,end_suffix_context,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(foreign_pid,'range',foreign_block,foreign_block,start,end,'bookmark','Foreign ',' target','bookmark','Foreign ',' target',1,t,t))

        manuscript_anchors.repair_stale_bookmark(con,current_chapter['id'])

        foreign=con.execute("SELECT * FROM bookmarks WHERE project_id=?",(foreign_pid,)).fetchone()
        assert foreign['stale']==1
        assert foreign['start_block_id']==foreign_block and foreign['end_block_id']==foreign_block
        assert storage.validate_project_invariants(con,foreign_pid)['ok']


def test_v500_cross_chapter_bookmark_stays_stale_until_both_edges_are_valid(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Alpha start',None)])
    _set_blocks(client,ch2['id'],[('end Omega',None)])
    first=_blocks(client,ch1['id'])[0]; second=_blocks(client,ch2['id'])[0]
    assert_ok(client.put('/api/bookmark',json={
        'target_type':'range','start_block_id':first['id'],'end_block_id':second['id'],
        'start_offset':first['text_content'].index('start'),'end_offset':len('end'),
    }))
    storage=app_module_fixture.storage
    with storage.tx() as con:
        # Model a failed repair on the Chapter-2 endpoint: its block still exists, but
        # the exact endpoint excerpt no longer matches and the Bookmark is stale. A
        # later save/repair in Chapter 1 must not incorrectly declare the whole range OK.
        con.execute("UPDATE manuscript_blocks SET text_content='changed Omega',content_html='changed Omega' WHERE id=?",(second['id'],))
        con.execute("UPDATE bookmarks SET stale=1 WHERE project_id=(SELECT project_id FROM chapters WHERE id=?)",(ch1['id'],))
        manuscript_anchors.repair_stale_bookmark(con,ch1['id'])
        bm=con.execute("SELECT * FROM bookmarks WHERE project_id=(SELECT project_id FROM chapters WHERE id=?)",(ch1['id'],)).fetchone()
        assert bm['stale']==1
        assert bm['end_block_id']==second['id']


def test_v500_text_bookmark_reanchors_through_insert_split_merge_and_replace(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha target Omega',None)])
    block=_blocks(client,ch['id'])[0]
    start='Alpha target Omega'.index('target'); end=start+len('target')
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':end}))

    # Insert before the selected prose; the ribbon must follow the same word.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{
        'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'PREFIX Alpha target Omega','text_content':'PREFIX Alpha target Omega','image_path':'','scene_id':None,
    }]}))
    bm=_bookmark(client); text='PREFIX Alpha target Omega'
    assert text[bm['start_offset']:bm['end_offset']]=='target' and not bm['stale']

    # Enter splits the paragraph and moves the selected word to a new block.
    old=_blocks(client,ch['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[
        {'id':old['id'],'block_type':'paragraph','semantic_style':'body','content_html':'PREFIX Alpha ','text_content':'PREFIX Alpha ','image_path':'','scene_id':None},
        {'id':None,'block_type':'paragraph','semantic_style':'body','content_html':'target Omega','text_content':'target Omega','image_path':'','scene_id':None},
    ]}))
    after=_blocks(client,ch['id']); right=after[1]; bm=_bookmark(client)
    assert bm['start_block_id']==right['id'] and bm['end_block_id']==right['id']
    assert right['text_content'][bm['start_offset']:bm['end_offset']]=='target' and not bm['stale']

    # Backspace merges the bookmarked paragraph into the one above. The right block
    # disappears but the bookmark must not disappear with it.
    left=after[0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{
        'id':left['id'],'block_type':'paragraph','semantic_style':'body','content_html':'PREFIX Alpha target Omega','text_content':'PREFIX Alpha target Omega','image_path':'','scene_id':None,
    }]}))
    bm=_bookmark(client); merged=_blocks(client,ch['id'])[0]
    assert bm and bm['start_block_id']==merged['id'] and merged['text_content'][bm['start_offset']:bm['end_offset']]=='target'

    # Replace All is a separate mutation path and must update the same anchor model.
    assert_ok(client.post('/api/replace',json={'query':'Alpha','replacement':'Alpha expanded','scope':'chapter','chapter_id':ch['id'],'preview':False}))
    bm=_bookmark(client); merged=_blocks(client,ch['id'])[0]
    assert merged['text_content'][bm['start_offset']:bm['end_offset']]=='target'


def test_v500_checkpoint_selective_context_preserves_excluded_notes_markup_scenes(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha beta gamma',None)])
    block=_blocks(client,ch['id'])[0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Current Scene','block_ids':[block['id']]}))
    marker=assert_ok(client.post('/api/labels',json={'name':'Keep','color':'#cc8844','description':''}))
    ann=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))['ids'][0]
    note=assert_ok(client.post('/api/comments',json={'body':'Keep current note','items':[{'block_id':block['id'],'start_offset':6,'end_offset':10,'selected_text':'beta'}]}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Prose only','include_scenes':False,'include_markup':False,'include_notes':False}))
    # Change prose without destroying the selected strings, then restore the checkpoint.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'ZZ Alpha beta gamma','text_content':'ZZ Alpha beta gamma','image_path':'','scene_id':scene['id']}]}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert pre['can_apply'] and not pre['requires_confirmation']
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'preflight_token':pre['preflight_token']}))
    anns=assert_ok(client.get('/api/annotations'))
    ids={a['id'] for a in anns}
    assert ann in ids
    comments=assert_ok(client.get('/api/comments?status=all'))
    assert any(c['id']==note['id'] for c in comments)
    assert next(b for b in _blocks(client,ch['id']) if b['id']==block['id'])['scene_id']==scene['id']


def test_v500_checkpoint_warns_before_unplaceable_current_note_and_history_recovers(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Checkpoint prose',None)])
    block=_blocks(client,ch['id'])[0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Before note'}))
    # Current prose and Note exist only after the Checkpoint; the selected text has no
    # deterministic home in the restored version.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Completely different current text','text_content':'Completely different current text','image_path':'','scene_id':None}]}))
    note=assert_ok(client.post('/api/comments',json={'body':'Current-only thought','items':[{'block_id':block['id'],'start_offset':0,'end_offset':10,'selected_text':'Completely'}]}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert pre['can_apply'] and pre['requires_confirmation']
    assert any(x['kind']=='Note' and x['id']==note['id'] for x in pre['losses'])
    refused=client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'preflight_token':pre['preflight_token']})
    assert refused.status_code==409 and refused.json()['detail']['code']=='CHECKPOINT_CONFIRM_REQUIRED'
    applied=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'confirm_conflicts':True,'preflight_token':pre['preflight_token']}))
    assert applied['history_revision_id']
    assert not any(c['id']==note['id'] for c in assert_ok(client.get('/api/comments?status=all')))
    # The pre-Apply safety revision really contains the newer prose/Note state.
    assert_ok(client.post(f"/api/revisions/{applied['history_revision_id']}/restore"))
    assert 'Completely different current text' in _blocks(client,ch['id'])[0]['text_content']
    assert any(c['id']==note['id'] for c in assert_ok(client.get('/api/comments?status=all')))


def test_v500_checkpoint_current_same_id_note_thread_wins_and_checkpoint_messages_union(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha beta gamma',None)])
    block=_blocks(client,ch['id'])[0]
    thread=assert_ok(client.post('/api/comments',json={'body':'Original','items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'Checkpoint reply'}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Notes'}))
    first=thread['messages'][0]['id']
    assert_ok(client.patch(f'/api/comment-messages/{first}',json={'body':'Current edited original'}))
    assert_ok(client.post(f"/api/comments/{thread['id']}/messages",json={'body':'Current newer reply'}))
    assert_ok(client.patch(f"/api/comments/{thread['id']}",json={'status':'resolved'}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert not pre['requires_confirmation']
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'preflight_token':pre['preflight_token']}))
    restored=assert_ok(client.get('/api/comments?status=all'))
    row=next(c for c in restored if c['id']==thread['id'])
    assert row['status']=='resolved'
    bodies=[m['body'] for m in row['messages']]
    assert bodies[0]=='Current edited original'
    assert 'Checkpoint reply' in bodies and 'Current newer reply' in bodies
    assert len(bodies)==3


def test_v500_checkpoint_recreates_deleted_marker_by_stable_id(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha beta',None)])
    block=_blocks(client,ch['id'])[0]
    marker=assert_ok(client.post('/api/labels',json={'name':'Continuity','color':'#aa7733','description':'saved'}))
    ann=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))['ids'][0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Marker restore'}))
    assert_ok(client.post('/api/annotations/clear-highlights',json={'items':[{'block_id':block['id'],'start_offset':0,'end_offset':5,'selected_text':'Alpha'}]}))
    assert_ok(client.delete(f"/api/labels/{marker['id']}"))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'confirm_conflicts':pre['requires_confirmation'],'preflight_token':pre['preflight_token']}))
    boot=assert_ok(client.get('/api/bootstrap'))
    recreated=next(m for m in boot['labels'] if m['id']==marker['id'])
    assert recreated['name']=='Continuity'
    anns=assert_ok(client.get('/api/annotations'))
    assert any(a['label_id']==marker['id'] and a['selected_text']=='Alpha' for a in anns)


def test_v500_checkpoint_scene_name_collision_is_deterministically_renamed(client,fresh_project):
    boot=fresh_project['bootstrap']; ch=boot['chapters'][0]; tab=boot['tabs'][0]
    _set_blocks(client,ch['id'],[('One',None),('Two',None)])
    blocks=_blocks(client,ch['id'])
    original=assert_ok(client.post('/api/scenes',json={'title':'Kitchen','block_ids':[blocks[0]['id']]}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Scene restore'}))
    assert_ok(client.delete(f"/api/scenes/{original['id']}"))
    # Occupy the saved visible name with a different logical Scene *outside* the
    # restored Chapter. It must survive while the restored Scene is deterministically
    # renamed rather than merged by name.
    other_ch=_add_chapter(client,tab['id'],'Elsewhere')
    _set_blocks(client,other_ch['id'],[('Elsewhere prose',None)])
    other_block=_blocks(client,other_ch['id'])[0]
    other=assert_ok(client.post('/api/scenes',json={'title':'Kitchen','block_ids':[other_block['id']]}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    rename=next(r for r in pre['renames'] if r['kind']=='scene' and r['from']=='Kitchen')
    assert rename['to'].startswith('Scene ')
    assert pre['requires_confirmation']
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'confirm_conflicts':True,'preflight_token':pre['preflight_token']}))
    scenes=assert_ok(client.get('/api/bootstrap'))['scenes']
    assert next(s for s in scenes if s['id']==other['id'])['title']=='Kitchen'
    assert any(s['title']==rename['to'] for s in scenes)


def test_v500_checkpoint_hard_scene_conflict_blocks_apply_without_touching_current(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('A',None),('MIDDLE',None),('B',None)])
    blocks=_blocks(client,ch['id'])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'With middle','include_scenes':False}))
    # Remove the middle paragraph and make A+B one current Scene.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[
        {'id':blocks[0]['id'],'block_type':'paragraph','semantic_style':'body','content_html':'A','text_content':'A','image_path':'','scene_id':None},
        {'id':blocks[2]['id'],'block_type':'paragraph','semantic_style':'body','content_html':'B','text_content':'B','image_path':'','scene_id':None},
    ]}))
    current=_blocks(client,ch['id'])
    scene=assert_ok(client.post('/api/scenes',json={'title':'Current span','block_ids':[b['id'] for b in current]}))
    before=[(b['id'],b['text_content'],b['scene_id']) for b in _blocks(client,ch['id'])]
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert not pre['can_apply']
    assert any('Current span' in msg for msg in pre['hard_conflicts'])
    rejected=client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'confirm_conflicts':True,'preflight_token':pre['preflight_token']})
    assert rejected.status_code==409 and rejected.json()['detail']['code']=='CHECKPOINT_HARD_CONFLICT'
    after=[(b['id'],b['text_content'],b['scene_id']) for b in _blocks(client,ch['id'])]
    assert after==before
    assert any(s['id']==scene['id'] for s in assert_ok(client.get('/api/bootstrap'))['scenes'])


def test_v500_checkpoint_preflight_token_rejects_changed_current(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Alpha',None)])
    block=_blocks(client,ch['id'])[0]
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Token'}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Changed after review','text_content':'Changed after review','image_path':'','scene_id':None}]}))
    response=client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'preflight_token':pre['preflight_token']})
    assert response.status_code==409 and response.json()['detail']['code']=='CHECKPOINT_PREFLIGHT_STALE'
    assert _blocks(client,ch['id'])[0]['text_content']=='Changed after review'


def test_v500_project_validator_query_count_is_bounded_and_not_typing_hook(client,fresh_project,app_module_fixture,monkeypatch):
    ch=fresh_project['bootstrap']['chapters'][0]
    # A moderately large chapter is enough to catch accidental N+1 validation.
    _set_blocks(client,ch['id'],[(f'Paragraph {i}',None) for i in range(120)])
    storage=app_module_fixture.storage
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        statements=[]
        con.set_trace_callback(statements.append)
        assert storage.validate_project_invariants(con,pid)['ok']
        con.set_trace_callback(None)
    selects=[s for s in statements if s.lstrip().upper().startswith('SELECT')]
    assert len(selects)<=12, selects
    # Ordinary Chapter saves use local anchor repair, not a full-project validator.
    called=[]
    original=storage_invariants.validate_project_invariants
    monkeypatch.setattr(storage_invariants,'validate_project_invariants',lambda *a,**k:(called.append(True) or {'ok':True,'errors':[]}))
    block=_blocks(client,ch['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Typing path','text_content':'Typing path','image_path':'','scene_id':None}]}))
    assert called==[]
    monkeypatch.setattr(storage_invariants,'validate_project_invariants',original)


def test_v500_invalid_history_candidate_is_rejected_before_current_changes(client,fresh_project,app_module_fixture,monkeypatch):
    storage=app_module_fixture.storage
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Current must survive',None)])
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
    rid=storage.commit_revision(pid,'Valid before tamper','auto',False)
    original=storage.materialize_revision_tree
    def invalid_tree(con,tree):
        materialized=original(con,tree)
        materialized={**materialized,'tabs':[dict(t,is_manuscript=0) for t in materialized['tabs']]}
        return materialized
    monkeypatch.setattr(storage,'materialize_revision_tree',invalid_tree)
    with pytest.raises(ValueError,match='exactly one Main Manuscript'):
        storage.restore_revision(rid,physical_backup=False,preserve_current=False)
    # Validation happens before the FK-safe delete/rebuild section starts.
    assert _blocks(client,ch['id'])[0]['text_content']=='Current must survive'
    monkeypatch.setattr(storage,'materialize_revision_tree',original)


def test_v500_scene_view_cannot_bypass_structural_scene_move(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Scene prose',None)])
    _set_blocks(client,ch2['id'],[('Other prose',None)])
    block=_blocks(client,ch1['id'])[0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Pinned structurally','block_ids':[block['id']]}))
    # Scene View edits prose in place; changing Chapter placement must go through the
    # explicit Scene move operation where contiguity/Chapter-header rules are enforced.
    moved=client.put(f"/api/scenes/{scene['id']}/blocks",json={'blocks':[{
        'id':block['id'],'chapter_id':ch2['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':'Scene prose edited','text_content':'Scene prose edited','image_path':'','scene_id':scene['id'],
    }]})
    assert moved.status_code==409
    assert 'move the Scene itself' in moved.text
    # Normal focused Scene editing is still allowed and keeps structural ownership.
    assert_ok(client.put(f"/api/scenes/{scene['id']}/blocks",json={'blocks':[{
        'id':block['id'],'chapter_id':ch1['id'],'block_type':'paragraph','semantic_style':'body',
        'content_html':'Scene prose edited','text_content':'Scene prose edited','image_path':'','scene_id':scene['id'],
    }]}))
    saved=assert_ok(client.get(f"/api/scenes/{scene['id']}/manuscript"))
    assert saved['blocks'][0]['chapter_id']==ch1['id'] and saved['blocks'][0]['text_content']=='Scene prose edited'


def test_v500_book_checkpoint_reanchors_current_note_and_markup_across_chapters(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Home target here',None)])
    _set_blocks(client,ch2['id'],[('Other prose',None)])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Whole book before move'}))

    # After the Checkpoint, the selected prose moves to another Chapter and receives
    # current-only editorial work. A Book restore must overlay that work globally,
    # not strand it on the Chapter where it happened to live at Apply time.
    b1=_blocks(client,ch1['id'])[0]; b2=_blocks(client,ch2['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch1['id']}/blocks",json={'blocks':[{'id':b1['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Home without it','text_content':'Home without it','image_path':'','scene_id':None}]}))
    moved='Other target here'; assert_ok(client.put(f"/api/chapters/{ch2['id']}/blocks",json={'blocks':[{'id':b2['id'],'block_type':'paragraph','semantic_style':'body','content_html':moved,'text_content':moved,'image_path':'','scene_id':None}]}))
    b2=_blocks(client,ch2['id'])[0]; start=moved.index('target'); end=start+6
    marker=assert_ok(client.post('/api/labels',json={'name':'Current-only marker','color':'#4c87c9','description':''}))
    ann_id=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'block_id':b2['id'],'start_offset':start,'end_offset':end,'selected_text':'target'}]}))['ids'][0]
    note=assert_ok(client.post('/api/comments',json={'body':'Current-only note','items':[{'block_id':b2['id'],'start_offset':start,'end_offset':end,'selected_text':'target'}]}))

    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={}))
    assert pre['can_apply'] and not pre['requires_confirmation'],pre
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'preflight_token':pre['preflight_token']}))

    anns=assert_ok(client.get('/api/annotations')); ann=next(a for a in anns if a['id']==ann_id)
    restored_ch1=_blocks(client,ch1['id']); target_block=next(b for b in restored_ch1 if 'target' in b['text_content'])
    assert ann['block_id']==target_block['id'] and not ann['stale']
    assert target_block['text_content'][ann['start_offset']:ann['end_offset']]=='target'
    comments=assert_ok(client.get('/api/comments?status=all')); restored_note=next(c for c in comments if c['id']==note['id'])
    note_ann=next(a for a in anns if a['thread_id']==note['id'])
    assert note_ann['block_id']==target_block['id'] and not note_ann['stale']
    assert restored_note['messages'][0]['body']=='Current-only note'


def test_v500_chapter_checkpoint_excluding_scenes_blocks_if_current_scene_prose_would_be_lost(client,fresh_project):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Checkpoint prose',None)])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Chapter prose only','include_scenes':False}))
    existing=_blocks(client,ch['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[
        {'id':existing['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Checkpoint prose','text_content':'Checkpoint prose','image_path':'','scene_id':None},
        {'id':None,'block_type':'paragraph','semantic_style':'body','content_html':'New current Scene prose','text_content':'New current Scene prose','image_path':'','scene_id':None},
    ]}))
    current_block=_blocks(client,ch['id'])[1]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Current-only Chapter Scene','block_ids':[current_block['id']]}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert not pre['can_apply']
    assert any('Scenes were excluded' in message for message in pre['hard_conflicts'])
    refused=client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'preflight_token':pre['preflight_token']})
    assert refused.status_code==409
    assert any(s['id']==scene['id'] for s in assert_ok(client.get('/api/bootstrap'))['scenes'])
    assert any(b['id']==current_block['id'] and b.get('scene_id')==scene['id'] for b in _blocks(client,ch['id']))


def test_v500_book_checkpoint_excluding_scenes_preserves_current_scene_identity(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Scene prose',None)])
    _set_blocks(client,ch2['id'],[('Destination prose',None)])
    scene_block=_blocks(client,ch1['id'])[0]; destination=_blocks(client,ch2['id'])[0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Current Scene','block_ids':[scene_block['id']]}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Prose only book','include_scenes':False}))

    # Move the Scene after the Checkpoint. Applying a prose-only Book Checkpoint must
    # restore prose placement without time-travelling or deleting current Scene state.
    assert_ok(client.post(f"/api/scenes/{scene['id']}/move",json={'target_block_id':destination['id']}))
    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={}))
    assert pre['can_apply'],pre
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'confirm_conflicts':pre['requires_confirmation'],'preflight_token':pre['preflight_token']}))

    after=assert_ok(client.get('/api/bootstrap')); current=next((s for s in after['scenes'] if s['id']==scene['id']),None)
    assert current is not None
    blocks=[b for ch in (ch1,ch2) for b in _blocks(client,ch['id'])]
    owned=[b for b in blocks if b.get('scene_id')==scene['id']]
    assert len(owned)==1 and owned[0]['text_content']=='Scene prose'


def test_v500_book_checkpoint_excluding_scenes_blocks_if_current_scene_prose_would_be_lost(client,fresh_project):
    boot=fresh_project['bootstrap']; ch=boot['chapters'][0]
    _set_blocks(client,ch['id'],[('Checkpoint prose',None)])
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Before current Scene','include_scenes':False}))

    existing=_blocks(client,ch['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[
        {'id':existing['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Checkpoint prose','text_content':'Checkpoint prose','image_path':'','scene_id':None},
        {'id':None,'block_type':'paragraph','semantic_style':'body','content_html':'New Scene prose','text_content':'New Scene prose','image_path':'','scene_id':None},
    ]}))
    new_block=_blocks(client,ch['id'])[1]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Must stay current','block_ids':[new_block['id']]}))

    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={}))
    assert not pre['can_apply']
    assert any('Scenes were excluded' in message for message in pre['hard_conflicts'])
    refused=client.post(f"/api/checkpoints/{cp['id']}/apply",json={'preflight_token':pre['preflight_token']})
    assert refused.status_code==409
    after=assert_ok(client.get('/api/bootstrap'))
    assert any(s['id']==scene['id'] for s in after['scenes'])
    assert any(b['id']==new_block['id'] and b.get('scene_id')==scene['id'] for b in _blocks(client,ch['id']))


def test_v500_checkpoint_never_time_travels_unrelated_project_tools(client,fresh_project):
    boot=fresh_project['bootstrap']; ch=boot['chapters'][0]
    _set_blocks(client,ch['id'],[('Stable anchor prose',None)])
    block=_blocks(client,ch['id'])[0]
    source=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Restore source'}))

    task=assert_ok(client.post('/api/tasks',json={'title':'Keep task','status':'open'}))
    cut=assert_ok(client.post('/api/cuts',json={'title':'Keep cut','content_html':'<p>Preserved cut</p>','source_chapter_id':ch['id']}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Keep canvas','canvas_type':'blank'}))
    story_map=assert_ok(client.post('/api/maps',json={'name':'Keep map'}))
    later_cp=assert_ok(client.post('/api/checkpoints',json={'scope':'book','name':'Keep later checkpoint'}))
    start='Stable anchor prose'.index('anchor'); end=start+6
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':start,'end_offset':end}))

    # Make Current prose differ so Apply really performs restoration.
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={'blocks':[{'id':block['id'],'block_type':'paragraph','semantic_style':'body','content_html':'Stable anchor prose changed','text_content':'Stable anchor prose changed','image_path':'','scene_id':None}]}))
    pre=assert_ok(client.post(f"/api/checkpoints/{source['id']}/preflight",json={}))
    assert pre['can_apply']
    assert_ok(client.post(f"/api/checkpoints/{source['id']}/apply",json={'confirm_conflicts':pre['requires_confirmation'],'preflight_token':pre['preflight_token']}))

    assert any(x['id']==task['id'] for x in assert_ok(client.get('/api/tasks')))
    assert any(x['id']==cut['id'] for x in assert_ok(client.get('/api/cuts')))
    after_boot=assert_ok(client.get('/api/bootstrap'))
    assert any(x['id']==canvas['id'] for x in after_boot['canvases'])
    assert any(x['id']==story_map['id'] for x in after_boot['maps'])
    assert any(x['id']==later_cp['id'] for x in assert_ok(client.get('/api/checkpoints'))['checkpoints'])
    bm=_bookmark(client); assert bm and not bm['stale']
    restored=_blocks(client,ch['id'])[0]
    assert restored['text_content'][bm['start_offset']:bm['end_offset']]=='anchor'


def test_v500_developmental_apply_moves_scene_without_runtime_gap_or_invalid_current(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('First scene',None)])
    _set_blocks(client,ch2['id'],[('Second scene',None)])
    first_block=_blocks(client,ch1['id'])[0]; second_block=_blocks(client,ch2['id'])[0]
    scene1=assert_ok(client.post('/api/scenes',json={'title':'First','block_ids':[first_block['id']]}))
    scene2=assert_ok(client.post('/api/scenes',json={'title':'Second','block_ids':[second_block['id']]}))
    canvas=assert_ok(client.post('/api/canvases',json={'name':'Development','canvas_type':'developmental','developmental_scope':'scenes'}))
    detail=assert_ok(client.get(f"/api/canvases/{canvas['id']}")); nodes=detail['nodes']
    first_node=next(n for n in nodes if n['scene_id']==scene1['id'])
    second_node=next(n for n in nodes if n['scene_id']==scene2['id'])
    result=assert_ok(client.post('/api/developmental/apply',json={'ordered_node_ids':[second_node['id'],first_node['id']]}))
    assert result['ok'] is True and result['scenes']==2
    assert any(b['scene_id']==scene2['id'] for b in _blocks(client,ch1['id']))
    with app_module_fixture.storage.tx() as con:
        assert app_module_fixture.storage.project_invariant_errors(con,fresh_project['bootstrap']['project']['id'])==[]

def test_v500_cutting_bookmarked_chapter_keeps_text_bookmark_visible_and_current_valid(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Survivor')
    _set_blocks(client,ch1['id'],[('Only deleted bookmark target',None)])
    _set_blocks(client,ch2['id'],[('Surviving manuscript prose',None)])
    doomed=_blocks(client,ch1['id'])[0]
    text=doomed['text_content']; start=text.index('bookmark'); end=start+8
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':doomed['id'],'end_block_id':doomed['id'],'start_offset':start,'end_offset':end}))

    assert_ok(client.delete(f"/api/chapters/{ch1['id']}"))
    bookmark=_bookmark(client)
    assert bookmark is not None and bookmark['stale']
    survivor=_blocks(client,ch2['id'])[0]
    assert bookmark['start_block_id']==survivor['id'] and bookmark['end_block_id']==survivor['id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v500_cutting_bookmarked_scene_keeps_text_bookmark_visible_and_current_valid(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('Scene-only bookmark target',None),('Surviving paragraph',None)])
    blocks=_blocks(client,ch['id'])
    scene=assert_ok(client.post('/api/scenes',json={'title':'Doomed scene','block_ids':[blocks[0]['id']]}))
    text=blocks[0]['text_content']; start=text.index('bookmark'); end=start+8
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':blocks[0]['id'],'end_block_id':blocks[0]['id'],'start_offset':start,'end_offset':end}))

    assert_ok(client.post(f"/api/scenes/{scene['id']}/cut"))
    bookmark=_bookmark(client)
    assert bookmark is not None and bookmark['stale']
    survivor=_blocks(client,ch['id'])[0]
    assert survivor['text_content']=='Surviving paragraph'
    assert bookmark['start_block_id']==survivor['id'] and bookmark['end_block_id']==survivor['id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v513_release_metadata_and_docs_are_finalized():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 4, 4)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    assert 'styles.css?v=5.4.4' in html and 'app.js?v=5.4.4' in html
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    assert readme.startswith('# Breadmaker Editor 5.4.4')
    assert not list(ROOT.glob('HARDENING_5.0.*.md'))
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert 'direct-manipulation contracts' in architecture
    assert 'Chapter-title interaction semantics' in architecture
    assert 'Chapter Bookmark' in architecture
    assert '5.2.2 manuscript-import progress contract' in architecture
    assert '5.2.0 Project surface contract' in architecture
    assert '5.1.2 Links manager direct-manipulation contract' in architecture
    assert '5.1.1 Task Links manager contract' in architecture
    assert '5.1.0 Tasks workspace contract' in architecture
    assert 'Capture work and jump back to its context.' not in html
    assert not (ROOT/'HANDOFF_5.0.0_WIP.md').exists()


def test_v504_release_packager_rejects_runtime_and_nested_artifacts():
    from scripts import package_release
    rejected=[
        Path('_unconfigured/project.sqlite3'),
        Path('project.sqlite3'),
        Path('project.sqlite3-wal'),
        Path('.pytest_cache/cache'),
        Path('__pycache__/app.pyc'),
        Path('nested/anything.zip'),
        Path('snapshot.tgz'),
        Path('HANDOFF_5.0.0_WIP.md'),
        Path('HANDOFF_5.0.1_WIP.md'),
        Path('HANDOFF_5.0.2_WIP.md'),
        Path('HANDOFF_5.0.3_WIP.md'),
        Path('HANDOFF_5.0.4_WIP.md'),
        Path('HANDOFF_5.0.5_WIP.md'),
        Path('HANDOFF_99.9.9_WIP.md'),
        Path('HARDENING_99.9.9.md'),
        Path('--help'),
    ]
    assert all(not package_release.should_include(path) for path in rejected)
    assert package_release.should_include(Path('README.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.0.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.1.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.2.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.3.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.4.md'))
    assert not package_release.should_include(Path('HARDENING_5.0.5.md'))


def test_v500_reversed_same_block_bookmark_is_rejected_by_current_and_history_invariants(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,ch['id'],[('abcdef',None)])
    block=_blocks(client,ch['id'])[0]
    assert_ok(client.put('/api/bookmark',json={'target_type':'range','start_block_id':block['id'],'end_block_id':block['id'],'start_offset':1,'end_offset':4}))
    storage=app_module_fixture.storage
    with storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        con.execute('UPDATE bookmarks SET start_offset=4,end_offset=2,stale=1 WHERE project_id=?',(pid,))
        errors=storage.project_invariant_errors(con,pid)
        assert 'Text Bookmark range is reversed' in errors
        with pytest.raises(ValueError,match='Text Bookmark range is reversed'):
            storage.validate_project_invariants(con,pid)
        tree=storage.materialize_revision_tree(con,storage.revision_tree(con,pid))
        assert tree.get('bookmark')
        tree['bookmark']['start_offset']=4; tree['bookmark']['end_offset']=2
        with pytest.raises(ValueError,match='Revision text Bookmark range is reversed'):
            storage.validate_revision_tree(con,tree)


def test_v501_dissolving_chapter_boundary_merges_prose_up_with_stable_identity(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Above',None)])
    _set_blocks(client,ch2['id'],[('Moved one',None),('Moved two',None)])
    before1=_blocks(client,ch1['id']); before2=_blocks(client,ch2['id'])
    scene=assert_ok(client.post('/api/scenes',json={'title':'Moved Scene','block_ids':[before2[0]['id'],before2[1]['id']]}))
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':ch2['id']}))

    result=assert_ok(client.post(f"/api/chapters/{ch2['id']}/dissolve"))
    assert result['target_chapter_id']==ch1['id'] and not result['target_is_implicit']
    merged=_blocks(client,ch1['id'])
    assert [b['text_content'] for b in merged]==['Above','Moved one','Moved two']
    assert [b['id'] for b in merged]==[before1[0]['id'],before2[0]['id'],before2[1]['id']]
    assert all(b['scene_id']==scene['id'] for b in merged[1:])
    assert client.get(f"/api/chapters/{ch2['id']}").status_code==404
    bm=_bookmark(client); assert bm['target_type']=='chapter' and bm['chapter_id']==ch1['id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v502_permanent_chapter_delete_removes_contents_without_merging_or_cut(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Above',None)])
    _set_blocks(client,ch2['id'],[('Delete me one',None),('Delete me two',None)])
    doomed=_blocks(client,ch2['id'])
    scene=assert_ok(client.post('/api/scenes',json={'title':'Delete me scene','block_ids':[b['id'] for b in doomed]}))
    before_cuts=assert_ok(client.get('/api/cuts'))

    assert_ok(client.delete(f"/api/chapters/{ch2['id']}/permanent"))

    assert client.get(f"/api/chapters/{ch2['id']}").status_code==404
    assert [b['text_content'] for b in _blocks(client,ch1['id'])]==['Above']
    assert assert_ok(client.get('/api/cuts'))==before_cuts
    boot=assert_ok(client.get('/api/bootstrap'))
    assert all(s['id']!=scene['id'] for s in boot['scenes'])
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert not con.execute('SELECT 1 FROM manuscript_blocks WHERE id IN (?,?)',(doomed[0]['id'],doomed[1]['id'])).fetchone()
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v501_dissolving_first_header_keeps_prose_in_implicit_full_text_container(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter 2')
    _set_blocks(client,ch1['id'],[('Before the first remaining header',None)])
    _set_blocks(client,ch2['id'],[('Second chapter prose',None)])
    original=_blocks(client,ch1['id'])[0]
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':ch1['id']}))

    result=assert_ok(client.post(f"/api/chapters/{ch1['id']}/dissolve"))
    assert result['target_is_implicit']
    boot=assert_ok(client.get('/api/bootstrap'))
    visible=[c for c in boot['chapters'] if c['tab_id']==tab['id'] and not c['is_implicit']]
    hidden=[c for c in boot['chapters'] if c['tab_id']==tab['id'] and c['is_implicit']]
    assert [c['id'] for c in visible]==[ch2['id']] and len(hidden)==1
    hidden_blocks=_blocks(client,hidden[0]['id'])
    assert [b['id'] for b in hidden_blocks]==[original['id']]
    assert hidden_blocks[0]['text_content']=='Before the first remaining header'
    book=assert_ok(client.get(f"/api/book?tab_id={tab['id']}"))
    assert book['chapters'][0]['is_implicit']==1
    assert any(b['id']==original['id'] and b['chapter_id']==hidden[0]['id'] for b in book['blocks'])
    bm=_bookmark(client); assert bm and bm['target_type']=='range' and bm['stale'] and bm['start_block_id']==original['id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v503_chapter_header_to_body_preserves_title_and_scene_contiguity(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Chapter Two Header')
    _set_blocks(client,ch1['id'],[('Scene above',None)])
    _set_blocks(client,ch2['id'],[('Scene below',None)])
    b1=_blocks(client,ch1['id'])[0]; b2=_blocks(client,ch2['id'])[0]
    scene=assert_ok(client.post('/api/scenes',json={'title':'Cross-boundary Scene','block_ids':[b1['id'],b2['id']]}))
    assert_ok(client.put('/api/bookmark',json={'target_type':'chapter','chapter_id':ch2['id']}))

    result=assert_ok(client.post(f"/api/chapters/{ch2['id']}/dissolve?title_style=body"))
    assert result['target_chapter_id']==ch1['id'] and result['title_style']=='body'
    assert result['converted_block_id']
    merged=_blocks(client,ch1['id'])
    assert [b['text_content'] for b in merged]==['Scene above','Chapter Two Header','Scene below']
    assert merged[1]['semantic_style']=='body'
    assert [b['scene_id'] for b in merged]==[scene['id'],scene['id'],scene['id']]
    bookmark=_bookmark(client)
    assert bookmark['target_type']=='range' and bookmark['start_block_id']==result['converted_block_id']
    assert bookmark['start_offset']==0 and bookmark['end_offset']==len('Chapter Two Header') and not bookmark['stale']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v503_create_scene_below_inserts_one_blank_scene_block_immediately_after_anchor(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]; tab=fresh_project['bootstrap']['tabs'][0]
    _set_blocks(client,ch['id'],[('Anchor prose',None),('Following prose',None)])
    before=_blocks(client,ch['id'])
    anchor=assert_ok(client.post('/api/scenes',json={'title':'Anchor Scene','block_ids':[before[0]['id']]}))
    created=assert_ok(client.post('/api/scenes',json={'tab_id':tab['id'],'after_scene_id':anchor['id']}))
    assert created['created_blank'] and len(created['block_ids'])==1
    after=_blocks(client,ch['id'])
    assert [b['text_content'] for b in after]==['Anchor prose','','Following prose']
    assert after[1]['id']==created['block_ids'][0] and after[1]['scene_id']==created['id']
    boot=assert_ok(client.get('/api/bootstrap'))
    scenes=[s for s in boot['scenes'] if s['tab_id']==tab['id']]
    assert [s['id'] for s in scenes][:2]==[anchor['id'],created['id']]
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v504_chapter_title_notes_and_markup_are_real_annotation_anchors_and_follow_style_conversion(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'The Crimson Door')
    _set_blocks(client,ch1['id'],[('Above prose',None)])
    _set_blocks(client,ch2['id'],[('Below prose',None)])
    marker=assert_ok(client.post('/api/labels',json={'name':'Header marker','color':'#bf7a52','description':'Title test'}))
    hi=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'chapter_id':ch2['id'],'start_offset':4,'end_offset':11,'selected_text':'Crimson','prefix_context':'The ','suffix_context':' Door'}]}))['ids'][0]
    note=assert_ok(client.post('/api/comments',json={'body':'Header note','items':[{'chapter_id':ch2['id'],'start_offset':12,'end_offset':16,'selected_text':'Door','prefix_context':'The Crimson ','suffix_context':''}]}))

    rows=assert_ok(client.get(f"/api/chapters/{ch2['id']}"))['annotations']
    by_id={row['id']:row for row in rows}
    assert by_id[hi]['block_id'] is None and by_id[hi]['chapter_id']==ch2['id']
    comments=assert_ok(client.get('/api/comments?status=all'))
    thread=next(row for row in comments if row['id']==note['id'])
    assert thread['anchors'][0]['chapter_title_anchor']==1 and thread['anchors'][0]['chapter_id']==ch2['id']

    # Editing the title reanchors by contextual selected text instead of pinning stale offsets.
    assert_ok(client.patch(f"/api/chapters/{ch2['id']}/title",json={'title':'Before The Crimson Door After'}))
    row=next(row for row in assert_ok(client.get('/api/annotations?status=all')) if row['id']==hi)
    assert row['selected_text']=='Crimson' and row['start_offset']==11 and not row['stale']

    # A Chapter-header style conversion preserves the editorial identities by moving
    # them onto the newly created stable manuscript paragraph.
    result=assert_ok(client.post(f"/api/chapters/{ch2['id']}/dissolve?title_style=body"))
    converted=result['converted_block_id']; assert converted
    rows=assert_ok(client.get('/api/annotations?status=all')); by_id={row['id']:row for row in rows}
    assert by_id[hi]['block_id']==converted and by_id[hi]['chapter_id']==ch1['id']
    note_payload=next(row for row in assert_ok(client.get('/api/comments?status=all')) if row['id']==note['id'])
    assert note_payload['anchors'][0]['block_id']==converted and not note_payload['anchors'][0]['chapter_title_anchor']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']
        tree=app_module_fixture.storage.materialize_revision_tree(con,app_module_fixture.storage.revision_tree(con,pid))
        assert any(a.get('id')==hi and a.get('block_id')==converted for a in tree['annotations'])
        app_module_fixture.storage.validate_revision_tree(con,tree)


def test_v504_removing_chapter_header_cleans_title_annotations_and_polymorphic_links(client,fresh_project,app_module_fixture):
    boot=fresh_project['bootstrap']; tab=boot['tabs'][0]; ch1=boot['chapters'][0]
    ch2=_add_chapter(client,tab['id'],'Disposable Header')
    _set_blocks(client,ch1['id'],[('Above',None)]); _set_blocks(client,ch2['id'],[('Below',None)])
    marker=assert_ok(client.post('/api/labels',json={'name':'Disposable title marker','color':'#6c8e63','description':''}))
    aid=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'chapter_id':ch2['id'],'start_offset':0,'end_offset':10,'selected_text':'Disposable'}]}))['ids'][0]
    task=assert_ok(client.post('/api/tasks',json={'title':'Points at title annotation','status':'open'}))
    assert_ok(client.post('/api/references',json={'source_type':'task','source_id':str(task['id']),'target_type':'annotation','target_id':str(aid)}))

    assert_ok(client.post(f"/api/chapters/{ch2['id']}/dissolve"))
    assert all(row['id']!=aid for row in assert_ok(client.get('/api/annotations?status=all')))
    refs=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))
    assert all(not (row['target_type']=='annotation' and str(row['target_id'])==str(aid)) for row in refs)
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']


def test_v504_checkpoint_restores_chapter_title_note_and_markup_by_title_identity(client,fresh_project,app_module_fixture):
    ch=fresh_project['bootstrap']['chapters'][0]
    assert_ok(client.patch(f"/api/chapters/{ch['id']}/title",json={'title':'Checkpoint Header'}))
    marker=assert_ok(client.post('/api/labels',json={'name':'Checkpoint title marker','color':'#8b6fba','description':''}))
    hi=assert_ok(client.post('/api/annotations/batch',json={'annotation_type':'highlight','label_id':marker['id'],'items':[{'chapter_id':ch['id'],'start_offset':0,'end_offset':10,'selected_text':'Checkpoint'}]}))['ids'][0]
    note=assert_ok(client.post('/api/comments',json={'body':'Checkpoint title note','items':[{'chapter_id':ch['id'],'start_offset':11,'end_offset':17,'selected_text':'Header'}]}))
    cp=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':ch['id'],'name':'Title editorial state'}))

    assert_ok(client.post('/api/annotations/clear-highlights',json={'items':[{'chapter_id':ch['id'],'start_offset':0,'end_offset':10,'selected_text':'Checkpoint'}]}))
    assert_ok(client.post(f"/api/comments/{note['id']}/delete"))
    assert all(row['id']!=hi for row in assert_ok(client.get('/api/annotations?status=all')))
    assert all(row['id']!=note['id'] for row in assert_ok(client.get('/api/comments?status=all')))

    pre=assert_ok(client.post(f"/api/checkpoints/{cp['id']}/preflight",json={'target_chapter_id':ch['id']}))
    assert not pre.get('hard_conflicts')
    assert_ok(client.post(f"/api/checkpoints/{cp['id']}/apply",json={'target_chapter_id':ch['id'],'confirm_conflicts':pre.get('requires_confirmation',False),'preflight_token':pre['preflight_token']}))
    anns=assert_ok(client.get('/api/annotations?status=all'))
    restored_hi=next(row for row in anns if row['annotation_type']=='highlight' and row['label_id']==marker['id'])
    assert restored_hi['block_id'] is None and restored_hi['chapter_id']==ch['id'] and restored_hi['selected_text']=='Checkpoint'
    restored_note=next(row for row in assert_ok(client.get('/api/comments?status=all')) if row['first_message']=='Checkpoint title note')
    assert restored_note['anchors'][0]['chapter_title_anchor']==1 and restored_note['anchors'][0]['chapter_id']==ch['id']
    with app_module_fixture.storage.tx() as con:
        pid=con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0]
        assert app_module_fixture.storage.validate_project_invariants(con,pid)['ok']
