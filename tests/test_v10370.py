"""M9B2B restricted historical conversion; USER executes verification only.

These tests use shared memory hosted adapters and the unchanged local verifier
and importer. They must never be executed by the assistant/agent.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.local_portability import LocalPortabilityError
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime
from workspace import Workspace


def _make(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    runtime = build_hosted_dev_runtime(documents=MemoryDocumentStore(), objects=MemoryObjectStore())
    pid = runtime.projects.create('My novel')['id']
    domain = runtime.projects.domain
    block = domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID)[0]
    domain.update('manuscript_block', {'project_id': pid, 'block_id': block['id']},
                  {'content_html': '<p>Original frozen text</p>', 'text_content': 'Original frozen text'},
                  user_id=_DEV_USER_ID, operation='m9b2b_freeze_text')
    return runtime, pid, block


def _create(runtime, pid, logical, parents, content):
    return runtime.projects.domain.create(logical, {'project_id': pid, **parents}, content, user_id=_DEV_USER_ID)


def _restore(tmp_path, package):
    path = tmp_path / 'export.breadmaker'
    path.write_bytes(package)
    ws = Workspace(tmp_path / 'local')
    ws.init()
    assert ws.verify_package(path)['ok']
    restored = ws.import_project(path)
    return sqlite3.connect(Path(restored['path']) / 'project.sqlite3')


def test_v10370_primary_hosted_tab_role_survives_local_import(monkeypatch, tmp_path):
    runtime, pid, _ = _make(monkeypatch)
    with _restore(tmp_path, HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)) as db:
        assert db.execute('SELECT COUNT(*) FROM document_tabs WHERE is_manuscript=1').fetchone()[0] == 1


def test_v10370_read_only_frozen_review_preserves_old_text_and_links_baseline(monkeypatch, tmp_path):
    runtime, pid, block = _make(monkeypatch)
    domain = runtime.projects.domain
    review = _create(runtime, pid, 'review', {}, {'name': 'Alpha Reading'})
    parents = {'project_id': pid}
    tabs = domain.list('document_tab', parents, user_id=_DEV_USER_ID)
    frozen = {'project': runtime.projects.get(pid), 'tabs': tabs, 'documents': tabs,
              'chapters': domain.list('chapter', parents, user_id=_DEV_USER_ID),
              'scenes': domain.list('scene', parents, user_id=_DEV_USER_ID),
              'blocks': domain.list('manuscript_block', parents, user_id=_DEV_USER_ID),
              'reviews': domain.list('review', parents, user_id=_DEV_USER_ID),
              'canvases': [], 'maps': [], 'labels': [], 'icon_markers': [], 'scene_gaps': []}
    _create(runtime, pid, 'review_snapshot', {'review_id': review['id']},
            {'snapshot': frozen, 'status': 'active'})
    domain.update('review', {'project_id': pid, 'review_id': review['id']}, {'status': 'active'},
                  user_id=_DEV_USER_ID, operation='m9b2b_review_active')
    domain.update('manuscript_block', {'project_id': pid, 'block_id': block['id']},
                  {'content_html': '<p>New draft</p>', 'text_content': 'New draft'},
                  user_id=_DEV_USER_ID, operation='m9b2b_edit_after_freeze')
    with _restore(tmp_path, HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)) as db:
        assert db.execute('SELECT status FROM reviews').fetchone()[0] == 'open'
        assert db.execute('SELECT text_content FROM manuscript_blocks').fetchone()[0] == 'New draft'
        assert db.execute('SELECT text_content FROM review_snapshot_blocks').fetchone()[0] == 'Original frozen text'
        assert db.execute('SELECT COUNT(*) FROM review_snapshot_checkpoints').fetchone()[0] == 1
        blob, digest = db.execute('SELECT snapshot_blob,snapshot_hash FROM checkpoints').fetchone()
        raw = zlib.decompress(blob)
        assert hashlib.sha256(raw).hexdigest() == digest
        assert json.loads(raw)['blocks'][0]['text_content'] == 'Original frozen text'


def test_v10370_checkpoint_version_one_preserves_frozen_content(monkeypatch, tmp_path):
    runtime, pid, _ = _make(monkeypatch)
    domain = runtime.projects.domain
    parents = {'project_id': pid}
    tab = domain.list('document_tab', parents, user_id=_DEV_USER_ID)[0]
    chapters = domain.list('chapter', parents, user_id=_DEV_USER_ID)
    blocks = domain.list('manuscript_block', parents, user_id=_DEV_USER_ID)
    frozen = {'version': 1, 'scope': 'book', 'captured_at': '2026-10-10T00:00:00Z',
              'tab': tab, 'chapters': chapters, 'blocks': blocks, 'scenes': [],
              'annotations': [], 'comment_threads': [], 'comment_messages': [],
              'include_scenes': True, 'include_markup': False, 'include_notes': False}
    _create(runtime, pid, 'checkpoint', {}, {'scope': 'book', 'name': 'Old manuscript', 'snapshot': frozen})
    with _restore(tmp_path, HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)) as db:
        blob, digest = db.execute('SELECT snapshot_blob,snapshot_hash FROM checkpoints WHERE name=?', ('Old manuscript',)).fetchone()
        raw = zlib.decompress(blob)
        assert hashlib.sha256(raw).hexdigest() == digest
        recovered = json.loads(raw)
        assert recovered['blocks'][0]['text_content'] == 'Original frozen text'
        assert recovered['chapters'][0]['id'] == db.execute('SELECT id FROM chapters').fetchone()[0]


def test_v10370_checkpoint_with_markup_cannot_drop_history(monkeypatch):
    runtime, pid, _ = _make(monkeypatch)
    _create(runtime, pid, 'checkpoint', {}, {'scope': 'book', 'snapshot': {'version': 1, 'scope': 'book',
        'tab': {}, 'chapters': [], 'blocks': [], 'scenes': [], 'annotations': [{'body': 'Keep me'}],
        'comment_threads': [], 'comment_messages': [], 'include_markup': True, 'include_notes': False}})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_CHECKPOINT_MARKUP_CODEC_REQUIRED'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)


def test_v10370_unrecognized_frozen_review_round_still_fails_closed(monkeypatch):
    runtime, pid, _ = _make(monkeypatch)
    review = _create(runtime, pid, 'review', {}, {'name': 'Full reading'})
    _create(runtime, pid, 'review_snapshot', {'review_id': review['id']},
            {'snapshot': {'blocks': [{'text_content': 'Preserve this'}]}, 'status': 'active'})
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_REVIEW_SNAPSHOT_PROJECTION_REQUIRED'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)
