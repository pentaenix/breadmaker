"""Private, bounded index protocol before runtime/UI activation."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError, HostedValidationError, ProjectUnavailable
from hosted.narration.index import PrivateNarrationIndex, summary, _root, _page, PAGE_RECORDS
from hosted.narration.recordings import ROOT
from tests.test_v10569_narration_recordings import env, UID, OTHER, PID, publish, begin, wav


def index(env):
    service = env[0]
    return PrivateNarrationIndex(env[2], service.parts.envelope, authority=service.authority, epoch=service.epoch)


def record(env, number=0):
    raw, kind, entity, _ = __import__('hosted.narration.metadata', fromlist=['normalize']).normalize(env[4], env[0].catalog)
    source, _ = env[0].source.capture(UID, PID, kind=kind, entity_id=entity)
    return summary(raw, source, record_id=f'synthetic_{number:04}', publication=f'{number:032x}',
                   generation=1, duration=.01, audio_bytes=524)


def install(env, owner, **kwargs):
    prepared = owner.prepare(UID, PID, **kwargs)
    env[2].run_transaction(prepared.commit)
    return prepared


def test_index_is_encrypted_and_reads_thin_authorized_metadata_only(env):
    owner = index(env)
    item = record(env)
    generation = env[1].project_generation(PID, UID)
    prepared = install(env, owner, add=[item])
    assert prepared.locations == {item['id']: '000'}
    raw = json.dumps([dict(row.data) for row in env[2].query_collection((ROOT, PID, 'index_pages'))])
    assert item['text'] not in raw and item['source_hash'] not in raw and item['voice'] not in raw
    result = owner.page(UID, PID)
    assert result == {'records': [item], 'next': None}
    assert 'source_text' not in item and 'timings' not in item
    assert item['source_hash'] == hashlib.sha256(env[4]['source_text'].encode()).hexdigest()
    assert env[1].project_generation(PID, UID) == generation


def test_two_page_read_is_bounded_and_does_not_open_every_record_envelope(env):
    owner = index(env)
    values = [record(env, i) for i in range(PAGE_RECORDS + 1)]
    install(env, owner, add=values[:PAGE_RECORDS])
    install(env, owner, add=values[PAGE_RECORDS:])
    original = owner.envelope.open
    opened = []
    def spy(*args, **kwargs):
        opened.append(kwargs['context']['record_id'])
        return original(*args, **kwargs)
    owner.envelope.open = spy
    first = owner.page(UID, PID)
    assert len(first['records']) == PAGE_RECORDS and first['next'] == '001:'
    second = owner.page(UID, PID, after=first['next'])
    assert second == {'records': [values[-1]], 'next': None}
    assert len(opened) == 2  # page envelopes, not129 per-record KMS unwraps


def test_index_stage_is_non_mutating_and_stale_preparation_cannot_overwrite(env):
    owner = index(env)
    first = owner.prepare(UID, PID, add=[record(env)])
    stale = owner.prepare(UID, PID, add=[record(env, 1)])
    assert env[2].get(_root(PID)) is None
    env[2].run_transaction(first.commit)
    with pytest.raises(DocumentConflict, match='INDEX_CHANGED'):
        env[2].run_transaction(stale.commit)
    assert owner.page(UID, PID)['records'] == [record(env)]


def test_removal_and_replacement_keep_count_and_cas_bindings(env):
    owner = index(env)
    old, new = record(env), record(env, 1)
    install(env, owner, add=[old])
    remove = [('000', old['id'], old['publication'], old['generation'])]
    install(env, owner, add=[new], remove=remove)
    assert owner.page(UID, PID)['records'] == [new]
    assert env[2].get(_root(PID)).data['pages']['000']['count'] == 1
    with pytest.raises(DocumentConflict, match='REMOVAL_CHANGED'):
        owner.prepare(UID, PID, remove=remove)
    install(env, owner, remove=[('000', new['id'], new['publication'], 1)])
    assert owner.page(UID, PID) == {'records': [], 'next': None}
    assert env[2].get(_page(PID, '000')) is None


def test_lost_membership_cannot_commit_prepared_index_or_read_cached_page(env):
    owner = index(env)
    prepared = owner.prepare(UID, PID, add=[record(env)])
    env[2].run_transaction(lambda tx: tx.delete(('projects', PID, 'members', UID)))
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        env[2].run_transaction(prepared.commit)
    assert env[2].get(_root(PID)) is None
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        owner.page(UID, PID)


def test_index_rechecks_authority_after_external_kms_open(env):
    owner = index(env)
    install(env, owner, add=[record(env)])
    original = owner.envelope.open
    def revoke(*args, **kwargs):
        value = original(*args, **kwargs)
        env[2].run_transaction(lambda tx: tx.delete(('projects', PID, 'members', UID)))
        return value
    owner.envelope.open = revoke
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        owner.page(UID, PID)


def test_other_project_account_epoch_or_tampered_page_cannot_open(env):
    owner = index(env)
    install(env, owner, add=[record(env)])
    with pytest.raises(ProjectUnavailable): owner.page(OTHER, PID)
    with pytest.raises(ProjectUnavailable): owner.page(UID, 'f' * 32)
    other_epoch = PrivateNarrationIndex(env[2], env[0].parts.envelope, authority=env[0].authority, epoch='new_epoch')
    with pytest.raises(HostedValidationError, match='INDEX_STATE_INVALID'): other_epoch.page(UID, PID)
    row = dict(env[2].get(_page(PID, '000')).data)
    env[2].run_transaction(lambda tx: tx.set(_page(PID, '000'), {**row, 'generation': True}))
    with pytest.raises(HostedValidationError, match='INDEX_PAGE_INVALID'): owner.page(UID, PID)


def test_index_rejects_unknown_fields_overflow_and_invalid_cursors_before_mutation(env):
    owner = index(env)
    item = record(env)
    for value in ({**item, 'source_text': 'must not become a bulk body'}, {**item, 'text': 'x' * 161},
                  {**item, 'generation': True}, {**item, 'source_hash': 'not pinned'}):
        with pytest.raises(HostedValidationError, match='ENTRY_INVALID'):
            owner.prepare(UID, PID, add=[value])
    for cursor in ('../../private', '999:', '000:bad/id'):
        with pytest.raises(ValueError, match='CURSOR_INVALID'): owner.page(UID, PID, after=cursor)
    assert env[2].get(_root(PID)) is None


def test_real_publish_delete_restore_and_replacement_update_index_in_same_cas(env):
    service = env[0]
    made, value = publish(env)
    assert service.index.page(UID, PID)['records'][0]['id'] == made['id']
    token = service.delete(UID, PID, made['id'], expected_generation=1, operation='delete')['undo_token']
    assert service.index.page(UID, PID)['records'] == []
    restored = service.restore(UID, PID, made['id'], token)
    item = service.index.page(UID, PID)['records'][0]
    assert item['generation'] == restored['generation'] == 3
    newer, _ = publish(env, operation='replacement', metadata={**env[4], 'replaces': made['id']})
    assert [item['id'] for item in service.index.page(UID, PID)['records']] == [newer['id']]


def test_index_encryption_failure_keeps_previous_audio_and_index_intact(env):
    service = env[0]
    old, _ = publish(env)
    root = env[2].get(_root(PID))
    page = env[2].get(_page(PID, '000'))
    made = begin(env, operation='replacement', metadata={**env[4], 'replaces': old['id']})
    service.upload_part(UID, PID, made['publication'], 0, wav())
    original = service.parts.envelope.seal
    def fail_index(*args, **kwargs):
        if kwargs['context']['entity'] == 'narration_index': raise RuntimeError('Synthetic KMS outage')
        return original(*args, **kwargs)
    service.parts.envelope.seal = fail_index
    with pytest.raises(RuntimeError, match='KMS outage'):
        service.finalize(UID, PID, made['publication'])
    assert env[2].get(_root(PID)) == root and env[2].get(_page(PID, '000')) == page
    assert [item['id'] for item in service.index.page(UID, PID)['records']] == [old['id']]


def test_revocation_during_index_encryption_cannot_publish_metadata_or_head(env):
    service = env[0]
    made = begin(env)
    service.upload_part(UID, PID, made['publication'], 0, wav())
    original = service.parts.envelope.seal
    def revoke(*args, **kwargs):
        sealed = original(*args, **kwargs)
        if kwargs['context']['entity'] == 'narration_index':
            env[2].run_transaction(lambda tx: tx.delete(('projects', PID, 'members', UID)))
        return sealed
    service.parts.envelope.seal = revoke
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        service.finalize(UID, PID, made['publication'])
    assert env[2].get(_root(PID)) is None
    assert not env[2].query_collection((ROOT, PID, 'records'))


def test_independent_concurrent_publications_retry_without_losing_other_index_entries(env):
    service = env[0]
    pending = [begin(env, operation=f'concurrent_{i}') for i in range(2)]
    for made in pending: service.upload_part(UID, PID, made['publication'], 0, wav())
    barrier = Barrier(2)
    original = service.parts.envelope.seal
    def synchronize(*args, **kwargs):
        sealed = original(*args, **kwargs)
        if kwargs['context']['entity'] == 'narration_index': barrier.wait(timeout=2)
        return sealed
    service.parts.envelope.seal = synchronize
    def finish(made):
        try: return service.finalize(UID, PID, made['publication'])
        except DocumentConflict as error: return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(finish, pending))
    assert sum(isinstance(item, dict) for item in result) == 1
    assert sum(isinstance(item, DocumentConflict) for item in result) == 1
    assert len(service.index.page(UID, PID)['records']) == 1
    service.parts.envelope.seal = original
    for made in pending: service.finalize(UID, PID, made['publication'])
    assert {item['id'] for item in service.index.page(UID, PID)['records']} == {made['id'] for made in pending}


def test_index_source_record_and_public_manifest_require_literal_positive_authority(env):
    from hosted.narration.models import NarrationModelReads
    from hosted.narration.source import NarrationSource
    service = env[0]
    made, _ = publish(env)
    for value in (None, False, 1, {}):
        denied = lambda *args: value
        owner = PrivateNarrationIndex(env[2], service.parts.envelope, authority=denied, epoch=service.epoch)
        with pytest.raises(HostedAuthorizationError): owner.page(UID, PID)
        with pytest.raises(HostedAuthorizationError): owner.prepare(UID, PID, add=[record(env, 1)])
        source = NarrationSource(env[1], denied)
        with pytest.raises(HostedAuthorizationError):
            source.capture(UID, PID, kind='block', entity_id=env[4]['block_id'])
        models = NarrationModelReads(env[2], env[3], None, authority=denied)
        with pytest.raises(HostedAuthorizationError): models.manifest(UID, PID)
    original = service.authority
    service.authority = lambda *args: None
    with pytest.raises(HostedAuthorizationError):
        env[2].run_transaction(lambda tx: service.record(tx, UID, PID, made['id']))
    service.authority = original
