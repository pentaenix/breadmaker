from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from revision_store.local_store import LocalRevisionStore, RevisionStoreCorruption, RevisionStoreError, _fsync_parent

GC_FORMAT = 'breadmaker-history-gc-state'
GC_VERSION = 1
DEFAULT_HISTORY_RETENTION = '90_days'
HISTORY_RETENTION_VALUES = ('30_days', '90_days', '1_year', 'forever')
_RETENTION_DAYS = {'30_days': 30, '90_days': 90, '1_year': 365}
DEFAULT_GC_GRACE_SECONDS = 24 * 60 * 60
MINIMUM_GC_SCANS = 2


class HistoryGCError(RuntimeError):
    pass


class HistoryGCCorruption(HistoryGCError):
    pass


class HistoryGCAuthorityUncertain(HistoryGCError):
    pass


class HistoryGCRemoteDelete(Protocol):
    """Narrow 7.7.7 capability. It cannot address Recovery Backup paths."""

    def delete_revision_publication(self, generation: int) -> int: ...
    def delete_checkpoint(self, checkpoint_id: str) -> int: ...
    def delete_snapshot(self, snapshot_object_id: str) -> int: ...


@dataclass(frozen=True)
class Segment:
    checkpoint_id: str
    checkpoint_generation: int
    revision_ids: tuple[str, ...]
    generations: tuple[int, ...]
    snapshot_object_ids: tuple[str, ...]
    created_at_values: tuple[str, ...]

    @property
    def oldest_generation(self) -> int:
        return min(self.generations) if self.generations else 0


@dataclass(frozen=True)
class GCPlan:
    epoch: int
    policy: str
    created_at: str
    retained_revision_ids: tuple[str, ...]
    retained_segment_ids: tuple[str, ...]
    candidate_segments: tuple[Segment, ...]
    uncertain_segment_ids: tuple[str, ...] = ()
    orphan_snapshot_ids: tuple[str, ...] = ()
    orphan_checkpoint_ids: tuple[str, ...] = ()
    digest: str = ''

    def as_dict(self) -> dict[str, Any]:
        return {
            'epoch': self.epoch,
            'policy': self.policy,
            'created_at': self.created_at,
            'retained_revision_ids': list(self.retained_revision_ids),
            'retained_segment_ids': list(self.retained_segment_ids),
            'candidate_segments': [
                {
                    'checkpoint_id': s.checkpoint_id,
                    'checkpoint_generation': s.checkpoint_generation,
                    'revision_ids': list(s.revision_ids),
                    'generations': list(s.generations),
                    'snapshot_object_ids': list(s.snapshot_object_ids),
                }
                for s in self.candidate_segments
            ],
            'uncertain_segment_ids': list(self.uncertain_segment_ids),
            'orphan_snapshot_ids': list(self.orphan_snapshot_ids),
            'orphan_checkpoint_ids': list(self.orphan_checkpoint_ids),
            'digest': self.digest,
        }


@dataclass
class GCResult:
    plan: GCPlan
    tombstoned: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    deleted_quarantine_entries: int = 0
    cancelled_tombstones: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            'plan': self.plan.as_dict(),
            'tombstoned': list(self.tombstoned),
            'quarantined': list(self.quarantined),
            'deleted_quarantine_entries': int(self.deleted_quarantine_entries),
            'cancelled_tombstones': list(self.cancelled_tombstones),
            'skipped': list(self.skipped),
        }


def normalize_history_retention(value: Any) -> str:
    text = str(value or DEFAULT_HISTORY_RETENTION).strip().lower()
    if text not in HISTORY_RETENTION_VALUES:
        raise ValueError('HISTORY_RETENTION_INVALID')
    return text


def retention_rank(value: Any) -> int:
    value = normalize_history_retention(value)
    return {'30_days': 0, '90_days': 1, '1_year': 2, 'forever': 3}[value]


