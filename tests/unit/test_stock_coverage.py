"""Synthetic project set proofs, never a provider scanner or manuscript crawl."""
import pytest

from hosted.security.canvas_projection import (
    ProjectRootObservation, _projection as canvas_projection,
)
from hosted.security.text_stock import (
    MAX_COUNT, TEXT_DEFINITION, TextCounts, ZERO_TEXT, _projection as text_projection,
)
from hosted.security.stock_coverage import (
    MAX_PROJECT_OBSERVATIONS, CompleteActiveProjectSet, ProjectStockObservation,
    StockCoverageError, summarize_stock,
)

PROJECT, OTHER = "a" * 32, "b" * 32


def _observation(project=PROJECT, generation=1, status="active", text=ZERO_TEXT, canvas=0):
    root = ProjectRootObservation(project, generation, status)
    return ProjectStockObservation(root, text_projection(project, generation, text), canvas_projection(project, generation, canvas))


class Proof:
    def __init__(self, *observations):
        self.roots = tuple(item.root for item in observations if item.root.status == "active")

    def require_complete_active_set(self):
        return CompleteActiveProjectSet(self.roots)


def _summarize(*observations, complete=True):
    return summarize_stock(observations, proof=Proof(*observations) if complete else None, enabled=True)


def test_disabled_does_not_inspect_inputs_or_call_proof():
    class Bomb:
        def __getattribute__(self, name):
            raise AssertionError("disabled input read")

    assert summarize_stock(Bomb(), proof=Bomb()) is None


def test_true_empty_requires_separate_trusted_complete_set_proof():
    unknown = _summarize(complete=False)
    assert not unknown.scope_complete
    assert unknown.current_cloud_projects is None
    assert unknown.current_cloud_words is None
    assert unknown.current_cloud_characters is None
    assert unknown.current_cloud_canvases is None
    empty = _summarize()
    assert empty.scope_complete
    assert empty.current_cloud_projects == empty.current_cloud_words == empty.current_cloud_characters == empty.current_cloud_canvases == 0


def test_complete_active_totals_and_coverage_are_numeric_only():
    first = _observation(text=TextCounts(3, 18, 2), canvas=2)
    second = _observation(OTHER, text=TextCounts(4, 20, 1), canvas=3)
    result = _summarize(first, second)
    assert result.scope_complete
    assert (result.current_cloud_projects, result.current_cloud_words, result.current_cloud_characters, result.current_cloud_canvases) == (2, 7, 38, 5)
    assert result.text_covered_roots == result.canvas_covered_roots == 2
    assert result.text_missing_roots == result.canvas_missing_roots == 0
    assert result.definition == TEXT_DEFINITION
    assert all(type(value) is int or value is None for value in result.scalars().values())
    assert PROJECT not in str(result.scalars()) and OTHER not in repr(result)


def test_known_observations_without_fleet_proof_never_become_fleet_totals():
    result = _summarize(_observation(text=TextCounts(3, 18, 2), canvas=2), complete=False)
    assert result.text_covered_roots == result.canvas_covered_roots == 1
    assert result.current_cloud_projects is None
    assert result.current_cloud_words is result.current_cloud_characters is result.current_cloud_canvases is None


def test_missing_imported_or_untouched_projections_remain_unknown_independently():
    first = _observation(text=None)
    result = _summarize(first)
    assert result.current_cloud_projects == 1
    assert result.current_cloud_words is result.current_cloud_characters is None
    assert result.current_cloud_canvases == 0
    assert result.text_missing_roots == 1
    second = _observation(text=ZERO_TEXT, canvas=None)
    result = _summarize(second)
    assert result.current_cloud_words == result.current_cloud_characters == 0
    assert result.current_cloud_canvases is None
    assert result.canvas_missing_roots == 1
    missing = ProjectStockObservation(first.root)
    result = _summarize(missing)
    assert result.text_missing_roots == result.canvas_missing_roots == 1


@pytest.mark.parametrize("status", ["deleted", "erasure_pending", "staging"])
def test_inactive_roots_excluded_not_miscounted_as_zero_active_projects(status):
    inactive = _observation(status=status, text=TextCounts(7, 20, 1), canvas=10)
    result = _summarize(inactive)
    assert result.active_roots == 0 and result.excluded_roots == 1
    assert result.current_cloud_projects == result.current_cloud_words == result.current_cloud_canvases == 0
    assert result.text_missing_roots == result.canvas_missing_roots == 0
    # Without complete active-set proof, an inactive result is not an empty fleet.
    assert _summarize(inactive, complete=False).current_cloud_projects is None


