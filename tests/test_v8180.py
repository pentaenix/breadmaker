from __future__ import annotations

from storage import schema as storage_schema

from pathlib import Path

from conftest import assert_ok
from workspace import APP_SCHEMA_VERSION
from test_v8100 import _chapter, _set_blocks

ROOT=Path(__file__).resolve().parents[1]


def _round_setup(client):
    chapter=_chapter(client)
    _set_blocks(client,chapter['id'],[{'text_content':'alpha beta gamma','content_html':'alpha beta gamma'}])
    review=assert_ok(client.post('/api/reviews',json={'name':'Reversible rounds','permission_read':True,'permission_comment':True}))
    participant=assert_ok(client.post(f"/api/reviews/{review['id']}/participants",json={'display_name':'Rebeca'}))
    return review,participant


def test_v8180_schema_and_tab_order():
    assert APP_SCHEMA_VERSION==storage_schema.SCHEMA_VERSION== 42
    assert tuple(map(int,ROOT.joinpath('VERSION').read_text().strip().split('.'))) >= (8,18,0)
    html=ROOT.joinpath('static','index.html').read_text()
    assert html.index('data-view="reviews"') < html.index('data-view="tasks"')
    schema=ROOT.joinpath('src','storage','schema.py').read_text()
    assert "'closed','archived'" in schema
    assert 'closed_at TEXT' in schema
    assert 'finished_at TEXT' not in schema


def test_v8180_rounds_close_reopen_and_switch_without_recreating_baseline(client,fresh_project):
    review,participant=_round_setup(client)
    first=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    r1=first['snapshot']; cp1=list(r1['checkpoint_ids'])
    package=client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/breadreview")
    assert package.status_code==200

    closed=assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{r1['id']}/close"))
    assert closed['status']=='closed'
    assert next(r for r in closed['rounds'] if r['id']==r1['id'])['status']=='closed'
    assert client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader").status_code==409

    second=assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
    r2=second['snapshot']; cp2=list(r2['checkpoint_ids'])
    assert r2['round_number']==2 and r2['id']!=r1['id']
    assert cp2 and cp2!=cp1

    # A late file from Round 1 is still valid while Round 2 is current.
    imported=assert_ok(client.post('/api/reviews/import-breadreview',files={'file':('late.breadreview',package.content,'application/octet-stream')}))
    assert imported['review_id']==review['id'] and imported['participant_id']==participant['id']

    reopened=assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{r1['id']}/reopen"))
    rounds={row['id']:row for row in reopened['rounds']}
    assert reopened['status']=='open'
    assert reopened['snapshot']['id']==r1['id']
    assert rounds[r1['id']]['status']=='open'
    assert rounds[r2['id']]['status']=='paused'
    assert rounds[r1['id']]['checkpoint_ids']==cp1
    assert rounds[r2['id']]['checkpoint_ids']==cp2
    reader=assert_ok(client.get(f"/api/reviews/{review['id']}/participants/{participant['id']}/reader"))
    assert reader['snapshot']['id']==r1['id']

    # Closing the reopened old round reveals the paused newer round; resuming it reuses it.
    after_close=assert_ok(client.post(f"/api/reviews/{review['id']}/rounds/{r1['id']}/close"))
    assert after_close['status']=='paused' and after_close['snapshot']['id']==r2['id']
    resumed=assert_ok(client.post(f"/api/reviews/{review['id']}/resume"))
    assert resumed['status']=='open' and resumed['snapshot']['id']==r2['id']
    assert resumed['snapshot']['checkpoint_ids']==cp2
