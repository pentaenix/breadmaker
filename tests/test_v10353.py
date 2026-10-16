from __future__ import annotations

import ast
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.domain_store import HostedDomainStore
from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore, ProjectUnavailable
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import HostedCloudProjectRepository, HostedCommittedAssetStore, HostedHistoryStore, ensure_hosted_user
from hosted.storage import MemoryObjectStore

OWNER = "usr_" + "a" * 32
OUTSIDER = "usr_" + "b" * 32
def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _valid_png() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00\x00\x00\x00\xff"
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(raw)) + _png_chunk(b"IEND", b"")


PNG = _valid_png()

def _services():
    documents = MemoryDocumentStore()
    objects = MemoryObjectStore()
    ensure_hosted_user(documents, user_id=OWNER, display_name="Owner")
    authority = HostedProjectRepository(documents)
    users = HostedUserRepository(HostedStructuredRepository(documents))
    projects = HostedCloudProjectRepository(documents, objects, authority, users, lambda: OWNER)
    return documents, objects, authority, users, projects


def test_v10353_hosted_composition_has_no_native_or_sqlite_persistence_bridge():
    hosted = (ROOT / "src/runtime/hosted.py").read_text(encoding="utf-8")
    environment = (ROOT / "src/backend/environment.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "src/backend/lifecycle.py").read_text(encoding="utf-8")
    assert "MemoryDocumentStore(), MemoryObjectStore()" in hosted
    assert "GoogleFirestoreDocumentStore" in hosted and "GoogleCloudObjectStore" in hosted
    assert "native_projects=None" in hosted and "native_assets=None" in hosted
    assert "native_history=None" in hosted and "native_backups=None" in hosted
    assert "'persistence':'firestore_gcs'" in hosted
    assert "'persistent_server_filesystem':False" in hosted and "'sqlite':False" in hosted
    assert "runtime=build_runtime(None)" in environment
    assert "if runtime.mode!='local':" in lifecycle
    # Hosted-only modules must not make local workspace/storage authority imports.
    for relative in ("src/hosted/domain_store.py", "src/hosted/document_io.py", "src/hosted/http_api.py", "src/hosted/http_compat.py", "src/hosted/http_auth.py", "src/hosted/packages.py", "src/hosted/runtime_services.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from workspace" not in source
        assert "from storage" not in source
        assert "BREADMAKER_HOME" not in source


def test_v10353_project_and_manuscript_state_survive_service_reconstruction():
    documents, objects, authority, users, projects = _services()
    created = projects.create("Cloud Book")
    pid = created["id"]
    assert len(pid) == 32
    domain = projects.domain
    tabs = domain.list("document_tab", {"project_id": pid}, user_id=OWNER)
    chapters = domain.list("chapter", {"project_id": pid}, user_id=OWNER)
    blocks = domain.list("manuscript_block", {"project_id": pid}, user_id=OWNER)
    assert [row["id"] for row in tabs] == ["tab_manuscript"]
    assert [row["id"] for row in chapters] == ["chapter_1"]
    assert [row["id"] for row in blocks] == ["b_1"]

    before = authority.get_for_user(pid, OWNER)["generation"]
    updated = domain.update(
        "manuscript_block",
        {"project_id": pid, "block_id": "b_1"},
        {"content_html": "<p>Hello cloud</p>", "text_content": "Hello cloud"},
        user_id=OWNER,
    )
    assert updated["text_content"] == "Hello cloud"
    assert authority.get_for_user(pid, OWNER)["generation"] == before + 1

    # Reconstruct every application service over the same provider stores. No process
    # cache or server filesystem is required to recover committed canonical state.
    reopened = HostedCloudProjectRepository(documents, objects, HostedProjectRepository(documents), users, lambda: OWNER)
    assert reopened.get(pid)["name"] == "Cloud Book"
    block = reopened.domain.get("manuscript_block", {"project_id": pid, "block_id": "b_1"}, user_id=OWNER)
    assert block["content_html"] == "<p>Hello cloud</p>"


def test_v10353_major_structured_product_flows_share_firestore_authority():
    documents, objects, authority, _users, projects = _services()
    pid = projects.create("Flows")["id"]
    domain = HostedDomainStore(documents, objects)

    task = domain.create("task", {"project_id": pid}, {"title": "Revise", "position": 0}, user_id=OWNER)
    canvas = domain.create("canvas", {"project_id": pid}, {"title": "Plot"}, user_id=OWNER)
    domain.create("canvas_node", {"project_id": pid, "canvas_id": canvas["id"]}, {"title": "Beat"}, user_id=OWNER)
    story_map = domain.create("story_map", {"project_id": pid}, {"title": "Timeline"}, user_id=OWNER)
    line = domain.create("map_line", {"project_id": pid, "map_id": story_map["id"]}, {"title": "A"}, user_id=OWNER)
    domain.create("map_item", {"project_id": pid, "map_id": story_map["id"], "line_id": line["id"]}, {"title": "Event"}, user_id=OWNER)
    review = domain.create("review", {"project_id": pid}, {"name": "Alpha", "status": "draft"}, user_id=OWNER)
    domain.create("review_participant", {"project_id": pid, "review_id": review["id"]}, {"display_name": "Reader"}, user_id=OWNER)
    thread = domain.create("comment_thread", {"project_id": pid}, {"title": "Note"}, user_id=OWNER)
    domain.create("comment_message", {"project_id": pid, "thread_id": thread["id"]}, {"body": "Check this"}, user_id=OWNER)

    assert domain.get("task", {"project_id": pid, "task_id": task["id"]}, user_id=OWNER)["title"] == "Revise"
    assert len(domain.list("canvas_node", {"project_id": pid, "canvas_id": canvas["id"]}, user_id=OWNER)) == 1
    assert len(domain.list("map_item", {"project_id": pid, "map_id": story_map["id"], "line_id": line["id"]}, user_id=OWNER)) == 1
    assert len(domain.list("review_participant", {"project_id": pid, "review_id": review["id"]}, user_id=OWNER)) == 1
    assert authority.get_for_user(pid, OWNER)["generation"] > 4


def test_v10353_assets_and_revisions_are_provider_backed_and_restart_safe():
    documents, objects, authority, _users, projects = _services()
    pid = projects.create("Objects")["id"]
    before = authority.get_for_user(pid, OWNER)["generation"]
    assets = HostedCommittedAssetStore(documents, objects, lambda: OWNER, lambda: pid)
    saved = assets.put_blob(PNG, ".png")
    assert saved.object_name and saved.object_generation
    assert authority.get_for_user(pid, OWNER)["generation"] == before + 1
    rows = projects.domain.list("project_asset", {"project_id": pid}, user_id=OWNER)
    assert len(rows) == 1 and rows[0]["sha256"] == saved.digest
    reconstructed_assets = HostedCommittedAssetStore(documents, objects, lambda: OWNER, lambda: pid)
    assert reconstructed_assets.read_blob(saved.api_path) == PNG

    history = HostedHistoryStore(documents, objects, lambda: OWNER, lambda: pid)
    revision = history.create_revision(b"revision-envelope", {"state_hash": "a" * 64})
    assert revision["object_name"]
    reconstructed = HostedHistoryStore(documents, objects, lambda: OWNER, lambda: pid)
    assert reconstructed.list_revisions()[0]["revision_id"] == revision["revision_id"]
    meta, envelope = reconstructed.read_revision(revision["revision_id"])
    assert meta["revision_id"] == revision["revision_id"] and envelope == b"revision-envelope"
    assert authority.get_for_user(pid, OWNER)["current_revision_id"] == revision["revision_id"]


def test_v10353_project_package_round_trip_allocates_fresh_identity_without_membership_authority():
    documents, objects, authority, _users, projects = _services()
    source = projects.create("Portable Cloud Book")
    source_id = source["id"]
    projects.domain.create("task", {"project_id": source_id}, {"title": "Carry me"}, user_id=OWNER)
    package = HostedProjectPackageService(projects).export_bytes(source_id, OWNER)
    assert OWNER.encode() not in package

    imported = HostedProjectPackageService(projects).import_bytes(package, OWNER, name_override="Imported")
    target_id = imported["id"]
    assert target_id != source_id and len(target_id) == 32
    assert imported["name"] == "Imported"
    tasks = projects.domain.list("task", {"project_id": target_id}, user_id=OWNER)
    assert [row["title"] for row in tasks] == ["Carry me"]
    membership = authority.membership(target_id, OWNER)
    assert membership and membership["role"] == "owner"


def test_v10353_legacy_breadmaker_decoder_is_transient_memory_only():
    source = (ROOT / "src/hosted/packages.py").read_text(encoding="utf-8")
    assert "sqlite3.connect(':memory:')" in source
    assert ".deserialize(database)" in source
    assert "PRAGMA integrity_check" in source and "PRAGMA foreign_key_check" in source
    assert "tempfile" not in source.lower()
    assert "mkstemp" not in source.lower()
    assert "source_project_id" in source and "_new_import_project_id" in source and "_finish_import" in source


def test_v10353_hosted_platform_bootstrap_routes_do_not_depend_on_local_runtime_router():
    source = (ROOT / "src/hosted/http_api.py").read_text(encoding="utf-8")
    for route in ("/api/locales", "/locales/{locale_code}.json", "/api/auth/session", "/api/auth/logout"):
        assert route in source
    assert "backend.runtime_routes" not in source
    packages = (ROOT / "src/hosted/packages.py").read_text(encoding="utf-8")
    assert "for asset in self.domain.list('project_asset'" in packages
    assert "list_prefix(f'projects/{project_id}/assets/sha256/')" not in packages


def test_v10353_direct_project_authority_fails_closed_for_non_member():
    documents, objects, authority, users, projects = _services()
    pid = projects.create("Private")["id"]
    ensure_hosted_user(documents, user_id=OUTSIDER, display_name="Outsider")
    with pytest.raises(ProjectUnavailable):
        authority.get_for_user(pid, OUTSIDER)
    with pytest.raises(ProjectUnavailable):
        HostedDomainStore(documents, objects).list("document_tab", {"project_id": pid}, user_id=OUTSIDER)


def test_v10353_project_trash_restore_remains_firestore_authoritative():
    documents, objects, authority, _users, projects = _services()
    project = projects.create("Restore me")
    pid = project["id"]
    projects.delete(pid)
    assert [row["id"] for row in projects.list_trash()] == [pid]
    with pytest.raises(ProjectUnavailable):
        authority.get_for_user(pid, OWNER)
    restored = projects.restore_from_trash(pid)
    assert restored["id"] == pid and restored["status"] == "active"
    assert authority.get_for_user(pid, OWNER)["status"] == "active"


def test_v10353_hosted_json_round_trip_is_authorization_neutral_and_allocates_new_id():
    documents, objects, _authority, _users, projects = _services()
    source = projects.create("JSON Source")
    projects.domain.create("task", {"project_id": source["id"]}, {"title": "JSON task"}, user_id=OWNER)
    service = HostedProjectPackageService(projects)
    exported = service.export_json(source["id"], OWNER)
    assert exported["authorization_portable"] is False
    assert OWNER not in json.dumps(exported, sort_keys=True)
    preview = service.preview_json(exported)
    assert preview["creates_new_project"] is True and preview["entity_count"] > 0
    imported = service.import_json(exported, OWNER, name_override="JSON Imported")
    assert imported["id"] != source["id"] and imported["name"] == "JSON Imported"
    tasks = projects.domain.list("task", {"project_id": imported["id"]}, user_id=OWNER)
    assert [row["title"] for row in tasks] == ["JSON task"]


def test_v10353_hosted_route_boundary_covers_every_current_local_product_route():
    manifest = json.loads((ROOT / "src/api/route_manifest.json").read_text(encoding="utf-8"))
    expected = set()
    for section in ("domains", "platform_domains"):
        for domain in manifest[section]:
            for route in domain["routes"]:
                expected.add((str(route["method"]).upper(), str(route["path"])))
    actual = set()
    for relative in ("src/hosted/http_api.py", "src/hosted/http_compat.py", "src/hosted/http_auth.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not decorator.args:
                    continue
                path = decorator.args[0]
                if isinstance(path, ast.Constant) and isinstance(path.value, str):
                    actual.add((method, path.value))
    assert expected <= actual, sorted(expected - actual)


def test_v10353_hosted_identity_platform_surface_is_cloud_only_and_preserves_security_routes():
    source=(ROOT/'src/hosted/http_auth.py').read_text(encoding='utf-8')
    for route in ('/auth/login','/api/auth/session/exchange','/api/auth/csrf','/api/auth/security','/api/auth/security/proof','/api/auth/security/sync','/api/auth/security/sign-out-everywhere'):
        assert route in source
    assert 'identity_platform' in source.lower()
    assert 'auth0' not in source.lower()
    assert not (ROOT/'src/auth/auth0.py').exists()
    assert (ROOT/'docs/archive/auth0_runtime/auth0.py.txt').exists()


def test_v10353_later_milestone_capability_boundaries_are_explicit_not_404_fallthroughs():
    compat = (ROOT / "src/hosted/http_compat.py").read_text(encoding="utf-8")
    assert "HOSTED_PORTABLE_REVIEW_UNAVAILABLE_UNTIL_M8" in compat
    assert "HOSTED_BACKUP_UNAVAILABLE_UNTIL_M9" in compat
    assert "HOSTED_HISTORY_RESTORE_UNAVAILABLE_UNTIL_M9" in compat
