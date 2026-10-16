from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations

import json
from pathlib import Path
from conftest import assert_ok


def _begin(client,label:str)->str:
    return assert_ok(client.post(f"/api/undo/begin?label={label.replace(' ','%20')}"))["token"]


def _finish(client,token:str):
    return assert_ok(client.post(f"/api/undo/finish/{token}"))


def _apply(client,token:str,direction:str):
    return assert_ok(client.post(f"/api/undo/apply/{token}?direction={direction}"))


def _set_blocks(client,cid:int,texts:list[str]):
    return assert_ok(client.put(f"/api/chapters/{cid}/blocks",json={"blocks":[
        {"block_type":"paragraph","semantic_style":"body","content_html":text,"text_content":text}
        for text in texts
    ]}))


def test_v4131_structural_undo_is_session_delta_not_history_restore():
    root=Path(__file__).resolve().parents[1]
    assert tuple(map(int,(root/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (5, 0, 2)
    js=frontend_source_from_app_path(root/'static/app.js')
    assert '/api/undo/begin' in js
    assert '/api/undo/finish/${token}' in js
    assert '/api/undo/apply/${target.token}?direction=undo' in js
    assert '/api/undo/apply/${target.token}?direction=redo' in js
    assert '/api/undo/rollback/${before}' in js
    # Legacy endpoints may remain server-side for compatibility, but new structural
    # stack entries carry one operation token rather than before/after History IDs.
    finish=js[js.index('async function finishStructuralUndo'):js.index('function withStructuralUndo')]
    assert "kind:'structural',token" in finish
    assert 'before,after:' not in finish


def test_v4131_create_chapter_undo_redo_is_transactional(client,fresh_project):
    boot=fresh_project['bootstrap']; tab=boot['chapters'][0]['tab_id']
    token=_begin(client,'Create chapter')
    created=assert_ok(client.post('/api/chapters',json={'title':'Generated Chapter','tab_id':tab}))
    result=_finish(client,token)
    assert result['changed']>=2 and result['tables']>=2
    assert created['id'] in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}

    _apply(client,token,'undo')
    assert created['id'] not in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}

    _apply(client,token,'redo')
    chapters=assert_ok(client.get('/api/bootstrap'))['chapters']
    assert any(row['id']==created['id'] and row['title']=='Generated Chapter' for row in chapters)

    _apply(client,token,'undo')
    assert created['id'] not in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}


def test_v4131_h1_chapter_generation_restores_same_blocks(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,chapter['id'],['alpha','NEW CHAPTER','beta','gamma'])
    original=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    original_ids=[row['id'] for row in original]
    pivot=original[1]['id']

    token=_begin(client,'Create Chapter from H1')
    generated=assert_ok(client.post(f"/api/chapters/{chapter['id']}/split",json={'block_id':pivot,'title':'New Chapter'}))
    _finish(client,token)
    after_boot=assert_ok(client.get('/api/bootstrap'))
    assert generated['id'] in {row['id'] for row in after_boot['chapters']}

    _apply(client,token,'undo')
    restored=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    assert [row['text_content'] for row in restored]==['alpha','NEW CHAPTER','beta','gamma']
    assert [row['id'] for row in restored]==original_ids
    assert generated['id'] not in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}

    _apply(client,token,'redo')
    assert generated['id'] in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}
    source=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']
    target=assert_ok(client.get(f"/api/chapters/{generated['id']}"))['blocks']
    assert [row['text_content'] for row in source]==['alpha']
    assert [row['text_content'] for row in target]==['beta','gamma']


def test_v4131_move_to_cuts_undo_redo_changes_both_sides(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,chapter['id'],['A B C'])
    baseline=len(assert_ok(client.get('/api/cuts')))

    token=_begin(client,'Move to Cuts')
    created=assert_ok(client.post('/api/cuts',json={'title':'B','content_html':'<p>B</p>','source_chapter_id':chapter['id']}))
    _set_blocks(client,chapter['id'],['A C'])
    _finish(client,token)

    _apply(client,token,'undo')
    assert [b['text_content'] for b in assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']]==['A B C']
    assert len(assert_ok(client.get('/api/cuts')))==baseline

    _apply(client,token,'redo')
    assert [b['text_content'] for b in assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']]==['A C']
    cuts=assert_ok(client.get('/api/cuts'))
    assert len(cuts)==baseline+1 and any(row['id']==created['id'] for row in cuts)


def test_v4131_unrelated_corrupt_history_object_cannot_veto_structural_undo(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]; tab=chapter['tab_id']
    # Create an old History state, then deliberately corrupt one immutable object it
    # references. The old whole-project Undo architecture would validate that graph
    # during every structural restore. The operation journal must not touch it.
    history_id=assert_ok(client.post('/api/autocommit?label=Old%20History'))['id']
    with storage_mutations.tx() as con:
        rev=con.execute('SELECT tree_json FROM revisions WHERE id=?',(history_id,)).fetchone()
        tree=json.loads(rev[0]); chunk=tree['block_groups'][0]['chunks'][0]
        payload=json.loads(con.execute('SELECT payload FROM content_objects WHERE hash=?',(chunk,)).fetchone()[0])
        block_hash=payload['items'][0]['object_hash']
        con.execute("UPDATE content_objects SET payload=? WHERE hash=?",('{"corrupt":true}',block_hash))

    token=_begin(client,'Create chapter despite unrelated bad History')
    created=assert_ok(client.post('/api/chapters',json={'title':'Safe Undo','tab_id':tab}))
    _finish(client,token)
    _apply(client,token,'undo')
    assert created['id'] not in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}
    _apply(client,token,'redo')
    assert created['id'] in {row['id'] for row in assert_ok(client.get('/api/bootstrap'))['chapters']}


def test_v4131_conflict_is_transactional_and_retry_token_survives(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    _set_blocks(client,chapter['id'],['before'])
    token=_begin(client,'Change manuscript')
    _set_blocks(client,chapter['id'],['after'])
    _finish(client,token)
    # Mutate the exact affected row outside the Undo stack to force a conflict,
    # preserving its identity so we can put the expected state back afterward.
    after_block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{**after_block,'content_html':'unexpected','text_content':'unexpected'}]}))
    failed=client.post(f'/api/undo/apply/{token}?direction=undo')
    assert failed.status_code==409
    assert [b['text_content'] for b in assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']]==['unexpected']
    # Put the expected after-state back; the same token must still be usable because
    # the failed apply did not consume or advance it.
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[{**after_block,'content_html':'after','text_content':'after'}]}))
    _apply(client,token,'undo')
    assert [b['text_content'] for b in assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']]==['before']
