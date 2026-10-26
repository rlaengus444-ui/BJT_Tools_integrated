"""Ordered M2 image-conversion UI; native conversion lives in m2_image_engine.

No engine import occurs until conversion starts, so this controller can be
mounted and inspected independently of native GDI+ initialization.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
MODE_SPECS = {
    "싱글": {"mode": "single", "size": (8504, 6614), "dpi": (400, 400)},
    "멀티": {"mode": "multi", "size": (8504, 13228), "dpi": (400, 800)},
}
CONFLICTS = {"건너뛰기 (기본)": "skip", "덮어쓰기": "overwrite"}


def natural_key(name):
    return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold())
                 for part in re.split(r"(\d+)", str(name)))


class M2ConverterApp:
    """One ordered input list and one cancellable conversion worker."""
    def __init__(self, root):
        self.root = root
        self.input_paths = []
        self.worker = None
        self.running = False
        self.cancel_event = threading.Event()
        self.stop_event = self.cancel_event
        self.events = queue.Queue()
        self.last_report = None
        self._revision = 0
        self._run_attempted = False
        self._closed = False
        self._close_requested = False
        self._poll_id = None
        self._controls = []
        self._states = {}
        self._dnd_registered = False
        self.mode_var = tk.StringVar(master=root, value="싱글")
        self.output_var = tk.StringVar(master=root)
        self.start_var = tk.StringVar(master=root, value="1")
        self.padding_var = tk.StringVar(master=root, value="3")
        self.conflict_var = tk.StringVar(master=root, value="건너뛰기 (기본)")
        self.spec_var = tk.StringVar(master=root)
        self.count_var = tk.StringVar(master=root, value="입력 0개 · 표시 순서대로 번호 부여")
        self.status_var = tk.StringVar(master=root, value="변환 전 · 이미지를 추가하고 출력 폴더를 선택하세요.")
        root.title("M2 이미지 변환 · 싱글 / 멀티")
        root.geometry("1120x780")
        root.minsize(1120, 780)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self._update_specs()
        self.mode_var.trace_add("write", self._mode_changed)
        for variable in (self.output_var, self.start_var, self.padding_var, self.conflict_var):
            variable.trace_add("write", self._settings_changed)
        self.root.bind("<Destroy>", self._destroyed, add="+")
        self._refresh_actions()
        self._poll_id = self.root.after(75, self._poll)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(16, 8))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=3, minsize=135)
        outer.rowconfigure(4, weight=1, minsize=75)
        mode = ttk.LabelFrame(outer, text="1. 변환 모드", padding=(12, 4))
        mode.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        line = ttk.Frame(mode)
        line.pack(fill="x")
        ttk.Label(line, text="모드").pack(side="left")
        self.mode_box = ttk.Combobox(line, textvariable=self.mode_var, values=tuple(MODE_SPECS),
                                   state="readonly", width=10)
        self.mode_box.pack(side="left", padx=(8, 18))
        ttk.Label(line, textvariable=self.spec_var).pack(side="left")
        ttk.Label(mode, text="4비트 팔레트 TIFF · LZW · 원본 GDI+ 변환방식  |  멀티는 큰 이미지 1장이며 다중 페이지를 뜻하지 않습니다.").pack(
            anchor="w", pady=(6, 0))
        self._controls.append(self.mode_box)

        inputs = ttk.LabelFrame(outer, text="2. 입력 순서", padding=(12, 5))
        inputs.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        inputs.columnconfigure(0, weight=1)
        inputs.rowconfigure(1, weight=1)
        buttons = ttk.Frame(inputs)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.add_files_button = ttk.Button(buttons, text="이미지 추가", command=self.choose_files)
        self.add_files_button.pack(side="left")
        self.add_folder_button = ttk.Button(buttons, text="폴더 추가", command=self.choose_folder)
        self.add_folder_button.pack(side="left", padx=6)
        self.clear_button = ttk.Button(buttons, text="비우기", command=self.clear_inputs)
        self.clear_button.pack(side="right")
        self.remove_button = ttk.Button(buttons, text="선택 제거", command=self.remove_selected)
        self.remove_button.pack(side="right", padx=6)
        self.down_button = ttk.Button(buttons, text="아래로", command=self.move_down)
        self.down_button.pack(side="right", padx=(6, 0))
        self.up_button = ttk.Button(buttons, text="위로", command=self.move_up)
        self.up_button.pack(side="right")
        frame = ttk.Frame(inputs)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("order", "name", "path"), show="headings",
                                 selectmode="extended", height=6)
        for column, title, width in (("order", "순서", 65), ("name", "원본명", 220), ("path", "경로", 660)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=55, stretch=column != "order",
                             anchor="e" if column == "order" else "w")
        self.input_tree = self.tree
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._refresh_actions())
        self.tree.bind("<Delete>", lambda event: self.remove_selected())
        footer = ttk.Frame(inputs)
        footer.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(footer, textvariable=self.count_var).pack(side="left")
        hint = ttk.Label(footer, text="이미지·폴더 드롭 · 폴더 바로 아래만 자연 정렬 · ZIP 미지원")
        hint.pack(side="right")
        self._controls.extend((self.add_files_button, self.add_folder_button, self.clear_button,
                               self.remove_button, self.up_button, self.down_button))
        if DND_FILES is not None:
            for widget in (self.tree, hint):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<DropEnter>>", self._drop_enter)
                    widget.dnd_bind("<<Drop>>", self._drop)
                    self._dnd_registered = True
                except tk.TclError:
                    pass

        output = ttk.LabelFrame(outer, text="3. 출력과 번호", padding=(12, 4))
        output.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="출력 폴더").grid(row=0, column=0, sticky="w")
        self.output_entry = ttk.Entry(output, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.output_button = ttk.Button(output, text="폴더 선택", command=self.choose_output)
        self.output_button.grid(row=0, column=2)
        settings = ttk.Frame(output)
        settings.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Label(settings, text="시작 번호").pack(side="left")
        start = ttk.Entry(settings, textvariable=self.start_var, width=10)
        start.pack(side="left", padx=(7, 18))
        ttk.Label(settings, text="자리수 (0~12)").pack(side="left")
        padding = ttk.Entry(settings, textvariable=self.padding_var, width=6)
        padding.pack(side="left", padx=(7, 18))
        ttk.Label(settings, text="충돌 처리").pack(side="left")
        conflict = ttk.Combobox(settings, textvariable=self.conflict_var, values=tuple(CONFLICTS),
                                state="readonly", width=18)
        conflict.pack(side="left", padx=7)
        ttk.Label(settings, text="예: 001.tif → 002.tif · 원본명은 사용하지 않음").pack(side="right")
        self._controls.extend((self.output_entry, self.output_button, start, padding, conflict))

        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        line = ttk.Frame(actions)
        line.pack(fill="x")
        self.convert_button = ttk.Button(line, text="변환 시작", command=self.start_conversion, style="Suite.Primary.TButton")
        self.run_button = self.convert_button
        self.convert_button.pack(side="left")
        self.stop_button = ttk.Button(line, text="중지", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=7)
        self.save_json_button = ttk.Button(line, text="결과 저장(JSON)", command=self.save_result)
        self.save_json_button.pack(side="right")
        self.save_log_button = ttk.Button(line, text="로그 저장(TXT)", command=self.save_log)
        self.save_log_button.pack(side="right", padx=7)
        self.progress_bar = ttk.Progressbar(actions, mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x", pady=(7, 4))
        ttk.Label(actions, textvariable=self.status_var, wraplength=1000).pack(fill="x")
        self._controls.extend((self.convert_button, self.save_json_button, self.save_log_button))
        log = ttk.LabelFrame(outer, text="변환 결과", padding=(8, 6))
        log.grid(row=4, column=0, sticky="nsew")
        log.rowconfigure(0, weight=1)
        log.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log, height=5, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _update_specs(self):
        spec = MODE_SPECS.get(self.mode_var.get())
        if spec:
            self.spec_var.set(f"{spec['size'][0]:,} × {spec['size'][1]:,} px  ·  DPI {spec['dpi'][0]} × {spec['dpi'][1]}")
        else:
            self.spec_var.set("모드를 선택하세요.")

    def _mode_changed(self, *unused):
        self._update_specs()
        self._invalidate()

    def _settings_changed(self, *unused):
        self._invalidate()

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _invalidate(self):
        self._revision += 1
        self.last_report = None
        self._run_attempted = False
        if self.is_busy():
            self.cancel_event.set()
            self.status_var.set("입력 변경 · 현재 변환 단계와 임시 파일 정리가 끝나기를 기다립니다.")
        else:
            self.progress_bar.configure(value=0)
            self.status_var.set("입력 변경 · 변환 전. 표시된 순서와 설정을 확인하세요.")
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        self._refresh_actions()

    def is_busy(self):
        return self.running or self.worker is not None and self.worker.is_alive()

    def _options(self):
        if self.mode_var.get() not in MODE_SPECS:
            raise ValueError("싱글 또는 멀티 모드를 선택하세요.")
        start_text, padding_text = self.start_var.get().strip(), self.padding_var.get().strip()
        if not start_text.isascii() or not start_text.isdecimal() or int(start_text) < 1:
            raise ValueError("시작 번호는 1 이상의 정수여야 합니다.")
        if not padding_text.isascii() or not padding_text.isdecimal() or not 0 <= int(padding_text) <= 12:
            raise ValueError("자리수는 0~12의 정수여야 합니다.")
        if self.conflict_var.get() not in CONFLICTS:
            raise ValueError("충돌 처리 방식을 선택하세요.")
        output = self.output_var.get().strip()
        if not output:
            raise ValueError("출력 폴더를 선택하세요.")
        return (MODE_SPECS[self.mode_var.get()]["mode"], int(start_text),
                int(padding_text), CONFLICTS[self.conflict_var.get()], output)

    def _refresh_actions(self):
        busy = self.is_busy()
        selected = self._selected_indices()
        valid = bool(self.input_paths)
        try:
            self._options()
        except ValueError:
            valid = False
        self.convert_button.configure(state="normal" if valid and not busy else "disabled")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.clear_button.configure(state="normal" if self.input_paths and not busy else "disabled")
        self.remove_button.configure(state="normal" if selected and not busy else "disabled")
        self.up_button.configure(state="normal" if selected and selected[0] > 0 and not busy else "disabled")
        self.down_button.configure(state="normal" if selected and selected[-1] + 1 < len(self.input_paths) and not busy else "disabled")
        self.save_json_button.configure(state="normal" if self.last_report is not None and not busy else "disabled")
        self.save_log_button.configure(state="normal" if self._run_attempted and not busy else "disabled")

    def _selected_indices(self):
        return sorted(int(iid) for iid in self.tree.selection())

    def _render_inputs(self, selected=()):
        self.tree.delete(*self.tree.get_children())
        for index, path in enumerate(self.input_paths):
            self.tree.insert("", "end", iid=str(index), values=(index + 1, Path(path).name, path))
        existing = [str(index) for index in selected if index < len(self.input_paths)]
        if existing:
            self.tree.selection_set(existing)
            self.tree.see(existing[0])
        self.count_var.set(f"입력 {len(self.input_paths):,}개 · 표시 순서대로 번호 부여")
        self._refresh_actions()

    def add_paths(self, paths):
        if self.is_busy():
            return
        additions, rejected = [], []
        for value in paths:
            path = Path(value).expanduser().absolute()
            try:
                if path.is_dir():
                    files = [child for child in path.iterdir() if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS]
                    additions.extend(str(child.resolve()) for child in sorted(files, key=lambda p: (natural_key(p.name), p.name)))
                elif path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    additions.append(str(path.resolve()))
                else:
                    rejected.append(str(path))
            except OSError as exc:
                rejected.append(f"{path}: {exc}")
        known = {os.path.normcase(path) for path in self.input_paths}
        fresh = []
        for path in additions:
            key = os.path.normcase(path)
            if key not in known:
                known.add(key)
                fresh.append(path)
        if fresh:
            self.input_paths.extend(fresh)
            self._invalidate()
            self._render_inputs()
        if rejected:
            messagebox.showwarning("입력 확인", "PNG·JPEG·BMP·TIF·TIFF 이미지 또는 이미지 폴더만 추가할 수 있습니다. ZIP은 지원하지 않습니다.\n\n" +
                "\n".join(rejected[:5]) + (f"\n외 {len(rejected) - 5}개" if len(rejected) > 5 else ""), parent=self.root)
        elif not fresh:
            self.status_var.set("추가할 새 이미지가 없습니다. 하위 폴더는 검색하지 않으며 중복 경로는 추가하지 않습니다.")

    def choose_files(self):
        if not self.is_busy():
            paths = filedialog.askopenfilenames(parent=self.root, title="M2 변환 이미지 선택",
                filetypes=[("이미지", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("모든 파일", "*.*")])
            if paths:
                self.add_paths(paths)

    def choose_folder(self):
        if not self.is_busy():
            value = filedialog.askdirectory(parent=self.root, title="이미지 폴더 추가")
            if value:
                self.add_paths([value])

    def choose_output(self):
        if not self.is_busy():
            value = filedialog.askdirectory(parent=self.root, title="TIFF 출력 폴더")
            if value:
                self.output_var.set(value)

    def _drop_enter(self, event):
        return "refuse_drop" if self.is_busy() else "copy"

    def _drop(self, event):
        if self.is_busy():
            return "refuse_drop"
        self.add_paths(self.root.tk.splitlist(event.data))
        return "copy"

    def move_up(self):
        if self.is_busy():
            return
        selected = set(self._selected_indices())
        if not selected:
            return
        changed = False
        for index in sorted(selected):
            if index > 0 and index - 1 not in selected:
                self.input_paths[index - 1], self.input_paths[index] = self.input_paths[index], self.input_paths[index - 1]
                selected.remove(index)
                selected.add(index - 1)
                changed = True
        if changed:
            self._invalidate()
            self._render_inputs(sorted(selected))

    def move_down(self):
        if self.is_busy():
            return
        selected = set(self._selected_indices())
        if not selected:
            return
        changed = False
        for index in sorted(selected, reverse=True):
            if index + 1 < len(self.input_paths) and index + 1 not in selected:
                self.input_paths[index + 1], self.input_paths[index] = self.input_paths[index], self.input_paths[index + 1]
                selected.remove(index)
                selected.add(index + 1)
                changed = True
        if changed:
            self._invalidate()
            self._render_inputs(sorted(selected))

    def remove_selected(self):
        if self.is_busy():
            return
        selected = self._selected_indices()
        if selected:
            for index in reversed(selected):
                del self.input_paths[index]
            self._invalidate()
            self._render_inputs([min(selected[0], len(self.input_paths) - 1)] if self.input_paths else ())

    def clear_inputs(self):
        if not self.is_busy() and self.input_paths:
            self.input_paths.clear()
            self._invalidate()
            self._render_inputs()

    def _set_busy(self, busy):
        if busy:
            self._states = {widget: str(widget.cget("state")) for widget in self._controls}
            for widget in self._controls:
                widget.configure(state="disabled")
        else:
            for widget, state in self._states.items():
                widget.configure(state=state)
            self._states.clear()
        self._refresh_actions()

    def start_conversion(self):
        if self.is_busy():
            return
        try:
            mode, start, padding, conflict, output = self._options()
            if not self.input_paths:
                raise ValueError("변환할 이미지를 추가하세요.")
            # Import only on a deliberate conversion request.  Native engine
            # initialization and all image processing stay outside this UI.
            from m2_image_engine import ConversionJob, convert_images
            job = ConversionJob(input_paths=tuple(self.input_paths), output_dir=output,
                                mode=mode, start_number=start, zero_pad=padding, conflict=conflict)
        except Exception as exc:
            messagebox.showerror("변환 준비", str(exc), parent=self.root)
            return
        self.last_report = None
        self._run_attempted = True
        self.cancel_event.clear()
        self.running = True
        self.progress_bar.configure(value=0)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log(f"모드: {self.mode_var.get()} · {self.spec_var.get()}")
        self._log(f"입력 {len(self.input_paths):,}개 · 시작 {start} · 자리수 {padding} · 정책 {conflict}")
        self._log("출력: " + output)
        self._set_busy(True)
        self.status_var.set("변환 시작 · 현재 표시된 입력 순서를 사용합니다.")
        revision = self._revision
        def work():
            last = [0.0]
            def callback(update):
                now = time.monotonic()
                data = dict(update)
                if now - last[0] >= .1 or data.get("stage") in {"failed", "error", "completed"}:
                    last[0] = now
                    self.events.put(("progress", data))
            try:
                report = convert_images(job, cancel_event=self.cancel_event, progress=callback)
                self.events.put(("done", report, revision))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}", revision))
        self.worker = threading.Thread(target=work, name="m2-converter-worker", daemon=True)
        self.worker.start()

    def stop(self):
        if self.is_busy():
            self.cancel_event.set()
            self.stop_button.configure(state="disabled")
            self.status_var.set("중지 요청 · 현재 변환 단계와 임시 파일 정리가 끝나기를 기다립니다.")

    def _show_report(self, report):
        data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        state = "중지 · 부분 결과" if data.get("cancelled") else ("변환 완료" if data.get("completed") else "실패 · 부분 결과")
        total = data.get("total", len(self.input_paths))
        self._log(f"\n{state} · 전체 {total:,} · 변환 {data.get('converted', 0):,} · 건너뜀 {data.get('skipped', 0):,} · 실패 {data.get('failed', 0):,}")
        if data.get("error"):
            self._log("오류: " + str(data["error"]))
        items = data.get("items") or []
        for item in items[:2000]:
            if not isinstance(item, dict):
                item = vars(item)
            source = item.get("source", item.get("source_path", ""))
            target = item.get("target", item.get("target_path", ""))
            message = item.get("error") or item.get("message") or ""
            self._log(f"[{item.get('status', '')}] {Path(str(source)).name} → {Path(str(target)).name}" + (" · " + str(message) if message else ""))
        if len(items) > 2000:
            self._log("화면에는 앞 2,000개를 표시합니다. 전체 항목은 결과 JSON에서 확인하세요.")
        seconds = data.get("elapsed_seconds", data.get("elapsed", 0))
        self.status_var.set(f"{state} · 변환 {data.get('converted', 0):,}, 건너뜀 {data.get('skipped', 0):,}, 실패 {data.get('failed', 0):,} · {float(seconds):.2f}초")
        if data.get("completed"):
            self.progress_bar.configure(value=100)

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            for _ in range(50):
                event = self.events.get_nowait()
                if event[0] == "progress":
                    if not self.cancel_event.is_set():
                        data = event[1]
                        index, total = data.get("index", 0), data.get("total", 0)
                        if isinstance(index, (int, float)) and isinstance(total, (int, float)) and total > 0:
                            self.progress_bar.configure(value=max(0, min(100, index * 100 / total)))
                        self.status_var.set(data.get("message") or f"{index}/{total} · {data.get('stage', '')}")
                elif event[0] == "done":
                    self.running = False
                    self.worker = None
                    if event[2] == self._revision:
                        self.last_report = event[1]
                        self._show_report(event[1])
                    else:
                        self.last_report = None
                        self._run_attempted = False
                        self.status_var.set("입력이 바뀌어 이전 조건의 결과 저장을 비활성화했습니다. 현재 조건으로 다시 실행하세요.")
                    self._set_busy(False)
                elif event[0] == "error":
                    self.running = False
                    self.worker = None
                    self.last_report = None
                    self._log("실패: " + event[1])
                    self.status_var.set("변환 실패 · " + event[1])
                    self._set_busy(False)
        except queue.Empty:
            pass
        if self._close_requested and not self.is_busy():
            self.close()
            return
        if not self._closed:
            self._poll_id = self.root.after(75, self._poll)

    def save_result(self, path=None):
        if self.is_busy() or self.last_report is None:
            return
        if path is None:
            path = filedialog.asksaveasfilename(parent=self.root, title="M2 변환 결과 JSON 저장",
                defaultextension=".json", initialfile="M2_변환결과.json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                data = self.last_report.to_dict() if hasattr(self.last_report, "to_dict") else dict(self.last_report)
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.status_var.set("JSON 저장 완료: " + str(path))
            except Exception as exc:
                messagebox.showerror("저장 오류", str(exc), parent=self.root)

    def save_log(self, path=None):
        if self.is_busy() or not self._run_attempted:
            return
        if path is None:
            path = filedialog.asksaveasfilename(parent=self.root, title="M2 변환 로그 저장",
                defaultextension=".txt", initialfile="M2_변환로그.txt", filetypes=[("Text", "*.txt")])
        if path:
            try:
                Path(path).write_text(self.log_text.get("1.0", "end-1c"), encoding="utf-8")
                self.status_var.set("로그 저장 완료: " + str(path))
            except Exception as exc:
                messagebox.showerror("저장 오류", str(exc), parent=self.root)

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
            self.status_var.set("현재 변환 단계와 임시 파일 정리를 마친 뒤 종료합니다.")
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
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    M2ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
