"""Display rounding must not change sequence exports or copied values."""
import ast
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sources import sequence_z, slice_compare_app
from display_numbers import format_number


class SequenceDisplayTests(unittest.TestCase):
    def test_display_rounding_and_raw_copy_are_separate(self):
        settings = sequence_z.SequenceZSettings(initial_z=Decimal('364.123456'))
        row = sequence_z.calculate_z(settings, 1)
        self.assertEqual(sequence_z.display_row_values(row)[1], '364.12')
        self.assertEqual(sequence_z.row_values(row)[1], '364.123456')
        captured = []
        fake = SimpleNamespace(_table_snapshot=(settings, 1, 1), is_busy=lambda: False,
            table=SimpleNamespace(selection=lambda: ('1',)), _copy=captured.append,
            status_var=SimpleNamespace(set=lambda text: None))
        sequence_z.SequenceZApp.copy_selected(fake)
        self.assertEqual(captured[0][0], sequence_z.row_values(row))

    def test_small_nonzero_remains_visible(self):
        row = sequence_z.SequenceZRow(1, Decimal('0.000001'), Decimal('0'), Decimal('0'))
        self.assertEqual(sequence_z.display_row_values(row)[1], '1.00e-6')
        self.assertEqual(sequence_z.row_values(row)[1], '0.000001')


class CompareDisplayTests(unittest.TestCase):
    def test_nested_display_does_not_mutate_raw_result(self):
        raw = {"value": 1.234567, "nested": [0.000001, 123], "path": "file1.2345.png"}
        rendered = slice_compare_app.display_value(raw)
        self.assertIn('1.23', rendered)
        self.assertIn('1.00e-06', rendered)
        self.assertIn('file1.2345.png', rendered)
        self.assertEqual(raw["value"], 1.234567)
        self.assertEqual(raw["nested"][0], 0.000001)


class AlignmentSummaryTests(unittest.TestCase):
    def test_log_uses_actual_value_without_changing_export_summary(self):
        path = Path(__file__).resolve().parents[1] / 'sources' / 'slice_alignment_checker.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == '_display_alignment_summary')
        namespace = {'format_number': format_number}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'), namespace)
        result = {'summary': '최대 0.000 mm입니다. 기준 0.123 mm를 넘었습니다.',
                  'boundary_signal_mm': 1e-6, 'boundary_status': 'suspected',
                  'settings': {'tolerance_mm': 0.12345}}
        self.assertEqual(namespace['_display_alignment_summary'](result), '최대 1.00e-06 mm입니다. 기준 0.12 mm를 넘었습니다.')
        self.assertIn('0.000 mm', result['summary'])


if __name__ == '__main__':
    unittest.main()
