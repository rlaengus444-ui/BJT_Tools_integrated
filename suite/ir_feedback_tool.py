"""Integrated IR view; the upstream control/session/Save path stays isolated."""
from pathlib import Path
import tkinter as tk

from ir_feedback.standalone_ir import ThermalFeedbackWindow, write_error_log
from ir_feedback.session import SessionController
from ir_feedback.excel_workbook import WorkbookSpec
from ir_feedback.excel_async import _EXCEL_FENCE
from ir_feedback.plot_archive import PlotArchive
from ir_feedback.single_instance import SingleInstance

BUILTIN_LABEL = '이미지 회복제어 (내장 Excel)'
BUILTIN_SHA256 = 'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20'
BUILTIN_WORKBOOK = Path(__file__).resolve().parent / 'ir_feedback/workbooks/01_IR_이미지기반_회복제어_2026.10.28.xlsx'
ALGORITHMS = {'image_recovery': BUILTIN_LABEL, 'basic': '기본 알고리즘', 'custom_excel': 'Excel 수식 파일'}
CELL_DEFAULTS = {'workbook_sheet': '수동 테스트', 'workbook_temperature_cell': 'B13',
    'workbook_result_cell': 'D13', 'workbook_power_cell': 'C13', 'workbook_target_cell': 'B5',
    'workbook_layer_cell': 'A13', 'workbook_slice_cell': 'BL13'}
SLICE_INPUTS = ('radius', 'dpi_x', 'dpi_y', 'threshold', 'first_layer', 'polarity', 'numbering')


def builtin_workbook_spec():
    return WorkbookSpec(str(BUILTIN_WORKBOOK), '수동 테스트', 'B13', 'C13', 'D13', 'B5',
                        'A13', 'BL13', expected_sha256=BUILTIN_SHA256)


class _MemoryPreference:
    def __init__(self): self.value = 100.0
    def load(self): return self.value
    def save(self, value): self.value = value


class _OfflineLease:
    def acquire(self): return True
    def close(self): pass


def _offline_runner(*args, **kwargs):
    raise RuntimeError('시험 모드에서는 모의 제어기를 명시적으로 주입해야 합니다.')


