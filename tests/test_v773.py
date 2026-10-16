from __future__ import annotations

from workspace import workspace

from storage import assets as storage_assets
from storage import context as storage_context
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import hashlib
import json
import random
import re
import sqlite3
import threading
from pathlib import Path

import pytest

import app as app_module
from revision_store.local_store import LocalRevisionStore, RevisionStoreCorruption, StaleRevisionPublication


def _store(*, interval: int = 10, root: Path | None = None) -> LocalRevisionStore:
    assert storage_context.PROJECT_ROOT is not None
    # Production History owns revision_history/v1 with the frozen interval. Tests
    # that intentionally exercise alternate checkpoint intervals use a separate
    # repository so the post-commit History worker cannot pre-create a conflicting
    # store contract.
    store_root = root or (storage_context.PROJECT_ROOT / 'revision_history' / ('v1' if interval == 10 else f'v773-test-{interval}'))
    return LocalRevisionStore(
        store_root,
        storage_context.DB_PATH,
        storage_context.ASSET_DIR,
        canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
        checkpoint_interval=interval,
        workspace_project_id=storage_mutations.configured_project_identity()[0],
    )


def _advance(title: str) -> int:
    with storage_mutations.tx(operation='v773-test-mutation') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (title, storage_values.now(), pid))
    return storage_mutations.current_project_generation()


def _read_title(database: Path) -> tuple[str, int]:
    con = sqlite3.connect(database)
    try:
        row = con.execute('SELECT title,project_generation FROM projects ORDER BY id LIMIT 1').fetchone()
        return str(row[0]), int(row[1])
    finally:
        con.close()


