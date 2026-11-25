"""AOR process controls and test/run readouts for the recoater tab."""
from dataclasses import asdict
import math
import tkinter as tk
from tkinter import ttk

from recoater_aor import AorParameters

GEOMETRY = (
    ('tray_diameter', '원형 트레이 지름 (mm)'), ('feed_width', '유효 토출 폭 (mm)'),
    ('feeder_diameter', '피딩롤러 지름 (mm)'), ('outlet_lead', '토출점 선행 거리 (mm)'),
)
CALIBRATION = (
    ('active_length', '활성 사면 길이 (mm)'), ('release_tau', '방출 시간상수 τ (s)'),
    ('start_angle', '붕락 시작각 (°)'), ('stop_angle', '붕락 정지각 (°)'),
    ('baseline_pct', '상시 흐름 비율 (%)'), ('entry_phase', '진입 위상 (°)'),
    ('measured_period', '시험 실측 주기 (s)'), ('period_variation', '주기 편차 (±%)'),
)
CONDITIONS = (('speed', '리코터 속도 (mm/s)'), ('outlet', '토출 두께 (mm)'), ('rpm', '피딩롤러 (RPM)'))
FLOW_METRICS = (
    ('mean_mass', '평균 질량유량 (g/s)'), ('mean_volume', '평균 체적유량 (mm³/s)'),
    ('ideal_volume', '롤러 기하유량 (mm³/s)'), ('gain', '요구 / 기하 유량'),
    ('period', '방출 주기 (s)'), ('pitch', '방출 간격 (mm)'),
)

def quantity(value, unit=''):
    if value is None or not math.isfinite(value):
        return '—'
    return f'{value:.5g}' + (' ' + unit if unit else '')


class AorPanel:
    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, style='Suite.Surface.TFrame')
        self.fields = {k: tk.StringVar(parent, value=f'{v:g}') for k, v in asdict(AorParameters()).items()}
        self._results = []
        app._label(self.frame, 'AOR 시험·운전', style='Suite.CardTitle.TLabel').pack(anchor='w', pady=(8, 2))
        table = ttk.Frame(self.frame, style='Suite.Surface.TFrame')
        table.pack(fill='x')
        for column, text in enumerate(('설정값', '시험', '운전')):
            app._label(table, text).grid(row=0, column=column, sticky='w' if column == 0 else 'e')
            table.columnconfigure(column, weight=1 if column else 0)
        for row, (key, label) in enumerate(CONDITIONS, 1):
            app._label(table, label).grid(row=row, column=0, sticky='w')
            for col, prefix in enumerate(('test_', 'run_'), 1):
                name = prefix + key
                entry = ttk.Entry(table, textvariable=self.fields[name], width=6, justify='right')
                entry.grid(row=row, column=col, sticky='ew', padx=(4, 0), pady=2)
                entry.bind('<FocusIn>', app._reveal_field)
                app._entries[name] = entry
        self.slider_value = tk.DoubleVar(parent, value=60)
        self._syncing = False
        self.rpm_slider = ttk.Scale(self.frame, from_=0, to=180, variable=self.slider_value,
                                    command=self._slide, takefocus=True)
        app._label(self.frame, '운전 RPM 조절').pack(anchor='w', pady=(5, 0))
        self.rpm_slider.pack(fill='x')
        self.fields['run_rpm'].trace_add('write', self._sync_slider)
        self._sync_slider()
        app._field_grid(self.frame, GEOMETRY, self.fields)
        self.calibration_open = tk.BooleanVar(parent, value=False)
        ttk.Checkbutton(self.frame, text='아발란체 보정값', style='Recoater.Card.TCheckbutton',
                        variable=self.calibration_open, command=self._toggle_calibration).pack(anchor='w')
        self._advanced = ttk.Frame(self.frame, style='Suite.Surface.TFrame')
        app._field_grid(self._advanced, CALIBRATION, self.fields)
        app._label(self._advanced, '실측 주기가 0이면 롤러 치수와 붕락각으로 주기를 계산합니다.',
                   wraplength=254, justify='left').pack(anchor='w')
        self._results_title = app._label(self.frame, '시험·운전 계산값', style='Suite.CardTitle.TLabel')
        self._results_title.pack(anchor='w', pady=(8, 2))
        metrics = ttk.Frame(self.frame, style='Suite.Surface.TFrame')
        metrics.pack(fill='x')
        for i, title in enumerate(('계산값', '시험', '운전')):
            app._label(metrics, title).grid(row=0, column=i, sticky='w' if i == 0 else 'e')
            metrics.columnconfigure(i, weight=1)
        for row, (name, title) in enumerate(FLOW_METRICS, 1):
            app._label(metrics, title).grid(row=row, column=0, sticky='w')
            pair = []
            for col in (1, 2):
                var = tk.StringVar(parent, value='—')
                app._label(metrics, value=var, style='Recoater.Value.TLabel').grid(row=row, column=col, sticky='e')
                pair.append(var)
            self._results.append((name, pair))
        self.tray = tk.StringVar(parent)
        app._label(self.frame, value=self.tray, wraplength=260, justify='left').pack(anchor='w', pady=(5, 0))

    def set_calibration_open(self, open_):
        self.calibration_open.set(open_)
        self._toggle_calibration()

    def _toggle_calibration(self):
        if self.calibration_open.get():
            self._advanced.pack(fill='x', before=self._results_title)
        else:
            self._advanced.pack_forget()

    def _sync_slider(self, *_):
        try:
            rpm = float(self.fields['run_rpm'].get())
        except ValueError:
            return
        if math.isfinite(rpm) and rpm >= 0:
            self._syncing = True
            try:
                self.rpm_slider.configure(to=max(180, rpm))
                self.slider_value.set(rpm)
            finally:
                self._syncing = False

    def _slide(self, value):
        if not self._syncing:
            self.fields['run_rpm'].set(str(round(float(value))))

    def show_results(self, feed):
        for name, pair in self._results:
            for var, condition in zip(pair, (feed.test, feed.run) if feed else (None, None)):
                var.set(quantity(getattr(condition, name, None)))
        if feed is None:
            self.tray.set('')
            return
        nominal = feed.expected_mass * (feed.covered_area / feed.tray_area)
        delta = feed.pass_mass - nominal
        percent = 100 * (delta / nominal) if nominal else None
        self.tray.set(
            f'원판 전면 기준 {quantity(feed.expected_mass, "g")} · 면적률 {quantity(100 * feed.covered_area / feed.tray_area, "%")}\n'
            f'피복 영역 위상 평균 {quantity(feed.covered_mean_mass, "g")}\n'
            f'1회 포집 {quantity(feed.pass_mass, "g")} · 통과 {quantity(feed.dwell, "s")}\n'
            f'연속피딩 대비 {quantity(delta, "g")} ({quantity(percent, "%")})')
