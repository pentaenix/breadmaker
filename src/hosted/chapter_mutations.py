from __future__ import annotations

"""M8B atomic chapter replacement over authoritative Firestore/GCS.

No process-global lock, cache, filesystem or background worker participates in commit.
Stage immutable blobs first; validate *all* reads before *any* metadata writes.
The receipt, block set and one project-generation advance share a transaction.
"""

import hashlib
import json
from typing import Any, Mapping

from hosted.domain_store import _ID_FIELDS, _MANAGED_MUTATION_FIELDS, _check_encrypted_block_metadata, _public, new_entity_id, operation_id
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.repositories import _canonical_payload, _fingerprint, _operation_id, render_model_path
from hosted.idempotency import stable_entity_id

# Leave capacity for membership, chapter, receipt and project metadata. Large
# chapters may be read within the JSON budget, but at most 150 records can change
# per operation; large structural rewrites still require segmented publication.
MAX_REPLACEMENT_BLOCKS = 150
MAX_CHAPTER_BLOCKS = 10000
MAX_REPLACEMENT_JSON_BYTES = 8 * 1024 * 1024
MAX_REPLACEMENT_DOCUMENT_BYTES = 512 * 1024
MAX_REPLACEMENT_METADATA_BYTES = 2 * 1024 * 1024


