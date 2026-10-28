"""Staged-erasure preparation: no credentials, mutation RPCs or production data."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hosted.erasure_census import CensusDocument, GoogleSnapshotCensus, SnapshotCensus, exact_timestamp
from hosted.erasure_manifest import (SealedErasureManifestStore, object_record, project_entries,
                                     retention_status)
from hosted.firestore.contracts import DocumentConflict
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage.memory import MemoryObjectStore

PROJECT = 'test-project'
DATABASE = f'projects/{PROJECT}/databases/(default)'
PREFIX = DATABASE + '/documents/'
PID, UID, OP = 'a'*32, 'usr_'+'b'*32, 'c'*64
ROOT = ('projects', PID)
NAME = '/'.join(ROOT)
READ = '2026-10-28T18:31:35.123456Z'
STAMP = '2026-10-28T18:31:30.123456789Z'
CONTENT, RECOVERY = f'{PROJECT}-breadmaker-content', f'{PROJECT}-breadmaker-recovery'


def document(path, *, exists=True, database=DATABASE):
    return NS(name=database+'/documents/'+path, fields={},
              update_time=STAMP if exists else None, create_time=STAMP if exists else None)


class API:
    def __init__(self):
        self.calls = []
        self.collections = {NAME: ['unknown'], NAME+'/unknown/missing': ['deep']}
        self.rows = {(NAME, 'unknown'): [document(NAME+'/unknown/missing', exists=False)],
                     (NAME+'/unknown/missing', 'deep'): [document(NAME+'/unknown/missing/deep/actual')]}

    def _request(self, method, request, kwargs):
        assert request['read_time'] == exact_timestamp(READ)
        assert kwargs['retry'] is None and 0 < kwargs['timeout'] <= 20
        self.calls.append((method, deepcopy(request)))

    def get_document(self, *, request, **kwargs):
        self._request('get_document', request, kwargs)
        assert request['mask'] == {'field_paths': []}
        return document(request['name'][len(PREFIX):])

    def list_collection_ids(self, *, request, **kwargs):
        self._request('list_collection_ids', request, kwargs)
        path = request['parent'][len(PREFIX):]
        return NS(collection_ids=self.collections.get(path, []), next_page_token='')

    def list_documents(self, *, request, **kwargs):
        self._request('list_documents', request, kwargs)
        assert request['show_missing'] is True and request['mask'] == {'field_paths': []}
        assert 'where' not in request and 'order_by' not in request
        path = request['parent'][len(PREFIX):]
        return NS(documents=self.rows.get((path, request['collection_id']), []), next_page_token='')


def census(api=None):
    api = api or API()
    client = NS(_database_string=DATABASE, _firestore_api=api)
    return GoogleSnapshotCensus(client, project=PROJECT, read_time=READ), api


def snapshot():
    return census()[0].scan((ROOT,))


def metadata(**changes):
    return {'name': f'projects/{PID}/blocks/old.json', 'generation': '41', 'metageneration': '2',
            'size': '100', 'timeCreated': '2026-10-01T00:00:00Z', **changes}


class Wrapper:
    key_resource = 'projects/test-project/locations/us-central1/keyRings/test/cryptoKeys/erasure'
    def wrap(self, raw): return raw[::-1]  # Test only, NEVER production key handling.
    def unwrap(self, raw): return raw[::-1]


def store(objects=None, authorize=None):
    objects = objects or MemoryObjectStore()
    return SealedErasureManifestStore(objects, HostedEnvelopeCodec(Wrapper()),
        authorize=authorize or (lambda pid, uid, op: (pid, uid, op) == (PID, UID, OP))), objects


def prepare(manifests, **kwargs):
    values = dict(project_id=PID, owner_user_id=UID, operation=OP,
                  requested_at='2026-10-28T18:00:00Z', objects=[(CONTENT, metadata())])
    values.update(kwargs)
    return manifests.prepare(snapshot(), **values)


def test_v10563_census_walks_unknown_orphan_descendants_at_one_read_time():
    scanner, api = census()
    result = scanner.scan((ROOT,))
    assert result.missing_parents == (ROOT+('unknown', 'missing'),)
    assert {row.path for row in result.documents} == {ROOT, ROOT+('unknown', 'missing', 'deep', 'actual')}
    assert result.documents[1].update_time == STAMP
    assert result.rpc_calls == len(api.calls) == 6
    assert all(call[1]['read_time'] == result.read_time for call in api.calls)
    public = json.dumps(result.public())
    assert PID not in public and 'actual' not in public
    assert result.public()['deletion_authorized'] is False
    assert result.public()['cross_provider_frozen'] is False


def test_v10563_census_explicitly_accounts_every_pagination_rpc():
    api = API()
    def paged(*, request, **kwargs):
        api._request('list_documents', request, kwargs)
        if request['collection_id'] == 'deep':
            return NS(documents=[], next_page_token='')
        if not request['page_token']:
            response = NS(documents=[document(NAME+'/unknown/first')], next_page_token='second')
        else:
            assert request['page_token'] == 'second'
            response = NS(documents=[document(NAME+'/unknown/last')], next_page_token='')
        class Pager:
            @property
            def pages(self):
                yield response
                raise AssertionError('Automatic second page must not escape RPC budget')
        return Pager()
    api.list_documents = paged
    result = census(api)[0].scan((ROOT,))
    assert len(result.documents) == 3
    assert len([call for call in api.calls if call[0] == 'list_documents']) == 2


@pytest.mark.parametrize('attack', ['foreign_database', 'foreign_root', 'wrong_parent', 'duplicate',
                                   'future', 'body', 'no_update_time', 'cycle', 'provider_failure'])
def test_v10563_census_refuses_ambiguous_or_untrusted_results(attack):
    scanner, api = census()
    row = document(NAME+'/unknown/actual')
    api.rows[(NAME, 'unknown')] = [row]
    if attack == 'foreign_database': row.name = 'projects/foreign/databases/(default)/documents/'+NAME+'/unknown/a'
    if attack == 'foreign_root': row.name = PREFIX+'projects/'+('d'*32)+'/unknown/a'
    if attack == 'wrong_parent': row.name = PREFIX+NAME+'/unknown/a/nested/b'
    if attack == 'duplicate': api.rows[(NAME, 'unknown')].append(row)
    if attack == 'future': row.update_time = '2026-10-28T18:31:35.123456001Z'
    if attack == 'body': row.fields = {'private': 'must not be accepted'}
    if attack == 'no_update_time': row.update_time = None
    if attack == 'provider_failure':
        def failed(**kwargs): raise RuntimeError('PRIVATE_ACCOUNT_IDENTIFIER')
        api.list_collection_ids = failed
    if attack == 'cycle':
        def cycle(*, request, **kwargs): return NS(documents=[], next_page_token='same')
        api.list_documents = cycle
    with pytest.raises(DocumentConflict) as failure:
        scanner.scan((ROOT,))
    assert 'PRIVATE_ACCOUNT' not in str(failure.value)


@pytest.mark.parametrize('limit', [{'max_entries': 1}, {'max_calls': 1}, {'max_depth': 2}])
def test_v10563_census_fails_instead_of_returning_partial_proof(limit):
    with pytest.raises(ValueError, match='BUDGET|DEPTH'):
        census()[0].scan((ROOT,), **limit)


def test_v10563_census_counts_empty_collections_and_rejects_duplicate_ids():
    scanner, api = census()
    api.collections[NAME] = ['empty', 'empty']
    with pytest.raises(DocumentConflict, match='DUPLICATE_COLLECTION'):
        scanner.scan((ROOT,))
    api.collections[NAME] = ['e'+str(i) for i in range(200)]
    with pytest.raises(ValueError, match='RPC_BUDGET'):
        scanner.scan((ROOT,), max_calls=5)
    assert len(api.calls) < 20


def test_v10563_census_never_turns_a_root_read_failure_into_empty_success():
    scanner, api = census()
    def missing(**kwargs): raise RuntimeError('404 or permission denied')
    api.get_document = missing
    with pytest.raises(DocumentConflict, match='PROVIDER_READ_FAILED'):
        scanner.scan((ROOT,))


def test_v10563_exact_nanosecond_write_preconditions_are_not_rounded():
    assert exact_timestamp(STAMP) == STAMP
    assert exact_timestamp('2026-10-28T18:31:30Z') == '2026-10-28T18:31:30.000000000Z'
    assert exact_timestamp('2026-10-28T18:31:30.123456001Z') != exact_timestamp('2026-10-28T18:31:30.123456002Z')
    with pytest.raises(ValueError, match='PRECISION'):
        GoogleSnapshotCensus(NS(_database_string=DATABASE, _firestore_api=API()),
                             project=PROJECT, read_time=STAMP)


def test_v10563_pinned_sdk_can_express_fixed_snapshot_and_empty_metadata_masks():
    # Provider-shape test requires no credentials or RPCs. It is not a live
    # orphan-discovery attestation and must never be presented as one.
    types = pytest.importorskip('google.cloud.firestore_v1.types.firestore')
    for cls in (types.ListDocumentsRequest, types.GetDocumentRequest):
        request = cls(read_time=READ, mask={'field_paths': []})
        assert type(request).pb(request).HasField('mask')
        assert type(request).pb(request).read_time.nanos == 123456000
    assert types.ListCollectionIdsRequest(read_time=READ).read_time.rfc3339() == READ


def test_v10563_manifest_encrypts_identifiers_and_preserves_every_observed_version():
    manifests, objects = store()
    versions = [(CONTENT, metadata()), (CONTENT, metadata(generation='40')),
                (RECOVERY, metadata(name=f'recovery/v1/projects/{PID}/archive', generation='39'))]
    prepared = prepare(manifests, objects=versions)
    assert prepared.state == 'prepared_not_authorized' and prepared.entry_count == 6
    commit, entries = manifests.read(project_id=PID, owner_user_id=UID, operation=OP,
                                    generation=prepared.generation)
    assert commit['cross_provider_frozen'] is False
    assert {row['generation'] for row in entries if row['kind']=='object'} == {'39', '40', '41'}
    assert any(row['kind']=='missing_parent' for row in entries)
    for meta in objects.list_prefix('erasure-audit/v1/'):
        raw = b''.join(objects.read_chunks(meta.name, max_bytes=200_000, if_generation_match=meta.generation))
        assert UID.encode() not in raw and STAMP.encode() not in raw and b'old.json' not in raw
        assert set(meta.metadata) == {'breadmaker-sha256'}


def test_v10563_manifest_retries_without_overwriting_any_generation():
    manifests, objects = store()
    first = prepare(manifests)
    versions = tuple((meta.name, meta.generation) for meta in objects.list_prefix('erasure-audit/'))
    assert prepare(manifests) == first
    assert tuple((meta.name, meta.generation) for meta in objects.list_prefix('erasure-audit/')) == versions
    with pytest.raises(DocumentConflict, match='REPLAY_CONFLICT'):
        prepare(manifests, objects=[(CONTENT, metadata(generation='42'))])
    assert tuple((meta.name, meta.generation) for meta in objects.list_prefix('erasure-audit/')) == versions


def test_v10563_interrupted_manifest_cannot_publish_a_partial_commit():
    manifests, objects = store()
    original = objects.write_chunks
    def interrupted(name, *args, **kwargs):
        if name.endswith('/commit.json'): raise RuntimeError('simulated interruption')
        return original(name, *args, **kwargs)
    objects.write_chunks = interrupted
    with pytest.raises(RuntimeError, match='interruption'): prepare(manifests)
    assert len(objects.list_prefix('erasure-audit/')) == 1
    objects.write_chunks = original
    with pytest.raises(DocumentConflict, match='MISSING_OR_CHANGED'):
        manifests.read(project_id=PID, owner_user_id=UID, operation=OP, generation=1)
    prepared = prepare(manifests)
    assert prepared.entry_count == 4
    assert len(objects.list_prefix('erasure-audit/')) == 2


def test_v10563_manifest_shards_are_bounded_and_committed_in_order(monkeypatch):
    import hosted.erasure_manifest as module
    monkeypatch.setattr(module, 'SHARD_BYTES', 700)
    manifests, objects = store()
    versions = [(CONTENT, metadata(generation=str(i))) for i in range(1, 10)]
    # The commit reference list itself also has a byte limit: refuse an
    # artificially tiny commit budget rather than silently truncating it.
    with pytest.raises(ValueError, match='SHARD_LIMIT'):
        prepare(manifests, objects=versions)
    assert not any(meta.name.endswith('/commit.json') for meta in objects.list_prefix('erasure-audit/'))


def test_v10563_complete_multishard_manifest_reopens_after_a_process_restart(monkeypatch):
    import hosted.erasure_manifest as module
    monkeypatch.setattr(module, 'SHARD_BYTES', 8192)
    manifests, objects = store()
    prepared = prepare(manifests, objects=[(CONTENT, metadata(generation=str(i))) for i in range(1, 121)])
    assert prepared.shard_count > 1
    restarted, _ = store(objects)
    commit, entries = restarted.read(project_id=PID, owner_user_id=UID, operation=OP,
                                     generation=prepared.generation)
    assert len(entries) == 123 and commit['entry_count'] == 123
    assert len({(e['name'], e['generation']) for e in entries if e['kind']=='object'}) == 120
    assert all(meta.size <= module.MAX_SEALED_BYTES for meta in objects.list_prefix('erasure-audit/'))


def test_v10563_revocation_during_manifest_preparation_leaves_no_commit():
    checks = []
    def authorize(*args):
        checks.append(args)
        return len(checks) == 1
    manifests, objects = store(authorize=authorize)
    with pytest.raises(DocumentConflict, match='OPERATOR_DENIED'): prepare(manifests)
    assert objects.list_prefix('erasure-audit/')
    assert not any(meta.name.endswith('/commit.json') for meta in objects.list_prefix('erasure-audit/'))


def test_v10563_manifest_reader_rejects_changed_account_and_authenticated_bad_schema():
    manifests, objects = store(authorize=lambda *args: True)
    prepared = prepare(manifests)
    with pytest.raises(DocumentConflict, match='COMMIT_INVALID'):
        manifests.read(project_id=PID, owner_user_id='usr_'+'d'*32, operation=OP,
                       generation=prepared.generation)
    commit, entries = manifests.read(project_id=PID, owner_user_id=UID, operation=OP,
                                     generation=prepared.generation)
    invalid = deepcopy(list(entries))
    invalid.append({'kind':'document', 'path':['projects','d'*32], 'update_time':STAMP})
    with pytest.raises(DocumentConflict, match='ENTRY_SCHEMA_INVALID'):
        manifests._validate_entries(commit, invalid)


def test_v10563_manifest_decryption_rejects_ciphertext_with_a_different_context():
    from dataclasses import replace
    import hashlib
    manifests, objects = store()
    prepare(manifests)
    shard = next(meta for meta in objects.list_prefix('erasure-audit/') if '/shard-' in meta.name)
    raw = objects._rows[shard.name].payload
    plain = manifests.codec.open(json.loads(raw), context=manifests._context(PID, OP, 0))
    wrong = manifests.codec.seal(plain, context=manifests._context(PID, 'd'*64, 0))
    swapped = json.dumps(wrong).encode()
    # Simulated provider corruption with a matching ordinary SHA and unchanged
    # generation; authenticated context, not only metadata, must reject it.
    objects._rows[shard.name].payload = swapped
    objects._rows[shard.name].metadata = replace(shard, size=len(swapped), sha256=hashlib.sha256(swapped).hexdigest())
    with pytest.raises(DocumentConflict, match='AUTHENTICATION_FAILED'): prepare(manifests)


@pytest.mark.parametrize('authorization', [False, None, 1, 'yes'])
def test_v10563_manifests_require_positive_current_operator_authority(authorization):
    manifests, objects = store(authorize=lambda *args: authorization)
    with pytest.raises(DocumentConflict, match='OPERATOR_DENIED'): prepare(manifests)
    assert not objects.list_prefix('erasure-audit/')
    with pytest.raises(DocumentConflict, match='OPERATOR_DENIED'):
        manifests.read(project_id=PID, owner_user_id=UID, operation=OP, generation=1)


def test_v10563_manifest_authority_is_rechecked_on_replay_and_read():
    allowed = [True]
    manifests, objects = store(authorize=lambda *args: allowed[0])
    prepared = prepare(manifests)
    allowed[0] = False
    with pytest.raises(DocumentConflict, match='OPERATOR_DENIED'): prepare(manifests)
    with pytest.raises(DocumentConflict, match='OPERATOR_DENIED'):
        manifests.read(project_id=PID, owner_user_id=UID, operation=OP, generation=prepared.generation)


def test_v10563_manifest_refuses_foreign_scope_duplicates_and_changed_provider_generation():
    manifests, objects = store()
    with pytest.raises(ValueError, match='OBJECT_SCOPE'):
        prepare(manifests, objects=[(CONTENT, metadata(name='projects/'+('d'*32)+'/foreign'))])
    with pytest.raises(ValueError, match='DUPLICATE'):
        prepare(manifests, objects=[(CONTENT, metadata()), (CONTENT, metadata())])
    prepared = prepare(manifests)
    meta = objects.stat(prepared.name)
    raw = b''.join(objects.read_chunks(meta.name, max_bytes=200_000))
    objects.write_chunks(meta.name, [raw], max_bytes=200_000, content_type='application/json',
                         if_generation_match=meta.generation)
    with pytest.raises(DocumentConflict, match='MISSING_OR_CHANGED'):
        manifests.read(project_id=PID, owner_user_id=UID, operation=OP, generation=prepared.generation)


def test_v10563_retention_never_reports_physical_completion_or_soft_delete_mutation():
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    record = object_record(CONTENT, metadata(), project=PROJECT, project_id=PID)
    assert retention_status(record, requested_at=READ, now=now)['state'] == 'eligible_for_revalidation'
    record['hold'] = True
    assert retention_status(record, requested_at=READ, now=now) == {'state':'held', 'next_check_at':None, 'complete':False}
    record = object_record(RECOVERY, metadata(softDeleteTime='2026-11-01T00:00:00Z',
        hardDeleteTime='2026-12-01T00:00:00Z'), project=PROJECT, project_id=PID)
    assert retention_status(record, requested_at=READ, now=now)['state'] == 'awaiting_provider_expiry'
    future = retention_status(record, requested_at=READ, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert future['state'] == 'provider_absence_check_required' and future['complete'] is False


def test_v10563_retention_keeps_both_floors_and_rounds_nanosecond_deadlines_up():
    record = object_record(RECOVERY, metadata(timeCreated='2026-11-01T00:00:00Z',
        retentionExpirationTime='2026-12-02T00:00:00.000000001Z',
        retention={'mode':'Locked', 'retainUntilTime':'2026-12-01T00:00:00Z'}), project=PROJECT, project_id=PID)
    result = retention_status(record, requested_at=READ, now=datetime(2026, 12, 2, tzinfo=timezone.utc))
    assert result['state'] == 'retained' and result['complete'] is False
    assert result['next_check_at'] == '2026-12-02T00:00:00.000001+00:00'


@pytest.mark.parametrize('bad', [{'temporaryHold':'false'}, {'generation':True},
    {'softDeleteTime':'2026-10-20T00:00:00Z'}, {'retention':{'mode':'Unknown'}},
    {'retentionExpirationTime':'not-a-time'}, {'metageneration':'0'}])
def test_v10563_invalid_retention_metadata_fails_closed(bad):
    with pytest.raises(ValueError): object_record(CONTENT, metadata(**bad), project=PROJECT, project_id=PID)


def test_v10563_preparation_does_not_change_or_enable_production_deletion_gate():
    from hosted.purge_production_gate import HostedProductionPurgeGate, REQUIRED_PROOFS
    assert HostedProductionPurgeGate.assess(recorded_proofs=frozenset(REQUIRED_PROOFS)).production_deletion_authorized is False
    assert not hasattr(SealedErasureManifestStore, 'delete')
    assert not hasattr(GoogleSnapshotCensus, 'execute')
