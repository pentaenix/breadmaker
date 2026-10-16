from __future__ import annotations

"""M10D: staging-only independent GCS -> isolated in-memory restore proof.

The job identity needs recovery-bucket objectViewer ONLY. It has no Firestore
client, source-bucket access, external identity authority, HTTP listener or
GCS mutation path. The test is a hosted-v3 product-state recovery rehearsal;
it does not claim exhaustive recovery of arbitrary Firestore/system records.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping

from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.contracts import HostedValidationError
from hosted.recovery_archives import HostedRecoveryArchiveService, MAX_RECOVERY_ARCHIVE_BYTES
from hosted.recovery_drills import HostedRecoveryDrillService
from hosted.storage.contracts import ObjectIntegrityError
from hosted.storage.google_driver import GoogleCloudObjectStore
# Duplicated only the narrow GCP project-ID syntax; do not import the
# Firestore-backed writer/plan runtime into an archive-only restoration job.
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

_HEX_SHA = re.compile(r'^[0-9a-f]{64}$')


class RecoveryDrillConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class DrillSelection:
    project_id: str
    owner_user_id: str
    operation: str
    expected_sha256: str
    expected_generation: int


def required_environment(env: Mapping[str, str]) -> tuple[str, str, DrillSelection]:
    if (env.get('BREADMAKER_ENV') != 'staging'
            or env.get('BREADMAKER_OPERATOR_MODE') != 'isolated_recovery_drill'
            or env.get('BREADMAKER_FIRESTORE_DATABASE') != '(default)'):
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_STAGING_MODE_REQUIRED')
    project = str(env.get('BREADMAKER_GCP_PROJECT_ID') or '')
    if not _PROJECT_RE.fullmatch(project):
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_PROJECT_SCOPE_INVALID')
    recovery_bucket = str(env.get('BREADMAKER_RECOVERY_BUCKET') or '')
    if recovery_bucket != f'{project}-breadmaker-recovery':
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_RECOVERY_BUCKET_SCOPE_INVALID')
    raw = env.get('BREADMAKER_DRILL_REQUEST_JSON', '')
    if not isinstance(raw, str) or not 10 <= len(raw) <= 1024:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_EXPLICIT_SELECTION_REQUIRED')
    try:
        request = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_REQUEST_INVALID') from exc
    if not isinstance(request, dict) or set(request) != {
        'project_id', 'owner_user_id', 'operation', 'expected_sha256', 'expected_generation'
    }:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_REQUEST_FIELDS_INVALID')
    try:
        pid = require_project_id(request['project_id'])
        uid = require_user_id(request['owner_user_id'])
    except HostedValidationError as exc:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_ID_INVALID') from exc
    operation = request['operation']
    # _key enforces the exact established immutable archive operation format.
    if not isinstance(operation, str):
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_OPERATION_INVALID')
    try:
        HostedRecoveryArchiveService._key(pid, uid, operation)
    except HostedValidationError as exc:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_OPERATION_INVALID') from exc
    digest = request['expected_sha256']
    generation = request['expected_generation']
    if not isinstance(digest, str) or not _HEX_SHA.fullmatch(digest):
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_SHA256_REQUIRED')
    if type(generation) is not int or generation < 1:
        raise RecoveryDrillConfigurationError('HOSTED_DRILL_GENERATION_REQUIRED')
    return project, recovery_bucket, DrillSelection(pid, uid, operation, digest, generation)


class _ReadOnlyRecoveryObjects:
    """Narrow adapter: cannot publish, update, enumerate unrelated data or delete."""

    def __init__(self, provider: Any):
        self._provider = provider

    def stat(self, name: str):
        return self._provider.stat(name)

    def read_chunks(self, name: str, *, max_bytes: int, if_generation_match: int | None = None):
        return self._provider.read_chunks(name, max_bytes=max_bytes,
                                          if_generation_match=if_generation_match)


class _IndependentArchiveReader:
    """Only implements what the existing offline drill needs (no live projects)."""

    _key = staticmethod(HostedRecoveryArchiveService._key)
    _verify = HostedRecoveryArchiveService._verify

    def __init__(self, objects: Any):
        self.recovery_objects = _ReadOnlyRecoveryObjects(objects)
        self.maximum = MAX_RECOVERY_ARCHIVE_BYTES


def verify_isolated_restore(objects: Any, selection: DrillSelection) -> dict:
    """Match operator-pinned generation/hash BEFORE and AFTER the restore drill."""
    reader = _IndependentArchiveReader(objects)
    snapshot_id, name = reader._key(selection.project_id, selection.owner_user_id,
                                    selection.operation)
    meta = reader.recovery_objects.stat(name)
    if meta is None or meta.generation != selection.expected_generation:
        raise ObjectIntegrityError('HOSTED_DRILL_PINNED_GENERATION_MISMATCH')
    archive = reader._verify(project_id=selection.project_id,
                             user_id=selection.owner_user_id, snapshot_id=snapshot_id, meta=meta)
    if archive.sha256 != selection.expected_sha256:
        raise ObjectIntegrityError('HOSTED_DRILL_PINNED_SHA256_MISMATCH')
    result = HostedRecoveryDrillService(reader).offline_drill(
        selection.project_id, selection.owner_user_id, operation=selection.operation)
    if (not result.semantic_match or not result.isolated
            or result.archive_sha256 != selection.expected_sha256):
        raise ObjectIntegrityError('HOSTED_DRILL_ISOLATED_RESTORE_MISMATCH')
    # No Firestore, source objects, or original hosted project are consulted.
    return {
        'result': 'ISOLATED_RESTORE_VERIFIED',
        'archive_sha256': result.archive_sha256,
        'object_generation': archive.object_generation,
        'restored_entity_groups': len(result.entity_counts),
        'restored_asset_count': len(result.asset_digests),
        'source_firestore_required': False,
        'production_deletion_authorized': False,
    }


def run_once(*, project: str, recovery_bucket: str, selection: DrillSelection,
             objects: Any = None) -> dict:
    # SDK initialized AFTER staging scope and pinned archive selection validated.
    if objects is None:
        objects = GoogleCloudObjectStore(project=project, bucket=recovery_bucket)
    return verify_isolated_restore(objects, selection)


def main(argv=None, *, env: Mapping[str, str] | None = None, run=run_once) -> int:
    parser = argparse.ArgumentParser(description='Independent staging recovery rehearsal; read only')
    parser.add_argument('operation', choices=('drill',))
    parser.parse_args(argv)
    try:
        project, bucket, selection = required_environment(os.environ if env is None else env)
        outcome = run(project=project, recovery_bucket=bucket, selection=selection)
        print(json.dumps(outcome, sort_keys=True))
        return 0
    except Exception as exc:
        # Operator logs intentionally exclude UID, project ID, object key,
        # request JSON, archive content and provider traceback.
        reason = str(exc)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,110}', reason):
            reason = 'HOSTED_DRILL_FAILED'
        print(f'Breadmaker isolated recovery drill BLOCKED: {reason}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
