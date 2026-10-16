"""M12T: user-bound private UI state has no plaintext or cross-project restore bypass.

The owner runs ./verify-release. The authoring assistant does not run tests.
"""
from __future__ import annotations

import json

import pytest

from test_v10524_private_live_restore import OWNER, DEST, private_store
from hosted.encrypted_recovery import HostedEncryptedRecoveryWriter, HostedEncryptedRecoveryReader
from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer
from hosted.firestore.contracts import HostedAuthorizationError, HostedValidationError
from hosted.runtime_services import ensure_hosted_user
from hosted.recovery_coverage import RecoveryCoverageError
from hosted.storage.memory import MemoryObjectStore


def test_v10532_bookmark_and_ui_state_are_encrypted_and_independently_mutable():
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('An unpublished draft')['id']
    source.put_member_state(pid, {'bookmark': {'block_id': 'b_1', 'offset': 23}})
    source.put_member_state(pid, {'value': {'zoom': 1.25}}, key='reading_zoom')
    source.put_member_state(pid, {'value': {'zoom': 1.50}}, key='reading_zoom')
    assert source.member_state(pid)['bookmark']['offset'] == 23
    assert source.member_state(pid, key='reading_zoom')['value']['zoom'] == 1.50
    for path in (
        ('projects', pid, 'members', OWNER, 'state', 'bookmark'),
        ('projects', pid, 'members', OWNER, 'ui_state', 'reading_zoom'),
    ):
        sealed = docs.get(path).data
        assert set(sealed) <= {'id', 'user_id', 'key', 'generation', 'created_at',
                               'updated_at', '_bm_private_fields'}
        assert '_bm_private_fields' in sealed
        # The opaque routing key ("reading_zoom") is intentionally visible:
        # it selects the member's document without decrypting a project.
        # The *value* and bookmark content must be inside the AEAD envelope.
        if path[-2] == 'ui_state':
            assert sealed['key'] == 'reading_zoom'
        encrypted_envelope = json.dumps(sealed['_bm_private_fields'], sort_keys=True)
        assert '"zoom"' not in encrypted_envelope
        assert 'block_id' not in encrypted_envelope
        assert '"offset"' not in encrypted_envelope
    source.delete_member_state(pid, key='reading_zoom')
    assert source.member_state(pid, key='reading_zoom') is None
    with pytest.raises(HostedValidationError, match='RESERVED_FIELD'):
        source.put_member_state(pid, {'_bm_private_fields': {'bogus': True}})


def test_v10532_member_bound_envelopes_reject_swapped_ciphertext():
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('Secret draft')['id']
    source.put_member_state(pid, {'value': 'secret 1'}, key='one')
    source.put_member_state(pid, {'value': 'secret 2'}, key='two')
    a = ('projects', pid, 'members', OWNER, 'ui_state', 'one')
    b = ('projects', pid, 'members', OWNER, 'ui_state', 'two')
    old_b = dict(docs.get(b).data)
    old_b['_bm_private_fields'] = docs.get(a).data['_bm_private_fields']
    docs.run_transaction(lambda tx: tx.set(b, old_b))
    with pytest.raises(HostedValidationError, match='DECRYPT_FAILED'):
        source.member_state(pid, key='two')


def test_v10532_private_owner_bookmarks_follow_destination_owner_on_restore():
    docs, _live, source, codec = private_store(OWNER)
    pid = source.create('An unpublished novel')['id']
    source.put_member_state(pid, {'bookmark': {'block_id': 'b_1', 'offset': 3}})
    source.put_member_state(pid, {'value': {'last_open_tab': 'tab_manuscript'}}, key='pane')
    recovery = MemoryObjectStore()
    writer = HostedEncryptedRecoveryWriter(source, recovery, codec)
    writer.create(pid, OWNER, operation='owner_state_backup')
    reader = HostedEncryptedRecoveryReader(recovery, codec,
        authorize=lambda p, u, sid: p == pid and u == OWNER)
    dest_docs, _dest_objects, destination, _codec = private_store(DEST)
    result = HostedEncryptedLiveRestorer(reader, destination,
        authorize_destination=lambda uid: uid == DEST).restore(
            pid, OWNER, DEST, operation='owner_state_backup', restore_operation='owner_state_restore')
    new_pid = result.destination_project_id
    assert destination.member_state(new_pid)['bookmark']['offset'] == 3
    assert destination.member_state(new_pid, key='pane')['value']['last_open_tab'] == 'tab_manuscript'
    assert dest_docs.get(('projects', new_pid, 'members', OWNER, 'state', 'bookmark')) is None
    for path in (('projects', new_pid, 'members', DEST, 'state', 'bookmark'),
                 ('projects', new_pid, 'members', DEST, 'ui_state', 'pane')):
        stored = dest_docs.get(path).data
        assert '_bm_private_fields' in stored
        assert 'tab_manuscript' not in str(stored) and 'block_id' not in str(stored)


