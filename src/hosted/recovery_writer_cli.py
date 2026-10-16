from __future__ import annotations

"""Separate *staging-only* GCS archive-writer Cloud Run Job entrypoint.

No hosted HTTP listener, end-user session, Firestore write API, account/purge
service or mutable live-content store is exposed in this process. The
infrastructure must enforce that same boundary with independent IAM.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

from hosted.backup_job_cli import (_PROJECT_RE, _BoundedGoogleScheduleReadStore)
from hosted.firestore.contracts import DocumentConflict, StoredDocument, require_document_path, require_collection_path
from hosted.storage.contracts import ObjectAlreadyExists, ObjectLimitExceeded, ObjectIntegrityError, ObjectGenerationConflict
from hosted.firestore.ids import require_project_id
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore, _snapshot_revision
from hosted.firestore.repositories import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository
from hosted.runtime_services import HostedCloudProjectRepository
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.recovery_writer import HostedRecoveryArchiveWriter
from hosted.storage.google_driver import GoogleCloudObjectStore


class RecoveryWriterConfigurationError(ValueError):
    pass


def required_environment(env: Mapping[str, str]) -> tuple[str, str, str]:
    if env.get('BREADMAKER_ENV') != 'staging' or env.get('BREADMAKER_OPERATOR_MODE') != 'recovery_archive_writer':
        raise RecoveryWriterConfigurationError('HOSTED_RECOVERY_WRITER_STAGING_MODE_REQUIRED')
    project = str(env.get('BREADMAKER_GCP_PROJECT_ID') or '')
    if not _PROJECT_RE.fullmatch(project) or env.get('BREADMAKER_FIRESTORE_DATABASE') != '(default)':
        raise RecoveryWriterConfigurationError('HOSTED_RECOVERY_WRITER_PROJECT_INVALID')
    content = str(env.get('BREADMAKER_STORAGE_BUCKET') or '')
    recovery = str(env.get('BREADMAKER_RECOVERY_BUCKET') or '')
    if content != f'{project}-breadmaker-content' or recovery != f'{project}-breadmaker-recovery':
        raise RecoveryWriterConfigurationError('HOSTED_RECOVERY_WRITER_BUCKET_SCOPE_INVALID')
    if content == recovery:
        raise RecoveryWriterConfigurationError('HOSTED_RECOVERY_WRITER_INDEPENDENT_BUCKET_REQUIRED')
    return project, content, recovery


class _ReadTransaction:
    """Scoped Firestore read transaction with bounded SDK query results."""

    def __init__(self, client: Any, transaction: Any, pid: str, max_records: int):
        self._client, self._tx, self._pid, self._max = client, transaction, pid, max_records

    def _scope(self, path: tuple[str, ...]):
        if tuple(path[:2]) != ('projects', self._pid):
            raise DocumentConflict('HOSTED_RECOVERY_WRITER_DOCUMENT_SCOPE_INVALID')

    def get(self, path: tuple[str, ...]):
        key = require_document_path(path)
        self._scope(key)
        snapshot = self._client.document('/'.join(key)).get(transaction=self._tx)
        if not snapshot.exists:
            return None
        return StoredDocument(key, snapshot.to_dict() or {}, _snapshot_revision(snapshot))

    def query_collection(self, path: tuple[str, ...], *, field: str | None = None, value: Any = None):
        key = require_collection_path(path)
        self._scope(key)
        query = self._client.collection('/'.join(key))
        if field is not None:
            from google.cloud.firestore_v1.base_query import FieldFilter
            query = query.where(filter=FieldFilter(str(field), '==', value))
        snapshots = list(self._tx.get(query.limit(self._max + 1)))
        if len(snapshots) > self._max:
            raise DocumentConflict('HOSTED_RECOVERY_WRITER_COLLECTION_BUDGET_EXCEEDED')
        return [StoredDocument(tuple(s.reference.path.split('/')), s.to_dict() or {}, _snapshot_revision(s))
                for s in snapshots]


class _ProjectReadStore:
    """No create/set/delete; limits every project collection before downloading."""

    def __init__(self, documents: GoogleFirestoreDocumentStore, pid: str, *, max_records: int = 20_000):
        self._docs = documents
        self._client = documents._client
        self._pid = require_project_id(pid)
        self._max = max_records

    def get(self, path: tuple[str, ...]):
        key = require_document_path(path)
        if key[:2] != ('projects', self._pid):
            raise DocumentConflict('HOSTED_RECOVERY_WRITER_DOCUMENT_SCOPE_INVALID')
        return self._docs.get(key)

    def query_collection(self, path: tuple[str, ...], *, field: str | None = None, value: Any = None):
        # Needed for read-only HostedProjectRepository membership enumeration.
        return self.run_transaction(lambda tx: tx.query_collection(path, field=field, value=value))

    def run_transaction(self, fn, *, max_attempts: int = 5):
        if not 1 <= max_attempts <= 5:
            raise ValueError('HOSTED_RECOVERY_WRITER_TRANSACTION_ATTEMPTS_INVALID')
        from google.cloud import firestore
        transaction = self._client.transaction(max_attempts=max_attempts)
        @firestore.transactional
        def perform(tx):
            return fn(_ReadTransaction(self._client, tx, self._pid, self._max))
        return perform(transaction)


class _LiveObjectReader:
    """Deliberately does not expose write or delete for live content."""

    def __init__(self, store: GoogleCloudObjectStore):
        self._store, self._bucket = store, store._bucket

    def stat(self, name):
        return self._store.stat(name)

    def read_chunks(self, name, *, max_bytes, if_generation_match=None, chunk_size=256*1024):
        return self._store.read_chunks(name, max_bytes=max_bytes,
                                       if_generation_match=if_generation_match, chunk_size=chunk_size)


class _ImmutableRecoveryWriter:
    """Recovery-only stat/read/create, never delete or list other buckets."""

    def __init__(self, store: GoogleCloudObjectStore):
        self._store, self._bucket = store, store._bucket

    def stat(self, name):
        return self._store.stat(name)

    def read_chunks(self, name, *, max_bytes, if_generation_match=None, chunk_size=256*1024):
        return self._store.read_chunks(name, max_bytes=max_bytes,
                                       if_generation_match=if_generation_match, chunk_size=chunk_size)

    def write_chunks(self, name, chunks, *, max_bytes, content_type, if_generation_match, metadata=None):
        from hosted.storage.keys import require_object_name
        key = require_object_name(name)
        if if_generation_match != 0 or not key.startswith('recovery/v1/projects/'):
            raise DocumentConflict('HOSTED_RECOVERY_WRITER_IMMUTABLE_CREATE_REQUIRED')
        if int(max_bytes) < 1 or int(max_bytes) > 64 * 1024 * 1024:
            raise ObjectLimitExceeded('HOSTED_RECOVERY_WRITER_OBJECT_BUDGET_INVALID')
        details = {str(k): str(v) for k, v in dict(metadata or {}).items()}
        expected = str(details.get('breadmaker-sha256') or '')
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ObjectIntegrityError('HOSTED_RECOVERY_WRITER_HASH_REQUIRED')
        data = bytearray()
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise ObjectIntegrityError('HOSTED_RECOVERY_WRITER_CHUNK_INVALID')
            if len(data) + len(chunk) > int(max_bytes):
                raise ObjectLimitExceeded('HOSTED_RECOVERY_WRITER_OBJECT_TOO_LARGE')
            data.extend(chunk)
        if not data or hashlib.sha256(data).hexdigest() != expected:
            raise ObjectIntegrityError('HOSTED_RECOVERY_WRITER_SOURCE_HASH_MISMATCH')
        # The shared GCS driver PATCHes metadata after creation; that needs
        # storage.objects.update, which the archive writer must never receive.
        # Set final immutable metadata on the *initial* generation-0 upload.
        blob = self._bucket.blob(key)
        blob.metadata = details
        try:
            blob.upload_from_string(bytes(data), content_type=content_type,
                                    if_generation_match=0, checksum='auto')
        except Exception as exc:
            translated = self._store._translate(exc)
            if isinstance(translated, ObjectGenerationConflict):
                raise ObjectAlreadyExists('HOSTED_OBJECT_ALREADY_EXISTS') from exc
            raise translated from exc
        verified = self._store.stat(key)
        if (verified is None or verified.generation != int(blob.generation or 0)
                or verified.size != len(data) or verified.sha256 != expected):
            raise ObjectIntegrityError('HOSTED_RECOVERY_WRITER_CREATE_VERIFY_FAILED')
        return verified


def run_once(*, project: str, content_bucket: str, recovery_bucket: str,
             now: datetime | None = None, schedule_documents=None,
             archive_factory=None, max_archives: int = 8) -> dict:
    """Injectable orchestration; actual providers are created lazily under staging IAM."""
    now = now or datetime.now(timezone.utc)
    schedule_documents = schedule_documents or _BoundedGoogleScheduleReadStore(project=project, database='(default)')
    planner = HostedRecoveryJobPlanner(schedule_documents)

    if archive_factory is None:
        # One SDK client per job. Each per-project facade limits the paths that
        # any package export can query, and exposes no Firestore mutator.
        shared_documents = GoogleFirestoreDocumentStore(project=project, database='(default)')
        content = _LiveObjectReader(GoogleCloudObjectStore(bucket=content_bucket, project=project))
        recovery = _ImmutableRecoveryWriter(GoogleCloudObjectStore(bucket=recovery_bucket, project=project))
        def archive_factory(item):
            docs = _ProjectReadStore(shared_documents, item.project_id)
            authority = HostedProjectRepository(docs)
            users = HostedUserRepository(HostedStructuredRepository(docs))
            projects = HostedCloudProjectRepository(docs, content, authority, users,
                                                    lambda: item.owner_user_id)
            return HostedRecoveryArchiveService(projects, recovery)

    report = HostedRecoveryArchiveWriter(planner, archive_factory, max_archives=max_archives).execute(now=now)
    return report.public()


def main(argv=None, *, env: Mapping[str, str] | None = None, run=run_once) -> int:
    parser = argparse.ArgumentParser(description='Staging-only immutable hosted-v3 recovery archive writer')
    parser.add_argument('operation', choices=('archive',))
    args = parser.parse_args(argv)
    try:
        config = os.environ if env is None else env
        project, content, recovery = required_environment(config)
        if args.operation != 'archive':
            raise RecoveryWriterConfigurationError('HOSTED_RECOVERY_WRITER_UNKNOWN_OPERATION')
        result = run(project=project, content_bucket=content, recovery_bucket=recovery)
        print(json.dumps({'result': 'ARCHIVES_VERIFIED', 'environment': 'staging', **result}, sort_keys=True))
        return 0
    except Exception as exc:
        # The scheduler's logs contain aggregate outcomes only, never manuscript,
        # project/user IDs, package bytes, tokens, or provider stack traces.
        code = str(exc)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,110}', code):
            code = 'HOSTED_RECOVERY_WRITER_FAILED'
        print(f'Breadmaker archive writer BLOCKED: {code}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
