from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import invariants as storage_invariants
from storage import mutations as storage_mutations

import io
import json
from pathlib import Path

from formats.project_json import validation as project_json_validation
from formats.project_json import markup as project_json_markup
def sample_project(name: str = "JSON Novel") -> dict:
    return {
        "format": "breadmaker-project",
        "schema_version": "1.0",
        "name": name,
        "markers": [
            {"id": "marker:plot", "name": "Plot", "description": "Plot work", "color": "#7a5cff"},
            {"id": "marker:character", "name": "Character", "color": "#2f8f6b"},
        ],
        "manuscript": {
            "tabs": [
                {
                    "id": "tab:manuscript",
                    "name": "Manuscript",
                    "main": True,
                    "content": "Loose **bold** tab prose with \\*literal stars\\*.",
                    "chapters": [
                        {
                            "id": "chapter:opening",
                            "title": "Opening",
                            "content": "A **bold** line.\nAn *italic* and __underlined__ line.\nLiteral \\*star\\* and \\_underscore\\_ and \\\\ slash.",
                            "scenes": [
                                {"id": "scene:arrival", "title": "Arrival", "content": "The scene starts here."}
                            ],
                        }
                    ],
                },
                {
                    "id": "tab:outline",
                    "name": "Outline",
                    "chapters": [
                        {"id": "chapter:outline-one", "title": "Beat One", "content": "Planning text."}
                    ],
                },
            ]
        },
        "canvases": [
            {
                "id": "canvas:story",
                "name": "Story",
                "type": "normal",
                "layout": "compact",
                "nodes": [
                    {
                        "id": "card:goal",
                        "type": "card",
                        "title": "Goal",
                        "body": "What the protagonist wants.",
                        "display": "header",
                        "marker": "marker:character",
                        "linked_to": "chapter:opening",
                        "links": [{"target": "task:rewrite", "relationship": "work"}],
                    },
                    {"id": "card:conflict", "type": "card", "title": "Conflict", "body": "Pressure", "color": "#aa6644"},
                    {"id": "index:one", "type": "index", "value": "1"},
                    {"id": "routing:one", "type": "routing"},
                ],
                "connectors": [
                    {
                        "from": "card:goal",
                        "to": "card:conflict",
                        "label": "causes",
                        "marker": "marker:plot",
                        "route": "straight",
                        "stroke": "dashed",
                        "end": "arrow",
                        "width": 2.0,
                    }
                ],
            },
            {
                "id": "canvas:characters",
                "name": "Characters",
                "parent": "canvas:story",
                "type": "normal",
                "layouts": [{"mode": "circular", "nodes": ["card:hero"], "origin": {"x": 300, "y": 200}}],
                "nodes": [{"id": "card:hero", "type": "card", "title": "Hero", "body": "Character notes."}],
            },
            {
                "id": "canvas:development",
                "name": "Development",
                "type": "developmental",
                "developmental_scope": "chapters",
                "structure": ["chapter:opening"],
                "nodes": [{"id": "card:dev-note", "type": "card", "title": "Check opening", "body": "Versioned free canvas."}],
            },
        ],
        "maps": [
            {
                "id": "map:arc",
                "name": "Arc",
                "orientation": "horizontal",
                "layout_mode": "scaled",
                "label_mode": "minimal",
                "ruler": "chapters",
                "lines": [
                    {
                        "id": "map-line:main",
                        "name": "Main",
                        "color": "#5f7385",
                        "items": [
                            {"id": "map-item:start", "type": "stop", "title": "Start", "anchor": "chapter:opening"},
                            {"id": "map-item:stretch", "type": "span", "title": "Pressure", "start": 0.25, "end": 0.6},
                        ],
                    }
                ],
            }
        ],
        "tasks": [
            {
                "id": "task:rewrite",
                "title": "Rewrite opening",
                "description": "Strengthen the first page.",
                "status": "open",
                "marker": "marker:plot",
                "subtasks": [
                    {"id": "subtask:first-pass", "title": "First pass", "description": "Read aloud", "completed": False}
                ],
                "links": [{"target": "chapter:opening", "relationship": "edits"}],
            }
        ],
    }


def upload(doc: dict, filename: str = "project.breadmaker.json"):
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    return {"file": (filename, io.BytesIO(raw), "application/json")}


