"""Atomic, document-scoped local editing markers, never manuscript rewrites."""
from types import SimpleNamespace
import pytest
from conftest import assert_ok


@pytest.fixture
def document(client,fresh_project):
    boot=fresh_project['bootstrap']
    tab=next(t for t in boot['tabs'] if t['is_manuscript'])
    chapter=next(c for c in boot['chapters'] if c['tab_id']==tab['id'])
    return tab,chapter


def paragraphs(client,chapter,texts):
    current=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={'blocks':[
        {'id':current['id'] if i==0 else None,'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text,'image_path':'','scene_id':None}
        for i,text in enumerate(texts)]}))
    return assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']


def scan(client,tab,kind='adverbs',action='scan'):
    return client.post('/api/annotations/editing-search',json={'tab_id':tab['id'],'scan':kind,'action':action})


def annotations(client,label):
    return assert_ok(client.get(f"/api/annotations?label_id={label['id']}"))


def test_markers_persist_and_reruns_keep_ids_without_rewriting(client,document):
    tab,chapter=document
    blocks=paragraphs(client,chapter,['😀 She walked quietly.','A friendly family left hurriedly.'])
    first=assert_ok(scan(client,tab));label=first['marker']
    assert first['matches']==first['added']==2
    before=annotations(client,label)
    assert [a['selected_text'] for a in before]==['quietly','hurriedly']
    assert before[0]['start_offset']==blocks[0]['text_content'].index('quietly')
    second=assert_ok(scan(client,tab))
    assert second['marker']['id']==label['id'] and second['added']==second['removed']==0
    assert annotations(client,label)==before
    assert assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks']==blocks


def test_reuses_existing_marker_preserves_manual_results_and_attributes(client,document):
    tab,chapter=document;block=paragraphs(client,chapter,['quietly and hurriedly'])[0]
    label=assert_ok(client.post('/api/labels',json={'name':'-LY ADVERBS','color':'#123abc','description':'My own notes'}))
    assert_ok(client.post('/api/annotations/batch',json={'label_id':label['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':7,'selected_text':'quietly'}]}))
    result=assert_ok(scan(client,tab))
    assert result['matches']==2 and result['added']==1
    assert result['marker']['id']==label['id'] and result['marker']['color']=='#123abc'
    assert result['marker']['description']=='My own notes'
    assert len(annotations(client,label))==2
    assert_ok(scan(client,tab));assert len(annotations(client,label))==2
    assert any(not a['body'] for a in annotations(client,label))


def test_no_matches_creates_no_empty_marker(client,document):
    tab,chapter=document;paragraphs(client,chapter,['A friendly family.'])
    before=assert_ok(client.get('/api/bootstrap'))['labels']
    assert assert_ok(scan(client,tab))=={'marker':None,'matches':0,'added':0,'removed':0}
    assert assert_ok(client.get('/api/bootstrap'))['labels']==before


def test_all_searches_choose_recommended_unused_colors_without_recoloring(client,document):
    from api.annotations.marker_colors import recommend_marker_color
    from api.annotations.editing_descriptions import DESCRIPTIONS
    tab,chapter=document
    paragraphs(client,chapter,['She realised she felt very afraid, slowly walking in order to avoid an unexpected surprise.'])
    used=[recommend_marker_color([])]
    assert_ok(client.post('/api/labels',json={'name':'Manual marker','color':used[0]}))
    for kind in ('telling','filter','adverbs','qualifiers','wordy','redundancies','emotions'):
        result=assert_ok(scan(client,tab,kind))
        assert result['matches']>0
        assert result['marker']['description']==DESCRIPTIONS[kind]
        color=result['marker']['color']
        assert color==recommend_marker_color(used) and color not in used
        used.append(color)
        assert assert_ok(scan(client,tab,kind))['marker']['color']==color
    assert len(set(used))==8
    labels=assert_ok(client.get('/api/bootstrap'))['labels']
    assert next(label for label in labels if label['name']=='Manual marker')['color']==used[0]


def test_generated_legacy_description_is_refreshed_but_custom_copy_survives(client,document):
    from api.annotations.editing_descriptions import DESCRIPTIONS,LEGACY_DESCRIPTION
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    marker=assert_ok(scan(client,tab))['marker']
    assert_ok(client.patch(f"/api/labels/{marker['id']}",json={'description':LEGACY_DESCRIPTION}))
    result=assert_ok(scan(client,tab))
    assert result['marker']['description']==DESCRIPTIONS['adverbs']
    assert result['marker']['id']==marker['id'] and result['marker']['color']==marker['color']
    assert result['added']==result['removed']==0
    assert_ok(client.patch(f"/api/labels/{marker['id']}",json={'description':'My own revision notes'}))
    assert assert_ok(scan(client,tab))['marker']['description']=='My own revision notes'


