from __future__ import annotations

from storage import invariants as storage_invariants
from storage import mutations as storage_mutations
from storage import schema as storage_schema
from storage import schema_contracts as storage_schema_contracts

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION


def _manuscript_chapters(client, count=4):
    boot=assert_ok(client.get('/api/bootstrap'))
    tab=next(row for row in boot['tabs'] if row['is_manuscript'])
    chapters=[row for row in boot['chapters'] if not row['is_implicit'] and row['tab_id']==tab['id']]
    while len(chapters)<count:
        created=assert_ok(client.post('/api/chapters',json={'title':f'Chapter {len(chapters)+1}','tab_id':tab['id']}))
        chapters.append(created)
    return chapters


def _start(client,name,scope_type='manuscript',scope_ids=None):
    review=assert_ok(client.post('/api/reviews',json={
        'name':name,'scope_type':scope_type,'scope_ids':scope_ids or [],'permission_read':True,
    }))
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    return review,started


def test_v8173_review_checkpoint_scope_thresholds_and_persistence(client,fresh_project):
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION and APP_SCHEMA_VERSION>=37
    chapters=_manuscript_chapters(client,4)

    whole_review,whole=_start(client,'Whole')
    assert whole['snapshot']['checkpoint_count']==1
    assert [cp['scope'] for cp in whole['snapshot']['checkpoints']]==['book']

    one_review,one=_start(client,'One','chapters',[chapters[0]['id']])
    assert one['snapshot']['checkpoint_count']==1
    assert one['snapshot']['checkpoints'][0]['scope']=='chapter'
    assert one['snapshot']['checkpoints'][0]['source_chapter_id']==chapters[0]['id']

    three_review,three=_start(client,'Three','chapters',[c['id'] for c in chapters[:3]])
    assert three['snapshot']['checkpoint_count']==3
    assert [cp['scope'] for cp in three['snapshot']['checkpoints']]==['chapter','chapter','chapter']
    assert [cp['source_chapter_id'] for cp in three['snapshot']['checkpoints']]==[c['id'] for c in chapters[:3]]

    four_review,four=_start(client,'Four','chapters',[c['id'] for c in chapters[:4]])
    assert four['snapshot']['checkpoint_count']==1
    assert [cp['scope'] for cp in four['snapshot']['checkpoints']]==['book']

    all_checkpoint_ids=set(whole['snapshot']['checkpoint_ids']+one['snapshot']['checkpoint_ids']+three['snapshot']['checkpoint_ids']+four['snapshot']['checkpoint_ids'])
    listed=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    assert all_checkpoint_ids <= {row['id'] for row in listed}

    # Review deletion removes only the links/snapshot, never the actual checkpoint objects.
    three_ids=list(three['snapshot']['checkpoint_ids'])
    assert_ok(client.delete(f"/api/reviews/{three_review['id']}"))
    remaining={row['id'] for row in assert_ok(client.get('/api/checkpoints'))['checkpoints']}
    assert set(three_ids) <= remaining

    with storage_mutations.tx(track_generation=False) as con:
        pid=int(con.execute('SELECT id FROM projects LIMIT 1').fetchone()[0])
        assert storage_invariants.project_invariant_errors(con,pid)==[]
        assert storage_schema_contracts.history_schema_classification(con)['unclassified']==[]
        assert storage_schema_contracts.generation_schema_classification(con)['unclassified']==[]


def test_v8173_paused_update_reuses_all_scoped_checkpoint_ids(client,fresh_project):
    chapters=_manuscript_chapters(client,3)
    review,started=_start(client,'Three','chapters',[c['id'] for c in chapters[:3]])
    before=list(started['snapshot']['checkpoint_ids'])
    assert len(before)==3
    count_before=len(assert_ok(client.get('/api/checkpoints'))['checkpoints'])
    assert_ok(client.post(f"/api/reviews/{review['id']}/pause"))
    updated=assert_ok(client.post(f"/api/reviews/{review['id']}/checkpoint/update"))
    assert updated['snapshot']['checkpoint_ids']==before
    assert len(assert_ok(client.get('/api/checkpoints'))['checkpoints'])==count_before
