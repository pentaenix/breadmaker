"""Bounded, owner-authorized current block reader for an explicit backfill.

Existing injected clients only. Reads no journal/history/cache, writes nothing,
and does not enable private codecs or invent a decryption capability.

Pointer metadata is capped before SDK decoding; GCS mode never selects the
Firestore text field. Inline text has its own read in the same authorized
transaction. Each RPC still reserves1MiB in the conservative invocation budget;
that counter is an allocation, not a measurement of accepted/wire bytes.
"""
import hashlib
import time
from hosted.domain_store import _json_gunzip
from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.security.saved_word_identity import verify_owner, _stamp
from hosted.security.account_word_backfill_store import BoundedAccountWordStore, BackfillUnavailable
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.keys import require_hybrid_entity_object_key

MAX_OBJECT_BYTES = 8 * 1024 * 1024
METADATA_MAX_BYTES = 32 * 1024
METADATA_FIELDS = ['generation', 'block_id', 'payload_object',
                   'object_name', 'object_generation', 'object_size', 'object_sha256',
                   '_bm_private_fields']
INLINE_FIELDS = ['generation', 'block_id', 'text_content']


def _remaining(deadline, cap=2):
    left = deadline - time.monotonic()
    if not 0 < left <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
    return min(cap, left)


class BoundedCurrentBlockReader:
    def __init__(self, store, objects):
        if type(store) is not BoundedAccountWordStore or type(objects) is not GoogleCloudObjectStore:
            raise BackfillUnavailable('WORD_BACKFILL_BOUNDED_READER_REQUIRED')
        self.store, self.objects = store, objects

    def _block(self, tx, pid, bid, fields, maximum, deadline):
        from google.cloud.firestore_v1 import types
        from google.cloud.firestore_v1._helpers import decode_dict
        name = self.store.database+'/documents/projects/'+pid+'/blocks/'+bid
        tx.charge_read(content_bytes=1024*1024)
        stream = self.store.client._firestore_api.batch_get_documents(request=dict(
            database=self.store.database, documents=[name], mask=types.DocumentMask(field_paths=fields),
            transaction=tx._transaction.id), metadata=self.store.client._rpc_metadata,
            retry=None, timeout=_remaining(deadline))
        found = None
        try:
            for row in stream:
                _remaining(deadline)
                if (found is not None or row._pb.ByteSize()>maximum
                        or row._pb.WhichOneof('result')!='found' or row.found.name!=name
                        or set(row.found.fields)-set(fields)):
                    raise BackfillUnavailable('WORD_BACKFILL_BLOCK_RESPONSE')
                raw=row.found.fields
                # Private envelopes are not a fallback text source. Reject them
                # before any decode, GCS access or credentials/codec discovery.
                if '_bm_private_fields' in raw:
                    raise BackfillUnavailable('WORD_BACKFILL_PRIVATE_READER_REQUIRED')
                for key,value in raw.items():
                    kind=value._pb.WhichOneof('value_type')
                    if key=='payload_object':
                        if kind=='null_value':continue
                        if kind!='map_value':raise BackfillUnavailable('WORD_BACKFILL_BLOCK_RESPONSE')
                        pointer=value.map_value.fields
                        if set(pointer)-{'object_name','object_generation','object_size','object_sha256','encryption_format','encryption_operation'}:
                            raise BackfillUnavailable('WORD_BACKFILL_OBJECT_POINTER_INVALID')
                        for field,item in pointer.items():self._primitive(field,item)
                    else:self._primitive(key,value)
                found=decode_dict(raw,self.store.client)
        finally:
            close=getattr(stream,'close',None) or getattr(stream,'cancel',None)
            if close:close()
        if found is None:raise BackfillUnavailable('WORD_BACKFILL_BLOCK_CHANGED')
        return found

    @staticmethod
    def _primitive(key,value):
        kind=value._pb.WhichOneof('value_type')
        if key in {'generation','object_generation','object_size'}:
            if kind!='integer_value' or not 1<=value.integer_value<=2**53-1:
                raise BackfillUnavailable('WORD_BACKFILL_BLOCK_RESPONSE')
        else:
            limits={'block_id':160,'object_name':1024,'object_sha256':64,
                    'encryption_format':64,'encryption_operation':160,'text_content':1_000_000}
            if key not in limits or kind!='string_value' or len(value.string_value)>limits[key]:
                raise BackfillUnavailable('WORD_BACKFILL_BLOCK_RESPONSE')

    def __call__(self, *, user_id, project_id, block_id, expected_generation,
                 deadline, max_text_chars, max_text_bytes):
        uid, pid = require_user_id(user_id), require_project_id(project_id)
        bid = require_opaque_id(block_id, label='block_id')
        if (uid != self.store.owner or type(expected_generation) is not int
                or not 1 <= expected_generation <= 2**53-1
                or type(max_text_chars) is not int or not 0 < max_text_chars <= 1_000_000
                or type(max_text_bytes) is not int or not 0 < max_text_bytes <= 4_000_000):
            raise BackfillUnavailable('WORD_BACKFILL_READER_SCOPE')
        def read(tx):
            profile = tx.get(('users', uid))
            if (profile is None or profile.data.get('status') != 'active'
                    or tx.get(('account_erasure_locks', uid)) is not None):
                raise BackfillUnavailable('WORD_BACKFILL_ACCOUNT_UNAVAILABLE')
            root = tx.get(('projects', pid))
            if root is None or root.data.get('status') != 'active':
                raise BackfillUnavailable('WORD_BACKFILL_PROJECT_UNAVAILABLE')
            _stamp(root.data.get('created_at'))
            owners = tx.saved_word_owners(pid, timeout=_remaining(deadline))
            verify_owner(project_id=pid, owner_rows=[(r.path, dict(r.data)) for r in owners],
                         hosted_user=dict(profile.data))
            # Read pointer metadata first, with a raw protobuf cap before SDK
            # recursive decode. GCS-backed blocks never request Firestore text.
            value = self._block(tx, pid, bid, METADATA_FIELDS, METADATA_MAX_BYTES, deadline)
            if value.get('generation') != expected_generation or value.get('block_id', bid) != bid:
                raise BackfillUnavailable('WORD_BACKFILL_BLOCK_CHANGED')
            if value.get('payload_object') is None and 'object_name' not in value:
                value = self._block(tx, pid, bid, INLINE_FIELDS, 1024*1024, deadline)
            if (type(value.get('generation')) is not int or value['generation'] != expected_generation
                    or value.get('block_id', bid) != bid):
                raise BackfillUnavailable('WORD_BACKFILL_BLOCK_CHANGED')
            return value
        raw = self.store.run_transaction(read, deadline=deadline)
        # These private sources require a separately verified bounded codec.
        if '_bm_private_fields' in raw:
            raise BackfillUnavailable('WORD_BACKFILL_PRIVATE_READER_REQUIRED')
        pointer = raw.get('payload_object')
        if pointer is None and 'object_name' in raw:
            pointer = {k: raw.get(k) for k in ('object_name', 'object_generation', 'object_size', 'object_sha256')}
        if pointer is not None:
            if type(pointer) is not dict or 'encryption_format' in pointer:
                raise BackfillUnavailable('WORD_BACKFILL_PRIVATE_READER_REQUIRED')
            if set(pointer) != {'object_name', 'object_generation', 'object_size', 'object_sha256'}:
                raise BackfillUnavailable('WORD_BACKFILL_OBJECT_POINTER_INVALID')
            generation, size, digest = (pointer[k] for k in ('object_generation', 'object_size', 'object_sha256'))
            if (type(generation) is not int or generation < 1 or type(size) is not int
                    or not 0 < size <= MAX_OBJECT_BYTES or type(digest) is not str
                    or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
                raise BackfillUnavailable('WORD_BACKFILL_OBJECT_POINTER_INVALID')
            try:
                key = pointer['object_name']
                if require_hybrid_entity_object_key('manuscript_block',
                        dict(project_id=pid, block_id=bid), key) != digest:
                    raise ValueError()
                blob = self.objects._bucket.blob(key)
                if self.store.budget is not None:self.store.budget.reserve(rpcs=1)
                blob.reload(if_generation_match=generation, retry=None, timeout=_remaining(deadline))
                if type(blob.size) is not int or blob.size != size or int(blob.generation) != generation:
                    raise ValueError()
                # One range GET, including an excess byte. No unbounded stream
                # or automatic transport retry. Hash is from canonical metadata.
                if self.store.budget is not None:self.store.budget.reserve(rpcs=1,download_bytes=size)
                encoded = blob.download_as_bytes(start=0, end=size, if_generation_match=generation,
                    raw_download=True, checksum=None, single_shot_download=True, retry=None,
                    timeout=_remaining(deadline, 4))
                _remaining(deadline)
                if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
                    raise ValueError()
                body = _json_gunzip(encoded, max_decoded_bytes=MAX_OBJECT_BYTES)
                if not set(body) <= {'text_content', 'content_html'}: raise ValueError()
                text = body.get('text_content')
            except BackfillUnavailable:raise
            except Exception:
                raise BackfillUnavailable('WORD_BACKFILL_OBJECT_UNAVAILABLE') from None
        else:
            text = raw.get('text_content')
        if (type(text) is not str or len(text) > max_text_chars
                or len(text.encode('utf-8')) > max_text_bytes):
            raise BackfillUnavailable('WORD_BACKFILL_TEXT_BUDGET')
        _remaining(deadline)
        return dict(generation=expected_generation, text_content=text)
