from conftest import assert_ok


def _save(client, chapter_id, text):
    return assert_ok(client.put(
        f'/api/chapters/{chapter_id}/blocks',
        json={'blocks':[{'block_type':'paragraph','semantic_style':'body','content_html':text,'text_content':text}]}
    ))


def _text(client, chapter_id):
    blocks=assert_ok(client.get(f'/api/chapters/{chapter_id}'))['blocks']
    return ' '.join(b['text_content'] for b in blocks)


def test_checkpoint_restore_is_repeatable_and_never_consumes_or_forks_checkpoints(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]

    # A B C -> Checkpoint 1
    _save(client,chapter['id'],'A B C')
    cp1=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Checkpoint 1'}))

    # B C A -> Checkpoint 2
    _save(client,chapter['id'],'B C A')
    cp2=assert_ok(client.post('/api/checkpoints',json={'scope':'chapter','chapter_id':chapter['id'],'name':'Checkpoint 2'}))
    hashes={cp1['id']:cp1['snapshot_hash'],cp2['id']:cp2['snapshot_hash']}

    # Restoring 1 returns the live manuscript to A B C, but neither saved version changes.
    result=assert_ok(client.post(f"/api/checkpoints/{cp1['id']}/apply",json={'target_chapter_id':chapter['id']}))
    live_id=result['chapter_id']
    assert _text(client,live_id)=='A B C'
    rows=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    assert {x['id'] for x in rows}=={cp1['id'],cp2['id']}
    assert {x['id']:x['snapshot_hash'] for x in rows}==hashes

    # Branch in a third direction without creating a checkpoint.
    _save(client,live_id,'A D C')

    # Checkpoint 2 is still independently restorable.
    result=assert_ok(client.post(f"/api/checkpoints/{cp2['id']}/apply",json={'target_chapter_id':chapter['id']}))
    live_id=result['chapter_id']
    assert _text(client,live_id)=='B C A'

    # And Checkpoint 1 is still independently restorable again.
    result=assert_ok(client.post(f"/api/checkpoints/{cp1['id']}/apply",json={'target_chapter_id':chapter['id']}))
    live_id=result['chapter_id']
    assert _text(client,live_id)=='A B C'

    final=assert_ok(client.get('/api/checkpoints'))['checkpoints']
    assert {x['id'] for x in final}=={cp1['id'],cp2['id']}
    assert {x['id']:x['snapshot_hash'] for x in final}==hashes
    history=assert_ok(client.get('/api/history'))['revisions']
    restore_safety=[x for x in history if 'Before applying checkpoint' in x['label']]
    assert len(restore_safety)>=3


def test_cuts_are_independent_of_history_restore_and_explicit_deletion_is_not_undone(client, fresh_project):
    chapter=fresh_project['bootstrap']['chapters'][0]
    first=assert_ok(client.post('/api/cuts',json={'title':'Cut A','content_html':'<p>A</p>','source_chapter_id':chapter['id']}))
    revision=assert_ok(client.post('/api/autocommit?label=Before+later+cut'))['id']
    second=assert_ok(client.post('/api/cuts',json={'title':'Cut B','content_html':'<p>B</p>','source_chapter_id':chapter['id']}))

    # Restoring an older project History state must not rewind the Cuts library.
    assert_ok(client.post(f'/api/revisions/{revision}/restore'))
    assert {x['id'] for x in assert_ok(client.get('/api/cuts'))}=={first['id'],second['id']}

    # Explicitly deleting a Cut is permanent; History does not resurrect it.
    assert_ok(client.delete(f"/api/cuts/{second['id']}"))
    assert_ok(client.post(f'/api/revisions/{revision}/restore'))
    assert {x['id'] for x in assert_ok(client.get('/api/cuts'))}=={first['id']}


def test_named_history_creation_endpoint_is_removed(client, fresh_project):
    response=client.post('/api/revisions',json={'label':'This should be a Checkpoint instead'})
    assert response.status_code==404


def test_history_is_rolling_and_prunes_unreachable_revision_content(client, fresh_project, app_module_fixture):
    from datetime import datetime, timedelta, timezone
    storage=app_module_fixture.storage
    chapter=fresh_project['bootstrap']['chapters'][0]
    # Generate a revision with a unique paragraph payload, then age it beyond retention.
    _save(client,chapter['id'],'Ancient history payload that should be collected.')
    old_id=assert_ok(client.post('/api/autocommit?label=Ancient'))['id']
    _save(client,chapter['id'],'Current payload that must remain.')
    current_id=assert_ok(client.post('/api/autocommit?label=Current'))['id']
    with storage.tx() as con:
        ancient=(datetime.now(timezone.utc)-timedelta(days=45)).isoformat(timespec='microseconds')
        # More than 200 newer rows ensures the "always keep newest 200" floor does not retain Ancient.
        con.execute('UPDATE revisions SET created_at=? WHERE id=?',(ancient,old_id))
        tree=storage.revision_tree(con,fresh_project['project']['id'] if isinstance(fresh_project['project']['id'],int) else 1)
        raw=__import__('json').dumps(tree,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        for i in range(205):
            con.execute('INSERT INTO revisions(project_id,label,kind,tree_hash,tree_json,created_at) VALUES(?,?,?,?,?,?)',(1,f'newer {i}','auto',storage.sha(raw+str(i)),raw,storage.now()))
        before=con.execute('SELECT COUNT(*) FROM content_objects').fetchone()[0]
        storage._prune_revisions(con,1)
        assert con.execute('SELECT 1 FROM revisions WHERE id=?',(old_id,)).fetchone() is None
        assert con.execute('SELECT 1 FROM revisions WHERE id=?',(current_id,)).fetchone() is not None
        after=con.execute('SELECT COUNT(*) FROM content_objects').fetchone()[0]
        assert after<=before
