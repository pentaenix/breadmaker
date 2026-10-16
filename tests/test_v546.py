from __future__ import annotations
from source_contracts import frontend_source_from_app_path,frontend_styles_source_from_styles_path

from storage import context as storage_context
from storage import database as storage_database

import json
from pathlib import Path

from conftest import assert_ok


def _structural_nodes(client, canvas_id):
    data = assert_ok(client.get(f"/api/canvases/{canvas_id}"))
    return data, sorted(
        [n for n in data["nodes"] if n["node_type"] == "chapter"],
        key=lambda n: n["lane_position"] if n["lane_position"] is not None else 999999,
    )


def test_v546_structure_versions_restore_saved_free_workspace_content(client, fresh_project):
    tab = fresh_project["bootstrap"]["tabs"][0]
    assert_ok(client.post("/api/chapters", json={"title": "Chapter 2", "tab_id": tab["id"]}))
    canvas = assert_ok(client.post("/api/canvases", json={"name": "Development", "canvas_type": "developmental"}))
    versions = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))["versions"]
    v1 = versions[0]
    _, structural = _structural_nodes(client, canvas["id"])
    chapter = structural[0]

    idea = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={
        "shape": "rect", "x": 300, "y": 420, "w": 260, "h": 140,
        "title": "Shared idea", "body": "One live body", "color": "#e8e8e8"
    }))
    assert_ok(client.patch(f"/api/nodes/{chapter['id']}", json={"color": "#1f2937"}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/update", json={}))

    # Change the same free-workspace object and add material that belongs only to Version 2.
    assert_ok(client.patch(f"/api/nodes/{idea['id']}", json={"x": 760, "y": 610, "w": 330, "h": 180, "shape": "text", "color": "#f2e9b8", "title": "Version 2 idea", "body": "Version 2 body"}))
    assert_ok(client.patch(f"/api/nodes/{chapter['id']}", json={"color": "#d7eef9"}))
    extra = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={"shape": "rect", "x": 1050, "y": 720, "title": "Version 2 only"}))
    edge = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={"from_node_id": idea["id"], "to_node_id": extra["id"], "from_port": "right", "to_port": "left"}))
    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={"name": "Version 2", "from_version_id": v1["id"]}))

    # Unsaved edits after the snapshot must not leak backward into either saved Version.
    assert_ok(client.patch(f"/api/nodes/{idea['id']}", json={"title": "Unsaved current edit", "body": "Unsaved current body"}))

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    data1, structural1 = _structural_nodes(client, canvas["id"])
    idea1 = next(n for n in data1["nodes"] if n["id"] == idea["id"])
    extra1 = next(n for n in data1["nodes"] if n["id"] == extra["id"])
    edge1 = next(e for e in data1["edges"] if e["id"] == edge["id"])
    assert idea1["developmental_hidden"] == 0
    assert (idea1["x"], idea1["y"], idea1["w"], idea1["h"], idea1["shape"], idea1["color"]) == (300.0, 420.0, 260.0, 140.0, "rect", "#e8e8e8")
    assert idea1["title"] == "Shared idea"
    assert idea1["body"] == "One live body"
    assert extra1["developmental_hidden"] == 1
    assert edge1["developmental_hidden"] == 1
    assert structural1[0]["color"] == "#1f2937"

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    data2, structural2 = _structural_nodes(client, canvas["id"])
    idea2 = next(n for n in data2["nodes"] if n["id"] == idea["id"])
    extra2 = next(n for n in data2["nodes"] if n["id"] == extra["id"])
    edge2 = next(e for e in data2["edges"] if e["id"] == edge["id"])
    assert idea2["developmental_hidden"] == 0
    assert (idea2["x"], idea2["y"], idea2["w"], idea2["h"], idea2["shape"], idea2["color"]) == (760.0, 610.0, 330.0, 180.0, "text", "#f2e9b8")
    assert idea2["title"] == "Version 2 idea"
    assert idea2["body"] == "Version 2 body"
    assert extra2["developmental_hidden"] == 0
    assert edge2["developmental_hidden"] == 0
    assert structural2[0]["color"] == "#d7eef9"

    d1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v1['id']}"))
    d2 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v2['id']}"))
    assert d1["workspace"]["schema"] == 2 and d2["workspace"]["schema"] == 2
    assert next(n for n in d1["workspace"]["nodes"] if n["id"] == idea["id"])["title"] == "Shared idea"
    assert next(n for n in d2["workspace"]["nodes"] if n["id"] == idea["id"])["title"] == "Version 2 idea"
    assert extra["id"] not in {n["id"] for n in d1["workspace"]["nodes"]}
    assert extra["id"] in {n["id"] for n in d2["workspace"]["nodes"]}


