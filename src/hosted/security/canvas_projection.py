"""Pure, unconnected projection of current canvas roots within one project.

No store, query, clock, client, account ID, manuscript access or runtime hook.
A private count is tied to the exact project generation. Only a matching,
explicitly active root observation can expose its scalar value. This is not a
fleet total or a lifetime canvas-creation counter; canvas nodes, edges and saved
versions do not contribute. A missing baseline remains unknown.

CALLER TRUST: evidence must come from server-authorized transaction before/after
images, never request fields. New-empty seeding uses a trusted proof interface,
not a Boolean flag. There is deliberately no production proof implementation or
projection deserializer. These Python types cannot establish authorization or
prove completeness. The future caller must establish both and persist the
projection in the SAME transaction as the canvas mutation and root generation.

Every generation must be covered, including unrelated edits, Undo/Redo, root
lifecycle changes, imports and restore. Only g -> g+1 is supported. A skipped or
unexpected transition must invalidate coverage, not infer a delta, reuse an old
count or reseed. Existing/imported/restored projects without a verified baseline
stay unknown; this module provides no backfill or nonempty-baseline shortcut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id

MAX_CANVASES = 9_007_199_254_740_991
MAX_GENERATION = 9_007_199_254_740_991
MAX_CANVAS_ROOT_CHANGES = 128


class CanvasProjectionError(ValueError):
    """Structural failure without identifiers or payload content."""


def _generation(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_GENERATION:
        raise CanvasProjectionError("CANVAS_PROJECTION_GENERATION_INVALID")


def _scope(value: str) -> None:
    if type(value) is not str:
        raise CanvasProjectionError("CANVAS_PROJECTION_SCOPE_INVALID")
    try:
        require_project_id(value)
    except HostedValidationError:
        raise CanvasProjectionError("CANVAS_PROJECTION_SCOPE_INVALID") from None


def _count(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_CANVASES):
        raise CanvasProjectionError("CANVAS_PROJECTION_COUNT_INVALID")


@dataclass(frozen=True)
class EmptyProjectCreation:
    """Private assertion returned only by the trusted creation verifier.

    Generation one alone is NOT proof of emptiness: imports may publish a
    nonempty project at generation one. The verifier must independently prove
    the authorized creation has no existing, staged or planned canvas roots.
    """

    project_id: str = field(repr=False)
    generation: int = 1

    def __post_init__(self) -> None:
        _scope(self.project_id)
        if type(self.generation) is not int or self.generation != 1:
            raise CanvasProjectionError("CANVAS_PROJECTION_CREATION_INVALID")


class TrustedEmptyProjectCreation(Protocol):
    """Server-only capability, never implemented by request deserialization.

    Verify authority and the complete empty creation in the current transaction,
    including that no projection already exists. Return the exact creation's
    scope or raise. The interface alone is not a security mechanism. Future
    composition must sanitize errors and never log provider payloads. No runtime
    implementation is provided in this source slice.
    """

    def require_empty_creation(self) -> EmptyProjectCreation: ...


@dataclass(frozen=True, init=False)
class CanvasProjection:
    """Private generation-bound state. No public dict/JSON export is provided."""

    project_id: str = field(repr=False)
    generation: int
    canvas_count: int | None = field(repr=False)

    def __init__(self, *args, **kwargs) -> None:
        raise CanvasProjectionError("CANVAS_PROJECTION_CONSTRUCTION_PRIVATE")


def _projection(project_id: str, generation: int, count: int | None) -> CanvasProjection:
    # Internal state construction is not an authorization boundary. Keeping it
    # private prevents a convenience API for seeding existing/imported projects.
    _scope(project_id)
    _generation(generation)
    _count(count)
    result = object.__new__(CanvasProjection)
    object.__setattr__(result, "project_id", project_id)
    object.__setattr__(result, "generation", generation)
    object.__setattr__(result, "canvas_count", count)
    return result


def seed_empty_projection(proof: TrustedEmptyProjectCreation) -> CanvasProjection:
    verifier = getattr(proof, "require_empty_creation", None)
    if not callable(verifier):
        raise CanvasProjectionError("CANVAS_PROJECTION_CREATION_PROOF_REQUIRED")
    try:
        creation = verifier()
    except Exception:
        # A future verifier may encounter provider errors. Never preserve their
        # payload in a public error or log; this boundary emits a fixed code.
        raise CanvasProjectionError("CANVAS_PROJECTION_CREATION_PROOF_REJECTED") from None
    if type(creation) is not EmptyProjectCreation:
        raise CanvasProjectionError("CANVAS_PROJECTION_CREATION_PROOF_INVALID")
    return _projection(creation.project_id, creation.generation, 0)


def unknown_projection(*, project_id: str, generation: int) -> CanvasProjection:
    """Represent no baseline at an observed generation; never infer zero."""
    return _projection(project_id, generation, None)


@dataclass(frozen=True)
class CanvasRootChange:
    """One root's existence before and after a trusted transaction, not content."""

    canvas_id: str = field(repr=False)
    existed_before: bool
    exists_after: bool

    def __post_init__(self) -> None:
        if type(self.canvas_id) is not str:
            raise CanvasProjectionError("CANVAS_PROJECTION_ROOT_INVALID")
        try:
            require_opaque_id(self.canvas_id)
        except HostedValidationError:
            raise CanvasProjectionError("CANVAS_PROJECTION_ROOT_INVALID") from None
        if type(self.existed_before) is not bool or type(self.exists_after) is not bool:
            raise CanvasProjectionError("CANVAS_PROJECTION_EXISTENCE_INVALID")


