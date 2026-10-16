from __future__ import annotations

from storage import context as storage_context
from storage import database as storage_database

import json
import os
import random
import sqlite3
import subprocess
import sys
import time

import pytest
from pathlib import Path

from conftest import assert_ok


def test_duplicate_phrase_annotation_reanchors_to_original_occurrence(client, fresh_project):
    ch=fresh_project["bootstrap"]["chapters"][0]
    text="target one target omega"
    block_id=assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"text_content":text,"content_html":text}]}))["ids"][0]
    second=text.rindex("target")
    annotation=assert_ok(client.post("/api/annotations/batch",json={"annotation_type":"highlight","items":[{"block_id":block_id,"start_offset":second,"end_offset":second+6,"selected_text":"target","prefix_context":"target one ","suffix_context":" omega"}]}))["ids"][0]
    new="prefix "+text
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"id":block_id,"text_content":new,"content_html":new}]}))
    rows=assert_ok(client.get("/api/annotations?status=all")); a=next(x for x in rows if x["id"]==annotation)
    assert a["stale"]==0
    assert a["start_offset"]==new.rindex("target")


def test_annotation_batch_is_all_or_nothing(client, fresh_project):
    ch=fresh_project["bootstrap"]["chapters"][0]
    block_id=assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"text_content":"safe","content_html":"safe"}]}))["ids"][0]
    before=len(assert_ok(client.get("/api/annotations?status=all")))
    response=client.post("/api/annotations/batch",json={"annotation_type":"highlight","items":[
        {"block_id":block_id,"start_offset":0,"end_offset":2,"selected_text":"sa"},
        {"block_id":"missing-block","start_offset":0,"end_offset":1,"selected_text":"x"}
    ]})
    assert response.status_code in (400,409)
    after=len(assert_ok(client.get("/api/annotations?status=all")))
    assert after==before


def test_session_undo_delta_does_not_pollute_visible_history(client, fresh_project):
    before=assert_ok(client.get("/api/history/v2"))["revisions"]
    chapter=fresh_project["bootstrap"]["chapters"][0]
    token=assert_ok(client.post("/api/undo/begin?label=Change%20chapter"))["token"]
    assert_ok(client.patch(f"/api/chapters/{chapter['id']}/title",json={"title":"Undo journal title"}))
    assert_ok(client.post(f"/api/undo/finish/{token}"))
    assert_ok(client.post(f"/api/undo/apply/{token}?direction=undo"))
    after=assert_ok(client.get("/api/history/v2"))["revisions"]
    assert [x["id"] for x in after]==[x["id"] for x in before]


def test_process_lock_rejects_second_server_owner(client, fresh_project, app_module_fixture):
    root=Path(storage_context.PROJECT_ROOT)
    code="""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storage import context as storage_context
from storage import database as storage_database
storage_context.configure_project(Path(sys.argv[2]))
try:
    storage_database.acquire_instance_lock()
except Exception as e:
    print(type(e).__name__+':'+str(e))
    raise SystemExit(0)
raise SystemExit(9)
"""
    proc=subprocess.run([sys.executable,"-c",code,str(Path(app_module_fixture.__file__).parent),str(root)],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0
    assert "already using" in proc.stdout


def test_crash_before_commit_rolls_back_and_commit_before_crash_survives(client, fresh_project, app_module_fixture):
    ch=fresh_project["bootstrap"]["chapters"][0]
    bid=assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"text_content":"original","content_html":"original"}]}))["ids"][0]
    db=Path(storage_context.DB_PATH)
    uncommitted="""
import sqlite3,sys,time
con=sqlite3.connect(sys.argv[1]); con.execute('PRAGMA journal_mode=WAL'); con.execute('BEGIN IMMEDIATE'); con.execute("UPDATE manuscript_blocks SET text_content='uncommitted',content_html='uncommitted' WHERE id=?",(sys.argv[2],)); time.sleep(30)
"""
    p=subprocess.Popen([sys.executable,"-c",uncommitted,str(db),bid])
    time.sleep(.4); p.kill(); p.wait(timeout=5)
    data=assert_ok(client.get(f"/api/chapters/{ch['id']}")); assert data["blocks"][0]["text_content"]=="original"
    committed="""
import os,sqlite3,sys
con=sqlite3.connect(sys.argv[1]); con.execute('PRAGMA journal_mode=WAL'); con.execute("UPDATE manuscript_blocks SET text_content='committed',content_html='committed' WHERE id=?",(sys.argv[2],)); con.commit(); os._exit(0)
"""
    subprocess.run([sys.executable,"-c",committed,str(db),bid],check=True,timeout=5)
    data=assert_ok(client.get(f"/api/chapters/{ch['id']}")); assert data["blocks"][0]["text_content"]=="committed"


