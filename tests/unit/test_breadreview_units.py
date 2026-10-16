from __future__ import annotations

import math
import pytest

from formats import breadreview
def test_seal_is_deterministic_and_does_not_mutate_input():
    source = {"review_id": "r1", "nested": {"b": 2, "a": 1}}
    before = {"review_id": "r1", "nested": {"b": 2, "a": 1}}
    first = breadreview.seal(source)
    second = breadreview.seal({"nested": {"a": 1, "b": 2}, "review_id": "r1"})
    assert source == before
    assert first["package_format"] == breadreview.PACKAGE_FORMAT
    assert first["package_version"] == breadreview.PACKAGE_VERSION
    assert first["payload_hash"] == second["payload_hash"]


def test_payload_hash_ignores_existing_payload_hash_field():
    manifest = {"x": 1, "payload_hash": "wrong"}
    assert breadreview.payload_hash(manifest) == breadreview.payload_hash({"x": 1})


@pytest.mark.parametrize("name", [
    "review.json", "assets/image.png", "assets/deep/file.bin", "évidence.txt", "./relative",
])
def test_safe_member_name_accepts_portable_relative_paths(name):
    assert breadreview._safe_member_name(name)


@pytest.mark.parametrize("name", [
    "", "/absolute", "../escape", "assets/../escape", "assets\\windows", "bad\x00name",
])
def test_safe_member_name_rejects_unsafe_paths(name):
    assert not breadreview._safe_member_name(name)


def test_creation_passphrase_enforces_minimum_but_read_accepts_short_legacy_input():
    assert breadreview._validated_passphrase("short", for_creation=False) == "short"
    with pytest.raises(breadreview.BreadReviewError) as exc:
        breadreview._validated_passphrase("short", for_creation=True)
    assert exc.value.code == "BREADREVIEW_032"


def test_passphrase_rejects_non_text_and_oversize():
    with pytest.raises(breadreview.BreadReviewError) as nontext:
        breadreview._validated_passphrase(123)  # type: ignore[arg-type]
    assert nontext.value.code == "BREADREVIEW_031"
    with pytest.raises(breadreview.BreadReviewError) as huge:
        breadreview._validated_passphrase("x" * (breadreview.MAX_PASSPHRASE_CHARS + 1))
    assert huge.value.code == "BREADREVIEW_031"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_json_shape_rejects_nonfinite_numbers(value):
    with pytest.raises(breadreview.BreadReviewError) as exc:
        breadreview._validate_json_shape({"value": value})
    assert exc.value.code == "BREADREVIEW_042"


def test_json_shape_rejects_non_string_object_keys():
    with pytest.raises(breadreview.BreadReviewError) as exc:
        breadreview._validate_json_shape({1: "x"})
    assert exc.value.code == "BREADREVIEW_025"
