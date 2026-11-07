"""Pure, bounded Schema-42 frozen-history import planning.

Native history is not a capture of today's live manuscript. Preserve its exact
source bytes/hash alongside a separately rebased rendering projection. Nothing
in this module publishes records, grants access, or trusts archived identities
as hosted authority. The caller stages the plan under a new authorized project.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import re
import zlib

from hosted.domain_store import _ID_FIELDS
from hosted.firestore.ids import require_opaque_id, require_project_id
from security.content_security import is_safe_inline_html, is_safe_project_asset_path
from storage.values import review_snapshot_content_hash

_LIMIT = 8 * 1024 * 1024
_TOTAL_LIMIT = 192 * 1024 * 1024
_MAX_HISTORY_ROWS = 100_000
_GROUPS = ('chapters', 'scenes', 'blocks', 'markers', 'questions', 'checkpoints')
_SOURCE_FIELDS = {
    'source_tab_id': 'document_tab', 'source_chapter_id': 'chapter',
    'source_scene_id': 'scene', 'source_block_id': 'manuscript_block',
    'source_marker_id': 'review_marker_label',
    'source_questionnaire_id': 'questionnaire',
    'source_question_id': 'questionnaire_question',
    'checkpoint_id': 'checkpoint',
}


@dataclass
class NativeHistoryPlan:
    payloads: dict = field(default_factory=dict)
    references: dict = field(default_factory=dict)
    assets: list = field(default_factory=list)


def _fail(reason):
    raise ValueError('HOSTED_PACKAGE_HISTORY_' + reason)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError):
        _fail('JSON_INVALID')


def _unpack(blob):
    if not isinstance(blob, bytes) or len(blob) > _LIMIT:
        _fail('CHECKPOINT_INVALID')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(blob, _LIMIT + 1)
        if len(raw) > _LIMIT or decoder.unconsumed_tail:
            _fail('TOO_LARGE')
        if not decoder.eof or decoder.unused_data:
            _fail('CHECKPOINT_INVALID')
        result = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError, zlib.error) as exc:
        if str(exc).startswith('HOSTED_PACKAGE_HISTORY_'):
            raise
        _fail('CHECKPOINT_INVALID')
    if not isinstance(result, dict):
        _fail('CHECKPOINT_INVALID')
    return raw, result


def _validate_blocks(blocks):
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        _fail('BLOCK_INVALID')
    for block in blocks:
        kind = str(block.get('block_type') or 'paragraph')
        fragment, path = str(block.get('content_html') or ''), str(block.get('image_path') or '')
        if kind == 'image':
            if fragment or not path or not is_safe_project_asset_path(path):
                _fail('UNSAFE_HTML')
        elif (kind not in {'paragraph', 'text'} or path
              or fragment and not is_safe_inline_html(fragment, str(block.get('text_content') or ''))):
            _fail('UNSAFE_HTML')


def prepare_native_history(con, pid, id_maps):
    """Return payload/field overrides and validated historical image bytes.

    Invoke after allocating live IDs and before generic FK translation. Apply
    references[(logical, native_id, column)] instead of generic FK conversion;
    merge payloads[(logical, native_id)] last. Assets are (source_api_path, bytes)
    pairs for the caller's normal image-validation/publication path.
    """
    require_project_id(pid)
    plan = NativeHistoryPlan()
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cache = {}
    row_count = 0

    def rows(table):
        nonlocal row_count
        if table not in cache:
            remaining = _MAX_HISTORY_ROWS - row_count
            cache[table] = ([dict(row) for row in con.execute(f'SELECT * FROM "{table}" LIMIT {remaining + 1}')]
                            if table in tables else [])
            row_count += len(cache[table])
            if row_count > _MAX_HISTORY_ROWS:
                _fail('ROW_LIMIT')
        return cache[table]

    def identity(kind, value):
        if value is None:
            return None
        old = str(value)
        if not old:
            _fail('REFERENCE_INVALID')
        # Deleted live sources are valid historical identities, not new live
        # records. Deterministic names keep anchors aligned with frozen blocks.
        prefix = ('snapshotquestion_' if kind == 'review_snapshot_question'
                  else _ID_FIELDS[kind][1])
        return require_opaque_id(id_maps.get((kind, old), prefix + 'legacy_' + old))

    def strict(kind, value):
        result = id_maps.get((kind, str(value)))
        if result is None:
            _fail('REFERENCE_INVALID')
        return result

    def override(kind, old, key, value):
        plan.references[(kind, str(old), key)] = value

    # Archived creation time is lineage evidence, never server-owned created_at.
    for logical, table in (('chapter', 'chapters'), ('document_tab', 'document_tabs')):
        for row in rows(table):
            if row.get('created_at'):
                plan.payloads[(logical, str(row['id']))] = {'native_created_at': row['created_at']}

    total = 0
    checkpoints = {str(row['id']): row for row in rows('checkpoints')}
    anchor_rounds = {str(row['id']): str(row['snapshot_id']) for row in rows('review_anchors')}
    snapshots = {str(row['id']): row for row in rows('review_snapshots')}
    frozen = {sid: {group: [] for group in _GROUPS} for sid in snapshots}
    for group in _GROUPS:
        for row in rows('review_snapshot_' + group):
            sid = str(row['snapshot_id'])
            if sid not in frozen:
                _fail('ROUND_REFERENCE_INVALID')
            frozen[sid][group].append(row)
        for groups in frozen.values():
            groups[group].sort(key=lambda row: (row.get('ordinal', row.get('position', 0)), str(row['id'])))
    for sid, row in snapshots.items():
        groups = frozen[sid]
        _validate_blocks(groups['blocks'])
        expected = review_snapshot_content_hash(row['review_id'], row['source_generation'], row['scope_type'],
            row, groups['chapters'], groups['scenes'], groups['blocks'], groups['questions'], groups['markers'])
        if expected != row.get('content_hash'):
            _fail('REVIEW_HASH_MISMATCH')
        source = _json({'snapshot': row, **groups})
        total += len(source)
        if len(source) > _LIMIT or total > _TOTAL_LIMIT:
            _fail('TOO_LARGE')
        projected = deepcopy(row)
        projected.update(id=strict('review_snapshot', sid), review_id=strict('review', row['review_id']))
        projected['native_source'] = {
            'format': 'breadmaker-native-review-v1', 'content_hash': expected,
            'relational_blob_b64': base64.b64encode(zlib.compress(source, 6)).decode('ascii'),
            'sha256': hashlib.sha256(source).hexdigest(),
        }
        for group, values in groups.items():
            output = []
            for original in values:
                saved = deepcopy(original)
                saved['snapshot_id'] = projected['id']
                # Relational join PKs are not public identity. Frozen question
                # identity *is* consumed by saved answers and must be rebased.
                if group == 'questions':
                    saved['id'] = identity('review_snapshot_question', original['id'])
                for key, logical in _SOURCE_FIELDS.items():
                    if key in saved:
                        saved[key] = (strict(logical, saved[key]) if key == 'checkpoint_id'
                                      else identity(logical, saved[key]))
                if group == 'checkpoints':
                    checkpoint = checkpoints.get(str(original['checkpoint_id']))
                    if checkpoint is None:
                        _fail('REFERENCE_INVALID')
                    for key in ('scope', 'name', 'description', 'source_chapter_title', 'word_count'):
                        if key in checkpoint:
                            saved[key] = checkpoint[key]
                    saved['checkpoint_updated_at'] = checkpoint.get('updated_at')
                output.append(saved)
            projected[group] = output
            projected[group[:-1] + '_count' if group != 'chapters' else 'chapter_count'] = len(output)
        projected['checkpoint_ids'] = [item['checkpoint_id'] for item in projected['checkpoints']]
        for table, key in (('review_comments', 'comment_count'), ('review_highlights', 'highlight_count'),
                           ('review_suggestions', 'suggestion_count')):
            projected[key] = sum(anchor_rounds.get(str(item.get('anchor_id'))) == sid for item in rows(table))
        projected['answer_count'] = sum(str(item['snapshot_id']) == sid for item in rows('review_question_answers'))
        projected['general_feedback_count'] = sum(str(item['snapshot_id']) == sid and bool(str(item.get('body') or '').strip())
                                                   for item in rows('review_general_feedback'))
        projected['checkpoint_update_allowed'] = False
        projected['checkpoint_update_blocker'] = 'HOSTED_REVIEW_CHECKPOINT_UPDATE_UNSUPPORTED'
        if len(_json(projected)) > _LIMIT:
            _fail('TOO_LARGE')
        plan.payloads[('review_snapshot', sid)] = {'snapshot': projected}

    # Pick the actual latest open/paused round; never lexical UUID ordering.
    for review in rows('reviews'):
        rounds = [row for row in snapshots.values() if row['review_id'] == review['id']]
        if rounds:
            rounds.sort(key=lambda row: ({'open': 0, 'paused': 1, 'closed': 2}[row['status']], -row['round_number']))
            summaries = []
            for native in sorted(rounds, key=lambda item: -item['round_number']):
                full = plan.payloads[('review_snapshot', str(native['id']))]['snapshot']
                summary = {key: deepcopy(value) for key, value in full.items()
                           if key not in {*_GROUPS, 'native_source'}}
                summary['checkpoints'] = deepcopy(full['checkpoints'])
                summaries.append(summary)
            plan.payloads[('review', str(review['id']))] = {
                'active_snapshot_id': strict('review_snapshot', rounds[0]['id']),
                'closed_snapshot_ids': [strict('review_snapshot', row['id']) for row in rounds if row['status'] == 'closed'],
                'native_rounds': summaries,
            }
        scope_type = review.get('scope_type', 'manuscript')
        scopes = []
        if scope_type in ('chapters', 'scenes'):
            kind = 'chapter' if scope_type == 'chapters' else 'scene'
            scopes = [strict(kind, item[kind + '_id']) for item in rows('review_scope_' + scope_type)
                      if item['review_id'] == review['id']]
        plan.payloads.setdefault(('review', str(review['id'])), {})['scope_ids'] = scopes

    for marker in rows('review_markers'):
        plan.payloads[('review_marker_assignment', str(marker['id']))] = {
            'marker_label_id': strict('review_marker_label', marker['marker_id'])}

    event_types = {'snapshot': 'review_snapshot', 'participant': 'review_participant',
                   'checkpoint': 'checkpoint', 'comment': 'review_comment',
                   'highlight': 'review_highlight', 'suggestion': 'review_suggestion',
                   'questionnaire': 'questionnaire', 'review': 'review'}
    for event in rows('review_events'):
        target = event_types.get(event.get('entity_type'))
        if target and event.get('entity_id'):
            override('review_event', event['id'], 'entity_id', identity(target, event['entity_id']))
        try:
            original = json.loads(event.get('payload_json') or '{}')
        except (ValueError, TypeError):
            _fail('EVENT_INVALID')
        if not isinstance(original, dict):
            _fail('EVENT_INVALID')
        payload = deepcopy(original)
        for key, target in {'snapshot_id': 'review_snapshot', 'participant_id': 'review_participant',
                            'checkpoint_id': 'checkpoint'}.items():
            if key in payload and payload[key] is not None:
                payload[key] = identity(target, payload[key])
        if 'checkpoint_ids' in payload:
            if not isinstance(payload['checkpoint_ids'], list):
                _fail('EVENT_INVALID')
            payload['checkpoint_ids'] = [identity('checkpoint', value) for value in payload['checkpoint_ids']]
        # Original actor attribution stays inert historical content. No member,
        # invitation, session or external access is created from this JSON.
        plan.payloads[('review_event', str(event['id']))] = {
            'payload_json': _json(payload).decode('utf-8'),
            'native_payload_json': event.get('payload_json') or '{}',
            'native_created_at': event.get('created_at'),
        }

    for anchor in rows('review_anchors'):
        sid = str(anchor['snapshot_id'])
        if sid not in frozen or snapshots[sid]['review_id'] != anchor['review_id']:
            _fail('ANCHOR_REFERENCE_INVALID')
        block_ids = {str(row['source_block_id']) for row in frozen[sid]['blocks']}
        for key in ('start_block_id', 'end_block_id'):
            if str(anchor[key]) not in block_ids:
                _fail('ANCHOR_REFERENCE_INVALID')
            override('review_anchor', anchor['id'], key, identity('manuscript_block', anchor[key]))
    for answer in rows('review_question_answers'):
        sid = str(answer['snapshot_id'])
        if (sid not in frozen or snapshots[sid]['review_id'] != answer['review_id']
                or str(answer['question_id']) not in {str(row['id']) for row in frozen[sid]['questions']}):
            _fail('QUESTION_REFERENCE_INVALID')
        override('review_question_answer', answer['id'], 'question_id',
                 identity('review_snapshot_question', answer['question_id']))

    assets = {row['hash']: row for row in rows('checkpoint_assets')}
    asset_refs = {}
    paths = {}
    for ref in rows('checkpoint_asset_refs'):
        strict('checkpoint', ref['checkpoint_id'])
        asset = assets.get(ref['asset_hash'])
        if not asset or not isinstance(asset['payload'], bytes):
            _fail('ASSET_INVALID')
        blob, digest, path = asset['payload'], str(ref['asset_hash']), str(ref['asset_path'])
        if (len(blob) != asset['byte_size'] or hashlib.sha256(blob).hexdigest() != digest
                or not re.fullmatch(r'/api/assets/img_[A-Za-z0-9_-]+\.(?:png|jpg|webp)', path)):
            _fail('ASSET_INVALID')
        if path in paths and paths[path] != blob:
            _fail('ASSET_CONFLICT')
        paths[path] = blob
        asset_refs.setdefault(str(ref['checkpoint_id']), []).append(dict(ref))
    if set(assets) - {ref['asset_hash'] for refs in asset_refs.values() for ref in refs}:
        # Orphans carry no snapshot semantics. Do not silently discard bytes.
        _fail('UNREFERENCED_ASSET')
    plan.assets = sorted(paths.items())

    nested_fields = {
        'tab_id': 'document_tab', 'chapter_id': 'chapter', 'scene_id': 'scene',
        'block_id': 'manuscript_block', 'thread_id': 'comment_thread', 'label_id': 'markup_label',
    }
    kinds = {'tab': 'document_tab', 'chapter': 'chapter', 'chapters': 'chapter',
             'blocks': 'manuscript_block', 'scenes': 'scene', 'annotations': 'annotation',
             'comment_threads': 'comment_thread', 'comment_messages': 'comment_message'}
    for row in rows('checkpoints'):
        old = str(row['id'])
        raw, source = _unpack(row['snapshot_blob'])
        total += len(raw)
        if total > _TOTAL_LIMIT:
            _fail('TOO_LARGE')
        if hashlib.sha256(raw).hexdigest() != row['snapshot_hash']:
            _fail('CHECKPOINT_HASH_MISMATCH')
        if source.get('version') != 1 or source.get('scope') not in ('book', 'chapter') or source['scope'] != row['scope']:
            _fail('CHECKPOINT_FORMAT_UNSUPPORTED')
        if not isinstance(source.get('tab'), dict) or not isinstance(source.get('blocks'), list):
            _fail('CHECKPOINT_INVALID')
        _validate_blocks(source['blocks'])
        snapshot = deepcopy(source)
        refs = asset_refs.get(old, [])
        required = {block.get('image_path') for block in source['blocks']
                    if block.get('block_type') == 'image' and str(block.get('image_path') or '').startswith('/api/assets/')}
        if required - {ref['asset_path'] for ref in refs}:
            _fail('ASSET_MISSING')
        for group, kind in kinds.items():
            if group not in snapshot:
                continue
            value = snapshot[group]
            objects = [value] if group in ('tab', 'chapter') else value
            if not isinstance(objects, list) or any(not isinstance(item, dict) for item in objects):
                _fail('CHECKPOINT_INVALID')
            for obj in objects:
                if 'id' in obj:
                    obj['id'] = identity(kind, obj['id'])
                for key, target in nested_fields.items():
                    if key in obj:
                        obj[key] = identity(target, obj[key])
        snapshot['native_source'] = {
            'format': 'breadmaker-native-checkpoint-v1', 'id': row['id'],
            'snapshot_hash': row['snapshot_hash'],
            'snapshot_blob_b64': base64.b64encode(row['snapshot_blob']).decode('ascii'),
            'asset_refs': refs,
        }
        if len(_json(snapshot)) > _LIMIT:
            _fail('TOO_LARGE')
        plan.payloads[('checkpoint', old)] = {'snapshot': snapshot}
        for key, target in {**_SOURCE_FIELDS, 'current_source_chapter_id': 'chapter'}.items():
            if key in row:
                override('checkpoint', old, key, identity(target, row[key]))
    return plan
