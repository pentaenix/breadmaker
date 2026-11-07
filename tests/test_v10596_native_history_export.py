"""Native frozen source evidence survives hosted export without hash rewriting."""
import base64
from copy import deepcopy
import hashlib
import io
import json
import sqlite3
import zipfile
import zlib

import pytest

from hosted.domain_store import _ID_FIELDS
from hosted.local_portability import _mapping
from hosted.native_history import prepare_native_history
from hosted.native_history_export import prepare_native_export
from tests.test_v10596_native_history_import import _fixture, PID


def _rich_native(tmp_path, aliases=False):
    from tests.test_v10596_native_package_import import database, insert, package, STAMP
    from tests.test_v10353 import PNG
    from storage.values import block_content_hash, review_snapshot_content_hash
    con, path = database(tmp_path)
    rid, sid = 'review_' + '1' * 32, 'review_snapshot_' + '2' * 32
    qid, question = 'questionnaire_' + '3' * 32, 'questionnaire_question_' + '4' * 32
    frozen_question = 'review_snapshot_question_' + '5' * 32
    participant, anchor = 'review_participant_' + '6' * 32, 'review_anchor_' + '7' * 32
    digest = hashlib.sha256(PNG).hexdigest()
    image = '/api/assets/original.png' if aliases else '/api/assets/img_' + digest[:24] + '.png'
    files = {}
    if aliases:
        insert(con, 'manuscript_blocks', id='second-image', project_id=1, tab_id=1, chapter_id=1,
               position=1, block_type='image', image_path='/api/assets/second.png')
        files['assets/second.png'] = PNG
    insert(con, 'reviews', id=rid, project_id=1, name='Frozen Review', status='open',
           permission_read=1, permission_comment=1, permission_suggest=1, permission_feedback=1)
    insert(con, 'review_participants', id=participant, review_id=rid, display_name='Synthetic Reader')
    insert(con, 'review_marker_labels', id=1, project_id=1, name='Frozen marker', color='#123456')
    insert(con, 'review_markers', id=1, review_id=rid, marker_id=1)
    insert(con, 'questionnaires', id=qid, project_id=1, name='Original questions')
    insert(con, 'questionnaire_questions', id=question, questionnaire_id=qid, prompt='Original prompt?')
    insert(con, 'review_questionnaires', id='review_questionnaire_' + '8' * 32, review_id=rid, questionnaire_id=qid)
    insert(con, 'review_snapshots', id=sid, review_id=rid, round_number=1, status='open', source_generation=1,
           scope_type='manuscript', permission_read=1, permission_comment=1, permission_suggest=1, permission_feedback=1, content_hash='pending')
    insert(con, 'review_snapshot_chapters', id=1, snapshot_id=sid, source_chapter_id=1, source_tab_id=1,
           ordinal=0, position=0, title='Frozen title')
    for index, block in enumerate([
        {'id': 'original-block', 'block_type': 'paragraph', 'content_html': 'Frozen prose', 'text_content': 'Frozen prose', 'image_path': ''},
        {'id': 'historical-image', 'block_type': 'image', 'content_html': '', 'text_content': '', 'image_path': image},
    ]):
        insert(con, 'review_snapshot_blocks', id=index+1, snapshot_id=sid, source_block_id=block['id'],
               source_tab_id=1, source_chapter_id=1, ordinal=index, position=index,
               semantic_style='body', block_type=block['block_type'], content_html=block['content_html'],
               text_content=block['text_content'], image_path=block['image_path'],
               content_hash=block_content_hash(block['content_html'], block['text_content'], block['image_path']))
    insert(con, 'review_snapshot_markers', id=1, snapshot_id=sid, source_marker_id=1,
           name='Frozen marker', color='#123456')
    insert(con, 'review_snapshot_questions', id=frozen_question, snapshot_id=sid, source_questionnaire_id=qid,
           source_question_id=question, questionnaire_name='Original questions', prompt='Original prompt?')
    insert(con, 'review_anchors', id=anchor, review_id=rid, snapshot_id=sid,
           start_block_id='original-block', end_block_id='original-block', start_offset=0, end_offset=6, selected_text='Frozen')
    insert(con, 'review_comments', id='review_comment_' + '9' * 32, review_id=rid, participant_id=participant,
           anchor_id=anchor, body='Original comment')
    insert(con, 'review_question_answers', id='review_answer_' + 'a' * 32, review_id=rid, snapshot_id=sid,
           question_id=frozen_question, participant_id=participant, answer_text='Original answer')
    source = {'version': 1, 'scope': 'book', 'captured_at': STAMP,
              'tab': {'id': 1, 'title': 'Manuscript', 'is_manuscript': 1, 'created_at': STAMP},
              'chapters': [{'id': 1, 'tab_id': 1, 'title': 'Frozen title', 'position': 0, 'created_at': STAMP}],
              'blocks': [{'id': row['source_block_id'], 'chapter_id': 1, 'position': row['position'],
                          'block_type': row['block_type'], 'content_html': row['content_html'], 'text_content': row['text_content'],
                          'image_path': row['image_path']} for row in con.execute('SELECT * FROM review_snapshot_blocks ORDER BY ordinal')],
              'scenes': [], 'annotations': [], 'comment_threads': [], 'comment_messages': [],
              'include_markup': True, 'include_notes': True, 'include_scenes': True}
    raw = json.dumps(source, sort_keys=True, separators=(',', ':')).encode()
    insert(con, 'checkpoints', id=4, project_id=1, scope='book', name='Original checkpoint',
           source_tab_id=1, source_tab_title='Manuscript', source_tab_created_at=STAMP,
           snapshot_blob=zlib.compress(raw), snapshot_hash=hashlib.sha256(raw).hexdigest())
    insert(con, 'checkpoint_assets', hash=digest, payload=PNG, byte_size=len(PNG))
    insert(con, 'checkpoint_asset_refs', checkpoint_id=4, asset_path=image, asset_hash=digest)
    insert(con, 'review_snapshot_checkpoints', id=1, snapshot_id=sid, checkpoint_id=4, ordinal=0)
    insert(con, 'review_events', id='review_event_' + 'b' * 32, review_id=rid, origin='local',
           event_type='REVIEW_ROUND_STARTED', entity_type='snapshot', entity_id=sid,
           payload_json=json.dumps({'snapshot_id': sid, 'checkpoint_ids': [4], 'round_number': 1}))
    row = dict(con.execute('SELECT * FROM review_snapshots').fetchone())
    groups = [[dict(value) for value in con.execute('SELECT * FROM review_snapshot_' + group)]
              for group in ('chapters', 'scenes', 'blocks', 'questions', 'markers')]
    content_hash = review_snapshot_content_hash(rid, 1, 'manuscript', row, *groups)
    con.execute('UPDATE review_snapshots SET content_hash=?', (content_hash,))
    # The image exists solely in checkpoint_assets, not as a live archive asset.
    return con, package(con, path, files=files), sid, content_hash, raw, PNG


