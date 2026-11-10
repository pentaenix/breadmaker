"""Pure synthetic projection contracts. No provider, storage or manuscript IO."""
from dataclasses import FrozenInstanceError

import pytest

from hosted.security.canvas_projection import (
    MAX_CANVAS_ROOT_CHANGES, MAX_CANVASES, MAX_GENERATION,
    CanvasMutation, CanvasProjection, CanvasProjectionError, CanvasRootChange,
    EmptyProjectCreation, ProjectRootObservation, advance_projection,
    eligible_canvas_count, seed_empty_projection, unknown_projection, _projection,
)

PROJECT = "a" * 32
OTHER = "b" * 32


class TrustedSyntheticCreation:
    def __init__(self):
        self.verifications = 0

    def require_empty_creation(self):
        self.verifications += 1
        return EmptyProjectCreation(PROJECT)


def _seed():
    return seed_empty_projection(TrustedSyntheticCreation())


def _root(generation=1, status="active", project=PROJECT):
    return ProjectRootObservation(project, generation, status)


def _change(name="canvas_one", before=False, after=True):
    return CanvasRootChange(name, before, after)


def _mutation(before=1, changes=(), project=PROJECT):
    return CanvasMutation(project, before, before + 1, changes)


def test_zero_seed_requires_verifier_and_matches_exact_creation():
    proof = TrustedSyntheticCreation()
    projection = seed_empty_projection(proof)
    assert proof.verifications == 1
    assert eligible_canvas_count(projection, _root()) == 0
    assert projection.generation == 1


@pytest.mark.parametrize("proof", [True, False, {}, {"empty": True}, object(), lambda: True])
def test_request_boolean_or_payload_is_not_creation_authority(proof):
    with pytest.raises(CanvasProjectionError, match="PROOF_REQUIRED"):
        seed_empty_projection(proof)


@pytest.mark.parametrize("value", [True, None, {}, {"project_id": PROJECT, "generation": 1}])
def test_verifier_must_return_private_typed_creation(value):
    class WrongProof:
        def require_empty_creation(self):
            return value

    with pytest.raises(CanvasProjectionError, match="PROOF_INVALID"):
        seed_empty_projection(WrongProof())


def test_rejected_trusted_creation_does_not_seed():
    class RejectedProof:
        def require_empty_creation(self):
            raise RuntimeError("synthetic sensitive provider payload")

    with pytest.raises(CanvasProjectionError, match="PROOF_REJECTED") as caught:
        seed_empty_projection(RejectedProof())
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize("generation", [True, 0, 2, 1.0, "1"])
def test_seed_assertion_requires_first_generation(generation):
    with pytest.raises(CanvasProjectionError, match="CREATION_INVALID"):
        EmptyProjectCreation(PROJECT, generation)


def test_create_update_delete_and_undo_are_current_inventory_not_lifetime():
    projection = advance_projection(_seed(), _mutation(changes=(_change(),)))
    assert eligible_canvas_count(projection, _root(2)) == 1
    projection = advance_projection(projection, _mutation(2, (_change(before=True),)))
    assert eligible_canvas_count(projection, _root(3)) == 1
    projection = advance_projection(projection, _mutation(3, (_change(before=True, after=False),)))
    assert eligible_canvas_count(projection, _root(4)) == 0
    projection = advance_projection(projection, _mutation(4, (_change(),)))
    assert eligible_canvas_count(projection, _root(5)) == 1


def test_replay_and_out_of_order_commits_do_not_double_count():
    mutation = _mutation(changes=(_change(),))
    current = advance_projection(_seed(), mutation)
    for wrong in (mutation, _mutation(3, (_change("canvas_two"),))):
        with pytest.raises(CanvasProjectionError, match="CONFLICT"):
            advance_projection(current, wrong)
    assert eligible_canvas_count(current, _root(2)) == 1


@pytest.mark.parametrize("after", [1, 3, 5])
def test_non_unit_or_no_generation_change_is_rejected(after):
    with pytest.raises(CanvasProjectionError, match="TRANSITION_INVALID"):
        CanvasMutation(PROJECT, 1, after)


def test_foreign_project_cannot_consume_another_projection():
    with pytest.raises(CanvasProjectionError, match="CONFLICT"):
        advance_projection(_seed(), _mutation(project=OTHER))
    assert eligible_canvas_count(_seed(), _root(project=OTHER)) is None


def test_unknown_baseline_stays_unknown_after_creates_deletes_and_noop():
    unknown = unknown_projection(project_id=PROJECT, generation=6)
    for generation, changes in ((6, (_change(),)), (7, (_change(before=True, after=False),)), (8, ())):
        unknown = advance_projection(unknown, _mutation(generation, changes))
        assert eligible_canvas_count(unknown, _root(generation + 1)) is None


def test_unrelated_commit_must_explicitly_carry_projection_forward():
    projection = advance_projection(_seed(), _mutation(changes=(_change(),)))
    assert eligible_canvas_count(projection, _root(3)) is None
    projection = advance_projection(projection, _mutation(2))
    assert eligible_canvas_count(projection, _root(3)) == 1


@pytest.mark.parametrize("status", [None, "deleted", "erasure_pending", "staging"])
def test_inactive_or_missing_status_never_exports_known_zero(status):
    assert eligible_canvas_count(_seed(), _root(status=status)) is None


