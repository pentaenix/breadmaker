"""Private checkpoint asset dependency tests. Human verifier only."""
from __future__ import annotations

import pytest

from hosted.checkpoint_dependencies import (
    HostedCheckpointDependencyError, require_checkpoint_assets_present,
)
from hosted.encrypted_recovery import HostedEncryptedRecoveryWriter
from hosted.storage.memory import MemoryObjectStore
from test_v10524_private_live_restore import OWNER, private_store


def asset_record():
    return {'data': {'name': 'img_' + 'a'*24 + '.png',
            'sha256': 'b'*64, 'api_path': '/api/assets/img_' + 'a'*24 + '.png'}}


def test_v10533_checkpoint_valid_reference_and_html_references_resolve():
    row = asset_record()
    api = row['data']['api_path']
    require_checkpoint_assets_present({'project_asset': [row], 'checkpoint': [{
        'data': {'snapshot': {'version': 1, 'blocks': [
            {'block_type': 'image', 'image_path': api, 'content_html': '<img src="' + api + '">'},
        ]}}}]})


def test_v10533_checkpoint_missing_historical_image_rejected():
    rows = {'checkpoint': [{'data': {'snapshot': {'blocks': [
        {'block_type': 'image', 'image_path': '/api/assets/img_' + 'a'*24 + '.png'},
    ]}}}]}
    with pytest.raises(HostedCheckpointDependencyError, match='ASSET_MISSING'):
        require_checkpoint_assets_present(rows)


def test_v10533_checkpoint_image_without_reference_or_malformed_name_rejected():
    with pytest.raises(HostedCheckpointDependencyError, match='IMAGE_REFERENCE_MISSING'):
        require_checkpoint_assets_present({'checkpoint': [{
            'data': {'snapshot': {'blocks': [{'block_type': 'image'}]}}}]})
    row = asset_record()
    with pytest.raises(HostedCheckpointDependencyError, match='REFERENCE_INVALID'):
        require_checkpoint_assets_present({'project_asset': [row], 'checkpoint': [{
            'data': {'snapshot': {'content_html': '<img src="/api/assets/../../secret">'}}}]})


def test_v10533_checkpoint_duplicate_asset_metadata_is_rejected():
    row = asset_record()
    with pytest.raises(HostedCheckpointDependencyError, match='INVENTORY_INVALID'):
        require_checkpoint_assets_present({'project_asset': [row, row]})


def test_v10533_private_recovery_fails_before_publishing_missing_checkpoint_image():
    docs, _live, source, codec = private_store(OWNER)
    pid = source.create('Illustrated manuscript')['id']
    source.domain.create('checkpoint', {'project_id': pid},
        {'snapshot': {'version': 1, 'blocks': [
            {'block_type': 'image', 'image_path': '/api/assets/img_' + 'a'*24 + '.png'}]}},
        user_id=OWNER, entity_id='checkpoint_missing_image')
    bucket = MemoryObjectStore()
    with pytest.raises(HostedCheckpointDependencyError, match='ASSET_MISSING'):
        HostedEncryptedRecoveryWriter(source, bucket, codec).create(
            pid, OWNER, operation='checkpoint_image_missing')
    assert not bucket.list_prefix(f'projects/{pid}/private/v1/')


def test_v10533_checkpoint_rejects_unportable_binary_or_absolute_image_path():
    with pytest.raises(HostedCheckpointDependencyError, match='BINARY_UNSUPPORTED'):
        require_checkpoint_assets_present({'checkpoint': [{
            'data': {'snapshot': {'$bytes': 'opaque-old-checkpoint'}}}]})
    with pytest.raises(HostedCheckpointDependencyError, match='REFERENCE_INVALID'):
        require_checkpoint_assets_present({'checkpoint': [{
            'data': {'snapshot': {'blocks': [
                {'block_type': 'image', 'image_path': 'file:///author/images/private.png'}
            ]}}}]})
