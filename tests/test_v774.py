from __future__ import annotations
from source_contracts import frontend_source_from_app_path

from storage import assets as storage_assets
from storage import context as storage_context
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import values as storage_values

import json
import re
from pathlib import Path

import pytest

import app as app_module
from api.durability import backups as backup_api
import workspace
from revision_store.restore import restore_local_revision


def _project_id() -> int:
    with storage_mutations.tx(operation='v774-project-read', track_generation=False) as con:
        return int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])


def _block_row():
    pid = _project_id()
    with storage_mutations.tx(operation='v774-block-read', track_generation=False) as con:
        return con.execute(
            'SELECT id,text_content,content_html,image_path FROM manuscript_blocks '
            'WHERE project_id=? ORDER BY tab_id,chapter_id,position,id LIMIT 1',
            (pid,),
        ).fetchone()


def _write_text(text: str) -> int:
    with storage_mutations.tx(operation='v774-write-text') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        row = con.execute(
            'SELECT id,image_path FROM manuscript_blocks WHERE project_id=? '
            'ORDER BY tab_id,chapter_id,position,id LIMIT 1',
            (pid,),
        ).fetchone()
        html = f'<p>{text}</p>'
        con.execute(
            'UPDATE manuscript_blocks SET block_type=\'text\',text_content=?,content_html=?,image_path=\'\','
            'content_hash=?,updated_at=? WHERE id=?',
            (text, html, storage_values.block_content_hash(html, text, ''), storage_values.now(), str(row[0])),
        )
    return storage_mutations.current_project_generation()


def _read_text() -> str:
    with storage_mutations.tx(operation='v774-read-text', track_generation=False) as con:
        return str(con.execute(
            'SELECT text_content FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1'
        ).fetchone()[0] or '')


def _set_project_title(title: str) -> int:
    with storage_mutations.tx(operation='v774-title') as con:
        pid = int(con.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0])
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?', (title, storage_values.now(), pid))
    return storage_mutations.current_project_generation()


def _project_title() -> str:
    with storage_mutations.tx(operation='v774-title-read', track_generation=False) as con:
        return str(con.execute('SELECT title FROM projects ORDER BY id LIMIT 1').fetchone()[0])


def _capture(label: str, reason: str = 'test') -> dict:
    return storage_history.capture_local_revision(
        label=label,
        creation_reason=reason,
        summary={'word_count': len(_read_text().split())},
    )


def _history(client):
    response = client.get('/api/history/v2')
    assert response.status_code == 200, response.text
    return response.json()


