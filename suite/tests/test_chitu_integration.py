"""Chitu suite adapter behavior, without native Windows GUI requirements."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'sources'))
import chitu_checker as app
import trimesh
from chitu_overlap.geometry import MeshObject, check_overlaps


class ChituIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bjt-chitu-test-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)

    def boxes(self, gap):
        files = [self.path / 'a.stl', self.path / 'b.stl']
        a, b = trimesh.creation.box(), trimesh.creation.box()
        b.apply_translation((1 + gap, 0, 0))
        a.export(files[0]); b.export(files[1])
        return files

    def test_default_zero_gap_still_rejects_surface_contact(self):
        self.assertEqual(app.DEFAULT_MIN_GAP_MM, 0)
        result = app.inspect_inputs(self.boxes(0))
        self.assertEqual(result.report['status'], 'violation')
        self.assertTrue(result.report['complete'])
        self.assertEqual(result.report['violations'][0]['status'], 'touching')

    def test_configurable_minimum_gap_and_equality(self):
        paths = self.boxes(.5)
        self.assertEqual(app.inspect_inputs(paths, 1).report['status'], 'violation')
        self.assertEqual(app.inspect_inputs(paths, .5).report['status'], 'clear')

    def test_source_files_unchanged(self):
        paths = self.boxes(.5)
        before = [path.read_bytes() for path in paths]
        app.inspect_inputs(paths)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_nonfinite_or_negative_gap_is_rejected(self):
        for value in ('NaN', 'inf', '-1', 'wrong'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.parse_gap(value)

    def test_unknown_project_is_inconclusive(self):
        source = self.path / 'source.chitu'; source.write_bytes(b'unknown')
        result = app.inspect_inputs([source])
        self.assertEqual(result.report['status'], 'inconclusive')
        self.assertFalse(result.report['complete'])
        self.assertIn('error', result.report)

    def test_cancel_before_loading_cannot_pass(self):
        event = threading.Event(); event.set()
        result = app.inspect_inputs([self.path / 'not-even-read.chitubox'], cancel_event=event)
        self.assertEqual(result.report['status'], 'inconclusive')
        self.assertTrue(result.report['cancelled'])

    def test_cancel_between_pairs_leaves_incomplete_result(self):
        objects = [MeshObject(str(i), trimesh.creation.box()) for i in range(6)]
        event = threading.Event()
        def progress(percent, message):
            if percent > 20:
                event.set()
        report = check_overlaps(objects, cancel_event=event, progress=progress)
        self.assertTrue(report['cancelled'])
        self.assertFalse(report['complete'])
        self.assertLess(len(report['pairs']), report['total_pair_count'])

    def test_json_export_preserves_settings_and_full_pair_records(self):
        result = app.inspect_inputs(self.boxes(.5), 1)
        output = self.path / 'result.json'
        self.assertTrue(app.write_result(result, output))
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['min_gap_mm'], 1)
        self.assertEqual(report['pairs'], result.report['pairs'])

    def test_existing_output_unchanged_if_save_cancelled(self):
        result = app.inspect_inputs(self.boxes(.5))
        output = self.path / 'result.json'; output.write_bytes(b'original')
        event = threading.Event(); event.set()
        self.assertFalse(app.write_result(result, output, cancel_event=event))
        self.assertEqual(output.read_bytes(), b'original')

    def test_existing_output_unchanged_on_html_generation_error(self):
        result = app.inspect_inputs(self.boxes(0))
        output = self.path / 'result.html'; output.write_bytes(b'original')
        with mock.patch('chitu_overlap.viewer.write_html_report', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                app.write_result(result, output, kind='html')
        self.assertEqual(output.read_bytes(), b'original')
        self.assertEqual(sorted(path.name for path in self.path.iterdir()), ['a.stl', 'b.stl', 'result.html'])

    def test_input_cannot_be_overwritten(self):
        paths = self.boxes(0)
        result = app.inspect_inputs(paths)
        before = paths[0].read_bytes()
        with self.assertRaises(ValueError):
            app.write_result(result, paths[0])
        self.assertEqual(paths[0].read_bytes(), before)

    def test_html_uses_selected_failed_pair_and_offline_bundle(self):
        result = app.inspect_inputs(self.boxes(0))
        output = self.path / 'result.html'
        self.assertTrue(app.write_result(result, output, kind='html', selected_pair=(0, 1)))
        page = output.read_text(encoding='utf-8')
        self.assertIn('"selected_pair":[0,1]', page)
        self.assertIn('fit(selectedPair())', page)
        self.assertNotIn('<script src=', page)

    def test_manifest_keeps_ten_existing_tool_sources_valid(self):
        from suite_loader import read_manifest, verify_sources
        manifest = read_manifest()
        self.assertEqual(manifest['suite_version'], '1.9.0')
        self.assertEqual(len(manifest['tools']), 12)
        spec = next(tool for tool in manifest['tools'] if tool['id'] == 'chitu_checker')
        self.assertFalse(spec['embedded_base'])
        self.assertEqual(spec['controller'], 'ChituCheckerApp')
        self.assertTrue(all(row['valid'] for row in verify_sources()))

    def test_cli_entry_preserves_failure_exit_code(self):
        with mock.patch('chitu_overlap.cli.main', return_value=1) as cli:
            self.assertEqual(app.main(['file.chitu', '--min-gap-mm', '1']), 1)
        cli.assert_called_once_with(['file.chitu', '--min-gap-mm', '1'])


if __name__ == '__main__':
    unittest.main()
