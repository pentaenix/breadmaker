"""Small synthetic fixtures for the internal canonical-plan transport."""
import gzip
import json

import pytest

from hosted.firestore.contracts import HostedValidationError
from hosted import package_import_snapshot as codec


BINDING = {'job_id': 'job', 'actor': 'actor', 'project_id': 'project',
           'source_sha256': 'a' * 64, 'operation': 'op'}
BATCHES = [[{'kind': 'record', 'data': {'text': '日本語 café 🌾', 'nested': [None, True, 2, 1.5]}}],
           [{'kind': 'asset', 'data': 'AAEC/w=='}]]


def decode(encoded, size, **changes):
    args = dict(binding=BINDING, index=2, first_batch=7, batch_count=2, decoded_bytes=size)
    args.update(changes)
    return codec.decode_snapshot(encoded, **args)


def test_roundtrip_preserves_payload_and_stable_canonical_encoding():
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    assert codec.encode_snapshot(BINDING, 2, 7, BATCHES) == (encoded, size)
    assert encoded[4:8] == bytes(4)
    assert decode(encoded, size) == BATCHES
    raw = gzip.decompress(encoded)
    assert size == len(raw)
    assert raw == json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()


def test_grouping_preserves_whole_batches_and_allows_single_large_batch(monkeypatch):
    batches = [[{'text': 'a'}], [{'text': 'b'}], [{'text': 'x' * 100}], [{'text': 'c'}]]
    monkeypatch.setattr(codec, 'TARGET_SNAPSHOT_BYTES', 35)
    groups = codec.group_batches(batches)
    assert [first for first, _ in groups] == [0, 2, 3]
    assert [batch for _, group in groups for batch in group] == batches
    assert groups[0][1][0] is batches[0]
    assert codec.group_batches([]) == []


@pytest.mark.parametrize('changes', [
    {'binding': {**BINDING, 'actor': 'other'}}, {'batch_count': 1}, {'index': 3},
    {'first_batch': 8}, {'index': True}, {'batch_count': True}, {'decoded_bytes': 0},
    {'max_batch_items': 65}, {'binding': {**BINDING, 'extra': 'no'}},
])
def test_rejects_wrong_pinned_metadata(changes):
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_'):
        decode(encoded, size, **changes)


@pytest.mark.parametrize('damage', [
    lambda data: data[:-1], lambda data: data + b'tail',
    lambda data: data + gzip.compress(b'', mtime=0),
    lambda data: data[:-8] + bytes([data[-8] ^ 1]) + data[-7:],
    lambda data: b'not gzip',
])
def test_rejects_corrupt_truncated_or_extra_gzip(damage):
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(damage(encoded), size)


def test_decompression_is_bounded_by_exact_pin_plus_one(monkeypatch):
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    factory = codec.zlib.decompressobj
    limits = []

    class Spy:
        def __init__(self, **kwargs):
            self.inner = factory(**kwargs)

        def decompress(self, data, limit):
            limits.append(limit)
            return self.inner.decompress(data, limit)

        def __getattr__(self, key):
            return getattr(self.inner, key)

    monkeypatch.setattr(codec.zlib, 'decompressobj', Spy)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(encoded, size, decoded_bytes=20)
    assert limits == [21]
    with pytest.raises(HostedValidationError):
        decode(encoded, size + 1)


@pytest.mark.parametrize('change', [
    {'v': True}, {'v': 2}, {'index': True}, {'first_batch': True}, {'extra': 'no'},
    {'batches': []}, {'batches': [[], []]}, {'batches': [[{}] * 65, [{}]]},
    {'batches': [[1], [{}]]}, {'batches': 'bad'}, {'actor': 1},
])
def test_rejects_malformed_wrapper(change):
    value = {'v': 1, **BINDING, 'index': 2, 'first_batch': 7, 'batches': BATCHES, **change}
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_'):
        decode(gzip.compress(raw, mtime=0), len(raw))


@pytest.mark.parametrize('raw', [b'{}', b'[]', b'{', b'\xff', b'NaN'])
def test_rejects_invalid_json(raw):
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(gzip.compress(raw, mtime=0), len(raw))


def test_rejects_noncanonical_and_duplicate_json_keys():
    encoded, _ = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    raw = gzip.decompress(encoded)
    for changed in (raw + b' ', b'{"v":1,' + raw[1:]):
        with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
            decode(gzip.compress(changed, mtime=0), len(changed))


@pytest.mark.parametrize('batches', [[[]], [[{}] * 65], [[{'tuple': (1, 2)}]],
                                   [[{1: 'key'}]], [[{'float': float('nan')}]]])
def test_encode_and_group_reject_lossy_or_invalid_payloads(batches):
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_'):
        codec.group_batches(batches)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_'):
        codec.encode_snapshot(BINDING, 2, 7, batches)


def test_preserves_original_batch_size_boundary_with_snapshot_headroom(monkeypatch):
    batch = [[{'text': 'x' * 20}]]
    original = codec._json({'v': 1, **BINDING, 'index': 7, 'items': batch[0]})
    monkeypatch.setattr(codec, 'MAX_PLAN_BYTES', len(original))
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, batch)
    assert size > len(original)
    assert decode(encoded, size, batch_count=1) == batch
    monkeypatch.setattr(codec, 'MAX_PLAN_BYTES', len(original) - 1)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_TOO_LARGE'):
        codec.encode_snapshot(BINDING, 2, 7, batch)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_TOO_LARGE'):
        decode(encoded, size, batch_count=1)


def test_total_decoded_limit_applies_to_encoder_and_decoder(monkeypatch):
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    monkeypatch.setattr(codec, 'MAX_DECODED_BYTES', size - 1)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_TOO_LARGE'):
        codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(encoded, size)


def test_group_rejects_overlarge_existing_batch(monkeypatch):
    monkeypatch.setattr(codec, 'MAX_PLAN_BYTES', 10)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_TOO_LARGE'):
        codec.group_batches(BATCHES)


def test_missing_wrapper_field_and_custom_item_limit():
    encoded, _ = codec.encode_snapshot(BINDING, 2, 7, BATCHES)
    value = json.loads(gzip.decompress(encoded))
    del value['operation']
    raw = codec._json(value)
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(gzip.compress(raw, mtime=0), len(raw))
    encoded, size = codec.encode_snapshot(BINDING, 2, 7, [[{}, {}], [{}]])
    with pytest.raises(HostedValidationError, match='HOSTED_IMPORT_SNAPSHOT_INVALID'):
        decode(encoded, size, max_batch_items=1)
