from __future__ import annotations
import io, json, zipfile
from conftest import assert_ok


def test_multiple_projects_are_isolated_and_switchable(client, fresh_project):
    first = fresh_project["project"]
    boot = fresh_project["bootstrap"]
    chapter = boot["chapters"][0]
    assert_ok(client.put(f"/api/chapters/{chapter['id']}/blocks", json={"blocks":[{"block_type":"paragraph","text_content":"Book A secret","content_html":"Book A secret"}]}))

    second = assert_ok(client.post("/api/projects", json={"name":"Second Book"}))["project"]
    boot2 = assert_ok(client.get("/api/bootstrap"))
    ch2 = boot2["chapters"][0]
    chapter2 = assert_ok(client.get(f"/api/chapters/{ch2['id']}"))
    assert "Book A secret" not in " ".join(b["text_content"] for b in chapter2["blocks"])

    assert_ok(client.post(f"/api/projects/{first['id']}/activate"))
    boot1 = assert_ok(client.get("/api/bootstrap"))
    ch1 = boot1["chapters"][0]
    chapter1 = assert_ok(client.get(f"/api/chapters/{ch1['id']}"))
    assert "Book A secret" in " ".join(b["text_content"] for b in chapter1["blocks"])
    assert first["id"] != second["id"]


def test_export_import_package_is_verified_and_independent(client, fresh_project):
    project=fresh_project["project"]
    ch=fresh_project["bootstrap"]["chapters"][0]
    assert_ok(client.put(f"/api/chapters/{ch['id']}/blocks",json={"blocks":[{"text_content":"Portable manuscript","content_html":"Portable manuscript"}]}))
    exported=client.get(f"/api/projects/{project['id']}/export")
    assert exported.status_code==200
    assert exported.content[:2]==b"PK"
    package=io.BytesIO(exported.content)
    with zipfile.ZipFile(package) as z:
        manifest=json.loads(z.read("manifest.json"))
        assert manifest["package_format"]=="breadmaker-project"
        assert "project.sqlite3" in manifest["file_hashes"]
    files={"file":("copy.breadmaker",exported.content,"application/octet-stream")}
    imported=client.post("/api/projects/import",files=files)
    assert imported.status_code==200, imported.text
    boot=assert_ok(client.get("/api/bootstrap")); ch2=boot["chapters"][0]
    data=assert_ok(client.get(f"/api/chapters/{ch2['id']}"))
    assert any(b["text_content"]=="Portable manuscript" for b in data["blocks"])
    assert imported.json()["project"]["id"] != project["id"]


def test_tampered_project_package_is_rejected(client, fresh_project):
    project=fresh_project["project"]
    active_before=assert_ok(client.get("/api/workspace"))["active_project_id"]
    exported=client.get(f"/api/projects/{project['id']}/export").content
    src=zipfile.ZipFile(io.BytesIO(exported))
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            data=src.read(name)
            if name=="project.sqlite3": data=data+b"tamper"
            z.writestr(name,data)
    response=client.post("/api/projects/import",files={"file":("bad.breadmaker",out.getvalue(),"application/octet-stream")})
    assert response.status_code==400
    assert "HASH_MISMATCH" in response.text
    assert assert_ok(client.get("/api/workspace"))["active_project_id"]==active_before


def test_recovery_restores_as_new_project(client, fresh_project):
    original=fresh_project["project"]["id"]
    result=assert_ok(client.post("/api/backup"))
    rows=assert_ok(client.get("/api/backups"))
    match=next(x for x in rows if x["file"]==result["file"])
    assert match["verified"] is True
    recovered=assert_ok(client.post(f"/api/backups/{match['file']}/restore-as-project"))["project"]
    assert recovered["id"]!=original
    assert "Recovered" in recovered["name"]

def test_package_requires_complete_hash_manifest_and_expected_files(client, fresh_project):
    project=fresh_project['project']
    exported=client.get(f"/api/projects/{project['id']}/export").content
    src=zipfile.ZipFile(io.BytesIO(exported))

    missing_hash=io.BytesIO()
    with zipfile.ZipFile(missing_hash,'w',zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist():
            data=src.read(name)
            if name=='manifest.json':
                manifest=json.loads(data); manifest['file_hashes'].pop('project.sqlite3',None)
                data=json.dumps(manifest).encode()
            z.writestr(name,data)
    response=client.post('/api/projects/import',files={'file':('missing-hash.breadmaker',missing_hash.getvalue(),'application/octet-stream')})
    assert response.status_code==400 and 'HASH_SET_MISMATCH' in response.text

    extra=io.BytesIO()
    with zipfile.ZipFile(extra,'w',zipfile.ZIP_DEFLATED) as z:
        for name in src.namelist(): z.writestr(name,src.read(name))
        z.writestr('unexpected.txt',b'not part of the project format')
    response=client.post('/api/projects/import',files={'file':('extra.breadmaker',extra.getvalue(),'application/octet-stream')})
    assert response.status_code==400 and 'UNEXPECTED_FILE' in response.text


def test_history_restore_never_reverts_workspace_project_name(client, fresh_project):
    project=fresh_project['project']
    revision=assert_ok(client.post('/api/autocommit?label='+__import__('urllib.parse').parse.quote(str('Before rename'))))['id']
    renamed=assert_ok(client.patch(f"/api/projects/{project['id']}",json={'name':'Current Professional Title'}))
    assert renamed['name']=='Current Professional Title'
    assert assert_ok(client.get('/api/bootstrap'))['project']['title']=='Current Professional Title'
    current_generation=assert_ok(client.get('/api/history/v2'))['current_generation']
    assert_ok(client.post(f'/api/history/v2/revisions/{revision}/restore',json={'expected_generation':current_generation}))
    # History is manuscript/project-content history. The workspace identity is not
    # historical content and restoring old prose must never silently rename a book.
    assert assert_ok(client.get('/api/bootstrap'))['project']['title']=='Current Professional Title'
    current=next(p for p in assert_ok(client.get('/api/workspace'))['projects'] if p['id']==project['id'])
    assert current['name']=='Current Professional Title'
