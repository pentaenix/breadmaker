"""Synthetic saved-text evidence only. No provider, credentials or manuscript IO."""
from dataclasses import FrozenInstanceError

import pytest

from hosted.security.canvas_projection import ProjectRootObservation
from hosted.security.text_stock import (
    MAX_BLOCK_TEXT_BYTES, MAX_COUNT, MAX_TEXT_CHANGES, TEXT_DEFINITION, UNICODE_VERSION,
    EmptyTextCreation, SavedTextBlock, TextBlockChange, TextCounts, TextMutation,
    TextProjection, TextStockError, ZERO_TEXT, _projection, advance_text_projection,
    eligible_text_counts, measure_saved_block, seed_empty_text_projection,
    unknown_text_projection,
)

PROJECT, OTHER = "a" * 32, "b" * 32


class Explosive:
    def __getattribute__(self, name):
        raise AssertionError("disabled code inspected input")


class EmptyProof:
    def require_empty_text_creation(self):
        return EmptyTextCreation(PROJECT)


def _seed():
    return seed_empty_text_projection(EmptyProof(), enabled=True)


def _counts(text="hello world", primary=True, kind="paragraph"):
    return measure_saved_block(SavedTextBlock(text, primary, kind), enabled=True)


def _mutation(before=1, changes=(), coverage="complete", project=PROJECT):
    return TextMutation(project, before, before + 1, changes, coverage)


def _advance(current, mutation):
    return advance_text_projection(current, mutation, enabled=True)


def _root(generation=1, status="active", project=PROJECT):
    return ProjectRootObservation(project, generation, status)


def test_default_off_never_inspects_payload_projection_or_proof():
    bomb = Explosive()
    assert measure_saved_block(bomb) is None
    assert seed_empty_text_projection(bomb) is None
    assert advance_text_projection(bomb, bomb) is None
    # Construction itself must not inspect or serialize input content.
    assert measure_saved_block(SavedTextBlock(bomb, bomb, bomb)) is None
    assert measure_saved_block(bomb, enabled="true") is None


@pytest.mark.parametrize("text,words,characters", [
    ("", 0, 0), (None, 0, 0), ("   \t\n", 0, 5),
    ("hello world", 2, 11), (" hello  world ", 2, 14),
    ("... !!!", 2, 7), ("can't co-operate", 2, 16),
    ("你好世界", 1, 4), ("a\u00a0b\u2003c\u2028d\u001ce", 5, 9),
    ("a\u200bb", 1, 3), ("👩\u200d💻", 1, 3),
    ("e\u0301 é", 2, 4), ("\r\n", 0, 2),
])
def test_versioned_saved_text_definition(text, words, characters):
    counts = _counts(text)
    assert counts == TextCounts(words, characters, 1)
    assert counts.words == len((text or "").split())
    assert counts.definition == TEXT_DEFINITION
    assert UNICODE_VERSION == "16.0.0"


def test_all_unicode_whitespace_matches_python_split_without_synthetic_characters():
    # Exercise all Unicode scalar values, not a hand-picked ASCII approximation.
    whitespace = "".join(chr(point) for point in range(0x110000) if chr(point).isspace())
    counts = _counts("a" + whitespace + "b")
    assert counts.words == len(("a" + whitespace + "b").split()) == 2
    assert counts.characters == len(whitespace) + 2
    blocks = ["  hello ", "", "world  "]
    assert sum(_counts(text).words for text in blocks) == len(" ".join(blocks).strip().split())
    assert sum(_counts(text).characters for text in blocks) == sum(map(len, blocks))
    assert sum(map(len, blocks)) != len(" ".join(blocks).strip())


@pytest.mark.parametrize("primary,kind", [(False, "paragraph"), (True, "image"), (None, "image"), (False, None)])
def test_known_exclusion_does_not_inspect_content(primary, kind):
    assert _counts(Explosive(), primary, kind) == ZERO_TEXT


@pytest.mark.parametrize("primary,kind", [(None, "paragraph"), (True, None), (None, None)])
def test_unknown_eligibility_is_not_empty_stock(primary, kind):
    assert _counts(Explosive(), primary, kind) is None


@pytest.mark.parametrize("primary,kind", [(1, "paragraph"), ("true", "paragraph"), (True, 3), (True, ""), (True, "x" * 65)])
def test_bad_eligibility_rejected(primary, kind):
    with pytest.raises(TextStockError, match="ELIGIBILITY_INVALID"):
        _counts("secret synthetic text", primary, kind)


@pytest.mark.parametrize("text", [42, False, b"bytes", "\ud800", "\udfff"])
def test_non_text_or_invalid_unicode_rejected_without_content(text):
    with pytest.raises(TextStockError, match="TEXT_INVALID") as caught:
        _counts(text)
    assert str(caught.value) == "TEXT_STOCK_TEXT_INVALID"


