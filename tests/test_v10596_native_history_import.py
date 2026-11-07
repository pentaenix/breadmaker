"""Frozen import projections must use historical content, not today's rows."""
import base64
from copy import deepcopy
import hashlib
import json
import sqlite3
import zlib

import pytest

from hosted.native_history import prepare_native_history
from hosted.review_history_ui import enrich_review_summaries, feedback_rows, snapshot_payload
from storage.values import review_snapshot_content_hash


PID = 'a' * 32


def _fixture(*, image=False):
    chapter = {'id': 1, 'snapshot_id': 'round_old', 'source_chapter_id': 11, 'source_tab_id': 1,
               'ordinal': 0, 'position': 0, 'title': 'Frozen title', 'is_implicit': 0, 'description': ''}
    block = {'id': 1, 'snapshot_id': 'round_old', 'source_block_id': 'deleted_block', 'source_tab_id': 1,
             'source_chapter_id': 11, 'source_scene_id': None, 'ordinal': 0, 'position': 0.0,
             'block_type': 'paragraph', 'semantic_style': 'body', 'content_html': 'Frozen <b>text</b>',
             'text_content': 'Frozen text', 'image_path': '', 'content_hash': 'saved-block-hash'}
    question = {'id': 'frozen_question', 'snapshot_id': 'round_old', 'source_questionnaire_id': 'deleted_form',
                'source_question_id': 'deleted_question', 'questionnaire_name': 'Frozen form', 'position': 0,
                'prompt': 'Original question?', 'required': 1}
    marker = {'id': 1, 'snapshot_id': 'round_old', 'source_marker_id': 3, 'position': 0,
              'name': 'Original marker', 'description': '', 'color': '#123456'}
    snapshot = {'id': 'round_old', 'review_id': 'review_one', 'round_number': 1, 'status': 'closed',
                'source_generation': 9, 'scope_type': 'chapters', 'permission_read': 1,
                'permission_comment': 1, 'permission_suggest': 1, 'permission_feedback': 1,
                'created_at': '2025-01-01', 'content_hash': ''}
    snapshot['content_hash'] = review_snapshot_content_hash('review_one', 9, 'chapters', snapshot,
        [chapter], [], [block], [question], [marker])
    checkpoint = {'version': 1, 'scope': 'chapter', 'captured_at': '2025-01-01',
                  'tab': {'id': 1, 'title': 'Manuscript', 'created_at': '2024-01-01'},
                  'chapter': {'id': 11, 'tab_id': 1, 'title': 'Frozen title', 'created_at': '2024-01-01'},
                  'blocks': [{'id': 'deleted_block', 'text_content': 'Frozen text', 'content_html': 'Frozen <b>text</b>',
                              'block_type': 'paragraph', 'image_path': ''}], 'scenes': [],
                  'annotations': [{'id': 5, 'block_id': 'deleted_block', 'thread_id': 7, 'label_id': 3}],
                  'comment_threads': [{'id': 7, 'status': 'open'}],
                  'comment_messages': [{'id': 8, 'thread_id': 7, 'body': 'Frozen note'}],
                  'include_markup': True, 'include_notes': True, 'include_scenes': True}
    asset = b'historic-image-bytes'
    digest = hashlib.sha256(asset).hexdigest()
    image_path = '/api/assets/img_' + digest[:24] + '.jpg'
    if image:
        checkpoint['blocks'].append({'id': 'deleted_image', 'block_type': 'image', 'image_path': image_path})
    raw = json.dumps(checkpoint, sort_keys=True, separators=(',', ':')).encode()
    tables = {
        'reviews': [{'id': 'review_one', 'status': 'open'}],
        'review_snapshots': [snapshot],
        'review_snapshot_chapters': [chapter], 'review_snapshot_blocks': [block],
        'review_snapshot_questions': [question], 'review_snapshot_markers': [marker],
        'review_snapshot_checkpoints': [{'id': 1, 'snapshot_id': 'round_old', 'checkpoint_id': 4, 'ordinal': 0, 'source_chapter_id': 11}],
        'review_anchors': [{'id': 'anchor_one', 'review_id': 'review_one', 'snapshot_id': 'round_old',
                            'start_block_id': 'deleted_block', 'end_block_id': 'deleted_block'}],
        'review_question_answers': [{'id': 'answer_one', 'review_id': 'review_one', 'snapshot_id': 'round_old', 'question_id': 'frozen_question'}],
        'checkpoints': [{'id': 4, 'scope': 'chapter', 'snapshot_blob': zlib.compress(raw),
                        'snapshot_hash': hashlib.sha256(raw).hexdigest(), 'source_chapter_id': 11,
                        'source_tab_id': 1, 'current_source_chapter_id': 11}],
        'chapters': [{'id': 11, 'title': 'Changed live title', 'created_at': '2024-01-01'}],
        'document_tabs': [{'id': 1, 'created_at': '2024-01-01'}],
    }
    if image:
        tables['checkpoint_assets'] = [{'hash': digest, 'payload': asset, 'byte_size': len(asset)}]
        tables['checkpoint_asset_refs'] = [{'checkpoint_id': 4, 'asset_path': image_path, 'asset_hash': digest}]
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    for table, rows in tables.items():
        columns = list(rows[0])
        # No affinity coercion: test hashes use the exact source values.
        con.execute(f'CREATE TABLE {table} (' + ','.join(columns) + ')')
        con.executemany(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in columns) + ')',
                        [[row.get(column) for column in columns] for row in rows])
    ids = {('review', 'review_one'): 'review_hosted', ('review_snapshot', 'round_old'): 'snapshot_hosted',
           ('checkpoint', '4'): 'checkpoint_hosted', ('chapter', '11'): 'chapter_hosted',
           ('document_tab', '1'): 'tab_hosted'}
    return con, ids, tables