def test_v774_release_contract_and_locale_parity():
    root = Path(__file__).resolve().parents[1]
    version = tuple(int(x) for x in (root / 'VERSION').read_text(encoding='utf-8').strip().split('.'))
    assert version >= (7, 7, 4)
    readme = (root / 'README.md').read_text(encoding='utf-8')
    architecture = (root / 'ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '7.7.4 History Restore + History UI' in readme
    assert 'next' in readme and 'generation' in readme
    assert 'compare-and-set' in architecture
    required = {
        'history.compare_current', 'history.save_checkpoint', 'history.restore',
        'history.restore_confirm', 'history.restore_stale', 'history.restore_failed',
        'history.today', 'history.yesterday', 'history.words_added', 'history.words_removed',
        'history.read_only_note', 'history.safety_state',
    }
    keysets = []
    for path in sorted((root / 'locales').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        strings = data['strings']
        assert required <= set(strings), path.name
        assert '{name}' in strings['history.restore_confirm']
        keysets.append(set(strings))
    assert keysets and all(keys == keysets[0] for keys in keysets[1:])


def test_v774_history_v2_lists_immutable_revision_ids_and_ui_has_no_delete_export(fresh_project, client):
    _write_text('History immutable state')
    revision = _capture('Immutable History')
    data = _history(client)
    assert data['revisions']
    row = next(x for x in data['revisions'] if x['id'] == revision['revision_id'])
    assert re.fullmatch(r'[0-9a-f]{32}', row['id'])
    assert row['generation_end'] == revision['generation_end']
    js = frontend_source_from_app_path(Path(__file__).resolve().parents[1] / 'static' / 'app.js')
    menu_start = js.index('function openHistoryRowMenu')
    menu_end = js.index('function renderHistoryModal', menu_start)
    menu = js[menu_start:menu_end]
    assert 'data-history-menu="compare"' in menu
    assert 'data-history-menu="checkpoint"' in menu
    assert 'data-history-menu="restore"' in menu
    assert 'data-history-menu="delete"' not in menu
    assert 'data-history-menu="export"' not in menu


def test_v774_compare_is_read_only(fresh_project, client):
    _write_text('Historical prose one')
    revision = _capture('Compare me')
    _write_text('Current prose two')
    before = storage_mutations.current_project_generation()
    response = client.get(f"/api/history/v2/revisions/{revision['revision_id']}/compare-current?scope=book")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['current_generation'] == before
    assert any(row['kind'] != 'same' for row in payload['rows'])
    assert storage_mutations.current_project_generation() == before
    assert _read_text() == 'Current prose two'


def test_v774_restore_is_forward_preserves_newer_history_identity_checkpoints_and_cuts(fresh_project, client):
    chapter_id = int(fresh_project['bootstrap']['chapters'][0]['id'])
    _write_text('State A historical manuscript')
    selected = _capture('State A')

    _write_text('State B newer manuscript')
    newer = _capture('State B')
    _set_project_title('Current Project Identity Must Survive')
    checkpoint = client.post('/api/checkpoints', json={'scope': 'chapter', 'chapter_id': chapter_id, 'name': 'Independent checkpoint'})
    assert checkpoint.status_code == 200, checkpoint.text
    checkpoint_id = int(checkpoint.json()['id'])
    cut = client.post('/api/cuts', json={'title': 'Independent cut', 'content_html': '<p>Keep this cut</p>', 'source_chapter_id': chapter_id})
    assert cut.status_code == 200, cut.text
    cut_id = int(cut.json()['id'])

    before_generation = storage_mutations.current_project_generation()
    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before_generation},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['previous_generation'] == before_generation
    assert result['current_generation'] == before_generation + 1
    assert result['idempotent'] is False
    assert _read_text() == 'State A historical manuscript'
    assert _project_title() == 'Current Project Identity Must Survive'
    assert storage_mutations.current_project_generation() == before_generation + 1

    checkpoints = client.get('/api/checkpoints').json()['checkpoints']
    cuts = client.get('/api/cuts').json()
    assert any(int(x['id']) == checkpoint_id for x in checkpoints)
    assert any(int(x['id']) == cut_id for x in cuts)

    history_ids = {x['revision_id'] for x in storage_history.list_local_revisions()}
    assert selected['revision_id'] in history_ids
    assert newer['revision_id'] in history_ids
    assert result['safety_revision_id'] in history_ids
    assert result['restored_history_revision_id'] in history_ids
    safety = storage_history.verify_local_revision(result['safety_revision_id'])['revision']
    assert int(safety['generation_end']) == before_generation

    backup = Path(result['recovery_backup'])
    assert backup.is_file()
    verified = workspace.workspace.verify_package(backup)
    assert int(verified['project_generation']) == before_generation


def test_v774_restore_safety_revision_can_restore_the_pre_restore_current(fresh_project, client):
    _write_text('Old state')
    old = _capture('Old state')
    _write_text('Immediate current not yet in History')
    pre_restore_generation = storage_mutations.current_project_generation()

    first = client.post(
        f"/api/history/v2/revisions/{old['revision_id']}/restore",
        json={'expected_generation': pre_restore_generation},
    )
    assert first.status_code == 200, first.text
    first_result = first.json()
    assert _read_text() == 'Old state'
    safety_id = first_result['safety_revision_id']
    assert safety_id

    undo_expected = storage_mutations.current_project_generation()
    second = client.post(
        f'/api/history/v2/revisions/{safety_id}/restore',
        json={'expected_generation': undo_expected},
    )
    assert second.status_code == 200, second.text
    assert second.json()['current_generation'] == undo_expected + 1
    assert _read_text() == 'Immediate current not yet in History'


def test_v774_retry_after_committed_restore_is_idempotent_but_later_edit_makes_retry_stale(fresh_project, client):
    _write_text('Retry source')
    source = _capture('Retry source')
    _write_text('Retry current')
    expected = storage_mutations.current_project_generation()
    url = f"/api/history/v2/revisions/{source['revision_id']}/restore"

    first = client.post(url, json={'expected_generation': expected})
    assert first.status_code == 200, first.text
    committed_generation = first.json()['current_generation']
    retry = client.post(url, json={'expected_generation': expected})
    assert retry.status_code == 200, retry.text
    assert retry.json()['idempotent'] is True
    assert retry.json()['current_generation'] == committed_generation
    assert storage_mutations.current_project_generation() == committed_generation

    _write_text('User edited after successful restore')
    after_edit = storage_mutations.current_project_generation()
    stale = client.post(url, json={'expected_generation': expected})
    assert stale.status_code == 409, stale.text
    assert stale.json()['detail']['code'] == 'HISTORY_RESTORE_STALE'
    assert storage_mutations.current_project_generation() == after_edit
    assert _read_text() == 'User edited after successful restore'


def test_v774_stale_restore_request_is_rejected_before_mutating_current(fresh_project, client):
    _write_text('Stale selected')
    selected = _capture('Stale selected')
    _write_text('Dialog current')
    stale_expected = storage_mutations.current_project_generation()
    _write_text('Newer edit while dialog open')
    newest = storage_mutations.current_project_generation()

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': stale_expected},
    )
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'HISTORY_RESTORE_STALE'
    assert storage_mutations.current_project_generation() == newest
    assert _read_text() == 'Newer edit while dialog open'


