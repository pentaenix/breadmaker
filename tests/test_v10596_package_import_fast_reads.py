"""Package authority wrappers preserve the existing bounded media-read path."""
import hashlib

import pytest

from hosted.account_lifecycle import AccountLifecycleObjectStore
from hosted.firestore.contracts import DocumentConflict
from hosted.package_import import _GuardedObjects
from hosted.storage.contracts import ObjectIntegrityError
from hosted.storage.memory import MemoryObjectStore
from hosted.storage.publication import ImmutableObjectPublisher


KEY = 'projects/' + 'a' * 32 + '/block-payloads/b_synthetic/body.json.gz'
BODY = b'Synthetic paragraph, no provider credentials.'


class FastStore(MemoryObjectStore):
    def __init__(self):
        super().__init__()
        self.fast = []
        self.streams = self.stats = 0
        self.fail = self.corrupt = False
        self.after_read = lambda: None

    def read_verified_bytes(self, name, **kwargs):
        self.fast.append((name, kwargs))
        if self.fail:
            raise RuntimeError('Synthetic fast-read failure')
        data = b''.join(super().read_chunks(name, max_bytes=kwargs['expected_size'],
                                          if_generation_match=kwargs['if_generation_match']))
        self.after_read()
        return b'corrupt' if self.corrupt else data

    def read_chunks(self, *args, **kwargs):
        self.streams += 1
        return super().read_chunks(*args, **kwargs)

    def stat(self, name):
        self.stats += 1
        return super().stat(name)


def test_small_package_publication_uses_fast_read_with_exact_pointer_and_both_guards():
    store = FastStore();checks = []
    guarded = _GuardedObjects(store, lambda: checks.append(True))
    saved = ImmutableObjectPublisher(guarded).publish_bytes(KEY, BODY, max_bytes=1024)
    assert store.fast == [(KEY, {'if_generation_match': saved.object.generation,
        'expected_size': len(BODY), 'expected_sha256': hashlib.sha256(BODY).hexdigest()})]
    assert len(checks) == 4  # before/after write, before/after verified media read
    assert store.streams == store.stats == 0
    # An unrelated method must not expose an unguarded provider operation.
    assert getattr(guarded, 'delete', None) is None


def test_fast_read_passes_through_existing_account_lifecycle_wrapper():
    store = FastStore()
    saved = ImmutableObjectPublisher(store).publish_bytes(KEY, BODY, max_bytes=1024)
    checks = []
    # The lifecycle facade delegates reads today; the package facade must keep
    # its own fresh before/after guards instead of exposing that bound method.
    lifecycle = AccountLifecycleObjectStore(store, None)
    guarded = _GuardedObjects(lifecycle, lambda: checks.append(True))
    assert guarded.read_verified_bytes(KEY, if_generation_match=saved.object.generation,
        expected_size=len(BODY), expected_sha256=saved.object.sha256) == BODY
    assert checks == [True, True]


def test_missing_optional_provider_capability_keeps_guarded_streaming_fallback():
    store = MemoryObjectStore();checks = []
    guarded = _GuardedObjects(store, lambda: checks.append(True))
    assert getattr(guarded, 'read_verified_bytes', None) is None
    saved = ImmutableObjectPublisher(guarded).publish_bytes(KEY, BODY, max_bytes=1024)
    assert saved.object.sha256 == hashlib.sha256(BODY).hexdigest()
    assert checks == [True, True, True, True]


@pytest.mark.parametrize('fail,corrupt', [(True, False), (False, True)])
def test_fast_read_failure_or_bad_bytes_never_fall_back(fail, corrupt):
    store = FastStore();store.fail = fail;store.corrupt = corrupt
    guarded = _GuardedObjects(store, lambda: None)
    expected = RuntimeError if fail else ObjectIntegrityError
    with pytest.raises(expected, match='fast-read failure|HASH_MISMATCH'):
        ImmutableObjectPublisher(guarded).publish_bytes(KEY, BODY, max_bytes=1024)
    assert len(store.fast) == 1 and store.streams == 0


def test_fast_read_rechecks_current_authority_before_returning_bytes():
    store = FastStore()
    saved = ImmutableObjectPublisher(store).publish_bytes(KEY, BODY, max_bytes=1024)
    permitted = True
    def check():
        if not permitted:
            raise DocumentConflict('HOSTED_ACCOUNT_ERASURE_PENDING')
    def revoke():
        nonlocal permitted
        permitted = False
    guarded = _GuardedObjects(store, check)
    store.after_read = revoke
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        guarded.read_verified_bytes(KEY, if_generation_match=saved.object.generation,
            expected_size=len(BODY), expected_sha256=saved.object.sha256)
    calls = len(store.fast)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        guarded.read_verified_bytes(KEY, if_generation_match=saved.object.generation,
            expected_size=len(BODY), expected_sha256=saved.object.sha256)
    assert len(store.fast) == calls  # rejection before any next provider read


@pytest.mark.parametrize('body', [b'', b'x' * (2 * 1024 * 1024 + 1)])
def test_fast_capability_does_not_expand_existing_small_read_limit(body):
    store = FastStore();checks = []
    saved = ImmutableObjectPublisher(_GuardedObjects(store, lambda: checks.append(True))).publish_bytes(
        KEY, body, max_bytes=len(body))
    assert saved.object.size == len(body)
    assert store.fast == [] and store.streams == 1 and len(checks) == 4