def _source():
    con, _, tables = _fixture(image=True)
    mapping = _mapping()
    logical_for_table = {table: logical for logical, table in mapping.items()}
    ids = {(logical_for_table[table], str(row['id'])):
           _ID_FIELDS[logical_for_table[table]][1] + 'legacy_' + str(row['id'])
           for table, rows in tables.items() if logical_for_table.get(table) in _ID_FIELDS
           for row in rows if 'id' in row}
    plan = prepare_native_history(con, PID, ids)
    fields = {'review_id': 'review', 'snapshot_id': 'review_snapshot', 'question_id': 'review_snapshot_question',
              'source_tab_id': 'document_tab', 'source_chapter_id': 'chapter', 'current_source_chapter_id': 'chapter'}
    records = {}
    for table, source_rows in tables.items():
        logical = logical_for_table.get(table)
        if logical not in _ID_FIELDS:
            continue
        for source in source_rows:
            if 'id' not in source:
                continue
            old = str(source['id']); eid = ids[logical, old]
            row = deepcopy(source)
            row['id'] = eid
            for key in source:
                if (logical, old, key) in plan.references:
                    row[key] = plan.references[logical, old, key]
                elif key in fields and source[key] is not None:
                    row[key] = ids.get((fields[key], str(source[key])), source[key])
            row.update(plan.payloads.get((logical, old), {}))
            row.pop('snapshot_blob', None)
            records.setdefault(logical, []).append({'ids': {'project_id': PID, _ID_FIELDS[logical][0]: eid}, 'data': row})
    assets = {'assets/' + path.removeprefix('/api/assets/'): blob for path, blob in plan.assets}
    return records, mapping, assets, tables


