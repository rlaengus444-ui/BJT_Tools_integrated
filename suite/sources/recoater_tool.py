"""Native interactive side-section simulator for the BJT recoater."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import math
import time
import tkinter as tk
from tkinter import ttk

from recoater_aor import AorParameters
from recoater_controls import AorPanel, quantity
from recoater_charts import (
    bind_chart_hover, discharge_readings, draw_comparison_chart, draw_discharge_chart, prepare_comparison,
)
from recoater_model import Parameters, ROLLER_EXAMPLE, SHEET_EXAMPLE, build_curve, make_model
from recoater_views import (
    STAGE_BG, SPREAD, STAGE_AOR, STAGE_COMPACT, STAGE_SPREAD, STAGE_FORMED, STAGE_PASSED, STAGE_FEED,
    chart_x_bounds, draw_height_chart,
    draw_section, empty, format_number,
)
from suite_theme import COLORS, FONTS, ThemeManager

POWDER_FIELDS = (
    ('layer', '레이어 두께 (mm)'), ('feed_ratio', '피딩배수 (배)'),
    ('loose_density', '겉보기 밀도 (g/cm³)'), ('bed_density', '베드밀도 (g/cm³)'),
    ('angle', '안식각 (°)'), ('bed_length', '베드 길이 (mm)'),
)
ROLLERS = (
    ('compact_diameter', '컴팩팅 지름 (mm)'), ('spread_diameter', '스프래딩 지름 (mm)'),
    ('spacing', '축 간 거리 (mm)'), ('clearance', '스프래딩 간극 (mm)'),
)
SPREAD_FIELDS = {'spread_diameter', 'spacing', 'clearance'}
PRESETS = {'원형 롤러 예시': ROLLER_EXAMPLE, '시트 기본값': SHEET_EXAMPLE}
DERIVED = (
    ('feed', '피딩 직후 평균 두께'), ('base', '컴팩팅 전 기준층'), ('required', '목표층에 필요한 겉보기 두께'),
    ('deposit', '형성층 두께'), ('balance', '잉여 두께'), ('swept', '스프래딩이 걷어낸 두께'),
    ('sweep', '스프래딩롤러가 지난 거리'), ('diverted', '접촉 후 유출 단면적'),
)

FEED_MODES = {'aor': 'AOR 피딩', 'uniform': '균일 피딩'}
DIRECTIONS = {'rear_to_front': '뒤 → 앞', 'front_to_rear': '앞 → 뒤'}
FLOW_VIEWS = {'time': '시간별 유량', 'distance': '거리별 피딩층 두께'}
COMPARISONS = {'flow': '순간 토출 유량', 'feed': '피딩 직후 두께', 'spread': '스프래딩 앞 높이',
               'compact': '컴팩팅 앞 높이', 'formed': '컴팩팅 후 두께'}
COMPARE_VIEWS = {'percent': '편차율 (%)', 'difference': '실단위 편차', 'overlay': '두 곡선 겹쳐 보기'}


def _compute_model(parameters, direction):
    model = make_model(parameters, two_pass=True, feed_direction=direction)
    reference = (make_model(replace(parameters, aor=None), continuous_lead=model.aor.lead,
                            two_pass=True, feed_direction=direction) if model.aor else None)
    return model, build_curve(model), reference


def _input_numbers(fields):
    values = {}
    for key, var in fields.items():
        try:
            values[key] = float(var.get())
        except ValueError:
            values[key] = math.nan
    return values



class RecoaterApp:
    def __init__(self, root):
        self.root = root
        self.fields = {key: tk.StringVar(root) for key, _ in (*POWDER_FIELDS, *ROLLERS)}
        self.spread_on = tk.BooleanVar(root, value=True)
        self.speed = tk.StringVar(root, value='16')
        self.feed_mode = tk.StringVar(root, value='aor')
        self.direction = tk.StringVar(root, value='rear_to_front')
        self.flow_view = tk.StringVar(root, value='time')
        self.compare_metric = tk.StringVar(root, value='flow')
        self.compare_view = tk.StringVar(root, value='percent')
        self._angles = dict.fromkeys(FEED_MODES, '45')
        self._mode = 'aor'
        self._chart_mode = 'flow'
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='recoater')
        self._model_future = self._comparison_future = None
        self._comparison_key = None
        self._comparisons = {}
        self.reference = None
        self._reset_on_build = True
        self._poll = None
        self.camera = tk.StringVar(root, value='follow')
        self.preset = tk.StringVar(root, value=next(iter(PRESETS)))
        self.distance_text = tk.StringVar(root, value='0')
        self._display_distance = '0'
        self.position = tk.DoubleVar(root)
        self.readout = tk.StringVar(root, value='—')
        self.status = tk.StringVar(root)
        self.metrics = [[tk.StringVar(root, value='—') for _ in range(3)] for _ in range(2)]
        self.derived = {key: tk.StringVar(root, value='—') for key, _ in DERIVED}
        self.model = None
        self.curve = ()
        self.distance = 0.
        self.problem = ''
        self.is_playing = False
        self._anim = self._edit = self._draw = None
        self._last_time = 0.
        self._loading = self._closed = False
        self._entries = {}
        self._styles()
        self._build()
        for var in (*self.fields.values(), *self.aor_panel.fields.values(), self.spread_on):
            var.trace_add('write', self._schedule_rebuild)
        self.speed.trace_add('write', self._speed_changed)
        self.feed_mode.trace_add('write', self._mode_changed)
        self.direction.trace_add('write', self._direction_changed)
        for var in (self.flow_view, self.compare_metric, self.compare_view):
            var.trace_add('write', self._view_changed)
        root.bind('<Unmap>', self._hidden, add='+')
        root.bind('<Destroy>', self._destroyed, add='+')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self._load_preset(ROLLER_EXAMPLE)

    def _styles(self):
        style = ttk.Style(self.root)
        style.configure('Recoater.Card.TFrame', background=COLORS['surface'],
                        bordercolor=COLORS['border'], borderwidth=1, relief='solid')
        style.configure('Recoater.Value.TLabel', background=COLORS['surface'],
                        foreground=COLORS['ink'], font=('Consolas', 10, 'bold'))
        style.configure('Recoater.Card.TCheckbutton', background=COLORS['surface'])
        style.configure('Recoater.View.TButton', background=COLORS['surface'], foreground=COLORS['muted'],
                        padding=(9, 3), borderwidth=0, font=FONTS['body'])
        style.configure('Recoater.ViewOn.TButton', background='#dfebf8', foreground='#20486c',
                        padding=(9, 3), borderwidth=0, font=FONTS['bold'])
        for name in ('Recoater.View.TButton', 'Recoater.ViewOn.TButton'):
            style.map(name, background=[('active', '#d9e8f8')])
        style.configure('Recoater.Graph.TNotebook', background=COLORS['surface'], borderwidth=0)
        style.configure('Recoater.Graph.TNotebook.Tab', padding=(11, 3), font=FONTS['body'])
        style.map('Recoater.Graph.TNotebook.Tab', background=[('selected', '#253e58'), ('active', '#e4edf8')],
                  foreground=[('selected', '#ffffff'), ('disabled', COLORS['disabled_ink'])])
        style.configure('Recoater.Progress.TLabel', background=COLORS['surface'],
                        foreground='#17395d', font=('Consolas', 12, 'bold'))

        style.configure('Recoater.Compact.TLabel', background=COLORS['surface'],
                        foreground='#855508', font=FONTS['bold'])
        style.configure('Recoater.Spread.TLabel', background=COLORS['surface'],
                        foreground=SPREAD, font=FONTS['bold'])

    @staticmethod
    def _card(parent, padding):
        return ttk.Frame(parent, style='Recoater.Card.TFrame', padding=padding)

    @staticmethod
    def _label(parent, text='', *, value=None, style='Suite.CardText.TLabel', **options):
        return ttk.Label(parent, text=text, textvariable=value, style=style, **options)

    def _build(self):
        body = ttk.Frame(self.root, style='Suite.Tool.TFrame', padding=(4, 6))
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1, minsize=420)
        body.columnconfigure(1, minsize=310)
        body.rowconfigure(0, weight=1)

        work = ttk.Frame(body, style='Suite.Tool.TFrame')
        work.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        work.columnconfigure(0, weight=1)
        work.rowconfigure(0, weight=3, minsize=185)
        work.rowconfigure(2, weight=2, minsize=125)

        scene_card = self._card(work, (11, 9))
        scene_card.grid(row=0, column=0, sticky='nsew', pady=(0, 8))
        heading = ttk.Frame(scene_card, style='Suite.Surface.TFrame')
        heading.pack(fill='x', pady=(0, 7))
        self._label(heading, '리코터 단면', style='Suite.CardTitle.TLabel').pack(side='left')
        self.view_buttons = {}
        for key, caption in (('full', '베드 전체'), ('follow', '롤러 확대')):
            button = ttk.Button(heading, text=caption, command=lambda mode=key: self._set_camera(mode))
            button.pack(side='right', padx=(5, 0))
            self.view_buttons[key] = button
        legend = ttk.Frame(scene_card, style='Suite.Surface.TFrame')
        legend.pack(side='bottom', fill='x', pady=(6, 0))
        self.spread_keys = []
        for column, (color, label, spread_only) in enumerate((
            (STAGE_AOR, 'AOR 토출', False), (STAGE_FEED, '피딩층', False),
            (STAGE_SPREAD, '스프래딩 앞', True), (STAGE_COMPACT, '컴팩팅 앞', False),
            (STAGE_PASSED, '통과층', True), (STAGE_FORMED, '형성층', False),
        )):
            item = ttk.Frame(legend, style='Suite.Surface.TFrame')
            item.grid(row=0, column=column, padx=(0, 11))
            tk.Canvas(item, width=9, height=9, highlightthickness=0, background=color).pack(
                side='left', padx=(0, 4))
            self._label(item, label).pack(side='left')
            if label == 'AOR 토출':
                self.aor_key = item
            if spread_only:
                self.spread_keys.append(item)
        self.scene = tk.Canvas(scene_card, width=1, height=1, background=STAGE_BG, highlightthickness=0)
        self.scene.pack(fill='both', expand=True)

        transport = self._card(work, (10, 6))
        transport.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        self._build_transport(transport)

        plot_card = self._card(work, (8, 4))
        plot_card.grid(row=2, column=0, sticky='nsew')
        self.plots = ttk.Notebook(plot_card, style='Recoater.Graph.TNotebook')
        self.plots.pack(fill='both', expand=True)
        self.plot_pages, self.canvases = {}, {}
        for key, caption in (('flow', '토출 파형'), ('height', '더미 높이'), ('compare', '연속피딩 대비')):
            page = ttk.Frame(self.plots, style='Suite.Surface.TFrame', padding=(4, 0))
            self.plots.add(page, text=caption)
            self.plot_pages[key] = page
            toolbar = ttk.Frame(page, style='Suite.Surface.TFrame')
            toolbar.pack(fill='x')
            if key == 'flow':
                self._choice(toolbar, self.flow_view, FLOW_VIEWS, 13).pack(side='left')
                self.flow_summary = tk.StringVar(self.root)
                self._label(toolbar, value=self.flow_summary).pack(side='right')
            elif key == 'compare':
                self.compare_selector = self._choice(toolbar, self.compare_metric, COMPARISONS, 13)
                self.compare_selector.pack(side='left')
                self._choice(toolbar, self.compare_view, COMPARE_VIEWS, 14).pack(side='left', padx=(4, 0))
                self.compare_summary = tk.StringVar(self.root)
                self.compare_current = tk.StringVar(self.root)
                self._label(page, value=self.compare_current).pack(side='bottom', fill='x')
                self._label(toolbar, value=self.compare_summary).pack(side='right')
            else:
                self._label(toolbar, '컴팩팅 ●   스프래딩 ◆    ·    눌러서 위치 이동').pack(anchor='w')
            canvas = tk.Canvas(page, width=1, height=1, background=COLORS['surface'],
                               highlightthickness=0, cursor='crosshair')
            canvas.pack(fill='both', expand=True)
            self.canvases[key] = canvas
            bind_chart_hover(canvas)
            canvas.bind('<Configure>', self._schedule_paint, add='+')
        self.chart = self.canvases['height']
        self.chart.bind('<Button-1>', self._chart_seek, add='+')
        self.plots.bind('<<NotebookTabChanged>>', self._plot_selected)
        self.scene.bind('<Configure>', self._schedule_paint)

        panel = self._card(body, (10, 9))
        panel.grid(row=0, column=1, sticky='nsew')
        self._build_sidebar(panel)

    @staticmethod
    def _choice(parent, variable, labels, width):
        display = tk.StringVar(parent, value=labels[variable.get()])
        combo = ttk.Combobox(parent, values=tuple(labels.values()), textvariable=display,
                             state='readonly', width=width)
        keys = {label: key for key, label in labels.items()}
        combo.bind('<<ComboboxSelected>>', lambda _event: variable.set(keys[display.get()]))
        variable.trace_add('write', lambda *_: display.set(labels.get(variable.get(), '')))
        return combo

    def _plot_selected(self, _event=None):
        selected = self.plots.select()
        for key, page in self.plot_pages.items():
            if str(page) == selected:
                self._chart_mode = key
                break
        self._schedule_paint()

    def _view_changed(self, *_):
        if not self._loading:
            self._schedule_paint()

    def _mode_changed(self, *_):
        if self._loading or self.feed_mode.get() == self._mode:
            return
        self._angles[self._mode] = self.fields['angle'].get()
        self._mode = self.feed_mode.get()
        self.fields['angle'].set(self._angles[self._mode])
        self._direction_changed()

    def _direction_changed(self, *_):
        if self._loading:
            return
        self._reset_on_build = True
        self._schedule_rebuild()

    def _build_transport(self, panel):
        commands = ttk.Frame(panel, style='Suite.Surface.TFrame')
        commands.pack(fill='x')
        self.play_button = ttk.Button(commands, text='재생', width=9, style='Suite.Primary.TButton',
                                      command=self.toggle_play)
        self.play_button.pack(side='left')
        self.rewind_button = ttk.Button(commands, text='처음으로', command=lambda: self.seek(0.))
        self.rewind_button.pack(side='left', padx=5)
        self.contact_button = ttk.Button(commands, text='최초 접촉', command=self._jump_contact)
        self.contact_button.pack(side='left')
        self._label(commands, 'mm/s').pack(side='right')
        self.speed_entry = ttk.Entry(commands, textvariable=self.speed, width=6, justify='right')
        self.speed_entry.pack(side='right', padx=5)
        self._label(commands, '재생 속도').pack(side='right')

        seek_row = ttk.Frame(panel, style='Suite.Surface.TFrame')
        seek_row.pack(fill='x', pady=(5, 3))
        self._label(seek_row, value=self.readout, style='Recoater.Progress.TLabel', width=17).pack(side='left')
        self.distance_entry = ttk.Entry(seek_row, textvariable=self.distance_text, width=8, justify='right')
        self.distance_entry.pack(side='right', padx=(6, 0))
        self.distance_entry.bind('<Return>', self._commit_distance)
        self.distance_entry.bind('<FocusOut>', self._commit_distance)
        self.distance_entry.bind('<FocusIn>', lambda _event: self.pause())
        self.timeline = ttk.Scale(seek_row, from_=0., to=1., orient='horizontal', variable=self.position,
                                  command=self._slide)
        self.timeline.pack(side='left', fill='x', expand=True)
        self.status_label = self._label(panel, value=self.status, style='Suite.CardText.TLabel',
                                        wraplength=510, justify='left')
        self.status_label.pack(fill='x', anchor='w')

    def _build_sidebar(self, panel):
        title_row = ttk.Frame(panel, style='Suite.Surface.TFrame')
        title_row.pack(fill='x', pady=(0, 8))
        self._label(title_row, '공정 조건', style='Suite.CardTitle.TLabel').pack(side='left')
        combo = ttk.Combobox(title_row, textvariable=self.preset, state='readonly', width=15,
                             values=tuple(PRESETS))
        combo.pack(side='right')
        combo.bind('<<ComboboxSelected>>', lambda _event: self._load_preset(PRESETS[self.preset.get()]))

        recipe = ttk.Frame(panel, style='Suite.Surface.TFrame')
        recipe.pack(fill='x', pady=(0, 2))
        for col, (caption, var, choices) in enumerate((
                ('피딩 방식', self.feed_mode, FEED_MODES), ('피딩 방향', self.direction, DIRECTIONS))):
            recipe.columnconfigure(col, weight=1, uniform='recipe')
            self._label(recipe, caption).grid(row=0, column=col, sticky='w')
            self._choice(recipe, var, choices, 9).grid(row=1, column=col, sticky='ew', padx=(0, 5) if col==0 else 0)
        self.sequence = tk.StringVar(self.root)
        self._label(panel, value=self.sequence, wraplength=280, justify='left').pack(fill='x', pady=(1, 6))
        ttk.Separator(panel).pack(fill='x', pady=(0, 4))
        summaries = ttk.Frame(panel, style='Suite.Surface.TFrame')
        summaries.pack(fill='x', pady=(0, 6))
        for column, caption in enumerate(('더미 위치', '높이 mm', '상승고 mm', '면적 mm²')):
            self._label(summaries, caption).grid(row=0, column=column, sticky='w' if column == 0 else 'e')
            summaries.columnconfigure(column, weight=1)
        self.spread_metrics = []
        for i, label in enumerate(('컴팩팅 앞', '스프래딩 앞')):
            style = 'Recoater.Spread.TLabel' if i else 'Recoater.Compact.TLabel'
            widgets = [self._label(summaries, label, style=style)]
            widgets.extend(self._label(summaries, value=var, style='Recoater.Value.TLabel')
                           for var in self.metrics[i])
            for column, widget in enumerate(widgets):
                widget.grid(row=i+1, column=column, sticky='w' if column == 0 else 'e', pady=3)
            if i:
                self.spread_metrics = widgets

        ttk.Separator(panel).pack(fill='x', pady=(0, 6))
        self.sidebar = tk.Canvas(panel, width=275, height=1, background=COLORS['surface'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, command=self.sidebar.yview)
        self.sidebar.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.sidebar.pack(side='left', fill='both', expand=True)
        settings = ttk.Frame(self.sidebar, style='Suite.Surface.TFrame', padding=(0, 0, 5, 0))
        window = self.sidebar.create_window(0, 0, window=settings, anchor='nw')
        self.sidebar.bind('<Configure>', lambda event: self.sidebar.itemconfigure(window, width=event.width))
        settings.bind('<Configure>', lambda _event: self.sidebar.configure(scrollregion=self.sidebar.bbox(window)))

        self._label(settings, '파우더와 베드', style='Suite.CardTitle.TLabel').pack(anchor='w')
        self._field_grid(settings, POWDER_FIELDS)
        self.aor_panel = AorPanel(self, settings)
        self.aor_panel.frame.pack(fill='x')
        self._roller_heading = self._label(settings, '롤러', style='Suite.CardTitle.TLabel')
        self._roller_heading.pack(anchor='w', pady=(7, 0))
        ttk.Checkbutton(settings, text='스프래딩롤러 사용', variable=self.spread_on,
                        style='Recoater.Card.TCheckbutton').pack(anchor='w', pady=(3, 0))
        self._field_grid(settings, ROLLERS)
        ttk.Separator(settings).pack(fill='x', pady=7)
        self._label(settings, '두께와 단면적', style='Suite.CardTitle.TLabel').pack(anchor='w')
        self._derived_rows = {}
        for key, label in DERIVED:
            row = ttk.Frame(settings, style='Suite.Surface.TFrame')
            row.pack(fill='x', pady=2)
            self._label(row, label, wraplength=166, justify='left').pack(side='left')
            self._label(row, value=self.derived[key], style='Recoater.Value.TLabel').pack(side='right')
            self._derived_rows[key] = row
        self._bind_scroll(self.sidebar)

    def _field_grid(self, parent, fields, variables=None):
        variables = self.fields if variables is None else variables
        grid = ttk.Frame(parent, style='Suite.Surface.TFrame')
        grid.pack(fill='x', pady=(2, 0))
        for column in range(2):
            grid.columnconfigure(column, weight=1, uniform='condition')
        for i, (key, label) in enumerate(fields):
            cell = ttk.Frame(grid, style='Suite.Surface.TFrame')
            cell.grid(row=i//2, column=i%2, sticky='ew', padx=(0, 5) if i%2 == 0 else 0, pady=(0, 5))
            self._label(cell, label).pack(anchor='w')
            entry = ttk.Entry(cell, textvariable=variables[key], width=10, justify='right')
            entry.pack(fill='x')
            entry.bind('<FocusIn>', self._reveal_field)
            self._entries[key] = entry

    def _bind_scroll(self, widget):
        widget.bind('<MouseWheel>', self._scroll, add='+')
        widget.bind('<Button-4>', self._scroll, add='+')
        widget.bind('<Button-5>', self._scroll, add='+')
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _scroll(self, event):
        if event.num in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            if not event.delta:
                return
            steps = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.sidebar.yview_scroll(steps, 'units')
        return 'break'

    def _reveal_field(self, event):
        area = self.sidebar.bbox('all')
        if not area or area[3] <= 0:
            return
        top = event.widget.winfo_rooty() - self.sidebar.winfo_rooty()
        if top < 0:
            self.sidebar.yview_moveto((self.sidebar.canvasy(0) + top - 8) / area[3])
        elif top + event.widget.winfo_height() > self.sidebar.winfo_height():
            self.sidebar.yview_moveto(
                (self.sidebar.canvasy(0) + top + event.widget.winfo_height()
                 - self.sidebar.winfo_height() + 8) / area[3])

    def _cancel(self, attribute):
        token = getattr(self, attribute)
        if token is not None:
            try:
                self.root.after_cancel(token)
            except tk.TclError:
                pass
            setattr(self, attribute, None)

    def _halt(self):
        self.is_playing = False
        self._cancel('_anim')

    def pause(self):
        if self.is_playing:
            self._halt()
            if not self._closed:
                self._paint()

    def _hidden(self, event):
        if event.widget is self.root:
            self.pause()

    def _destroyed(self, event):
        if event.widget is self.root:
            self._closed = True
            self._halt()
            self._cancel('_poll')
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._cancel('_edit')
            self._cancel('_draw')

    def close(self):
        self._closed = True
        self._halt()
        self.root.destroy()

    def _speed_value(self):
        try:
            speed = float(self.speed.get())
        except ValueError:
            return None
        return speed if math.isfinite(speed) and speed > 0 else None

    def _speed_changed(self, *_):
        if self._loading:
            return
        if self._speed_value() is None:
            self._halt()
        self._last_time = time.monotonic()
        self._schedule_paint()

    def _load_preset(self, params):
        self._loading = True
        try:
            values = asdict(params)
            for name, var in self.fields.items():
                var.set(f'{values[name]:g}')
            self._angles['uniform'] = f'{params.angle:g}'
            self._angles['aor'] = '45'
            self.fields['angle'].set(self._angles[self._mode])
            self.spread_on.set(params.spread_on)
        finally:
            self._loading = False
        self._reset_on_build = True
        self._rebuild()

    def _schedule_rebuild(self, *_):
        if self._loading or self._closed:
            return
        self._halt()
        self.preset.set('사용자 입력')
        self.play_button.state(['disabled'])
        # An earlier background result must not overwrite an in-progress edit.
        for task in (self._model_future, self._comparison_future):
            if task is not None:
                task.cancel()
        self._model_future = self._comparison_future = None
        self._cancel('_edit')
        self._edit = self.root.after(110, self._rebuild)

    def _rebuild(self):
        self._halt()
        self._cancel('_edit')
        self.problem = ''
        spread = self.spread_on.get()
        for name in SPREAD_FIELDS:
            self._entries[name].state(['!disabled'] if spread else ['disabled'])
        for widget in self.spread_metrics:
            if spread:
                widget.grid()
            else:
                widget.grid_remove()
        for item in self.spread_keys:
            if spread:
                item.grid()
            else:
                item.grid_remove()
        for row in self._derived_rows.values():
            row.pack_forget()
        for key, _ in DERIVED:
            if spread or key not in {'swept', 'sweep', 'diverted'}:
                self._derived_rows[key].pack(fill='x', pady=2)
        aor = self.feed_mode.get() == 'aor'
        if aor:
            self.aor_key.grid()
            self.aor_panel.frame.pack(fill='x', before=self._roller_heading)
        else:
            self.aor_key.grid_remove()
            self.aor_panel.frame.pack_forget()
            self.plots.select(self.plot_pages['height'])
        for key in ('flow', 'compare'):
            self.plots.tab(self.plot_pages[key], state='normal' if aor else 'disabled')
        self.sequence.set('피딩 후 앞 → 뒤로 복귀하며 스프래딩·컴팩팅' if
                          self.direction.get() == 'rear_to_front' else '같은 주행에서 피딩 → 스프래딩 → 컴팩팅')
        if not spread:
            self.sequence.set(self.sequence.get().replace('스프래딩·', '').replace('스프래딩 → ', ''))
            if self.compare_metric.get() == 'spread':
                self.compare_metric.set('compact')
        self.compare_selector.configure(values=tuple(v for k, v in COMPARISONS.items() if spread or k != 'spread'))
        for task in (self._comparison_future, self._model_future):
            if task is not None:
                task.cancel()
        self._comparisons.clear()
        self._comparison_future = self._comparison_key = None
        self.model = self.reference = None
        self.curve = ()
        self.problem = '공정 조건 계산 중…'
        parameters = Parameters(**_input_numbers(self.fields), spread_on=spread,
                                aor=AorParameters(**_input_numbers(self.aor_panel.fields)) if aor else None)
        self._model_future = self._executor.submit(_compute_model, parameters, self.direction.get())
        self._arm_poll()
        self._paint(reset_entry=True)

    def _arm_poll(self):
        if self._poll is None and not self._closed:
            self._poll = self.root.after(25, self._collect_calculations)

    def _collect_calculations(self):
        self._poll = None
        if self._closed:
            return
        ready = False
        if self._model_future is not None and self._model_future.done():
            task, self._model_future = self._model_future, None
            try:
                self.model, self.curve, self.reference = task.result()
                self.distance = 0. if self._reset_on_build else min(self.distance, self.model.travel_end)
                self._reset_on_build = False
                self.problem = ''
            except (ValueError, ArithmeticError) as exc:
                self.problem = str(exc) if isinstance(exc, ValueError) else '입력값의 단위와 크기를 확인하세요.'
            self.aor_panel.show_results(self.model.aor if self.model else None)
            ready = True
        if self._comparison_future is not None and self._comparison_future.done():
            task, self._comparison_future = self._comparison_future, None
            try:
                self._comparisons[self._comparison_key] = task.result()
            except (ValueError, ArithmeticError) as exc:
                self._comparisons[self._comparison_key] = str(exc) or '비교 곡선을 계산할 수 없습니다.'
            ready = True
        if ready:
            self._paint(reset_entry=True)
        if self._model_future is not None or self._comparison_future is not None:
            self._arm_poll()

    def _set_camera(self, value):
        self.camera.set(value)
        self._paint()

    def _slide(self, fraction):
        if self.model is not None:
            self.seek(float(fraction) * self.model.travel_end)

    def _commit_distance(self, _event):
        if self.model is not None:
            text = self.distance_text.get()
            if text == self._display_distance:
                return
            try:
                requested = float(text)
            except ValueError:
                requested = self.distance
            self.seek(requested if math.isfinite(requested) else self.distance)

    def _jump_contact(self):
        if self.model is not None:
            self.seek(self.model.contact_x)

    def _chart_seek(self, event):
        if self.model is not None:
            left, right = chart_x_bounds(self.chart.winfo_width())
            if left <= event.x <= right:
                self.seek((event.x-left)/(right-left) * self.model.travel_end)

    def seek(self, distance):
        if self.model is None or self._edit is not None:
            return
        self._halt()
        self.distance = min(self.model.travel_end, max(0., distance))
        self._paint(reset_entry=True)

    def toggle_play(self):
        if self.is_playing:
            self.pause()
            return
        if self.model is None or self._edit is not None or self._speed_value() is None:
            return
        if self.distance >= self.model.travel_end:
            self.distance = 0.
        self.is_playing = True
        self._last_time = time.monotonic()
        self._paint()
        self._anim = self.root.after(33, self._tick)

    def _tick(self):
        self._anim = None
        speed = self._speed_value()
        if self._closed or not self.is_playing or self.model is None or speed is None or not self.root.winfo_viewable():
            self._halt()
            return
        now = time.monotonic()
        length = self.model.travel_end
        self.distance = min(length, self.distance + speed * max(0., now-self._last_time))
        self._last_time = now
        if self.distance >= length:
            self._halt()
        self._paint(reset_entry=True)
        if self.is_playing:
            self._anim = self.root.after(33, self._tick)

    def _schedule_paint(self, *_):
        if self._draw is None and not self._closed:
            self._draw = self.root.after_idle(self._paint)

    def _paint(self, *, reset_entry=False):
        self._cancel('_draw')
        if self._closed:
            return
        model = self.model
        speed_ok = self._speed_value() is not None
        ready = model is not None and self._edit is None
        at_end = model is not None and self.distance >= model.travel_end
        action = '일시정지' if self.is_playing else '다시 재생' if at_end else '재생'
        self.play_button.configure(text=action, state='normal' if ready and speed_ok else 'disabled')
        for widget in (self.rewind_button, self.timeline, self.distance_entry):
            widget.state(['!disabled'] if ready else ['disabled'])
        in_bed = model is not None and model.contact_x <= model.travel_end
        self.contact_button.state(['!disabled'] if ready and in_bed else ['disabled'])
        for key, button in self.view_buttons.items():
            button.configure(style='Recoater.ViewOn.TButton' if self.camera.get() == key else 'Recoater.View.TButton')
        if model is None:
            self.readout.set('—')
            self.status.set(self.problem)
            self.status_label.configure(style='Suite.CardText.TLabel' if self._model_future else 'Suite.Status.Error.TLabel')
            for var in (*self.metrics[0], *self.metrics[1], *self.derived.values()):
                var.set('—')
            empty(self.scene, '계산 중…' if self._model_future else '공정 조건을 확인하세요.')
            for canvas in self.canvases.values():
                empty(canvas, '')
            self.flow_summary.set('')
            self.compare_summary.set('')
            self.compare_current.set('')
            return

        state = model.at(self.distance)
        length = model.travel_end
        self.position.set(self.distance/length)
        self.readout.set(f'{format_number(self.distance, 2)} / {length:.5g} mm')
        if reset_entry or self.root.focus_get() is not self.distance_entry:
            self._display_distance = f'{self.distance:.9g}'
            self.distance_text.set(self._display_distance)
        for profile, row in zip((state if state.compact_started else None, state.spreader), self.metrics):
            if profile is not None:
                for var, number in zip(row, (profile.height, profile.rise, profile.pile_area)):
                    var.set(format_number(number))
            else:
                for var in row:
                    var.set('—')
        formed = None
        if state.compact_started:
            formed = state.local.formed_height if state.local is not None else model.deposit
        amounts = (model.feed, model.base, model.required, formed, model.balance,
                   model.swept_thickness, state.spread_distance, state.diverted_area)
        for (key, _), number in zip(DERIVED, amounts):
            self.derived[key].set('—' if number is None else format_number(number) + (' mm²' if key == 'diverted' else ' mm'))
        self._show_status(model, state, speed_ok, in_bed)
        draw_section(self.scene, model, state, self.camera.get() == 'follow')
        if self._chart_mode == 'height':
            draw_height_chart(self.chart, model, state, self.curve)
        elif self._chart_mode == 'flow' and model.aor:
            view = self.flow_view.get()
            draw_discharge_chart(self.canvases['flow'], model, state, view)
            low, mean, high, contrast = discharge_readings(model, view)
            unit = 'g/s' if view == 'time' else 'mm'
            self.flow_summary.set(f'골 {low:.4g}   평균 {mean:.4g}   마루 {high:.4g} {unit}  ·  대비 {100*contrast:.3g}%')
        elif self._chart_mode == 'compare' and self.reference:
            self._draw_comparison(state)

    def _draw_comparison(self, state):
        metric = self.compare_metric.get()
        canvas = self.canvases['compare']
        if metric not in self._comparisons:
            if self._comparison_key != metric or self._comparison_future is None:
                if self._comparison_future is not None:
                    self._comparison_future.cancel()
                self._comparison_key = metric
                self._comparison_future = self._executor.submit(prepare_comparison, self.model, self.reference, metric)
                self._arm_poll()
            self.compare_summary.set('')
            self.compare_current.set('')
            empty(canvas, '비교 곡선 계산 중…')
            return
        data = self._comparisons[metric]
        if isinstance(data, str):
            self.compare_summary.set('')
            self.compare_current.set('')
            empty(canvas, data)
            return
        current = data.at_state(state)
        if current is None:
            self.compare_current.set('이 항목의 공정 구간 밖입니다.')
        else:
            self.compare_current.set(f'AOR {quantity(current.actual, data.unit)}   ·   연속 {quantity(current.reference, data.unit)}'
                                     f'   ·   편차 {quantity(current.difference, data.unit)} ({quantity(current.percent, "%")})')
        view = self.compare_view.get()
        summary = data.percent if view == 'percent' else data.difference
        unit = '%' if view == 'percent' else data.unit
        self.compare_summary.set('최소 ' + quantity(summary.minimum) + '  최대 ' + quantity(summary.maximum)
                                 + '  RMS ' + quantity(summary.rms, unit))
        draw_comparison_chart(canvas, data, current, view)

    def _show_status(self, model, state, speed_ok, in_bed):
        style = 'Suite.CardText.TLabel'
        if not speed_ok:
            message, style = '재생 속도에 0보다 큰 수를 입력하세요.', 'Suite.Status.Error.TLabel'
        else:
            stage = {'feed': '뒤 → 앞 피딩', 'compact': '앞 → 뒤 스프래딩·컴팩팅',
                     'combined': '앞 → 뒤 피딩·스프래딩·컴팩팅'}[state.phase]
            if not model.parameters.spread_on:
                stage = stage.replace('스프래딩·', '')
            if state.phase == 'combined' and self.distance >= model.feed_end:
                stage = '토출 완료 · 앞 → 뒤 롤러 통과'
            if self.distance >= model.travel_end:
                stage = '완료'
            details = []
            if model.aor:
                details.append('토출 ' + quantity(state.flux, 'g/s'))
                details.append('경과 ' + quantity(state.elapsed, 's'))
            if state.contacted:
                details.append('롤러 접촉')
            elif state.compact_started and model.balance < -1e-12:
                details.append('평균 공급 부족')
            message = '  ·  '.join((stage, *details))
        self.status_label.configure(style=style)
        self.status.set(message)

    def input_state(self):
        selected = self.plots.select()
        plot = next((k for k, page in self.plot_pages.items() if str(page) == selected), self._chart_mode)
        metric = self.compare_metric.get()
        if not self.spread_on.get() and metric == 'spread':
            metric = 'compact'
        return {'parameters': {name: var.get() for name, var in self.fields.items()},
                'spread_on': self.spread_on.get(), 'speed': self.speed.get(), 'camera': self.camera.get(),
                'feed_mode': self._mode, 'direction': self.direction.get(),
                'aor': {name: var.get() for name, var in self.aor_panel.fields.items()},
                'angles': {**self._angles, self._mode: self.fields['angle'].get()},
                'preset': self.preset.get(), 'calibration_open': self.aor_panel.calibration_open.get(),
                'plot': 'height' if self._mode == 'uniform' else plot, 'flow_view': self.flow_view.get(),
                'compare_metric': metric, 'compare_view': self.compare_view.get()}

    def restore_input_state(self, state):
        if not isinstance(state, dict) or not state:
            return
        raw = state.get('parameters', {})
        self._loading = True
        try:
            if isinstance(raw, dict):
                for name, value in raw.items():
                    if name in self.fields and isinstance(value, str) and len(value) <= 80:
                        self.fields[name].set(value)
            # Pre-AOR profiles are uniform recipes. A fresh profile opens in AOR mode.
            mode = state.get('feed_mode', 'uniform' if raw else 'aor')
            self._mode = mode if isinstance(mode, str) and mode in FEED_MODES else 'aor'
            self.feed_mode.set(self._mode)
            for key, var, choices, default in (
                ('direction', self.direction, DIRECTIONS, 'rear_to_front'),
                ('flow_view', self.flow_view, FLOW_VIEWS, 'time'),
                ('compare_metric', self.compare_metric, COMPARISONS, 'flow'),
                ('compare_view', self.compare_view, COMPARE_VIEWS, 'percent')):
                value = state.get(key)
                var.set(value if isinstance(value, str) and value in choices else default)
            angles = state.get('angles', {})
            for key in FEED_MODES:
                value = angles.get(key) if isinstance(angles, dict) else None
                if isinstance(value, str) and len(value) <= 80:
                    self._angles[key] = value
            if isinstance(raw, dict) and 'angle' in raw:
                self._angles[self._mode] = self.fields['angle'].get()
            self.fields['angle'].set(self._angles[self._mode])
            aor = state.get('aor')
            if isinstance(aor, dict):
                for name, value in aor.items():
                    if name in self.aor_panel.fields and isinstance(value, str) and len(value) <= 80:
                        self.aor_panel.fields[name].set(value)
            plot = state.get('plot')
            self._chart_mode = plot if isinstance(plot, str) and plot in self.plot_pages else 'flow'
            if self._mode == 'uniform':
                self._chart_mode = 'height'
            self.plots.tab(self.plot_pages[self._chart_mode], state='normal')
            self.plots.select(self.plot_pages[self._chart_mode])
            if type(state.get('spread_on')) is bool:
                self.spread_on.set(state['spread_on'])
            if isinstance(state.get('speed'), str) and len(state['speed']) <= 80:
                self.speed.set(state['speed'])
            if state.get('camera') in ('follow', 'full'):
                self.camera.set(state['camera'])
            preset = state.get('preset')
            if isinstance(preset, str) and preset in PRESETS:
                self.preset.set(preset)
            elif raw:
                self.preset.set('사용자 입력')
            self.aor_panel.set_calibration_open(state.get('calibration_open') is True)
        finally:
            self._loading = False
        self._reset_on_build = True
        self._rebuild()


def main():
    root = tk.Tk()
    root.title('리코터 시뮬레이터')
    root.geometry('1130x690')
    root.minsize(820, 540)
    ThemeManager(root).apply()
    RecoaterApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