def test_frozen_review_preserves_deleted_sources_questions_hash_and_checkpoint_links():
    con, ids, tables = _fixture()
    plan = prepare_native_history(con, PID, ids)
    frozen = plan.payloads['review_snapshot', 'round_old']['snapshot']
    assert frozen['chapters'][0]['title'] == 'Frozen title'
    assert frozen['chapters'][0]['source_chapter_id'] == 'chapter_hosted'
    assert frozen['blocks'][0]['source_block_id'] == 'b_legacy_deleted_block'
    assert frozen['blocks'][0]['content_html'] == 'Frozen <b>text</b>'
    assert frozen['content_hash'] == tables['review_snapshots'][0]['content_hash']
    assert frozen['checkpoint_ids'] == ['checkpoint_hosted']
    assert frozen['questions'][0]['id'] == plan.references['review_question_answer', 'answer_one', 'question_id']
    assert frozen['markers'][0]['name'] == 'Original marker'
    assert plan.references['review_anchor', 'anchor_one', 'start_block_id'] == frozen['blocks'][0]['source_block_id']
    evidence = frozen['native_source']
    raw = zlib.decompress(base64.b64decode(evidence['relational_blob_b64']))
    assert hashlib.sha256(raw).hexdigest() == evidence['sha256']
    assert json.loads(raw)['blocks'] == tables['review_snapshot_blocks']
    assert plan.payloads['chapter', '11']['native_created_at'] == '2024-01-01'


