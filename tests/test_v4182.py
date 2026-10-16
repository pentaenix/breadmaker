from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path
from pathlib import Path
from conftest import assert_ok

ROOT=Path(__file__).resolve().parents[1]

def _blocks(client,cid):
    return assert_ok(client.get(f'/api/chapters/{cid}'))['blocks']

def test_v4182_scene_view_gap_and_move_contract(client,fresh_project):
    boot=fresh_project['bootstrap']; ch1=boot['chapters'][0]; tab=ch1['tab_id']
    # Build Chapter 1 with Scene A, an unassigned gap, and the start of Scene B.
    first=_blocks(client,ch1['id'])[0]
    payload=[
        {'id':first['id'],'content_html':'Scene A prose','text_content':'Scene A prose'},
        {'content_html':'Loose bridge words here','text_content':'Loose bridge words here'},
        {'content_html':'Scene B begins','text_content':'Scene B begins'},
    ]
    ids=assert_ok(client.put(f"/api/chapters/{ch1['id']}/blocks",json={'blocks':payload}))['ids']
    ch2=assert_ok(client.post('/api/chapters',json={'title':'Chapter 2','tab_id':tab}))
    b2=_blocks(client,ch2['id'])[0]
    assert_ok(client.put(f"/api/chapters/{ch2['id']}/blocks",json={'blocks':[
        {'id':b2['id'],'content_html':'Scene B ends','text_content':'Scene B ends'},
        {'content_html':'Tail outside scenes','text_content':'Tail outside scenes'},
    ]}))
    ch2_ids=[b['id'] for b in _blocks(client,ch2['id'])]
    a=assert_ok(client.post('/api/scenes',json={'title':'A','block_ids':[ids[0]]}))
    b=assert_ok(client.post('/api/scenes',json={'title':'B','block_ids':[ids[2],ch2_ids[0]]}))
    boot2=assert_ok(client.get('/api/bootstrap'))
    brow=next(x for x in boot2['scenes'] if x['id']==b['id'])
    assert brow['first_block_id']==ids[2] and brow['last_block_id']==ch2_ids[0] and brow['chapter_count']==2
    gap=next(g for g in boot2['scene_gaps'] if g['first_block_id']==ids[1])
    assert gap['before_scene_id']==a['id'] and gap['after_scene_id']==b['id'] and gap['word_count']==4

    # Scene View edits only Scene B; neighboring Chapter prose must survive untouched.
    scene_view=assert_ok(client.get(f"/api/scenes/{b['id']}/manuscript"))
    assert [x['text_content'] for x in scene_view['blocks']]==['Scene B begins','Scene B ends']
    edited=[
        {**{k:v for k,v in scene_view['blocks'][0].items() if k in ('id','block_type','semantic_style','content_html','text_content','image_path','scene_id')},'chapter_id':ch1['id'],'content_html':'Scene B begins edited','text_content':'Scene B begins edited'},
        {**{k:v for k,v in scene_view['blocks'][1].items() if k in ('id','block_type','semantic_style','content_html','text_content','image_path','scene_id')},'chapter_id':ch2['id']},
    ]
    assert_ok(client.put(f"/api/scenes/{b['id']}/blocks",json={'blocks':edited}))
    assert [x['text_content'] for x in _blocks(client,ch1['id'])]==['Scene A prose','Loose bridge words here','Scene B begins edited']

    # Moving B before A moves only B's prose. Chapter 2 remains a Chapter and its header/title is untouched.
    assert_ok(client.post(f"/api/scenes/{b['id']}/move",json={'before_scene_id':a['id']}))
    ch1_after=_blocks(client,ch1['id']); ch2_after=_blocks(client,ch2['id'])
    assert [x['text_content'] for x in ch1_after]==['Scene B begins edited','Scene B ends','Scene A prose','Loose bridge words here']
    assert all(x['scene_id']==b['id'] for x in ch1_after[:2])
    assert [x['text_content'] for x in ch2_after]==['Tail outside scenes']
    assert assert_ok(client.get(f"/api/chapters/{ch2['id']}"))['chapter']['title']=='Chapter 2'
    ordered=[x['id'] for x in assert_ok(client.get('/api/bootstrap'))['scenes']]
    assert ordered.index(b['id'])<ordered.index(a['id'])


def test_v4182_frontend_contract():
    js=frontend_source_from_app_path(ROOT/'static/app.js'); css=frontend_styles_source_from_styles_path((ROOT/'static/styles.css'))
    assert '#manuscriptPaper:focus{outline:none}' in css
    assert 'scene-gap-row' in css and 'words outside Scenes' in js
    assert 'Drop Scene here' in css
    assert 'async function selectScene' in js and "state.viewMode='scene'" in js
    assert "row.ondblclick" in js and 'selectScene(scene.id)' in js
    assert 'scene-grip' in js
    assert "appendSceneGhost(editor,block,ch.id,previousScene,{book:true})" in js
    assert "if(book&&String(scene.first_block_id||'')!==String(block.id))return" in js
