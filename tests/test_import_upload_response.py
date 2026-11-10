"""Provider-response reuse must not remove independent publication integrity."""
import hashlib
from types import SimpleNamespace

import pytest

from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.storage.publication import ImmutableObjectPublisher
from hosted.storage.contracts import ObjectIntegrityError, ObjectAlreadyExists

KEY = 'projects/' + 'a' * 32 + '/block-payloads/b_1/body.json.gz'
BODY = b'synthetic paragraph'


class Blob:
    def __init__(self, response=None, downloaded=BODY):
        self.name = KEY
        self.generation = None
        self.size = None
        self.metadata = None
        self.content_type = 'application/octet-stream'
        self.response = response or {}
        self.downloaded = downloaded
        self.reloads = 0
        self.reads = 0

    def upload_from_file(self, source, **kwargs):
        assert source.read() == BODY
        assert kwargs['if_generation_match'] == 0
        assert kwargs['checksum'] == 'auto'
        # Model the SDK replacing, not merging, local request properties.
        self.generation = self.response.get('generation', 7)
        self.size = self.response.get('size', len(BODY))
        self.metadata = self.response.get('metadata', {'breadmaker-sha256': hashlib.sha256(BODY).hexdigest()})

    def reload(self, **kwargs):
        self.reloads += 1
        if self.generation:
            assert kwargs == {'if_generation_match': self.generation}
        else:
            assert kwargs == {}
        self.generation = 7
        self.size = len(BODY)
        self.metadata = {'breadmaker-sha256': hashlib.sha256(BODY).hexdigest()}

    def download_as_bytes(self, **kwargs):
        assert kwargs['if_generation_match'] == 7
        assert kwargs['end'] == len(BODY)
        self.reads += 1
        return self.downloaded


def store(blob):
    return GoogleCloudObjectStore(bucket='synthetic', client=SimpleNamespace(
        bucket=lambda _: SimpleNamespace(blob=lambda _: blob)))


def test_complete_upload_response_needs_no_metadata_get_but_verifies_bytes():
    blob = Blob()
    result = ImmutableObjectPublisher(store(blob)).publish_bytes(KEY, BODY, max_bytes=100)
    assert result.object.sha256 == hashlib.sha256(BODY).hexdigest()
    assert blob.reloads == 0 and blob.reads == 1


@pytest.mark.parametrize('response', [{'size': 1}, {'metadata': {}},
    {'metadata': {'breadmaker-sha256': '0' * 64}}])
def test_mismatched_response_fails_without_reloading_away_evidence(response):
    blob = Blob(response)
    with pytest.raises(ObjectIntegrityError):
        ImmutableObjectPublisher(store(blob)).publish_bytes(KEY, BODY, max_bytes=100)
    assert blob.reloads == 0 and blob.reads == 0


@pytest.mark.parametrize('response', [{'generation': None}, {'size': None}])
def test_incomplete_response_retains_metadata_fallback(response):
    blob = Blob(response)
    ImmutableObjectPublisher(store(blob)).publish_bytes(KEY, BODY, max_bytes=100)
    assert blob.reloads == 1 and blob.reads == 1


def test_correct_metadata_cannot_hide_corrupted_media():
    blob = Blob(downloaded=b'x' * len(BODY))
    with pytest.raises(ObjectIntegrityError):
        ImmutableObjectPublisher(store(blob)).publish_bytes(KEY, BODY, max_bytes=100)
    assert blob.reloads == 0 and blob.reads == 1


def test_invalid_generation_fails_closed():
    blob = Blob({'generation': -1})
    # Existing create-only adapter semantics translate generation conflicts.
    with pytest.raises(ObjectAlreadyExists):
        store(blob).write_chunks(KEY, [BODY], max_bytes=100,
            content_type='application/octet-stream', if_generation_match=0)
    assert blob.reloads == 0 and blob.reads == 0


def test_size_fallback_cannot_change_generation():
    class ChangedGeneration(Blob):
        def reload(self, **kwargs):
            super().reload(**kwargs)
            self.generation = 8
    blob = ChangedGeneration({'size': None})
    with pytest.raises(ObjectIntegrityError):
        store(blob).write_chunks(KEY, [BODY], max_bytes=100,
            content_type='application/octet-stream', if_generation_match=0)
    assert blob.reads == 0


@pytest.mark.parametrize('response', [{'generation': 'invalid'}, {'size': 'invalid'}])
def test_malformed_response_fails_closed(response):
    blob = Blob(response)
    with pytest.raises(ValueError):
        store(blob).write_chunks(KEY, [BODY], max_bytes=100,
            content_type='application/octet-stream', if_generation_match=0)
    assert blob.reloads == 0 and blob.reads == 0


def test_declared_sha_mismatch_rejected_before_upload():
    blob = Blob()
    with pytest.raises(ObjectIntegrityError):
        store(blob).write_chunks(KEY, [BODY], max_bytes=100,
            content_type='application/octet-stream', if_generation_match=0,
            metadata={'breadmaker-sha256': '0' * 64})
    assert blob.generation is None and blob.reads == 0
