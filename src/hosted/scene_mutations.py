"""Bounded, atomic in-place Scene View saves.

Structural scene rewrites remain fail-closed until their annotation/bookmark and
chapter-order repairs have a single publication contract. Immutable prose is
staged before the Firestore transaction; one project CAS publishes all pointers.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from hosted.chapter_mutations import (MAX_CHAPTER_BLOCKS, MAX_REPLACEMENT_BLOCKS,
    MAX_REPLACEMENT_JSON_BYTES, MAX_REPLACEMENT_DOCUMENT_BYTES, MAX_REPLACEMENT_METADATA_BYTES)
from hosted.domain_store import _MANAGED_MUTATION_FIELDS, _merge_manuscript_patch, operation_id
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.repositories import _canonical_payload, _fingerprint, _operation_id


def save_scene_blocks(domain, *, project_id, scene_id, user_id, blocks,
                      expected_revisions, operation=None):
    pid = require_project_id(project_id)
    sid = require_opaque_id(scene_id, label='scene_id')
    uid = require_user_id(user_id)
    if not isinstance(blocks, list) or not blocks or len(blocks) > MAX_CHAPTER_BLOCKS:
        raise HostedValidationError('HOSTED_SCENE_BLOCKS_REQUIRED')
    if not isinstance(expected_revisions, Mapping) or not expected_revisions:
        raise HostedValidationError('HOSTED_SCENE_EXPECTED_REVISIONS_REQUIRED')
    # Do not introduce a plaintext receipt or silently weaken either private codec.
    if domain.encrypted_objects is not None or domain.encrypted_fields is not None:
        raise HostedValidationError('HOSTED_SCENE_PRIVATE_SAVE_UNAVAILABLE')
    model = domain._model('manuscript_block')
    ids = {'project_id': pid, 'block_id': 'b_placeholder'}
    def authorize(tx):
        return domain._authorize_and_project(tx, model, ids, uid, mutation=True)[1]
    domain.documents.run_transaction(authorize)
    op = _operation_id(operation or operation_id('scene_save'))
    receipt_path = ('projects', pid, 'mutation_receipts', hashlib.sha256(op.encode('ascii')).hexdigest()[:40])
    scene_path = ('projects', pid, 'scenes', sid)
    normalized = []
    seen = set()
    for incoming in blocks:
        if not isinstance(incoming, Mapping):
            raise HostedValidationError('HOSTED_SCENE_BLOCK_INVALID')
        raw = dict(incoming)
        bid = require_opaque_id(str(raw.get('id') or ''), label='block_id')
        if bid in seen:
            raise HostedValidationError('HOSTED_SCENE_BLOCK_DUPLICATE')
        seen.add(bid)
        if any(key in raw for key in _MANAGED_MUTATION_FIELDS) or any(str(key).startswith('_bm_') for key in raw):
            raise HostedValidationError('HOSTED_SCENE_MANAGED_FIELD_RESERVED')
        if raw.get('block_id', bid) != bid or raw.get('scene_id', sid) != sid:
            raise HostedValidationError('HOSTED_SCENE_BLOCK_ID_CONFLICT')
        normalized.append((bid, _canonical_payload(raw)))
    chapters = {str(row.get('chapter_id') or '') for _, row in normalized}
    try:
        revisions = {cid: int(expected_revisions[cid]) for cid in chapters}
        if any(value < 1 or isinstance(expected_revisions[cid], (bool, float)) for cid, value in revisions.items()):
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HostedValidationError('HOSTED_SCENE_EXPECTED_REVISIONS_REQUIRED') from exc
    if len(set(revisions.values())) != 1:
        raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
    expected = next(iter(revisions.values()))
    logical = {'scene_id': sid, 'blocks': normalized, 'expected_revisions': revisions}
    encoded = json.dumps(logical, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(encoded) > MAX_REPLACEMENT_JSON_BYTES:
        raise HostedValidationError('HOSTED_SCENE_SAVE_TOO_LARGE')
    digest = hashlib.sha256(encoded).hexdigest()

    def inspect(tx):
        project_path, project = domain._authorize_and_project(tx, model, ids, uid, mutation=True)
        scene = tx.get(scene_path)
        if scene is None:
            raise DocumentNotFound('HOSTED_SCENE_NOT_FOUND')
        receipt = tx.get(receipt_path)
        if receipt is not None:
            data = receipt.data
            if data.get('actor') != uid or data.get('scene_id') != sid or data.get('digest') != digest:
                raise DocumentConflict('HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD')
            if int(project['generation']) != int(data['generation']):
                raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
            return project_path, project, scene, [], dict(data['result'])
        if int(project['generation']) != expected:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        current = tx.query_collection(('projects', pid, 'blocks'), field='scene_id', value=sid)
        by_id = {str(row.data['id']): row for row in current}
        if set(by_id) != seen:
            raise HostedValidationError('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE')
        chapter_rows = {cid: tx.get(('projects', pid, 'chapters', cid)) for cid in chapters}
        if any(row is None or row.data.get('tab_id') != scene.data.get('tab_id') for row in chapter_rows.values()):
            raise HostedValidationError('HOSTED_SCENE_FOREIGN_CHAPTER')
        for bid, raw in normalized:
            previous = by_id[bid].data
            if (str(previous.get('chapter_id')) != str(raw.get('chapter_id'))
                    or raw.get('tab_id', previous.get('tab_id')) != previous.get('tab_id')
                    or raw.get('position', previous.get('position')) != previous.get('position')):
                raise HostedValidationError('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE')
        chapter_order = {cid: index for index, cid in enumerate(sorted(chapters,
            key=lambda cid: (int(chapter_rows[cid].data.get('position') or 0), cid)))}
        actual = [str(row.data['id']) for row in sorted(current, key=lambda row:
            (chapter_order[str(row.data['chapter_id'])], int(row.data.get('position') or 0), str(row.data['id'])))]
        if [bid for bid, _ in normalized] != actual:
            raise HostedValidationError('HOSTED_SCENE_STRUCTURAL_SAVE_UNAVAILABLE')
        return project_path, project, scene, current, None

    _, _, _, current, replay = domain.documents.run_transaction(inspect)
    if replay is not None:
        return replay
    originals = {str(row.data['id']): row for row in current}
    hydrated = {str(row['id']): row for row in domain.list('manuscript_block', {'project_id': pid}, user_id=uid, field='scene_id', value=sid)}
    if domain.project_generation(pid, uid) != expected or set(hydrated) != seen:
        raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
    changed = [(bid, raw) for bid, raw in normalized if any(hydrated.get(bid, {}).get(key) != value for key, value in raw.items())]
    if len(changed) > MAX_REPLACEMENT_BLOCKS:
        raise HostedValidationError('HOSTED_SCENE_SAVE_TOO_LARGE')
    prepared = {}
    size = 0
    for bid, raw in changed:
        merged = _merge_manuscript_patch(hydrated[bid], raw)
        structured, body = domain._hybrid_payload('manuscript_block', merged)
        pointer = domain._publish_hybrid('manuscript_block', {'project_id': pid, 'block_id': bid}, body,
            user_id=uid, operation=hashlib.sha256(f'{op}\0{bid}'.encode()).hexdigest())
        stored = _canonical_payload({**{key: value for key, value in structured.items()
            if key not in _MANAGED_MUTATION_FIELDS and not key.startswith('_bm_')}, 'payload_object': pointer})
        row_size = len(json.dumps(stored, ensure_ascii=False).encode())
        size += row_size
        if row_size > MAX_REPLACEMENT_DOCUMENT_BYTES or size > MAX_REPLACEMENT_METADATA_BYTES:
            raise HostedValidationError('HOSTED_SCENE_SAVE_TOO_LARGE')
        prepared[bid] = stored
    stamp = domain.clock.now_iso()
    def mutate(tx):
        project_path, project, scene, current, replay = inspect(tx)
        if replay is not None:
            return replay
        # Bind staged bodies to the exact observed metadata, in addition to CAS.
        if any(row.data != originals[str(row.data['id'])].data for row in current):
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        generation = int(project['generation']) + 1
        result = {'ok': True, 'ids': [bid for bid, _ in normalized],
                  'chapter_revisions': {cid: str(generation) for cid in chapters}}
        for row in current:
            bid = str(row.data['id'])
            if bid in prepared:
                stored = prepared[bid]
                tx.set(row.path, {**dict(row.data), **stored, 'generation': int(row.data['generation']) + 1,
                    'updated_at': stamp, '_bm_last_operation': op, '_bm_last_fingerprint': _fingerprint(stored)})
        tx.set(scene_path, {**dict(scene.data), 'updated_at': stamp,
            'generation': int(scene.data.get('generation') or 0) + 1})
        tx.set(project_path, {**project, 'generation': generation, 'updated_at': stamp})
        tx.create(receipt_path, {'actor': uid, 'scene_id': sid, 'digest': digest,
            'generation': generation, 'result': result, 'created_at': stamp})
        return result
    return domain.documents.run_transaction(mutate)
