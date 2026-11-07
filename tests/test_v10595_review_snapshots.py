"""Review snapshots remain valid across SQLite and native package round trips."""
import pytest

from conftest import assert_ok
from storage import database as storage_database
from storage import invariants as storage_invariants
from storage import mutations as storage_mutations


def _validate_project():
    with storage_database.connect() as con:
        pid=int(con.execute('SELECT id FROM projects').fetchone()[0])
        assert storage_invariants.project_invariant_errors(con,pid)==[]


@pytest.mark.parametrize('position',[0.0,2.5])
@pytest.mark.parametrize('refresh',[False,True],ids=['start','refresh'])
def test_review_scene_snapshot_survives_validate_export_import_and_reopen(client,fresh_project,position,refresh):
    project_id=fresh_project['project']['id']
    chapter=fresh_project['bootstrap']['chapters'][0]
    loaded=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks",json={
        'expected_revision':loaded['revision'],
        'blocks':[{'text_content':'Synthetic scene prose','content_html':'Synthetic scene prose'}],
    }))
    block=assert_ok(client.get(f"/api/chapters/{chapter['id']}"))['blocks'][0]
    review=assert_ok(client.post('/api/reviews',json={'name':'Scene round trip','permission_read':True}))
    if refresh:
        assert_ok(client.post(f"/api/reviews/{review['id']}/start"))
        assert_ok(client.post(f"/api/reviews/{review['id']}/pause"))
    scene=assert_ok(client.post('/api/scenes',json={'title':'Synthetic scene','block_ids':[block['id']]}))
    # SQLite's live Scene column has REAL affinity, including ordinary whole positions.
    # Fractional positions must remain exact rather than being truncated in the snapshot.
    with storage_mutations.tx(operation='test-review-scene-position') as con:
        con.execute('UPDATE scenes SET position=? WHERE id=?',(position,scene['id']))
        assert isinstance(con.execute('SELECT position FROM scenes WHERE id=?',(scene['id'],)).fetchone()[0],float)
    endpoint='checkpoint/update' if refresh else 'start'
    started=assert_ok(client.post(f"/api/reviews/{review['id']}/{endpoint}"))
    frozen=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert frozen['scenes'][0]['position']==position
    assert frozen['scenes'][0]['source_scene_id']==scene['id']
    assert frozen['blocks'][0]['text_content']=='Synthetic scene prose'
    _validate_project()
    assert_ok(client.post(f'/api/projects/{project_id}/activate'))
    package=client.get(f'/api/projects/{project_id}/export')
    assert package.status_code==200,package.text
    imported=assert_ok(client.post('/api/projects/import',files={
        'file':('review-scene.breadmaker',package.content,'application/octet-stream'),
    }))
    imported_id=imported['project']['id']
    assert imported_id!=project_id
    assert_ok(client.post(f'/api/projects/{project_id}/activate'))
    assert_ok(client.post(f'/api/projects/{imported_id}/activate'))
    reopened=assert_ok(client.get(f"/api/reviews/{review['id']}/snapshot"))
    assert reopened['id']==started['snapshot']['id']
    assert reopened['content_hash']==frozen['content_hash']
    assert reopened['scenes']==frozen['scenes']
    assert reopened['blocks']==frozen['blocks']
    _validate_project()
    # Hash validation must still reject a real change to the frozen Scene.
    with storage_mutations.tx(operation='test-review-scene-tamper') as con:
        con.execute('UPDATE review_snapshot_scenes SET position=position+0.25 WHERE snapshot_id=?',(frozen['id'],))
        pid=int(con.execute('SELECT id FROM projects').fetchone()[0])
        errors=storage_invariants.project_invariant_errors(con,pid)
        assert any('content hash does not match its frozen content' in error for error in errors)
        con.execute('UPDATE review_snapshot_scenes SET position=? WHERE snapshot_id=?',(position,frozen['id']))
