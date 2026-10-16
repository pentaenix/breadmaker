"""M9B2A semantic ID portability tests (USER verifier only; never run by agent).

Portable downloads must restore the same Canvas/Map *relationships*, not only
insert rows that satisfy Schema-42's declared foreign keys. Unsupported frozen
hosted version/snapshot formats must refuse export rather than lose content.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.local_portability import LocalPortabilityError, to_local_package
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime
from workspace import Workspace


def _project(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    runtime = build_hosted_dev_runtime(documents=MemoryDocumentStore(), objects=MemoryObjectStore())
    pid = runtime.projects.create('Portability with Maps and Canvas')['id']
    chapter = runtime.projects.domain.list('chapter', {'project_id': pid}, user_id=_DEV_USER_ID)[0]
    return runtime, pid, chapter


def _create(runtime, logical, parents, data):
    return runtime.projects.domain.create(logical, parents, data, user_id=_DEV_USER_ID)


def _restore(tmp_path, payload):
    source = tmp_path / 'portable.breadmaker'
    source.write_bytes(payload)
    workspace = Workspace(tmp_path / 'local')
    workspace.init()
    assert workspace.verify_package(source)['ok']
    restored = workspace.import_project(source)
    return sqlite3.connect(Path(restored['path']) / 'project.sqlite3')


def test_v10369_canvas_map_and_non_fk_links_survive_real_local_import(monkeypatch, tmp_path):
    runtime, pid, chapter = _project(monkeypatch)
    parents = {'project_id': pid}
    canvas = _create(runtime, 'canvas', parents, {'name': 'Plot canvas', 'position': 0})
    cparents = {**parents, 'canvas_id': canvas['id']}
    node1 = _create(runtime, 'canvas_node', cparents, {'title': 'Opening', 'x': 120, 'y': 140, 'chapter_id': chapter['id']})
    node2 = _create(runtime, 'canvas_node', cparents, {'title': 'Crisis', 'x': 440, 'y': 330})
    _create(runtime, 'canvas_edge', cparents,
            {'from_node_id': node1['id'], 'to_node_id': node2['id'], 'label': 'Changes'})
    story_map = _create(runtime, 'story_map', parents, {'name': 'Story path'})
    mparents = {**parents, 'map_id': story_map['id']}
    line1 = _create(runtime, 'map_line', mparents, {'name': 'Main', 'color': '#245cd7'})
    line2 = _create(runtime, 'map_line', mparents, {'name': 'Subplot', 'color': '#528a53'})
    item = _create(runtime, 'map_item', {**mparents, 'line_id': line1['id']},
                   {'title': 'Opening', 'item_type': 'stop', 'start_anchor_type': 'chapter',
                    'start_anchor_id': chapter['id'], 'source_key': 'chapter:' + chapter['id']})
    _create(runtime, 'map_junction', mparents,
            {'from_line_id': line1['id'], 'to_line_id': line2['id'],
             'from_item_id': item['id'], 'kind': 'branch'})
    _create(runtime, 'cross_feature_reference', parents,
            {'source_type': 'chapter', 'source_id': chapter['id'],
             'target_type': 'canvas', 'target_id': canvas['id'],
             'relationship': 'visualizes'})
    raw = HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)
    with _restore(tmp_path, raw) as db:
        chapter_id = db.execute('SELECT id FROM chapters WHERE title=?', ('Chapter 1',)).fetchone()[0]
        canvas_id = db.execute('SELECT id FROM canvases WHERE name=?', ('Plot canvas',)).fetchone()[0]
        assert db.execute('SELECT COUNT(*) FROM canvas_nodes WHERE canvas_id=?', (canvas_id,)).fetchone()[0] == 2
        assert db.execute('''SELECT n.title FROM canvas_edges e JOIN canvas_nodes n ON n.id=e.from_node_id''').fetchone()[0] == 'Opening'
        assert db.execute('SELECT chapter_id FROM canvas_nodes WHERE title=?', ('Opening',)).fetchone()[0] == chapter_id
        map_id = db.execute('SELECT id FROM story_maps WHERE name=?', ('Story path',)).fetchone()[0]
        assert db.execute('SELECT COUNT(*) FROM map_lines WHERE map_id=?', (map_id,)).fetchone()[0] == 2
        anchor, key = db.execute('SELECT start_anchor_id,source_key FROM map_items WHERE title=?', ('Opening',)).fetchone()
        assert anchor == str(chapter_id) and key == f'chapter:{chapter_id}'
        assert db.execute('''SELECT ml.name FROM map_junctions j JOIN map_lines ml ON ml.id=j.to_line_id''').fetchone()[0] == 'Subplot'
        ref = db.execute('SELECT source_id,target_id FROM references_map').fetchone()
        assert ref == (str(chapter_id), str(canvas_id))
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None


def test_v10369_invalid_anchor_id_fails_even_without_sql_fk(monkeypatch):
    runtime, pid, _ = _project(monkeypatch)
    parents = {'project_id': pid}
    story_map = _create(runtime, 'story_map', parents, {'name': 'Arc'})
    mparents = {**parents, 'map_id': story_map['id']}
    line = _create(runtime, 'map_line', mparents, {'name': 'Main', 'color': '#333333'})
    _create(runtime, 'map_item', {**mparents, 'line_id': line['id']},
            {'title': 'Lost anchor', 'start_anchor_type': 'chapter',
             'start_anchor_id': 'chapter_does_not_exist'})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_SOURCE_REFERENCE_INVALID'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)


def test_v10369_hosted_canvas_frozen_geometry_is_not_silently_recast_as_developmental_version(monkeypatch):
    runtime, pid, _ = _project(monkeypatch)
    canvas = _create(runtime, 'canvas', {'project_id': pid}, {'name': 'Draft'})
    parents = {'project_id': pid, 'canvas_id': canvas['id']}
    version = _create(runtime, 'canvas_version', parents, {'name': 'Layout 1', 'position': 0})
    _create(runtime, 'canvas_version_item', {**parents, 'version_id': version['id']},
            {'kind': 'node', 'source_id': 'node_example', 'payload': {'title': 'Do not erase this'}})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)


def test_v10369_hosted_review_project_snapshot_requires_projection(monkeypatch):
    runtime, pid, _ = _project(monkeypatch)
    review = _create(runtime, 'review', {'project_id': pid}, {'name': 'Alpha'})
    _create(runtime, 'review_snapshot', {'project_id': pid, 'review_id': review['id']},
            {'snapshot': {'blocks': [{'text_content': 'Never discard this draft'}]}, 'status': 'active'})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)


def test_v10369_hosted_checkpoint_snapshot_requires_lossless_codec(monkeypatch):
    runtime, pid, _ = _project(monkeypatch)
    _create(runtime, 'checkpoint', {'project_id': pid},
            {'scope': 'book', 'name': 'Earlier draft', 'snapshot': {'blocks': [{'text_content': 'Old version'}]}})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_CHECKPOINT_CODEC_REQUIRED'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)


def test_v10369_source_key_is_rebased_without_changing_unrelated_template_keys(monkeypatch, tmp_path):
    runtime, pid, chapter = _project(monkeypatch)
    m = _create(runtime, 'story_map', {'project_id': pid}, {'name': 'Detailed'})
    parents = {'project_id': pid, 'map_id': m['id']}
    ln = _create(runtime, 'map_line', parents, {'name': 'Main', 'color': '#111111'})
    _create(runtime, 'map_item', {**parents, 'line_id': ln['id']},
            {'title': 'Template stop', 'source_key': 'template:4'})
    _create(runtime, 'map_item', {**parents, 'line_id': ln['id']},
            {'title': 'Chapter stop', 'source_key': f'chapter:{chapter["id"]}'})
    with _restore(tmp_path, HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)) as db:
        keys = {row[0] for row in db.execute('SELECT source_key FROM map_items')}
        local_id = db.execute('SELECT id FROM chapters WHERE title=?', ('Chapter 1',)).fetchone()[0]
        assert keys == {'template:4', f'chapter:{local_id}'}