@dataclass(frozen=True)
class CanvasMutation:
    """Complete root-existence changes for exactly one project-generation step.

    Empty changes carry the count through a generation whose canvas-root set is
    proven unchanged. Mutation evidence is internal, not a client delta API.
    """

    project_id: str = field(repr=False)
    before_generation: int
    after_generation: int
    changes: tuple[CanvasRootChange, ...] = ()

    def __post_init__(self) -> None:
        _scope(self.project_id)
        _generation(self.before_generation)
        _generation(self.after_generation)
        if self.after_generation != self.before_generation + 1:
            raise CanvasProjectionError("CANVAS_PROJECTION_TRANSITION_INVALID")
        if type(self.changes) is not tuple or len(self.changes) > MAX_CANVAS_ROOT_CHANGES:
            raise CanvasProjectionError("CANVAS_PROJECTION_CHANGES_INVALID")
        if any(type(change) is not CanvasRootChange for change in self.changes):
            raise CanvasProjectionError("CANVAS_PROJECTION_CHANGES_INVALID")
        if len({change.canvas_id for change in self.changes}) != len(self.changes):
            raise CanvasProjectionError("CANVAS_PROJECTION_DUPLICATE_ROOT")


def advance_projection(current: CanvasProjection, mutation: CanvasMutation) -> CanvasProjection:
    """Apply a proven g -> g+1 once; duplicate/out-of-order steps conflict."""
    if type(current) is not CanvasProjection or type(mutation) is not CanvasMutation:
        raise CanvasProjectionError("CANVAS_PROJECTION_EVIDENCE_INVALID")
    if current.project_id != mutation.project_id or current.generation != mutation.before_generation:
        raise CanvasProjectionError("CANVAS_PROJECTION_CONFLICT")
    count = current.canvas_count
    if count is not None:
        if sum(change.existed_before for change in mutation.changes) > count:
            raise CanvasProjectionError("CANVAS_PROJECTION_BASELINE_CONFLICT")
        count += sum(int(change.exists_after) - int(change.existed_before) for change in mutation.changes)
        _count(count)
    return _projection(current.project_id, mutation.after_generation, count)


@dataclass(frozen=True)
class ProjectRootObservation:
    """Current root metadata, read by a future trusted caller without content."""

    project_id: str = field(repr=False)
    generation: int
    status: str | None

    def __post_init__(self) -> None:
        _scope(self.project_id)
        _generation(self.generation)
        if self.status not in (None, "active", "deleted", "erasure_pending", "staging"):
            raise CanvasProjectionError("CANVAS_PROJECTION_ROOT_STATUS_INVALID")


def eligible_canvas_count(
    projection: CanvasProjection | None, root: ProjectRootObservation | None,
) -> int | None:
    """Only scalar output: unknown/ineligible/stale/missing becomes None.

    Inactive roots are not returned as a known zero. A future aggregate owner
    must establish complete active-parent coverage before claiming a fleet sum.
    """
    if projection is None or root is None:
        return None
    if type(projection) is not CanvasProjection or type(root) is not ProjectRootObservation:
        raise CanvasProjectionError("CANVAS_PROJECTION_EVIDENCE_INVALID")
    if (root.status != "active" or root.project_id != projection.project_id
            or root.generation != projection.generation):
        return None
    return projection.canvas_count
