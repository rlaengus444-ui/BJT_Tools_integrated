"""Independent M1 Z reference, saved XLSM caches, paging and atomic CSV tests.

The embedded table contains original XML numeric cache strings, not values
computed by the implementation under test. Source workbook SHA-256:
2865e4965090b129739478120f8d5737c927eb24c34d0409ea7d9c60cc20a61d
M1 Z축!B16:E3558, 3543 layers / 10629 Z cache values. No macro executed.
"""
import base64
import csv
from decimal import Decimal, localcontext
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from sources import sequence_z as z

D = Decimal


class SequenceZTests(unittest.TestCase):
    def setUp(self):
        self.settings = z.SequenceZSettings()

    def assert_values(self, row, expected):
        self.assertEqual((row.printing_z, row.trinity_z, row.recoating_z), tuple(map(D, expected)))
        self.assertTrue(all(isinstance(value, D) for value in (row.printing_z, row.trinity_z, row.recoating_z)))

    def test_all_10629_original_cached_values(self):
        rows = list(csv.reader(io.StringIO(zlib.decompress(base64.b64decode(_CACHED_Z)).decode("ascii"))))
        self.assertEqual(len(rows), 3543)
        maximum_error = D(0)
        for layer, cached in enumerate(rows, 1):
            actual = z.calculate_z(self.settings, layer)
            self.assertEqual(actual.layer, layer)
            for value, raw_cached in zip((actual.printing_z, actual.trinity_z, actual.recoating_z), cached):
                error = abs(value - D(raw_cached))
                maximum_error = max(maximum_error, error)
                self.assertLessEqual(error, D("1e-12"), (layer, str(value), raw_cached))
        self.assertEqual(maximum_error, D("4.55e-14"))

    def test_original_first_middle_last_saved_rows(self):
        for layer, expected in ((1, ("364", "363.5", "364")),
                                (2, ("363.9", "363.4", "363.9")),
                                (1602, ("203.9", "203.4", "203.9")),
                                (1822, ("181.9", "181.4", "181.9")),
                                (3543, ("9.8", "9.3", "9.8"))):
            with self.subTest(layer=layer):
                self.assert_values(z.calculate_z(self.settings, layer), expected)

    def test_zero_boundary_and_negative_z_are_not_clamped(self):
        self.assert_values(z.calculate_z(self.settings, 3641), ("0", "-0.5", "0"))
        self.assert_values(z.calculate_z(self.settings, 3642), ("-0.1", "-0.6", "-0.1"))

    def test_rise_and_drop_are_separate_not_equal_printing(self):
        settings = z.SequenceZSettings("10", "0.7", "0.2")
        self.assert_values(z.calculate_z(settings, 10), ("9.1", "8.5", "8.7"))

    def test_zero_signed_and_decimal_settings(self):
        cases = [(z.SequenceZSettings(0, 0, 0), 3, ("-0.2", "-0.1", "-0.1")),
                 (z.SequenceZSettings("-10", "-.2", "-.3"), 1, ("-10", "-9.7", "-10")),
                 (z.SequenceZSettings("123.4567", ".1234", ".4321"), 17,
                  ("121.8567", "121.8333", "122.2654"))]
        for settings, layer, expected in cases:
            with self.subTest(settings=settings):
                self.assert_values(z.calculate_z(settings, layer), expected)

    def test_exact_long_inputs_ignore_small_global_decimal_context(self):
        settings = z.SequenceZSettings("12345678901234567890.12345678901234567890",
                                       ".00000000000000000001", ".00000000000000000002")
        with localcontext() as context:
            context.prec = 6
            row = z.calculate_z(settings, 2)
            self.assertEqual(context.prec, 6)
        self.assert_values(row, ("12345678901234567890.02345678901234567890",
                                 "12345678901234567890.12345678901234567889",
                                 "12345678901234567890.12345678901234567891"))

    def test_large_nonsequential_layer_direct_calculation(self):
        self.assert_values(z.calculate_z(self.settings, 1000000), ("-99635.9", "-99636.4", "-99635.9"))
        self.assert_values(z.calculate_z(self.settings, 2), ("363.9", "363.4", "363.9"))

    def test_settings_normalize_and_reject_nonfinite_or_missing(self):
        self.assertEqual(z.SequenceZSettings("364", 0.6, D(".5")), self.settings)
        for field in ("initial_z", "feeding_drop", "recoating_rise"):
            for invalid in ("", None, True, "NaN", "sNaN", "Infinity", "-Infinity", "bad"):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    z.SequenceZSettings(**{field: invalid})

    def test_invalid_layers_are_rejected(self):
        for invalid in (0, -1, True, False, 1.0, D(1), "1", None):
            with self.subTest(layer=invalid), self.assertRaises(ValueError):
                z.calculate_z(self.settings, invalid)

    def test_settings_and_results_are_immutable(self):
        with self.assertRaises(AttributeError):
            self.settings.initial_z = D(1)
        row = z.calculate_z(self.settings, 1)
        with self.assertRaises(AttributeError):
            row.printing_z = D(1)

    def test_pages_use_inclusive_range_and_last_partial_page(self):
        first = z.calculate_page(self.settings, 7, 410)
        second = z.calculate_page(self.settings, 7, 410, page_index=1)
        last = z.calculate_page(self.settings, 7, 410, page_index=2)
        self.assertIsInstance(first, tuple)
        self.assertEqual([len(first), len(second), len(last)], [200, 200, 4])
        self.assertEqual((first[0].layer, first[-1].layer, second[0].layer, last[-1].layer), (7, 206, 207, 410))
        self.assertEqual(last[-1], z.calculate_z(self.settings, 410))

    def test_billion_layer_page_calculates_only_200_rows(self):
        with patch.object(z, "calculate_z", wraps=z.calculate_z) as calculate:
            page = z.calculate_page(self.settings, 1, 1000000000, page_index=4999999)
        self.assertEqual(calculate.call_count, 200)
        self.assertEqual((page[0].layer, page[-1].layer), (999999801, 1000000000))

    def test_invalid_page_bounds(self):
        cases = [(0, 1, {}), (2, 1, {}), (1, 1000000001, {}),
                 (1, 5, {"page_index": 1}), (1, 5, {"page_index": -1}),
                 (1, 5, {"page_index": True}), (1, 5, {"page_index": 0.5}),
                 (1, 5, {"page_size": 0}), (1, 5, {"page_size": 201}),
                 (1, 5, {"page_size": True})]
        for start, end, kwargs in cases:
            with self.subTest(start=start, end=end, kwargs=kwargs), self.assertRaises(ValueError):
                z.calculate_page(self.settings, start, end, **kwargs)

    def test_csv_bom_inclusive_values_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "결과.csv"
            progress = []
            result = z.export_z_csv(self.settings, 7, 9, path, progress=lambda percent, message: progress.append((percent, message)))
            self.assertTrue(result.completed)
            self.assertFalse(result.cancelled)
            self.assertEqual((result.rows_written, result.total_rows), (3, 3))
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows[0]), 4)
            self.assertEqual(rows[1:], [["7", "363.4", "362.9", "363.4"],
                                         ["8", "363.3", "362.8", "363.3"],
                                         ["9", "363.2", "362.7", "363.2"]])
            self.assertEqual(progress[-1][0], 100)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), [path.name])

    def test_csv_precancel_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_bytes(b"original")
            event = threading.Event()
            event.set()
            result = z.export_z_csv(self.settings, 1, 100, path, cancel_event=event)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.completed)
            self.assertEqual(result.rows_written, 0)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_csv_partial_cancel_removes_temporary_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_bytes(b"original")
            event = threading.Event()
            original = z.calculate_z
            def calculate(settings, layer):
                row = original(settings, layer)
                if layer == 7:
                    event.set()
                return row
            with patch.object(z, "calculate_z", side_effect=calculate):
                result = z.export_z_csv(self.settings, 1, 1000, path, stop_event=event)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.completed)
            self.assertEqual(result.rows_written, 7)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_csv_commit_failure_keeps_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_bytes(b"original")
            with patch.object(z.os, "replace", side_effect=OSError("simulated disk failure")), self.assertRaises(OSError):
                z.export_z_csv(self.settings, 1, 10, path)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_csv_calculation_failure_keeps_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_bytes(b"original")
            original = z.calculate_z
            def calculate(settings, layer):
                if layer == 4:
                    raise RuntimeError("simulated calculation failure")
                return original(settings, layer)
            with patch.object(z, "calculate_z", side_effect=calculate), self.assertRaises(RuntimeError):
                z.export_z_csv(self.settings, 1, 10, path)
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_csv_invalid_range_or_event_conflict_does_not_touch_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_bytes(b"original")
            for start, end in ((0, 10), (5, 4), (1, 1000000001)):
                with self.assertRaises(ValueError):
                    z.export_z_csv(self.settings, start, end, path)
            with self.assertRaises(ValueError):
                z.export_z_csv(self.settings, 1, 10, path, threading.Event(), stop_event=threading.Event())
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_decimal_display_does_not_round_or_keep_negative_zero(self):
        cases = [(D("0.0000"), "0"), (D("-0.00"), "0"), (D("12.34000"), "12.34"),
                 (D("0.0000000000000000000000000000000000001234"), "0.0000000000000000000000000000000000001234")]
        for value, expected in cases:
            self.assertEqual(z.format_decimal(value), expected)


_CACHED_Z = "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"

if __name__ == "__main__":
    unittest.main()
