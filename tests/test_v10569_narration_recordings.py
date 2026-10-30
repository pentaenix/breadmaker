"""Synthetic canonical hosted narration publication; no cloud or inference."""
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hosted.domain_store import HostedDomainStore
from hosted.firestore.contracts import (DocumentConflict, DocumentNotFound, HostedAuthorizationError,
                                         HostedValidationError, ProjectUnavailable)
from hosted.firestore.memory import MemoryDocumentStore
from hosted.narration.authority import NarrationAuthority
from hosted.narration.recordings import HostedNarrationRecordings, ROOT, USAGE, CAPACITY, _head, _intent
from hosted.narration.sealed_parts import SealedNarrationParts, PART_BYTES
from hosted.runtime_services import ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage.memory import MemoryObjectStore

UID, OTHER, PID = 'usr_' + 'a' * 32, 'usr_' + 'b' * 32, 'c' * 32


class Wrapper:
    key_resource = 'projects/synthetic/locations/us-central1/keyRings/test/cryptoKeys/test'
    def __init__(self):
        self.key = AESGCM.generate_key(256)
    def wrap(self, raw):
        import secrets
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, raw, None)
    def unwrap(self, wrapped):
        return AESGCM(self.key).decrypt(wrapped[:12], wrapped[12:], None)


def wav(frames=240):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b'\x00\x01' * frames)
    return stream.getvalue()


@pytest.fixture
def env():
    documents, objects, audio_objects = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    domain = HostedDomainStore(documents, objects)
    ensure_hosted_user(documents, user_id=UID, display_name='Synthetic owner')
    ensure_hosted_user(documents, user_id=OTHER, display_name='Synthetic other')
    domain.projects.create('Synthetic project', owner_user_id=UID, operation_id='create', project_id=PID)
    tab = domain.create('document_tab', {'project_id': PID}, {'name': 'Main', 'is_manuscript': True}, user_id=UID)
    chapter = domain.create('chapter', {'project_id': PID}, {'title': 'Synthetic title', 'tab_id': tab['id']}, user_id=UID)
    block = domain.create('manuscript_block', {'project_id': PID}, {
        'text_content': 'Synthetic 😀 prose', 'content_html': 'Synthetic 😀 prose',
        'chapter_id': chapter['id'], 'tab_id': tab['id'], 'position': 0}, user_id=UID)
    clock = [1000.0]
    authority = NarrationAuthority(domain, issuer='', now=lambda: clock[0], development=True)
    parts = SealedNarrationParts(audio_objects, HostedEnvelopeCodec(Wrapper()))
    catalog = {'voices': [{'id': 'af_heart', 'language': 'en-us'}]}
    service = HostedNarrationRecordings(domain, parts, authority=authority, catalog=catalog,
                                       epoch='synthetic_epoch', now=lambda: clock[0])
    metadata = dict(block_id=block['id'], source_text=block['text_content'], text=block['text_content'],
                    start=0, end=len(block['text_content'].encode('utf-16-le')) // 2,
                    voice='af_heart', timings=[dict(start=0, end=9, time=0, duration=.005)])
    return service, domain, documents, audio_objects, metadata, clock


def begin(env, *, payload=None, operation='first', metadata=None):
    service, _, _, _, default, _ = env
    return service.begin(UID, PID, default if metadata is None else metadata,
                         audio_bytes=len(wav() if payload is None else payload), operation=operation)


