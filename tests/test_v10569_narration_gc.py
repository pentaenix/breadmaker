"""Disposable encrypted derivatives only. No cloud or owner project access."""
from copy import deepcopy

import pytest

from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.narration.gc import NarrationGC, GRACE, FENCE_BYTES, is_fence, LEASE
from hosted.narration.maintenance import NarrationMaintenanceStore, RECEIPTS
from hosted.narration.recordings import USAGE, ATTEMPTS, ROOT, UNDO_SECONDS, _intent
from hosted.storage.contracts import ObjectAlreadyExists, ObjectGenerationConflict
from tests.test_v10569_narration_recordings import env, begin, publish, UID, PID, wav


def collector(env, *, allowed=True):
    def retention(tx, pid, uid):
        assert (pid, uid) == (PID, UID)
        return allowed
    return NarrationGC(NarrationMaintenanceStore(env[2]), env[3], retention_allows=retention, now=lambda: env[5][0])


def expired_delete(env):
    made, record = publish(env)
    token = env[0].delete(UID, PID, made['id'], expected_generation=1, operation='delete')['undo_token']
    env[5][0] += GRACE + UNDO_SECONDS + 121
    return made, record, token


def usage(env): return env[2].get((USAGE, UID)).data


def test_active_recording_never_age_expires_or_advances_manuscript_generation(env):
    made, record = publish(env)
    env[5][0] += 10 * GRACE
    generation = env[1].project_generation(PID, UID)
    original = deepcopy(usage(env))
    gc = collector(env); lease = gc.acquire()
    assert gc.collect(PID, made['publication'], lease) is False
    assert usage(env) == original and not is_fence(env[3].list_prefix('projects/')[0])
    assert env[0].finalize(UID, PID, made['publication']) == record
    assert env[1].project_generation(PID, UID) == generation


def test_deleted_record_fenced_before_refund_and_collected_only_once(env):
    made, _, token = expired_delete(env)
    generation = env[1].project_generation(PID, UID)
    gc = collector(env); lease = gc.acquire()
    assert gc.collect(PID, made['publication'], lease)
    assert usage(env) == dict(v=1, bytes=FENCE_BYTES, records=0, uploads={})
    assert all(is_fence(item) for item in env[3].list_prefix('projects/'))
    assert env[2].get(_intent(PID, made['publication'])) is None
    receipt = env[2].get((RECEIPTS, PID + '_' + made['publication'])).data
    assert receipt['fenced_objects'] == 1 and receipt['retained_capacity_bytes'] == FENCE_BYTES
    assert (receipt['expires_at'] - receipt['completed_at']).days == 30
    assert not gc.collect(PID, made['publication'], lease)
    assert usage(env)['bytes'] == FENCE_BYTES
    with pytest.raises(DocumentNotFound):
        env[0].restore(UID, PID, made['id'], token)
    assert env[1].project_generation(PID, UID) == generation


def test_undo_grace_and_unsettled_upload_grace_are_preserved(env):
    made, _ = publish(env)
    gc = collector(env); lease = gc.acquire()
    token = env[0].delete(UID, PID, made['id'], expected_generation=1, operation='delete')['undo_token']
    assert not gc.collect(PID, made['publication'], lease)
    assert env[0].restore(UID, PID, made['id'], token)['id'] == made['id']
    assert not is_fence(env[3].list_prefix('projects/')[0])


def test_unwritten_abandoned_upload_refunds_only_after_grace(env):
    made = begin(env)
    env[5][0] += 3600 + GRACE + 1
    gc = collector(env); lease = gc.acquire()
    assert gc.collect(PID, made['publication'], lease)
    assert usage(env) == dict(v=1, bytes=0, records=0, uploads={})
    assert not env[3].list_prefix('projects/')


def test_paused_create_only_writer_cannot_recreate_audio_after_gc(env):
    made = begin(env)
    saved = []
    original = env[0].parts.write
    def pause(ciphertext, header, **kwargs):
        saved.append((ciphertext, header, kwargs))
        raise TimeoutError('Synthetic suspended I/O')
    env[0].parts.write = pause
    with pytest.raises(TimeoutError):
        env[0].upload_part(UID, PID, made['publication'], 0, wav())
    assert not env[3].list_prefix('projects/')
    env[5][0] += 3600 + GRACE + 121
    gc = collector(env); lease = gc.acquire()
    assert gc.collect(PID, made['publication'], lease)
    ciphertext, header, kwargs = saved[0]
    with pytest.raises(ObjectAlreadyExists):
        original(ciphertext, header, **kwargs)
    assert all(is_fence(item) for item in env[3].list_prefix('projects/'))
    assert usage(env)['bytes'] == FENCE_BYTES


def test_lost_upload_response_binds_observed_generation_before_fencing(env):
    made = begin(env)
    original = env[0].parts.write
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError('Synthetic lost upload response')
    env[0].parts.write = lost
    with pytest.raises(TimeoutError):
        env[0].upload_part(UID, PID, made['publication'], 0, wav())
    env[5][0] += 3600 + GRACE + 121
    write = env[3].write_chunks
    def fence(name, chunks, **kwargs):
        intent = env[2].get(_intent(PID, made['publication'])).data
        assert intent['state'] == 'collecting'
        assert intent['objects'][0]['generation'] == kwargs['if_generation_match'] > 0
        assert list(chunks) == [b'']
        return write(name, [b''], **kwargs)
    env[3].write_chunks = fence
    gc = collector(env); lease = gc.acquire()
    assert gc.collect(PID, made['publication'], lease)


