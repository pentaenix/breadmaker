from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.domain_store import HostedDomainStore
from hosted.firestore import (
    HostedProjectRepository,
    HostedStructuredRepository,
    HostedUserRepository,
    MemoryDocumentStore,
    ProjectUnavailable,
)
from hosted.firestore.contracts import DocumentNotFound, HostedValidationError
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.storage import MemoryObjectStore

OWNER = "usr_" + "c" * 32
READER = "usr_" + "d" * 32

def _services(user_id: str = OWNER):
    documents = MemoryDocumentStore()
    objects = MemoryObjectStore()
    ensure_hosted_user(documents, user_id=OWNER, display_name="Owner")
    ensure_hosted_user(documents, user_id=READER, display_name="Reader")
    authority = HostedProjectRepository(documents)
    users = HostedUserRepository(HostedStructuredRepository(documents))
    current = {"user_id": user_id}
    projects = HostedCloudProjectRepository(documents, objects, authority, users, lambda: current["user_id"])
    return documents, objects, authority, users, current, projects


def test_v10354_project_lifecycle_membership_and_member_state_are_firestore_authoritative():
    documents, objects, authority, users, current, projects = _services()
    created = projects.create("Lifecycle")
    pid = created["id"]
    assert projects.rename(pid, "Renamed")["name"] == "Renamed"

    projects.add_member(pid, READER, "editor")
    assert projects.membership(pid, READER)["role"] == "editor"
    current["user_id"] = READER
    reader_projects = HostedCloudProjectRepository(documents, objects, authority, users, lambda: current["user_id"])
    assert reader_projects.get(pid)["name"] == "Renamed"
    saved = reader_projects.put_member_state(pid, {"bookmark": {"block_id": "b_1", "offset": 3}})
    assert saved["bookmark"]["block_id"] == "b_1"
    assert reader_projects.member_state(pid)["bookmark"]["offset"] == 3
    reader_projects.put_member_state(pid, {"value": {"zoom": 1.25}}, key="canvas_zoom")
    assert reader_projects.member_state(pid, key="canvas_zoom")["value"]["zoom"] == 1.25

    current["user_id"] = OWNER
    owner_projects = HostedCloudProjectRepository(documents, objects, authority, users, lambda: current["user_id"])
    owner_projects.delete(pid)
    assert [row["id"] for row in owner_projects.list_trash()] == [pid]
    assert owner_projects.restore_from_trash(pid)["status"] == "active"


def test_v10354_manuscript_hierarchy_annotations_and_comments_survive_reconstruction():
    documents, objects, authority, users, current, projects = _services()
    pid = projects.create("Manuscript")["id"]
    domain = projects.domain
    tab = domain.create("document_tab", {"project_id": pid}, {"title": "Appendix", "position": 1}, user_id=OWNER)
    chapter = domain.create("chapter", {"project_id": pid}, {"title": "Cloud chapter", "position": 1, "tab_id": tab["id"]}, user_id=OWNER)
    scene = domain.create("scene", {"project_id": pid}, {"title": "Arrival", "position": 0, "tab_id": tab["id"]}, user_id=OWNER)
    block = domain.create(
        "manuscript_block", {"project_id": pid},
        {"chapter_id": chapter["id"], "scene_id": scene["id"], "tab_id": tab["id"], "position": 0, "content_html": "<p>Hello</p>", "text_content": "Hello"},
        user_id=OWNER,
    )
    before = authority.get_for_user(pid, OWNER)["generation"]
    annotations = domain.create_batch_atomic(
        "annotation", {"project_id": pid},
        [
            {"block_id": block["id"], "annotation_type": "highlight", "start_offset": 0, "end_offset": 2, "selected_text": "He"},
            {"block_id": block["id"], "annotation_type": "note", "start_offset": 2, "end_offset": 5, "selected_text": "llo"},
        ],
        user_id=OWNER,
    )
    assert len(annotations) == 2
    assert authority.get_for_user(pid, OWNER)["generation"] == before + 1
    with pytest.raises(HostedValidationError):
        domain.create_batch_atomic("annotation", {"project_id": pid}, [{"body": "ok"}, {"generation": 99}], user_id=OWNER)
    assert len(domain.list("annotation", {"project_id": pid}, user_id=OWNER)) == 2

    thread = domain.create("comment_thread", {"project_id": pid}, {"title": "Line note"}, user_id=OWNER)
    domain.create("comment_message", {"project_id": pid, "thread_id": thread["id"]}, {"body": "Please revise"}, user_id=OWNER)
    reopened = HostedDomainStore(documents, objects)
    assert reopened.get("manuscript_block", {"project_id": pid, "block_id": block["id"]}, user_id=OWNER)["text_content"] == "Hello"
    assert reopened.list("comment_message", {"project_id": pid, "thread_id": thread["id"]}, user_id=OWNER)[0]["body"] == "Please revise"


