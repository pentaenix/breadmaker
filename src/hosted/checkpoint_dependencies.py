from __future__ import annotations

"""Strict private-checkpoint image dependency closure for sealed recovery.

A checkpoint's immutable snapshot can refer to a historical image no longer
in the live editor. Such a backup is NOT complete if that image's authenticated
project-asset row/bytes are absent. This validates the closed references before
sealed archive publication or staging any destination data. Specialized
checkpoint_asset_reference rows and independent blob families remain unsupported
and are rejected by the separate physical recovery inventory.
"""

import re
from collections.abc import Mapping

_FROM_API = re.compile(r'/api/assets/([A-Za-z0-9_-]{1,160}\.(?:png|jpg|webp))')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')
_MAX_NODES = 50_000
_MAX_STRING = 4 * 1024 * 1024


class HostedCheckpointDependencyError(ValueError):
    """No title, contents or user paths included in error diagnostics."""


def require_checkpoint_assets_present(records: Mapping[str, list[dict]]) -> None:
    """Reject missing, duplicate or unsupported snapshot image dependencies.

    All referenced project assets must also be in the authoritative, sealed
    asset inventory. The exporter independently validates actual image bytes;
    this function deliberately never treats plaintext blob paths as evidence.
    """
    by_path: dict[str, str] = {}
    for row in records.get('project_asset', []):
        if not isinstance(row, Mapping) or not isinstance(row.get('data'), Mapping):
            raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_INVENTORY_INVALID')
        entry = row['data']
        name, digest, api = entry.get('name'), entry.get('sha256'), entry.get('api_path')
        if (not isinstance(name, str) or not _FROM_API.fullmatch('/api/assets/' + name)
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                or api != '/api/assets/' + name or api in by_path):
            raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_INVENTORY_INVALID')
        by_path[api] = digest

    visited = 0
    for checkpoint in records.get('checkpoint', []):
        if not isinstance(checkpoint, Mapping) or not isinstance(checkpoint.get('data'), Mapping):
            raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_SNAPSHOT_INVALID')
        source = checkpoint['data']
        snapshot = source.get('snapshot')
        stack = [(snapshot, '')]
        while stack:
            item, field = stack.pop()
            visited += 1
            if visited > _MAX_NODES:
                raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_REFERENCE_LIMIT')
            if isinstance(item, Mapping):
                if set(item) == {'$bytes'}:
                    # The old binary checkpoint format cannot be searched for
                    # references without a dedicated authenticated decoder.
                    raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_BINARY_UNSUPPORTED')
                if item.get('block_type') == 'image' and not item.get('image_path'):
                    raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_IMAGE_REFERENCE_MISSING')
                if item.get('image_path'):
                    path = item['image_path']
                    if not isinstance(path, str) or not _FROM_API.fullmatch(path):
                        raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_REFERENCE_INVALID')
                    if path not in by_path:
                        raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_MISSING')
                stack.extend((child, str(key)) for key, child in item.items())
            elif isinstance(item, list):
                stack.extend((child, field) for child in item)
            elif isinstance(item, str):
                if len(item) > _MAX_STRING:
                    raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_REFERENCE_LIMIT')
                if '/api/assets/' not in item:
                    continue
                matches = _FROM_API.findall(item)
                if not matches or field == 'image_path' and item not in by_path:
                    raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_REFERENCE_INVALID')
                for leaf in matches:
                    if '/api/assets/' + leaf not in by_path:
                        raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_MISSING')
                # Ensure no malformed nested/escaped asset references get
                # bypassed by a valid reference appearing elsewhere in HTML.
                if item.count('/api/assets/') != len(matches):
                    raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_ASSET_REFERENCE_INVALID')
            elif item is not None and not isinstance(item, (bool, int, float)):
                raise HostedCheckpointDependencyError('HOSTED_CHECKPOINT_SNAPSHOT_INVALID')
