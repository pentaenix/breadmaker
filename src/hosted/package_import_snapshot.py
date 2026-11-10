"""Pure canonical-plan snapshot transport, not a user-facing archive format.

Object authority, generation, encoded length and digest are checked by the
caller. This codec validates the pinned decoded representation independently.
"""
from __future__ import annotations

import gzip
import json
import zlib

from hosted.firestore.contracts import HostedValidationError

TARGET_SNAPSHOT_BYTES = 16 * 1024**2
MAX_PLAN_BYTES = 256 * 1024**2
# A snapshot adds binding/range fields and list delimiters to existing batches.
# Leave bounded headroom without tightening the existing single-batch boundary.
MAX_WRAPPER_BYTES = 1024**2
MAX_DECODED_BYTES = MAX_PLAN_BYTES + MAX_WRAPPER_BYTES
_BINDING_KEYS = {'job_id', 'actor', 'project_id', 'source_sha256', 'operation'}
_WRAPPER_KEYS = _BINDING_KEYS | {'v', 'index', 'first_batch', 'batches'}


def _invalid():
    return HostedValidationError('HOSTED_IMPORT_SNAPSHOT_INVALID')


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _invalid() from None


def _json_value(value):
    # Never silently convert tuples, non-string keys, or other Python objects.
    if type(value) is dict:
        return all(type(k) is str and _json_value(v) for k, v in value.items())
    if type(value) is list:
        return all(_json_value(v) for v in value)
    return value is None or type(value) in (str, int, float, bool)


def _validate_batches(batches, max_batch_items=64):
    if type(batches) is not list or not batches:
        raise _invalid()
    try:
        for batch in batches:
            if (type(batch) is not list or not 0 < len(batch) <= max_batch_items
                    or any(type(item) is not dict for item in batch) or not _json_value(batch)):
                raise _invalid()
    except RecursionError:
        raise _invalid() from None


def _validate_binding(binding):
    if (type(binding) is not dict or set(binding) != _BINDING_KEYS
            or any(type(value) is not str or not value for value in binding.values())):
        raise HostedValidationError('HOSTED_IMPORT_SNAPSHOT_BINDING_INVALID')


def _validate_range(index, first_batch):
    if type(index) is not int or index < 0 or type(first_batch) is not int or first_batch < 0:
        raise _invalid()


def group_batches(batches):
    """Group whole existing batches, allowing one valid batch above the target."""
    if type(batches) is not list:
        raise _invalid()
    if not batches:
        return []
    _validate_batches(batches)
    groups, current, first, size = [], [], 0, 2
    for index, batch in enumerate(batches):
        batch_size = len(_json(batch))
        if batch_size > MAX_PLAN_BYTES:
            raise HostedValidationError('HOSTED_IMPORT_SNAPSHOT_TOO_LARGE')
        if current and size + 1 + batch_size > TARGET_SNAPSHOT_BYTES:
            groups.append((first, current))
            current, first, size = [], index, 2
        size += batch_size + bool(current)
        current.append(batch)
    groups.append((first, current))
    return groups


def _validate_original_sizes(binding, first_batch, batches):
    for offset, batch in enumerate(batches):
        original = {'v': 1, **binding, 'index': first_batch + offset, 'items': batch}
        if len(_json(original)) > MAX_PLAN_BYTES:
            raise HostedValidationError('HOSTED_IMPORT_SNAPSHOT_TOO_LARGE')


def encode_snapshot(binding, index, first_batch, batches):
    _validate_binding(binding)
    _validate_range(index, first_batch)
    _validate_batches(batches)
    _validate_original_sizes(binding, first_batch, batches)
    source = _json({'v': 1, **binding, 'index': index, 'first_batch': first_batch, 'batches': batches})
    if len(source) > MAX_DECODED_BYTES:
        raise HostedValidationError('HOSTED_IMPORT_SNAPSHOT_TOO_LARGE')
    return gzip.compress(source, mtime=0), len(source)


def decode_snapshot(encoded, *, binding, index, first_batch, batch_count,
                    decoded_bytes, max_batch_items=64):
    _validate_binding(binding)
    _validate_range(index, first_batch)
    if (type(batch_count) is not int or batch_count <= 0
            or type(max_batch_items) is not int or not 0 < max_batch_items <= 64
            or type(decoded_bytes) is not int or not 0 < decoded_bytes <= MAX_DECODED_BYTES
            or type(encoded) is not bytes):
        raise _invalid()
    try:
        decoder = zlib.decompressobj(wbits=31)
        source = decoder.decompress(encoded, decoded_bytes + 1)
        # A single complete gzip member only: no concatenation, padding or tail.
        if (len(source) != decoded_bytes or not decoder.eof
                or decoder.unused_data or decoder.unconsumed_tail):
            raise _invalid()
        value = json.loads(source.decode('utf-8'))
    except (zlib.error, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    if (type(value) is not dict or set(value) != _WRAPPER_KEYS
            or type(value['v']) is not int or value['v'] != 1
            or type(value['index']) is not int or value['index'] != index
            or type(value['first_batch']) is not int or value['first_batch'] != first_batch):
        raise _invalid()
    if any(type(value[key]) is not str or value[key] != binding[key] for key in _BINDING_KEYS):
        raise HostedValidationError('HOSTED_IMPORT_SNAPSHOT_BINDING_INVALID')
    batches = value['batches']
    _validate_batches(batches, max_batch_items)
    if len(batches) != batch_count or _json(value) != source:
        raise _invalid()
    _validate_original_sizes(binding, first_batch, batches)
    return batches
