"""Restore imported native frozen evidence without inventing a new history.

The normal hosted export still owns current live records. This adapter restores
native identities for imported rows, preserves historical IDs even after live
deletion, and verifies that stored rendering projections agree with their source
evidence before unpacking it into the local relational model.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import re

from hosted.domain_store import _ID_FIELDS
from hosted.native_history import _GROUPS, _SOURCE_FIELDS, _LIMIT, _unpack, _validate_blocks
from storage.values import review_snapshot_content_hash


def _fail(reason):
    raise ValueError('HOSTED_PORTABLE_NATIVE_HISTORY_' + reason)


def _decode_evidence(value):
    if not isinstance(value, str) or len(value) > 4 * ((_LIMIT + 2) // 3):
        _fail('EVIDENCE_TOO_LARGE')
    try:
        blob = base64.b64decode(value, validate=True)
        raw, source = _unpack(blob)
    except (ValueError, TypeError):
        _fail('EVIDENCE_INVALID')
    return blob, raw, source


@dataclass
class NativeExportPlan:
    records: dict
    snapshots: set = field(default_factory=set)
    preferred: dict = field(default_factory=dict)
    reserved: dict = field(default_factory=dict)
    raw_columns: dict = field(default_factory=dict)


def native_id(logical, value):
    if value is None:
        return None
    prefix = ('snapshotquestion_' if logical == 'review_snapshot_question' else _ID_FIELDS[logical][1]) + 'legacy_'
    value = str(value)
    return value[len(prefix):] if value.startswith(prefix) else None


def prepare_native_export(records, mapping, assets):
    if (not isinstance(records, dict) or any(not isinstance(items, list) for items in records.values())
            or sum(len(items) for items in records.values()) > 100_000
            or any(not isinstance(item, dict) or not isinstance(item.get('ids'), dict)
                   or not isinstance(item.get('data'), dict)
                   for items in records.values() for item in items)):
        _fail('SOURCE_INVALID')
    result = NativeExportPlan(deepcopy(records))
    records = result.records
    native = any(isinstance(item.get('data', {}).get('snapshot'), dict)
                 and isinstance(item['data']['snapshot'].get('native_source'), dict)
                 for key in ('checkpoint', 'review_snapshot') for item in records.get(key, []))
    native = native or any(native_id(logical, item.get('data', {}).get('id')) is not None
                           for logical, items in records.items() if logical in _ID_FIELDS
                           for item in items)
    if not native:
        return result
    logical_for_table = {table: logical for logical, table in mapping.items()}
    expanded = 0

    def prefer(table, old, value):
        result.preferred[(table, str(old))] = value
        if isinstance(value, int):
            result.reserved.setdefault(table, set()).add(value)

    def reserve(kind, value):
        if value is not None and isinstance(value, int):
            result.reserved.setdefault(mapping[kind], set()).add(value)

    # Original imported IDs keep source hashes/lineages meaningful. New hosted
    # rows are allocated outside the full historical integer reservation set.
    for logical, items in records.items():
        if logical not in _ID_FIELDS or logical not in mapping:
            continue
        for item in items:
            old = str(item['data'].get('id') or '')
            restored = native_id(logical, old)
            if restored is None:
                continue
            # Association path IDs intentionally replaced native join-row PKs.
            if logical in {'review_questionnaire_assignment', 'review_scope_chapter', 'review_scope_scene'}:
                continue
            prefer(mapping[logical], old, int(restored) if restored.isdecimal() else restored)

    def historical_integer(kind, value):
        restored = native_id(kind, value)
        if restored is None or not restored.isdecimal() or int(restored) < 1:
            _fail('SOURCE_REFERENCE_INVALID')
        reserve(kind, int(restored))
        return int(restored)

    for item in records.get('canvas_version_item', []):
        row = item['data']; kind = row.get('entity_type')
        if kind in {'chapter', 'scene'} and native_id(kind, row.get('entity_id')) is not None:
            row['entity_id'] = historical_integer(kind, row['entity_id'])
            result.raw_columns[('canvas_version_items', str(row['id']))] = {'entity_id'}
    for item in records.get('canvas_version', []):
        row = item['data']
        if native_id('canvas_version', row.get('id')) is None or 'workspace_json' not in row:
            continue
        try:
            workspace = json.loads(row['workspace_json'] or '{}')
        except (ValueError, TypeError):
            _fail('CANVAS_WORKSPACE_INVALID')
        if not isinstance(workspace, dict):
            _fail('CANVAS_WORKSPACE_INVALID')
        fields = {'label_id': 'markup_label', 'chapter_id': 'chapter', 'scene_id': 'scene',
                  'icon_marker_id': 'icon_marker', 'marker_id': 'markup_label',
                  'from_node_id': 'canvas_node', 'to_node_id': 'canvas_node'}
        for group, kind in (('nodes', 'canvas_node'), ('edges', 'canvas_edge')):
            values = workspace.get(group, [])
            if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
                _fail('CANVAS_WORKSPACE_INVALID')
            for value in values:
                value['id'] = historical_integer(kind, value.get('id'))
                for key, target in fields.items():
                    if value.get(key) is not None:
                        value[key] = historical_integer(target, value[key])
        styles = workspace.get('structural_styles', {})
        if not isinstance(styles, dict):
            _fail('CANVAS_WORKSPACE_INVALID')
        restored_styles = {}
        for key, style in styles.items():
            match = re.fullmatch(r'(chapter|scene):(.+)', key)
            if match is None or not isinstance(style, dict):
                _fail('CANVAS_WORKSPACE_INVALID')
            if style.get('label_id') is not None:
                style['label_id'] = historical_integer('markup_label', style['label_id'])
            restored_styles[match[1] + ':' + str(historical_integer(match[1], match[2]))] = style
        if 'structural_styles' in workspace:
            workspace['structural_styles'] = restored_styles
        row['workspace_json'] = json.dumps(workspace, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    reviews = {str(item['data']['id']): item['data'] for item in records.get('review', [])}
    image_hashes = {}
    asset_hashes = {}
    asset_by_digest = {}
    digest_by_buffer = {}
    for name, data in assets.items():
        digest = digest_by_buffer.get(id(data))
        if digest is None:
            digest = hashlib.sha256(data).hexdigest()
            digest_by_buffer[id(data)] = digest
        asset_hashes[name] = digest
        asset_by_digest.setdefault(digest, name)
    for item in records.get('checkpoint', []):
        source = (item['data'].get('snapshot') or {}).get('native_source', {})
        for ref in source.get('asset_refs', []):
            path, digest = str(ref.get('asset_path') or ''), str(ref.get('asset_hash') or '')
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or not path.startswith('/api/assets/'):
                _fail('ASSET_INVALID')
            canonical = asset_by_digest.get(digest)
            if canonical is None:
                _fail('ASSET_MISSING')
            leaf = path.removeprefix('/api/assets/')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}\.(png|jpg|webp)', leaf):
                _fail('ASSET_INVALID')
            alias = 'assets/' + leaf
            if alias in assets and assets[alias] != assets[canonical]:
                _fail('ASSET_CONFLICT')
            assets[alias] = assets[canonical]
            asset_hashes[alias] = digest
            image_hashes[path] = digest

    def same_image(actual, original):
        if actual == original:
            return True
        digest = image_hashes.get(original)
        candidate = asset_hashes.get('assets/' + str(actual).removeprefix('/api/assets/'))
        return bool(digest and candidate == digest)

    def compare(actual, original, kind=None):
        if actual == original:
            return
        if kind and str(native_id(kind, actual)) == str(original):
            return
        _fail('PROJECTION_DIVERGED')

    for item in records.get('review_snapshot', []):
        row = item['data']; projected = row.get('snapshot')
        evidence = projected.get('native_source') if isinstance(projected, dict) else None
        if not isinstance(evidence, dict) or evidence.get('format') != 'breadmaker-native-review-v1':
            continue
        try:
            blob, raw, source = _decode_evidence(evidence['relational_blob_b64'])
        except (KeyError, ValueError, TypeError):
            _fail('EVIDENCE_INVALID')
        if hashlib.sha256(raw).hexdigest() != evidence.get('sha256'):
            _fail('EVIDENCE_INVALID')
        expanded += len(raw)
        if expanded > 192 * 1024 * 1024 or set(source) != {'snapshot', *_GROUPS}:
            _fail('EVIDENCE_INVALID')
        original = source.get('snapshot')
        if not isinstance(original, dict) or any(not isinstance(source.get(group), list) for group in _GROUPS):
            _fail('EVIDENCE_INVALID')
        derived = {'native_source', 'checkpoint_ids', 'checkpoint_update_allowed', 'checkpoint_update_blocker',
                   'chapter_count', 'scene_count', 'block_count', 'marker_count', 'question_count', 'checkpoint_count',
                   'comment_count', 'highlight_count', 'suggestion_count', 'answer_count', 'general_feedback_count'}
        if set(projected) - set(original) - set(_GROUPS) - derived:
            _fail('PROJECTION_DIVERGED')
        _validate_blocks(source['blocks'])
        expected = review_snapshot_content_hash(original['review_id'], original['source_generation'], original['scope_type'],
            original, source['chapters'], source['scenes'], source['blocks'], source['questions'], source['markers'])
        if expected != evidence.get('content_hash') or expected != original.get('content_hash'):
            _fail('HASH_MISMATCH')
        sid = str(row['id']); rid = str(row.get('review_id') or item['ids'].get('review_id'))
        compare(sid, original['id'], 'review_snapshot'); compare(rid, original['review_id'], 'review')
        for key in original:
            if key not in {'id', 'review_id', 'status', 'paused_at', 'closed_at'}:
                compare(projected.get(key), original[key])
        restored = deepcopy(original)
        restored.update(id=sid, review_id=rid)
        review = reviews.get(rid, {})
        if sid in review.get('closed_snapshot_ids', []) or review.get('status') in {'closed', 'archived'}:
            restored['status'] = 'closed'
        elif str(review.get('active_snapshot_id') or '') == sid:
            restored['status'] = 'paused' if review.get('status') == 'paused' else 'open'
        item['data'] = restored
        prefer('review_snapshots', sid, original['id'])
        result.snapshots.add(sid)
        for group in _GROUPS:
            actual = projected.get(group)
            if not isinstance(actual, list) or len(actual) != len(source[group]):
                _fail('PROJECTION_DIVERGED')
            table = 'review_snapshot_' + group
            for current, saved in zip(actual, source[group]):
                allowed_extra = {'scope', 'name', 'description', 'source_chapter_title', 'word_count', 'checkpoint_updated_at'} if group == 'checkpoints' else set()
                if not isinstance(current, dict) or not isinstance(saved, dict) or set(current) - set(saved) - allowed_extra:
                    _fail('PROJECTION_DIVERGED')
                for key, value in saved.items():
                    if key == 'snapshot_id':
                        compare(current.get(key), value, 'review_snapshot')
                    elif key == 'id' and group == 'questions':
                        compare(current.get(key), value, 'review_snapshot_question')
                    elif key == 'image_path':
                        if not same_image(current.get(key), value):
                            _fail('PROJECTION_DIVERGED')
                    else:
                        compare(current.get(key), value, _SOURCE_FIELDS.get(key))
                local = deepcopy(saved)
                local['snapshot_id'] = sid
                old = str(current['id'])
                local['id'] = old
                if group == 'checkpoints':
                    local['checkpoint_id'] = current['checkpoint_id']
                records.setdefault(logical_for_table[table], []).append({'ids': {'project_id': item['ids']['project_id'], 'frozen_id': old}, 'data': local})
                prefer(table, old, saved['id'])
                raw_columns = set(_SOURCE_FIELDS) & set(local) - {'checkpoint_id'}
                result.raw_columns[(table, old)] = raw_columns
                for key in raw_columns:
                    reserve(_SOURCE_FIELDS[key], saved[key])

    nested_fields = {'tab_id': 'document_tab', 'chapter_id': 'chapter', 'scene_id': 'scene',
                     'block_id': 'manuscript_block', 'thread_id': 'comment_thread', 'label_id': 'markup_label'}
    kinds = {'tab': 'document_tab', 'chapter': 'chapter', 'chapters': 'chapter', 'blocks': 'manuscript_block',
             'scenes': 'scene', 'annotations': 'annotation', 'comment_threads': 'comment_thread', 'comment_messages': 'comment_message'}
    for item in records.get('checkpoint', []):
        row = item['data']; projected = row.get('snapshot')
        evidence = projected.get('native_source') if isinstance(projected, dict) else None
        if not isinstance(evidence, dict) or evidence.get('format') != 'breadmaker-native-checkpoint-v1':
            continue
        try:
            blob, raw, source = _decode_evidence(evidence['snapshot_blob_b64'])
        except (KeyError, ValueError, TypeError):
            _fail('EVIDENCE_INVALID')
        if hashlib.sha256(raw).hexdigest() != evidence.get('snapshot_hash') or evidence['snapshot_hash'] != row.get('snapshot_hash'):
            _fail('HASH_MISMATCH')
        expanded += len(raw)
        if expanded > 192 * 1024 * 1024:
            _fail('EVIDENCE_INVALID')
        _validate_blocks(source['blocks'])
        if set(projected) != set(source) | {'native_source'}:
            _fail('PROJECTION_DIVERGED')
        for key, value in source.items():
            if key not in kinds:
                compare(projected.get(key), value)
                continue
            saved = [value] if key in ('tab', 'chapter') else value
            actual = [projected[key]] if key in ('tab', 'chapter') else projected[key]
            if not isinstance(saved, list) or not isinstance(actual, list) or len(saved) != len(actual):
                _fail('PROJECTION_DIVERGED')
            for old, current in zip(saved, actual):
                if set(old) != set(current):
                    _fail('PROJECTION_DIVERGED')
                for column, cell in old.items():
                    kind = kinds[key] if column == 'id' else nested_fields.get(column)
                    if column == 'image_path':
                        if not same_image(current[column], cell):
                            _fail('PROJECTION_DIVERGED')
                    elif key == 'annotations' and column == 'id' and str(current[column]).startswith('eann_'):
                        pass  # Packed annotation display ID is not a frozen link target.
                    else:
                        compare(current[column], cell, kind)
                    if kind:
                        reserve(kind, cell)
        row.pop('snapshot')
        row['snapshot_blob'] = {'$bytes': base64.b64encode(blob).decode('ascii')}
        old = str(row['id'])
        raw_columns = set()
        for column, kind in {**_SOURCE_FIELDS, 'current_source_chapter_id': 'chapter'}.items():
            if column in row and row[column] is not None:
                restored = native_id(kind, row[column])
                if restored is None or not restored.isdecimal():
                    _fail('SOURCE_REFERENCE_INVALID')
                row[column] = int(restored); raw_columns.add(column); reserve(kind, row[column])
        result.raw_columns[('checkpoints', old)] = raw_columns

    # The native anchor FK targets frozen source_block_id, not its row PK.
    native_sids = result.snapshots
    for item in records.get('review_anchor', []):
        row = item['data']
        if str(row.get('snapshot_id') or item['ids'].get('snapshot_id')) not in native_sids:
            continue
        for key in ('start_block_id', 'end_block_id'):
            restored = native_id('manuscript_block', row.get(key))
            if restored is None:
                _fail('ANCHOR_REFERENCE_INVALID')
            row[key] = restored
        result.raw_columns[('review_anchors', str(row['id']))] = {'start_block_id', 'end_block_id'}

    for logical, items in records.items():
        for item in items:
            row = item['data']
            if logical == 'review_questionnaire_assignment':
                # Hosted paths use the selected Questionnaire ID under each
                # Review; native join rows have globally unique independent PKs.
                rid = str(row.get('review_id') or item['ids'].get('review_id') or '')
                qid = str(row.get('questionnaire_id') or item['ids'].get('questionnaire_id') or '')
                old = 'review_questionnaire_' + hashlib.sha256((rid + '/' + qid).encode()).hexdigest()[:32]
                row['id'] = old
                item['ids'] = {'project_id': item['ids']['project_id'], 'relation_id': old}
            elif logical == 'review' and 'native_rounds' in row:
                for key in ('native_rounds', 'active_snapshot_id', 'closed_snapshot_ids'):
                    row.pop(key, None)
                if row.get('status') == 'active':
                    row['status'] = 'open'
            elif logical == 'review_marker_assignment':
                alias = row.pop('marker_label_id', None)
                if alias is not None and alias != row.get('marker_id'):
                    _fail('MARKER_ALIAS_CONFLICT')
            elif logical == 'review_event':
                original_json = row.pop('native_payload_json', None)
                if original_json is not None:
                    try:
                        original = json.loads(original_json)
                        current = json.loads(row.get('payload_json') or '{}')
                    except (TypeError, ValueError):
                        _fail('EVENT_INVALID')
                    if not isinstance(original, dict) or not isinstance(current, dict) or set(original) != set(current):
                        _fail('EVENT_INVALID')
                    for key, value in original.items():
                        kind = {'snapshot_id': 'review_snapshot', 'participant_id': 'review_participant',
                                'checkpoint_id': 'checkpoint'}.get(key)
                        if key == 'checkpoint_ids':
                            if not isinstance(value, list) or not isinstance(current[key], list) or len(value) != len(current[key]):
                                _fail('EVENT_INVALID')
                            for actual, expected in zip(current[key], value):
                                compare(actual, expected, 'checkpoint')
                        else:
                            compare(current[key], value, kind)
                    row['payload_json'] = original_json
                target = {'snapshot': 'review_snapshot', 'participant': 'review_participant',
                          'checkpoint': 'checkpoint', 'comment': 'review_comment', 'highlight': 'review_highlight',
                          'suggestion': 'review_suggestion', 'questionnaire': 'questionnaire', 'review': 'review'}.get(row.get('entity_type'))
                if target and row.get('entity_id'):
                    restored = native_id(target, row['entity_id'])
                    if restored is not None:
                        row['entity_id'] = restored
                if 'native_created_at' in row:
                    row['created_at'] = row.pop('native_created_at')
    return result
