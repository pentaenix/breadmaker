from __future__ import annotations
from conftest import assert_ok


def _blocks(client, chapter_id, texts):
    return assert_ok(client.put(f"/api/chapters/{chapter_id}/blocks",json={"blocks":[{"text_content":t,"content_html":t,"semantic_style":"body"} for t in texts]}))["ids"]


def test_scene_can_span_chapters_and_remains_independent_of_headers(client, fresh_project):
    boot=fresh_project["bootstrap"]; c1=boot["chapters"][0]
    c2=assert_ok(client.post("/api/chapters",json={"title":"Chapter 2","tab_id":c1["tab_id"]}))
    b1=_blocks(client,c1["id"],["one","two"]); b2=_blocks(client,c2["id"],["three","four"])
    scene=assert_ok(client.post("/api/scenes",json={"title":"Crossing","block_ids":[b1[1],b2[0]]}))
    boot2=assert_ok(client.get('/api/bootstrap')); saved=next(x for x in boot2['scenes'] if x['id']==scene['id'])
    assert saved['chapter_count']==2 and saved['block_count']==2
    assert client.post(f"/api/scenes/{scene['id']}/move",json={"target_chapter_id":c2["id"]}).status_code==409
    assert assert_ok(client.get(f"/api/chapters/{c2['id']}"))['chapter']['title']=='Chapter 2'


def test_find_replace_literal_and_whole_word(client, fresh_project):
    ch=fresh_project["bootstrap"]["chapters"][0]
    _blocks(client,ch["id"],["C++ and C+ and cat concatenate cat.","cat"])
    base={"query":"cat","case_sensitive":False,"whole_word":True,"scope":"chapter","chapter_id":ch["id"],"tab_id":ch["tab_id"]}
    found=assert_ok(client.post("/api/find",json=base))
    assert found["count"]==3
    preview=assert_ok(client.post("/api/replace",json={**base,"replacement":"dog\\1","preview":True}))
    assert preview["count"]==3
    done=assert_ok(client.post("/api/replace",json={**base,"replacement":"dog\\1","preview":False}))
    assert done["count"]==3
    data=assert_ok(client.get(f"/api/chapters/{ch['id']}"))
    text=" ".join(b["text_content"] for b in data["blocks"])
    assert "dog\\1" in text
    assert "concatenate" in text


def test_universal_task_link_to_manuscript_range(client, fresh_project):
    ch=fresh_project["bootstrap"]["chapters"][0]
    ids=_blocks(client,ch["id"],["Explain the kingdom here."])
    task=assert_ok(client.post("/api/tasks",json={"title":"Explain kingdom","status":"later"}))
    ref=assert_ok(client.post("/api/references",json={"source_type":"task","source_id":str(task["id"]),"target_type":"manuscript_range","target_id":"range","start_block_id":ids[0],"end_block_id":ids[0],"start_offset":0,"end_offset":7,"excerpt":"Explain"}))
    rows=assert_ok(client.get(f"/api/references?source_type=task&source_id={task['id']}"))
    assert rows[0]["id"]==ref["id"] and rows[0]["target_type"]=="manuscript_range"
    tasks=assert_ok(client.get("/api/tasks")); row=next(t for t in tasks if t["id"]==task["id"])
    assert row["reference_count"]==1 and row["status"]=="later"


def test_revision_restore_preserves_tasks_links_cuts_and_canvas(client, fresh_project):
    ch=fresh_project["bootstrap"]["chapters"][0]
    ids=_blocks(client,ch["id"],["anchor text"])
    task=assert_ok(client.post("/api/tasks",json={"title":"Keep me"}))
    assert_ok(client.post("/api/references",json={"source_type":"task","source_id":str(task["id"]),"target_type":"manuscript_range","target_id":"range","start_block_id":ids[0],"end_block_id":ids[0],"excerpt":"anchor"}))
    assert_ok(client.post("/api/cuts",json={"title":"Old line","content_html":"<p>cut prose</p>","source_chapter_id":ch["id"]}))
    canvas=assert_ok(client.post("/api/canvases",json={"name":"Plan","canvas_type":"blank"}))
    revision=assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str("Preserve all"))))
    assert_ok(client.delete(f"/api/tasks/{task['id']}")); assert_ok(client.delete(f"/api/canvases/{canvas['id']}"))
    current_generation=assert_ok(client.get('/api/history/v2'))['current_generation']
    assert_ok(client.post(f"/api/history/v2/revisions/{revision['id']}/restore",json={'expected_generation':current_generation}))
    assert any(t["title"]=="Keep me" for t in assert_ok(client.get("/api/tasks")))
    assert assert_ok(client.get("/api/references?source_type=task"))
    assert any(c["title"]=="Old line" for c in assert_ok(client.get("/api/cuts")))
    assert any(c["name"]=="Plan" for c in assert_ok(client.get("/api/bootstrap"))["canvases"])