def test_v10354_reviews_canvas_maps_tasks_and_checkpoint_pointer_deletion_are_real_behaviors():
    documents, objects, authority, users, current, projects = _services()
    pid = projects.create("Flows")["id"]
    domain = projects.domain

    review = domain.create("review", {"project_id": pid}, {"name": "Beta", "status": "draft"}, user_id=OWNER)
    participant = domain.create("review_participant", {"project_id": pid, "review_id": review["id"]}, {"display_name": "Reader"}, user_id=OWNER)
    snapshot = domain.create("review_snapshot", {"project_id": pid, "review_id": review["id"]}, {"snapshot": {"chapters": ["chapter_1"]}, "status": "active"}, user_id=OWNER)
    assert domain.get("review_snapshot", {"project_id": pid, "review_id": review["id"], "snapshot_id": snapshot["id"]}, user_id=OWNER)["snapshot"]["chapters"] == ["chapter_1"]

    task = domain.create("task", {"project_id": pid}, {"title": "Revise", "position": 0}, user_id=OWNER)
    domain.create("task_milestone", {"project_id": pid, "task_id": task["id"]}, {"title": "Draft", "position": 0}, user_id=OWNER)
    canvas = domain.create("canvas", {"project_id": pid}, {"title": "Plot", "position": 0}, user_id=OWNER)
    n1 = domain.create("canvas_node", {"project_id": pid, "canvas_id": canvas["id"]}, {"title": "A", "position": 0}, user_id=OWNER)
    n2 = domain.create("canvas_node", {"project_id": pid, "canvas_id": canvas["id"]}, {"title": "B", "position": 1}, user_id=OWNER)
    domain.create("canvas_edge", {"project_id": pid, "canvas_id": canvas["id"]}, {"from_node_id": n1["id"], "to_node_id": n2["id"]}, user_id=OWNER)
    story_map = domain.create("story_map", {"project_id": pid}, {"title": "Timeline", "position": 0}, user_id=OWNER)
    line = domain.create("map_line", {"project_id": pid, "map_id": story_map["id"]}, {"title": "Main", "position": 0}, user_id=OWNER)
    domain.create("map_item", {"project_id": pid, "map_id": story_map["id"], "line_id": line["id"]}, {"title": "Beat", "position": 0, "schematic_start": .5}, user_id=OWNER)

    checkpoint = domain.create("checkpoint", {"project_id": pid}, {"name": "Before edit", "snapshot": {"marker": "safe"}}, user_id=OWNER)
    pointer = checkpoint["payload_object"]
    assert objects.stat(pointer["object_name"]) is not None
    domain.delete_immutable_pointer("checkpoint", {"project_id": pid, "checkpoint_id": checkpoint["id"]}, user_id=OWNER)
    with pytest.raises(DocumentNotFound):
        domain.get("checkpoint", {"project_id": pid, "checkpoint_id": checkpoint["id"]}, user_id=OWNER)
    assert objects.stat(pointer["object_name"]) is not None

    assert domain.list("review_participant", {"project_id": pid, "review_id": review["id"]}, user_id=OWNER)[0]["id"] == participant["id"]
    assert len(domain.list("canvas_edge", {"project_id": pid, "canvas_id": canvas["id"]}, user_id=OWNER)) == 1
    assert len(domain.list("map_item", {"project_id": pid, "map_id": story_map["id"], "line_id": line["id"]}, user_id=OWNER)) == 1


def test_v10354_hosted_route_compatibility_no_longer_fakes_m5_product_behaviors():
    compat = (ROOT / "src/hosted/http_compat.py").read_text(encoding="utf-8")
    api = (ROOT / "src/hosted/http_api.py").read_text(encoding="utf-8")
    assert "hosted_canvas_populate_catalog(request:Request):return _story_catalog_for_project" in compat
    assert "hosted_map_populate_catalog(request:Request):return _story_catalog_for_project" in compat
    assert "'changed':False,'mode':'client-layout'" not in compat
    assert "HOSTED_CHECKPOINT_IMMUTABLE" not in compat
    assert "delete_immutable_pointer('checkpoint'" in compat
    assert "create_batch_atomic('annotation'" in api
    # Later milestones are still explicit capability boundaries rather than fake success.
    assert "HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9" in compat
    assert "HOSTED_HISTORY_RESTORE_UNAVAILABLE_UNTIL_M9" in compat
