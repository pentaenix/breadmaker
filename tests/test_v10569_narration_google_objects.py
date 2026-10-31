"""Fake SDK validates exact Google request arguments; never acquires ADC."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from hosted.narration.gc import FENCE_METADATA, is_fence
from hosted.narration.google_objects import GoogleNarrationObjects, MAX_BYTES
from hosted.storage.contracts import ObjectAlreadyExists, ObjectNotFound, ObjectLimitExceeded
from hosted.storage.memory import MemoryObjectStore

PROJECT = 'breadmaker-prod-511902'
NAME = 'projects/' + 'a' * 32 + '/narration/' + 'b' * 32 + '/000.bin'


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.metadata, self.generation = {}, None
    def reload(self, **kwargs):
        self.bucket.calls.append(('reload', kwargs))
        item = self.bucket.memory.stat(self.name)
        if item is None:
            raise ObjectNotFound('Synthetic absent')
        for key, value in asdict(item).items(): setattr(self, key, value)
    def upload_from_string(self, payload, **kwargs):
        self.bucket.calls.append(('upload', kwargs))
        item = self.bucket.memory.write_chunks(self.name, [payload], max_bytes=MAX_BYTES,
            content_type=kwargs['content_type'], if_generation_match=kwargs['if_generation_match'], metadata=self.metadata)
        self.generation = item.generation
    def download_as_bytes(self, **kwargs):
        self.bucket.calls.append(('read', kwargs))
        item = self.bucket.memory.stat(self.name)
        data = b''.join(self.bucket.memory.read_chunks(self.name, max_bytes=item.size, if_generation_match=kwargs['if_generation_match']))
        return data[:kwargs['end'] + 1]


class Bucket:
    def __init__(self):
        self.memory, self.calls = MemoryObjectStore(), []
        self.versioning_enabled, self.retention_period, self.lifecycle_rules = False, None, []
        self.soft_delete_policy = SimpleNamespace(retention_duration_seconds=0)
        self.iam_configuration = SimpleNamespace(public_access_prevention='enforced', uniform_bucket_level_access_enabled=True)
    def blob(self, name): return Blob(self, name)
    def reload(self, **kwargs): self.calls.append(('bucket', kwargs))


class Client:
    def __init__(self): self.value, self.calls = Bucket(), []
    def bucket(self, name):
        assert name == PROJECT + '-breadmaker-narration'
        return self.value
    def list_blobs(self, bucket, **kwargs):
        self.calls.append(kwargs)
        rows = self.value.memory.list_prefix(kwargs['prefix'])
        selected = [row for row in rows if row.name >= kwargs['start_offset']][:kwargs['max_results']]
        return [SimpleNamespace(**asdict(row)) for row in selected]


@pytest.fixture
def env():
    client = Client()
    return GoogleNarrationObjects(bucket=PROJECT + '-breadmaker-narration', project=PROJECT, client=client), client


def write(objects, payload=b'x' * 32, *, generation=0, metadata=None):
    return objects.write_chunks(NAME, [payload], max_bytes=len(payload),
        content_type='application/octet-stream', if_generation_match=generation, metadata=metadata)


def test_provider_forbids_other_buckets_and_every_non_narration_path(env):
    objects, client = env
    for suffix in ('content', 'assets', 'recovery', 'models'):
        with pytest.raises(ValueError, match='BUCKET_SCOPE'):
            GoogleNarrationObjects(bucket=PROJECT + '-breadmaker-' + suffix, project=PROJECT, client=client)
    for path in ('../private', NAME.replace('/narration/', '/assets/'), NAME.replace('/000.bin', '/016.bin')):
        with pytest.raises(ValueError, match='OBJECT_SCOPE'):
            objects.stat(path)
    assert not client.value.calls


def test_writes_are_bounded_single_request_create_only_and_read_back_generation(env):
    objects, client = env
    original = write(objects)
    upload, reload = client.value.calls
    assert upload[1] == dict(content_type='application/octet-stream', if_generation_match=0,
                            checksum='auto', timeout=(3, 15), retry=None)
    assert reload[1] == dict(if_generation_match=original.generation, timeout=10, retry=None)
    with pytest.raises(ObjectAlreadyExists): write(objects)
    with pytest.raises(ValueError, match='REPLACEMENT_FORBIDDEN'):
        write(objects, generation=original.generation)


def test_gc_empty_marker_is_atomic_generation_replacement_not_delete_then_create(env):
    objects, client = env
    original = write(objects)
    result = write(objects, b'', generation=original.generation, metadata=FENCE_METADATA)
    assert result.generation != original.generation and is_fence(result)
    with pytest.raises(ObjectAlreadyExists): write(objects)
    with pytest.raises(ValueError, match='MUST_NOT_BE_DELETED'):
        objects.delete(NAME, if_generation_match=result.generation)
    assert is_fence(objects.stat(NAME))


def test_size_rejection_precedes_provider_io_and_no_unmarked_empty_object(env):
    objects, client = env
    with pytest.raises(ObjectLimitExceeded):
        objects.write_chunks(NAME, [b'x' * 100], max_bytes=50,
            content_type='application/octet-stream', if_generation_match=0)
    with pytest.raises(ValueError, match='REPLACEMENT_FORBIDDEN'): write(objects, b'')
    with pytest.raises(ValueError, match='WRITE_INVALID'):
        objects.write_chunks(NAME, [], max_bytes=MAX_BYTES + 1,
            content_type='application/octet-stream', if_generation_match=0)
    assert not client.value.calls


def test_reads_use_generation_range_and_no_decompression_or_unbounded_retries(env):
    objects, client = env
    original = write(objects)
    assert b''.join(objects.read_chunks(NAME, max_bytes=32, if_generation_match=original.generation)) == b'x' * 32
    assert client.value.calls[-1] == ('read', dict(if_generation_match=original.generation,
        start=0, end=32, timeout=(3, 15), retry=None, checksum=None, raw_download=True))
    with pytest.raises(ObjectLimitExceeded):
        objects.read_chunks(NAME, max_bytes=10, if_generation_match=original.generation)
    with pytest.raises(ValueError, match='READ_INVALID'):
        objects.read_chunks(NAME, max_bytes=32)


def test_gc_requires_actual_private_unversioned_unretained_no_expiry_bucket(env):
    objects, client = env
    objects.verify_gc_bucket()
    assert client.value.calls == [('bucket', dict(timeout=10, retry=None))]
    changes = [(client.value, 'versioning_enabled', True), (client.value, 'retention_period', 1),
               (client.value, 'lifecycle_rules', [{'action': {'type': 'Delete'}}]),
               (client.value.soft_delete_policy, 'retention_duration_seconds', 604800),
               (client.value.iam_configuration, 'public_access_prevention', 'inherited'),
               (client.value.iam_configuration, 'uniform_bucket_level_access_enabled', False)]
    for target, field, value in changes:
        original = getattr(target, field); setattr(target, field, value)
        with pytest.raises(ValueError, match='BUCKET_PROTECTIONS_CHANGED'): objects.verify_gc_bucket()
        setattr(target, field, original)


def test_whole_bucket_inventory_is_bounded_and_does_not_hide_unknown_prefixes(env):
    objects, client = env
    write(objects)
    client.value.memory.write_chunks('unknown/synthetic', [b'x'], max_bytes=1,
        content_type='application/octet-stream', if_generation_match=0)
    first, cursor = objects.inventory_page(limit=1)
    assert len(first) == 1 and cursor == NAME
    second, cursor = objects.inventory_page(after=cursor, limit=1)
    assert [item.name for item in second] == ['unknown/synthetic'] and cursor == ''
    assert all(call['prefix'] == '' and call['max_results'] == 3 and call['retry'] is None for call in client.calls)
    with pytest.raises(ValueError, match='BOUNDED_INVENTORY'):
        objects.list_prefix('projects/')
