from __future__ import annotations

"""M10B non-destructive Cloud Run recovery schedule observer.

This is deliberately NOT an archive writer. It has no write APIs, does not
impersonate a project owner and cannot erase anything. Scheduled archive
publication is a later privileged review/deployment gate.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

from hosted.firestore.contracts import DocumentConflict, StoredDocument
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner, MAX_SCHEDULE_SCAN

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class RecoveryPlanJobConfigurationError(ValueError):
    pass


def _required_environment(env: Mapping[str, str]) -> tuple[str, str]:
    """Require explicit staging/plan-only intent before any Google SDK call."""
    if env.get("BREADMAKER_ENV") != "staging" or env.get("BREADMAKER_OPERATOR_MODE") != "recovery_plan_read_only":
        raise RecoveryPlanJobConfigurationError("HOSTED_RECOVERY_PLAN_MODE_NOT_STAGING_READ_ONLY")
    project = str(env.get("BREADMAKER_GCP_PROJECT_ID") or "")
    database = str(env.get("BREADMAKER_FIRESTORE_DATABASE") or "")
    if not _PROJECT_RE.fullmatch(project):
        raise RecoveryPlanJobConfigurationError("HOSTED_RECOVERY_PLAN_PROJECT_INVALID")
    if database != "(default)":
        raise RecoveryPlanJobConfigurationError("HOSTED_RECOVERY_PLAN_DATABASE_INVALID")
    return project, database


class _BoundedGoogleScheduleReadStore:
    """Minimal read facade; never exposes Firestore set/create/delete/transaction.

    The generic Google adapter's query_collection() has no max-items contract.
    Use a 501-document SDK limit here, so a hostile/oversized collection cannot
    pull an unbounded number of schedules into the job process.
    """

    def __init__(self, *, project: str, database: str) -> None:
        from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
        self._documents = GoogleFirestoreDocumentStore(project=project, database=database)

    def get(self, path: tuple[str, ...]) -> StoredDocument | None:
        if len(path) != 2 and len(path) != 4:
            raise DocumentConflict("HOSTED_RECOVERY_PLAN_READ_SCOPE_INVALID")
        if path[0] != "projects" or (len(path) == 4 and path[2] != "members"):
            raise DocumentConflict("HOSTED_RECOVERY_PLAN_READ_SCOPE_INVALID")
        return self._documents.get(path)

    def query_collection(self, path: tuple[str, ...]) -> list[StoredDocument]:
        if path != ("recovery_schedules",):
            raise DocumentConflict("HOSTED_RECOVERY_PLAN_READ_SCOPE_INVALID")
        from hosted.firestore.google_driver import _snapshot_revision
        query = self._documents._client.collection("recovery_schedules").limit(MAX_SCHEDULE_SCAN + 1)
        return [StoredDocument(tuple(s.reference.path.split("/")), s.to_dict() or {}, _snapshot_revision(s))
                for s in query.stream()]


def build_plan(documents: Any, *, now: datetime) -> dict[str, Any]:
    """Credential-free pure orchestration for conformance and real GCP reads."""
    return HostedRecoveryJobPlanner(documents).inspect(now=now).public()


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         store_factory=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Breadmaker staging recovery schedule inspection")
    parser.add_argument("operation", choices=("plan",))
    args = parser.parse_args(argv)
    try:
        project, database = _required_environment(os.environ if env is None else env)
        if args.operation != "plan":  # additional fail-closed defense
            raise RecoveryPlanJobConfigurationError("HOSTED_RECOVERY_PLAN_UNKNOWN_OPERATION")
        factory = store_factory or _BoundedGoogleScheduleReadStore
        documents = factory(project=project, database=database)
        report = build_plan(documents, now=now or datetime.now(timezone.utc))
        print(json.dumps({"result": "READ_ONLY", "environment": "staging", **report}, sort_keys=True))
        return 0
    except Exception as exc:
        # Never expose a project title, user id, credentials, or SDK traceback
        # as part of a scheduled job log. Failed scans have no success report.
        code = str(exc)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,110}", code):
            code = "HOSTED_RECOVERY_PLAN_FAILED"
        print(f"Breadmaker recovery plan BLOCKED: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