def test_v546_deleting_workspace_objects_is_version_local(client, fresh_project):
    canvas = assert_ok(client.post("/api/canvases", json={"name": "Development", "canvas_type": "developmental"}))
    v1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))["versions"][0]
    a = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={"shape": "rect", "x": 200, "y": 300, "title": "A"}))
    b = assert_ok(client.post(f"/api/canvases/{canvas['id']}/nodes", json={"shape": "rect", "x": 500, "y": 300, "title": "B"}))
    edge = assert_ok(client.post(f"/api/canvases/{canvas['id']}/edges", json={"from_node_id": a["id"], "to_node_id": b["id"], "from_port": "right", "to_port": "left"}))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/update", json={}))
    v2 = assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions", json={"name": "Other", "from_version_id": v1["id"]}))

    # Delete A only from the current Version and save that Version. Version 1 must
    # still own A and its connector; Version 2 now owns the workspace without them.
    assert_ok(client.delete(f"/api/nodes/{a['id']}"))
    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/update", json={}))
    detail1 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v1['id']}"))
    detail2 = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions/{v2['id']}"))
    assert a["id"] in {n["id"] for n in detail1["workspace"].get("nodes", [])}
    assert edge["id"] in {e["id"] for e in detail1["workspace"].get("edges", [])}
    assert a["id"] not in {n["id"] for n in detail2["workspace"].get("nodes", [])}
    assert edge["id"] not in {e["id"] for e in detail2["workspace"].get("edges", [])}

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v1['id']}/activate", json={}))
    restored = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert next(n for n in restored['nodes'] if n['id'] == a['id'])['developmental_hidden'] == 0
    assert next(e for e in restored['edges'] if e['id'] == edge['id'])['developmental_hidden'] == 0

    assert_ok(client.post(f"/api/canvases/{canvas['id']}/versions/{v2['id']}/activate", json={}))
    removed = assert_ok(client.get(f"/api/canvases/{canvas['id']}"))
    assert next(n for n in removed['nodes'] if n['id'] == a['id'])['developmental_hidden'] == 1
    assert next(e for e in removed['edges'] if e['id'] == edge['id'])['developmental_hidden'] == 1


def test_v546_release_ui_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = frontend_source_from_app_path(root / "static" / "app.js")
    css = frontend_styles_source_from_styles_path((root / "static" / "styles.css"))
    assert 'id="canvasStructureBtn"' in html
    assert 'aria-label="Structure"' in html
    assert 'developmentalStructureToggle' not in html
    assert 'canvasVersionsBtn' not in html
    assert 'id="applyStructure"' not in html
    assert 'function openStructurePanel(' in js
    assert 'id="structureOrderToggle"' in js
    assert 'id="reviewStructureChanges"' in js
    assert '>Review changes<' in js
    assert '#canvasStructureBtn.active' in css


def test_v546_real_pre_workspace_schema_migrates_without_losing_versions(client, fresh_project, tmp_path):
    """A real initialized project with the three 5.4.7 columns removed upgrades in place."""
    import sqlite3
    import subprocess
    import sys
    canvas = assert_ok(client.post('/api/canvases', json={'name': 'Legacy Development', 'canvas_type': 'developmental'}))
    before = assert_ok(client.get(f"/api/canvases/{canvas['id']}/versions"))['versions']
    assert before and before[0]['name'] == 'Version 1'

    legacy_root = tmp_path / 'legacy-project'
    legacy_root.mkdir()
    legacy_db = legacy_root / 'project.sqlite3'
    with storage_database.connect() as src, sqlite3.connect(legacy_db) as dst:
        src.backup(dst)

    script = r'''
import json, sqlite3, sys
from pathlib import Path
root = Path(sys.argv[1])
db = root / 'project.sqlite3'
con = sqlite3.connect(db)
for table, column in (
    ('canvas_nodes','developmental_hidden'),
    ('canvas_edges','developmental_hidden'),
    ('canvas_versions','workspace_json'),
):
    cols = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
    if column in cols:
        con.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
con.commit(); con.close()
storage_context.configure_project(root)
storage_database.init_db()
con = storage_database.connect()
try:
    cols = {t:{r[1] for r in con.execute(f'PRAGMA table_info({t})')} for t in ('canvas_nodes','canvas_edges','canvas_versions')}
    versions = [dict(r) for r in con.execute('SELECT id,name,workspace_json FROM canvas_versions ORDER BY id')]
    print(json.dumps({'cols':{k:sorted(v) for k,v in cols.items()},'versions':versions}))
finally:
    con.close()
'''
    proc = subprocess.run(
        [sys.executable, '-c', script, str(legacy_root)],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True, capture_output=True, timeout=30, check=True,
    )
    migrated = json.loads(proc.stdout.strip().splitlines()[-1])
    assert 'developmental_hidden' in migrated['cols']['canvas_nodes']
    assert 'developmental_hidden' in migrated['cols']['canvas_edges']
    assert 'workspace_json' in migrated['cols']['canvas_versions']
    assert len(migrated['versions']) == len(before)
    assert migrated['versions'][0]['name'] == 'Version 1'
    assert migrated['versions'][0]['workspace_json'] == '{}'
