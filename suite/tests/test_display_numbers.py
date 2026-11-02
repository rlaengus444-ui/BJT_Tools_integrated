"""Presentation must not weaken clearance certificates or round real inputs."""
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from display_numbers import format_display_value, format_lower_bound, format_number


def test_measurements_use_two_decimal_places_but_counts_and_labels_stay_exact():
    assert format_number(1.9782753934187156) == "1.98"
    assert format_number(171) == "171"
    assert format_number("v1.8.0 / part_00123 / 0.123456") == "v1.8.0 / part_00123 / 0.123456"
    assert format_number(-0.0) == "0.00"
    assert format_number(1.2, trim=True) == "1.2"


def test_small_positive_intersection_and_physical_values_never_look_like_zero():
    assert format_number(4.1453492372056635e-6) == "4.15e-06"
    assert format_number(-1e-6) == "-1.00e-06"
    assert format_number(1.234567e-7, scientific=True) == "1.23e-07"


def test_decimal_inputs_keep_large_integer_digits_and_very_small_scale():
    value = Decimal("9" * 400 + ".123456789")
    assert format_number(value) == "9" * 400 + ".12"
    assert format_number(Decimal("1.23456e-400")) == "1.23e-400"
    assert value == Decimal("9" * 400 + ".123456789")


def test_displayed_lower_bound_remains_a_true_bound_at_binary_decimal_edges():
    values = [3.4785022735595703, 0.29, math.nextafter(1.5, -math.inf),
              math.nextafter(1.5, math.inf), 0.0, 0.000001, -0.000001, 100_000_000.12345]
    for value in values:
        text = format_lower_bound(value)
        assert Decimal(text) <= Decimal.from_float(value)
        assert len(text.partition(".")[2]) == 2
    assert format_lower_bound(3.4785022735595703) == "3.47"
    assert format_lower_bound(Decimal("0.29")) == "0.29"


def test_nested_display_is_separate_from_full_precision_json_and_original_values():
    values = {"distance": 1.9782753934187156, "rows": [171, 0.000001], "name": "part_0.123456.stl"}
    original = json.dumps(values)
    text = format_display_value(values)
    assert "1.98" in text and "1.00e-06" in text
    assert "part_0.123456.stl" in text and "171" in text
    assert json.dumps(values) == original
    assert json.loads(original)["distance"] == 1.9782753934187156


def test_nonfinite_values_are_not_presented_as_measurements():
    assert format_number(None) == "—"
    assert format_number(float("nan")) == "NaN"
    assert format_number(float("inf")) == "∞"


@pytest.mark.parametrize("key", ["bjt", "arrange", "overlay", "exchanger", "alignment", "thermal",
                                  "compare", "number_tools", "sequence_z", "m2_converter", "chitu_checker"])
def test_shared_display_integrity_is_checked_before_any_tool_executes(tmp_path, key):
    import suite_loader
    path = tmp_path / "display_numbers.py"
    path.write_text("raise AssertionError('must not execute')\n")
    manifest = {"tools": [{"id": key}], "dependencies": [
        {"path": "display_numbers.py", "sha256": "0" * 64}]}
    with mock.patch.object(suite_loader, "BASE_DIR", tmp_path), mock.patch.object(suite_loader, "read_manifest", return_value=manifest):
        with pytest.raises(RuntimeError, match="display_numbers.py"):
            suite_loader.ToolLoader().load(key)