def _logical_state(database: Path, asset_root: Path) -> dict:
    """Independent exact-state oracle used by the randomized reconstruction test."""
    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    try:
        tables = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        state: dict[str, object] = {}
        for table in sorted(storage_schema_contracts._GENERATION_CANONICAL_TABLES):
            if table not in tables:
                continue
            info = list(con.execute(f'PRAGMA table_info("{table}")'))
            columns = [str(r[1]) for r in info]
            pk = [str(r[1]) for r in sorted((r for r in info if int(r[5] or 0) > 0), key=lambda r: int(r[5]))]
            order = ','.join(f'"{c}"' for c in pk) if pk else 'rowid'
            rows = []
            for row in con.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
                rows.append(tuple(bytes(v).hex() if isinstance(v, bytes) else v for v in row))
            state[table] = {'columns': columns, 'rows': rows}
        refs: set[str] = set()
        if 'manuscript_blocks' in tables:
            refs.update(str(r[0]) for r in con.execute("SELECT image_path FROM manuscript_blocks WHERE COALESCE(image_path,'')<>''"))
        if 'canvas_nodes' in tables:
            refs.update(str(r[0]) for r in con.execute("SELECT image_path FROM canvas_nodes WHERE COALESCE(image_path,'')<>''"))
        if 'cuts' in tables:
            for row in con.execute("SELECT content_html FROM cuts WHERE COALESCE(content_html,'')<>''"):
                refs.update('/api/assets/' + name for name in re.findall(r'/api/assets/([A-Za-z0-9._/\-]+)', str(row[0] or '')))
    finally:
        con.close()
    assets = {}
    for ref in sorted(refs):
        if not ref.startswith('/api/assets/'):
            continue
        rel = ref.removeprefix('/api/assets/')
        path = asset_root / rel
        assert path.is_file(), ref
        assets[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    state['__assets__'] = assets
    return state


def test_v773_release_contract():
    root = Path(__file__).resolve().parents[1]
    version=tuple(int(x) for x in (root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))
    assert version >= (7,7,3)
    text = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '7.7.3' in text
    assert 'Local Revision Store v1' in text
    assert 'no deletion or GC capability' in text


def test_v773_first_revision_publishes_checkpoint_and_revision_manifest_last(fresh_project):
    store = _store(interval=3)
    stages: list[str] = []
    row = store.capture(label='Initial local revision', creation_reason='test', stage_hook=lambda stage, _ctx: stages.append(stage))
    generation = storage_mutations.current_project_generation()
    assert row['generation_start'] == generation == row['generation_end']
    assert row['parent_revision_id'] is None
    assert row['checkpoint_generation'] == generation
    assert row['distance_from_checkpoint'] == 0
    assert row['idempotent'] is False
    assert stages == ['SNAPSHOT_OBJECT_FINALIZED', 'SNAPSHOT_VERIFIED', 'BEFORE_CHECKPOINT_PUBLICATION', 'CHECKPOINT_PUBLISHED', 'CHECKPOINT_VERIFIED', 'BEFORE_REVISION_PUBLICATION', 'REVISION_PUBLISHED']
    listed = store.list_revisions()
    assert [r['revision_id'] for r in listed] == [row['revision_id']]
    verified = store.verify_revision(row['revision_id'])
    assert verified['canonical_state_hash'] == row['expected_state_hash']
    assert list(store.checkpoints_dir.glob('*.checkpoint.json'))
    assert list(store.revisions_dir.glob('*.revision.json'))


def test_v773_duplicate_capture_same_generation_is_idempotent(fresh_project):
    store = _store()
    first = store.capture(label='First', creation_reason='test')
    before_objects = {p.name for p in store.objects_dir.iterdir()}
    second = store.capture(label='Retry', creation_reason='retry')
    assert second['idempotent'] is True
    assert second['revision_id'] == first['revision_id']
    assert len(store.list_revisions()) == 1
    assert {p.name for p in store.objects_dir.iterdir()} == before_objects


def test_v773_crash_before_revision_publication_leaves_only_non_authoritative_orphans(fresh_project):
    store = _store()
    current_before = _read_title(storage_context.DB_PATH)
    def crash(stage, _ctx):
        if stage == 'BEFORE_REVISION_PUBLICATION':
            raise OSError('simulated process death before revision publication')
    with pytest.raises(OSError, match='simulated process death'):
        store.capture(label='Will not publish', creation_reason='fault', stage_hook=crash)
    assert store.list_revisions() == []
    assert list(store.objects_dir.iterdir())
    assert list(store.checkpoints_dir.glob('*.checkpoint.json'))
    assert _read_title(storage_context.DB_PATH) == current_before
    retry = store.capture(label='Retry after crash', creation_reason='retry')
    assert retry['generation_end'] == current_before[1]
    assert len(store.list_revisions()) == 1


def test_v773_timeout_after_revision_publication_converges_on_retry(fresh_project):
    store = _store()
    published: dict[str, str] = {}
    def timeout(stage, ctx):
        if stage == 'REVISION_PUBLISHED':
            published.update(ctx)
            raise TimeoutError('response lost after durable publication')
    with pytest.raises(TimeoutError, match='response lost'):
        store.capture(label='Accepted then timeout', creation_reason='fault', stage_hook=timeout)
    assert len(store.list_revisions()) == 1
    retry = store.capture(label='Retry', creation_reason='retry')
    assert retry['idempotent'] is True
    assert retry['revision_id'] == published['revision_id']
    assert len(store.list_revisions()) == 1


def test_v773_reverse_completion_order_cannot_move_history_head_backwards(fresh_project):
    store = _store()
    base = store.capture(label='Base', creation_reason='test')
    older_generation = _advance('generation older')
    older_ready = threading.Event()
    release_older = threading.Event()
    errors: list[Exception] = []
    results: dict[str, dict] = {}

    def hold_older(stage, _ctx):
        if stage == 'SNAPSHOT_OBJECT_FINALIZED':
            older_ready.set()
            assert release_older.wait(5)

    def older_worker():
        try:
            results['older'] = store.capture(label='Older', creation_reason='race', stage_hook=hold_older)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=older_worker)
    thread.start()
    assert older_ready.wait(5)
    newer_generation = _advance('generation newer')
    newer = store.capture(label='Newer', creation_reason='race')
    release_older.set()
    thread.join(5)
    assert newer_generation > older_generation > base['generation_end']
    assert newer['generation_end'] == newer_generation
    assert any(isinstance(exc, StaleRevisionPublication) for exc in errors)
    listed = store.list_revisions()
    assert listed[0]['generation_end'] == newer_generation
    assert all(row['generation_end'] != older_generation for row in listed)