def test_hostile_origin_is_rejected_and_browser_lease_blocks_stale_tab(client, fresh_project):
    blocked=client.post("/api/tasks",json={"title":"bad"},headers={"Origin":"https://evil.example"})
    assert blocked.status_code==403
    first={"x-breadmaker-client":"tab-one","Origin":"http://localhost:8765"}
    second={"x-breadmaker-client":"tab-two","Origin":"http://localhost:8765"}
    assert client.post("/api/lease",headers=first).status_code==200
    blocked2=client.post("/api/tasks",json={"title":"stale"},headers=second)
    assert blocked2.status_code==409
    assert client.post("/api/lease/release",headers=first).status_code==200
    assert client.post("/api/tasks",json={"title":"new owner"},headers=second).status_code==200
    client.post("/api/lease/release",headers=second)


@pytest.mark.parametrize('seed',[8126,8127,8128,8129,8130,8131,8132,8133,8134,8135])
def test_randomized_structural_operations_preserve_database_invariants(seed,client,fresh_project):
    """Bounded structural soak shards: 200 total mutations, each under 10 seconds.

    Keep a tiny local identity model rather than re-downloading the full bootstrap on
    structural actions. Ten independent 20-operation shards preserve 200 total
    randomized mutations. Project health is checked once mid-shard and once at the
    end; repeatedly running the full health inventory after ordinary mutations tests
    health-scan cost rather than structural correctness and erodes timeout headroom.
    """
    rng=random.Random(seed)
    boot=fresh_project["bootstrap"]
    chapters=list(boot["chapters"])
    canvases=list(boot["canvases"])
    tab_id=boot["tabs"][0]["id"]
    actions=[]

    def assert_healthy(step: int):
        health=assert_ok(client.get("/api/project/health"))
        database=health["database"]
        if not database["ok"]:
            errors=database.get("deep_errors") or [database.get("error") or "unspecified database failure"]
            # Keep the digest actionable: the health API already supplied
            # the detailed cause, but a plain dict assertion hid it under
            # truncated pytest/verification output. Do not retry or ignore it.
            first=str(errors[0]).replace("\n", " ")[:280]
            recent=','.join(actions[-8:])
            pytest.fail(
                f"BREADMAKER_HEALTH_INVARIANT seed={seed} step={step} "
                f"error={first} recent_actions={recent}",
                pytrace=False,
            )

    for step in range(20):
        action=rng.choice(["chapter","canvas","node","task","map","marker","document"])
        actions.append(f"{step}:{action}")
        if action=="chapter":
            if rng.random()<.65 or len(chapters)<2:
                created=assert_ok(client.post("/api/chapters",json={"title":f"C{seed}-{step}","tab_id":tab_id}))
                chapters.append(created)
            else:
                ch=rng.choice(chapters)
                renamed=assert_ok(client.patch(f"/api/chapters/{ch['id']}/title",json={"title":f"Renamed {seed}-{step}"}))
                ch.update(renamed)
        elif action=="canvas":
            parent=rng.choice(canvases)["id"] if canvases and rng.random()<.4 else None
            created=assert_ok(client.post("/api/canvases",json={"name":f"Canvas {seed}-{step}","parent_id":parent}))
            canvases.append(created)
        elif action=="node" and canvases:
            cv=rng.choice(canvases)
            assert_ok(client.post(f"/api/canvases/{cv['id']}/nodes",json={"shape":rng.choice(["rect","text"]),"title":f"N{seed}-{step}","x":rng.randrange(0,900),"y":rng.randrange(0,700)}))
        elif action=="task": assert_ok(client.post("/api/tasks",json={"title":f"Task {seed}-{step}","status":rng.choice(["open","later","done","archived"])}))
        elif action=="map": assert_ok(client.post("/api/maps",json={"name":f"Map {seed}-{step}","orientation":rng.choice(["horizontal","vertical"]),"layout_mode":rng.choice(["schematic","scaled"])}))
        elif action=="marker": assert_ok(client.post("/api/labels",json={"name":f"M{seed}-{step}","color":f"#{rng.randrange(0,0xFFFFFF):06x}"}))
        elif action=="document": assert_ok(client.post("/api/documents",json={"title":f"Notes {seed}-{step}","is_manuscript":False}))
        if step==9:
            assert_healthy(step)
    assert_healthy(19)

