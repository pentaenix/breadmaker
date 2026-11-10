"""Pure, unconnected stock coverage; no collection, publication or Admin hook.

Known per-project values do not establish fleet completeness. A separate trusted
verifier must prove the full active root set AND exact generations at one
consistent observation point. Non-atomic inventory COUNT equality is not proof.
No verifier implementation, account identifiers, project list export, persistence
or free-form output exists here. All text uses one versioned definition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hosted.security.canvas_projection import (
    CanvasProjection, ProjectRootObservation, eligible_canvas_count,
)
from hosted.security.text_stock import (
    MAX_COUNT, TEXT_DEFINITION, TextProjection, eligible_text_counts,
)

MAX_PROJECT_OBSERVATIONS = 4096


class StockCoverageError(ValueError):
    """Fixed errors only; never identifiers or provider payloads."""


@dataclass(frozen=True)
class ProjectStockObservation:
    """Private trusted metadata plus optional same-generation projections."""

    root: ProjectRootObservation = field(repr=False)
    text: TextProjection | None = field(default=None, repr=False)
    canvas: CanvasProjection | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.root) is not ProjectRootObservation:
            raise StockCoverageError("STOCK_COVERAGE_ROOT_INVALID")
        if self.text is not None and type(self.text) is not TextProjection:
            raise StockCoverageError("STOCK_COVERAGE_TEXT_INVALID")
        if self.canvas is not None and type(self.canvas) is not CanvasProjection:
            raise StockCoverageError("STOCK_COVERAGE_CANVAS_INVALID")


@dataclass(frozen=True)
class CompleteActiveProjectSet:
    """Verifier assertion, not a public/request constructor for authority.

    Roots must identify the entire active set at a consistent observation point.
    This type cannot prove that fact or authorize reading anything. Empty roots
    must be proved empty; absence of observations is never evidence of zero.
    """

    roots: tuple[ProjectRootObservation, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.roots) is not tuple or len(self.roots) > MAX_PROJECT_OBSERVATIONS:
            raise StockCoverageError("STOCK_COVERAGE_PROOF_INVALID")
        if any(type(root) is not ProjectRootObservation or root.status != "active" for root in self.roots):
            raise StockCoverageError("STOCK_COVERAGE_PROOF_INVALID")
        if len({root.project_id for root in self.roots}) != len(self.roots):
            raise StockCoverageError("STOCK_COVERAGE_DUPLICATE_ROOT")


class TrustedActiveProjectSet(Protocol):
    """Server-only verifier of complete exact-generation active-parent coverage.

    Must establish authority, completeness and consistency independently, never
    by trusting request flags, matching a separate COUNT, or reading manuscripts.
    No implementation is supplied. Provider exceptions must not be logged.
    """

    def require_complete_active_set(self) -> CompleteActiveProjectSet: ...


@dataclass(frozen=True)
class StockCoverage:
    """Bounded scalar-only result, not a wire format or v1 inventory extension."""

    scope_complete: bool
    observed_roots: int
    active_roots: int
    excluded_roots: int
    unknown_roots: int
    text_covered_roots: int
    text_missing_roots: int
    canvas_covered_roots: int
    canvas_missing_roots: int
    current_cloud_projects: int | None
    current_cloud_words: int | None
    current_cloud_characters: int | None
    current_cloud_canvases: int | None
    definition: str = TEXT_DEFINITION

    def scalars(self) -> dict[str, int | None]:
        """No identifiers, content, per-user history or free-form fields."""
        return {name: getattr(self, name) for name in (
            "observed_roots", "active_roots", "excluded_roots", "unknown_roots",
            "text_covered_roots", "text_missing_roots", "canvas_covered_roots",
            "canvas_missing_roots", "current_cloud_projects", "current_cloud_words",
            "current_cloud_characters", "current_cloud_canvases",
        )}


def summarize_stock(
    observations: tuple[ProjectStockObservation, ...], *,
    proof: TrustedActiveProjectSet | None = None, enabled: bool = False,
) -> StockCoverage | None:
    """Default-off; no input/proof inspection. Unknown totals never become zero.

    Bounded scope: more than 4096 observed roots requires a separately reviewed
    complete aggregation design, not batching this API and adding partial sums.
    Overflow also produces unknown totals; observed coverage counts remain exact.
    """
    if enabled is not True:
        return None
    if type(observations) is not tuple or len(observations) > MAX_PROJECT_OBSERVATIONS:
        raise StockCoverageError("STOCK_COVERAGE_OBSERVATIONS_INVALID")
    if any(type(item) is not ProjectStockObservation for item in observations):
        raise StockCoverageError("STOCK_COVERAGE_OBSERVATIONS_INVALID")
    if len({item.root.project_id for item in observations}) != len(observations):
        raise StockCoverageError("STOCK_COVERAGE_DUPLICATE_ROOT")
    active = tuple(item for item in observations if item.root.status == "active")
    unknown = sum(item.root.status is None for item in observations)
    complete = False
    if proof is not None:
        try:
            assertion = proof.require_complete_active_set()
        except Exception:
            raise StockCoverageError("STOCK_COVERAGE_PROOF_REJECTED") from None
        if type(assertion) is not CompleteActiveProjectSet:
            raise StockCoverageError("STOCK_COVERAGE_PROOF_INVALID")
        expected = {(item.root.project_id, item.root.generation) for item in active}
        proven = {(root.project_id, root.generation) for root in assertion.roots}
        complete = not unknown and expected == proven
    texts = tuple(eligible_text_counts(item.text, item.root) for item in active)
    canvases = tuple(eligible_canvas_count(item.canvas, item.root) for item in active)
    text_known = sum(value is not None for value in texts)
    canvas_known = sum(value is not None for value in canvases)
    words = characters = canvas_total = None
    if complete and text_known == len(active):
        word_sum = sum(value.words for value in texts)
        character_sum = sum(value.characters for value in texts)
        if max(word_sum, character_sum) <= MAX_COUNT:
            words, characters = word_sum, character_sum
    if complete and canvas_known == len(active):
        value = sum(canvases)
        if value <= MAX_COUNT:
            canvas_total = value
    return StockCoverage(
        complete, len(observations), len(active), len(observations) - len(active) - unknown,
        unknown, text_known, len(active) - text_known, canvas_known, len(active) - canvas_known,
        len(active) if complete else None, words, characters, canvas_total,
    )