def test_v773_checkpoint_lineage_is_bounded(fresh_project):
    store = _store(interval=3)
    rows = [store.capture(label='g0', creation_reason='test')]
    for i in range(1, 9):
        _advance(f'checkpoint-{i}')
        rows.append(store.capture(label=f'g{i}', creation_reason='test'))
    assert max(int(r['distance_from_checkpoint']) for r in rows) <= 2
    assert sum(1 for r in rows if int(r['distance_from_checkpoint']) == 0) >= 3
    for row in rows:
        store.verify_revision(row['revision_id'])


def test_v773_missing_or_corrupt_lineage_fails_closed_without_touching_current(fresh_project):
    store = _store(interval=4)
    first = store.capture(label='Checkpoint anchor', creation_reason='test')
    _advance('child revision')
    child = store.capture(label='Child', creation_reason='test')
    current = _read_title(storage_context.DB_PATH)
    checkpoint = next(store.checkpoints_dir.glob(f'*{first["checkpoint_id"]}*.checkpoint.json'))
    checkpoint.write_text('{truncated', encoding='utf-8')
    with pytest.raises(RevisionStoreCorruption):
        store.verify_revision(child['revision_id'])
    assert _read_title(storage_context.DB_PATH) == current


def test_v773_corrupted_snapshot_byte_is_detected_and_recovery_backup_remains_independent(fresh_project):
    store = _store()
    row = store.capture(label='Good revision', creation_reason='test')
    backup = storage_recovery.backup_project('v773-independent-recovery')
    assert backup
    backup_info = workspace.verify_package(backup)
    assert int(backup_info['project_generation']) == storage_mutations.current_project_generation()
    obj = store.objects_dir / row['snapshot_object_id'] / 'project.sqlite3'
    data = bytearray(obj.read_bytes())
    data[max(0, len(data)//2)] ^= 0x01
    obj.write_bytes(bytes(data))
    current = _read_title(storage_context.DB_PATH)
    with pytest.raises(RevisionStoreCorruption):
        store.verify_revision(row['revision_id'])
    assert _read_title(storage_context.DB_PATH) == current
    assert int(workspace.verify_package(backup)['project_generation']) == int(backup_info['project_generation'])


def test_v773_materialization_reconstructs_exact_selected_generation_and_never_current(fresh_project, tmp_path):
    store = _store()
    first_title = 'historical title'
    first_generation = _advance(first_title)
    first = store.capture(label='Historical', creation_reason='test')
    second_title = 'current title'
    second_generation = _advance(second_title)
    store.capture(label='Current', creation_reason='test')
    target = tmp_path / 'materialized-history'
    result = store.materialize_revision(first['revision_id'], target)
    assert _read_title(result['database']) == (first_title, first_generation)
    assert _read_title(storage_context.DB_PATH) == (second_title, second_generation)
    assert result['canonical_state_hash'] == first['expected_state_hash']


def test_v773_revision_store_has_no_direct_delete_surface(fresh_project):
    store = _store()
    row = store.capture(label='Retain me', creation_reason='test')
    revision_path = next(store.revisions_dir.glob(f'*{row["revision_id"]}*.revision.json'))
    before = revision_path.read_bytes()
    # Deletion remains a separate History-GC capability introduced later; the
    # immutable LocalRevisionStore itself never exposes ad-hoc delete/prune APIs.
    assert not hasattr(store, 'gc') and not hasattr(store, 'delete_revision') and not hasattr(store, 'prune')
    store.verify_revision(row['revision_id'])
    assert revision_path.read_bytes() == before


@pytest.mark.parametrize('seed', list(range(7730, 7750)))
def test_v773_randomized_generation_reconstruction_property(seed, fresh_project, client, tmp_path):
    """One shard of the 2,000-mutation / 200-reconstruction property gate.

    The release-wide property budget remains unchanged, but each shard is a distinct
    pytest item so the global 10-second-per-test reliability rule remains absolute.
    """
    rng = random.Random(seed)
    boot = fresh_project['bootstrap']
    tab = next(t for t in boot['tabs'] if t.get('is_manuscript'))
    scene = client.post('/api/scenes', json={'title': f'Property scene {seed}', 'tab_id': tab['id']}).json()
    label = client.post('/api/labels', json={'name': f'Property marker {seed}', 'color': '#725ac1', 'description': ''}).json()
    icon = client.post('/api/icon-markers', json={'name': f'Property icon {seed}', 'description': '', 'icon': '◆'}).json()
    task = client.post('/api/tasks', json={'title': f'Property task {seed}', 'description': '', 'status': 'open', 'marker_id': label['id']}).json()
    canvas = client.post('/api/canvases', json={'name': f'Property canvas {seed}'}).json()
    for name, data in [('property-a.bin', f'asset-A-{seed}'.encode()), ('property-b.bin', f'asset-B-{seed}'.encode())]:
        storage_assets.durable_write_project_asset(storage_context.ASSET_DIR / name, data)
    node = client.post(f'/api/canvases/{canvas["id"]}/nodes', json={'title': 'Property node', 'image_path': '/api/assets/property-a.bin', 'icon_marker_id': icon['id']}).json()
    story_map = client.post('/api/maps', json={'name': f'Property map {seed}'}).json()
    line = client.post(f'/api/maps/{story_map["id"]}/lines', json={'name': 'Property line'}).json()

    store = _store(interval=7)
    expected: dict[str, dict] = {}
    # Twenty parametrized items × 100 mutations = 2,000 canonical mutations total.
    # Each shard publishes/reconstructs 10 revisions => 200 total reconstructions.
    # Keep the exact property budget while leaving enough headroom for SQLite close,
    # materialization verification, and TestClient teardown inside the hard 10-second
    # per-item budget even on slower developer machines.
    for i in range(100):
        global_i=(seed-7730)*100+i
        with storage_mutations.tx(operation='v773-property') as con:
            pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
            mode = rng.randrange(11)
            t = storage_values.now()
            if mode == 0:
                con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (f'property-title-{global_i}-{rng.randrange(9999)}', t, pid))
            elif mode == 1:
                row = con.execute('SELECT id FROM chapters WHERE project_id=? ORDER BY id LIMIT 1', (pid,)).fetchone()
                con.execute('UPDATE chapters SET description=?,updated_at=? WHERE id=?', (f'chapter-{global_i}-{rng.randrange(9999)}', t, int(row[0])))
            elif mode == 2:
                row = con.execute('SELECT id,content_html,text_content,image_path FROM manuscript_blocks WHERE project_id=? ORDER BY id LIMIT 1', (pid,)).fetchone()
                text = f'words {global_i} {rng.randrange(9999)}'; html = f'<p>{text}</p>'
                con.execute('UPDATE manuscript_blocks SET text_content=?,content_html=?,content_hash=?,updated_at=? WHERE id=?', (text, html, storage_values.block_content_hash(html, text, str(row[3] or '')), t, str(row[0])))
            elif mode == 3:
                con.execute('UPDATE tasks SET description=?,updated_at=? WHERE id=?', (f'task-{global_i}-{rng.randrange(9999)}', t, task['id']))
            elif mode == 4:
                con.execute('UPDATE scenes SET description=?,updated_at=? WHERE id=?', (f'scene-{global_i}-{rng.randrange(9999)}', t, scene['id']))
            elif mode == 5:
                con.execute('UPDATE markup_labels SET description=?,updated_at=? WHERE id=?', (f'marker-{global_i}-{rng.randrange(9999)}', t, label['id']))
            elif mode == 6:
                con.execute('UPDATE icon_markers SET description=?,updated_at=? WHERE id=?', (f'icon-{global_i}-{rng.randrange(9999)}', t, icon['id']))
            elif mode == 7:
                con.execute('UPDATE canvases SET description=?,updated_at=? WHERE id=?', (f'canvas-{global_i}-{rng.randrange(9999)}', t, canvas['id']))
            elif mode == 8:
                asset = '/api/assets/property-a.bin' if rng.randrange(2) else '/api/assets/property-b.bin'
                con.execute('UPDATE canvas_nodes SET body=?,image_path=?,updated_at=? WHERE id=?', (f'node-{global_i}-{rng.randrange(9999)}', asset, t, node['id']))
            elif mode == 9:
                con.execute('UPDATE story_maps SET description=?,updated_at=? WHERE id=?', (f'map-{global_i}-{rng.randrange(9999)}', t, story_map['id']))
            else:
                con.execute('UPDATE map_lines SET description=?,updated_at=? WHERE id=?', (f'line-{global_i}-{rng.randrange(9999)}', t, line['id']))
        if i % 10 == 9:
            oracle = _logical_state(storage_context.DB_PATH, storage_context.ASSET_DIR)
            row = store.capture(label=f'Property boundary {global_i}', creation_reason='property')
            expected[row['revision_id']] = oracle

    ids = list(expected)
    assert len(ids) == 10
    for index, rid in enumerate(rng.sample(ids, 10)):
        target = tmp_path / f'reconstruct-{seed}-{index}'
        result = store.materialize_revision(rid, target)
        assert _logical_state(result['database'], target / 'assets') == expected[rid]

def test_v773_missing_parent_revision_is_detected_without_touching_current(fresh_project):
    store = _store(interval=5)
    parent = store.capture(label='Parent', creation_reason='fault-fixture')
    _advance('child-current')
    child = store.capture(label='Child', creation_reason='fault-fixture')
    current = _read_title(storage_context.DB_PATH)
    parent_path = next(store.revisions_dir.glob(f'*{parent["revision_id"]}*.revision.json'))
    parent_path.unlink()
    with pytest.raises(RevisionStoreCorruption):
        store.verify_revision(child['revision_id'])
    assert _read_title(storage_context.DB_PATH) == current


def test_v773_duplicate_revision_publication_fails_closed_instead_of_guessing_authority(fresh_project):
    store = _store()
    row = store.capture(label='One authority', creation_reason='fault-fixture')
    current = _read_title(storage_context.DB_PATH)
    original = next(store.revisions_dir.glob(f'*{row["revision_id"]}*.revision.json'))
    duplicate = json.loads(original.read_text(encoding='utf-8'))
    duplicate_id = 'f' * 32 if row['revision_id'] != 'f' * 32 else 'e' * 32
    duplicate['revision_id'] = duplicate_id
    duplicate_path = store.revisions_dir / f'g{row["generation_end"]:020d}-{duplicate_id}.revision.json'
    duplicate_path.write_text(json.dumps(duplicate, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_DUPLICATE_GENERATION_PUBLICATION'):
        store.list_revisions()
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_DUPLICATE_GENERATION_PUBLICATION'):
        store.capture(label='Must not guess', creation_reason='fault')
    assert _read_title(storage_context.DB_PATH) == current


def test_v773_truncated_latest_revision_blocks_new_publication_fail_closed(fresh_project):
    store = _store()
    row = store.capture(label='Published', creation_reason='fault-fixture')
    _advance('current after damaged history')
    current = _read_title(storage_context.DB_PATH)
    revision_path = next(store.revisions_dir.glob(f'*{row["revision_id"]}*.revision.json'))
    revision_path.write_text('{"revision_format":', encoding='utf-8')
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_PUBLICATION_HEAD_UNCERTAIN'):
        store.capture(label='Should be blocked', creation_reason='fault')
    assert _read_title(storage_context.DB_PATH) == current


def test_v773_filesystem_listing_and_mtime_order_never_define_head(fresh_project):
    import os
    store = _store(interval=4)
    first = store.capture(label='Older', creation_reason='ordering')
    _advance('newer-generation')
    second = store.capture(label='Newer', creation_reason='ordering')
    older_path = next(store.revisions_dir.glob(f'*{first["revision_id"]}*.revision.json'))
    newer_path = next(store.revisions_dir.glob(f'*{second["revision_id"]}*.revision.json'))
    # Make the older generation look newer to any wall-clock / directory-order implementation.
    os.utime(newer_path, (1, 1))
    os.utime(older_path, (2_000_000_000, 2_000_000_000))
    listed = store.list_revisions()
    assert listed[0]['revision_id'] == second['revision_id']
    assert listed[0]['generation_end'] > listed[1]['generation_end']


def test_v773_all_major_canonical_families_and_assets_roundtrip_exactly(fresh_project, client, tmp_path):
    def ok(response):
        assert response.status_code < 400, response.text
        return response.json()

    boot = fresh_project['bootstrap']
    tab = next(t for t in boot['tabs'] if t.get('is_manuscript'))
    chapter = ok(client.post('/api/chapters', json={'title': 'History chapter', 'tab_id': tab['id']}))
    scene = ok(client.post('/api/scenes', json={'title': 'History scene', 'tab_id': tab['id']}))
    label = ok(client.post('/api/labels', json={'name': 'History marker', 'color': '#4a6fa5', 'description': 'marker metadata'}))
    icon = ok(client.post('/api/icon-markers', json={'name': 'History icon', 'description': 'icon metadata', 'icon': '★'}))
    task = ok(client.post('/api/tasks', json={'title': 'History task', 'description': 'task metadata', 'status': 'open', 'marker_id': label['id']}))
    canvas = ok(client.post('/api/canvases', json={'name': 'History canvas', 'description': 'canvas metadata'}))
    asset_name = 'v773-history-asset.bin'
    asset_bytes = b'Breadmaker 7.7.3 immutable revision asset\x00\x01\x02'
    storage_assets.durable_write_project_asset(storage_context.ASSET_DIR / asset_name, asset_bytes)
    node = ok(client.post(f'/api/canvases/{canvas["id"]}/nodes', json={
        'shape': 'rect', 'title': 'History asset card', 'body': 'asset-bearing canvas node',
        'image_path': f'/api/assets/{asset_name}', 'icon_marker_id': icon['id'],
    }))
    story_map = ok(client.post('/api/maps', json={'name': 'History map', 'description': 'map metadata'}))
    line = ok(client.post(f'/api/maps/{story_map["id"]}/lines', json={'name': 'History line', 'description': 'line metadata'}))
    cut = ok(client.post('/api/cuts', json={'title': 'History cut', 'content_html': '<p>Removed prose</p>', 'source_chapter_id': chapter['id'], 'source_scene_id': scene['id'], 'note': 'cut metadata'}))

    store = _store(interval=5)
    historical = store.capture(label='Every canonical family', creation_reason='coverage')
    historical_generation = historical['generation_end']

    # Move Current forward after capture; materialization must still be the old exact state.
    _advance('current diverged after history capture')
    current = _read_title(storage_context.DB_PATH)
    target = tmp_path / 'all-families-history'
    result = store.materialize_revision(historical['revision_id'], target)
    assert result['revision']['generation_end'] == historical_generation
    assert _read_title(storage_context.DB_PATH) == current
    assert (target / 'assets' / asset_name).read_bytes() == asset_bytes

    con = sqlite3.connect(result['database'])
    con.row_factory = sqlite3.Row
    try:
        checks = {
            'chapters': ('id', chapter['id'], 'title', 'History chapter'),
            'scenes': ('id', scene['id'], 'title', 'History scene'),
            'markup_labels': ('id', label['id'], 'name', 'History marker'),
            'icon_markers': ('id', icon['id'], 'name', 'History icon'),
            'tasks': ('id', task['id'], 'title', 'History task'),
            'canvases': ('id', canvas['id'], 'name', 'History canvas'),
            'canvas_nodes': ('id', node['id'], 'image_path', f'/api/assets/{asset_name}'),
            'story_maps': ('id', story_map['id'], 'name', 'History map'),
            'map_lines': ('id', line['id'], 'name', 'History line'),
            'cuts': ('id', cut['id'], 'title', 'History cut'),
        }
        for table, (id_col, entity_id, value_col, expected) in checks.items():
            found = con.execute(f'SELECT {value_col} FROM {table} WHERE {id_col}=?', (entity_id,)).fetchone()
            assert found is not None and found[0] == expected, table
    finally:
        con.close()


def test_v773_recovery_package_never_contains_revision_store_bytes(fresh_project):
    import zipfile
    store = _store()
    row = store.capture(label='Local history only', creation_reason='isolation')
    assert row['revision_id']
    backup = storage_recovery.backup_project('v773-history-isolation')
    assert backup is not None
    with zipfile.ZipFile(backup) as zf:
        names = set(zf.namelist())
    assert not any(name.startswith('revision_history/') for name in names)
    assert 'project.sqlite3' in names


def test_v773_autocommit_boundary_publishes_current_revision_store(fresh_project, client):
    _advance('autocommit-boundary')
    response = client.post('/api/autocommit', params={'label': 'Quiet boundary', 'source': 'quiet'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['id'] is not None
    assert payload['local_revision_id'] == payload['id']
    store = _store()
    verified = store.verify_revision(payload['local_revision_id'])
    assert 'legacy_history_revision_id' not in verified['revision']['summary']
    assert verified['revision']['summary']['label'] == 'Quiet boundary'
