from __future__ import annotations

"""M9D: explicit, durable scheduled archive cycle (no in-process clock/timer).

A privileged Cloud Run Job in M10 will supply authenticated project/owner pairs
and a trusted UTC instant. Nothing here is exposed as an HTTP endpoint. No
retention pruning is performed: the independent recovery bucket is immutable.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.recovery_archives import HostedRecoveryArchiveService, RecoveryArchive


class RecoveryScheduleError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryScheduleError('HOSTED_SCHEDULE_UTC_INSTANT_REQUIRED')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RecoveryCycle:
    due: bool
    archive: RecoveryArchive | None
    next_due: str
    verified: bool


class HostedScheduledRecoveryService:
    """Owner-validated durable configuration and job-invoked scheduled backup.

    One immutable operation name per due slot makes a crash after GCS publishing
    but before receipt commit safe: another worker reads the same verified blob.
    This does not constitute a cross-provider Firestore/GCS snapshot lock; the
    existing archive generation-fence is still enforced by M9A.
    """

    def __init__(self, recovery: HostedRecoveryArchiveService):
        self.recovery = recovery
        self.documents = recovery.documents

    def _owner(self, tx, project_id: str, user_id: str) -> None:
        project, membership = self.recovery.authority._require_member(tx, project_id, user_id, owner=True)
        if str(project.get('status') or 'active') != 'active':
            raise ProjectUnavailable('PROJECT_NOT_FOUND')

    @staticmethod
    def _path(pid: str) -> tuple[str, ...]:
        return ('recovery_schedules', pid)

    def configure(self, project_id: str, owner_user_id: str, *, enabled: bool,
                  cadence_hours: int = 24, retention_days: int = 30,
                  now: datetime) -> dict[str, Any]:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        stamp = _utc(now)
        if type(enabled) is not bool or type(cadence_hours) is not int or cadence_hours not in (24, 48, 72, 168):
            raise RecoveryScheduleError('HOSTED_SCHEDULE_POLICY_INVALID')
        if type(retention_days) is not int or not 30 <= retention_days <= 3650:
            raise RecoveryScheduleError('HOSTED_SCHEDULE_RETENTION_TOO_SHORT')
        path = self._path(pid)

        def update(tx):
            self._owner(tx, pid, uid)
            old = tx.get(path)
            if old is not None and old.data.get('owner_user_id') != uid:
                raise DocumentConflict('HOSTED_SCHEDULE_OWNER_CHANGED')
            previous = dict(old.data) if old else {}
            modified = (previous.get('enabled') != enabled or previous.get('cadence_hours') != cadence_hours)
            row = {
                'project_id': pid, 'owner_user_id': uid, 'enabled': enabled,
                'cadence_hours': cadence_hours, 'retention_days': retention_days,
                'next_due_epoch': int(stamp.timestamp()) if modified or old is None else int(previous['next_due_epoch']),
                'last_archive_name': previous.get('last_archive_name', ''),
                'last_archive_generation': previous.get('last_archive_generation', 0),
                'last_archive_sha256': previous.get('last_archive_sha256', ''),
                'last_project_generation': previous.get('last_project_generation', 0),
                'last_verified_epoch': previous.get('last_verified_epoch', 0),
            }
            tx.set(path, row)
            return row

        return dict(self.documents.run_transaction(update))

    def tick(self, project_id: str, owner_user_id: str, *, now: datetime) -> RecoveryCycle:
        """Explicit single-project job tick. No Cloud Scheduler or UI connection."""
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        stamp = _utc(now)
        epoch = int(stamp.timestamp())

        def before(tx):
            self._owner(tx, pid, uid)
            state = tx.get(self._path(pid))
            return dict(state.data) if state else None

        state = self.documents.run_transaction(before)
        if state is None or not state.get('enabled'):
            raise RecoveryScheduleError('HOSTED_SCHEDULE_DISABLED')
        due_epoch = int(state['next_due_epoch'])
        if epoch < due_epoch:
            return RecoveryCycle(False, None, datetime.fromtimestamp(due_epoch, timezone.utc).isoformat(), False)
        cadence = int(state['cadence_hours']) * 3600
        # Bound names, tie operation to the due instant (not a caller-chosen key).
        operation = 'scheduled_' + hashlib.sha256(f'{pid}:{uid}:{due_epoch}'.encode()).hexdigest()[:40]
        archived = self.recovery.create(pid, uid, operation=operation)
        if not archived.sha256 or archived.size < 1:
            raise DocumentConflict('HOSTED_SCHEDULE_ARCHIVE_UNVERIFIED')

        def commit(tx):
            self._owner(tx, pid, uid)
            current_doc = tx.get(self._path(pid))
            if current_doc is None:
                raise DocumentConflict('HOSTED_SCHEDULE_CONFIG_REMOVED')
            current = dict(current_doc.data)
            if (not current.get('enabled') or current.get('owner_user_id') != uid
                    or int(current.get('cadence_hours') or 0) * 3600 != cadence):
                raise DocumentConflict('HOSTED_SCHEDULE_CONFIG_CHANGED')
            project_doc = tx.get(('projects', pid))
            if project_doc is None or int(project_doc.data.get('generation') or 0) != archived.project_generation:
                # The archive remains an immutable valid earlier backup, but
                # it cannot satisfy this cycle after another edit intervenes.
                raise DocumentConflict('HOSTED_SCHEDULE_CONCURRENT_PROJECT_EDIT')
            if int(current['next_due_epoch']) != due_epoch:
                # A concurrent tick committed first, or policy was reconfigured.
                raise DocumentConflict('HOSTED_SCHEDULE_CONCURRENT_TICK')
            # A late tick is one cycle, not a burst of thousands of catch-up
            # archives. The next cycle cannot be earlier than now + cadence.
            next_due = max(due_epoch + cadence, epoch + cadence)
            current.update({
                'next_due_epoch': next_due,
                'last_archive_name': archived.object_name,
                'last_archive_generation': archived.object_generation,
                'last_archive_sha256': archived.sha256,
                'last_project_generation': archived.project_generation,
                'last_verified_epoch': epoch,
            })
            tx.set(self._path(pid), current)
            return next_due

        next_due_epoch = self.documents.run_transaction(commit)
        return RecoveryCycle(True, archived, datetime.fromtimestamp(next_due_epoch, timezone.utc).isoformat(), True)

    def check_last(self, project_id: str, owner_user_id: str) -> RecoveryArchive:
        """Verify exact receipt bytes, not merely a Firestore success flag."""
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        def inspect(tx):
            self._owner(tx, pid, uid)
            row = tx.get(self._path(pid))
            if row is None or not row.data.get('last_archive_name'):
                raise RecoveryScheduleError('HOSTED_SCHEDULE_NO_VERIFIED_BACKUP')
            return dict(row.data)
        recorded = self.documents.run_transaction(inspect)
        meta = self.recovery.recovery_objects.stat(str(recorded['last_archive_name']))
        if meta is None or meta.generation != int(recorded['last_archive_generation']):
            raise DocumentConflict('HOSTED_SCHEDULE_ARCHIVE_MISSING_OR_CHANGED')
        details = dict(meta.metadata)
        archived = self.recovery._verify(project_id=pid, user_id=uid,
            snapshot_id=str(details.get('breadmaker-snapshot-id') or ''), meta=meta)
        if (archived.sha256 != recorded['last_archive_sha256']
                or archived.project_generation != recorded['last_project_generation']):
            raise DocumentConflict('HOSTED_SCHEDULE_RECEIPT_MISMATCH')
        return archived