def test_unknown_root_eligibility_prevents_complete_scope():
    result = _summarize(_observation(status=None))
    assert result.unknown_roots == 1 and result.excluded_roots == 0
    assert not result.scope_complete and result.current_cloud_projects is None


@pytest.mark.parametrize("generation,project", [(2, PROJECT), (1, OTHER)])
def test_stale_or_wrong_project_projection_cannot_contribute(generation, project):
    root = ProjectRootObservation(PROJECT, 1, "active")
    observation = ProjectStockObservation(root, text_projection(project, generation, ZERO_TEXT), canvas_projection(project, generation, 0))
    result = _summarize(observation)
    assert result.current_cloud_projects == 1
    assert result.text_missing_roots == result.canvas_missing_roots == 1
    assert result.current_cloud_words is result.current_cloud_canvases is None


def test_stale_or_different_fleet_proof_never_claims_complete():
    observed = _observation()
    for proof in (Proof(), Proof(_observation(OTHER)), Proof(_observation(generation=2))):
        result = summarize_stock((observed,), proof=proof, enabled=True)
        assert not result.scope_complete and result.current_cloud_projects is None


def test_complete_set_order_does_not_matter_but_duplicate_projects_rejected():
    first, second = _observation(), _observation(OTHER)
    assert summarize_stock((first, second), proof=Proof(second, first), enabled=True).scope_complete
    with pytest.raises(StockCoverageError, match="DUPLICATE_ROOT"):
        _summarize(first, first)
    with pytest.raises(StockCoverageError, match="DUPLICATE_ROOT"):
        CompleteActiveProjectSet((first.root, first.root))


@pytest.mark.parametrize("proof", [True, False, {}, {"complete": True}])
def test_request_flag_not_scope_authority(proof):
    with pytest.raises(StockCoverageError, match="PROOF_REJECTED"):
        summarize_stock((), proof=proof, enabled=True)


def test_proof_type_and_error_sanitization():
    class Wrong:
        def require_complete_active_set(self):
            return True

    class Rejected:
        def require_complete_active_set(self):
            raise RuntimeError("synthetic private provider detail")

    with pytest.raises(StockCoverageError, match="PROOF_INVALID"):
        summarize_stock((), proof=Wrong(), enabled=True)
    with pytest.raises(StockCoverageError, match="PROOF_REJECTED") as caught:
        summarize_stock((), proof=Rejected(), enabled=True)
    assert str(caught.value) == "STOCK_COVERAGE_PROOF_REJECTED"
    assert caught.value.__suppress_context__


def test_safe_integer_fleet_overflow_suppresses_totals_without_losing_coverage():
    first = _observation(text=TextCounts(MAX_COUNT, MAX_COUNT, 1), canvas=MAX_COUNT)
    second = _observation(OTHER, text=TextCounts(1, 1, 1), canvas=1)
    result = _summarize(first, second)
    assert result.scope_complete and result.text_covered_roots == result.canvas_covered_roots == 2
    assert result.current_cloud_projects == 2
    assert result.current_cloud_words is result.current_cloud_characters is result.current_cloud_canvases is None


def test_bounded_batches_cannot_claim_unbounded_fleet_coverage():
    observation = _observation()
    for bad in ([observation], (object(),), (observation,) * (MAX_PROJECT_OBSERVATIONS + 1)):
        with pytest.raises(StockCoverageError, match="OBSERVATIONS_INVALID"):
            summarize_stock(bad, enabled=True)
    with pytest.raises(StockCoverageError, match="PROOF_INVALID"):
        CompleteActiveProjectSet((observation.root,) * (MAX_PROJECT_OBSERVATIONS + 1))
    with pytest.raises(StockCoverageError, match="PROOF_INVALID"):
        CompleteActiveProjectSet((_observation(status="deleted").root,))


def test_null_is_not_zero_when_only_some_active_roots_have_stock():
    result = _summarize(_observation(text=TextCounts(3, 5, 1), canvas=2), _observation(OTHER, text=None, canvas=None))
    assert result.current_cloud_projects == 2
    assert result.text_covered_roots == result.text_missing_roots == 1
    assert result.canvas_covered_roots == result.canvas_missing_roots == 1
    assert result.current_cloud_words is result.current_cloud_canvases is None
