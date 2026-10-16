from __future__ import annotations

import pytest

from formats.project_json import validation as project_json_validation
from formats.project_json import markup as project_json_markup
@pytest.mark.parametrize("value,expected", [
    (None, ""),
    ("  keep spaces  ", "  keep spaces  "),
])
def test_text_optional_preserves_content(value, expected):
    assert project_json_validation._text(value, "$.x") == expected


def test_text_required_trims_and_rejects_empty():
    assert project_json_validation._text("  hello  ", "$.x", required=True) == "hello"
    with pytest.raises(project_json_validation.ProjectJSONError) as exc:
        project_json_validation._text("   ", "$.x", required=True)
    assert exc.value.path == "$.x"


@pytest.mark.parametrize("value,expected", [(0, 0.0), (3, 3.0), (2.5, 2.5)])
def test_number_accepts_finite_numeric_values(value, expected):
    assert project_json_validation._number(value, "$.n") == expected


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), "3"])
def test_number_rejects_bool_nonfinite_and_text(value):
    with pytest.raises(project_json_validation.ProjectJSONError, match="finite number"):
        project_json_validation._number(value, "$.n")


def test_number_enforces_bounds():
    with pytest.raises(project_json_validation.ProjectJSONError, match="at least"):
        project_json_validation._number(4, "$.n", low=5)
    with pytest.raises(project_json_validation.ProjectJSONError, match="at most"):
        project_json_validation._number(6, "$.n", high=5)


@pytest.mark.parametrize("value,expected", [("#AABBCC", "#aabbcc"), ("#000000", "#000000")])
def test_color_normalizes_hex(value, expected):
    assert project_json_validation._color(value, "$.color", allow_empty=False) == expected


@pytest.mark.parametrize("value", ["red", "#fff", "#gggggg", 123])
def test_color_rejects_non_six_digit_hex(value):
    with pytest.raises(project_json_validation.ProjectJSONError, match="six-digit hex"):
        project_json_validation._color(value, "$.color", allow_empty=False)


@pytest.mark.parametrize("value", ["chapter:intro", "card:goal.1", "scene:a-b_c"])
def test_ref_accepts_readable_namespaced_ids(value):
    assert project_json_validation._ref(value, "$.ref") == value


@pytest.mark.parametrize("value", ["chapter", "bad ref:x", "chapter:/intro", "chapter:"])
def test_ref_rejects_invalid_ids(value):
    with pytest.raises(project_json_validation.ProjectJSONError, match="Reference IDs"):
        project_json_validation._ref(value, "$.ref")


def test_load_bytes_accepts_utf8_bom_and_requires_object_root():
    assert project_json_validation.load_bytes(b'\xef\xbb\xbf{"name":"Book"}') == {"name": "Book"}
    with pytest.raises(project_json_validation.ProjectJSONError, match="root JSON value"):
        project_json_validation.load_bytes(b'[1,2,3]')


def test_load_bytes_reports_json_location():
    with pytest.raises(project_json_validation.ProjectJSONError) as exc:
        project_json_validation.load_bytes(b'{"x": }')
    assert exc.value.code == "PROJECT_JSON_004"
    assert "line 1" in exc.value.message


def test_markup_round_trip_preserves_supported_inline_semantics():
    markup = r"**bold** *italics* __under__ \*literal\* \_literal\_ \\ slash"
    html_text, plain = project_json_markup.markup_to_html_text(markup)
    restored = project_json_markup.inline_html_to_markup(html_text, plain)
    assert "**bold**" in restored
    assert "*italics*" in restored
    assert "__under__" in restored
    assert "literal" in plain
