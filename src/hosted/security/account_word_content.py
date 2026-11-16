"""Bounded, owner-authorized current block reader for an explicit backfill.

Existing injected clients only. Reads no journal/history/cache, writes nothing,
and does not enable private codecs or invent a decryption capability.
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
CONTENT_FIELDS = ['generation', 'block_id', 'text_content', 'payload_object',
                  'object_name', 'object_generation', 'object_size', 'object_sha256',
                  '_bm_private_fields']


def _remaining(deadline, cap=2):
    left = deadline - time.monotonic()
    if not 0 < left <= 30: raise BackfillUnavailable('WORD_BACKFILL_DEADLINE')
    return min(cap, left)


class BoundedCurrentBlockReader:
    def __init__(self, store, objects):
        if type(store) is not BoundedAccountWordStore or type(objects) is not GoogleCloudObjectStore:
            raise BackfillUnavailable('WORD_BACKFILL_BOUNDED_READER_REQUIRED')
        self.store, self.objects = store, objects

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
            # The metadata adapter deliberately cannot read manuscript bodies.
            # Only this separately authorized fixed current-block path can.
            row = tx._ref(('projects', pid, 'blocks', bid)).get(field_paths=CONTENT_FIELDS,
                transaction=tx._transaction, retry=None, timeout=_remaining(deadline))
            _remaining(deadline)
            value = row.to_dict() if row.exists else None
            if (type(value) is not dict or type(value.get('generation')) is not int
                    or value['generation'] != expected_generation
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
                blob.reload(if_generation_match=generation, retry=None, timeout=_remaining(deadline))
                if type(blob.size) is not int or blob.size != size or int(blob.generation) != generation:
                    raise ValueError()
                # One range GET, including an excess byte. No unbounded stream
                # or automatic transport retry. Hash is from canonical metadata.
                encoded = blob.download_as_bytes(start=0, end=size, if_generation_match=generation,
                    raw_download=True, checksum=None, single_shot_download=True, retry=None,
                    timeout=_remaining(deadline, 4))
                _remaining(deadline)
                if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
                    raise ValueError()
                body = _json_gunzip(encoded, max_decoded_bytes=MAX_OBJECT_BYTES)
                if not set(body) <= {'text_content', 'content_html'}: raise ValueError()
                text = body.get('text_content')
            except Exception:
                raise BackfillUnavailable('WORD_BACKFILL_OBJECT_UNAVAILABLE') from None
        else:
            text = raw.get('text_content')
        if (type(text) is not str or len(text) > max_text_chars
                or len(text.encode('utf-8')) > max_text_bytes):
            raise BackfillUnavailable('WORD_BACKFILL_TEXT_BUDGET')
        _remaining(deadline)
        return dict(generation=expected_generation, text_content=text)
