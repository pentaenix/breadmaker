from __future__ import annotations

"""Bounded off-line recovery drills for future privileged Cloud Run Jobs.

Drill inputs are explicit owner/project/operation tuples resolved through the
independent recovery bucket; the live Firestore project may be totally absent.
This code does not schedule itself or write a 'passed' claim to a mutable user
profile. M10 must provide audited job identity and append-only evidence storage.
"""

from dataclasses import dataclass

from hosted.firestore.ids import require_project_id, require_user_id, require_opaque_id
from hosted.recovery_drills import HostedRecoveryDrillService, RecoveryDrillResult


@dataclass(frozen=True)
class OfflineDrillRequest:
    project_id: str
    owner_user_id: str
    archive_operation: str


@dataclass(frozen=True)
class OfflineDrillBatch:
    reports: tuple[RecoveryDrillResult, ...]
    verified_count: int
    live_storage_untouched: bool = True
    deployment_proof: bool = False


class HostedRecoveryDrillCoordinator:
    MAX_BATCH = 12

    def __init__(self, recovery_drills: HostedRecoveryDrillService) -> None:
        if type(recovery_drills) is not HostedRecoveryDrillService:
            raise ValueError('HOSTED_OPERATOR_DRILL_SERVICE_REQUIRED')
        self.drills = recovery_drills

    def execute_isolated(self, requests: tuple[OfflineDrillRequest, ...]) -> OfflineDrillBatch:
        if (not isinstance(requests, tuple) or not 1 <= len(requests) <= self.MAX_BATCH
                or any(type(x) is not OfflineDrillRequest for x in requests)):
            raise ValueError('HOSTED_OPERATOR_DRILL_BATCH_INVALID')
        # Validate the *entire* batch before any read or expensive isolated
        # restore; duplicates must not run one drill then fail halfway through.
        seen = set()
        validated = []
        for item in requests:
            pid = require_project_id(item.project_id)
            uid = require_user_id(item.owner_user_id)
            operation = require_opaque_id(item.archive_operation, label='drill_operation')
            key = (pid, uid, operation)
            if key in seen:
                raise ValueError('HOSTED_OPERATOR_DRILL_DUPLICATE')
            seen.add(key)
            validated.append(key)
        reports = []
        for pid, uid, operation in validated:
            # `offline_drill` verifies the entire independently retained object
            # before importing it into fresh MemoryDocument/Object stores.
            report = self.drills.offline_drill(pid, uid, operation=operation)
            if not report.semantic_match or not report.isolated or report.source_project_id != pid:
                raise ValueError('HOSTED_OPERATOR_DRILL_RECOVERY_UNVERIFIED')
            reports.append(report)
        return OfflineDrillBatch(tuple(reports), len(reports))
