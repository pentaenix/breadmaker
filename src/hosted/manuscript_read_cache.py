"""Bounded derived read bundles; never authoritative manuscript mutations.

The canonical objects, pointers, user content and project generation are untouched.
The owner-operated legacy backfill and active-editor incremental maintenance share
the same verified, pointer-CAS publication. No detached/background server workers.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib

from hosted.domain_store import _json_gzip
from hosted.firestore.contracts import DocumentNotFound, HostedValidationError, HostedAuthorizationError
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.idempotency import stable_entity_id


def build_legacy_read_cache(domain, *, project_id, user_id, block_ids):
    return _build_read_cache(domain,project_id=project_id,user_id=user_id,block_ids=block_ids,
        owner_only=True,force=False)


def _read_many(tx,paths):
    method=getattr(tx,'get_many',None)
    return method(paths) if method is not None else [tx.get(path) for path in paths]


def _authorize(domain,tx,pid,uid,owner_only):
    _,member=domain.projects._require_member(tx,pid,uid,owner=owner_only)
    if member.get('role') not in {'owner','editor'}:
        raise HostedAuthorizationError('PROJECT_PERMISSION_DENIED')


def _build_read_cache(domain, *, project_id, user_id, block_ids, owner_only, force):
    pid,uid=require_project_id(project_id),require_user_id(user_id)
    ids=[require_opaque_id(value,label='block_id') for value in block_ids]
    if not 1<=len(ids)<=64 or len(set(ids))!=len(ids):
        raise HostedValidationError('HOSTED_READ_CACHE_BATCH_INVALID')
    if domain.encrypted_objects is not None or domain.encrypted_fields is not None:
        raise HostedValidationError('HOSTED_PRIVATE_READ_CACHE_NOT_SUPPORTED')
    paths=[('projects',pid,'blocks',value) for value in ids]

    def load(tx):
        _authorize(domain,tx,pid,uid,owner_only)
        rows=_read_many(tx,paths)
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
        if not force and domain._read_pointer(data) is not pointer:
            continue # Already accelerated with the CURRENT canonical hash.
        selected.append(row)
    if not selected:
        return {'requested':len(ids),'cached':0,'skipped':len(ids)}

    # Reuse verified existing bundles instead of re-reading 64 canonical objects
    # after every one-paragraph edit. A changed pointer falls back to canonical.
    pointers={domain._payload_key(domain._read_pointer(row.data)):domain._read_pointer(row.data) for row in selected}
    with ThreadPoolExecutor(max_workers=8) as pool:
        payloads=dict(zip(pointers,pool.map(domain._read_hybrid_body,pointers.values())))
    def body(row):
        data=dict(row.data);pointer=data['payload_object']
        hydrated=domain._hydrate('manuscript_block',data,payload_cache=payloads)
        result={key:hydrated[key] for key in ('content_html','text_content') if key in hydrated}
        if (set(result)!={'content_html','text_content'}
                or not all(isinstance(value,str) for value in result.values())
                or hashlib.sha256(_json_gzip(result)).hexdigest()!=pointer['object_sha256']):
            raise HostedValidationError('HOSTED_READ_CACHE_SOURCE_INVALID')
        return result
    bodies=[body(row) for row in selected]
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
        _authorize(domain,tx,pid,uid,owner_only)
        current=_read_many(tx,[row.path for row in selected])
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


def _pending_chunks(rows):
    from hosted.domain_store import HostedDomainStore
    rows=sorted(rows,key=lambda row:(int(row.get('position') or 0),str(row.get('created_at') or ''),str(row.get('id') or '')))
    needed=[]
    for offset in range(0,len(rows),64):
        chunk=rows[offset:offset+64];references=set();stale=False
        for row in chunk:
            pointer=HostedDomainStore._read_pointer(row)
            if not isinstance(pointer,dict) or not pointer.get('bundle_entry'):stale=True
            else:references.add(HostedDomainStore._payload_key(pointer))
        if stale or len(references)>2:needed.append(chunk)
    return needed


def pending_read_cache_chapters(rows):
    """Metadata-only hints, collected during an already-authorized content read."""
    from collections import defaultdict
    chapters=defaultdict(list)
    for row in rows:chapters[str(row.get('chapter_id') or '')].append(row)
    return {cid for cid,records in chapters.items() if cid and _pending_chunks(records)}


def refresh_chapter_read_cache(domain, *, project_id, chapter_id, user_id):
    """One bounded active-editor step; retry/resume by recomputing current metadata.

    At most two valid bundle objects per 64-block window is already healthy (new
    import batch boundaries need not align with chapters). Repack only stale or
    fragmented windows, rather than rewriting the whole chapter after an insertion.
    """
    from hosted.action_journal import bind_action
    pid,uid=require_project_id(project_id),require_user_id(user_id)
    cid=require_opaque_id(chapter_id,label='chapter_id')
    if domain.encrypted_objects is not None or domain.encrypted_fields is not None:
        raise HostedValidationError('HOSTED_PRIVATE_READ_CACHE_NOT_SUPPORTED')
    def load(tx):
        _authorize(domain,tx,pid,uid,False)
        if tx.get(('projects',pid,'chapters',cid)) is None:
            raise DocumentNotFound('HOSTED_CHAPTER_NOT_FOUND')
        return tx.query_collection(('projects',pid,'blocks'),field='chapter_id',value=cid)
    rows=domain.documents.run_transaction(load)
    if len(rows)>10000:raise HostedValidationError('HOSTED_READ_CACHE_CHAPTER_TOO_LARGE')
    if any(row.data.get('id')!=row.path[-1] for row in rows):
        raise HostedValidationError('HOSTED_READ_CACHE_BLOCK_ID_INVALID')
    needed=_pending_chunks([dict(row.data) for row in rows])
    if not needed:return {'updated':0,'more':False}
    if any('bundle_entry' in (row.get('payload_object') or {}) for row in needed[0]):
        raise HostedValidationError('HOSTED_READ_CACHE_CANONICAL_BUNDLE_UNSUPPORTED')
    # Derived-cache changes aren't manuscript actions and must not advance the
    # project generation or fill the structural Undo journal. Both authorizations
    # still occur inside the publication transactions; only this cache-only helper
    # runs outside the optional action binding.
    with bind_action(None,pid,uid):
        result=_build_read_cache(domain,project_id=pid,user_id=uid,
            block_ids=[str(row['id']) for row in needed[0]],owner_only=False,force=True)
    return {'updated':result['cached'],'more':len(needed)>1 or result['skipped']>0}
