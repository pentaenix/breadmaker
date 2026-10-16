from __future__ import annotations

import threading
from dataclasses import dataclass

QUIET_SECONDS = 120.0
LONG_SESSION_SECONDS = 15.0 * 60.0
RETRY_SECONDS = 60.0

@dataclass(frozen=True)
class SchedulerDecision:
    trigger: str
    generation: int
    retry_only: bool = False
    project_id: str = ''

class RecoveryBackupScheduler:
    """Thread-safe trigger coordinator for the built-in Recovery layer.

    Canonical generation is authority. Monotonic time is cadence only. Local
    Recovery protection and external-destination retry state are deliberately
    separate: a broken USB/cloud destination must never suppress future local
    Recovery points, and a local-package failure must not suppress remote retry.

    Project activation happens on request threads while scheduling happens on a
    background thread. Every decision is therefore bound to the project identity
    that produced it and delayed results may be applied only to that same project.
    """
    def __init__(self, *, quiet_seconds: float = QUIET_SECONDS, long_session_seconds: float = LONG_SESSION_SECONDS, retry_seconds: float = RETRY_SECONDS):
        self.quiet_seconds=float(quiet_seconds)
        self.long_session_seconds=float(long_session_seconds)
        self.retry_seconds=float(retry_seconds)
        self._lock=threading.RLock()
        self.project_id=''
        self.observed_generation=0
        self.dirty_generation=0
        self.dirty_since: float|None=None
        self.last_change: float|None=None
        self.last_success_generation=0
        self.local_retry_due: float|None=None
        self.destination_retry_due: float|None=None
        self.manual_pending=False

    def reset(self, project_id: str, generation: int, now_mono: float, *, protected_generation: int | None = None) -> None:
        with self._lock:
            self.project_id=str(project_id or '')
            self.observed_generation=max(0,int(generation or 0))
            # Unknown protection is never evidence that Current is protected. Runtime
            # activation passes the verified Recovery high-water explicitly; any caller
            # that omits it fails toward more backup work.
            protected=0 if protected_generation is None else max(0,min(self.observed_generation,int(protected_generation or 0)))
            self.last_success_generation=protected
            if self.observed_generation>protected:
                # Current survived a prior process but its full local Recovery package
                # did not. Treat it exactly like meaningful dirty work after restart.
                self.dirty_generation=self.observed_generation
                self.dirty_since=now_mono
                self.last_change=now_mono
            else:
                self.dirty_generation=0
                self.dirty_since=None
                self.last_change=None
            self.local_retry_due=None
            self.destination_retry_due=None
            self.manual_pending=False

    def observe(self, project_id: str, generation: int, now_mono: float) -> None:
        with self._lock:
            pid=str(project_id or '')
            gen=max(0,int(generation or 0))
            if pid!=self.project_id:
                # Project identity changed without an explicit activation reset. Treat
                # protection as unknown rather than manufacturing a baseline from Current.
                self.reset(pid,gen,now_mono,protected_generation=0);return
            if gen < self.observed_generation:
                return
            if gen > self.observed_generation:
                self.observed_generation=gen
                if gen <= self.last_success_generation:
                    self.dirty_generation=0;self.dirty_since=None;self.last_change=None
                    return
                self.dirty_generation=gen
                if self.dirty_since is None:self.dirty_since=now_mono
                self.last_change=now_mono

    def request_manual(self) -> None:
        with self._lock:
            self.manual_pending=True

    def has_pending_work(self) -> bool:
        """Return whether this project still needs coordinator attention.

        Clean schedulers are intentionally not permanent background work. The durable
        Recovery high-water lives on disk; this in-memory object only needs to remain
        scheduled while Current is dirty, a retry/manual action is pending, or the
        project is the selected local-shell project handled separately by app.py.
        """
        with self._lock:
            return bool(
                self.manual_pending
                or self.local_retry_due is not None
                or self.destination_retry_due is not None
                or (self.dirty_generation and self.dirty_generation>self.last_success_generation)
            )

    def due(self, now_mono: float) -> SchedulerDecision|None:
        with self._lock:
            if self.manual_pending:
                self.manual_pending=False
                return SchedulerDecision('manual_catch_up',self.observed_generation,False,self.project_id)

            # Local Recovery and destination retry clocks are independent. A local
            # package backoff suppresses only *new local package attempts*; it must not
            # delay a destination retry that is already due. Conversely, destination
            # backoff never suppresses quiet/long-session local Recovery.
            local_retry_pending=self.local_retry_due is not None
            if self.local_retry_due is not None and now_mono >= self.local_retry_due:
                self.local_retry_due=None
                return SchedulerDecision('retry_local_recovery',self.observed_generation,False,self.project_id)

            if not local_retry_pending and self.dirty_generation and self.dirty_generation>self.last_success_generation:
                if self.last_change is not None and now_mono-self.last_change>=self.quiet_seconds:
                    return SchedulerDecision('quiet_session',self.observed_generation,False,self.project_id)
                if self.dirty_since is not None and now_mono-self.dirty_since>=self.long_session_seconds:
                    return SchedulerDecision('long_session',self.observed_generation,False,self.project_id)

            if self.destination_retry_due is not None and now_mono >= self.destination_retry_due:
                self.destination_retry_due=None
                return SchedulerDecision('retry_failed_destinations',self.observed_generation,True,self.project_id)
            return None

    def _same_project(self, project_id: str|None) -> bool:
        return project_id is None or str(project_id or '')==self.project_id

    def mark_local_success(self, generation: int, now_mono: float, *, project_id: str|None=None) -> bool:
        with self._lock:
            if not self._same_project(project_id):return False
            gen=max(0,int(generation or 0))
            self.last_success_generation=max(self.last_success_generation,gen)
            self.local_retry_due=None
            if gen>=self.observed_generation:
                self.dirty_generation=0;self.dirty_since=None;self.last_change=None
            else:
                self.dirty_generation=self.observed_generation
                self.dirty_since=now_mono;self.last_change=now_mono
            return True

    def mark_local_failure(self, now_mono: float, *, project_id: str|None=None) -> bool:
        with self._lock:
            if not self._same_project(project_id):return False
            self.local_retry_due=now_mono+self.retry_seconds
            return True

    def mark_destination_success(self, *, project_id: str|None=None) -> bool:
        with self._lock:
            if not self._same_project(project_id):return False
            self.destination_retry_due=None
            return True

    def mark_destination_failure(self, now_mono: float, *, project_id: str|None=None) -> bool:
        with self._lock:
            if not self._same_project(project_id):return False
            self.destination_retry_due=now_mono+self.retry_seconds
            return True

    def mark_success(self, generation: int, now_mono: float, *, destinations_ok: bool=True, project_id: str|None=None) -> bool:
        with self._lock:
            if not self._same_project(project_id):return False
            self.mark_local_success(generation,now_mono,project_id=project_id)
            if destinations_ok:self.mark_destination_success(project_id=project_id)
            else:self.mark_destination_failure(now_mono,project_id=project_id)
            return True

