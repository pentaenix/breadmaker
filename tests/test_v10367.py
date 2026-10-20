"""M9B hosted -> current local package contracts (user-operated verifier only).

No remote credentials; hosted memory Firestore/GCS source, real unchanged local
Schema-42 package verification and import as independent new local identity.
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

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.memory import MemoryDocumentStore
from hosted.local_portability import LocalPortabilityError, to_local_package
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime
from workspace import Workspace


def _source(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, objects = MemoryDocumentStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=objects)
    pid = runtime.projects.create('From the hosted cloud')['id']
    chapter = runtime.projects.domain.list('chapter', {'project_id': pid}, user_id=_DEV_USER_ID)[0]
    blocks = runtime.projects.domain.list('manuscript_block', {'project_id': pid}, user_id=_DEV_USER_ID)
    block = blocks[0]
    runtime.projects.domain.update(
        'manuscript_block', {'project_id': pid, 'block_id': block['id']},
        {'content_html': '<p>A complete writer-owned manuscript</p>',
         'text_content': 'A complete writer-owned manuscript'},
        user_id=_DEV_USER_ID, operation='m9b_content_change',
    )
    return runtime, pid, chapter, block


def test_v10367_hosted_export_verifies_and_imports_using_unchanged_local_code(monkeypatch, tmp_path):
    runtime, pid, chapter, block = _source(monkeypatch)
    package = HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)
    with zipfile.ZipFile(io.BytesIO(package)) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        assert manifest['package_format'] == 'breadmaker-project'
        assert manifest['package_version'] == 2 and manifest['schema_version'] == 42
        assert manifest['project_id'] == pid
        assert 'project.sqlite3' in manifest['file_hashes']
    path = tmp_path / 'cloud-to-local.breadmaker'
    path.write_bytes(package)
    target = Workspace(tmp_path / 'local-installation')
    target.init()
    verified = target.verify_package(path)
    assert verified['ok'] and verified['project_id'] == pid
    restored = target.import_project(path)
    assert restored['id'] != pid   # no imported hosted authorization/identity
    with sqlite3.connect(Path(restored['path']) / 'project.sqlite3') as con:
        row = con.execute('SELECT text_content,content_html FROM manuscript_blocks').fetchone()
        assert row == ('A complete writer-owned manuscript', '<p>A complete writer-owned manuscript</p>')
        assert con.execute('SELECT title FROM projects').fetchone()[0] == 'From the hosted cloud'
        assert con.execute('PRAGMA foreign_key_check').fetchone() is None


def test_v10367_authority_fence_refuses_concurrent_hosted_edit(monkeypatch):
    runtime, pid, _, _ = _source(monkeypatch)
    service = HostedProjectPackageService(runtime.projects)
    native = service.export_bytes
    def race(project_id, user_id):
        payload = native(project_id, user_id)
        runtime.projects.rename(pid, 'Changed during snapshot')
        return payload
    service.export_bytes = race
    with pytest.raises(DocumentConflict, match='HOSTED_PORTABLE_CONCURRENT_MUTATION'):
        service.export_local_bytes(pid, _DEV_USER_ID)


def test_hosted_local_package_restores_as_fresh_hosted_project(monkeypatch):
    runtime,pid,_chapter,_block=_source(monkeypatch)
    service=HostedProjectPackageService(runtime.projects)
    package=service.export_local_bytes(pid,_DEV_USER_ID)
    restored=service.import_bytes(package,_DEV_USER_ID,name_override='Isolated restored project')
    assert restored['id']!=pid
    rows=runtime.projects.domain.list('manuscript_block',{'project_id':restored['id']},user_id=_DEV_USER_ID)
    assert any(row['text_content']=='A complete writer-owned manuscript' for row in rows)
    assert runtime.projects.authority.get_for_user(restored['id'],_DEV_USER_ID)['generation']>=1
    # A restore must remain portable; chapter -> document-tab references may
    # point to an entity translated later in the legacy table ordering.
    assert service.export_local_bytes(restored['id'],_DEV_USER_ID)


def test_v10367_untranslatable_hosted_field_fails_closed(monkeypatch):
    runtime, pid, _, _ = _source(monkeypatch)
    service = HostedProjectPackageService(runtime.projects)
    source = service.export_bytes(pid, _DEV_USER_ID)
    with zipfile.ZipFile(io.BytesIO(source)) as zf:
        manifest = zf.read('manifest.json')
        payload = json.loads(zf.read('project.json'))
    payload['entities']['chapter'][0]['data']['experimental_only_cloud_field'] = 'Never discard this.'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', manifest)
        zf.writestr('project.json', json.dumps(payload))
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_UNREPRESENTABLE_FIELD'):
        to_local_package(out.getvalue(), project_generation=runtime.projects.get(pid)['generation'])


def test_v10367_missing_parent_is_not_translated_to_null(monkeypatch):
    runtime, pid, _, _ = _source(monkeypatch)
    source = HostedProjectPackageService(runtime.projects).export_bytes(pid, _DEV_USER_ID)
    with zipfile.ZipFile(io.BytesIO(source)) as zf:
        manifest = zf.read('manifest.json')
        payload = json.loads(zf.read('project.json'))
    payload['entities']['chapter'][0]['data']['tab_id'] = 'tab_foreign'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', manifest)
        zf.writestr('project.json', json.dumps(payload))
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_DANGLING_REFERENCE'):
        to_local_package(out.getvalue(), project_generation=runtime.projects.get(pid)['generation'])


def test_v10367_hosted_recovery_archive_format_is_unchanged(monkeypatch):
    runtime, pid, _, _ = _source(monkeypatch)
    payload = HostedProjectPackageService(runtime.projects).export_bytes(pid, _DEV_USER_ID)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        manifest = json.loads(zf.read('manifest.json'))
        assert manifest['format'] == 'breadmaker-hosted-project'
        assert manifest['format_version'] == 3
        assert 'project.json' in zf.namelist()


def test_v10367_hosted_v3_restore_creates_new_project_on_a_fresh_runtime(monkeypatch):
    docs, objects = MemoryDocumentStore(), MemoryObjectStore()
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    first = build_hosted_dev_runtime(documents=docs, objects=objects)
    pid = first.projects.create('Original')['id']
    source = HostedProjectPackageService(first.projects).export_bytes(pid, _DEV_USER_ID)
    second = build_hosted_dev_runtime(documents=docs, objects=objects)
    restored = HostedProjectPackageService(second.projects).import_bytes(
        source, _DEV_USER_ID, operation='m9b_restore_from_archive'
    )
    assert restored['id'] != pid
    after = second.projects.domain.list('chapter', {'project_id': restored['id']}, user_id=_DEV_USER_ID)
    assert any(chapter['title'] == 'Chapter 1' for chapter in after)
    assert second.projects.get(pid)['id'] == pid


def test_v10367_local_export_rejects_unresolved_asset_reference(monkeypatch):
    runtime, pid, _, block = _source(monkeypatch)
    runtime.projects.domain.update(
        'manuscript_block', {'project_id': pid, 'block_id': block['id']},
        {'image_path': '/api/assets/img_' + '0' * 24 + '.png'},
        user_id=_DEV_USER_ID, operation='m9b_fake_missing_asset',
    )
    with pytest.raises(LocalPortabilityError, match='HOSTED_PORTABLE_ASSET_REFERENCE_MISSING'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid, _DEV_USER_ID)