def test_v774_corrupt_selected_revision_cannot_change_current(fresh_project, client):
    _write_text('Corrupt selected')
    selected = _capture('Corrupt selected')
    _write_text('Known good current')
    before = storage_mutations.current_project_generation()
    store = storage_history.local_revision_store()
    path = next(store.revisions_dir.glob(f"*{selected['revision_id']}*.revision.json"))
    path.write_bytes(b'{"truncated":')

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'HISTORY_RESTORE_CORRUPT'
    assert storage_mutations.current_project_generation() == before
    assert _read_text() == 'Known good current'


def test_v774_interrupted_activation_rolls_back_current(fresh_project):
    _write_text('Interrupted selected')
    selected = _capture('Interrupted selected')
    _write_text('Current must survive rollback')
    before = storage_mutations.current_project_generation()
    stages = []

    def crash(stage: str):
        stages.append(stage)
        if stage == 'BEFORE_ACTIVATION_COMMIT':
            raise RuntimeError('simulated process death inside activation')

    with pytest.raises(RuntimeError, match='simulated process death'):
        restore_local_revision(
            selected['revision_id'],
            expected_generation=before,
            verified_backup=lambda: backup_api.verified_full_backup('v774_interrupted_restore'),
            label='Interrupted selected',
            stage_hook=crash,
        )
    assert 'CURRENT_WRITER_STATE_CLEARED' in stages
    assert storage_mutations.current_project_generation() == before
    assert _read_text() == 'Current must survive rollback'


