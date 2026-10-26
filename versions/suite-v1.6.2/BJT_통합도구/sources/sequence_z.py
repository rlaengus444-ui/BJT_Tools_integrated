"""M1 Z-axis sequence calculations with exact Decimal arithmetic.

The original worksheet fixes the layer thickness at 0.1.  This module keeps
that constant and calculates all three Z values independently.  It imports no
spreadsheet library and does not read or modify the source workbook.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import numbers
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


LAYER_THICKNESS = Decimal("0.1")
MAX_LAYER = 1_000_000_000
PAGE_SIZE = 200
CSV_HEADERS = ("레이어", "프린팅 Z", "이동/피딩(Trinity) Z", "리코팅 Z")


def _decimal(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(label + "에 유한한 숫자를 입력하세요.")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(label + "에 유한한 숫자를 입력하세요.") from exc
    if not result.is_finite():
        raise ValueError(label + "에는 NaN이나 무한대를 사용할 수 없습니다.")
    return result


def _layer(value, label="레이어", maximum=None):
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(label + "는 1 이상의 정수여야 합니다.")
    value = int(value)
    if value < 1 or maximum is not None and value > maximum:
        suffix = f"1~{maximum:,}" if maximum is not None else "1 이상"
        raise ValueError(label + "는 " + suffix + "의 정수여야 합니다.")
    return value


def _range(start, end):
    start = _layer(start, "시작 레이어", MAX_LAYER)
    end = _layer(end, "끝 레이어", MAX_LAYER)
    if start > end:
        raise ValueError("시작 레이어는 끝 레이어보다 클 수 없습니다.")
    return start, end


@dataclass(frozen=True)
class SequenceZSettings:
    initial_z: Decimal = Decimal("364")
    feeding_drop: Decimal = Decimal("0.6")
    recoating_rise: Decimal = Decimal("0.5")

    def __post_init__(self):
        for name, label in (("initial_z", "초기 Z"),
                            ("feeding_drop", "이동/피딩 하강값"),
                            ("recoating_rise", "리코팅 상승값")):
            object.__setattr__(self, name, _decimal(getattr(self, name), label))


@dataclass(frozen=True)
class SequenceZRow:
    layer: int
    printing_z: Decimal
    trinity_z: Decimal
    recoating_z: Decimal


def calculate_z(settings, layer):
    """Return the worksheet's three exact values; negative results are valid."""
    if not isinstance(settings, SequenceZSettings):
        raise TypeError("settings는 SequenceZSettings여야 합니다.")
    layer = _layer(layer)
    offset = Decimal(layer - 1)
    values = (settings.initial_z, settings.feeding_drop,
              settings.recoating_rise, LAYER_THICKNESS)
    # Include the complete integer/fraction span, rather than rounding long
    # user inputs to Decimal's default 28-digit context.
    smallest_exponent = min(value.as_tuple().exponent for value in values)
    largest_adjusted = max([value.adjusted() for value in values] +
                           [offset.adjusted() - 1])
    with localcontext() as context:
        context.prec = max(28, largest_adjusted - smallest_exponent + 8)
        context.Emax = max(context.Emax, largest_adjusted + 4)
        context.Emin = min(context.Emin, smallest_exponent - 4)
        printing = settings.initial_z - offset * LAYER_THICKNESS
        trinity = printing - settings.feeding_drop + LAYER_THICKNESS
        recoating = trinity + settings.recoating_rise
    return SequenceZRow(layer, printing, trinity, recoating)


def format_decimal(value):
    """Fixed decimal notation without binary-float noise or cosmetic zeroes."""
    value = _decimal(value, "계산값")
    if not value:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def row_values(row):
    return (str(row.layer), format_decimal(row.printing_z),
            format_decimal(row.trinity_z), format_decimal(row.recoating_z))