def test_delete_then_restore_requires_current_generation_not_old_count():
    projection = advance_projection(_seed(), _mutation(changes=(_change(),)))
    projection = advance_projection(projection, _mutation(2))
    assert eligible_canvas_count(projection, _root(3, "deleted")) is None
    assert eligible_canvas_count(projection, _root(4, "active")) is None
    restored = advance_projection(projection, _mutation(3))
    assert eligible_canvas_count(restored, _root(4, "active")) == 1


def test_missing_projection_or_root_is_unavailable():
    assert eligible_canvas_count(None, _root()) is None
    assert eligible_canvas_count(_seed(), None) is None


@pytest.mark.parametrize("count", [True, -1, 1.0, "1", MAX_CANVASES + 1])
def test_private_projection_rejects_invalid_numeric_counts(count):
    with pytest.raises(CanvasProjectionError, match="COUNT_INVALID"):
        _projection(PROJECT, 1, count)


def test_underflow_and_overflow_fail_without_modifying_input():
    zero = _seed()
    with pytest.raises(CanvasProjectionError, match="BASELINE_CONFLICT"):
        advance_projection(zero, _mutation(changes=(_change(before=True, after=False),)))
    maximum = _projection(PROJECT, 1, MAX_CANVASES)
    with pytest.raises(CanvasProjectionError, match="COUNT_INVALID"):
        advance_projection(maximum, _mutation(changes=(_change(),)))
    assert eligible_canvas_count(zero, _root()) == 0
    assert eligible_canvas_count(maximum, _root()) == MAX_CANVASES


def test_duplicate_root_images_cannot_count_twice():
    with pytest.raises(CanvasProjectionError, match="DUPLICATE_ROOT"):
        _mutation(changes=(_change(), _change()))


def test_change_list_is_bounded_immutable_and_typed():
    changes = tuple(_change(f"canvas_{i}") for i in range(MAX_CANVAS_ROOT_CHANGES))
    assert eligible_canvas_count(advance_projection(_seed(), _mutation(changes=changes)), _root(2)) == len(changes)
    for invalid in (changes + (_change("overflow"),), list(changes), (True,)):
        with pytest.raises(CanvasProjectionError, match="CHANGES_INVALID"):
            _mutation(changes=invalid)


@pytest.mark.parametrize("before,after", [(1, True), (False, 1), (None, False), ("false", True)])
def test_existence_flags_are_strict_booleans(before, after):
    with pytest.raises(CanvasProjectionError, match="EXISTENCE_INVALID"):
        _change(before=before, after=after)


@pytest.mark.parametrize("generation", [True, 0, -1, 1.0, "1", MAX_GENERATION + 1])
def test_generation_validation_never_coerces(generation):
    with pytest.raises(CanvasProjectionError, match="GENERATION_INVALID"):
        unknown_projection(project_id=PROJECT, generation=generation)


def test_private_state_is_immutable_and_public_result_is_scalar_only():
    projection = advance_projection(_seed(), _mutation(changes=(_change(),)))
    with pytest.raises(FrozenInstanceError):
        projection.canvas_count = 99
    assert type(eligible_canvas_count(projection, _root(2))) is int
    assert PROJECT not in repr(projection)
    assert "canvas_one" not in repr(_change())


@pytest.mark.parametrize("project", ["", "not_a_project", True])
def test_invalid_scope_has_sanitized_structural_error(project):
    with pytest.raises(CanvasProjectionError, match="SCOPE_INVALID") as caught:
        unknown_projection(project_id=project, generation=1)
    assert "not_a_project" not in str(caught.value)


def test_mixed_batch_counts_root_existence_not_edits():
    before = _projection(PROJECT, 7, 2)
    changes = (_change("add"), _change("remove", True, False),
               _change("edit", True, True), _change("absent", False, False))
    after = advance_projection(before, _mutation(7, changes))
    assert eligible_canvas_count(after, _root(8)) == 2


@pytest.mark.parametrize("canvas", ["", "projects/other/canvas", True])
def test_invalid_canvas_scope_is_structural_and_sanitized(canvas):
    with pytest.raises(CanvasProjectionError, match="ROOT_INVALID"):
        _change(name=canvas)


def test_balanced_delta_cannot_hide_more_existing_roots_than_baseline():
    changes = (_change("removed", True, False), _change("added", False, True))
    with pytest.raises(CanvasProjectionError, match="BASELINE_CONFLICT"):
        advance_projection(_seed(), _mutation(changes=changes))


def test_exhausted_generation_cannot_wrap_or_restart():
    with pytest.raises(CanvasProjectionError, match="GENERATION_INVALID"):
        CanvasMutation(PROJECT, MAX_GENERATION, MAX_GENERATION + 1)


def test_unknown_status_is_rejected_instead_of_assumed_active():
    with pytest.raises(CanvasProjectionError, match="ROOT_STATUS_INVALID"):
        _root(status="published")


def test_projection_has_no_public_constructor_for_reseeding_existing_state():
    with pytest.raises(CanvasProjectionError, match="CONSTRUCTION_PRIVATE"):
        CanvasProjection(PROJECT, 3, 20)