def test_v774_missing_historical_asset_is_rehydrated_before_reference_becomes_current(fresh_project, client):
    asset = storage_context.ASSET_DIR / 'v774-history-asset.bin'
    original = b'v774 historical immutable asset\x00\x01'
    storage_assets.durable_write_project_asset(asset, original)
    with storage_mutations.tx(operation='v774-image-state') as con:
        row = con.execute('SELECT id FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()
        ref = '/api/assets/' + asset.name
        con.execute(
            "UPDATE manuscript_blocks SET block_type='image',content_html='',text_content='',image_path=?,content_hash=?,updated_at=? WHERE id=?",
            (ref, storage_values.block_content_hash('', '', ref), storage_values.now(), str(row[0])),
        )
    selected = _capture('Historical asset state')
    _write_text('Current does not reference old asset')
    asset.unlink()
    assert not asset.exists()
    before = storage_mutations.current_project_generation()

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 200, response.text
    assert asset.read_bytes() == original
    row = _block_row()
    assert str(row['image_path']) == '/api/assets/' + asset.name
    assert str(row['text_content'] or '') == ''


def test_v774_asset_identity_conflict_fails_before_database_activation(fresh_project, client):
    asset = storage_context.ASSET_DIR / 'v774-conflict.bin'
    original = b'original immutable bytes'
    storage_assets.durable_write_project_asset(asset, original)
    with storage_mutations.tx(operation='v774-conflict-image') as con:
        row = con.execute('SELECT id FROM manuscript_blocks ORDER BY tab_id,chapter_id,position,id LIMIT 1').fetchone()
        ref = '/api/assets/' + asset.name
        con.execute(
            "UPDATE manuscript_blocks SET block_type='image',content_html='',text_content='',image_path=?,content_hash=?,updated_at=? WHERE id=?",
            (ref, storage_values.block_content_hash('', '', ref), storage_values.now(), str(row[0])),
        )
    selected = _capture('Asset identity state')
    _write_text('Safe current state')
    # Simulate external corruption of an immutable path; restore must never overwrite it.
    asset.write_bytes(b'different bytes at same immutable path')
    before = storage_mutations.current_project_generation()

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'HISTORY_RESTORE_CORRUPT'
    assert storage_mutations.current_project_generation() == before
    assert _read_text() == 'Safe current state'
    assert asset.read_bytes() == b'different bytes at same immutable path'


def test_v774_save_verified_history_as_checkpoint(fresh_project, client):
    _write_text('Checkpoint from History')
    selected = _capture('Checkpoint source')
    before = client.get('/api/checkpoints').json()['checkpoints']
    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/checkpoint",
        json={'scope': 'book', 'name': 'Pinned historical book', 'description': 'Saved from verified History.'},
    )
    assert response.status_code == 200, response.text
    checkpoint = response.json()
    after = client.get('/api/checkpoints').json()['checkpoints']
    assert len(after) == len(before) + 1
    assert any(int(x['id']) == int(checkpoint['id']) and x['name'] == 'Pinned historical book' for x in after)