def test_byte_bound_catches_multibyte_and_single_byte_overflow(monkeypatch):
    import hosted.security.text_stock as module
    monkeypatch.setattr(module, "MAX_BLOCK_TEXT_BYTES", 8)
    assert _counts("a" * 8).characters == 8
    assert _counts("😀😀").characters == 2
    with pytest.raises(TextStockError, match="TEXT_TOO_LARGE"):
        _counts("😀😀😀")
    with pytest.raises(TextStockError, match="TEXT_INVALID"):
        _counts("a" * 9)
    assert MAX_BLOCK_TEXT_BYTES == 8 * 1024 * 1024


def test_unicode_runtime_mismatch_rejected_not_silently_redefined(monkeypatch):
    import hosted.security.text_stock as module
    monkeypatch.setattr(module.unicodedata, "unidata_version", "future")
    with pytest.raises(TextStockError, match="UNICODE_VERSION_UNSUPPORTED"):
        _counts("hello")


@pytest.mark.parametrize("proof", [True, False, {}, {"empty": True}, object()])
def test_request_flag_is_not_empty_creation_authority(proof):
    with pytest.raises(TextStockError, match="PROOF_REJECTED"):
        seed_empty_text_projection(proof, enabled=True)


def test_empty_proof_is_typed_and_provider_failures_are_sanitized():
    class Wrong:
        def require_empty_text_creation(self):
            return {"project_id": PROJECT, "generation": 1}

    class Rejected:
        def require_empty_text_creation(self):
            raise RuntimeError("synthetic provider secret")

    with pytest.raises(TextStockError, match="PROOF_INVALID"):
        seed_empty_text_projection(Wrong(), enabled=True)
    with pytest.raises(TextStockError, match="PROOF_REJECTED") as caught:
        seed_empty_text_projection(Rejected(), enabled=True)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert _seed().counts == ZERO_TEXT
    with pytest.raises(TextStockError, match="CREATION_INVALID"):
        EmptyTextCreation(PROJECT, 2)


def test_create_edit_delete_restore_and_scope_changes_use_current_stock():
    original, changed = _counts(), _counts("updated")
    state = _advance(_seed(), _mutation(changes=(TextBlockChange("one", ZERO_TEXT, original),)))
    assert state.counts == original
    state = _advance(state, _mutation(2, (TextBlockChange("one", original, changed),)))
    assert state.counts == changed
    state = _advance(state, _mutation(3, (TextBlockChange("one", changed, ZERO_TEXT),)))
    assert state.counts == ZERO_TEXT
    # Undo/delete restore must reapply the exact authorized saved contribution.
    state = _advance(state, _mutation(4, (TextBlockChange("one", ZERO_TEXT, changed),)))
    assert state.counts == changed
    # Moving out of primary manuscript / becoming an image is known exclusion.
    state = _advance(state, _mutation(5, (TextBlockChange("one", changed, ZERO_TEXT),)))
    assert state.counts == ZERO_TEXT
    state = _advance(state, _mutation(6, (TextBlockChange("one", ZERO_TEXT, original),)))
    assert state.counts == original


def test_eligible_empty_blocks_are_not_absent_and_can_be_deleted():
    empty = _counts("")
    assert empty != ZERO_TEXT
    state = _advance(_seed(), _mutation(changes=(TextBlockChange("one", ZERO_TEXT, empty),)))
    assert state.counts == TextCounts(0, 0, 1)
    state = _advance(state, _mutation(2, (TextBlockChange("one", empty, ZERO_TEXT),)))
    assert state.counts == ZERO_TEXT


@pytest.mark.parametrize("changes,coverage", [
    ((), "unknown"), ((TextBlockChange("one", None, ZERO_TEXT),), "complete"),
    ((TextBlockChange("one", ZERO_TEXT, None),), "complete"),
])
def test_incomplete_scope_or_before_after_invalidates_known_stock(changes, coverage):
    state = _advance(_seed(), _mutation(changes=changes, coverage=coverage))
    assert state.generation == 2 and state.counts is None
    assert _advance(state, _mutation(2)).counts is None
    assert TextMutation(PROJECT, 1, 2).coverage == "unknown"


def test_unknown_import_never_becomes_zero_even_after_complete_mutation():
    imported = unknown_text_projection(project_id=PROJECT, generation=1)
    state = _advance(imported, _mutation(changes=(TextBlockChange("one", ZERO_TEXT, _counts()),)))
    assert state.counts is None
    assert _advance(state, _mutation(2)).counts is None


