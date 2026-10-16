from __future__ import annotations

"""M9 operator-facing recovery job planning, independent of HTTP and timers.

Read-only source-of-truth enumeration. This module does *not* run scheduled
backups under an impersonated writer identity. The M10 job must authenticate its
own dedicated service identity and invoke the existing owner-authorized tick
through an audited job composition after establishing IAM and tenancy rules.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.ids import require_project_id, require_user_id

MAX_SCHEDULE_SCAN = 500


def _epoch(now: datetime) -> int:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('HOSTED_OPERATOR_UTC_CLOCK_REQUIRED')
    return int(now.astimezone(timezone.utc).timestamp())


@dataclass(frozen=True)
class ScheduledRecoveryItem:
    project_id: str
    owner_user_id: str
    due_epoch: int
    project_generation: int
    cadence_hours: int = 24


@dataclass(frozen=True)
class ScheduledRecoveryPlan:
    due_items: tuple[ScheduledRecoveryItem, ...]
    checked_count: int
    disabled_count: int
    not_yet_due_count: int
    fingerprint: str
    execution_authorized: bool = False

    def public(self) -> dict:
        return {
            'due_count': len(self.due_items),
            'checked_count': self.checked_count,
            'disabled_count': self.disabled_count,
            'not_yet_due_count': self.not_yet_due_count,
            'fingerprint': self.fingerprint,
            'execution_authorized': False,
        }


class HostedRecoveryJobPlanner:
    """Fails closed on malformed schedules instead of silently skipping writers.

    Read operations are not a transactionally consistent database-wide scan;
    consumers must replan prior to execution and must never treat its hash as
    authorization to write to Firestore or GCS.
    """

    def __init__(self, documents, *, max_records: int = MAX_SCHEDULE_SCAN):
        if not 1 <= max_records <= MAX_SCHEDULE_SCAN:
            raise ValueError('HOSTED_OPERATOR_SCHEDULE_BUDGET_INVALID')
        self.documents = documents
        self.max_records = max_records

    def inspect(self, *, now: datetime) -> ScheduledRecoveryPlan:
        epoch = _epoch(now)
        schedules = self.documents.query_collection(('recovery_schedules',))
        if len(schedules) > self.max_records:
            raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_SCAN_TOO_LARGE')
        due = []
        disabled = not_yet = 0
        seen = set()
        states = []
        for doc in schedules:
            path = tuple(doc.path)
            if len(path) != 2 or path[0] != 'recovery_schedules':
                raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_SCOPE_INVALID')
            pid = require_project_id(path[1])
            if pid in seen:
                raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_DUPLICATE')
            seen.add(pid)
            data = doc.data
            uid = require_user_id(data.get('owner_user_id'))
            if data.get('project_id') != pid or type(data.get('enabled')) is not bool:
                raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_RECORD_INVALID')
            if type(data.get('cadence_hours')) is not int or data['cadence_hours'] not in (24, 48, 72, 168):
                raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_CADENCE_INVALID')
            if type(data.get('retention_days')) is not int or data['retention_days'] < 30:
                raise DocumentConflict('HOSTED_OPERATOR_RETENTION_FLOOR_REQUIRED')
            if type(data.get('next_due_epoch')) is not int or data['next_due_epoch'] < 0:
                raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_DUE_INVALID')
            project = self.documents.get(('projects', pid))
            membership = self.documents.get(('projects', pid, 'members', uid))
            if (project is None or project.data.get('status') != 'active'
                    or membership is None or membership.data.get('role') != 'owner'):
                raise DocumentConflict('HOSTED_OPERATOR_OWNER_OR_PROJECT_CHANGED')
            generation = project.data.get('generation')
            if type(generation) is not int or generation < 1:
                raise DocumentConflict('HOSTED_OPERATOR_PROJECT_GENERATION_INVALID')
            states.append((pid, uid, str(doc.revision), str(project.revision),
                           str(membership.revision), generation, data['enabled'],
                           data['next_due_epoch'], data['cadence_hours'], data['retention_days']))
            if not data['enabled']:
                disabled += 1
            elif data['next_due_epoch'] > epoch:
                not_yet += 1
            else:
                due.append(ScheduledRecoveryItem(pid, uid, data['next_due_epoch'], generation, data['cadence_hours']))
        fingerprint = hashlib.sha256(json.dumps(sorted(states), separators=(',', ':')).encode()).hexdigest()
        # A cross-provider write fence and operator principal are absent. No
        # handler here calls `tick` or a storage mutator.
        return ScheduledRecoveryPlan(tuple(sorted(due, key=lambda x: (x.due_epoch, x.project_id))),
                                     len(states), disabled, not_yet, fingerprint)

    def revalidate(self, plan: ScheduledRecoveryPlan, *, now: datetime) -> ScheduledRecoveryPlan:
        latest = self.inspect(now=now)
        if plan.fingerprint != latest.fingerprint or plan.due_items != latest.due_items:
            raise DocumentConflict('HOSTED_OPERATOR_SCHEDULE_DRIFT')
        return latest