def test_lost_fence_response_resumes_without_double_refund_or_delete_recreate_gap(env):
    made, _, _ = expired_delete(env)
    write = env[3].write_chunks
    calls = []
    def lost(name, chunks, **kwargs):
        assert kwargs['if_generation_match'] > 0
        calls.append(kwargs)
        write(name, chunks, **kwargs)
        raise TimeoutError('Synthetic lost fence response')
    env[3].write_chunks = lost
    gc = collector(env); lease = gc.acquire()
    before = deepcopy(usage(env))
    with pytest.raises(TimeoutError):
        gc.collect(PID, made['publication'], lease)
    assert usage(env) == before and is_fence(env[3].list_prefix('projects/')[0])
    env[3].write_chunks = write
    assert gc.collect(PID, made['publication'], lease)
    assert len(calls) == 1 and usage(env)['bytes'] == FENCE_BYTES


def test_generation_mismatch_stops_without_overwrite_or_refund(env):
    made, _, _ = expired_delete(env)
    original = env[3].list_prefix('projects/')[0]
    encrypted = b''.join(env[3].read_chunks(original.name, max_bytes=original.size))
    changed = env[3].write_chunks(original.name, [encrypted], max_bytes=len(encrypted),
        content_type=original.content_type, if_generation_match=original.generation)
    gc = collector(env); lease = gc.acquire()
    before = deepcopy(usage(env))
    with pytest.raises(DocumentConflict, match='OBJECT_CHANGED'):
        gc.collect(PID, made['publication'], lease)
    assert env[3].stat(original.name) == changed and usage(env) == before


def test_missing_fence_or_changed_claim_never_refunds(env):
    made, _, _ = expired_delete(env)
    original = env[3].write_chunks
    env[3].write_chunks = lambda *args, **kwargs: None
    gc = collector(env); lease = gc.acquire()
    before = deepcopy(usage(env))
    with pytest.raises(DocumentConflict, match='FENCE_NOT_INSTALLED'):
        gc.collect(PID, made['publication'], lease)
    assert usage(env) == before
    env[3].write_chunks = original
    assert gc.collect(PID, made['publication'], lease)


def test_pending_finalize_and_delete_undo_cannot_race_claim(env):
    made, _, token = expired_delete(env)
    gc = collector(env); lease = gc.acquire()
    assert gc.claim(PID, made['publication'], lease)['state'] == 'collecting'
    with pytest.raises(DocumentConflict, match='UPLOAD_UNAVAILABLE'):
        env[0].finalize(UID, PID, made['publication'])
    with pytest.raises(DocumentNotFound):
        env[0].restore(UID, PID, made['id'], token)
    assert gc.collect(PID, made['publication'], lease)


def test_retention_hold_unknown_owner_or_schema_cannot_start_collection(env):
    made, _, _ = expired_delete(env)
    before = deepcopy(usage(env))
    for allowed in (False, None, 1):
        gc = collector(env, allowed=allowed); lease = gc.acquire()
        assert not gc.collect(PID, made['publication'], lease)
        gc.release(lease)
    assert usage(env) == before
    path = _intent(PID, made['publication'])
    env[2].run_transaction(lambda tx: tx.set(path, {**dict(tx.get(path).data), 'v': 999}))
    gc = collector(env); lease = gc.acquire()
    with pytest.raises(HostedValidationError, match='STATE_INVALID'):
        gc.collect(PID, made['publication'], lease)
    assert usage(env) == before and not is_fence(env[3].list_prefix('projects/')[0])


def test_lease_loss_or_overlap_stops_remaining_provider_mutations(env):
    made, _, _ = expired_delete(env)
    gc = collector(env); lease = gc.acquire()
    with pytest.raises(DocumentConflict, match='LEASE_BUSY'):
        collector(env).acquire()
    env[5][0] += 121
    replacement = collector(env).acquire()
    with pytest.raises(DocumentConflict, match='LEASE_LOST'):
        gc.collect(PID, made['publication'], lease)
    assert not is_fence(env[3].list_prefix('projects/')[0])
    assert gc.collect(PID, made['publication'], replacement)


def test_gc_retrying_transactions_do_not_double_settle(env):
    made, _, _ = expired_delete(env)
    env[2].retry_next_transaction()
    gc = collector(env); lease = gc.acquire()
    env[2].retry_next_transaction()
    assert gc.collect(PID, made['publication'], lease)
    assert usage(env)['bytes'] == FENCE_BYTES and usage(env)['records'] == 0


def test_maintenance_port_cannot_write_canonical_data_or_change_erasure_fences(env):
    port = NarrationMaintenanceStore(env[2])
    for path in (('projects', PID), ('users', UID), ('account_erasure_locks', UID),
                 ('projects', PID, 'blocks', 'block'), ('identity_bindings', 'binding')):
        for operation in ('create', 'set', 'delete'):
            with pytest.raises(ValueError, match='WRITE_SCOPE'):
                port.run_transaction(lambda tx: getattr(tx, operation)(path, *([] if operation == 'delete' else [{}])))
    with pytest.raises(ValueError, match='QUERY_SCOPE'):
        port.run_transaction(lambda tx: tx.query_collection(('projects', PID, 'blocks')))
    assert env[1].project_generation(PID, UID) == 4