class App(ThermalFeedbackWindow):
    # The suite keeps pumping until owned workers/helpers and archives retire.
    CLOSE_GRACE_SECONDS = float('inf')

    def __init__(self, root, *, test_mode=False, controller=None, discoverer=None,
                 archive=None, maximum_ir_preference=None, lease_factory=None):
        self._lease = None
        self._lease_factory = lease_factory or (_OfflineLease if test_mode else SingleInstance)
        self._suite_close_requested = False
        self._selected_mode = 'image_recovery'
        self._custom_excel_path = ''
        self._custom_json_path = ''
        self._custom_cells = dict(CELL_DEFAULTS)
        self._test_mode = test_mode
        if test_mode:
            controller = controller if controller is not None else SessionController(runner=_offline_runner)
            maximum_ir_preference = maximum_ir_preference or _MemoryPreference()
            discoverer = discoverer or (lambda: [])
            archive = archive if archive is not None else PlotArchive(directories=())
        kwargs = dict(controller=controller, auto_discover=not test_mode, archive=archive,
                      maximum_ir_preference=maximum_ir_preference)
        if discoverer is not None: kwargs['discoverer'] = discoverer
        super().__init__(root, **kwargs)
        self.algorithm_box.configure(values=tuple(ALGORITHMS.values()), width=27)
        self._show_algorithm('image_recovery')
        self.slice_panel.workbook_button.configure(text='기존 참고용 시트 저장 / 사용')
        self.root.bind('<Destroy>', self._release_on_destroy, add='+')
        self._append_log('내장 이미지 회복제어 · 원본 Excel 수식 사용 · Excel 데스크톱 필요 · 처음 준비에는 시간이 걸릴 수 있습니다.')

    def _remember_algorithm(self):
        if self._selected_mode == 'custom_excel':
            self._custom_excel_path = self.settings_path.get()
            self._custom_cells = {name: getattr(self, name).get() for name in CELL_DEFAULTS}
        elif self._selected_mode == 'basic':
            self._custom_json_path = self.settings_path.get()

    def _show_algorithm(self, mode):
        self._selected_mode = mode
        self.algorithm_type.set(ALGORITHMS[mode])
        if mode == 'image_recovery':
            self.settings_path.set('내장 원본 시트 · Excel 데스크톱 필요')
            self.algorithm_path_label.set('계산시트')
            self.excel_fields.grid_remove()
        elif mode == 'custom_excel':
            self.settings_path.set(self._custom_excel_path)
            self.algorithm_path_label.set('Excel 파일')
            for name, value in self._custom_cells.items(): getattr(self, name).set(value)
            self.excel_fields.grid()
        else:
            self.settings_path.set(self._custom_json_path)
            self.algorithm_path_label.set('설정 JSON(선택)')
            self.excel_fields.grid_remove()
        self._refresh_buttons()

    def _algorithm_changed(self, _event=None):
        mode = next((key for key, label in ALGORITHMS.items() if self.algorithm_type.get() == label), None)
        if mode is None: return
        self._remember_algorithm()
        self._show_algorithm(mode)

    def _refresh_buttons(self):
        super()._refresh_buttons()
        if self.algorithm_type.get() == BUILTIN_LABEL:
            self.settings_entry.configure(state='readonly')
            self.browse_button.configure(state='disabled')

    def _selected_workbook_spec(self):
        if self.algorithm_type.get() == BUILTIN_LABEL:
            return builtin_workbook_spec()
        return super()._selected_workbook_spec()

    def _selected_settings_path(self):
        return None if self.algorithm_type.get() == BUILTIN_LABEL else super()._selected_settings_path()

    def connect(self):
        if self.closing or self.controller.closed or self.controller.thread is not None:
            return
        if self._lease is None:
            lease = self._lease_factory()
            try:
                if not lease.acquire():
                    self._append_log('다른 IR 피드백 프로그램이 실행 중입니다. 해당 프로그램을 종료한 뒤 연결하세요.')
                    return
            except OSError as error:
                self._append_log(str(error))
                return
            self._lease = lease
        return super().connect()

    def _close_workers_ready(self):
        if not super()._close_workers_ready(): return False
        worker = self.slice_panel.worker
        if worker is not None and worker.is_alive(): return False
        try:
            return bool(_EXCEL_FENCE.retirement_status().get('verified'))
        except Exception:
            return False

    def request_suite_close(self):
        self._suite_close_requested = True
        self.close()

    @property
    def suite_close_cancelled(self):
        return self._suite_close_requested and not self.closing

    @property
    def is_busy(self):
        worker = self.slice_panel.worker
        return self.controller.worker_alive or (worker is not None and worker.is_alive()) or self.archive.busy

    def _release_on_destroy(self, event):
        if event.widget is self.root and self._lease is not None:
            lease, self._lease = self._lease, None
            lease.close()

    def input_state(self):
        self._remember_algorithm()
        return {'machine': self.machine.get(), 'target': self.target_temperature.get(),
            'maximum_ir': self.maximum_ir_percent.get(), 'algorithm': self._selected_mode,
            'custom_excel': self._custom_excel_path, 'custom_json': self._custom_json_path,
            'custom_cells': dict(self._custom_cells),
            'slice_parameters': {name: getattr(self.slice_panel, name).get() for name in SLICE_INPUTS},
            'slice_paths': list(map(str, self.slice_panel.paths))}

    def restore_input_state(self, state):
        if not isinstance(state, dict): return
        for key, variable in (('machine', self.machine), ('target', self.target_temperature),
                              ('maximum_ir', self.maximum_ir_percent)):
            value = state.get(key)
            if isinstance(value, str) and (key != 'machine' or value in ('M1', 'S3')): variable.set(value)
            elif key == 'maximum_ir' and key in state:
                variable.set('')
        for key, attribute in (('custom_excel', '_custom_excel_path'), ('custom_json', '_custom_json_path')):
            if isinstance(state.get(key), str): setattr(self, attribute, state[key])
        cells = state.get('custom_cells', {})
        if isinstance(cells, dict):
            for name in CELL_DEFAULTS:
                if isinstance(cells.get(name), str): self._custom_cells[name] = cells[name]
        panel = self.slice_panel
        params = state.get('slice_parameters', {})
        if isinstance(params, dict):
            for name in SLICE_INPUTS:
                if isinstance(params.get(name), str): getattr(panel, name).set(params[name])
        if panel.debounce is not None:
            panel.after_cancel(panel.debounce)
            panel.debounce = None
        paths = state.get('slice_paths')
        if isinstance(paths, list) and all(isinstance(path, str) for path in paths):
            panel.paths = tuple(paths)
            panel.selected.set(f'이전 선택 {len(paths)}개 · 다시 분석 필요' if paths else '선택한 파일 없음')
        # No previous analysis or job authority is ever restored.
        panel.dataset = None
        panel.clear_session()
        panel.summary.set('슬라이싱 이미지는 다시 분석한 뒤 사용하세요.')
        mode = state.get('algorithm')
        self._show_algorithm(mode if isinstance(mode, str) and mode in ALGORITHMS else 'image_recovery')


def main():
    from suite_ui import create_root
    root = create_root()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