def test_native_source_ids_frozen_rows_and_exact_checkpoint_bytes_are_restored():
    records, mapping, assets, source = _source()
    before = deepcopy(records)
    plan = prepare_native_export(records, mapping, assets)
    sid = 'snapshot_legacy_round_old'
    assert plan.snapshots == {sid}
    assert plan.preferred['review_snapshots', sid] == 'round_old'
    assert plan.preferred['chapters', 'chapter_legacy_11'] == 11
    frozen = plan.records['review_snapshot_blocks'][0]['data']
    assert frozen['source_block_id'] == 'deleted_block'
    assert frozen['source_chapter_id'] == 11
    assert frozen['snapshot_id'] == sid
    assert plan.records['review_snapshot'][0]['data']['content_hash'] == source['review_snapshots'][0]['content_hash']
    checkpoint = plan.records['checkpoint'][0]['data']
    assert base64.b64decode(checkpoint['snapshot_blob']['$bytes']) == source['checkpoints'][0]['snapshot_blob']
    assert checkpoint['source_chapter_id'] == 11
    anchor = plan.records['review_anchor'][0]['data']
    assert anchor['start_block_id'] == 'deleted_block'
    assert 'start_block_id' in plan.raw_columns['review_anchors', anchor['id']]
    assert 'snapshot' not in checkpoint
    assert records == before


def test_deleted_historical_integer_sources_are_reserved_against_new_hosted_ids():
    records, mapping, assets, source = _source()
    records.pop('chapter')
    plan = prepare_native_export(records, mapping, assets)
    assert 11 in plan.reserved['chapters']
    assert ('chapters', 'chapter_legacy_11') not in plan.preferred


def test_structure_only_native_history_restores_deleted_sources_and_nested_workspace():
    records = {
        'canvas_version': [{'ids': {'project_id': PID, 'version_id': 'version_legacy_2'}, 'data': {
            'id': 'version_legacy_2', 'canvas_id': 'canvas_legacy_1',
            'workspace_json': json.dumps({'schema': 2, 'nodes': [{'id': 'node_legacy_9', 'chapter_id': 'chapter_legacy_11'}],
                'edges': [{'id': 'edge_legacy_8', 'from_node_id': 'node_legacy_9', 'to_node_id': 'node_legacy_10'}],
                'structural_styles': {'chapter:chapter_legacy_11': {'label_id': 'label_legacy_3'}}})}}],
        'canvas_version_item': [{'ids': {'project_id': PID, 'item_id': 'item_legacy_4'}, 'data': {
            'id': 'item_legacy_4', 'entity_type': 'chapter', 'entity_id': 'chapter_legacy_11'}}],
    }
    plan = prepare_native_export(records, _mapping(), {})
    workspace = json.loads(plan.records['canvas_version'][0]['data']['workspace_json'])
    assert workspace['nodes'][0] == {'id': 9, 'chapter_id': 11}
    assert workspace['edges'][0]['from_node_id'] == 9
    assert workspace['structural_styles'] == {'chapter:11': {'label_id': 3}}
    assert plan.records['canvas_version_item'][0]['data']['entity_id'] == 11
    assert 11 in plan.reserved['chapters'] and 10 in plan.reserved['canvas_nodes']
    assert plan.raw_columns['canvas_version_items', 'item_legacy_4'] == {'entity_id'}


def test_encoded_evidence_is_bounded_before_base64_allocation(monkeypatch):
    from hosted import native_history_export as codec
    monkeypatch.setattr(codec, '_LIMIT', 8)
    monkeypatch.setattr(codec.base64, 'b64decode', lambda *args, **kwargs: pytest.fail('Must reject before decoding'))
    with pytest.raises(ValueError, match='EVIDENCE_TOO_LARGE'):
        codec._decode_evidence('x' * 100)


