"""IR integration contracts; all controllers, leases and writers are owned fakes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ir_feedback_tool as integration
from ir_feedback.excel_workbook import PreparedWorkbook, WorkbookSpec
from ir_feedback.plot_archive import PlotArchive
from ir_feedback.layer_charts import LayerPoint
from ir_feedback.session import SessionController
from suite_loader import read_manifest, verify_sources


NON_IR_SOURCE_HASHES = {
    'bjt': 'ab9bdb6771c8c42752e38280df87b97edc76884e53644ab212467247b8099372',
    'arrange': 'e642a123eeba3a8664d79a1167883f4561f181496e2f1767fccdf3274c69ea8d',
    'overlay': '5455ce414fdfe390c8244a61de6c2cbbf65117567e9fcbb14cef5de3b0dd8264',
    'exchanger': '71c8ae1e272cdbfb0f21acae101628073c5c04bc50ac73f5e57240cb33856a5b',
    'alignment': 'aa54ddb053b84e062b2152bdccb2ef374651925622a726dba246e9d54069c648',
    'thermal': '2a2d75f97786ac0fc1738cee66afc64be7cdfae0fd624ff3ef329609c52faece',
    'compare': '08d9217ac71333052cbd9d906c4f61020f6901f34f0caab809a98b8354ea80c7',
    'number_tools': '280cacc658aeb03b01de9d326f01cb0d4b5450102d9383f684cc8d1addb56dc5',
    'sequence_z': '3ad9c345ba2a5758107c29dd3aad125fad91b26d96ba6fe3eeda9aa9dd5c4bd8',
    'm2_converter': '856efcb9b94846719c7261991c3964c45418463c122999ed8398c13b41faf7da',
    'chitu_checker': 'bb5c6845a4727ab49fc4dc8fc9c913bb0e16248aba896e849d0c76cc82088fe0',
    # v1.10.1 intentionally changes the packing tab layout for CHITUBOX export.
    'nesting': 'd8c89fc11b746428f3031f1a42024597c40b32421d06b5e40c36b3c27fa770f0',
}


class ResourceAndNamespaceTests(unittest.TestCase):
    def test_non_ir_sources_match_reviewed_hashes(self):
        tools = {item['id']: item for item in read_manifest()['tools']}
        self.assertEqual(set(tools), set(NON_IR_SOURCE_HASHES) | {'ir_feedback'})
        for key, checksum in NON_IR_SOURCE_HASHES.items():
            with self.subTest(tool=key):
                path = ROOT / tools[key]['source']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)
                self.assertEqual(tools[key]['sha256'], checksum)
        self.assertTrue(all(item['valid'] for item in verify_sources()))

    def test_builtin_uses_original_recovery_sheet_and_pinned_mapping(self):
        raw = integration.BUILTIN_WORKBOOK.read_bytes()
        self.assertEqual(len(raw), 11_523_470)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20')
        spec = integration.builtin_workbook_spec()
        self.assertEqual(spec.path, str(integration.BUILTIN_WORKBOOK))
        self.assertEqual(spec.expected_sha256, integration.BUILTIN_SHA256)
        self.assertEqual((spec.sheet, spec.temperature_cell, spec.actual_power_cell,
                          spec.recommendation_cell, spec.target_cell, spec.layer_cell,
                          spec.slice_start_cell), ('수동 테스트', 'B13', 'C13', 'D13', 'B5', 'A13', 'BL13'))
        spec.validate()
        self.assertEqual(spec.slice_cells()[0][1], 'BL13')
        self.assertEqual(spec.slice_cells()[-1][1], 'BW13')

    def test_modified_builtin_is_rejected_before_formula_inspection(self):
        with mock.patch('ir_feedback.excel_workbook.inspect_workbook',
                        side_effect=AssertionError('must not scan modified built-in workbook')) as inspect:
            with self.assertRaisesRegex(ValueError, '계산시트가 변경'):
                PreparedWorkbook.from_bytes(integration.builtin_workbook_spec(), b'tampered')
            inspect.assert_not_called()

    def test_external_workbook_pin_remains_optional(self):
        spec = WorkbookSpec('custom.xlsx', '수동 테스트', 'B13', 'C13', 'D13', 'B5', 'A13', 'BL13')
        self.assertIsNone(spec.expected_sha256)
        spec.validate()
        metadata = {'sha256': hashlib.sha256(b'owned fake').hexdigest()}
        with mock.patch('ir_feedback.excel_workbook.inspect_workbook', return_value=metadata):
            prepared = PreparedWorkbook.from_bytes(spec, b'owned fake')
        self.assertEqual(prepared.data, b'owned fake')

    def test_imports_preserve_foreign_flat_modules_and_sys_path(self):
        script = '''
import sys, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
names = ('session', 'adapter_contracts', 'standalone_ir', 'excel_workbook', 'slice_data')
sentinels = {name: types.ModuleType(name) for name in names}
sys.modules.update(sentinels)
before = list(sys.path)
import ir_feedback_tool
import ir_feedback.session, ir_feedback.adapter_contracts
assert sys.path == before
assert all(sys.modules[name] is value for name, value in sentinels.items())
assert ir_feedback.session.SessionController.__module__ == 'ir_feedback.session'
assert ir_feedback.adapter_contracts.AdapterError.__module__ == 'ir_feedback.adapter_contracts'
'''
        run = subprocess.run([sys.executable, '-c', script, str(ROOT)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class LeaseAndShutdownTests(unittest.TestCase):
    def fake_view(self, acquired=True):
        lease = mock.Mock()
        lease.acquire.return_value = acquired
        view = integration.App.__new__(integration.App)
        view.closing = False
        view.controller = SimpleNamespace(closed=False, thread=None)
        view._lease = None
        view._lease_factory = mock.Mock(return_value=lease)
        view._append_log = mock.Mock()
        view.root = object()
        return view, lease

    def test_failed_lease_prevents_any_connect(self):
        view, lease = self.fake_view(False)
        with mock.patch.object(integration.ThermalFeedbackWindow, 'connect') as connect:
            view.connect()
        connect.assert_not_called()
        lease.acquire.assert_called_once_with()
        self.assertIsNone(view._lease)

    def test_lease_is_reused_and_released_only_after_host_destruction(self):
        view, lease = self.fake_view()
        with mock.patch.object(integration.ThermalFeedbackWindow, 'connect') as connect:
            view.connect()
            view.connect()
        self.assertEqual(connect.call_count, 2)
        lease.acquire.assert_called_once_with()
        view._release_on_destroy(SimpleNamespace(widget=object()))
        lease.close.assert_not_called()
        view._release_on_destroy(SimpleNamespace(widget=view.root))
        view._release_on_destroy(SimpleNamespace(widget=view.root))
        lease.close.assert_called_once_with()
        self.assertIsNone(view._lease)

    def test_lease_error_does_not_connect(self):
        view, lease = self.fake_view()
        lease.acquire.side_effect = OSError('owned fake refusal')
        with mock.patch.object(integration.ThermalFeedbackWindow, 'connect') as connect:
            view.connect()
        connect.assert_not_called()
        self.assertIsNone(view._lease)

    def test_close_waits_for_slice_worker_and_excel_retirement(self):
        view, _ = self.fake_view()
        worker = mock.Mock()
        view.slice_panel = SimpleNamespace(worker=worker)
        with mock.patch.object(integration.ThermalFeedbackWindow, '_close_workers_ready', return_value=True), \
                mock.patch.object(integration._EXCEL_FENCE, 'retirement_status', return_value={'verified': False}) as fence:
            worker.is_alive.return_value = True
            self.assertFalse(view._close_workers_ready())
            fence.assert_not_called()
            worker.is_alive.return_value = False
            self.assertFalse(view._close_workers_ready())
            fence.return_value = {'verified': True}
            self.assertTrue(view._close_workers_ready())

    def test_close_retirement_failure_is_not_treated_as_success(self):
        view, _ = self.fake_view()
        view.slice_panel = SimpleNamespace(worker=None)
        with mock.patch.object(integration.ThermalFeedbackWindow, '_close_workers_ready', return_value=True), \
                mock.patch.object(integration._EXCEL_FENCE, 'retirement_status', side_effect=RuntimeError('owned fake')):
            self.assertFalse(view._close_workers_ready())

    def test_suite_keeps_tk_alive_while_ir_archive_is_pending(self):
        from suite_ui import SuiteApp
        app = SuiteApp.__new__(SuiteApp)
        app._pending_tool_closes = {'ir_feedback'}
        app.hosts = {'ir_feedback': SimpleNamespace(_disposed=False)}
        app.controllers = {'ir_feedback': SimpleNamespace(suite_close_cancelled=False)}
        app.status = mock.Mock()
        app.root = mock.Mock()
        app._finish_close()
        app.root.destroy.assert_not_called()
        app.root.after.assert_called_once_with(100, app._finish_close)

    def test_failed_archive_cancel_cancels_suite_close_and_keeps_recovery_view(self):
        from suite_ui import SuiteApp
        app = SuiteApp.__new__(SuiteApp)
        app._pending_tool_closes = {'ir_feedback'}
        app.hosts = {'ir_feedback': SimpleNamespace(_disposed=False)}
        app.controllers = {'ir_feedback': SimpleNamespace(suite_close_cancelled=True)}
        app.closing = True
        app._settings_closed_saved = True
        app.status = mock.Mock()
        app.stop_button = mock.Mock()
        app.root = mock.Mock()
        app._finish_close()
        self.assertFalse(app.closing)
        self.assertFalse(app._settings_closed_saved)
        app.root.destroy.assert_not_called()
        app.root.after.assert_not_called()

    def test_lazy_ir_mount_during_close_starts_one_cleanup_before_root_can_close(self):
        from suite_ui import SuiteApp
        app = SuiteApp.__new__(SuiteApp)
        app.controllers = {}
        app.hosts = {}
        app.loading = {'ir_feedback'}
        app._pending_tool_closes = set()
        app.closing = True
        app._settings_closed_saved = True
        app.status = mock.Mock()
        app.root = mock.Mock()
        app._persist_before_close = mock.Mock()
        app._finish_close()
        app.root.destroy.assert_not_called()
        app._persist_before_close.assert_not_called()
        events = []
        app._persist_before_close.side_effect = lambda: events.append('inputs captured')
        controller = SimpleNamespace(suite_close_cancelled=False,
            request_suite_close=mock.Mock(side_effect=lambda: events.append('IR close requested')))
        app.controllers['ir_feedback'] = controller
        app.hosts['ir_feedback'] = SimpleNamespace(_disposed=False)
        app.loading.clear()
        app._finish_close()
        app._finish_close()
        self.assertEqual(events, ['inputs captured', 'IR close requested'])
        controller.request_suite_close.assert_called_once_with()
        self.assertEqual(app._pending_tool_closes, {'ir_feedback'})
        app.root.destroy.assert_not_called()

    def test_ir_close_request_exception_preserves_root_and_allows_recovery(self):
        from suite_ui import SuiteApp
        app = SuiteApp.__new__(SuiteApp)
        app.controllers = {'ir_feedback': SimpleNamespace(
            request_suite_close=mock.Mock(side_effect=RuntimeError('Owned cleanup refusal')))}
        app.hosts = {'ir_feedback': SimpleNamespace(_disposed=False)}
        app._pending_tool_closes = set()
        app.closing = True
        app._settings_closed_saved = True
        app.close_errors = []
        app._persist_before_close = mock.Mock()
        app._write_error = mock.Mock()
        app.status = mock.Mock()
        app.stop_button = mock.Mock()
        app.root = mock.Mock()
        self.assertFalse(app._start_ir_close())
        self.assertFalse(app.closing)
        self.assertFalse(app._settings_closed_saved)
        self.assertEqual(app._pending_tool_closes, set())
        self.assertEqual(len(app.close_errors), 1)
        self.assertIn('Owned cleanup refusal', app.close_errors[0])
        app._write_error.assert_called_once_with(app.close_errors[0])
        app.stop_button.configure.assert_called_once_with(state='normal')
        app.root.destroy.assert_not_called()

    def test_busy_uses_owned_slice_worker_instead_of_stale_progress_flag(self):
        view, _ = self.fake_view()
        view.controller = SimpleNamespace(worker_alive=False)
        worker = mock.Mock()
        worker.is_alive.return_value = False
        view.slice_panel = SimpleNamespace(worker=worker, running=True)
        view.archive = SimpleNamespace(busy=False)
        self.assertFalse(view.is_busy)
        worker.is_alive.return_value = True
        view.slice_panel.running = False
        self.assertTrue(view.is_busy)
        worker.is_alive.return_value = False
        view.archive.busy = True
        self.assertTrue(view.is_busy)
        view.archive.busy = False
        view.controller.worker_alive = True
        self.assertTrue(view.is_busy)

    def test_malformed_saved_cap_never_widens_and_non_string_algorithm_is_safe(self):
        class Variable:
            def __init__(self, value): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value
        view, _ = self.fake_view()
        view.machine = Variable('M1')
        view.target_temperature = Variable('65')
        view.maximum_ir_percent = Variable('80')
        view._custom_cells = dict(integration.CELL_DEFAULTS)
        view._show_algorithm = mock.Mock()
        view.slice_panel = SimpleNamespace(debounce=None, dataset=object(), paths=(),
            selected=Variable(''), summary=Variable(''), clear_session=mock.Mock(),
            **{name: Variable('') for name in integration.SLICE_INPUTS})
        for invalid in (True, False, [], [90], {}, 90, None):
            with self.subTest(cap=invalid):
                view.maximum_ir_percent.set('80')
                view.restore_input_state({'maximum_ir': invalid, 'algorithm': ['image_recovery']})
                self.assertEqual(view.maximum_ir_percent.get(), '')
                view._show_algorithm.assert_called_with('image_recovery')
                self.assertIsNone(view.slice_panel.dataset)


@unittest.skipUnless(os.name == 'nt', 'Native Windows named-mutex contention')
class NativeMutexTests(unittest.TestCase):
    def test_standalone_first_and_suite_first_exclude_other_process_until_release(self):
        owner_script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
if sys.argv[2] == 'suite':
    from ir_feedback_tool import SingleInstance
else:
    from ir_feedback.single_instance import SingleInstance
instance = SingleInstance()
try:
    acquired = instance.acquire()
    Path(sys.argv[3]).write_text(json.dumps({'acquired': acquired, 'name': instance.name}))
    if acquired: sys.stdin.readline()
finally:
    instance.close()
'''
        contender_script = '''
import json, sys
sys.path.insert(0, sys.argv[1])
if sys.argv[2] == 'suite':
    from ir_feedback_tool import SingleInstance
else:
    from ir_feedback.single_instance import SingleInstance
instance = SingleInstance()
try: print(json.dumps({'acquired': instance.acquire(), 'name': instance.name}), flush=True)
finally: instance.close()
'''
        for first, second in (('standalone', 'suite'), ('suite', 'standalone')):
            with self.subTest(first=first), tempfile.TemporaryDirectory(prefix='bjt-ir-lease-test-') as directory:
                ready = Path(directory) / 'ready.json'
                owner = subprocess.Popen([sys.executable, '-c', owner_script, str(ROOT), first, str(ready)],
                                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 15
                    while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists(), 'Owned mutex fixture did not become ready')
                    record = json.loads(ready.read_text())
                    self.assertTrue(record['acquired'])
                    self.assertEqual(record['name'], r'Local\MADDE.IRFeedback.Standalone')
                    blocked = subprocess.run([sys.executable, '-c', contender_script, str(ROOT), second],
                                             capture_output=True, text=True, timeout=15, check=True)
                    response = json.loads(blocked.stdout)
                    self.assertFalse(response['acquired'])
                    self.assertEqual(response['name'], record['name'])
                    owner.communicate('release\n', timeout=15)
                    self.assertEqual(owner.returncode, 0)
                    reopened = subprocess.run([sys.executable, '-c', contender_script, str(ROOT), second],
                                              capture_output=True, text=True, timeout=15, check=True)
                    self.assertTrue(json.loads(reopened.stdout)['acquired'])
                finally:
                    if owner.poll() is None:
                        owner.kill()
                        owner.communicate(timeout=5)


class IntegratedViewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as error:
            self.skipTest('Tk display unavailable: ' + str(error))
        self.directory = tempfile.TemporaryDirectory(prefix='bjt-ir-suite-test-')
        self.addCleanup(self.directory.cleanup)
        self.archive = PlotArchive(directories=[self.directory.name])
        self.error_log_patch = mock.patch('ir_feedback.standalone_ir.write_error_log')
        self.error_log_patch.start()
        self.addCleanup(self.error_log_patch.stop)
        self.discoverer = mock.Mock(return_value=[])
        self.controller = SessionController(runner=lambda *args: None)
        self.view = integration.App(self.root, test_mode=True, controller=self.controller,
                                    discoverer=self.discoverer, archive=self.archive)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.close()
        self.archive.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def alive(self):
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def pump_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            self.root.update()
            time.sleep(.01)
        self.assertTrue(predicate(), 'Owned integration fixture did not reach expected state')

    def pump_for(self, duration=.1):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)

    def test_default_is_builtin_and_mount_never_connects_or_discovers(self):
        self.assertEqual(self.view.algorithm_type.get(), integration.BUILTIN_LABEL)
        self.assertEqual(self.view._selected_workbook_spec(), integration.builtin_workbook_spec())
        self.assertIsNone(self.controller.thread)
        self.discoverer.assert_not_called()
        self.assertIsNone(self.view._lease)
        self.assertEqual(str(self.view.browse_button.cget('state')), 'disabled')

    def test_switching_preserves_custom_workbook_cells_and_json_path(self):
        view = self.view
        view.algorithm_type.set('Excel 수식 파일')
        view._algorithm_changed()
        view.settings_path.set('C:/사용자/사용자 계산.xlsx')
        view.workbook_temperature_cell.set('E21')
        view.workbook_result_cell.set('G21')
        view.algorithm_type.set('기본 알고리즘')
        view._algorithm_changed()
        view.settings_path.set('C:/사용자/설정.json')
        view.algorithm_type.set(integration.BUILTIN_LABEL)
        view._algorithm_changed()
        self.assertEqual(view._selected_workbook_spec(), integration.builtin_workbook_spec())
        view.algorithm_type.set('Excel 수식 파일')
        view._algorithm_changed()
        self.assertEqual(view.settings_path.get(), 'C:/사용자/사용자 계산.xlsx')
        self.assertEqual(view.workbook_temperature_cell.get(), 'E21')
        self.assertEqual(view.workbook_result_cell.get(), 'G21')
        view.algorithm_type.set('기본 알고리즘')
        view._algorithm_changed()
        self.assertEqual(view.settings_path.get(), 'C:/사용자/설정.json')

    def test_input_roundtrip_omits_runtime_authority_and_requires_image_reanalysis(self):
        from suite_state import capture_inputs, restore_inputs
        view = self.view
        view.machine.set('S3')
        view.target_temperature.set('71.5')
        view.maximum_ir_percent.set('80')
        view.slice_panel.radius.set('4.25')
        view.slice_panel.dpi_x.set('501')
        view.slice_panel.dpi_y.set('502')
        view.slice_panel.paths = ('C:/슬라이싱/1.png', 'C:/슬라이싱/2.png')
        view.status.set('must never restore connected status')
        view.host.set('must never restore PID')
        state = capture_inputs('ir_feedback', view)
        encoded = json.dumps(state, ensure_ascii=False)
        self.assertNotIn('must never restore', encoded)
        self.assertNotIn('active_dataset', encoded)
        view.restore_input_state({'machine': 'M1', 'target': '65', 'maximum_ir': '100',
                                  'slice_parameters': {'radius': '1'}, 'slice_paths': []})
        self.assertEqual(view.target_temperature.get(), '65')
        self.assertEqual(view.slice_panel.paths, ())
        self.assertEqual(restore_inputs('ir_feedback', view, state), [])
        self.assertEqual(view.machine.get(), 'S3')
        self.assertEqual(view.target_temperature.get(), '71.5')
        self.assertEqual(view.maximum_ir_percent.get(), '80')
        self.assertEqual(view.slice_panel.radius.get(), '4.25')
        self.assertEqual(view.slice_panel.paths, ('C:/슬라이싱/1.png', 'C:/슬라이싱/2.png'))
        self.assertIsNone(view.slice_panel.dataset)
        self.assertIsNone(view.slice_panel.active_dataset)
        self.assertIsNone(self.controller.thread)
        self.assertFalse(view.slice_panel.running)
        self.assertIsNone(view.slice_panel.debounce)

    def test_restored_unknown_algorithm_and_machine_do_not_create_live_authority(self):
        self.view.restore_input_state({'algorithm': 'unknown', 'machine': 'M2', 'connected': True,
                                       'pid': 111, 'active_dataset': {'fake': True}})
        self.assertEqual(self.view.algorithm_type.get(), integration.BUILTIN_LABEL)
        self.assertEqual(self.view.machine.get(), 'M1')
        self.assertIsNone(self.controller.thread)
        self.assertIsNone(self.view.slice_panel.active_dataset)

    def test_shutdown_waits_for_worker_discovery_slice_and_excel_fence_separately(self):
        view = self.view
        control_worker, discovery_worker, slice_worker = (mock.Mock() for _ in range(3))
        control_worker.is_alive.return_value = True
        discovery_worker.is_alive.return_value = False
        slice_worker.is_alive.return_value = False
        self.controller.thread = control_worker
        view.discovery_thread = discovery_worker
        view.slice_panel.worker = slice_worker
        view.maximum_ir_percent.set('77')
        with mock.patch.object(integration._EXCEL_FENCE, 'retirement_status', return_value={'verified': True}) as fence:
            view.request_suite_close()
            self.pump_for()
            self.assertTrue(self.alive())
            self.assertFalse(view._closing_archive_started)
            control_worker.is_alive.return_value = False
            discovery_worker.is_alive.return_value = True
            self.pump_for()
            self.assertTrue(self.alive())
            self.assertFalse(view._closing_archive_started)
            discovery_worker.is_alive.return_value = False
            slice_worker.is_alive.return_value = True
            self.pump_for()
            self.assertTrue(self.alive())
            self.assertFalse(view._closing_archive_started)
            slice_worker.is_alive.return_value = False
            fence.return_value = {'verified': False}
            self.pump_for()
            self.assertTrue(self.alive())
            self.assertFalse(view._closing_archive_started)
            fence.return_value = {'verified': True}
            self.pump_until(lambda: not self.alive())
        self.assertEqual(view.maximum_ir_preference.load(), 77)

    def test_shutdown_waits_for_slow_csv_and_both_plot_writes(self):
        started, release = threading.Event(), threading.Event()
        output = Path(self.directory.name)
        from ir_feedback.layer_charts import _write_csv
        def slow_writer(path, points):
            started.set()
            if not release.wait(5):
                raise TimeoutError('Owned test writer release missing')
            _write_csv(path, points)
        self.archive.writer = slow_writer
        point = LayerPoint(1, 64.5, 60, 60, 59, 59, True)
        self.archive.capture(('owned-fixture',), [point], 'M1', 65)
        try:
            self.view.request_suite_close()
            self.pump_until(started.is_set)
            self.assertTrue(self.alive())
            self.assertTrue(self.archive.busy)
            self.assertEqual([], list(output.glob('*.png')))
            release.set()
            self.pump_until(lambda: not self.alive())
            self.assertEqual(1, len(list(output.glob('*.csv'))))
            self.assertEqual(2, len(list(output.glob('*.png'))))
        finally:
            release.set()

    def test_archive_failure_cancel_and_retry_preserve_data_and_preferences(self):
        from ir_feedback.layer_charts import _write_csv
        point = LayerPoint(1, 64.5, 60, 60, 59, 59, True)
        self.archive.capture(('owned-fixture',), [point], 'S3', 65)
        self.archive.writer = mock.Mock(side_effect=OSError('Owned read-only destination'))
        self.view.maximum_ir_percent.set('76')
        with mock.patch('ir_feedback.standalone_ir.filedialog.askdirectory', return_value=''):
            self.view.request_suite_close()
            self.pump_until(lambda: self.view.suite_close_cancelled)
        self.assertTrue(self.alive())
        self.assertEqual(self.archive.retained_rows, 1)
        self.assertTrue(self.archive.failed)
        self.assertEqual(self.view.maximum_ir_preference.load(), 76)
        self.archive.writer = _write_csv
        self.view.request_suite_close()
        self.pump_until(lambda: not self.alive())
        self.assertEqual(1, len(list(Path(self.directory.name).glob('*.csv'))))
        self.assertEqual(2, len(list(Path(self.directory.name).glob('*.png'))))


if __name__ == '__main__':
    unittest.main()