def test_v600_release_ui_and_public_contract():
    root = Path(__file__).resolve().parents[1]
    assert tuple(int(x) for x in (root / "VERSION").read_text().strip().split(".")) >= (6,0,0)
    html = (root / "static" / "index.html").read_text()
    js = frontend_source_from_app_path(root / "static" / "app.js")
    assert "/static/app.js?v=" in html and "/static/styles.css?v=" in html
    assert 'id="projectJsonImportInput"' in html
    assert "Import JSON…" in js
    assert "JSON Project" in js
    assert (root / "docs" / "BREADMAKER_PROJECT_JSON.md").is_file()
    assert (root / "docs" / "breadmaker-project.schema.json").is_file()
    assert (root / "docs" / "skills" / "breadmaker-project-builder" / "SKILL.md").is_file()


def test_v600_validation_creates_default_main_tab_and_rejects_bad_refs():
    minimal = {"format": "breadmaker-project", "schema_version": "1.0", "name": "Minimal"}
    validated = project_json_validation.validate_document(minimal)
    tabs = validated["document"]["manuscript"]["tabs"]
    assert len(tabs) == 1 and tabs[0]["main"] and tabs[0]["name"] == "Manuscript"

    bad = sample_project("Bad")
    bad["canvases"][0]["nodes"][0]["links"] = ["chapter:missing"]
    try:
        project_json_validation.validate_document(bad)
    except project_json_validation.ProjectJSONError as exc:
        assert "chapter:missing" in exc.detail()
    else:
        raise AssertionError("Broken symbolic references must fail validation")


def test_v600_invalid_import_is_rejected_before_creating_project(client, fresh_project):
    before = client.get("/api/workspace").json()["projects"]
    bad = sample_project("Never Created")
    bad["tasks"][0]["links"] = ["chapter:does-not-exist"]
    response = client.post("/api/projects/import-json", files=upload(bad))
    assert response.status_code == 400
    after = client.get("/api/workspace").json()["projects"]
    assert len(after) == len(before)
    assert not any(p["name"] == "Never Created" for p in after)


def test_v600_preview_does_not_create_project_and_import_builds_full_project(client, fresh_project):
    before = client.get("/api/workspace").json()["projects"]
    doc = sample_project("Generated Project")
    preview = client.post("/api/projects/import-json/preview", files=upload(doc))
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["name"] == "Generated Project"
    assert data["summary"]["markers"] == 2
    assert data["summary"]["canvases"] == 3
    assert data["summary"]["tasks"] == 1
    assert len(client.get("/api/workspace").json()["projects"]) == len(before)

    response = client.post("/api/projects/import-json", files=upload(doc))
    assert response.status_code == 200, response.text
    after = client.get("/api/workspace").json()["projects"]
    assert len(after) == len(before) + 1
    assert any(p["name"] == "Generated Project" for p in after)

    with storage_mutations.tx() as con:
        project = dict(con.execute("SELECT * FROM projects").fetchone())
        assert project["title"] == "Generated Project"
        markers = {r["name"]: r["color"] for r in con.execute("SELECT name,color FROM markup_labels")}
        assert markers == {"Plot": "#7a5cff", "Character": "#2f8f6b"}
        tabs = [dict(r) for r in con.execute("SELECT * FROM document_tabs ORDER BY position,id")]
        assert [t["title"] for t in tabs] == ["Manuscript", "Outline"]
        assert [bool(t["is_manuscript"]) for t in tabs] == [True, False]
        implicit = con.execute("SELECT id FROM chapters WHERE tab_id=? AND is_implicit=1", (tabs[0]["id"],)).fetchone()
        assert implicit is not None
        explicit = dict(con.execute("SELECT * FROM chapters WHERE title='Opening'").fetchone())
        blocks = [dict(r) for r in con.execute("SELECT * FROM manuscript_blocks WHERE chapter_id=? ORDER BY position", (explicit["id"],))]
        assert any("<strong>bold</strong>" in b["content_html"] for b in blocks)
        assert any("<em>italic</em>" in b["content_html"] and "<u>underlined</u>" in b["content_html"] for b in blocks)
        literal = next(b for b in blocks if "Literal" in b["text_content"])
        assert "*star*" in literal["text_content"] and "_underscore_" in literal["text_content"] and "\\ slash" in literal["text_content"]

        canvases = {r["name"]: dict(r) for r in con.execute("SELECT * FROM canvases")}
        assert canvases["Characters"]["parent_id"] == canvases["Story"]["id"]
        card = dict(con.execute("SELECT * FROM canvas_nodes WHERE canvas_id=? AND title='Goal'", (canvases["Story"]["id"],)).fetchone())
        assert card["display_mode"] == "header"
        assert card["label_id"] is not None and card["chapter_id"] == explicit["id"]
        edge = dict(con.execute("SELECT * FROM canvas_edges WHERE canvas_id=?", (canvases["Story"]["id"],)).fetchone())
        assert edge["route_style"] == "straight" and edge["line_style"] == "dashed"
        assert edge["color"] == "#7a5cff" and edge["marker_id"] is not None

        dev = canvases["Development"]
        versions = [dict(r) for r in con.execute("SELECT * FROM canvas_versions WHERE canvas_id=?", (dev["id"],))]
        assert len(versions) == 1
        struct = [dict(r) for r in con.execute("SELECT * FROM canvas_nodes WHERE canvas_id=? AND node_type='chapter'", (dev["id"],))]
        assert len(struct) == 1 and struct[0]["chapter_id"] == explicit["id"] and struct[0]["lane_position"] == 0
        assert "card:dev-note" not in versions[0]["workspace_json"]  # snapshots store internal workspace data, not public IDs
        assert "Check opening" in versions[0]["workspace_json"]

        map_item = dict(con.execute("SELECT * FROM map_items WHERE title='Start'").fetchone())
        assert map_item["start_anchor_type"] == "chapter" and str(map_item["start_anchor_id"]) == str(explicit["id"])
        task = dict(con.execute("SELECT * FROM tasks WHERE title='Rewrite opening'").fetchone())
        assert task["marker_id"] is not None
        assert con.execute("SELECT COUNT(*) FROM task_milestones WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM references_map").fetchone()[0] >= 2
        storage_invariants.validate_project_invariants(con, int(project["id"]))