def conservative_retention(values: Iterable[Any], default: str = DEFAULT_HISTORY_RETENTION) -> str:
    normalized = []
    for value in values:
        try:
            normalized.append(normalize_history_retention(value))
        except ValueError:
            continue
    if not normalized:
        return normalize_history_retention(default)
    return max(normalized, key=retention_rank)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _parse_created_at(value: Any) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _plan_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _segment_from_rows(checkpoint_id: str, rows: list[dict[str, Any]]) -> Segment:
    ordered = sorted(rows, key=lambda r: int(r['generation_end']))
    checkpoint_generations = {int(r['checkpoint_generation']) for r in ordered}
    if len(checkpoint_generations) != 1:
        raise HistoryGCCorruption('HISTORY_GC_SEGMENT_CHECKPOINT_GENERATION_AMBIGUOUS')
    return Segment(
        checkpoint_id=str(checkpoint_id),
        checkpoint_generation=next(iter(checkpoint_generations)),
        revision_ids=tuple(str(r['revision_id']) for r in ordered),
        generations=tuple(int(r['generation_end']) for r in ordered),
        snapshot_object_ids=tuple(str(r['snapshot_object_id']) for r in ordered),
        created_at_values=tuple(str(r.get('created_at') or '') for r in ordered),
    )


def _retained_ids(rows: list[dict[str, Any]], policy: str, now_utc: datetime) -> set[str]:
    policy = normalize_history_retention(policy)
    if not rows:
        return set()
    newest = max(rows, key=lambda r: int(r['generation_end']))
    retained = {str(newest['revision_id'])}
    if policy == 'forever':
        retained.update(str(row['revision_id']) for row in rows)
        return retained
    cutoff = now_utc - timedelta(days=_RETENTION_DAYS[policy])
    for row in rows:
        created = _parse_created_at(row.get('created_at'))
        # Wall-clock metadata chooses retention only when trustworthy. Ambiguity is
        # fail-safe: malformed/future timestamps keep data rather than destroy it.
        if created is None or created > now_utc + timedelta(minutes=5) or created >= cutoff:
            retained.add(str(row['revision_id']))
    return retained


def _build_plan_from_verified_rows(
    rows: list[dict[str, Any]],
    *,
    policy: str,
    epoch: int,
    now_utc: datetime,
    physical_snapshot_ids: Iterable[str] = (),
    physical_checkpoint_ids: Iterable[str] = (),
) -> GCPlan:
    policy = normalize_history_retention(policy)
    rows = sorted((dict(r) for r in rows), key=lambda r: int(r['generation_end']))
    retained_ids = _retained_ids(rows, policy, now_utc)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row['checkpoint_id']), []).append(row)
    segments = [_segment_from_rows(cid, segment_rows) for cid, segment_rows in grouped.items()]
    # v1 intentionally promotes any retained revision to its whole bounded checkpoint
    # segment. This keeps lineage semantics boring and avoids introducing mutable stubs.
    retained_segment_ids = {
        segment.checkpoint_id
        for segment in segments
        if any(rid in retained_ids for rid in segment.revision_ids)
    }
    candidate_segments = tuple(sorted(
        (segment for segment in segments if segment.checkpoint_id not in retained_segment_ids),
        key=lambda s: (s.oldest_generation, s.checkpoint_id),
    ))
    referenced_snapshots = {str(row['snapshot_object_id']) for row in rows}
    referenced_checkpoints = {str(row['checkpoint_id']) for row in rows}
    orphan_snapshots = tuple(sorted(set(str(x) for x in physical_snapshot_ids) - referenced_snapshots))
    orphan_checkpoints = tuple(sorted(set(str(x) for x in physical_checkpoint_ids) - referenced_checkpoints))
    digest_payload = {
        'epoch': int(epoch),
        'policy': policy,
        'retained_revision_ids': sorted(retained_ids),
        'retained_segment_ids': sorted(retained_segment_ids),
        'candidate_segments': [
            {
                'checkpoint_id': s.checkpoint_id,
                'generations': list(s.generations),
                'revision_ids': list(s.revision_ids),
                'snapshot_object_ids': list(s.snapshot_object_ids),
            }
            for s in candidate_segments
        ],
        'orphan_snapshot_ids': list(orphan_snapshots),
        'orphan_checkpoint_ids': list(orphan_checkpoints),
    }
    return GCPlan(
        epoch=int(epoch),
        policy=policy,
        created_at=_iso(now_utc),
        retained_revision_ids=tuple(sorted(retained_ids)),
        retained_segment_ids=tuple(sorted(retained_segment_ids)),
        candidate_segments=candidate_segments,
        orphan_snapshot_ids=orphan_snapshots,
        orphan_checkpoint_ids=orphan_checkpoints,
        digest=_plan_digest(digest_payload),
    )


