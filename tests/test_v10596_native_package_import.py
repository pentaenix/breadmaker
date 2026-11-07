"""Standalone WAL backups and native relational paths survive hosted imports."""
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from hosted.native_package import prepare_native_package, canonical_connection_hash
from hosted.packages import HostedProjectPackageService
from hosted.firestore.repositories import render_model_path
from hosted.firestore.model_registry import MODELS
from hosted.domain_store import _ID_FIELDS
from revision_store.local_store import canonical_state_hash
from storage.schema import SCHEMA_SQL, SCHEMA_VERSION
from storage.schema_contracts import _GENERATION_CANONICAL_TABLES
from tests.test_v10353 import _services, OWNER, PNG
from tests.test_v10573 import fenced_service

PID = 'c' * 32
TARGET = 'd' * 32
STAMP = '2026-01-01T00:00:00Z'


def database(tmp_path, *, wal=False):
    path = tmp_path / 'source.sqlite3'
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if wal:
        assert con.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    con.executescript(SCHEMA_SQL)
    con.execute("INSERT INTO storage_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    insert(con, 'projects', id=1, project_uid=PID, title='Synthetic native import')
    insert(con, 'document_tabs', id=1, project_id=1, title='Manuscript', is_manuscript=1)
    insert(con, 'chapters', id=1, project_id=1, tab_id=1, title='First chapter', position=0)
    insert(con, 'manuscript_blocks', id='original-block', project_id=1, tab_id=1, chapter_id=1,
           position=0, text_content='An intact synthetic paragraph.', content_html='<p>An intact synthetic paragraph.</p>')
    con.commit()
    return con, path


def insert(con, table, **values):
    columns = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
    for field in ('created_at', 'updated_at'):
        if field in columns:
            values.setdefault(field, STAMP)
    names = ','.join('"' + key + '"' for key in values)
    con.execute(f'INSERT INTO "{table}"({names}) VALUES({",".join("?" for _ in values)})', list(values.values()))


def package(con, path, *, files=None, canonical=True, manifest_edit=None):
    con.commit()
    if con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal':
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    contents = {'project.sqlite3': path.read_bytes(), **(files or {})}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
    manifest = {'package_format': 'breadmaker-project', 'package_version': 2, 'schema_version': 42,
                'project_id': PID, 'project_generation': 1, 'file_hashes': hashes,
                'project_fingerprint': hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    if canonical:
        manifest['canonical_state_hash'] = canonical_state_hash(path, _GENERATION_CANONICAL_TABLES, hashes)
    if manifest_edit:
        manifest_edit(manifest)
    contents['manifest.json'] = json.dumps(manifest).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    return out.getvalue()


def rewrite(raw, edit):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    edit(entries)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def test_checkpointed_wal_backup_imports_without_touching_source_bytes(tmp_path):
    con, path = database(tmp_path, wal=True)
    try:
        raw = package(con, path)
        original = path.read_bytes()
        assert original[18:20] == b'\x02\x02'
        service, docs, _ = fenced_service()
        restored = service.import_bytes(raw, OWNER, operation='synthetic-wal-native-import')
        blocks = service.domain.list('manuscript_block', {'project_id': restored['id']}, user_id=OWNER)
        assert [row['text_content'] for row in blocks] == ['An intact synthetic paragraph.']
        assert docs.get(('projects', restored['id'])) is not None
        assert restored['id'] != PID and path.read_bytes() == original
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    finally:
        con.close()


def test_connection_hash_matches_existing_local_canonical_contract(tmp_path):
    con, path = database(tmp_path)
    try:
        hashes = {'project.sqlite3': hashlib.sha256(path.read_bytes()).hexdigest(), 'assets/synthetic.png': 'f' * 64}
        assert canonical_connection_hash(con, hashes) == canonical_state_hash(path, _GENERATION_CANONICAL_TABLES, hashes)
    finally:
        con.close()


@pytest.mark.parametrize('case', ['missing_hash', 'changed_hash', 'stale_generation', 'wal_sidecar', 'db_bytes', 'canonical_mismatch'])
def test_unverified_or_incomplete_wal_package_never_stages(tmp_path, case):
    con, path = database(tmp_path, wal=True)
    try:
        raw = package(con, path, canonical=case != 'missing_hash')
        def edit(entries):
            manifest = json.loads(entries['manifest.json'])
            if case == 'changed_hash':
                manifest['file_hashes']['project.sqlite3'] = '0' * 64
            elif case == 'stale_generation':
                manifest['project_generation'] = 2
            elif case == 'wal_sidecar':
                entries['project.sqlite3-wal'] = b'not a standalone backup'
            elif case == 'db_bytes':
                entries['project.sqlite3'] += b'changed bytes'
            elif case == 'canonical_mismatch':
                manifest['canonical_state_hash'] = '0' * 64
            entries['manifest.json'] = json.dumps(manifest).encode()
        raw = rewrite(raw, edit)
        service, docs, objects = fenced_service()
        with pytest.raises(ValueError, match='HOSTED_PACKAGE_'):
            service.import_bytes(raw, OWNER, operation='synthetic-invalid-wal-' + case)
        assert service.projects.list(OWNER) == []
        assert list(objects.list_prefix('projects/')) == []
        assert not [key for key in docs._documents if key[0] == 'projects']
    finally:
        con.close()


def hierarchy(con):
    insert(con, 'scenes', id=1, project_id=1, tab_id=1, position=0, title='Scene')
    insert(con, 'canvases', id=1, project_id=1, name='Parent', position=0)
    insert(con, 'canvases', id=2, project_id=1, parent_id=1, name='Child', position=1)
    insert(con, 'canvas_versions', id=1, canvas_id=2, position=0, name='Version')
    insert(con, 'canvas_version_items', id=1, version_id=1, entity_type='chapter', entity_id=1)
    insert(con, 'story_maps', id=1, project_id=1, name='Timeline', position=0)
    insert(con, 'map_lines', id=1, map_id=1, name='Story', color='#123456')
    insert(con, 'map_items', id=1, line_id=1, title='Scene stop', start_anchor_type='scene', start_anchor_id='1', source_key='scene:1')
    insert(con, 'map_items', id=2, line_id=1, title='Text span', start_anchor_type='manuscript_range', start_block_id='original-block')
    insert(con, 'icon_markers', id=1, project_id=1, name='Icon', icon='star')
    insert(con, 'markup_labels', id=1, project_id=1, name='Label', color='#123456')
    insert(con, 'canvas_nodes', id=1, canvas_id=2, icon_marker_id=1, chapter_id=1)
    insert(con, 'canvas_nodes', id=2, canvas_id=2, scene_id=1)
    insert(con, 'canvas_edges', id=1, canvas_id=2, from_node_id=1, to_node_id=2, marker_id=1)
    insert(con, 'tasks', id=1, project_id=1, title='Task', marker_id=1)
    insert(con, 'references_map', id=1, project_id=1, source_type='task', source_id='1', target_type='chapter', target_id='1', start_block_id='original-block')
    con.commit()


def test_nested_paths_and_live_semantic_references_are_rebased_before_staging(tmp_path):
    con, path = database(tmp_path)
    try:
        hierarchy(con)
        plan = prepare_native_package(package(con, path), TARGET)
        records = {(row['entity'], row['id']): row for row in plan['records']}
        item = records['canvas_version_item', 'item_legacy_1']
        assert item['parents'] == {'project_id': TARGET, 'canvas_id': 'canvas_legacy_2', 'version_id': 'version_legacy_1'}
        assert item['data']['version_id'] == 'version_legacy_1'
        assert item['data']['entity_id'] == 'chapter_legacy_1'
        stop = records['map_item', 'item_legacy_1']
        assert stop['parents'] == {'project_id': TARGET, 'map_id': 'map_legacy_1', 'line_id': 'line_legacy_1'}
        assert stop['data']['start_anchor_id'] == 'scene_legacy_1'
        assert stop['data']['source_key'] == 'scene:scene_legacy_1'
        assert records['map_item', 'item_legacy_2']['data']['start_block_id'] == 'b_legacy_original-block'
        assert records['canvas', 'canvas_legacy_2']['data']['parent_id'] == 'canvas_legacy_1'
        assert records['canvas_node', 'node_legacy_1']['data']['icon_marker_id'] == 'marker_legacy_1'
        assert records['canvas_edge', 'edge_legacy_1']['data']['marker_id'] == 'label_legacy_1'
        assert records['task', 'task_legacy_1']['data']['marker_id'] == 'label_legacy_1'
        ref = records['cross_feature_reference', 'reference_legacy_1']['data']
        assert (ref['source_id'], ref['target_id'], ref['start_block_id']) == ('task_legacy_1', 'chapter_legacy_1', 'b_legacy_original-block')
        for entry in plan['records']:
            render_model_path(MODELS[entry['entity']], **entry['parents'], **{_ID_FIELDS[entry['entity']][0]: entry['id']})
        service, _, _ = fenced_service()
        restored = service.import_bytes(package(con, path), OWNER)
        rows = service.domain.list('map_item', {'project_id': restored['id'], 'map_id': 'map_legacy_1', 'line_id': 'line_legacy_1'}, user_id=OWNER)
        assert len(rows) == 2
    finally:
        con.close()


@pytest.mark.parametrize('case', ['missing_parent', 'missing_semantic', 'conflicting_parent', 'participant_authority', 'unsafe_asset', 'missing_image', 'unsafe_html'])
def test_invalid_plan_fails_before_any_provider_content_write(tmp_path, case):
    con, path = database(tmp_path)
    try:
        hierarchy(con)
        files = None
        if case == 'missing_parent':
            con.execute('UPDATE canvas_version_items SET version_id=999')
        elif case == 'missing_semantic':
            con.execute("UPDATE map_items SET start_anchor_id='999' WHERE id=1")
        elif case == 'conflicting_parent':
            con.execute('UPDATE canvas_edges SET canvas_id=1')
        elif case == 'missing_image':
            con.execute("UPDATE manuscript_blocks SET block_type='image',content_html='',text_content='',image_path='/api/assets/img_missing.png'")
        elif case == 'unsafe_html':
            con.execute("UPDATE manuscript_blocks SET content_html='<script>bad()</script>'")
        elif case == 'participant_authority':
            con.execute('ALTER TABLE review_participants ADD COLUMN user_id TEXT')
        elif case == 'unsafe_asset':
            files = {'assets/img_bad.png': b'not an image'}
        raw = package(con, path, files=files)
        service, docs, objects = fenced_service()
        with pytest.raises(ValueError, match='HOSTED_PACKAGE_'):
            service.import_bytes(raw, OWNER)
        assert list(objects.list_prefix('projects/')) == []
        assert not [key for key in docs._documents if key[0] == 'projects']
    finally:
        con.close()


def test_delete_journal_packages_without_optional_canonical_hash_still_import(tmp_path):
    con, path = database(tmp_path)
    try:
        assert path.read_bytes()[18:20] == b'\x01\x01'
        plan = prepare_native_package(package(con, path, canonical=False), TARGET)
        assert plan['title'] == 'Synthetic native import'
    finally:
        con.close()


def test_uncheckpointed_committed_wal_content_is_not_silently_replaced_by_main_db(tmp_path):
    con, path = database(tmp_path, wal=True)
    try:
        raw = package(con, path)
        con.execute("UPDATE manuscript_blocks SET text_content='Only in the WAL'")
        con.commit()
        assert path.with_name(path.name + '-wal').stat().st_size > 0
        def edit(entries):
            manifest = json.loads(entries['manifest.json'])
            manifest['canonical_state_hash'] = canonical_connection_hash(con, manifest['file_hashes'])
            entries['manifest.json'] = json.dumps(manifest).encode()
        with pytest.raises(ValueError, match='CANONICAL_STATE_MISMATCH'):
            prepare_native_package(rewrite(raw, edit), TARGET)
    finally:
        con.close()


def test_canvas_frozen_workspace_retains_deleted_entities_and_rebases_links(tmp_path):
    con, path = database(tmp_path)
    try:
        hierarchy(con)
        con.execute('UPDATE canvas_version_items SET entity_id=999')
        workspace = {'schema': 3, 'nodes': [{'id': 1, 'chapter_id': 999, 'label_id': 1, 'body': 'Frozen body'}],
                     'edges': [{'id': 1, 'from_node_id': 1, 'to_node_id': 2, 'marker_id': 1}],
                     'structural_styles': {'chapter:999': {'label_id': 1, 'color': '#123456'}},
                     'viewport': {'zoom': 1.5}}
        con.execute('UPDATE canvas_versions SET workspace_json=?', (json.dumps(workspace),))
        plan = prepare_native_package(package(con, path), TARGET)
        version = next(row for row in plan['records'] if row['entity'] == 'canvas_version')
        saved = json.loads(version['data']['workspace_json'])
        assert saved['nodes'] == [{'id': 'node_legacy_1', 'chapter_id': 'chapter_legacy_999', 'label_id': 'label_legacy_1', 'body': 'Frozen body'}]
        assert saved['edges'][0]['from_node_id'] == 'node_legacy_1'
        assert saved['structural_styles']['chapter:chapter_legacy_999']['label_id'] == 'label_legacy_1'
        assert saved['viewport'] == {'zoom': 1.5}
        assert next(row for row in plan['records'] if row['entity'] == 'canvas_version_item')['data']['entity_id'] == 'chapter_legacy_999'
    finally:
        con.close()


def test_same_image_bytes_under_native_aliases_keep_both_api_paths(tmp_path):
    from hosted.runtime_services import HostedCommittedAssetStore
    con, path = database(tmp_path)
    try:
        con.execute("UPDATE manuscript_blocks SET block_type='image', content_html='', text_content='', image_path='/api/assets/img_original.png'")
        raw = package(con, path, files={'assets/img_original.png': PNG, 'assets/img_alias.png': PNG})
        service, _, _ = fenced_service()
        restored = service.import_bytes(raw, OWNER)
        assets = HostedCommittedAssetStore(service.documents, service.objects, lambda: OWNER, lambda: restored['id'])
        assert assets.read_blob('/api/assets/img_original.png') == PNG
        assert assets.read_blob('/api/assets/img_alias.png') == PNG
    finally:
        con.close()


def test_shared_preflight_preserves_hosted_v3_records_and_paths():
    from hosted.native_package import prepare_project_package
    _, _, _, _, projects = _services()
    pid = projects.create('Hosted v3 source')['id']
    service = HostedProjectPackageService(projects)
    raw = service.export_bytes(pid, OWNER)
    plan = prepare_project_package(raw, TARGET)
    assert plan['title'] == 'Hosted v3 source'
    assert {row['entity'] for row in plan['records']} == {'chapter', 'document_tab', 'manuscript_block'}
    assert all(row['parents']['project_id'] == TARGET for row in plan['records'])
    with pytest.raises(ValueError, match='UNSUPPORTED'):
        prepare_native_package(raw, TARGET)


def test_v3_participant_account_bindings_never_transfer_to_imported_project():
    from hosted.native_package import prepare_project_package
    from hosted.firestore.contracts import ProjectUnavailable
    from tests.test_v10353 import OUTSIDER
    _, _, _, _, projects = _services()
    pid = projects.create('V3 participant portability')['id']
    review = projects.domain.create('review', {'project_id': pid}, {'name': 'Review'}, user_id=OWNER)
    projects.domain.create('review_participant', {'project_id': pid, 'review_id': review['id']},
        {'display_name': 'Retained participant', 'status': 'in_progress', 'user_id': OUTSIDER,
         'invitation_id': 'nonportable-invitation', 'role': 'owner'}, user_id=OWNER)
    source = HostedProjectPackageService(projects).export_bytes(pid, OWNER)
    plan = prepare_project_package(source, TARGET)
    saved = next(row for row in plan['records'] if row['entity'] == 'review_participant')['data']
    assert saved['display_name'] == 'Retained participant'
    assert not {'user_id', 'invitation_id', 'role'} & set(saved)
    service, _, _ = fenced_service()
    restored = service.import_bytes(source, OWNER)
    participant = service.domain.list('review_participant', {'project_id': restored['id'], 'review_id': review['id']}, user_id=OWNER)[0]
    assert not {'user_id', 'invitation_id', 'role'} & set(participant)
    with pytest.raises(ProjectUnavailable):
        service.projects.authority.get_for_user(restored['id'], OUTSIDER)


@pytest.mark.parametrize('case', ['pointer', 'nested_pointer', 'missing_body', 'identity', 'unsafe_html', 'missing_image'])
def test_v3_invalid_payloads_fail_before_any_inline_or_resumable_staging(case):
    from hosted.native_package import prepare_project_package
    _, _, _, _, projects = _services()
    pid = projects.create('Safe source')['id']
    source = HostedProjectPackageService(projects).export_bytes(pid, OWNER)
    def edit(entries):
        document = json.loads(entries['project.json'])
        row = document['entities']['manuscript_block'][0]['data']
        if case == 'pointer':
            row.pop('content_html', None)
            row.pop('text_content', None)
            row['payload_object'] = {'object_name': 'projects/' + 'f' * 32 + '/foreign', 'object_generation': 1}
        elif case == 'nested_pointer':
            row['unexpected'] = {'object_name': 'foreign'}
        elif case == 'missing_body':
            row.pop('content_html', None)
            row.pop('text_content', None)
        elif case == 'identity':
            row['id'] = 'different_block'
        elif case == 'unsafe_html':
            row['content_html'] = '<script>bad()</script>'
        elif case == 'missing_image':
            row.update(block_type='image', image_path='/api/assets/img_missing.png', content_html='', text_content='')
        entries['project.json'] = json.dumps(document).encode()
    source = rewrite(source, edit)
    with pytest.raises(ValueError, match='HOSTED_PACKAGE_'):
        prepare_project_package(source, TARGET)
    service, docs, objects = fenced_service()
    with pytest.raises(ValueError, match='HOSTED_PACKAGE_'):
        service.import_bytes(source, OWNER)
    assert not list(objects.list_prefix('projects/'))
    assert not [key for key in docs._documents if key[0] == 'projects']


def test_crafted_v3_participant_authority_is_rejected_before_staging():
    from hosted.native_package import prepare_project_package
    _, _, _, _, projects = _services()
    pid = projects.create('Safe review source')['id']
    review = projects.domain.create('review', {'project_id': pid}, {'name': 'Review'}, user_id=OWNER)
    projects.domain.create('review_participant', {'project_id': pid, 'review_id': review['id']},
                           {'display_name': 'Local participant'}, user_id=OWNER)
    source = HostedProjectPackageService(projects).export_bytes(pid, OWNER)
    def edit(entries):
        data = json.loads(entries['project.json'])
        data['entities']['review_participant'][0]['data']['user_id'] = OWNER
        entries['project.json'] = json.dumps(data).encode()
    with pytest.raises(ValueError, match='PARTICIPANT_AUTHORITY_FORBIDDEN'):
        prepare_project_package(rewrite(source, edit), TARGET)


def test_recursive_view_replacing_product_table_is_rejected_before_execution(tmp_path):
    con, path = database(tmp_path)
    try:
        con.execute('DROP TABLE projects')
        con.execute('CREATE VIEW projects AS WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x) SELECT n AS id FROM x')
        raw = package(con, path, canonical=False)
        with pytest.raises(ValueError, match='SCHEMA_OBJECT_INVALID'):
            prepare_native_package(raw, TARGET)
    finally:
        con.close()


@pytest.mark.parametrize('html', ['<img SRC=/api/assets/img_missing.png>', '<IMG src="/api/assets/img_missing.png"/>'])
def test_cut_image_closure_uses_html_attribute_normalization(tmp_path, html):
    con, path = database(tmp_path)
    try:
        insert(con, 'cuts', id=1, project_id=1, title='Missing image Cut', content_html=html)
        with pytest.raises(ValueError, match='ASSET_REFERENCE_MISSING'):
            prepare_native_package(package(con, path), TARGET)
    finally:
        con.close()


def test_native_checkpoint_image_projection_imports_with_original_api_path(tmp_path):
    import zlib
    from hosted.runtime_services import HostedCommittedAssetStore
    con, path = database(tmp_path)
    try:
        digest = hashlib.sha256(PNG).hexdigest()
        image_path = '/api/assets/img_' + digest[:24] + '.png'
        snapshot = {'version': 1, 'scope': 'chapter', 'tab': {'id': 1, 'title': 'Manuscript', 'created_at': STAMP},
                    'chapter': {'id': 1, 'tab_id': 1, 'title': 'First chapter', 'created_at': STAMP},
                    'blocks': [{'id': 'historical-image', 'block_type': 'image', 'image_path': image_path}],
                    'scenes': [], 'annotations': [], 'comment_threads': [], 'comment_messages': []}
        raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
        insert(con, 'checkpoints', id=1, project_id=1, scope='chapter', source_tab_id=1, source_chapter_id=1,
               snapshot_blob=zlib.compress(raw), snapshot_hash=hashlib.sha256(raw).hexdigest())
        insert(con, 'checkpoint_assets', hash=digest, payload=PNG, byte_size=len(PNG))
        insert(con, 'checkpoint_asset_refs', checkpoint_id=1, asset_path=image_path, asset_hash=digest)
        service, _, _ = fenced_service()
        restored = service.import_bytes(package(con, path), OWNER)
        checkpoint = service.domain.list('checkpoint', {'project_id': restored['id']}, user_id=OWNER)[0]
        assert checkpoint['snapshot']['blocks'][0]['image_path'] == image_path
        assets = HostedCommittedAssetStore(service.documents, service.objects, lambda: OWNER, lambda: restored['id'])
        assert assets.read_blob(image_path) == PNG
        # The ordinary public v3 round trip carries the body, never its GCS pointer.
        again = service.import_bytes(service.export_bytes(restored['id'], OWNER), OWNER)
        assert service.domain.list('checkpoint', {'project_id': again['id']}, user_id=OWNER)[0]['snapshot']['blocks'][0]['image_path'] == image_path
    finally:
        con.close()