def test_offscreen_chapters_included_other_documents_and_titles_excluded(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    second=assert_ok(client.post('/api/chapters',json={'tab_id':tab['id'],'title':'hurriedly'}))
    paragraphs(client,second,['softly'])
    other=assert_ok(client.post('/api/documents',json={'title':'Other document'}))
    paragraphs(client,{'id':other['chapter_id']},['loudly'])
    result=assert_ok(scan(client,tab));rows=annotations(client,result['marker'])
    assert result['matches']==2 and [a['selected_text'] for a in rows]==['quietly','softly']
    other_result=assert_ok(scan(client,other));assert other_result['matches']==1
    assert len(annotations(client,result['marker']))==3
    assert_ok(scan(client,tab));assert len(annotations(client,result['marker']))==3


def test_rerun_removes_stale_owned_only_not_manual(client,document):
    tab,chapter=document;block=paragraphs(client,chapter,['quietly swiftly'])[0]
    result=assert_ok(scan(client,tab));label=result['marker']
    # Simulate stale and duplicate generated anchors through their canonical owner.
    from storage import mutations
    with mutations.tx() as con:
        con.execute('UPDATE annotations SET stale=1 WHERE label_id=?',(label['id'],))
    assert_ok(client.post('/api/annotations/batch',json={'label_id':label['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':7,'selected_text':'quietly'}]}))
    result=assert_ok(scan(client,tab));rows=annotations(client,label)
    assert result['removed']==2 and result['added']==1
    assert len(rows)==2 and sum(not a['body'] for a in rows)==1