@pytest.mark.parametrize('change', ['prose', 'question', 'extra_content', 'checkpoint_prose', 'checkpoint_note'])
def test_native_evidence_cannot_silently_replace_a_different_rendered_projection(change):
    records, mapping, assets, _ = _source()
    snapshot = records['review_snapshot'][0]['data']['snapshot']
    checkpoint = records['checkpoint'][0]['data']['snapshot']
    if change == 'prose':
        snapshot['blocks'][0]['text_content'] = 'Different content'
    elif change == 'question':
        snapshot['questions'][0]['prompt'] = 'Different question'
    elif change == 'extra_content':
        snapshot['blocks'][0]['unrepresented_note'] = 'Do not drop this'
    elif change == 'checkpoint_prose':
        checkpoint['blocks'][0]['text_content'] = 'Different saved prose'
    else:
        checkpoint['comment_messages'][0]['body'] = 'Different note'
    with pytest.raises(ValueError, match='PROJECTION_DIVERGED'):
        prepare_native_export(records, mapping, assets)


@pytest.mark.parametrize('location,key,value', [
    ('projection', 'id', 'forged_other_identity'),
    ('projection', 'review_id', 'forged_other_identity'),
    ('row', 'source_generation', 999),
    ('row', 'content_hash', '0' * 64),
    ('row', 'scope_type', 'manuscript'),
    ('row', 'round_number', 2),
    ('row', 'permission_comment', 0),
    ('parents', 'review_id', 'forged_other_identity'),
    ('parents', 'snapshot_id', 'forged_other_identity'),
])
def test_native_review_identity_and_immutable_outer_metadata_must_agree(location, key, value):
    records, mapping, assets, _ = _source()
    item = records['review_snapshot'][0]
    target = {'projection': item['data']['snapshot'], 'row': item['data'], 'parents': item['ids']}[location]
    target[key] = value
    with pytest.raises(ValueError, match='PROJECTION_DIVERGED'):
        prepare_native_export(records, mapping, assets)


def test_a_forged_identity_cannot_match_a_null_frozen_reference():
    records, mapping, assets, _ = _source()
    records['review_snapshot'][0]['data']['snapshot']['blocks'][0]['source_scene_id'] = 'forged'
    with pytest.raises(ValueError, match='PROJECTION_DIVERGED'):
        prepare_native_export(records, mapping, assets)


@pytest.mark.parametrize('key,value', [
    ('id', 'checkpoint_legacy_999'), ('scope', 'book'),
    ('source_tab_id', 'tab_legacy_999'), ('source_chapter_id', 'chapter_legacy_999'),
    ('source_chapter_title', 'Different source title'),
    ('source_chapter_created_at', '2026-01-01'), ('include_markup', False),
])
def test_native_checkpoint_immutable_outer_metadata_must_agree(key, value):
    records, mapping, assets, _ = _source()
    records['checkpoint'][0]['data'][key] = value
    with pytest.raises(ValueError, match='PROJECTION_DIVERGED'):
        prepare_native_export(records, mapping, assets)


@pytest.mark.parametrize('status,closed,expected', [
    ('closed', True, 'closed'), ('active', False, 'open'), ('paused', False, 'paused'),
])
def test_later_review_lifecycle_and_hosted_timestamps_do_not_change_frozen_hashes(status, closed, expected):
    records, mapping, assets, source = _source()
    row = records['review_snapshot'][0]['data']; sid = row['id']
    row.update(created_at='2026-11-07', generation=42, status='paused', paused_at='2026-11-06')
    review = records['review'][0]['data']
    review.update(status=status, active_snapshot_id='' if closed else sid,
                  closed_snapshot_ids=[sid] if closed else [])
    if closed:
        row['closed_at'] = '2026-11-07'
    result = prepare_native_export(records, mapping, assets).records['review_snapshot'][0]['data']
    assert result['status'] == expected
    assert result['paused_at'] == '2026-11-06'
    if closed:
        assert result['closed_at'] == '2026-11-07'
    assert result['created_at'] == source['review_snapshots'][0]['created_at']
    assert result['content_hash'] == source['review_snapshots'][0]['content_hash']


def test_checkpoint_editable_metadata_and_current_lineage_survive_unchanged():
    records, mapping, assets, _ = _source()
    row = records['checkpoint'][0]['data']
    row.update(name='Renamed', description='Edited description', current_source_chapter_id='chapter_legacy_12',
               current_source_chapter_created_at='2026-11-07', created_at='2026-11-07', updated_at='2026-11-08')
    result = prepare_native_export(records, mapping, assets).records['checkpoint'][0]['data']
    assert result['name'] == 'Renamed' and result['description'] == 'Edited description'
    assert result['current_source_chapter_id'] == 12
    assert result['current_source_chapter_created_at'] == '2026-11-07'
    assert result['created_at'] == '2026-11-07' and result['updated_at'] == '2026-11-08'


