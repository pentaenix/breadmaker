from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import mutations as storage_mutations
from storage import schema as storage_schema

import io
from pathlib import Path

from PIL import Image
from docx import Document

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]


def _png(seed:int)->bytes:
    out=io.BytesIO();Image.new('RGBA',(48,32),(30*seed%255,70*seed%255,120*seed%255,180)).save(out,format='PNG');return out.getvalue()


def _asset(client,seed:int)->str:
    return assert_ok(client.post('/api/assets',files={'file':(f'row-{seed}.png',_png(seed),'image/png')}))['path']


def test_v81912_schema_and_row_contract_are_deliberately_constrained():
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION== 42
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,19,12)
    schema=ROOT.joinpath('src','storage','schema.py').read_text()
    js=frontend_source_from_app_path(ROOT.joinpath('static','app.js'))
    assert "image_row_id TEXT NOT NULL DEFAULT ''" in schema
    assert 'members.length<4' in js
    assert "'row_before'" in js and "'row_after'" in js
    assert "const placement=imageDropPlacement(wrap,event);return{target_block_id:wrap.dataset.blockId,target_wrap:wrap,placement,offset:0}" in js
    # Pasting beside is intentionally impossible: paste resolves below the last row member.
    assert "const last=imageRowWraps(selectedRow).at(-1)" in js
    assert "placement:'after'" in js


def test_v81912_image_rows_share_width_cap_at_four_and_persist_to_snapshots_and_exports(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    target=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks'][0]
    assets=[_asset(client,i) for i in range(1,6)]
    first=assert_ok(client.post('/api/manuscript/images/insert',json={
        'image_path':assets[0],'target_block_id':target['id'],'placement':'after','image_width':82,
        'image_footer':'A','image_description':'First comparison image',
    }))
    ids=[first['block_id']]
    for index in range(1,4):
        inserted=assert_ok(client.post('/api/manuscript/images/insert',json={
            'image_path':assets[index],'target_block_id':ids[-1],'placement':'row_after','image_width':25,
            'image_footer':chr(65+index),'image_description':f'Comparison image {index+1}',
        }))
        ids.append(inserted['block_id'])
    detail=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))
    images=[b for b in detail['blocks'] if b['id'] in ids]
    row_ids={b['image_row_id'] for b in images}
    assert len(row_ids)==1 and next(iter(row_ids))
    assert {b['image_width'] for b in images}=={82}

    too_many=client.post('/api/manuscript/images/insert',json={
        'image_path':assets[4],'target_block_id':ids[-1],'placement':'row_after',
    })
    assert too_many.status_code==409

    checkpoint=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Image row'}))
    snap=assert_ok(client.get(f'/api/checkpoints/{checkpoint["id"]}'))['snapshot']
    cp=[b for b in snap['blocks'] if b.get('block_type')=='image']
    assert len(cp)==4 and len({b['image_row_id'] for b in cp})==1 and {b['image_width'] for b in cp}=={82}
    comparison=assert_ok(client.get(f'/api/checkpoints/compare/versions?left={checkpoint["id"]}&right=current'))
    assert any('readonly-image-row checkpoint-image-row' in str(row.get('left') or '') for row in comparison['rows'])

    review=assert_ok(client.post('/api/reviews',json={'name':'Image row review','permission_read':True}))
    started=assert_ok(client.post(f'/api/reviews/{review["id"]}/start'))
    with storage_mutations.tx(track_generation=False) as con:
        rv=[dict(row) for row in con.execute('SELECT image_row_id,image_width FROM review_snapshot_blocks WHERE snapshot_id=? AND block_type=\'image\' ORDER BY ordinal',(started['snapshot']['id'],))]
    assert len(rv)==4 and len({row['image_row_id'] for row in rv})==1 and {row['image_width'] for row in rv}=={82}

    docx=client.get('/api/export/docx');assert docx.status_code==200
    doc=Document(io.BytesIO(docx.content))
    assert any(len(table.columns)==4 for table in doc.tables)
    pdf=client.post('/api/export/pdf',json={'cover':False,'title':'Rows','page_numbering':False,'header':'','watermark':'','font_size':11,'font_type':'book-serif','page_size':'letter','chapter_page_breaks':True})
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')


def test_v81912_dragging_out_of_a_row_separates_it_without_losing_other_members(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    target=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks'][0]
    a=assert_ok(client.post('/api/manuscript/images/insert',json={'image_path':_asset(client,11),'target_block_id':target['id'],'placement':'after','image_width':64}))['block_id']
    b=assert_ok(client.post('/api/manuscript/images/insert',json={'image_path':_asset(client,12),'target_block_id':a,'placement':'row_after'}))['block_id']
    c=assert_ok(client.post('/api/manuscript/images/insert',json={'image_path':_asset(client,13),'target_block_id':b,'placement':'row_after'}))['block_id']
    assert_ok(client.post(f'/api/manuscript/images/{b}/move',json={'target_block_id':target['id'],'placement':'before'}))
    detail=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks']
    by={row['id']:row for row in detail}
    assert by[b]['image_row_id']==''
    assert by[a]['image_row_id'] and by[a]['image_row_id']==by[c]['image_row_id']
    assert by[a]['image_width']==by[c]['image_width']==64


def test_v81912_chapter_cut_roundtrips_image_row_relationship(client,fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    target=assert_ok(client.get(f'/api/chapters/{chapter["id"]}'))['blocks'][0]
    a=assert_ok(client.post('/api/manuscript/images/insert',json={
        'image_path':_asset(client,21),'target_block_id':target['id'],'placement':'after','image_width':71,
        'image_footer':'Before','image_description':'Before image',
    }))['block_id']
    b=assert_ok(client.post('/api/manuscript/images/insert',json={
        'image_path':_asset(client,22),'target_block_id':a,'placement':'row_after',
        'image_footer':'After','image_description':'After image',
    }))['block_id']
    assert_ok(client.delete(f'/api/chapters/{chapter["id"]}'))
    cuts=assert_ok(client.get('/api/cuts'));cut=next(row for row in cuts if row['title'].startswith('Cut:'))
    assert cut['content_html'].count('data-row=')==2
    assert cut['content_html'].count('data-width="71"')==2

    bootstrap=assert_ok(client.get('/api/bootstrap'))
    replacement=bootstrap['chapters'][0]
    block=assert_ok(client.get(f'/api/chapters/{replacement["id"]}'))['blocks'][0]
    assert_ok(client.post(f'/api/cuts/{cut["id"]}/insert',json={'chapter_id':replacement['id'],'block_id':block['id'],'offset':0}))
    restored=assert_ok(client.get(f'/api/chapters/{replacement["id"]}'))['blocks']
    images=[row for row in restored if row['block_type']=='image']
    assert len(images)==2 and images[0]['image_row_id']==images[1]['image_row_id'] and images[0]['image_row_id']
    assert {row['image_width'] for row in images}=={71}
    assert [row['image_footer'] for row in images]==['Before','After']