def test_whole_pass_is_undoable_and_redoable(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly hurriedly'])
    token=assert_ok(client.post('/api/undo/begin?label=Editing%20search'))['token']
    result=assert_ok(scan(client,tab));assert_ok(client.post(f'/api/undo/finish/{token}'))
    assert len(annotations(client,result['marker']))==2
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    assert result['marker']['id'] not in [l['id'] for l in assert_ok(client.get('/api/bootstrap'))['labels']]
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    assert len(annotations(client,result['marker']))==2


@pytest.mark.parametrize('field,limit',[('MAX_EDITING_CHARACTERS',3),('MAX_EDITING_BLOCK_CHARACTERS',3),('MAX_EDITING_MATCHES',1)])
def test_size_limits_are_atomic(client,document,monkeypatch,field,limit):
    import api.annotations.routes as owner
    tab,chapter=document;paragraphs(client,chapter,['quietly hurriedly'])
    monkeypatch.setattr(owner,field,limit)
    assert scan(client,tab).status_code==413
    assert assert_ok(client.get('/api/bootstrap'))['labels']==[]


def test_mid_scan_failure_rolls_back_without_partial_marker(client,document,monkeypatch):
    import api.annotations.routes as owner
    tab,chapter=document;paragraphs(client,chapter,['quietly','hurriedly'])
    native=owner.find_matches
    def fail(text,kind):
        if text=='hurriedly':raise RuntimeError('Synthetic matcher failure')
        return native(text,kind)
    monkeypatch.setattr(owner,'find_matches',fail)
    with pytest.raises(RuntimeError,match='Synthetic'):scan(client,tab)
    assert assert_ok(client.get('/api/bootstrap'))['labels']==[]


@pytest.mark.parametrize('payload',[
    {'tab_id':True,'scan':'adverbs'},{'tab_id':'1','scan':'adverbs'},
    {'tab_id':1,'scan':'arbitrary regex'},{'scan':'adverbs'},
])
def test_invalid_inputs_rejected(client,fresh_project,payload):
    assert client.post('/api/annotations/editing-search',json=payload).status_code==422


def test_cross_origin_search_is_rejected_without_mutation(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    response=client.post('/api/annotations/editing-search',headers={'Origin':'https://untrusted.example'},json={'tab_id':tab['id'],'scan':'adverbs'})
    assert response.status_code==403
    assert assert_ok(client.get('/api/bootstrap'))['labels']==[]


def test_missing_document_is_rejected(client,document):
    tab,_=document
    assert scan(client,{'id':tab['id']+100000}).status_code==404


def test_project_header_does_not_scan_previous_projects_text(client,document,fresh_project):
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    previous=fresh_project['project']['id']
    other=assert_ok(client.post('/api/projects',json={'name':'Another isolated project'}))['project']['id']
    response=client.post('/api/annotations/editing-search',headers={'X-Breadmaker-Project':other},json={'tab_id':tab['id'],'scan':'adverbs'})
    assert response.status_code==200 and response.json()['matches']==0
    response=client.post('/api/annotations/editing-search',headers={'X-Breadmaker-Project':previous},json={'tab_id':tab['id'],'scan':'adverbs'})
    assert response.status_code==200 and response.json()['matches']==1


def test_local_only_route_denies_hosted_before_storage(client,document,monkeypatch):
    import api.annotations.routes as owner
    tab,_=document;monkeypatch.setattr(owner,'runtime',SimpleNamespace(mode='hosted'))
    monkeypatch.setattr(owner.storage_mutations,'tx',lambda:pytest.fail('Hosted route accessed native storage'))
    assert scan(client,tab).status_code==404
    assert client.get(f"/api/annotations/editing-search?tab_id={tab['id']}").status_code==404


def search_status(client,tab,kind='adverbs'):
    return assert_ok(client.get(f"/api/annotations/editing-search?tab_id={tab['id']}"))['searches'][kind]


def test_clear_remembers_renamed_marker_and_preserves_manual_and_other_documents(client,document):
    tab,chapter=document;block=paragraphs(client,chapter,['quietly swiftly'])[0]
    marker=assert_ok(scan(client,tab))['marker']
    assert_ok(client.patch(f"/api/labels/{marker['id']}",json={'name':'My adverbs'}))
    other=assert_ok(client.post('/api/documents',json={'title':'Other document'}))
    paragraphs(client,{'id':other['chapter_id']},['loudly'])
    assert assert_ok(scan(client,other))['marker']['id']==marker['id']
    assert_ok(client.post('/api/annotations/batch',json={'label_id':marker['id'],'items':[{'block_id':block['id'],'start_offset':0,'end_offset':7,'selected_text':'quietly'}]}))
    assert search_status(client,tab)['matches']==2
    result=assert_ok(scan(client,tab,action='clear'))
    assert result['removed']==2 and result['marker']['id']==marker['id']
    assert len(annotations(client,marker))==2
    state=search_status(client,tab)
    assert state['marker']['name']=='My adverbs' and state['matches']==0
    # Even after all owned results are cleared everywhere, the registry retains
    # the marker identity independently of its current name.
    assert_ok(scan(client,other,action='clear'))
    assert search_status(client,tab)['marker']['id']==marker['id']
    result=assert_ok(scan(client,tab,action='recheck'))
    assert result['marker']['id']==marker['id'] and result['added']==1
    assert len(annotations(client,marker))==2


def test_recheck_replaces_owned_results_instead_of_duplicating(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly swiftly'])
    marker=assert_ok(scan(client,tab))['marker'];before=annotations(client,marker)
    result=assert_ok(scan(client,tab,action='recheck'));after=annotations(client,marker)
    assert result['removed']==result['added']==result['matches']==2
    assert [a['selected_text'] for a in after]==[a['selected_text'] for a in before]
    # SQLite may legitimately reuse row IDs after deleting the old rows.
    assert all(a['status']=='open' and not a['stale'] for a in after)


def test_remove_is_global_and_undo_restores_marker_and_results(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    marker=assert_ok(scan(client,tab))['marker']
    other=assert_ok(client.post('/api/documents',json={'title':'Second document'}))
    paragraphs(client,{'id':other['chapter_id']},['softly']);assert_ok(scan(client,other))
    before=annotations(client,marker)
    token=assert_ok(client.post('/api/undo/begin?label=Remove%20editing%20marker'))['token']
    result=assert_ok(scan(client,tab,action='remove'))
    assert result['removed']==2 and result['marker'] is None
    assert_ok(client.post(f'/api/undo/finish/{token}'))
    assert search_status(client,tab)['marker'] is None
    assert annotations(client,marker)==[]
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=undo'))
    assert annotations(client,marker)==before
    assert search_status(client,tab)['marker']['id']==marker['id']
    assert_ok(client.post(f'/api/undo/apply/{token}?direction=redo'))
    assert search_status(client,tab)['marker'] is None


def test_failed_recheck_does_not_clear_existing_results(client,document,monkeypatch):
    import api.annotations.routes as owner
    tab,chapter=document;paragraphs(client,chapter,['quietly swiftly'])
    marker=assert_ok(scan(client,tab))['marker'];before=annotations(client,marker)
    monkeypatch.setattr(owner,'MAX_EDITING_MATCHES',1)
    assert scan(client,tab,action='recheck').status_code==413
    assert annotations(client,marker)==before


def test_deleted_marker_registry_cannot_bind_an_unrelated_reused_numeric_id(client,document):
    tab,chapter=document;paragraphs(client,chapter,['quietly'])
    marker=assert_ok(scan(client,tab))['marker']
    assert_ok(scan(client,tab,action='remove'))
    other=assert_ok(client.post('/api/labels',json={'name':'Unrelated marker','color':'#123abc'}))
    assert other['id']==marker['id']  # SQLite deliberately reuses the numeric ID.
    assert search_status(client,tab)['marker'] is None