def test_self_signed_native_checkpoint_evidence_cannot_reintroduce_script_html():
    records, mapping, assets, _ = _source()
    row = records['checkpoint'][0]['data']
    snapshot = row['snapshot']; evidence = snapshot['native_source']
    original = json.loads(zlib.decompress(base64.b64decode(evidence['snapshot_blob_b64'])))
    original['blocks'][0]['content_html'] = '<script>alert(1)</script>'
    snapshot['blocks'][0]['content_html'] = original['blocks'][0]['content_html']
    raw = json.dumps(original).encode()
    evidence.update(snapshot_blob_b64=base64.b64encode(zlib.compress(raw)).decode(), snapshot_hash=hashlib.sha256(raw).hexdigest())
    row['snapshot_hash'] = evidence['snapshot_hash']
    with pytest.raises(ValueError, match='UNSAFE_HTML'):
        prepare_native_export(records, mapping, assets)


@pytest.mark.parametrize('aliases', [False, True], ids=['canonical-image', 'two-aliases-one-blob'])
def test_real_native_hosted_native_roundtrip_preserves_frozen_hashes_images_and_live_edits(tmp_path, aliases):
    from hosted.packages import HostedProjectPackageService
    from tests.test_v10353 import _services, OWNER
    from tests.test_v10572 import reopened
    con, native, sid, digest, raw, png = _rich_native(tmp_path, aliases)
    service = HostedProjectPackageService(_services()[-1])
    restored = service.import_bytes(native, OWNER)
    pid = restored['id']; domain = service.domain
    domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_legacy_original-block'},
                  {'content_html': 'Edited current prose', 'text_content': 'Edited current prose'}, user_id=OWNER)
    domain.create('chapter', {'project_id': pid}, {'tab_id': 'tab_legacy_1', 'title': 'New hosted chapter', 'position': 1}, user_id=OWNER)
    exported = service.export_local_bytes(pid, OWNER)
    if aliases:
        with zipfile.ZipFile(io.BytesIO(service.export_bytes(pid, OWNER))) as hosted:
            assert len([name for name in hosted.namelist() if name.startswith('assets/')]) == 1
        with zipfile.ZipFile(io.BytesIO(exported)) as native_zip:
            assert native_zip.read('assets/original.png') == native_zip.read('assets/second.png') == png
    with reopened(exported) as result:
        assert result.execute('SELECT content_hash FROM review_snapshots WHERE id=?', (sid,)).fetchone()[0] == digest
        assert result.execute('SELECT text_content FROM review_snapshot_blocks WHERE source_block_id=?', ('original-block',)).fetchone()[0] == 'Frozen prose'
        assert result.execute('SELECT text_content FROM manuscript_blocks WHERE id=?', ('original-block',)).fetchone()[0] == 'Edited current prose'
        assert zlib.decompress(result.execute('SELECT snapshot_blob FROM checkpoints WHERE id=4').fetchone()[0]) == raw
        assert result.execute('SELECT payload FROM checkpoint_assets').fetchone()[0] == png
        assert result.execute('SELECT source_chapter_id FROM review_snapshot_chapters').fetchone()[0] == 1
        assert result.execute("SELECT id FROM chapters WHERE title='New hosted chapter'").fetchone()[0] != 1
        assert result.execute('SELECT answer_text FROM review_question_answers').fetchone()[0] == 'Original answer'
        assert result.execute('SELECT body FROM review_comments').fetchone()[0] == 'Original comment'
        event = result.execute('SELECT entity_id,payload_json FROM review_events').fetchone()
        assert event[0] == sid and json.loads(event[1])['snapshot_id'] == sid
        assert json.loads(event[1])['checkpoint_ids'] == [4]
        assert result.execute('PRAGMA foreign_key_check').fetchall() == []
        from storage.invariants import project_invariant_errors
        result.row_factory = sqlite3.Row
        assert project_invariant_errors(result, 1) == []
    con.close()
