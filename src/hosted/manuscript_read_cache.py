"""Explicit owner-operated acceleration of legacy paragraphs; never a migration.

The canonical objects, pointers, user content and project generation are untouched.
This module has no HTTP/startup/scheduler hook: existing durable projects require an
operator-reviewed invocation. Each call is at most 64 records and is resumable.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib

from hosted.domain_store import _json_gzip
from hosted.firestore.contracts import DocumentNotFound, HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.idempotency import stable_entity_id


def build_legacy_read_cache(domain, *, project_id, user_id, block_ids):
    pid,uid=require_project_id(project_id),require_user_id(user_id)
    ids=[require_opaque_id(value,label='block_id') for value in block_ids]
    if not 1<=len(ids)<=64 or len(set(ids))!=len(ids):
        raise HostedValidationError('HOSTED_READ_CACHE_BATCH_INVALID')
    if domain.encrypted_objects is not None or domain.encrypted_fields is not None:
        raise HostedValidationError('HOSTED_PRIVATE_READ_CACHE_NOT_SUPPORTED')
    paths=[('projects',pid,'blocks',value) for value in ids]

    def load(tx):
        domain.projects._require_member(tx,pid,uid,owner=True)
        rows=[tx.get(path) for path in paths]
        if any(row is None for row in rows):
            raise DocumentNotFound('HOSTED_DOCUMENT_NOT_FOUND')
        return rows
    rows=domain.documents.run_transaction(load)
    selected=[]
    for row in rows:
        data=dict(row.data);pointer=data.get('payload_object')
        if (not isinstance(pointer,dict) or not pointer.get('object_sha256')
                or 'bundle_entry' in pointer):
            continue
        if domain._read_pointer(data) is not pointer:
            continue # Already accelerated with the CURRENT canonical hash.
        selected.append(row)
    if not selected:
        return {'requested':len(ids),'cached':0,'skipped':len(ids)}

    def body(row):
        data=dict(row.data);pointer=data['payload_object']
        result=domain._read_hybrid_body(pointer)
        if (set(result)!={'content_html','text_content'}
                or not all(isinstance(value,str) for value in result.values())
                or hashlib.sha256(_json_gzip(result)).hexdigest()!=pointer['object_sha256']):
            raise HostedValidationError('HOSTED_READ_CACHE_SOURCE_INVALID')
        return result
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies=list(pool.map(body,selected))
    encoded=_json_gzip({'entries':{row.path[-1]:value for row,value in zip(selected,bodies)}})
    if len(encoded)>8*1024*1024:
        raise HostedValidationError('HOSTED_READ_CACHE_TOO_LARGE')
    digest=hashlib.sha256(encoded).hexdigest()
    pointer=domain._publish_hybrid('manuscript_block',
        {'project_id':pid,'block_id':stable_entity_id('b_cache_',digest)},encoded)
    verified=domain._read_hybrid_body(pointer)
    if verified!={'entries':{row.path[-1]:value for row,value in zip(selected,bodies)}}:
        raise HostedValidationError('HOSTED_READ_CACHE_VERIFICATION_FAILED')

    def attach(tx):
        domain.projects._require_member(tx,pid,uid,owner=True)
        current=[tx.get(row.path) for row in selected]
        pending=[]
        for original,row in zip(selected,current):
            # A concurrent edit or deletion wins. Never attach an old bundle to
            # changed text, recreate a deleted row, or overwrite newer metadata.
            if row is None or row.data.get('payload_object')!=original.data['payload_object']:
                continue
            data=dict(row.data)
            data['_bm_read_bundle']={**pointer,'bundle_entry':row.path[-1],
                'source_sha256':original.data['payload_object']['object_sha256']}
            pending.append((row.path,data))
        # Provider rule: all authorization/CAS reads precede every write.
        for path,data in pending:tx.set(path,data)
        return len(pending)
    count=domain.documents.run_transaction(attach)
    return {'requested':len(ids),'cached':count,'skipped':len(ids)-count}