def test_checkpoint_keeps_exact_compressed_bytes_notes_and_historic_images():
    con, ids, tables = _fixture(image=True)
    plan = prepare_native_history(con, PID, ids)
    snapshot = plan.payloads['checkpoint', '4']['snapshot']
    assert snapshot['blocks'][0]['id'] == 'b_legacy_deleted_block'
    assert snapshot['annotations'][0]['block_id'] == snapshot['blocks'][0]['id']
    assert snapshot['comment_messages'][0]['thread_id'] == snapshot['comment_threads'][0]['id']
    assert snapshot['comment_messages'][0]['body'] == 'Frozen note'
    assert snapshot['chapter']['id'] == 'chapter_hosted'
    assert base64.b64decode(snapshot['native_source']['snapshot_blob_b64']) == tables['checkpoints'][0]['snapshot_blob']
    assert snapshot['native_source']['snapshot_hash'] == tables['checkpoints'][0]['snapshot_hash']
    assert plan.assets == [(snapshot['blocks'][1]['image_path'], b'historic-image-bytes')]
    # Canonical hosted-v3 JSON export/reimport never needs to coerce bytes.
    assert json.loads(json.dumps(snapshot)) == snapshot


@pytest.mark.parametrize('sql,error', [
    ("UPDATE review_snapshot_blocks SET text_content='corrupt',content_html='corrupt'", 'REVIEW_HASH_MISMATCH'),
    ("UPDATE checkpoints SET snapshot_hash='corrupt'", 'CHECKPOINT_HASH_MISMATCH'),
    ("UPDATE review_anchors SET start_block_id='unfrozen'", 'ANCHOR_REFERENCE_INVALID'),
    ("UPDATE review_question_answers SET question_id='unfrozen'", 'QUESTION_REFERENCE_INVALID'),
    ("DELETE FROM checkpoint_asset_refs", 'UNREFERENCED_ASSET'),
    ("UPDATE checkpoint_assets SET payload=x'00'", 'ASSET_INVALID'),
])
def test_corrupt_history_fails_during_pure_preflight(sql, error):
    con, ids, _ = _fixture(image=True)
    con.execute(sql)
    with pytest.raises(ValueError, match=error):
        prepare_native_history(con, PID, ids)


def test_oversized_checkpoint_decompression_is_bounded(monkeypatch):
    from hosted import native_history
    monkeypatch.setattr(native_history, '_LIMIT', 100)
    with pytest.raises(ValueError, match='TOO_LARGE'):
        native_history._unpack(zlib.compress(b'x' * 10000))