def replace_chapter_blocks(domain, *, project_id: str, chapter_id: str, user_id: str,
                           blocks: list[Mapping[str, Any]], expected_revision: Any = None,
                           operation: str | None = None) -> dict[str, Any]:
    pid = require_project_id(project_id)
    cid = require_opaque_id(chapter_id, label='chapter_id')
    uid = require_user_id(user_id)
    if not isinstance(blocks, list) or len(blocks) > MAX_CHAPTER_BLOCKS:
        raise HostedValidationError('HOSTED_CHAPTER_REPLACE_TOO_LARGE')
    if any(not isinstance(row, Mapping) for row in blocks):
        raise HostedValidationError('HOSTED_CHAPTER_BLOCK_INVALID')
    op = _operation_id(operation or operation_id('chapter_replace'))
    model = domain._model('manuscript_block')
    chapter_model = domain._model('chapter')
    chapter_path = render_model_path(chapter_model, project_id=pid, chapter_id=cid)
    collection_path = ('projects', pid, 'blocks')
    receipt_path = ('projects', pid, 'mutation_receipts', hashlib.sha256(op.encode('ascii')).hexdigest()[:40])
    existing = domain.list('manuscript_block', {'project_id':pid}, user_id=uid, field='chapter_id', value=cid)
    if len(existing) > MAX_CHAPTER_BLOCKS:
        raise HostedValidationError('HOSTED_CHAPTER_REPLACE_TOO_LARGE')
    originals = {str(row['id']):row for row in existing}
    expected_project_generation = domain.project_generation(pid, uid)
    if expected_revision is not None:
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HostedValidationError('HOSTED_CHAPTER_EXPECTED_REVISION_INVALID') from exc
        if expected != expected_project_generation:
            # In-flight retry of a previously committed request is handled by
            # the durable receipt *inside* the transaction below.
            if operation is None:
                raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
    else:
        expected = expected_project_generation
    stamp = domain.clock.now_iso()

    normalized: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for ordinal, incoming in enumerate(blocks):
        raw = dict(incoming)
        provided = str(raw.get('id') or raw.get('block_id') or '')
        if provided and raw.get('id') and raw.get('block_id') and str(raw['id']) != str(raw['block_id']):
            raise HostedValidationError('HOSTED_CHAPTER_BLOCK_ID_CONFLICT')
        eid = require_opaque_id(provided or stable_entity_id(_ID_FIELDS['manuscript_block'][1], f'{op}_{ordinal}'), label='block_id')
        if eid in seen:
            raise HostedValidationError('HOSTED_CHAPTER_BLOCK_DUPLICATE')
        seen.add(eid)
        if raw.get('chapter_id') and str(raw['chapter_id']) != cid:
            raise HostedValidationError('HOSTED_CHAPTER_BLOCK_FOREIGN_CHAPTER')
        entity_expected = raw.pop('generation', None)
        # Identity and ordering are server-selected, never caller-managed.
        if any(key in raw for key in _MANAGED_MUTATION_FIELDS) or any(str(key).startswith('_bm_') for key in raw):
            raise HostedValidationError('HOSTED_CHAPTER_MANAGED_FIELD_RESERVED')
        raw['id'] = eid
        raw['block_id'] = eid
        raw['chapter_id'] = cid
        raw['position'] = ordinal
        public = _canonical_payload(raw)
        if entity_expected is not None:
            try:entity_expected = int(entity_expected)
            except (TypeError, ValueError, OverflowError) as exc:
                raise HostedValidationError('HOSTED_GENERATION_INVALID') from exc
            if entity_expected < 1:
                raise HostedValidationError('HOSTED_GENERATION_INVALID')
        normalized.append((eid,public,{'expected_generation':entity_expected}))

    logical = {'chapter_id':cid,'blocks':[row for _,row,_ in normalized],
               'expected_revision':expected_revision, 'entity_generations':[meta['expected_generation'] for _,_,meta in normalized]}
    canonical = json.dumps(logical,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    if len(canonical) > MAX_REPLACEMENT_JSON_BYTES:
        raise HostedValidationError('HOSTED_CHAPTER_REPLACE_TOO_LARGE')
    # Keep the SAME bounded write budget. A large chapter with one edited
    # paragraph is not a 2,000-record replacement. Never republish unchanged
    # immutable bodies or rewrite their Firestore metadata.
    unchanged=({eid for eid,public,_ in normalized if eid in originals
        and all(originals[eid].get(key)==value for key,value in public.items())}
        if domain.encrypted_objects is None and domain.encrypted_fields is None else set())
    if len(normalized)-len(unchanged)+len(set(originals)-seen)>MAX_REPLACEMENT_BLOCKS:
        raise HostedValidationError('HOSTED_CHAPTER_REPLACE_TOO_LARGE')
    digest = hashlib.sha256(canonical).hexdigest()
    # Staging publication may leave unreferenced immutable objects on a failed
    # CAS, but no malformed/stale operation can write authoritative pointers.
    prepared = []
    metadata_size = 0
    resolved={}
    for eid, public, meta in normalized:
        ids = {'project_id':pid,'block_id':eid}
        old = originals.get(eid)
        if old is not None:
            if meta['expected_generation'] is not None and int(old.get('generation') or 0) != meta['expected_generation']:
                # Do not reject the replay here; transaction receipt is checked
                # before reading/updating any block.
                if operation is None:
                    raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
            merged = {**old,**public}
        else:
            merged = public
        resolved[eid]=merged
        if eid in unchanged:
            prepared.append((eid,render_model_path(model,**ids),dict(old),meta['expected_generation']))
            continue
        hybrid = domain._hybrid_payload('manuscript_block', merged)
        if domain.encrypted_objects is not None and hybrid is None:
            raise HostedValidationError('HOSTED_ENCRYPTED_BLOCK_PAYLOAD_REQUIRED')
        if hybrid is not None:
            # Split the structured Firestore fields from the serialized GCS body.
            # Both are required for legacy publication and opt-in encryption.
            structured, encoded = hybrid
            pointer = domain._publish_hybrid('manuscript_block', ids, encoded, user_id=uid, operation=hashlib.sha256(f'{op}\0{eid}'.encode('utf-8')).hexdigest())
            stored = _canonical_payload({**{
                k:v for k,v in structured.items() if k not in _MANAGED_MUTATION_FIELDS and not k.startswith('_bm_')
            },'payload_object':pointer})
        else:
            stored = dict(public)
        if domain.encrypted_objects is not None:
            _check_encrypted_block_metadata(stored)
        row_bytes = len(json.dumps(stored,ensure_ascii=False,sort_keys=True).encode('utf-8'))
        if row_bytes > MAX_REPLACEMENT_DOCUMENT_BYTES:
            raise HostedValidationError('HOSTED_CHAPTER_BLOCK_TOO_LARGE')
        metadata_size += row_bytes
        if metadata_size > MAX_REPLACEMENT_METADATA_BYTES:
            raise HostedValidationError('HOSTED_CHAPTER_REPLACE_TOO_LARGE')
        prepared.append((eid,render_model_path(model, **ids), stored, meta['expected_generation']))

    if domain.encrypted_objects is not None:
        # Ciphertext-pointers are idempotently stable per operation and block.
        # Do not persist an unkeyed digest of the full plaintext chapter in
        # Firestore receipts when the encrypted block boundary is selected.
        digest = _fingerprint({'chapter_id':cid, 'prepared':[(eid, stored) for eid,_,stored,_ in prepared],
                               'expected_revision':expected_revision})

    def mutate(tx):
        ids = {'project_id':pid,'block_id':prepared[0][0] if prepared else 'b_placeholder'}
        project_path, project = domain._authorize_and_project(tx, model, ids, uid, mutation=True)
        if tx.get(chapter_path) is None:
            raise DocumentNotFound('HOSTED_CHAPTER_NOT_FOUND')
        receipt = tx.get(receipt_path)
        if receipt is not None:
            data = dict(receipt.data)
            if data.get('actor') != uid or data.get('chapter_id') != cid or data.get('digest') != digest:
                raise DocumentConflict('HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD')
            if int(project.get('generation') or 0) != int(data['generation']):
                raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
            current = tx.query_collection(collection_path, field='chapter_id',value=cid)
            if {str(row.data.get('id')) for row in current} != set(data['ids']):
                raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
            changed=set(data.get('changed_ids',data['ids']))
            for row in current:
                if str(row.data.get('id')) in changed and row.data.get('_bm_last_operation') != op:
                    raise DocumentConflict('HOSTED_REPLAY_SUPERSEDED')
            return (int(data['generation']), [dict(row.data) for row in current])
        # These reads must happen before *any* transaction writes (Firestore rule).
        current = tx.query_collection(collection_path, field='chapter_id',value=cid)
        now = {str(row.data.get('id')):row for row in current}
        chosen_paths = {path for _eid,path,_stored,_gen in prepared}
        foreign = []
        for eid, path, _stored, _gen in prepared:
            if eid not in now:
                row = tx.get(path)
                if row is not None:
                    foreign.append(eid)
        if foreign:
            raise DocumentConflict('HOSTED_CHAPTER_BLOCK_FOREIGN_CHAPTER')
        if int(project.get('generation') or 0) != expected:
            raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        for eid,path,stored,entity_gen in prepared:
            previous=now.get(eid)
            if previous is not None and entity_gen is not None and int(previous.data.get('generation') or 0)!=entity_gen:
                raise DocumentConflict('HOSTED_GENERATION_CONFLICT')
        # All authorization, conflicts, reads and size bounds precede writes.
        result=[]
        for eid,path,stored,_gen in prepared:
            previous=now.get(eid)
            if eid in unchanged:
                result.append(dict(previous.data))
                continue
            if previous is None:
                row={**stored,'generation':1,'created_at':stamp,'updated_at':stamp,
                     '_bm_create_operation':op,'_bm_create_fingerprint':_fingerprint(stored),
                     '_bm_last_operation':op}
                tx.create(path,row)
            else:
                row={**dict(previous.data),**stored,'generation':int(previous.data.get('generation') or 0)+1,
                     'updated_at':stamp,'_bm_last_operation':op,'_bm_last_fingerprint':_fingerprint(stored)}
                # Removing old GCS pointers is not necessary; orphan GC is M9.
                if 'payload_object' not in stored:
                    row.pop('payload_object',None)
                tx.set(path,row)
            result.append(row)
        for eid, old in now.items():
            if old.path not in chosen_paths:
                tx.delete(old.path)
        next_generation=int(project['generation'])+1
        # Project generation and receipt commit atomically with all block paths.
        project['generation']=next_generation
        project['updated_at']=stamp
        tx.set(project_path,project)
        tx.create(receipt_path,{'actor':uid,'chapter_id':cid,'digest':digest,
                                'ids':[eid for eid,*_ in prepared],
                                'changed_ids':[eid for eid,*_ in prepared if eid not in unchanged],
                                'generation':next_generation,'created_at':stamp})
        return (next_generation,result)

    generation, result = domain.documents.run_transaction(mutate)
    # Existing payloads were authorized/verified in domain.list; new immutable
    # objects were verified during staging. The successful generation CAS binds
    # those bodies to these exact committed pointers. Do not download them again.
    if domain.encrypted_objects is not None or domain.encrypted_fields is not None:
        hydrated=[domain._hydrate('manuscript_block',row,
            ids={'project_id':pid,'block_id':str(row['block_id'])},user_id=uid) for row in result]
    else:
        hydrated = [{**_public(row),**{field:resolved[str(row['id'])][field]
            for field in ('content_html','text_content') if field in resolved[str(row['id'])]}} for row in result]
    by_id = {str(row['id']):row for row in hydrated}
    ordered = [by_id[eid] for eid,_data,_gen in normalized]
    return {'ok':True,'ids':[eid for eid,_data,_gen in normalized],
            'blocks':[ _public(row) for row in ordered ],'revision':str(generation),'generation':generation}