def test_v10532_other_members_state_blocks_backup_instead_of_silent_loss():
    docs, _live, source, codec = private_store(OWNER)
    pid = source.create('Collaborative draft')['id']
    # Source-only adversarial state from a real project collaborator; no old
    # user's private preferences may be silently omitted or transferred.
    other = DEST
    ensure_hosted_user(docs, user_id=other, display_name="Other reviewer")
    source.add_member(pid, other, "editor")
    path = ('projects', pid, 'members', other, 'ui_state', 'foreign_prefs')
    sealed = source.domain.encrypted_fields.seal('member_ui_state',
        {'project_id': pid, 'user_id': other, 'key': 'foreign_prefs'}, {'value': 'foreign secret'})
    docs.run_transaction(lambda tx: tx.create(path, {**sealed, 'generation': 1,
                        'created_at': '2026-01-01', 'updated_at': '2026-01-01'}))
    with pytest.raises(RecoveryCoverageError, match='FOREIGN_MEMBER_STATE_NOT_PORTABLE'):
        HostedEncryptedRecoveryWriter(source, MemoryObjectStore(), codec).create(
            pid, OWNER, operation='reject_foreign_state')


def test_v10532_private_member_state_api_cannot_address_other_user():
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('Hidden writing room')['id']
    ensure_hosted_user(docs, user_id=DEST, display_name='Other member')
    source.add_member(pid, DEST, 'editor')
    with pytest.raises(HostedAuthorizationError, match='OTHER_USER_DENIED'):
        source.put_member_state(pid, {'value': 'private'}, key='reading', user_id=DEST)
    with pytest.raises(HostedAuthorizationError, match='OTHER_USER_DENIED'):
        source.member_state(pid, key='reading', user_id=DEST)
    with pytest.raises(HostedAuthorizationError, match='OTHER_USER_DENIED'):
        source.delete_member_state(pid, key='reading', user_id=DEST)
    assert docs.get(('projects', pid, 'members', DEST, 'ui_state', 'reading')) is None


def test_v10532_private_bookmark_rejects_legacy_plaintext_without_fallback():
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('Secret research')['id']
    docs.run_transaction(lambda tx: tx.create(
        ('projects', pid, 'members', OWNER, 'state', 'bookmark'),
        {'bookmark': {'block_id': 'secret'}, 'generation': 1}))
    with pytest.raises(HostedValidationError, match='PLAINTEXT_FORBIDDEN'):
        source.member_state(pid)
    with pytest.raises(HostedValidationError, match='PLAINTEXT_FORBIDDEN'):
        source.put_member_state(pid, {'bookmark': {'block_id': 'new'}})


def test_v10532_transaction_retry_does_not_repeat_external_member_encryption(monkeypatch):
    docs, _live, source, _codec = private_store(OWNER)
    pid = source.create('Private manuscript')['id']
    codec = source._member_state_codec
    old_seal = codec.seal
    calls = []

    def counted(logical, ids, value):
        calls.append(logical)
        return old_seal(logical, ids, value)

    monkeypatch.setattr(codec, 'seal', counted)
    docs.retry_next_transaction()
    source.put_member_state(pid, {'value': 'Only one KMS wrap'}, key='pane')
    assert calls == ['member_ui_state']
    assert source.member_state(pid, key='pane')['value'] == 'Only one KMS wrap'
