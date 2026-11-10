"""Unconnected saved-text stock, with no store, query, history or runtime hook.

Words follow Project Center's primary-Manuscript/non-image scope and Python
Unicode whitespace splitting. Characters deliberately differ: the sum of saved
text code points, including whitespace, with no separators, trimming or Unicode
normalization. Neither definition claims parity with every editor view. Markup,
titles, image text, secondary tabs and history are excluded. Unicode is pinned.

CALLER TRUST: eligibility and complete transaction before/after evidence must
come from authorized server state, never request flags or arbitrary deltas.
Python types/proof interfaces are not security mechanisms. Scope changes
(including primary tab and block kind) require ALL affected before/after images;
otherwise invalidate coverage. There is no production verifier or nonempty seed.
Every root generation must be covered atomically with a future private projection
write, including unrelated edits, delete/restore and Undo/Redo. Existing/imported
roots remain unknown; this module must never be used to query/backfill content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
import unicodedata

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.ids import require_opaque_id, require_project_id
from hosted.security.canvas_projection import ProjectRootObservation

MAX_COUNT = 9_007_199_254_740_991
MAX_BLOCK_TEXT_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHANGES = 150
UNICODE_VERSION = "16.0.0"  # Python 3.14, the repository's pinned CI runtime.
TEXT_DEFINITION = "primary_manuscript_non_image_saved_text_v1_ucd16_0_0"


class TextStockError(ValueError):
    """Fixed structural codes, never text, identifiers or provider exceptions."""


def _scope(project_id: str) -> None:
    if type(project_id) is not str:
        raise TextStockError("TEXT_STOCK_SCOPE_INVALID")
    try:
        require_project_id(project_id)
    except HostedValidationError:
        raise TextStockError("TEXT_STOCK_SCOPE_INVALID") from None


def _number(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= MAX_COUNT:
        raise TextStockError("TEXT_STOCK_NUMBER_INVALID")


@dataclass(frozen=True)
class TextCounts:
    """Private contribution; zero blocks means absent or provably excluded."""

    words: int
    characters: int
    blocks: int
    definition: str = TEXT_DEFINITION

    def __post_init__(self) -> None:
        for value in (self.words, self.characters, self.blocks):
            _number(value)
        if self.definition != TEXT_DEFINITION or type(self.definition) is not str:
            raise TextStockError("TEXT_STOCK_DEFINITION_INVALID")
        if self.words > self.characters or (self.blocks == 0 and self.characters != 0):
            raise TextStockError("TEXT_STOCK_COUNTS_INVALID")


ZERO_TEXT = TextCounts(0, 0, 0)


@dataclass(frozen=True)
class SavedTextBlock:
    """Already-authorized save input, NOT a loader or request-deserialization API.

    Trusted tab membership must establish primary-Manuscript eligibility. None
    metadata means unknown, not excluded. A known SQL-like null text_content is
    empty, matching Project Center; a missing/unreadable block is not this type.
    No validation reads content at construction, so the disabled gate is inert.
    """

    text_content: str | None = field(repr=False)
    is_primary_manuscript: bool | None
    block_kind: str | None


def measure_saved_block(block: SavedTextBlock | None, *, enabled: bool = False) -> TextCounts | None:
    """Default-off: do not even inspect the input when disabled. No IO/logging."""
    if enabled is not True:
        return None
    if block is None:
        return None
    if type(block) is not SavedTextBlock:
        raise TextStockError("TEXT_STOCK_BLOCK_INVALID")
    primary, kind = block.is_primary_manuscript, block.block_kind
    if primary is not None and type(primary) is not bool:
        raise TextStockError("TEXT_STOCK_ELIGIBILITY_INVALID")
    if kind is not None and (type(kind) is not str or not 1 <= len(kind) <= 64):
        raise TextStockError("TEXT_STOCK_ELIGIBILITY_INVALID")
    if primary is False or kind == "image":
        return ZERO_TEXT  # Do not inspect excluded payloads.
    if primary is None or kind is None:
        return None
    if unicodedata.unidata_version != UNICODE_VERSION:
        raise TextStockError("TEXT_STOCK_UNICODE_VERSION_UNSUPPORTED")
    text = block.text_content
    if text is None:
        text = ""
    if type(text) is not str or len(text) > MAX_BLOCK_TEXT_BYTES:
        raise TextStockError("TEXT_STOCK_TEXT_INVALID")
    try:
        size = len(text.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise TextStockError("TEXT_STOCK_TEXT_INVALID") from None
    if size > MAX_BLOCK_TEXT_BYTES:
        raise TextStockError("TEXT_STOCK_TEXT_TOO_LARGE")
    # Same token boundaries as str.split() without allocating a token list.
    words, in_word = 0, False
    for character in text:
        if character.isspace():
            in_word = False
        elif not in_word:
            words += 1
            in_word = True
    return TextCounts(words, len(text), 1)


@dataclass(frozen=True)
class EmptyTextCreation:
    """Trusted new-empty creation, never an imported root merely at generation 1."""

    project_id: str = field(repr=False)
    generation: int = 1

    def __post_init__(self) -> None:
        _scope(self.project_id)
        if type(self.generation) is not int or self.generation != 1:
            raise TextStockError("TEXT_STOCK_CREATION_INVALID")


class TrustedEmptyTextCreation(Protocol):
    """Verify new creation, no prior projection and no eligible planned blocks.

    No production implementation exists. The verifier must also prove trusted
    primary-tab/block-kind metadata. It must not retrieve manuscripts/backfill.
    """

    def require_empty_text_creation(self) -> EmptyTextCreation: ...


@dataclass(frozen=True, init=False)
class TextProjection:
    """Private generation-bound state, no public JSON/constructor or reseeding."""

    project_id: str = field(repr=False)
    generation: int
    counts: TextCounts | None = field(repr=False)

    def __init__(self, *args, **kwargs) -> None:
        raise TextStockError("TEXT_STOCK_CONSTRUCTION_PRIVATE")


def _projection(project_id: str, generation: int, counts: TextCounts | None) -> TextProjection:
    _scope(project_id)
    _number(generation, minimum=1)
    if counts is not None and type(counts) is not TextCounts:
        raise TextStockError("TEXT_STOCK_COUNTS_INVALID")
    result = object.__new__(TextProjection)
    for name, value in (("project_id", project_id), ("generation", generation), ("counts", counts)):
        object.__setattr__(result, name, value)
    return result


def unknown_text_projection(*, project_id: str, generation: int) -> TextProjection:
    return _projection(project_id, generation, None)


def seed_empty_text_projection(
    proof: TrustedEmptyTextCreation, *, enabled: bool = False,
) -> TextProjection | None:
    if enabled is not True:
        return None
    try:
        verifier = getattr(proof, "require_empty_text_creation", None)
        if not callable(verifier):
            raise TextStockError("TEXT_STOCK_CREATION_PROOF_REQUIRED")
        creation = verifier()
    except Exception:
        raise TextStockError("TEXT_STOCK_CREATION_PROOF_REJECTED") from None
    if type(creation) is not EmptyTextCreation:
        raise TextStockError("TEXT_STOCK_CREATION_PROOF_INVALID")
    return _projection(creation.project_id, creation.generation, ZERO_TEXT)


@dataclass(frozen=True)
class TextBlockChange:
    """Complete per-block contributions, not client-controlled count deltas.

    None means unknown before/after. ZERO_TEXT means proven absent or excluded.
    A known eligible empty block contributes TextCounts(0, 0, 1), not ZERO_TEXT.
    """

    block_id: str = field(repr=False)
    before: TextCounts | None
    after: TextCounts | None

    def __post_init__(self) -> None:
        try:
            if type(self.block_id) is not str:
                raise HostedValidationError("INVALID")
            require_opaque_id(self.block_id)
        except HostedValidationError:
            raise TextStockError("TEXT_STOCK_BLOCK_ID_INVALID") from None
        for value in (self.before, self.after):
            if value is not None and (type(value) is not TextCounts or value.blocks > 1
                                      or value.characters > MAX_BLOCK_TEXT_BYTES):
                raise TextStockError("TEXT_STOCK_BLOCK_COUNTS_INVALID")


@dataclass(frozen=True)
class TextMutation:
    """Internal transaction evidence; completeness defaults unknown.

    Complete means every affected text/eligibility change is represented, even
    a tab reclassification or block moved between tabs. It is NOT authority from
    a Boolean/request field. Future composition must verify that claim from its
    entire authorized mutation plan and before images. Empty complete changes
    require proof that the eligible text stock is unchanged at this generation.
    """

    project_id: str = field(repr=False)
    before_generation: int
    after_generation: int
    changes: tuple[TextBlockChange, ...] = ()
    coverage: str = "unknown"

    def __post_init__(self) -> None:
        _scope(self.project_id)
        _number(self.before_generation, minimum=1)
        _number(self.after_generation, minimum=1)
        if self.after_generation != self.before_generation + 1:
            raise TextStockError("TEXT_STOCK_TRANSITION_INVALID")
        if type(self.coverage) is not str or self.coverage not in ("unknown", "complete"):
            raise TextStockError("TEXT_STOCK_COVERAGE_INVALID")
        if type(self.changes) is not tuple or len(self.changes) > MAX_TEXT_CHANGES:
            raise TextStockError("TEXT_STOCK_CHANGES_INVALID")
        if any(type(change) is not TextBlockChange for change in self.changes):
            raise TextStockError("TEXT_STOCK_CHANGES_INVALID")
        if len({change.block_id for change in self.changes}) != len(self.changes):
            raise TextStockError("TEXT_STOCK_DUPLICATE_BLOCK")


def advance_text_projection(
    current: TextProjection, mutation: TextMutation, *, enabled: bool = False,
) -> TextProjection | None:
    """Only g -> g+1; no jump, replay, automatic reseed or guessed delta."""
    if enabled is not True:
        return None
    if type(current) is not TextProjection or type(mutation) is not TextMutation:
        raise TextStockError("TEXT_STOCK_EVIDENCE_INVALID")
    if current.project_id != mutation.project_id or current.generation != mutation.before_generation:
        raise TextStockError("TEXT_STOCK_CONFLICT")
    counts = current.counts
    if mutation.coverage != "complete" or any(
        change.before is None or change.after is None for change in mutation.changes
    ):
        counts = None
    if counts is not None:
        values = []
        for name in ("words", "characters", "blocks"):
            before = sum(getattr(change.before, name) for change in mutation.changes)
            after = sum(getattr(change.after, name) for change in mutation.changes)
            if before > getattr(counts, name):
                raise TextStockError("TEXT_STOCK_BASELINE_CONFLICT")
            values.append(getattr(counts, name) - before + after)
        counts = TextCounts(*values)
    return _projection(current.project_id, mutation.after_generation, counts)


def eligible_text_counts(
    projection: TextProjection | None, root: ProjectRootObservation | None,
) -> TextCounts | None:
    if projection is None or root is None:
        return None
    if type(projection) is not TextProjection or type(root) is not ProjectRootObservation:
        raise TextStockError("TEXT_STOCK_EVIDENCE_INVALID")
    if (root.status != "active" or root.project_id != projection.project_id
            or root.generation != projection.generation):
        return None
    return projection.counts