def test_v774_restore_latest_still_creates_one_new_forward_generation(fresh_project, client):
    _write_text('Latest retained state')
    latest = _capture('Latest retained state')
    before = storage_mutations.current_project_generation()
    assert int(latest['generation_end']) == before
    response = client.post(
        f"/api/history/v2/revisions/{latest['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['current_generation'] == before + 1
    assert storage_mutations.current_project_generation() == before + 1
    assert _read_text() == 'Latest retained state'


def test_v774_pre_restore_backup_failure_aborts_before_safety_or_current_activation(fresh_project):
    _write_text('Backup failure selected')
    selected = _capture('Backup failure selected')
    _write_text('Backup failure current')
    before_generation = storage_mutations.current_project_generation()
    before_ids = [x['revision_id'] for x in storage_history.list_local_revisions()]

    def fail_backup():
        raise RuntimeError('simulated verified backup failure')

    with pytest.raises(RuntimeError, match='simulated verified backup failure'):
        restore_local_revision(
            selected['revision_id'],
            expected_generation=before_generation,
            verified_backup=fail_backup,
            label='Backup failure selected',
        )
    assert storage_mutations.current_project_generation() == before_generation
    assert _read_text() == 'Backup failure current'
    assert [x['revision_id'] for x in storage_history.list_local_revisions()] == before_ids


def test_v774_post_commit_later_edit_cannot_be_mislabeled_as_restore(fresh_project):
    _write_text('Landmark selected')
    selected = _capture('Landmark selected')
    _write_text('Landmark current')
    before = storage_mutations.current_project_generation()
    injected = {'done': False}

    def hook(stage: str):
        if stage == 'ACTIVATION_COMMITTED' and not injected['done']:
            injected['done'] = True
            _write_text('User edit immediately after restore commit')

    result = restore_local_revision(
        selected['revision_id'],
        expected_generation=before,
        verified_backup=lambda: backup_api.verified_full_backup('v774_landmark_race'),
        label='Landmark selected',
        stage_hook=hook,
    )
    assert result.current_generation == before + 1
    assert storage_mutations.current_project_generation() == before + 2
    assert _read_text() == 'User edit immediately after restore commit'
    assert result.restored_history_revision_id is None
    assert result.history_landmark_error
    latest_generation = storage_mutations.current_project_generation()
    assert not any(
        int(row['generation_end']) == latest_generation and row.get('creation_reason') == 'restore'
        for row in storage_history.list_local_revisions()
    )


def test_v774_restore_replaces_structural_writer_tables_but_not_project_metadata(fresh_project, client):
    task = client.post('/api/tasks', json={'title': 'Task at selected state', 'description': 'A', 'status': 'open'})
    assert task.status_code == 200, task.text
    task_id = int(task.json()['id'])
    canvas = client.post('/api/canvases', json={'name': 'Canvas at selected state'})
    assert canvas.status_code == 200, canvas.text
    canvas_id = int(canvas.json()['id'])
    story_map = client.post('/api/maps', json={'name': 'Map at selected state'})
    assert story_map.status_code == 200, story_map.text
    map_id = int(story_map.json()['id'])
    _write_text('Structural selected text')
    selected = _capture('Structural selected')

    with storage_mutations.tx(operation='v774-structural-newer') as con:
        con.execute('UPDATE tasks SET title=?,description=?,updated_at=? WHERE id=?', ('Task newer', 'B', storage_values.now(), task_id))
        con.execute('UPDATE canvases SET name=?,description=?,updated_at=? WHERE id=?', ('Canvas newer', 'newer', storage_values.now(), canvas_id))
        con.execute('UPDATE story_maps SET name=?,description=?,updated_at=? WHERE id=?', ('Map newer', 'newer', storage_values.now(), map_id))
    _write_text('Structural newer text')
    _set_project_title('Project metadata remains current')
    before = storage_mutations.current_project_generation()

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 200, response.text
    with storage_mutations.tx(operation='v774-structural-read', track_generation=False) as con:
        task_row = con.execute('SELECT title,description FROM tasks WHERE id=?', (task_id,)).fetchone()
        canvas_row = con.execute('SELECT name,description FROM canvases WHERE id=?', (canvas_id,)).fetchone()
        map_row = con.execute('SELECT name,description FROM story_maps WHERE id=?', (map_id,)).fetchone()
    assert tuple(task_row) == ('Task at selected state', 'A')
    assert tuple(canvas_row) == ('Canvas at selected state', '')
    assert tuple(map_row) == ('Map at selected state', '')
    assert _read_text() == 'Structural selected text'
    assert _project_title() == 'Project metadata remains current'


def test_v774_restore_does_not_rewind_newer_history_or_ui_state(fresh_project, client):
    _write_text('Independent roots selected')
    selected = _capture('Independent roots selected')
    _write_text('Independent roots current')
    newer = _capture('Independent newer History landmark')
    ui = client.put('/api/ui-state/v774-independent', json={'value': {'panel': 'current', 'zoom': 1.25}})
    assert ui.status_code == 200, ui.text
    before = storage_mutations.current_project_generation()

    response = client.post(
        f"/api/history/v2/revisions/{selected['revision_id']}/restore",
        json={'expected_generation': before},
    )
    assert response.status_code == 200, response.text
    ids={row['revision_id'] for row in client.get('/api/history/v2').json()['revisions']}
    assert newer['revision_id'] in ids
    assert client.get('/api/ui-state/v774-independent').json()['value'] == {'panel': 'current', 'zoom': 1.25}


def test_v774_corrupt_newest_history_is_ignored_while_previous_valid_revision_remains_usable(fresh_project, client):
    _write_text('Previous valid History')
    previous = _capture('Previous valid History')
    _write_text('Newest History to corrupt')
    newest = _capture('Newest History to corrupt')
    _write_text('Current after both')
    before = storage_mutations.current_project_generation()
    store = storage_history.local_revision_store()
    newest_path = next(store.revisions_dir.glob(f"*{newest['revision_id']}*.revision.json"))
    newest_path.write_bytes(b'not-json')

    listed = _history(client)['revisions']
    ids = {row['revision_id'] for row in listed}
    assert previous['revision_id'] in ids
    assert newest['revision_id'] not in ids
    comparison = client.get(f"/api/history/v2/revisions/{previous['revision_id']}/compare-current?scope=book")
    assert comparison.status_code == 200, comparison.text
    assert any(row['kind'] != 'same' for row in comparison.json()['rows'])
    assert storage_mutations.current_project_generation() == before
    assert _read_text() == 'Current after both'