def plan_local_history_gc(store: LocalRevisionStore, policy: str, *, epoch: int = 1, now_utc: datetime | None = None) -> GCPlan:
    now_utc = (now_utc or _utc_now()).astimezone(timezone.utc)
    with store._lock:
        # Any ambiguous/corrupt retained publication makes deletion unsafe. Unlike
        # normal History listing, GC is never allowed to skip malformed authority.
        try:
            published = store._published_revisions(fail_on_uncertain_head=True)
            verified_rows = []
            for published_row in published:
                rid = str(published_row.manifest['revision_id'])
                verified_rows.append(dict(store.verify_revision(rid)['revision']))
        except (RevisionStoreError, RevisionStoreCorruption) as exc:
            raise HistoryGCCorruption('HISTORY_GC_REPOSITORY_UNCERTAIN') from exc

        physical_snapshots = [p.name for p in store.objects_dir.iterdir() if p.is_dir() and re.fullmatch(r'g\d{20}-[0-9a-f]{32}', p.name)]
        physical_checkpoints = []
        for path in store.checkpoints_dir.glob('*.checkpoint.json'):
            match = re.match(r'^g\d{20}-([0-9a-f]{32})\.checkpoint\.json$', path.name)
            if match:
                physical_checkpoints.append(match.group(1))
        return _build_plan_from_verified_rows(
            verified_rows,
            policy=policy,
            epoch=epoch,
            now_utc=now_utc,
            physical_snapshot_ids=physical_snapshots,
            physical_checkpoint_ids=physical_checkpoints,
        )


def plan_remote_history_gc(rows: list[dict[str, Any]], policy: str, *, epoch: int = 1, now_utc: datetime | None = None) -> GCPlan:
    """Plan from already-verified remote publications.

    Provider listings may discover publications, but callers must only pass rows that
    RemoteHistoryRepository fully verified. Missing listing entries are never proof of
    death; the resulting behavior under an incomplete listing is a storage leak.
    """
    now_utc = (now_utc or _utc_now()).astimezone(timezone.utc)
    manifests = [dict(row['local_revision_manifest']) for row in rows]
    return _build_plan_from_verified_rows(manifests, policy=policy, epoch=epoch, now_utc=now_utc)