def test_unrelated_complete_generation_carries_stock_and_root_lifecycle_hides_it():
    state = _advance(_seed(), _mutation())
    assert eligible_text_counts(state, _root(2)) == ZERO_TEXT
    for root in (_root(1), _root(2, "deleted"), _root(2, None), _root(2, project=OTHER)):
        assert eligible_text_counts(state, root) is None
    assert eligible_text_counts(None, _root(2)) is None
    assert eligible_text_counts(state, None) is None
    # Restore must also advance through an evidenced generation.
    restored = _advance(state, _mutation(2))
    assert eligible_text_counts(restored, _root(3)) == ZERO_TEXT


@pytest.mark.parametrize("before,after", [(1, 1), (1, 3), (2, 1), (True, 2), (1, 2.0), (0, 1), (MAX_COUNT, MAX_COUNT + 1)])
def test_non_unit_or_unbounded_generations_rejected(before, after):
    with pytest.raises(TextStockError):
        TextMutation(PROJECT, before, after)


def test_wrong_scope_replay_and_skipped_generation_conflict():
    state = _advance(_seed(), _mutation())
    for mutation in (_mutation(), _mutation(3), _mutation(2, project=OTHER)):
        with pytest.raises(TextStockError, match="CONFLICT"):
            _advance(state, mutation)


def test_duplicate_or_unbounded_changes_rejected():
    one = TextBlockChange("one", ZERO_TEXT, _counts())
    with pytest.raises(TextStockError, match="DUPLICATE_BLOCK"):
        _mutation(changes=(one, one))
    with pytest.raises(TextStockError, match="CHANGES_INVALID"):
        _mutation(changes=[one])
    changes = tuple(TextBlockChange(str(n), ZERO_TEXT, _counts()) for n in range(MAX_TEXT_CHANGES + 1))
    with pytest.raises(TextStockError, match="CHANGES_INVALID"):
        _mutation(changes=changes)


def test_balanced_impossible_before_images_are_not_allowed():
    same = _counts()
    with pytest.raises(TextStockError, match="BASELINE_CONFLICT"):
        _advance(_seed(), _mutation(changes=(TextBlockChange("one", same, same),)))


def test_count_overflow_and_mixed_definitions_rejected():
    at_limit = _projection(PROJECT, 1, TextCounts(MAX_COUNT, MAX_COUNT, 1))
    with pytest.raises(TextStockError, match="NUMBER_INVALID"):
        _advance(at_limit, _mutation(changes=(TextBlockChange("one", ZERO_TEXT, _counts("a")),)))
    for definition in ("checkpoint_regex_v1", "primary_manuscript_non_image_saved_text_v1_ucd15_0_0", None):
        with pytest.raises(TextStockError, match="DEFINITION_INVALID"):
            TextCounts(1, 1, 1, definition)


@pytest.mark.parametrize("values", [(True, 1, 1), (1.0, 1, 1), (-1, 1, 1), (2, 1, 1), (0, 1, 0), (0, 0, MAX_COUNT + 1)])
def test_strict_count_invariants(values):
    with pytest.raises(TextStockError):
        TextCounts(*values)


def test_projection_private_and_inputs_not_exposed_in_repr():
    with pytest.raises(TextStockError, match="CONSTRUCTION_PRIVATE"):
        TextProjection(PROJECT, 1, ZERO_TEXT)
    state = _seed()
    with pytest.raises(FrozenInstanceError):
        state.generation = 2
    assert PROJECT not in repr(state)
    assert "synthetic private text" not in repr(SavedTextBlock("synthetic private text", True, "paragraph"))
    assert "private_block" not in repr(TextBlockChange("private_block", ZERO_TEXT, ZERO_TEXT))


@pytest.mark.parametrize("counts", [TextCounts(0, 0, 2), TextCounts(1, MAX_BLOCK_TEXT_BYTES + 1, 1), True, {}])
def test_each_block_change_has_one_bounded_contribution(counts):
    with pytest.raises(TextStockError, match="BLOCK_COUNTS_INVALID"):
        TextBlockChange("one", ZERO_TEXT, counts)


@pytest.mark.parametrize("block_id", ["", "bad/path", "x" * 161, 1])
def test_invalid_block_identifiers_are_not_echoed(block_id):
    with pytest.raises(TextStockError, match="BLOCK_ID_INVALID") as caught:
        TextBlockChange(block_id, ZERO_TEXT, ZERO_TEXT)
    assert str(caught.value) == "TEXT_STOCK_BLOCK_ID_INVALID"


@pytest.mark.parametrize("coverage", [True, None, "partial", "complete "])
def test_mutation_coverage_is_closed(coverage):
    with pytest.raises(TextStockError, match="COVERAGE_INVALID"):
        _mutation(coverage=coverage)
