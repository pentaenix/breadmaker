"""Atomic, recoverable edits to an existing hosted manuscript.

Stage immutable text first and publish all metadata in one authorized CAS. The
existing structural journal remains bounded; larger imports fail before content
publication and can still use the resumable new-project import path.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
import json

from hosted.domain_store import _ID_FIELDS
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.repositories import _canonical_payload, _fingerprint, render_model_path
from hosted.idempotency import stable_entity_id

MAX_CHANGED_RECORDS = 95
MAX_METADATA_BYTES = 300_000
_LIMIT = 'This import is too large to safely change an existing project. Import it as a new project instead; your current manuscript has not changed.'


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode())


def install_existing_document(domain, project_id, user_id, tabs, *, operation,
                              replace_main=False, after_document_id='', confirmed=False):
    pid, uid = require_project_id(project_id), require_user_id(user_id)
    if domain.encrypted_fields is not None or domain.encrypted_objects is not None:
        raise HostedValidationError('HOSTED_PRIVATE_BATCH_NOT_READY')
    generation = domain.project_generation(pid, uid)
    stamp = domain.clock.now_iso()
    model = domain._model('document_tab')
    parents = {'project_id': pid, 'tab_id': 'listing_placeholder'}
    receipt_path = ('projects', pid, 'mutation_receipts', hashlib.sha256(operation.encode()).hexdigest()[:40])
    digest = _fingerprint({'tabs': tabs, 'replace_main': replace_main, 'after': after_document_id, 'confirmed': confirmed})

    def inspect(tx):
        _, root = domain._authorize_and_project(tx, model, parents, uid, mutation=True)
        receipt = tx.get(receipt_path)
        if receipt:
            if receipt.data['digest'] != digest or receipt.data['actor'] != uid:
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
            if receipt.data['generation'] != root['generation']:
                raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            return None, None, dict(receipt.data['result'])
        if root['generation'] != generation:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        existing = sorted(tx.query_collection(('projects', pid, 'tabs')),
                          key=lambda row: (float(row.data.get('position') or 0), row.path[-1]))
        main = next((row for row in existing if row.data.get('is_manuscript') or row.data.get('kind') == 'manuscript'), None)
        if main is None:
            raise DocumentNotFound('HOSTED_MAIN_MANUSCRIPT_NOT_FOUND')
        removed = []
        if replace_main:
            removed += tx.query_collection(('projects', pid, 'chapters'), field='tab_id', value=main.path[-1])
            removed += tx.query_collection(('projects', pid, 'blocks'), field='tab_id', value=main.path[-1])
            chapter_ids = {row.path[-1] for row in removed if row.path[-2] == 'chapters'}
            # Scene metadata belongs to the replaced chapters. Other tabs and
            # linked planning/review records are deliberately not rewritten.
            removed += [row for row in tx.query_collection(('projects', pid, 'scenes'))
                        if str(row.data.get('tab_id') or '') == main.path[-1]
                        or str(row.data.get('chapter_id') or '') in chapter_ids]
        if len(removed) > MAX_CHANGED_RECORDS or _size([row.data for row in removed]) > MAX_METADATA_BYTES:
            raise HostedValidationError(_LIMIT)
        return existing, removed, None

    existing, removed, replay = domain.documents.run_transaction(inspect)
    if replay is not None:
        return replay
    main = next(row for row in existing if row.data.get('is_manuscript') or row.data.get('kind') == 'manuscript')
    if replace_main and not confirmed:
        # Read only the bounded old blocks to require explicit replacement consent
        # for actual prose/images, rather than relying on browser readiness state.
        for row in removed:
            if row.path[-2] != 'blocks':
                continue
            data = domain._hydrate('manuscript_block', row.data,
                ids={'project_id': pid, 'block_id': row.path[-1]}, user_id=uid)
            if str(data.get('text_content') or '').strip() or data.get('image_path'):
                raise DocumentConflict('Confirm replacement of the existing manuscript before importing.')
    prepared, document_ids, chapters = [], [], 0
    positions = {row.path[-1]: i for i, row in enumerate(existing)}
    if not replace_main and after_document_id and after_document_id not in positions:
        raise DocumentNotFound('HOSTED_IMPORT_DESTINATION_NOT_FOUND')
    insert_at = positions.get(after_document_id, len(existing)-1) + 1

    def add(entity, data):
        eid = stable_entity_id(_ID_FIELDS[entity][1], f'{operation}_{len(prepared)}')
        ids = domain._ids_with_entity(entity, {'project_id': pid}, eid)
        public = _canonical_payload(domain._payload_with_identity(entity, data, eid))
        stored = {**public, 'generation': 1, 'created_at': stamp, 'updated_at': stamp,
                  '_bm_create_operation': operation, '_bm_create_fingerprint': _fingerprint(public)}
        prepared.append((entity, ids, render_model_path(domain._model(entity), **ids), stored))
        if len(prepared) + len(removed) > MAX_CHANGED_RECORDS:
            raise HostedValidationError(_LIMIT)
        return eid

    for tpos, tab in enumerate(tabs):
        if replace_main:
            tid = main.path[-1]
        else:
            tid = add('document_tab', {'title': str(tab.get('title') or 'Imported document')[:240],
                'kind': 'document', 'is_manuscript': False, 'position': insert_at + tpos})
        if tid not in document_ids:
            document_ids.append(tid)
        for cpos, chapter in enumerate(tab.get('chapters') or []):
            cid = add('chapter', {'tab_id': tid, 'title': str(chapter.get('title') or 'Chapter')[:240],
                                 'position': chapters if replace_main else cpos})
            chapters += 1
            for bpos, block in enumerate(chapter.get('blocks') or [{'text': '', 'html': ''}]):
                if block.get('kind') == 'image' or block.get('image_path'):
                    raise HostedValidationError('Import documents with images as a new project. Existing-project image import is not available yet.')
                add('manuscript_block', {'tab_id': tid, 'chapter_id': cid, 'position': bpos,
                    'block_type': 'paragraph', 'semantic_style': block.get('style') or 'body',
                    'scene_id': None, 'text_content': block.get('text') or '', 'content_html': block.get('html') or ''})
    if not prepared:
        raise HostedValidationError('HOSTED_IMPORT_DOCUMENT_EMPTY')
    target_positions = {row.path[-1]: index + (len(tabs) if index >= insert_at else 0)
                        for index, row in enumerate(existing)}
    shifted = [] if replace_main else [row for row in existing
        if float(row.data.get('position') or 0) != target_positions[row.path[-1]]]
    if len(prepared) + len(removed) + len(shifted) > MAX_CHANGED_RECORDS:
        raise HostedValidationError(_LIMIT)
    # Bound text before publishing objects, then bound the actual journal metadata.
    if _size([row[3] for row in prepared]) > 8*1024*1024:
        raise HostedValidationError(_LIMIT)

    def stage(item):
        entity, ids, path, stored = item
        hybrid = domain._hybrid_payload(entity, stored)
        if hybrid is not None:
            metadata, encoded = hybrid
            pointer = domain._publish_hybrid(entity, ids, encoded, user_id=uid,
                operation=hashlib.sha256(f'{operation}:{ids["block_id"]}'.encode()).hexdigest())
            stored = {**metadata, 'payload_object': pointer}
        return entity, ids, path, stored

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(copy_context().run, stage, item) for item in prepared]
        prepared = [future.result() for future in futures]
    if _size([row[3] for row in prepared]+[row.data for row in removed]+[row.data for row in shifted]*2) > MAX_METADATA_BYTES:
        raise HostedValidationError(_LIMIT)
    result = {'ok': True, 'document_ids': document_ids, 'first_document_id': document_ids[0],
              'tabs': len(document_ids), 'chapters': chapters, 'images': 0}

    def commit(tx):
        root_path, root = domain._authorize_and_project(tx, model, parents, uid, mutation=True)
        receipt = tx.get(receipt_path)
        if receipt:
            if receipt.data['digest'] != digest or receipt.data['actor'] != uid or receipt.data['generation'] != root['generation']:
                raise DocumentConflict('HOSTED_IMPORT_REQUEST_CHANGED')
            return dict(receipt.data['result'])
        if root['generation'] != generation:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        for entity, ids, path, stored in prepared:
            domain.entities._authorize(tx, domain._model(entity), ids, uid, mutation=True)
            if tx.get(path) is not None:
                raise DocumentConflict('HOSTED_IMPORT_ENTITY_EXISTS')
        for row in removed + shifted:
            current = tx.get(row.path)
            if current is None or dict(current.data) != dict(row.data):
                raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        domain._advance_project(tx, root_path, root, expected_generation=generation, stamp=stamp)
        for row in removed:
            tx.delete(row.path)
        for row in shifted:
            tx.set(row.path, {**row.data, 'position': target_positions[row.path[-1]],
                'generation': int(row.data.get('generation') or 0)+1, 'updated_at': stamp})
        for _, _, path, stored in prepared:
            tx.create(path, stored)
        tx.create(receipt_path, {'actor': uid, 'digest': digest, 'generation': generation+1, 'result': result})
        return result
    return domain.documents.run_transaction(commit)
