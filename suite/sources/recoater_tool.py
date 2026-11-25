"""Native interactive side-section simulator for the BJT recoater."""
from __future__ import annotations

from dataclasses import asdict
import math
import time
import tkinter as tk
from tkinter import ttk

from recoater_model import Parameters, ROLLER_EXAMPLE, SHEET_EXAMPLE, build_curve, make_model
from recoater_views import (
    CONTACT, DEPOSIT, FEED, PASS, PLOT, POWDER, SPREAD, chart_x_bounds, draw_height_chart,
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
    ('feed', '피딩 직후 두께'), ('base', '컴팩팅 전 균일층'), ('required', '목표층에 필요한 겉보기 두께'),
    ('deposit', '형성층 두께'), ('balance', '잉여 두께'), ('swept', '스프래딩이 걷어낸 두께'),
    ('sweep', '스프래딩롤러가 지난 거리'), ('diverted', '접촉 후 유출 단면적'),
)


class RecoaterApp:
    def __init__(self, root):
        self.root = root
        self.fields = {key: tk.StringVar(root) for key, _ in (*POWDER_FIELDS, *ROLLERS)}
        self.spread_on = tk.BooleanVar(root, value=True)
        self.speed = tk.StringVar(root, value='16')
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
        for var in (*self.fields.values(), self.spread_on):
            var.trace_add('write', self._schedule_rebuild)
        self.speed.trace_add('write', self._speed_changed)
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
        self._label(heading, '이동 단면', style='Suite.CardTitle.TLabel').pack(side='left')
        self.view_buttons = {}
        for key, caption in (('full', '베드 전체'), ('follow', '롤러 확대')):
            button = ttk.Button(heading, text=caption, command=lambda mode=key: self._set_camera(mode))
            button.pack(side='right', padx=(5, 0))
            self.view_buttons[key] = button
        legend = ttk.Frame(scene_card, style='Suite.Surface.TFrame')
        legend.pack(side='bottom', fill='x', pady=(6, 0))
        self.spread_keys = []
        for column, (color, label, spread_only) in enumerate((
            (POWDER, '컴팩팅 앞', False), (SPREAD, '스프래딩 앞', True),
            (DEPOSIT, '형성층', False), (PASS, '통과층', True), (FEED, '피딩층', False),
        )):
            item = ttk.Frame(legend, style='Suite.Surface.TFrame')
            item.grid(row=0, column=column, padx=(0, 11))
            tk.Canvas(item, width=9, height=9, highlightthickness=0, background=color).pack(
                side='left', padx=(0, 4))
            self._label(item, label).pack(side='left')
            if spread_only:
                self.spread_keys.append(item)
        self.scene = tk.Canvas(scene_card, width=1, height=1, background=PLOT, highlightthickness=0)
        self.scene.pack(fill='both', expand=True)

        transport = self._card(work, (10, 6))
        transport.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        self._build_transport(transport)

        plot_card = self._card(work, (11, 7))
        plot_card.grid(row=2, column=0, sticky='nsew')
        chart_heading = ttk.Frame(plot_card, style='Suite.Surface.TFrame')
        chart_heading.pack(fill='x', pady=(0, 3))
        self._label(chart_heading, '진행거리별 높이 (mm)', style='Suite.CardTitle.TLabel').pack(side='left')
        self._label(chart_heading, '실선 · 진행   점선 · 예상   거리 클릭 · 이동').pack(side='right')
        self.chart = tk.Canvas(plot_card, width=1, height=1, background=COLORS['surface'],
                               highlightthickness=0, cursor='crosshair')
        self.chart.pack(fill='both', expand=True)
        self.chart.bind('<Button-1>', self._chart_seek)
        for canvas in (self.scene, self.chart):
            canvas.bind('<Configure>', self._schedule_paint)

        panel = self._card(body, (10, 9))
        panel.grid(row=0, column=1, sticky='nsew')
        self._build_sidebar(panel)

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
        self._label(seek_row, value=self.readout, style='Recoater.Value.TLabel', width=24).pack(side='left')
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
        self._label(settings, '롤러', style='Suite.CardTitle.TLabel').pack(anchor='w', pady=(7, 0))
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

    def _field_grid(self, parent, fields):
        grid = ttk.Frame(parent, style='Suite.Surface.TFrame')
        grid.pack(fill='x', pady=(2, 0))
        for column in range(2):
            grid.columnconfigure(column, weight=1, uniform='condition')
        for i, (key, label) in enumerate(fields):
            cell = ttk.Frame(grid, style='Suite.Surface.TFrame')
            cell.grid(row=i//2, column=i%2, sticky='ew', padx=(0, 5) if i%2 == 0 else 0, pady=(0, 5))
            self._label(cell, label).pack(anchor='w')
            entry = ttk.Entry(cell, textvariable=self.fields[key], width=10, justify='right')
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
            self.spread_on.set(params.spread_on)
        finally:
            self._loading = False
        self.distance = 0.
        self._rebuild()

    def _schedule_rebuild(self, *_):
        if self._loading or self._closed:
            return
        self._halt()
        self.preset.set('사용자 입력')
        self.play_button.state(['disabled'])
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
        try:
            values = {}
            for name, var in self.fields.items():
                try:
                    values[name] = float(var.get())
                except ValueError:
                    values[name] = math.nan
            self.model = make_model(Parameters(**values, spread_on=spread))
            self.curve = build_curve(self.model)
            self.distance = min(self.distance, self.model.parameters.bed_length)
        except (ValueError, ArithmeticError) as exc:
            self.model = None
            self.curve = ()
            self.problem = str(exc) if isinstance(exc, ValueError) else '입력값의 단위와 크기를 확인하세요.'
        self._paint(reset_entry=True)

    def _set_camera(self, value):
        self.camera.set(value)
        self._paint()

    def _slide(self, fraction):
        if self.model is not None:
            self.seek(float(fraction) * self.model.parameters.bed_length)

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
            self.seek((event.x-left)/(right-left) * self.model.parameters.bed_length)

    def seek(self, distance):
        if self.model is None or self._edit is not None:
            return
        self._halt()
        self.distance = min(self.model.parameters.bed_length, max(0., distance))
        self._paint(reset_entry=True)

    def toggle_play(self):
        if self.is_playing:
            self.pause()
            return
        if self.model is None or self._edit is not None or self._speed_value() is None:
            return
        if self.distance >= self.model.parameters.bed_length:
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
        length = self.model.parameters.bed_length
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
        at_end = model is not None and self.distance >= model.parameters.bed_length
        action = '일시정지' if self.is_playing else '다시 재생' if at_end else '재생'
        self.play_button.configure(text=action, state='normal' if ready and speed_ok else 'disabled')
        for widget in (self.rewind_button, self.timeline, self.distance_entry):
            widget.state(['!disabled'] if ready else ['disabled'])
        in_bed = model is not None and model.contact_x <= model.parameters.bed_length
        self.contact_button.state(['!disabled'] if ready and in_bed else ['disabled'])
        for key, button in self.view_buttons.items():
            button.configure(style='Suite.Primary.TButton' if self.camera.get() == key else 'Suite.Secondary.TButton')
        if model is None:
            self.readout.set('—')
            self.status.set(self.problem)
            self.status_label.configure(style='Suite.Status.Error.TLabel')
            for var in (*self.metrics[0], *self.metrics[1], *self.derived.values()):
                var.set('—')
            empty(self.scene, '공정 조건을 확인하세요.')
            empty(self.chart, '')
            return

        state = model.at(self.distance)
        length = model.parameters.bed_length
        self.position.set(self.distance/length)
        self.readout.set(f'{format_number(self.distance, 2)} / {length:.5g} mm')
        if reset_entry or self.root.focus_get() is not self.distance_entry:
            self._display_distance = f'{self.distance:.9g}'
            self.distance_text.set(self._display_distance)
        for profile, row in zip((state, state.spreader), self.metrics):
            if profile is not None:
                for var, number in zip(row, (profile.height, profile.rise, profile.pile_area)):
                    var.set(format_number(number))
        amounts = (model.feed, model.base, model.required, model.deposit, model.balance,
                   model.swept_thickness, state.spread_distance, state.diverted_area)
        for (key, _), number in zip(DERIVED, amounts):
            self.derived[key].set(format_number(number) + (' mm²' if key == 'diverted' else ' mm'))
        self._show_status(model, state, speed_ok, in_bed)
        draw_section(self.scene, model, state, self.camera.get() == 'follow')
        draw_height_chart(self.chart, model, state, self.curve)

    def _show_status(self, model, state, speed_ok, in_bed):
        style = 'Suite.CardText.TLabel'
        if not speed_ok:
            message, style = '재생 속도에 0보다 큰 수를 입력하세요.', 'Suite.Status.Error.TLabel'
        elif model.balance < -1e-12:
            message, style = '목표층을 만들기에 파우더 공급이 부족합니다.', 'Suite.Status.Warning.TLabel'
        elif model.excess <= 0:
            message = '컴팩팅롤러 앞에 쌓이는 파우더가 없습니다.'
        elif state.contacted:
            message, style = '접촉 중 · 최초 접촉 ' + format_number(model.contact_x, 2) + ' mm', 'Suite.Status.Success.TLabel'
        elif in_bed:
            message = '접촉 전 · 최초 접촉 ' + format_number(model.contact_x, 2) + ' mm'
        elif state.spreader is None:
            message = '스프래딩롤러 미사용'
        else:
            message = '베드 내 접촉 없음 · 예상 접촉 거리 ' + format_number(model.contact_x, 2) + ' mm'
        self.status_label.configure(style=style)
        self.status.set(message)

    def input_state(self):
        return {'parameters': {name: var.get() for name, var in self.fields.items()},
                'spread_on': self.spread_on.get(), 'speed': self.speed.get(), 'camera': self.camera.get()}

    def restore_input_state(self, state):
        if not isinstance(state, dict):
            return
        raw = state.get('parameters', {})
        self._loading = True
        try:
            if isinstance(raw, dict):
                for name, value in raw.items():
                    if name in self.fields and isinstance(value, str) and len(value) <= 80:
                        self.fields[name].set(value)
            if type(state.get('spread_on')) is bool:
                self.spread_on.set(state['spread_on'])
            if isinstance(state.get('speed'), str) and len(state['speed']) <= 80:
                self.speed.set(state['speed'])
            if state.get('camera') in ('follow', 'full'):
                self.camera.set(state['camera'])
            if raw:
                self.preset.set('사용자 입력')
        finally:
            self._loading = False
        self.distance = 0.
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