def test_v600_developmental_structure_rejects_non_main_manuscript_chapter():
    doc = sample_project("Bad Structure")
    doc["canvases"][2]["structure"] = ["chapter:outline-one"]
    try:
        project_json_validation.validate_document(doc)
    except project_json_validation.ProjectJSONError as exc:
        assert "Main Manuscript" in exc.detail()
    else:
        raise AssertionError("Developmental structure may only use Main Manuscript entities")


def test_v600_export_is_valid_and_reimportable(client, fresh_project):
    doc = sample_project("Round Trip")
    first = client.post("/api/projects/import-json", files=upload(doc))
    assert first.status_code == 200, first.text
    active = client.get("/api/workspace").json()["active_project_id"]
    exported = client.get(f"/api/projects/{active}/export-json")
    assert exported.status_code == 200, exported.text
    payload = json.loads(exported.content)
    validated = project_json_validation.validate_document(payload)
    assert validated["document"]["name"] == "Round Trip"
    assert any(m["color"] == "#7a5cff" for m in payload["markers"])
    main = next(t for t in payload["manuscript"]["tabs"] if t.get("main"))
    assert "Loose **bold** tab prose" in main.get("content", "")
    assert any("**bold**" in ch["content"] for ch in main["chapters"])

    again = client.post("/api/projects/import-json", files={"file": ("roundtrip.breadmaker.json", io.BytesIO(exported.content), "application/json")})
    assert again.status_code == 200, again.text
    assert again.json()["project"]["name"] == "Round Trip (2)"
    with storage_mutations.tx() as con:
        assert con.execute("SELECT COUNT(*) FROM markup_labels").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM canvases").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        storage_invariants.validate_project_invariants(con, int(con.execute("SELECT id FROM projects").fetchone()[0]))


def test_v600_markup_escape_contract():
    html_text, plain = project_json_markup.markup_to_html_text(r"**bold** *italics* __under__ \*literal\* \_literal\_ \\ slash")
    assert "<strong>bold</strong>" in html_text
    assert "<em>italics</em>" in html_text
    assert "<u>under</u>" in html_text
    assert "*literal*" in plain and "_literal_" in plain and "\\ slash" in plain
    markup = project_json_markup.inline_html_to_markup(html_text, plain)
    assert "**bold**" in markup and "*italics*" in markup and "__under__" in markup
