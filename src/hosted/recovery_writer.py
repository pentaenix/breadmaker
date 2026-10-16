from __future__ import annotations

"""M10C: bounded, immutable, Firestore-read-only scheduled archive publisher.

This job NEVER updates the recovery_schedules collection, impersonates a user
session, prunes backups, or authorizes physical deletion. Each UTC cadence slot
has a stable immutable GCS key; a retry verifies the existing archive. Thus
Firestore requires read-only IAM and the independent bucket only needs object
Creator + Viewer, not any delete or administration authority.

This is the hosted-v3 *product-state* archive format. It is not a claim of
exhaustive Firestore/system/orphan data preservation or a live DR rehearsal.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from hosted.firestore.contracts import DocumentConflict
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner, ScheduledRecoveryItem
from hosted.recovery_archives import HostedRecoveryArchiveService

MAX_ARCHIVES_PER_INVOCATION = 8


def slot_operation(item: ScheduledRecoveryItem, *, now: datetime, cadence_hours: int) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise DocumentConflict('HOSTED_RECOVERY_WRITER_UTC_REQUIRED')
    if type(cadence_hours) is not int or cadence_hours not in (24, 48, 72, 168):
        raise DocumentConflict('HOSTED_RECOVERY_WRITER_CADENCE_INVALID')
    epoch = int(now.astimezone(timezone.utc).timestamp())
    if type(item.due_epoch) is not int or item.due_epoch < 0 or item.due_epoch > epoch:
        raise DocumentConflict('HOSTED_RECOVERY_WRITER_NOT_DUE')
    interval = cadence_hours * 3600
    slot_epoch = item.due_epoch + ((epoch - item.due_epoch) // interval) * interval
    fingerprint = hashlib.sha256(
        f'm10c:hosted-v3:{item.project_id}:{item.owner_user_id}:{slot_epoch}'.encode('utf-8')
    ).hexdigest()
    return 'backup_' + fingerprint[:48]


@dataclass(frozen=True)
class ArchiveWriterReport:
    inspected: int
    published: int
    archive_size_bytes: int
    fingerprint: str
    destructive_actions_allowed: bool = False

    def public(self) -> dict:
        return {
            'inspected': self.inspected,
            'published_or_verified': self.published,
            'archive_size_bytes': self.archive_size_bytes,
            'fingerprint': self.fingerprint,
            'destructive_actions_allowed': False,
        }


class HostedRecoveryArchiveWriter:
    """Revalidate every slot against the source-of-truth before publishing.

    A worker crash after GCS publish is safe: the next attempt verifies that
    same immutable blob. The batch is not atomic; earlier verified archives
    survive later failure. A separate per-project archive creation limit bounds
    memory, while the read adapter bounds enumeration. A full disaster restore
    test remains an operational staging prerequisite.
    """

    def __init__(self, planner: HostedRecoveryJobPlanner,
                 archive_for_item: Callable[[ScheduledRecoveryItem], HostedRecoveryArchiveService],
                 *, max_archives: int = MAX_ARCHIVES_PER_INVOCATION) -> None:
        if type(max_archives) is not int or not 1 <= max_archives <= MAX_ARCHIVES_PER_INVOCATION:
            raise ValueError('HOSTED_RECOVERY_WRITER_BUDGET_INVALID')
        self.planner = planner
        self.archive_for_item = archive_for_item
        self.max_archives = max_archives

    def execute(self, *, now: datetime) -> ArchiveWriterReport:
        plan = self.planner.inspect(now=now)
        if len(plan.due_items) > self.max_archives:
            # Do not silently omit writers; configure more frequent schedules or
            # deliberately shard a future reviewed worker before activation.
            raise DocumentConflict('HOSTED_RECOVERY_WRITER_DUE_BUDGET_EXCEEDED')
        total = 0
        for item in plan.due_items:
            # The schedule and owner can change after the first inspection.
            latest = self.planner.revalidate(plan, now=now)
            if item not in latest.due_items:
                raise DocumentConflict('HOSTED_RECOVERY_WRITER_PLAN_CHANGED')
            # The planner fingerprint includes exact schedule cadence, enablement,
            # owner, revision and due epoch. Revalidate before every write.
            operation = slot_operation(item, now=now, cadence_hours=item.cadence_hours)
            archive = self.archive_for_item(item).create(item.project_id, item.owner_user_id,
                                                         operation=operation)
            if archive.project_generation < 1 or archive.size < 1 or len(archive.sha256) != 64:
                raise DocumentConflict('HOSTED_RECOVERY_WRITER_ARCHIVE_NOT_VERIFIED')
            total += archive.size
        return ArchiveWriterReport(plan.checked_count, len(plan.due_items), total, plan.fingerprint)
