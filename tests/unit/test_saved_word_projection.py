import pytest
from hosted.security.saved_word_projection import (
    Contribution, Projection, count_saved_text, prepare_complete_snapshot,
    prepare_replacement,
)


def test_disabled_does_not_consume_content():
    def forbidden():
        raise AssertionError('content inspected')
        yield
    assert prepare_complete_snapshot(forbidden(), revision=1).words is None


@pytest.mark.parametrize('text,count', [('', 0), ('hello world', 2),
    ("don't mother-in-law", 2), ('日本語 中文', 2), ('---', 1),
    ('a\nb\tc', 3), ('😀', 0)])
def test_versioned_history_semantics(text, count):
    assert count_saved_text(text).words == count


def test_create_edit_delete_and_undo_candidates():
    empty = prepare_complete_snapshot([], revision=1, enabled=True)
    first = prepare_replacement(empty, expected_revision=1, next_revision=2,
        removed=[], added=[Contribution(4)], enabled=True)
    edited = prepare_replacement(first, expected_revision=2, next_revision=3,
        removed=[Contribution(4)], added=[Contribution(7)], enabled=True)
    undo = prepare_replacement(edited, expected_revision=3, next_revision=4,
        removed=[Contribution(7)], added=[Contribution(4)], enabled=True)
    deleted = prepare_replacement(undo, expected_revision=4, next_revision=5,
        removed=[Contribution(4)], added=[], enabled=True)
    assert (first.words, edited.words, undo.words, deleted.words) == (4, 7, 4, 0)
    assert deleted.blocks == 0


def test_unknown_cannot_be_repaired_by_partial_edit():
    result = prepare_replacement(Projection.unknown(8), expected_revision=8,
        next_revision=9, removed=[], added=[Contribution(2)], enabled=True)
    assert result.words is None and result.blocks is None


def test_stale_or_replayed_revision_rejected():
    with pytest.raises(ValueError):
        prepare_replacement(Projection(3, 1, 2), expected_revision=2,
            next_revision=3, removed=[], added=[], enabled=True)


@pytest.mark.parametrize('value', [True, -1, 100_000_001, '2'])
def test_invalid_counts_rejected(value):
    with pytest.raises(ValueError):
        Contribution(value)


def test_partial_and_negative_projection_rejected():
    for blocks, words in [(None, 0), (0, None), (0, 1), (-1, 0)]:
        with pytest.raises(ValueError):
            Projection(1, blocks, words)


def test_replacement_cannot_underflow():
    with pytest.raises(ValueError):
        prepare_replacement(Projection(1, 1, 2), expected_revision=1,
            next_revision=2, removed=[Contribution(3)], added=[], enabled=True)