def publish(env, *, operation='first', metadata=None, payload=None):
    payload = wav() if payload is None else payload
    made = begin(env, operation=operation, metadata=metadata, payload=payload)
    for i in range((len(payload) + PART_BYTES - 1) // PART_BYTES):
        env[0].upload_part(UID, PID, made['publication'], i, payload[i * PART_BYTES:(i + 1) * PART_BYTES])
    return made, env[0].finalize(UID, PID, made['publication'])


def test_binary_parts_are_ciphertext_and_context_bound(env):
    service, _, _, objects, _, _ = env
    publication, payload = 'd' * 32, b'Synthetic private audio'
    ciphertext, header = service.parts.seal(payload, pid=PID, publication=publication, ordinal=0)
    ref = service.parts.write(ciphertext, header, pid=PID, publication=publication, ordinal=0)
    assert ref['size'] == len(payload) + 16 and payload not in ciphertext
    assert service.parts.read(ref, pid=PID, publication=publication, ordinal=0) == payload
    with pytest.raises(Exception):
        service.parts.read(ref, pid='e' * 32, publication=publication, ordinal=0)
    assert len(objects.list_prefix('projects/')) == 1


def test_publication_does_not_advance_project_or_history_and_keeps_metadata_private(env):
    service, domain, documents, objects, metadata, _ = env
    generation = domain.project_generation(PID, UID)
    made, record = publish(env)
    assert record['source_text'] == metadata['source_text'] and record['duration'] == .01
    assert domain.project_generation(PID, UID) == generation
    rows = documents.query_collection((ROOT, PID, 'intents'))
    assert metadata['source_text'] not in json.dumps([dict(row.data) for row in rows])
    assert objects.list_prefix('projects/')[0].size == len(wav()) + 16
    assert service.finalize(UID, PID, made['publication']) == record


def test_operation_replay_survives_instance_loss_without_duplicate_quota(env):
    service, _, documents, _, _, _ = env
    made = begin(env)
    replica = HostedNarrationRecordings(service.domain, service.parts, authority=service.authority,
        catalog=service.catalog, epoch=service.epoch, now=service.now)
    assert replica.begin(UID, PID, env[4], audio_bytes=len(wav()), operation='first') == made
    row = documents.get((USAGE, UID)).data
    intent = documents.get(_intent(PID, made['publication'])).data
    assert row['records'] == 1 and row['bytes'] == len(wav()) + 16 + intent['metadata_reserved']


def test_begin_rejects_changed_metadata_with_same_operation(env):
    begin(env)
    with pytest.raises(DocumentConflict, match='IDEMPOTENCY'):
        begin(env, metadata={**env[4], 'generation_pace': 1.1})
    assert len(env[2].query_collection((ROOT, PID, 'intents'))) == 1


def test_provider_transaction_retry_does_not_duplicate_capacity_or_parts(env):
    service, _, documents, _, _, _ = env
    documents.retry_next_transaction()
    made = begin(env)
    documents.retry_next_transaction()
    service.upload_part(UID, PID, made['publication'], 0, wav())
    documents.retry_next_transaction()
    service.finalize(UID, PID, made['publication'])
    assert documents.get((USAGE, UID)).data['records'] == 1
    assert len(env[3].list_prefix('projects/')) == 1


def test_part_replay_requires_exact_bytes_without_duplicate_objects(env):
    service = env[0]; made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    service.upload_part(UID, PID, made['publication'], 0, wav())
    with pytest.raises(DocumentConflict, match='REPLAY_CHANGED'):
        service.upload_part(UID, PID, made['publication'], 0, wav()[:-1] + b'2')
    assert len(env[3].list_prefix('projects/')) == 1


def test_incomplete_or_malformed_audio_cannot_publish(env):
    made = begin(env)
    with pytest.raises(DocumentConflict, match='INCOMPLETE'):
        env[0].finalize(UID, PID, made['publication'])
    env[0].upload_part(UID, PID, made['publication'], 0, b'x' * len(wav()))
    with pytest.raises(HostedValidationError, match='AUDIO_INVALID'):
        env[0].finalize(UID, PID, made['publication'])
    assert not env[2].query_collection((ROOT, PID, 'records'))


def test_changed_source_cannot_publish_but_retains_intent_for_gc(env):
    service, domain, documents, objects, metadata, _ = env
    made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    domain.update('manuscript_block', {'project_id': PID, 'block_id': metadata['block_id']},
                  {'text_content': 'Actual edit', 'content_html': 'Actual edit'}, user_id=UID)
    with pytest.raises(DocumentConflict, match='TEXT_CHANGED'):
        service.finalize(UID, PID, made['publication'])
    assert not documents.query_collection((ROOT, PID, 'records'))
    assert objects.list_prefix('projects/') and documents.get((USAGE, UID)).data['bytes'] > 0


def test_history_bookkeeping_alone_does_not_reject_unchanged_source(env):
    service, _, documents, _, _, _ = env
    made = begin(env)
    def history(tx):
        row = dict(tx.get(('projects', PID)).data)
        row['generation'] += 1
        tx.set(('projects', PID), row)
    documents.run_transaction(history)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    assert service.finalize(UID, PID, made['publication'])['id'] == made['id']


def test_replacement_is_pointer_last_and_failed_replacement_keeps_good_audio(env):
    service, _, documents, _, metadata, _ = env
    old, _ = publish(env)
    new = begin(env, operation='replacement', metadata={**metadata, 'replaces': old['id']})
    assert documents.get(_head(PID, old['id'])).data['state'] == 'active'
    service.upload_part(UID, PID, new['publication'], 0, b'x' * len(wav()))
    with pytest.raises(HostedValidationError):
        service.finalize(UID, PID, new['publication'])
    assert documents.get(_head(PID, old['id'])).data['state'] == 'active'


def test_two_racing_replacements_cannot_both_publish(env):
    service, _, documents, _, metadata, _ = env
    old, _ = publish(env)
    pending = [begin(env, operation='replace_' + str(i), metadata={**metadata, 'replaces': old['id']}) for i in range(2)]
    for made in pending:
        service.upload_part(UID, PID, made['publication'], 0, wav())
    service.finalize(UID, PID, pending[0]['publication'])
    with pytest.raises(DocumentConflict, match='REPLACEMENT_CHANGED'):
        service.finalize(UID, PID, pending[1]['publication'])
    assert documents.get(_head(PID, old['id'])).data['state'] == 'retired'


def test_delete_undo_checks_current_membership_and_expiry(env):
    service, _, documents, _, _, clock = env
    made, _ = publish(env)
    undo = service.delete(UID, PID, made['id'])
    with pytest.raises(DocumentNotFound):
        documents.run_transaction(lambda tx: service.record(tx, UID, PID, made['id']))
    assert service.restore(UID, PID, made['id'], undo['undo_token'])['id'] == made['id']
    undo = service.delete(UID, PID, made['id'])
    clock[0] += 86401
    with pytest.raises(DocumentNotFound):
        service.restore(UID, PID, made['id'], undo['undo_token'])
    assert documents.get((USAGE, UID)).data['bytes'] > 0  # no unproven refund/deletion


def test_cross_user_cannot_publish_delete_restore_or_read(env):
    service, _, documents, _, metadata, _ = env
    made, _ = publish(env)
    for call in [lambda: service.begin(OTHER, PID, metadata, audio_bytes=len(wav()), operation='foreign'),
                 lambda: service.delete(OTHER, PID, made['id']),
                 lambda: documents.run_transaction(lambda tx: service.record(tx, OTHER, PID, made['id']))]:
        with pytest.raises((ProjectUnavailable, DocumentNotFound, HostedAuthorizationError)):
            call()


def test_closure_or_revocation_between_upload_and_publish_fails_closed(env):
    service, _, documents, _, _, _ = env
    made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    documents.run_transaction(lambda tx: tx.create(('account_erasure_locks', UID), {'v': 1}))
    with pytest.raises(HostedAuthorizationError):
        service.finalize(UID, PID, made['publication'])
    assert not documents.query_collection((ROOT, PID, 'records'))


def test_source_deleted_or_moved_cannot_publish(env):
    service, _, documents, _, metadata, _ = env
    made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    documents.run_transaction(lambda tx: tx.delete(('projects', PID, 'blocks', metadata['block_id'])))
    with pytest.raises(DocumentNotFound):
        service.finalize(UID, PID, made['publication'])


def test_upload_capacity_and_slots_are_account_wide_and_conservative(env):
    begin(env)
    begin(env, operation='second')
    with pytest.raises(DocumentConflict, match='UPLOAD_BUSY'):
        begin(env, operation='third')
    env[5][0] += 3601
    row = dict(env[2].get((USAGE, UID)).data)
    row['bytes'] = CAPACITY
    env[2].run_transaction(lambda tx: tx.set((USAGE, UID), row))
    with pytest.raises(DocumentConflict, match='STORAGE_CAPACITY'):
        begin(env, operation='third')


def test_expired_upload_cannot_complete_or_release_unproven_capacity(env):
    made = begin(env); before = dict(env[2].get((USAGE, UID)).data)
    env[5][0] += 3601
    with pytest.raises(DocumentConflict, match='UPLOAD_UNAVAILABLE'):
        env[0].upload_part(UID, PID, made['publication'], 0, wav())
    assert env[2].get((USAGE, UID)).data == before


def test_large_lossless_recording_uses_bounded_binary_parts(env):
    payload = wav(PART_BYTES // 2 + 10)
    made, record = publish(env, payload=payload)
    assert record['duration'] > 43
    rows = env[3].list_prefix('projects/')
    assert len(rows) == 2 and max(row.size for row in rows) <= PART_BYTES + 16
    assert env[2].get(_intent(PID, made['publication'])).data['part_count'] == 2


def test_restored_database_epoch_cannot_authorize_old_derivatives(env):
    made, _ = publish(env)
    env[0].epoch = 'new_restore_epoch'
    with pytest.raises(DocumentNotFound):
        env[2].run_transaction(lambda tx: env[0].record(tx, UID, PID, made['id']))


def test_object_intent_is_durable_before_io_and_late_source_edit_cannot_attach(env):
    service, domain, documents, objects, metadata, _ = env
    made = begin(env)
    original = objects.write_chunks
    def interrupt(name, chunks, **kwargs):
        intent = documents.get(_intent(PID, made['publication'])).data
        assert len(intent['attempts']) == 1 and intent['state'] == 'pending'
        assert documents.get((USAGE, UID)).data['bytes'] >= len(wav()) + 16
        result = original(name, chunks, **kwargs)
        domain.update('manuscript_block', {'project_id': PID, 'block_id': metadata['block_id']},
            {'text_content': 'Changed during I/O', 'content_html': 'Changed during I/O'}, user_id=UID)
        return result
    objects.write_chunks = interrupt
    with pytest.raises(DocumentConflict, match='TEXT_CHANGED'):
        service.upload_part(UID, PID, made['publication'], 0, wav())
    assert not documents.get(_intent(PID, made['publication'])).data['parts']
    assert len(objects.list_prefix('projects/')) == 1  # durable orphan intent for GC


def test_uncertain_object_completion_retry_is_new_ciphertext_and_stays_accounted(env):
    service, _, documents, objects, _, clock = env
    made = begin(env)
    original = objects.write_chunks
    def uncertain(name, chunks, **kwargs):
        original(name, chunks, **kwargs)
        raise TimeoutError('synthetic response lost AFTER upload')
    objects.write_chunks = uncertain
    with pytest.raises(TimeoutError):
        service.upload_part(UID, PID, made['publication'], 0, wav())
    objects.write_chunks = original
    with pytest.raises(DocumentConflict, match='PART_BUSY'):
        service.upload_part(UID, PID, made['publication'], 0, wav())
    clock[0] += 121
    service.upload_part(UID, PID, made['publication'], 0, wav())
    service.finalize(UID, PID, made['publication'])
    assert len(objects.list_prefix('projects/')) == 2
    data = documents.get(_intent(PID, made['publication'])).data
    assert documents.get((USAGE, UID)).data['bytes'] == (len(wav()) + 16) * 2 + data['metadata_reserved']
    assert len(data['attempts']) == 2


def test_concurrent_begin_cannot_oversubscribe_last_available_space(env):
    service, _, documents, _, metadata, _ = env
    probe = begin(env, operation='probe')
    reserved = documents.get(_intent(PID, probe['publication'])).data['reserved_bytes']
    documents.run_transaction(lambda tx: tx.set((USAGE, UID), {
        'v': 1, 'bytes': CAPACITY - reserved, 'records': 0, 'uploads': {}}))
    def attempt(i):
        try:
            return service.begin(UID, PID, metadata, audio_bytes=len(wav()), operation='parallel_' + str(i))
        except DocumentConflict as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert outcomes.count('NARRATION_STORAGE_CAPACITY') == 7
    assert documents.get((USAGE, UID)).data['bytes'] == CAPACITY


def test_cloud_calls_never_run_inside_retryable_firestore_callback(env):
    service, _, documents, objects, _, _ = env
    original_tx, state = documents.run_transaction, [False]
    def guarded_tx(fn, **kwargs):
        def callback(tx):
            state[0] = True
            try:
                return fn(tx)
            finally:
                state[0] = False
        return original_tx(callback, **kwargs)
    documents.run_transaction = guarded_tx
    wrapper = service.parts.envelope._wrapper
    def guard(fn):
        def call(*args, **kwargs):
            assert not state[0], 'KMS/GCS is forbidden inside retryable callback'
            return fn(*args, **kwargs)
        return call
    wrapper.wrap, wrapper.unwrap = guard(wrapper.wrap), guard(wrapper.unwrap)
    objects.write_chunks, objects.read_chunks = guard(objects.write_chunks), guard(objects.read_chunks)
    publish(env)


def test_chapter_title_target_uses_opaque_canonical_title_and_utf16_range(env):
    block = env[1].get('manuscript_block', {'project_id': PID, 'block_id': env[4]['block_id']}, user_id=UID)
    cid, text = block['chapter_id'], 'Synthetic title'
    metadata = dict(block_id='chapter-title:' + cid, target_type='chapter_title', chapter_id=cid,
                    source_text=text, text=text, start=0, end=len(text), voice='af_heart', timings=[])
    _, result = publish(env, metadata=metadata)
    assert result['chapter_id'] == cid and result['source_text'] == text


def test_production_authority_rechecks_real_admission_and_greenlist_in_transaction(env):
    from auth.identity_platform import IdentityPlatformAdmissionStore
    from hosted.firestore.ids import identity_binding_id
    service, domain, documents, _, _, clock = env
    issuer, subject, email = 'identity-platform://synthetic', 'synthetic_provider_uid', 'tester@example.test'
    admission = IdentityPlatformAdmissionStore(documents, now=lambda: clock[0])
    grant_path = admission.greenlist_path(email)
    def configure(tx):
        tx.create(('identity_bindings', identity_binding_id(issuer + '\n' + subject)), {
            'issuer': issuer, 'subject': subject, 'user_id': UID, 'status': 'active'})
        tx.create(admission.admission_path(subject), {
            'external_uid': subject, 'status': 'active', 'greenlist_digest': grant_path[-1]})
        tx.create(grant_path, {'email': email, 'bound_uid': subject, 'status': 'active', 'expires_at_epoch': 0})
    documents.run_transaction(configure)
    authority = NarrationAuthority(domain, issuer=issuer, now=lambda: clock[0])
    service.authority = service.source.authority = authority
    made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    grant = dict(documents.get(grant_path).data)
    documents.run_transaction(lambda tx: tx.set(grant_path, {**grant, 'status': 'revoked'}))
    with pytest.raises(HostedAuthorizationError):
        service.finalize(UID, PID, made['publication'])
    assert not documents.query_collection((ROOT, PID, 'records'))


def test_unknown_schema_or_foreign_owner_fails_closed(env):
    made = begin(env)
    path = _intent(PID, made['publication'])
    data = dict(env[2].get(path).data)
    env[2].run_transaction(lambda tx: tx.set(path, {**data, 'future_unknown_field': 1}))
    with pytest.raises(HostedValidationError, match='STATE_INVALID'):
        env[0].upload_part(UID, PID, made['publication'], 0, wav())
    env[2].run_transaction(lambda tx: tx.set(path, {**data, 'owner_user_id': OTHER}))
    with pytest.raises(DocumentConflict, match='OWNERSHIP_CHANGED'):
        env[0].upload_part(UID, PID, made['publication'], 0, wav())


def test_delete_and_undo_lost_response_replays_are_safe_and_generation_fenced(env):
    made, _ = publish(env)
    service = env[0]
    token = service.delete(UID, PID, made['id'], expected_generation=1, operation='delete_once')
    assert service.delete(UID, PID, made['id'], expected_generation=1, operation='delete_once') == token
    first = service.restore(UID, PID, made['id'], token['undo_token'])
    assert first['generation'] == 3
    assert service.restore(UID, PID, made['id'], token['undo_token']) == first
    with pytest.raises(DocumentConflict, match='REPLAY_SUPERSEDED'):
        service.delete(UID, PID, made['id'], expected_generation=1, operation='delete_once')
    with pytest.raises(DocumentConflict, match='GENERATION_CONFLICT'):
        service.delete(UID, PID, made['id'], expected_generation=1, operation='new_delete')
    assert env[2].get(_head(PID, made['id'])).data['state'] == 'active'


def test_large_unicode_source_preserves_native_character_limits_without_fs_expansion(env):
    service, domain, documents, _, metadata, _ = env
    source = '漢😀' * 50000
    selected = source[:20000]
    domain.update('manuscript_block', {'project_id': PID, 'block_id': metadata['block_id']},
        {'text_content': source, 'content_html': source}, user_id=UID)
    large = {**metadata, 'source_text': source, 'text': selected,
             'end': len(selected.encode('utf-16-le')) // 2, 'timings': []}
    made, result = publish(env, metadata=large)
    assert result['source_text'] == source and result['text'] == selected
    assert len(json.dumps(dict(documents.get(_intent(PID, made['publication'])).data)).encode()) < 750000
