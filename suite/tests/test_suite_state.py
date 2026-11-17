"""Input persistence, including real close/reopen coverage for all tools."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suite_state import (SettingsStore, _variables, capture_inputs, restore_inputs,
                         settings_path)


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='bjt-input-state-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'profile' / 'settings.json'

    def test_per_user_location_never_uses_exe_folder(self):
        name = 'LOCALAPPDATA' if os.name == 'nt' else 'XDG_CONFIG_HOME'
        with mock.patch.dict(os.environ, {name: self.directory.name}):
            self.assertEqual(settings_path(), Path(self.directory.name) / 'BJT_Tools' / 'settings.json')

    def test_unicode_and_unopened_tool_states_survive(self):
        first = SettingsStore(self.path)
        first.set_tool('chitu_checker', {'variables': {'gap_var': '1.25'},
                                         'inputs': {'paths': ['C:/작업/카메라.chitubox']}})
        first.set_tool('bjt', {'variables': {'workers_var': '6'}})
        first.save()
        second = SettingsStore(self.path)
        second.set_tool('bjt', {'variables': {'workers_var': '8'}})
        second.save()
        third = SettingsStore(self.path)
        self.assertEqual(third.tool_state('chitu_checker'), first.tool_state('chitu_checker'))
        self.assertEqual(third.tool_state('bjt')['variables']['workers_var'], '8')

    def test_failed_atomic_replace_leaves_previous_file_and_removes_temp(self):
        store = SettingsStore(self.path)
        store.set_tool('overlay', {'variables': {'threshold_var': '127'}})
        store.save()
        before = self.path.read_bytes()
        store.set_tool('overlay', {'variables': {'threshold_var': '128'}})
        with mock.patch('suite_state.os.replace', side_effect=PermissionError('read-only')):
            with self.assertRaises(PermissionError):
                store.save()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob('.settings-*.tmp')))

    def test_corrupt_file_defaults_and_preserves_recovery_copy(self):
        self.path.parent.mkdir()
        bad = b'{broken\xff'
        self.path.write_bytes(bad)
        store = SettingsStore(self.path)
        self.assertTrue(store.load_error)
        self.assertEqual(store.tool_state('bjt'), {})
        store.save()
        backups = list(self.path.parent.glob('settings.json.corrupt-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), bad)
        self.assertEqual(json.loads(self.path.read_text())['tools'], {})

    def test_unknown_schema_and_non_object_tools_do_not_break_start(self):
        self.path.parent.mkdir()
        for data in ({'schema_version': 999, 'tools': {}},
                     {'schema_version': 1, 'tools': []}, []):
            with self.subTest(data=data):
                self.path.write_text(json.dumps(data))
                self.assertTrue(SettingsStore(self.path).load_error)

    def test_memory_only_test_store_does_not_write(self):
        store = SettingsStore(None)
        store.set_tool('sequence_z', {'variables': {'initial_z_var': '360'}})
        with mock.patch('suite_state.tempfile.NamedTemporaryFile', side_effect=AssertionError('write')):
            store.save()
        self.assertEqual(store.tool_state('sequence_z')['variables']['initial_z_var'], '360')

    def test_existing_legacy_inputs_migrate_once_per_tool_without_legacy_writes(self):
        from suite_ui import SuiteApp
        for key in ('bjt', 'arrange', 'thermal'):
            with self.subTest(tool=key):
                legacy_reads, legacy_writes = [], []

                class Controller:
                    def _load_settings(self):
                        legacy_reads.append(key)
                        self.loaded_input = 'previous user value'
                        return {'canvas_width': 1234} if key == 'arrange' else True

                    def _load_saved_settings(self, silent=True):
                        return self._legacy_thermal(silent)

                    def _legacy_thermal(self, silent):
                        legacy_reads.append(key)
                        self.loaded_input = 'previous user value'
                        return True

                    def _save_settings(self, silent=True):
                        legacy_writes.append(key)

                module = SimpleNamespace(Controller=Controller)
                app = SuiteApp.__new__(SuiteApp)
                app.specs = {key: {'controller': 'Controller'}}
                app.settings_store = SettingsStore(self.path)
                # A different lazy tool may already have saved the suite file.
                app.settings_store.set_tool('unopened_other_tool', {'variables': {'x': '1'}})
                app.settings_store.save()
                app.test_mode = False
                app._configure_tool_persistence(key, module)
                instance = Controller()
                load_name = '_load_saved_settings' if key == 'thermal' else '_load_settings'
                getattr(instance, load_name)()
                self.assertEqual(instance.loaded_input, 'previous user value')
                self.assertEqual(legacy_reads, [key])
                instance._save_settings()
                self.assertEqual(legacy_writes, [])

                # Established central values have precedence, even if legacy
                # files still exist. Test mode never reads a real home profile.
                app.settings_store.set_tool(key, {'variables': {}})
                getattr(Controller(), load_name)()
                self.assertEqual(legacy_reads, [key])
                app.settings_store.data['tools'].pop(key)
                app.test_mode = True
                getattr(Controller(), load_name)()
                self.assertEqual(legacy_reads, [key])


class InputSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tcl = tk.Tcl()

    def test_raw_invalid_text_does_not_call_get_or_persist_progress(self):
        controller = SimpleNamespace(initial_z_var=tk.StringVar(self.tcl, value='-.'),
                                     nested={'dpi': tk.StringVar(self.tcl, value='')},
                                     status_var=tk.StringVar(self.tcl, value='RUNNING'),
                                     progress_var=tk.DoubleVar(self.tcl, value=70))
        snapshot = capture_inputs('sequence_z', controller)
        self.assertEqual(snapshot['variables'], {'initial_z_var': '-.', 'nested.dpi': ''})
        controller.initial_z_var.set('123')
        restore_inputs('sequence_z', controller, snapshot)
        self.assertEqual(controller.initial_z_var.get(), '-.')
        self.assertEqual(controller.progress_var.get(), 70)

    def test_invalid_numeric_tcl_value_is_saved_but_safe_default_restored(self):
        controller = SimpleNamespace(canvas_width_var=tk.IntVar(self.tcl, value=2000))
        controller.canvas_width_var.set('unfinished')
        snapshot = capture_inputs('other', controller)
        self.assertEqual(snapshot['variables']['canvas_width_var'], 'unfinished')
        controller.canvas_width_var.set(2000)
        warnings = restore_inputs('other', controller, snapshot)
        self.assertEqual(controller.canvas_width_var.get(), 2000)
        self.assertEqual(len(warnings), 1)

    def test_mode_preset_traces_cannot_overwrite_custom_fields(self):
        controller = SimpleNamespace(mode_var=tk.StringVar(self.tcl, value='싱글'),
                                     width_var=tk.StringVar(self.tcl, value='100'),
                                     dpi_x_var=tk.StringVar(self.tcl, value='400'))
        seen = []
        controller.mode_var.trace_add('write', lambda *args: (
            seen.append('preset'), controller.width_var.set('200')))
        snapshot = {'variables': {'width_var': '333', 'dpi_x_var': '501', 'mode_var': '멀티'}}
        restore_inputs('other', controller, snapshot)
        self.assertEqual(controller.width_var.get(), '333')
        self.assertEqual(controller.dpi_x_var.get(), '501')
        self.assertEqual(seen, [])
        controller.mode_var.set('싱글')
        self.assertEqual(seen, ['preset'])

    def test_chitu_display_fields_are_excluded_and_paths_are_restored(self):
        controller = SimpleNamespace(input_paths=['a.stl', 'b.stl'],
                                     input_var=tk.StringVar(self.tcl, value='summary'),
                                     gap_var=tk.StringVar(self.tcl, value='2.0'),
                                     drop_hint_var=tk.StringVar(self.tcl, value='DnD'),
                                     _refresh_actions=lambda: None)
        controller.set_inputs = lambda paths: setattr(controller, 'input_paths', paths)
        snapshot = capture_inputs('chitu_checker', controller)
        self.assertEqual(snapshot['variables'], {'gap_var': '2.0'})
        controller.input_paths = []
        restore_inputs('chitu_checker', controller, snapshot)
        self.assertEqual(controller.input_paths, ['a.stl', 'b.stl'])


GUI_AVAILABLE = os.name == 'nt' or bool(os.environ.get('DISPLAY'))


@unittest.skipUnless(GUI_AVAILABLE, 'real Tk display required for all-tool roundtrip')
class AllToolCloseReopenTests(unittest.TestCase):
    def test_save_failure_is_recorded_and_does_not_prevent_orderly_close(self):
        from suite_ui import SuiteApp, create_root
        root = create_root()
        root.withdraw()
        app = SuiteApp(root, test_mode=True)
        try:
            with (mock.patch.object(app.settings_store, 'save', side_effect=PermissionError('read-only')),
                  mock.patch.object(app, '_write_error')):
                app.request_close(confirm=False)
            self.assertEqual(app.close_errors, ['read-only'])
            self.assertTrue(app._settings_closed_saved)
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass

    def test_all_thirteen_controllers_close_and_reopen_with_input_collections(self):
        from suite_ui import SuiteApp, create_root
        from PIL import Image
        import numpy as np
        with tempfile.TemporaryDirectory(prefix='bjt-state-gui-') as directory:
            base = Path(directory)
            path = base / 'profile' / 'settings.json'
            image = base / '한글 입력 001.png'
            Image.new('L', (4, 5), 127).save(image)
            roots = []
            callback_errors = []
            try:
                def open_suite():
                    root = create_root()
                    root.withdraw()
                    roots.append(root)
                    app = SuiteApp(root, test_mode=True, settings_path=path)
                    root.report_callback_exception = lambda kind, error, trace: callback_errors.append(str(error))
                    for key in app.specs:
                        app.mount_tool(key, app.loader.load(key))
                    root.update_idletasks()
                    return root, app

                def close_suite(root, app):
                    app.request_close(confirm=False)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        try:
                            if not root.winfo_exists():
                                return
                            root.update()
                        except tk.TclError:
                            return
                        time.sleep(.01)
                    self.fail('Suite did not finish asynchronous IR archive/worker shutdown')

                root, first = open_suite()
                self.assertEqual(len(first.controllers), 13)
                c = first.controllers
                c['bjt'].input_var.set(str(base / '새 모델.stl'))
                c['bjt'].output_var.set(str(base / '별도 출력.stl'))
                c['bjt'].dpi_x_var.set('501')
                arrange = c['arrange']
                module = first.loader.modules['arrange']
                arrange.canvas_width_var.set(1234)
                arrange.sources = {'s1': module.SourceSet('s1', base, [image])}
                arrange.source_by_folder = {str(base): 's1'}
                arrange.placements = {'p1': module.Placement('p1', 's1', '첫 입력', 12, 34, False, 29)}
                arrange.placement_order = ['p1']
                arrange.selected_id = 'p1'
                arrange.output_folder_manual = True
                arrange.output_folder_var.set(str(base / '배치 출력'))
                c['overlay'].folders = [base]
                c['overlay'].threshold_var.set('127')
                c['exchanger'].a_folder_var.set(str(base / 'A'))
                c['exchanger'].b_folder_var.set(str(base / 'B'))
                c['exchanger'].a_start_var.set('22')
                c['alignment'].source = [str(image)]
                c['alignment'].vars['dpi'].set('501')
                c['alignment'].scan_mode.set('full')
                thermal = c['thermal']
                thermal.nx.set('6')
                # These are unbound translated Comboboxes, rather than Tk
                # variables. Their persisted value must remain canonical.
                for widget_name in ('box_mat', 'sic_type', 'k_mode', 'o2_tau_mode'):
                    widget = getattr(thermal, widget_name)
                    values = getattr(widget, '_canonical_values', ())
                    alternative = next((value for value in values if value != widget.get()), None)
                    if alternative is not None:
                        widget.set(alternative)
                if hasattr(thermal, 'language_var'):
                    thermal.language_var.set('en')
                thermal.profile_tree.delete(*thermal.profile_tree.get_children())
                for values in (('0', '23.5'), ('10.25', '600'), ('30', '25')):
                    thermal.profile_tree.insert('', 'end', values=values)
                thermal.tc_path = str(base / '측정.csv')
                thermal.tc_time_min = np.array([0., 1., 2.])
                thermal.tc_center_c = np.array([22., 25., 30.])
                c['compare'].left.set_paths([str(image)])
                c['compare'].right.set_paths([str(image)])
                c['compare'].verify_identical.set(True)
                c['number_tools'].source_var.set(str(base / '이미지001.tif'))
                c['number_tools'].dup_min_var.set('2')
                c['sequence_z'].initial_z_var.set('360.5')
                c['sequence_z'].end_var.set('200')
                converter = c['m2_converter']
                converter.input_paths = [str(image)]
                converter.mode_var.set('멀티')
                converter.output_var.set(str(base / '이미지 출력'))
                for field, value in (('width_var', '123'), ('height_var', '234'),
                                     ('dpi_x_var', '501.25'), ('dpi_y_var', '502.5')):
                    getattr(converter, field).set(value)
                c['chitu_checker'].set_inputs([str(base / '카메라.chitubox')])
                c['chitu_checker'].gap_var.set('1.5')
                c['chitu_checker'].show_all_var.set(True)
                c['ir_feedback'].target_temperature.set('71.25')
                c['ir_feedback'].maximum_ir_percent.set('79')
                c['ir_feedback'].slice_panel.radius.set('4.5')
                c['ir_feedback'].slice_panel.paths = (str(image),)
                expected = {key: capture_inputs(key, value) for key, value in c.items()}
                close_suite(root, first)
                self.assertFalse(first.close_errors)
                self.assertTrue(path.exists())
                root, second = open_suite()
                for key, controller in second.controllers.items():
                    actual = capture_inputs(key, controller)
                    with self.subTest(tool=key):
                        self.assertEqual(actual, expected[key])
                self.assertFalse(second.settings_warnings)
                close_suite(root, second)
                self.assertFalse(second.close_errors)
                self.assertFalse(callback_errors)
                report_path = os.environ.get('BJT_STATE_TEST_REPORT')
                if report_path:
                    report = {'passed': True, 'tool_count': len(expected),
                              'python_callback_errors': callback_errors,
                              'tools': {key: {
                                  'variable_count': len(state['variables']),
                                  'variables': sorted(state['variables']),
                                  'unbound_entry_count': len(state.get('entries', {})),
                                  'unbound_entries': sorted(state.get('entries', {})),
                                  'input_models': sorted(state['inputs']),
                              } for key, state in expected.items()}}
                    target = Path(report_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                for root in roots:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass


if __name__ == '__main__':
    unittest.main()
