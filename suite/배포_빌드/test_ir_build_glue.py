"""Offline negative gates for the new IR frozen release evidence."""
import copy
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from release_exe import require_ir_check


def fixture_report():
    sha = 'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20'
    names = ('strict_manifest_resources', 'test_mode_initialization_has_no_native_process',
             'authentic_builtin_workbook_and_worker_assets',
             'production_parser_accepts_pinned_original', 'manual_images_and_previous_layer_features',
             'offline_connect_tab_resize_preserves_session',
             'asynchronous_close_preserves_csv_and_two_plots', 'suite_button_fonts_and_single_title',
             'whole_flow_has_no_native_or_com_attempts', 'callback_errors')
    checks = {name: {'passed': True} for name in names}
    checks['authentic_builtin_workbook_and_worker_assets'].update(
        workbook_sha256=sha, workbook_bytes=11523470,
        bridge_sha256='939c65d211ff9d144e956eb7b2bae26c4cfab80437c2f0a6c60efa70f4c1b215',
        mapping={'sheet': '수동 테스트', 'temperature_cell': 'B13', 'actual_power_cell': 'C13',
                 'recommendation_cell': 'D13', 'target_cell': 'B5', 'layer_cell': 'A13',
                 'slice_start_cell': 'BL13'})
    checks['production_parser_accepts_pinned_original'].update(
        metadata={'sha256': sha, 'sheet': '수동 테스트', 'first_row': 13},
        background_thread=True, excel_calculated=False)
    checks['offline_connect_tab_resize_preserves_session'].update(
        observed_layers=3, save_calls=0, real_adapters_created=0)
    checks['asynchronous_close_preserves_csv_and_two_plots'].update(
        rows=3, writer_pending_keeps_suite_alive=True, worker_retired=True,
        files=['test.csv', 'test_온도.png', 'test_IR.png'])
    measured = {'native_processes': 0, 'adapter_factories': 0, 'adapter_constructors': 0,
                'excel_controllers': 0, 'save_calls': 0}
    checks['whole_flow_has_no_native_or_com_attempts']['measured_attempts'] = dict(measured)
    return {'passed': True, 'frozen': True, 'excel_com_verified': False,
            'equipment_verified': False, 'checks': checks, 'native_operation_attempts': measured}


class IrReleaseEvidenceTests(unittest.TestCase):
    def test_exact_complete_offline_report_is_accepted(self):
        require_ir_check(fixture_report())

    def test_missing_failed_or_nonfrozen_evidence_is_rejected(self):
        good = fixture_report()
        for name in good['checks']:
            bad = copy.deepcopy(good)
            del bad['checks'][name]
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                require_ir_check(bad)
        for key in ('passed', 'frozen'):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                require_ir_check(dict(good, **{key: False}))

    def test_other_workbook_bridge_or_cell_mapping_cannot_pass(self):
        for key, wrong in (('workbook_sha256', '0' * 64), ('workbook_bytes', 1),
                           ('bridge_sha256', '0' * 64), ('mapping', {'slice_start_cell': 'A1'})):
            bad = fixture_report()
            bad['checks']['authentic_builtin_workbook_and_worker_assets'][key] = wrong
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                require_ir_check(bad)

    def test_mock_tests_cannot_be_labelled_excel_or_equipment_verification(self):
        for key in ('excel_com_verified', 'equipment_verified'):
            bad = fixture_report()
            bad[key] = True
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                require_ir_check(bad)

    def test_native_attempt_anywhere_in_offline_flow_is_rejected(self):
        for name in fixture_report()['native_operation_attempts']:
            for location in ('native_operation_attempts', 'whole_flow_has_no_native_or_com_attempts'):
                bad = fixture_report()
                counter = bad[location] if location == 'native_operation_attempts' else bad['checks'][location]['measured_attempts']
                counter[name] = 1
                with self.subTest(name=name, location=location), self.assertRaises(RuntimeError):
                    require_ir_check(bad)

    def test_lost_rows_missing_plot_and_early_worker_exit_are_rejected(self):
        for key, wrong in (('rows', 2), ('writer_pending_keeps_suite_alive', False),
                           ('worker_retired', False), ('files', ['test.csv', 'test_IR.png'])):
            bad = fixture_report()
            bad['checks']['asynchronous_close_preserves_csv_and_two_plots'][key] = wrong
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                require_ir_check(bad)

    def test_frozen_ir_actions_strip_python_paths_and_choose_gui_or_cli(self):
        build = Path(__file__).resolve().parent
        for action, executable in (('ir-feedback-exe', 'BJT_통합프로그램.exe'),
                                   ('ir-feedback-cli-exe', 'BJT_명령줄.exe')):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'app-source').mkdir()
                (root / 'app-source/source_manifest.json').write_text('{"suite_version":"1.10.0"}')
                script = root / 'run_build.py'
                shutil.copy2(build / script.name, script)
                seen = {}
                def execute(command, **kwargs):
                    seen.update(command=command, environment=kwargs['env'])
                    Path(command[-1]).write_text('{"passed":true,"frozen":true}')
                    return SimpleNamespace(returncode=0)
                runtime = SimpleNamespace(process_environment=lambda python: {})
                with mock.patch.dict(sys.modules, {'suite_runtime': runtime}), \
                        mock.patch.dict('os.environ', {'PYTHONPATH': 'unbundled-python', 'NUMBA_CACHE_DIR': 'old'}), \
                        mock.patch.object(sys, 'argv', [str(script), action]), \
                        mock.patch('subprocess.run', side_effect=execute), \
                        mock.patch.object(sys, 'path', list(sys.path)):
                    with self.assertRaises(SystemExit) as result:
                        runpy.run_path(str(script), run_name='__main__')
                self.assertEqual(result.exception.code, 0)
                self.assertEqual(Path(seen['command'][0]).name, executable)
                self.assertEqual(seen['command'][1], '--frozen-ir-feedback')
                self.assertNotIn('PYTHONPATH', seen['environment'])
                self.assertNotIn('NUMBA_CACHE_DIR', seen['environment'])
                self.assertNotIn('unbundled-python', seen['environment']['PATH'])


if __name__ == '__main__':
    unittest.main()