def calculate_page(settings, start, end, page_index=0, page_size=PAGE_SIZE):
    """Calculate one bounded page, never materializing the full layer range."""
    start, end = _range(start, end)
    if isinstance(page_index, bool) or not isinstance(page_index, numbers.Integral) or page_index < 0:
        raise ValueError("페이지 번호는 0 이상의 정수여야 합니다.")
    if isinstance(page_size, bool) or not isinstance(page_size, numbers.Integral) or not 1 <= page_size <= PAGE_SIZE:
        raise ValueError(f"페이지 크기는 1~{PAGE_SIZE}여야 합니다.")
    first = start + int(page_index) * int(page_size)
    if first > end:
        raise ValueError("표 범위를 벗어난 페이지입니다.")
    return tuple(calculate_z(settings, number)
                 for number in range(first, min(end, first + int(page_size) - 1) + 1))


@dataclass(frozen=True)
class CSVExportResult:
    path: str
    rows_written: int
    total_rows: int
    completed: bool
    cancelled: bool
    elapsed_seconds: float


def export_z_csv(settings, start, end, path, cancel_event=None, progress=None, *, stop_event=None):
    """Stream all rows into a temporary UTF-8-BOM CSV, then atomically publish.

    Cancellation or failure leaves any existing destination unchanged.  A
    cancelled result counts temporary rows, which were not published.
    """
    if not isinstance(settings, SequenceZSettings):
        raise TypeError("settings는 SequenceZSettings여야 합니다.")
    start, end = _range(start, end)
    if cancel_event is not None and stop_event is not None and cancel_event is not stop_event:
        raise ValueError("중지 이벤트는 하나만 지정하세요.")
    event = cancel_event if cancel_event is not None else stop_event
    destination = Path(os.path.abspath(os.fspath(path)))
    if not destination.parent.is_dir():
        raise ValueError("CSV를 저장할 폴더가 없습니다.")
    if destination.is_dir():
        raise ValueError("CSV 저장 경로에 폴더를 지정할 수 없습니다.")
    started = time.monotonic()
    total, written = end - start + 1, 0
    temporary = None
    descriptor = None
    last_progress = 0.0

    def stopped():
        return event is not None and event.is_set()

    def result(completed, cancelled):
        return CSVExportResult(str(destination), written, total, completed,
                               cancelled, round(time.monotonic() - started, 6))

    try:
        if stopped():
            return result(False, True)
        descriptor, name = tempfile.mkstemp(prefix=".sequence-z-", suffix=".tmp", dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            descriptor = None
            writer = csv.writer(stream)
            writer.writerow(CSV_HEADERS)
            for layer in range(start, end + 1):
                if stopped():
                    return result(False, True)
                writer.writerow(row_values(calculate_z(settings, layer)))
                written += 1
                now = time.monotonic()
                if progress is not None and (written == total or now - last_progress >= .1):
                    last_progress = now
                    progress(written * 100 // total,
                             f"CSV {written:,}/{total:,}행 작성 중")
            stream.flush()
            os.fsync(stream.fileno())
        if stopped():
            return result(False, True)
        os.replace(temporary, destination)
        temporary = None
        return result(True, False)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class SequenceZApp:
    """Paged calculator controller compatible with the suite's ToolRoot."""
    def __init__(self, root):
        self.root = root
        self.worker = None
        self.running = False
        self.cancel_event = threading.Event()
        self.stop_event = self.cancel_event
        self.events = queue.Queue()
        self.last_export_result = None
        self._closed = False
        self._close_requested = False
        self._poll_id = None
        self._controls = []
        self._control_states = {}
        self._table_snapshot = None
        self._query_row = None
        self._page_index = 0
        self._revision = 0
        root.title("시퀀스별 Z값 · M1 Z축")
        root.geometry("1120x780")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.initial_z_var = tk.StringVar(master=root, value="364")
        self.feeding_drop_var = tk.StringVar(master=root, value="0.6")
        self.recoating_rise_var = tk.StringVar(master=root, value="0.5")
        self.layer_var = tk.StringVar(master=root, value="1")
        self.start_var = tk.StringVar(master=root, value="1")
        self.end_var = tk.StringVar(master=root, value="3543")
        self.status_var = tk.StringVar(master=root, value="미계산 · 설정을 확인하고 조회 또는 표 계산을 누르세요.")
        self.query_status_var = tk.StringVar(master=root, value="미계산")
        self.table_status_var = tk.StringVar(master=root, value="표 미계산")
        self.query_values = [tk.StringVar(master=root, value="—") for _ in range(3)]
        self._build_ui()
        for variable in (self.initial_z_var, self.feeding_drop_var, self.recoating_rise_var):
            variable.trace_add("write", self._settings_changed)
        self.layer_var.trace_add("write", self._query_changed)
        self.start_var.trace_add("write", self._range_changed)
        self.end_var.trace_add("write", self._range_changed)
        self.root.bind("<Destroy>", self._destroyed, add="+")
        self._refresh_actions()
        self._poll_id = self.root.after(75, self._poll)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(16, 12))
        outer.pack(fill="both", expand=True)
        settings = ttk.LabelFrame(outer, text="1. Z 설정", padding=(12, 8))
        settings.pack(fill="x")
        for index, (label, variable) in enumerate((
                ("초기 Z", self.initial_z_var),
                ("이동/피딩(Trinity) 하강값", self.feeding_drop_var),
                ("리코팅 상승값", self.recoating_rise_var))):
            ttk.Label(settings, text=label).grid(row=0, column=index * 2, sticky="w", padx=(0 if index == 0 else 20, 7))
            entry = ttk.Entry(settings, textvariable=variable, width=16)
            entry.grid(row=0, column=index * 2 + 1, sticky="ew")
            settings.columnconfigure(index * 2 + 1, weight=1)
            self._controls.append(entry)
        ttk.Label(settings, text="원본 고정값: 레이어 두께 0.1  ·  하강값은 빼고 상승값은 더합니다. 음수 입력·음수 계산값도 허용합니다.").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        query = ttk.LabelFrame(outer, text="2. 단일 레이어 조회", padding=(12, 8))
        query.pack(fill="x", pady=(10, 0))
        line = ttk.Frame(query)
        line.pack(fill="x")
        ttk.Label(line, text="레이어").pack(side="left")
        entry = ttk.Entry(line, textvariable=self.layer_var, width=12)
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda event: self.query_layer())
        self.query_button = ttk.Button(line, text="조회", command=self.query_layer)
        self.query_button.pack(side="left")
        self.copy_query_button = ttk.Button(line, text="조회 결과 복사", command=self.copy_query)
        self.copy_query_button.pack(side="right")
        ttk.Label(line, textvariable=self.query_status_var).pack(side="right", padx=12)
        values = ttk.Frame(query)
        values.pack(fill="x", pady=(8, 0))
        for index, label in enumerate(("프린팅 Z", "이동/피딩(Trinity) Z", "리코팅 Z")):
            values.columnconfigure(index, weight=1)
            cell = ttk.Frame(values)
            cell.grid(row=0, column=index, sticky="ew", padx=(0, 12))
            ttk.Label(cell, text=label).pack(anchor="w")
            ttk.Label(cell, textvariable=self.query_values[index], font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        self._controls.extend((entry, self.query_button, self.copy_query_button))

        table_box = ttk.LabelFrame(outer, text="3. 전체표 · 페이지당 200행", padding=(12, 8))
        table_box.pack(fill="both", expand=True, pady=(10, 0))
        actions = ttk.Frame(table_box)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Label(actions, text="시작").pack(side="left")
        first = ttk.Entry(actions, textvariable=self.start_var, width=12)
        first.pack(side="left", padx=(6, 12))
        ttk.Label(actions, text="끝").pack(side="left")
        last = ttk.Entry(actions, textvariable=self.end_var, width=12)
        last.pack(side="left", padx=(6, 12))
        self.calculate_button = ttk.Button(actions, text="표 계산", command=self.calculate_table, style="Suite.Primary.TButton")
        self.calculate_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="전체 범위 CSV 저장", command=self.export_csv)
        self.export_button.pack(side="right")
        self.copy_selected_button = ttk.Button(actions, text="선택행 복사", command=self.copy_selected)
        self.copy_selected_button.pack(side="right", padx=8)
        self._controls.extend((first, last, self.calculate_button, self.export_button, self.copy_selected_button))
        frame = ttk.Frame(table_box)
        frame.pack(fill="both", expand=True)
        columns = ("layer", "printing", "trinity", "recoating")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended", height=8)
        for column, title in zip(columns, CSV_HEADERS):
            self.table.heading(column, text=title)
            self.table.column(column, width=110 if column == "layer" else 230, minwidth=90, anchor="e", stretch=True)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.table.bind("<<TreeviewSelect>>", lambda event: self._refresh_actions())
        self.table.bind("<Control-c>", self.copy_selected, add="+")
        pager = ttk.Frame(table_box)
        pager.pack(fill="x", pady=(7, 0))
        self.prev_button = ttk.Button(pager, text="이전 200행", command=self.previous_page)
        self.prev_button.pack(side="left")
        self.next_button = ttk.Button(pager, text="다음 200행", command=self.next_page)
        self.next_button.pack(side="left", padx=6)
        ttk.Label(pager, textvariable=self.table_status_var).pack(side="left", padx=10)
        self._controls.extend((self.prev_button, self.next_button))
        ttk.Label(outer, text="프린팅 Z = 초기 Z − (레이어−1)×0.1   |   Trinity Z = 프린팅 Z − 하강값 + 0.1   |   리코팅 Z = Trinity Z + 상승값").pack(
            fill="x", pady=(8, 3))
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        self.stop_button = ttk.Button(footer, text="CSV 저장 중지", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")
        self.progress_bar = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ttk.Label(outer, textvariable=self.status_var, wraplength=1000).pack(fill="x", pady=(5, 0))

    def _settings(self):
        return SequenceZSettings(self.initial_z_var.get(), self.feeding_drop_var.get(), self.recoating_rise_var.get())

    @staticmethod
    def _input_layer(text, label):
        value = text.strip()
        if not value or not value.isascii() or not value.isdecimal():
            raise ValueError(label + "에 1 이상의 정수를 입력하세요.")
        return _layer(int(value), label, MAX_LAYER)

    def _clear_query(self):
        self._query_row = None
        self.query_status_var.set("미계산")
        for value in self.query_values:
            value.set("—")

    def _clear_table(self):
        self._table_snapshot = None
        self._page_index = 0
        self.table.delete(*self.table.get_children())
        self.table_status_var.set("표 미계산")

    def _changed(self, message):
        self._revision += 1
        self.last_export_result = None
        if self.is_busy():
            self.cancel_event.set()
            self.status_var.set("입력 변경 · CSV 저장 중지와 임시 파일 정리를 기다립니다.")
        else:
            self.progress_bar.configure(value=0)
            self.status_var.set(message)
        self._refresh_actions()

    def _settings_changed(self, *unused):
        self._clear_query()
        self._clear_table()
        self._changed("설정 변경 · 미계산. 조회 또는 표 계산을 다시 실행하세요.")

    def _range_changed(self, *unused):
        self._clear_table()
        self._changed("표 범위 변경 · 미계산. 표 계산을 다시 실행하세요.")

    def _query_changed(self, *unused):
        self._clear_query()
        if not self.is_busy():
            self.status_var.set("조회 레이어 변경 · 단일 조회를 다시 실행하세요. 전체표 조건은 유지됩니다.")
        self._refresh_actions()

    def is_busy(self):
        return self.running or self.worker is not None and self.worker.is_alive()

    def _refresh_actions(self):
        busy = self.is_busy()
        self.query_button.configure(state="disabled" if busy else "normal")
        self.calculate_button.configure(state="disabled" if busy else "normal")
        self.copy_query_button.configure(state="normal" if self._query_row is not None and not busy else "disabled")
        self.copy_selected_button.configure(state="normal" if self._table_snapshot and self.table.selection() and not busy else "disabled")
        self.export_button.configure(state="normal" if self._table_snapshot is not None and not busy else "disabled")
        count = 0 if self._table_snapshot is None else (self._table_snapshot[2] - self._table_snapshot[1]) // PAGE_SIZE + 1
        self.prev_button.configure(state="normal" if not busy and self._table_snapshot and self._page_index > 0 else "disabled")
        self.next_button.configure(state="normal" if not busy and self._table_snapshot and self._page_index + 1 < count else "disabled")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def query_layer(self):
        if self.is_busy():
            return
        try:
            layer = self._input_layer(self.layer_var.get(), "조회 레이어")
            row = calculate_z(self._settings(), layer)
        except (ValueError, InvalidOperation) as exc:
            messagebox.showerror("입력 확인", str(exc), parent=self.root)
            return
        self._query_row = row
        for variable, value in zip(self.query_values, row_values(row)[1:]):
            variable.set(value)
        self.query_status_var.set(f"{layer:,} 레이어 · 계산 완료")
        self.status_var.set("단일 조회 완료 · 원본 수식 그대로 계산했습니다.")
        self._refresh_actions()

    def calculate_table(self):
        if self.is_busy():
            return
        try:
            settings = self._settings()
            start = self._input_layer(self.start_var.get(), "시작 레이어")
            end = self._input_layer(self.end_var.get(), "끝 레이어")
            start, end = _range(start, end)
        except (ValueError, InvalidOperation) as exc:
            messagebox.showerror("입력 확인", str(exc), parent=self.root)
            return
        self._table_snapshot = settings, start, end
        self._page_index = 0
        self._show_page()
        self.status_var.set(f"표 계산 완료 · 총 {end - start + 1:,}행. CSV는 현재 페이지와 관계없이 전체 범위를 저장합니다.")

    def _show_page(self):
        if self._table_snapshot is None:
            return
        settings, start, end = self._table_snapshot
        rows = calculate_page(settings, start, end, self._page_index)
        self.table.delete(*self.table.get_children())
        for row in rows:
            self.table.insert("", "end", iid=str(row.layer), values=row_values(row))
        pages = (end - start) // PAGE_SIZE + 1
        self.table_status_var.set(f"{rows[0].layer:,}~{rows[-1].layer:,} / 총 {end - start + 1:,}행 · {self._page_index + 1:,}/{pages:,}페이지")
        self._refresh_actions()

    def previous_page(self):
        if not self.is_busy() and self._table_snapshot is not None and self._page_index:
            self._page_index -= 1
            self._show_page()

    def next_page(self):
        if self.is_busy() or self._table_snapshot is None:
            return
        _, start, end = self._table_snapshot
        if (self._page_index + 1) * PAGE_SIZE < end - start + 1:
            self._page_index += 1
            self._show_page()

    def _copy(self, rows):
        text = "\t".join(CSV_HEADERS) + "\n" + "\n".join("\t".join(values) for values in rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def copy_query(self):
        if self._query_row is not None and not self.is_busy():
            self._copy([row_values(self._query_row)])
            self.status_var.set("단일 조회 결과를 클립보드에 복사했습니다.")

    def copy_selected(self, event=None):
        if self._table_snapshot is not None and not self.is_busy():
            rows = [self.table.item(iid, "values") for iid in self.table.selection()]
            if rows:
                self._copy(rows)
                self.status_var.set(f"선택한 {len(rows):,}행을 클립보드에 복사했습니다.")
        if event is not None:
            return "break"

    def _set_busy(self, busy):
        if busy:
            self._control_states = {widget: str(widget.cget("state")) for widget in self._controls}
            for widget in self._controls:
                widget.configure(state="disabled")
        else:
            for widget, state in self._control_states.items():
                widget.configure(state=state)
            self._control_states.clear()
        self._refresh_actions()

    def export_csv(self, path=None):
        if self.is_busy() or self._table_snapshot is None:
            return
        # Validate the snapshot even if a caller modified StringVars without
        # interacting with the entries.  Never export stale table conditions.
        settings, start, end = self._table_snapshot
        try:
            current = (self._settings(), self._input_layer(self.start_var.get(), "시작 레이어"),
                       self._input_layer(self.end_var.get(), "끝 레이어"))
            if current != self._table_snapshot:
                self._clear_table()
                self._refresh_actions()
                raise ValueError("입력이 바뀌었습니다. 표 계산을 다시 실행하세요.")
        except ValueError as exc:
            messagebox.showerror("입력 확인", str(exc), parent=self.root)
            return
        if path is None:
            path = filedialog.asksaveasfilename(parent=self.root, title="시퀀스별 Z값 전체 CSV 저장",
                defaultextension=".csv", initialfile=f"M1_Z_{start}_{end}.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self.cancel_event.clear()
        self.running = True
        self.last_export_result = None
        self.progress_bar.configure(value=0)
        self._set_busy(True)
        self.status_var.set(f"전체 범위 {start:,}~{end:,} · CSV를 임시 파일에 작성합니다.")
        revision = self._revision
        def work():
            try:
                result = export_z_csv(settings, start, end, path, self.cancel_event,
                    lambda percent, message: self.events.put(("progress", percent, message)))
                self.events.put(("done", result, revision))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        self.worker = threading.Thread(target=work, name="sequence-z-csv", daemon=True)
        self.worker.start()

    def stop(self):
        if self.is_busy():
            self.cancel_event.set()
            self.stop_button.configure(state="disabled")
            self.status_var.set("CSV 저장 중지 요청 · 임시 파일 정리를 기다립니다.")

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            for _ in range(50):
                event = self.events.get_nowait()
                if event[0] == "progress":
                    if not self.cancel_event.is_set():
                        self.progress_bar.configure(value=event[1])
                        self.status_var.set(event[2])
                elif event[0] == "done":
                    result = event[1]
                    self.running = False
                    self.worker = None
                    self.last_export_result = result
                    self._set_busy(False)
                    if result.completed:
                        self.progress_bar.configure(value=100)
                        suffix = " (입력 변경 전 조건)" if event[2] != self._revision else ""
                        self.status_var.set(f"CSV 저장 완료{suffix} · {result.rows_written:,}행 · {result.path}")
                    else:
                        self.progress_bar.configure(value=0)
                        self.status_var.set("CSV 저장 중지 · 미완성 임시 파일을 제거했습니다. 기존 저장 파일은 유지됩니다.")
                elif event[0] == "error":
                    self.running = False
                    self.worker = None
                    self._set_busy(False)
                    self.status_var.set("CSV 저장 실패 · " + event[1])
        except queue.Empty:
            pass
        if self._close_requested and not self.is_busy():
            self.close()
            return
        if not self._closed:
            self._poll_id = self.root.after(75, self._poll)

    def _destroyed(self, event):
        if event.widget is self.root:
            self._closed = True
            self.cancel_event.set()
            if self._poll_id is not None:
                try:
                    self.root.after_cancel(self._poll_id)
                except tk.TclError:
                    pass
                self._poll_id = None

    def close(self):
        if self._closed:
            return
        self.cancel_event.set()
        if self.is_busy():
            self._close_requested = True
            self.status_var.set("CSV 저장 중지와 임시 파일 정리를 마친 뒤 종료합니다.")
            return
        self._closed = True
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.root.destroy()


def main():
    root = tk.Tk()
    SequenceZApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