@pytest.mark.parametrize('fragment,path,kind', [
    ('<img src=x onerror=alert(1)>', '', 'paragraph'),
    ('<b onclick=alert(1)>Frozen text</b>', '', 'paragraph'),
    ('<script>alert(1)</script>', '', 'paragraph'),
    ('', 'https://external.invalid/track.jpg', 'image'),
])
def test_checkpoint_rendered_html_is_rejected_even_when_hash_is_recomputed(fragment, path, kind):
    con, ids, tables = _fixture()
    source = json.loads(zlib.decompress(tables['checkpoints'][0]['snapshot_blob']))
    source['blocks'][0].update(content_html=fragment, image_path=path, block_type=kind)
    raw = json.dumps(source).encode()
    con.execute('UPDATE checkpoints SET snapshot_blob=?,snapshot_hash=?',
                (zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
    with pytest.raises(ValueError, match='UNSAFE_HTML'):
        prepare_native_history(con, PID, ids)


def test_sidebar_uses_small_derived_summaries_without_snapshot_object_reads():
    con, ids, _ = _fixture()
    plan = prepare_native_history(con, PID, ids)
    review = {'id': 'review_hosted', 'status': 'open', **plan.payloads['review', 'review_one']}
    def metadata_only(kind, parents):
        assert kind == 'review_event', 'Sidebar must not load frozen manuscript objects'
        assert parents == {'project_id': PID, 'review_id': 'review_hosted'}
        return []
    result = enrich_review_summaries(PID, [review], metadata_only)[0]
    assert result['rounds'][0]['id'] == 'snapshot_hosted'
    assert result['snapshot']['block_count'] == 1
    assert 'blocks' not in result['snapshot']
    assert 'native_source' not in result['snapshot']
    assert 'native_rounds' not in result
    assert 'native_rounds' in review


def test_selected_round_feedback_joins_frozen_anchors_questions_and_filters_reader():
    con, ids, _ = _fixture()
    plan = prepare_native_history(con, PID, ids)
    frozen = plan.payloads['review_snapshot', 'round_old']['snapshot']
    question = frozen['questions'][0]
    source = {
        'review_anchor': [{'id': 'anchor', 'start_block_id': 'b_legacy_deleted_block', 'end_block_id': 'b_legacy_deleted_block', 'start_offset': 0, 'end_offset': 4}],
        'review_participant': [{'id': 'reader_one', 'display_name': 'Synthetic Reader'}],
        'review_comment': [{'id': 'comment', 'anchor_id': 'anchor', 'participant_id': 'reader_one', 'body': 'Frozen feedback'},
                           {'id': 'private_other', 'anchor_id': 'anchor', 'participant_id': 'reader_other'}],
        'review_question_answer': [{'id': 'answer', 'snapshot_id': 'snapshot_hosted', 'participant_id': 'reader_one', 'question_id': question['id'], 'answer_text': 'Original answer'}],
        'review_general_feedback': [{'id': 'general', 'snapshot_id': 'snapshot_hosted', 'participant_id': 'reader_one', 'body': 'Original report'}],
    }
    def authorized_list(kind, parents):
        assert parents['project_id'] == PID and parents['review_id'] == 'review_hosted'
        assert kind != 'review_snapshot', 'Selected round must not hydrate other novels'
        if kind == 'review_anchor':
            assert parents['snapshot_id'] == 'snapshot_hosted'
        return source.get(kind, [])
    before = deepcopy(frozen)
    result = feedback_rows(PID, 'review_hosted', authorized_list, 'reader_one', 'snapshot_hosted', [frozen])
    assert len(result['comments']) == 1
    assert result['comments'][0]['start_block_id'] == frozen['blocks'][0]['source_block_id']
    assert result['comments'][0]['participant_name'] == 'Synthetic Reader'
    assert result['feedback_forms'][0]['answers'][0]['prompt'] == 'Original question?'
    assert result['feedback_forms'][0]['general_feedback'] == 'Original report'
    assert 'native_source' not in snapshot_payload({'snapshot': frozen})
    assert frozen == before


def test_checkpoint_lineage_uses_archived_creation_only_when_live_id_matches():
    from hosted.checkpoint_ui import HostedCheckpointUI
    from hosted.firestore.contracts import DocumentNotFound
    con, ids, tables = _fixture()
    plan = prepare_native_history(con, PID, ids)
    class Domain:
        def get(self, kind, parents, **kwargs):
            if kind == 'chapter' and parents['chapter_id'] == 'chapter_hosted':
                return {'id': 'chapter_hosted', 'created_at': '2026-11-07', 'native_created_at': '2024-01-01'}
            raise DocumentNotFound('missing')
    ui = HostedCheckpointUI(Domain(), PID, 'owner', None)
    row = {'scope': 'chapter', 'source_chapter_id': 'chapter_hosted', 'source_chapter_created_at': '2024-01-01',
           **plan.payloads['checkpoint', '4']}
    meta, snapshot = ui.projection(row)
    assert meta['source_exists'] is True and meta['current_chapter_id'] == 'chapter_hosted'
    assert snapshot['blocks'][0]['text_content'] == 'Frozen text'
    row['source_chapter_created_at'] = 'another-lineage'
    assert ui.projection(row)[0]['source_exists'] is False


def test_portable_export_restores_native_lineage_without_extra_field():
    from hosted.portability_editing import project_row
    for table in ('chapters', 'document_tabs'):
        projected = project_row(table, {'title': 'Saved', 'created_at': '2026-11-07',
            'native_created_at': '2024-01-01'}, 1, '2026-11-07', {})
        assert projected == {'title': 'Saved', 'created_at': '2024-01-01'}
        for invalid in (None, [], 123, '', 'x' * 129):
            with pytest.raises(ValueError, match='NATIVE_LINEAGE_INVALID'):
                project_row(table, {'native_created_at': invalid}, 1, 'now', {})
