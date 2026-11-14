"""Unwired saved-word projection foundation. No provider or content reads.

Inputs must be produced by an authorized canonical writer, never an Admin caller.
This module does NOT prove storage coverage or install a runtime producer.
"""
from dataclasses import dataclass
import re

DEFINITION = 'saved_text_unicode_words_v1'
MAX_BLOCKS = 100_000
MAX_TEXT_CHARS = 1_000_000
MAX_WORDS = 100_000_000
_WORD = re.compile(r"[\w’'-]+", re.UNICODE)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


@dataclass(frozen=True)
class Contribution:
    words: int
    definition: str = DEFINITION

    def __post_init__(self):
        if self.definition != DEFINITION or not _integer(self.words, 0, MAX_WORDS):
            raise ValueError('INVALID_WORD_CONTRIBUTION')


def count_saved_text(text: str) -> Contribution:
    """Count already-authorized plaintext; retain no text or content digest."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise ValueError('INVALID_SAVED_TEXT')
    return Contribution(sum(1 for _ in _WORD.finditer(text)))


@dataclass(frozen=True)
class Projection:
    revision: int
    blocks: int | None
    words: int | None
    definition: str = DEFINITION

    def __post_init__(self):
        if (self.definition != DEFINITION or not _integer(self.revision, 1, 2**53-1)
                or ((self.blocks is None) != (self.words is None))
                or (self.blocks is not None and (
                    not _integer(self.blocks, 0, MAX_BLOCKS)
                    or not _integer(self.words, 0, MAX_WORDS)
                    or (self.blocks == 0 and self.words != 0)))):
            raise ValueError('INVALID_WORD_PROJECTION')

    @classmethod
    def unknown(cls, revision):
        return cls(revision, None, None)


def prepare_complete_snapshot(contributions, *, revision, enabled=False):
    """Prepare a candidate only; caller must verify full manifest and commit CAS.

    Imports/backfills/restores cannot publish this candidate until canonical
    membership, all blocks, and the exact target revision have been verified.
    """
    if enabled is not True:
        return Projection.unknown(revision)
    count = words = 0
    for item in contributions:
        if not isinstance(item, Contribution):
            raise ValueError('INVALID_WORD_CONTRIBUTION')
        count += 1
        words += item.words
        if count > MAX_BLOCKS or words > MAX_WORDS:
            raise ValueError('WORD_PROJECTION_LIMIT')
    return Projection(revision, count, words)


def prepare_replacement(current, *, expected_revision, next_revision,
                        removed, added, enabled=False):
    """Pure candidate for an exact canonical replacement (including Undo).

    No operation receipts live here: caller must atomically CAS the canonical
    records and this projection and return before counting an idempotent replay.
    Unknown coverage stays unknown; editing a subset never proves full coverage.
    """
    if (not isinstance(current, Projection) or current.revision != expected_revision
            or next_revision != expected_revision + 1):
        raise ValueError('WORD_PROJECTION_REVISION_CONFLICT')
    if enabled is not True or current.words is None:
        return Projection.unknown(next_revision)
    before = prepare_complete_snapshot(removed, revision=next_revision, enabled=True)
    after = prepare_complete_snapshot(added, revision=next_revision, enabled=True)
    return Projection(next_revision, current.blocks - before.blocks + after.blocks,
                      current.words - before.words + after.words)