def _durable_replace_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(_canonical(value))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        _fsync_parent(path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _empty_state() -> dict[str, Any]:
    return {'gc_format': GC_FORMAT, 'gc_version': GC_VERSION, 'epoch': 0, 'candidates': {}, 'quarantine': {}}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise HistoryGCCorruption('HISTORY_GC_STATE_CORRUPT') from exc
    if not isinstance(state, dict) or state.get('gc_format') != GC_FORMAT or int(state.get('gc_version') or 0) != GC_VERSION:
        raise HistoryGCCorruption('HISTORY_GC_STATE_FORMAT_INVALID')
    if not isinstance(state.get('candidates'), dict) or not isinstance(state.get('quarantine'), dict):
        raise HistoryGCCorruption('HISTORY_GC_STATE_STRUCTURE_INVALID')
    state['epoch'] = int(state.get('epoch') or 0)
    return state


def _candidate_key(segment: Segment) -> str:
    return f'segment:{segment.checkpoint_id}'


def _orphan_object_key(object_id: str) -> str:
    return f'orphan-object:{object_id}'


def _orphan_checkpoint_key(checkpoint_id: str) -> str:
    return f'orphan-checkpoint:{checkpoint_id}'


def _candidate_payload(segment: Segment, plan: GCPlan, now_utc: datetime) -> dict[str, Any]:
    return {
        'kind': 'segment',
        'checkpoint_id': segment.checkpoint_id,
        'checkpoint_generation': segment.checkpoint_generation,
        'revision_ids': list(segment.revision_ids),
        'generations': list(segment.generations),
        'snapshot_object_ids': list(segment.snapshot_object_ids),
        'first_seen_epoch': plan.epoch,
        'last_seen_epoch': plan.epoch,
        'first_seen_at': _iso(now_utc),
        'last_seen_at': _iso(now_utc),
        'scan_count': 1,
        'plan_digest': plan.digest,
    }


def _eligible(candidate: dict[str, Any], *, epoch: int, now_utc: datetime, grace_seconds: float) -> bool:
    first_epoch = int(candidate.get('first_seen_epoch') or 0)
    scan_count = int(candidate.get('scan_count') or 0)
    first_seen = _parse_created_at(candidate.get('first_seen_at'))
    if first_epoch <= 0 or first_seen is None:
        return False
    return scan_count >= MINIMUM_GC_SCANS and epoch > first_epoch and (now_utc - first_seen).total_seconds() >= float(grace_seconds)


def _move_file_to_quarantine(source: Path, destination: Path) -> None:
    source = Path(source)
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise HistoryGCCorruption('HISTORY_GC_QUARANTINE_COLLISION')
    os.replace(source, destination)
    _fsync_parent(source)
    _fsync_parent(destination)


def _quarantine_local_segment(store: LocalRevisionStore, segment: Segment, quarantine_root: Path) -> None:
    """Hide authority newest→oldest, then dependent checkpoint/object bytes.

    If the process dies while authority is being removed, the remaining visible
    revisions are an older valid prefix. Payloads are not touched until all authority
    manifests from this segment are absent from the live repository.
    """
    revisions_q = quarantine_root / 'revisions'
    checkpoints_q = quarantine_root / 'checkpoints'
    objects_q = quarantine_root / 'objects'

    for generation, revision_id in sorted(zip(segment.generations, segment.revision_ids), reverse=True):
        source = store._revision_path(int(generation), str(revision_id))
        _move_file_to_quarantine(source, revisions_q / source.name)

    for generation, revision_id in zip(segment.generations, segment.revision_ids):
        if store._revision_path(int(generation), str(revision_id)).exists():
            raise HistoryGCCorruption('HISTORY_GC_AUTHORITY_STILL_VISIBLE')

    checkpoint_source = store._checkpoint_path(int(segment.checkpoint_generation), str(segment.checkpoint_id))
    _move_file_to_quarantine(checkpoint_source, checkpoints_q / checkpoint_source.name)

    for object_id in segment.snapshot_object_ids:
        source = store.objects_dir / object_id
        if source.exists():
            destination = objects_q / object_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise HistoryGCCorruption('HISTORY_GC_QUARANTINE_COLLISION')
            os.replace(source, destination)
            _fsync_parent(source)
            _fsync_parent(destination)


def _quarantine_orphan_snapshot(store: LocalRevisionStore, object_id: str, quarantine_root: Path) -> None:
    source = store.objects_dir / object_id
    if not source.exists():
        return
    destination = quarantine_root / 'orphans' / 'objects' / object_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    _fsync_parent(source)
    _fsync_parent(destination)


def _quarantine_orphan_checkpoint(store: LocalRevisionStore, checkpoint_id: str, quarantine_root: Path) -> None:
    matches = list(store.checkpoints_dir.glob(f'g*-{checkpoint_id}.checkpoint.json'))
    if len(matches) > 1:
        raise HistoryGCCorruption('HISTORY_GC_ORPHAN_CHECKPOINT_AMBIGUOUS')
    if matches:
        _move_file_to_quarantine(matches[0], quarantine_root / 'orphans' / 'checkpoints' / matches[0].name)


def run_local_history_gc(
    store: LocalRevisionStore,
    policy: str,
    *,
    now_utc: datetime | None = None,
    grace_seconds: float = DEFAULT_GC_GRACE_SECONDS,
    stage_hook=None,
) -> GCResult:
    """Run one conservative local GC epoch.

    The first qualifying scan only records candidates. A later independent scan after
    the grace period may quarantine them. Quarantine itself is retained for another
    grace period before physical removal. Metadata corruption causes no deletion.
    """
    now_utc = (now_utc or _utc_now()).astimezone(timezone.utc)
    gc_root = store.root / 'gc'
    state_path = gc_root / 'state.json'
    quarantine_base = gc_root / 'quarantine'
    with store._lock:
        state = _load_state(state_path)
        epoch = int(state['epoch']) + 1
        plan = plan_local_history_gc(store, policy, epoch=epoch, now_utc=now_utc)
        result = GCResult(plan)
        candidates = dict(state.get('candidates') or {})
        live_keys: set[str] = set()

        for segment in plan.candidate_segments:
            key = _candidate_key(segment)
            live_keys.add(key)
            existing = candidates.get(key)
            if isinstance(existing, dict):
                # Candidate identity changing under the same checkpoint is uncertain.
                if list(existing.get('revision_ids') or []) != list(segment.revision_ids) or list(existing.get('snapshot_object_ids') or []) != list(segment.snapshot_object_ids):
                    result.skipped.append(f'{key}:identity_changed')
                    candidates.pop(key, None)
                    continue
                existing = dict(existing)
                existing['last_seen_epoch'] = epoch
                existing['last_seen_at'] = _iso(now_utc)
                existing['scan_count'] = int(existing.get('scan_count') or 0) + 1
                existing['plan_digest'] = plan.digest
                candidates[key] = existing
            else:
                candidates[key] = _candidate_payload(segment, plan, now_utc)
                result.tombstoned.append(key)

        for object_id in plan.orphan_snapshot_ids:
            key = _orphan_object_key(object_id)
            live_keys.add(key)
            existing = candidates.get(key)
            if isinstance(existing, dict):
                existing = dict(existing);existing['last_seen_epoch'] = epoch;existing['last_seen_at'] = _iso(now_utc);existing['scan_count'] = int(existing.get('scan_count') or 0) + 1;candidates[key] = existing
            else:
                candidates[key] = {'kind':'orphan-object','object_id':object_id,'first_seen_epoch':epoch,'last_seen_epoch':epoch,'first_seen_at':_iso(now_utc),'last_seen_at':_iso(now_utc),'scan_count':1,'plan_digest':plan.digest};result.tombstoned.append(key)

        for checkpoint_id in plan.orphan_checkpoint_ids:
            key = _orphan_checkpoint_key(checkpoint_id)
            live_keys.add(key)
            existing = candidates.get(key)
            if isinstance(existing, dict):
                existing = dict(existing);existing['last_seen_epoch'] = epoch;existing['last_seen_at'] = _iso(now_utc);existing['scan_count'] = int(existing.get('scan_count') or 0) + 1;candidates[key] = existing
            else:
                candidates[key] = {'kind':'orphan-checkpoint','checkpoint_id':checkpoint_id,'first_seen_epoch':epoch,'last_seen_epoch':epoch,'first_seen_at':_iso(now_utc),'last_seen_at':_iso(now_utc),'scan_count':1,'plan_digest':plan.digest};result.tombstoned.append(key)

        # A retention change or newly published revision can make an earlier candidate
        # live again. Cancel it rather than carrying stale destructive intent forward.
        for key in list(candidates):
            if key not in live_keys:
                candidates.pop(key, None)
                result.cancelled_tombstones.append(key)

        quarantine_state = dict(state.get('quarantine') or {})
        # Persist the recomputed plan/candidate set before any mutation. An interrupted
        # process never resumes a stale delete list blindly; the next run replans.
        state = {
            'gc_format': GC_FORMAT,
            'gc_version': GC_VERSION,
            'epoch': epoch,
            'last_plan_digest': plan.digest,
            'last_plan_at': _iso(now_utc),
            'candidates': candidates,
            'quarantine': quarantine_state,
        }
        _durable_replace_json(state_path, state)
        if stage_hook:stage_hook('LOCAL_GC_PLAN_PERSISTED',{'epoch':epoch,'plan_digest':plan.digest,'candidate_count':len(candidates)})

        segments_by_key = {_candidate_key(segment): segment for segment in plan.candidate_segments}
        for key, candidate in list(candidates.items()):
            if not _eligible(candidate, epoch=epoch, now_utc=now_utc, grace_seconds=grace_seconds):
                continue
            qid = f'e{epoch:08d}-{uuid.uuid4().hex}'
            qroot = quarantine_base / qid
            kind = str(candidate.get('kind') or '')
            if stage_hook:stage_hook('BEFORE_LOCAL_QUARANTINE',{'epoch':epoch,'candidate_key':key,'kind':kind})
            if kind == 'segment':
                segment = segments_by_key.get(key)
                if segment is None:
                    continue
                _quarantine_local_segment(store, segment, qroot)
            elif kind == 'orphan-object':
                _quarantine_orphan_snapshot(store, str(candidate.get('object_id') or ''), qroot)
            elif kind == 'orphan-checkpoint':
                _quarantine_orphan_checkpoint(store, str(candidate.get('checkpoint_id') or ''), qroot)
            else:
                continue
            quarantine_state[qid] = {
                'candidate_key': key,
                'kind': kind,
                'quarantined_at': _iso(now_utc),
                'quarantined_epoch': epoch,
            }
            candidates.pop(key, None)
            result.quarantined.append(key)
            if stage_hook:stage_hook('LOCAL_QUARANTINED',{'epoch':epoch,'candidate_key':key,'kind':kind})

        # Physical removal is deliberately one more grace boundary after quarantine.
        for qid, qmeta in list(quarantine_state.items()):
            quarantined_at = _parse_created_at(qmeta.get('quarantined_at')) if isinstance(qmeta, dict) else None
            qepoch = int(qmeta.get('quarantined_epoch') or 0) if isinstance(qmeta, dict) else 0
            if quarantined_at is None or epoch <= qepoch or (now_utc - quarantined_at).total_seconds() < float(grace_seconds):
                continue
            target = quarantine_base / qid
            if target.exists():
                shutil.rmtree(target, ignore_errors=False)
                _fsync_parent(target)
            quarantine_state.pop(qid, None)
            result.deleted_quarantine_entries += 1

        state.update({'candidates': candidates, 'quarantine': quarantine_state})
        _durable_replace_json(state_path, state)
        return result


def run_remote_history_gc(
    *,
    repository,
    delete_capability: HistoryGCRemoteDelete,
    state_path: Path,
    policy: str,
    now_utc: datetime | None = None,
    grace_seconds: float = DEFAULT_GC_GRACE_SECONDS,
    stage_hook=None,
) -> GCResult:
    """Run one remote GC epoch using only verified immutable publication authority.

    Provider listing is discovery, never negative authority. Repository verification and
    writer authority are checked again immediately before publication deletion.
    """
    now_utc = (now_utc or _utc_now()).astimezone(timezone.utc)
    repository.assert_writer_authority()
    state_path = Path(state_path)
    state = _load_state(state_path)
    epoch = int(state['epoch']) + 1
    try:
        rows = repository.gc_verified_chain()
    except Exception as exc:
        raise HistoryGCCorruption('REMOTE_HISTORY_GC_REPOSITORY_UNCERTAIN') from exc
    plan = plan_remote_history_gc(rows, policy, epoch=epoch, now_utc=now_utc)
    result = GCResult(plan)
    candidates = dict(state.get('candidates') or {})
    live = set()
    by_key = {}
    for segment in plan.candidate_segments:
        key = _candidate_key(segment);live.add(key);by_key[key] = segment
        existing = candidates.get(key)
        if isinstance(existing, dict):
            if list(existing.get('revision_ids') or []) != list(segment.revision_ids):
                candidates.pop(key, None);result.skipped.append(f'{key}:identity_changed');continue
            existing = dict(existing);existing['last_seen_epoch'] = epoch;existing['last_seen_at'] = _iso(now_utc);existing['scan_count'] = int(existing.get('scan_count') or 0) + 1;existing['plan_digest'] = plan.digest;candidates[key] = existing
        else:
            candidates[key] = _candidate_payload(segment, plan, now_utc);result.tombstoned.append(key)
    for key in list(candidates):
        if key not in live:
            candidates.pop(key, None);result.cancelled_tombstones.append(key)

    state = {'gc_format':GC_FORMAT,'gc_version':GC_VERSION,'epoch':epoch,'last_plan_digest':plan.digest,'last_plan_at':_iso(now_utc),'candidates':candidates,'quarantine':{}}
    _durable_replace_json(state_path, state)
    if stage_hook:stage_hook('REMOTE_GC_PLAN_PERSISTED',{'epoch':epoch,'plan_digest':plan.digest,'candidate_count':len(candidates)})

    for key, candidate in list(candidates.items()):
        if not _eligible(candidate, epoch=epoch, now_utc=now_utc, grace_seconds=grace_seconds):
            continue
        segment = by_key.get(key)
        if segment is None:
            continue
        # Re-read authority after the grace period and before each destructive batch.
        repository.assert_writer_authority()
        fresh_rows = repository.gc_verified_chain()
        fresh_plan = plan_remote_history_gc(fresh_rows, policy, epoch=epoch, now_utc=now_utc)
        fresh = next((s for s in fresh_plan.candidate_segments if s.checkpoint_id == segment.checkpoint_id), None)
        if fresh is None or fresh.revision_ids != segment.revision_ids:
            result.skipped.append(f'{key}:replan_changed')
            continue
        if stage_hook:stage_hook('BEFORE_REMOTE_AUTHORITY_DELETE',{'epoch':epoch,'candidate_key':key,'checkpoint_id':segment.checkpoint_id})
        # Remove authority newest→oldest. If the process dies mid-loop, the remaining
        # remote visible revisions are an older valid prefix. Payload bytes remain.
        for generation in sorted(segment.generations, reverse=True):
            removed=int(delete_capability.delete_revision_publication(int(generation)) or 0)
            if removed < 1:
                raise HistoryGCAuthorityUncertain('REMOTE_HISTORY_GC_PUBLICATION_DELETE_UNCONFIRMED')
        # Only after every publication is absent may dependent bytes disappear.
        for generation in segment.generations:
            if repository.publication_exists(int(generation)):
                raise HistoryGCCorruption('REMOTE_HISTORY_GC_AUTHORITY_STILL_VISIBLE')
        delete_capability.delete_checkpoint(segment.checkpoint_id)
        for snapshot_id in segment.snapshot_object_ids:
            delete_capability.delete_snapshot(snapshot_id)
        candidates.pop(key, None)
        result.quarantined.append(key)
        if stage_hook:stage_hook('REMOTE_SEGMENT_DELETED',{'epoch':epoch,'candidate_key':key,'checkpoint_id':segment.checkpoint_id})

    state.update({'candidates': candidates})
    _durable_replace_json(state_path, state)
    return result
