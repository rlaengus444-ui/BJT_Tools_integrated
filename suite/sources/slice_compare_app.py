"""Slice Compare — Windows image bundle inspector (all processing stays local)."""
from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
try:
    from display_numbers import format_display_value
except ModuleNotFoundError as exc:
    if exc.name != "display_numbers":
        raise
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from display_numbers import format_display_value
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None

from slice_compare_engine import SourceSpec, CompareOptions, compare_sources


# Style only this tool's widgets; other tabs keep their own theme and settings.
def _compare_button(*args, **kwargs):
    kwargs.setdefault('style', 'SliceCompare.TButton')
    return ttk.Button(*args, **kwargs)

def _compare_frame(*args, **kwargs):
    kwargs.setdefault('style', 'SliceCompare.TFrame')
    return ttk.Frame(*args, **kwargs)

def _compare_notebook(*args, **kwargs):
    kwargs.setdefault('style', 'SliceCompare.TNotebook')
    return ttk.Notebook(*args, **kwargs)

def _compare_treeview(*args, **kwargs):
    kwargs.setdefault('style', 'SliceCompare.Treeview')
    return ttk.Treeview(*args, **kwargs)

BG = '#f3f5f8'
INK = '#172238'
MUTED = '#63738a'
BLUE = '#315efb'
STATUS = {'identical': '일치', 'different': '차이 있음', 'left_only': 'A에만 있음',
          'right_only': 'B에만 있음', 'error': '검사 오류', 'unsupported': '미지원'}
DIFFERENCES = {'format': '실제 파일 형식', 'frame_count': '프레임 / 페이지 수',
    'width': '가로 해상도', 'height': '세로 해상도', 'bit_depth': '채널당 비트 수',
    'channels': '채널 수', 'bits_per_pixel': '픽셀당 비트 수', 'dtype': '픽셀 데이터 타입',
    'mode': '색상 모드', 'palette_sha256': '색상 팔레트', 'orientation': '이미지 방향',
    'color_info': '색상 프로파일 / 색상 해석', 'color_interpretation': '비트 수 / 색상 해석', 'transparency': '투명도', 'photometric': 'TIFF 색상 해석',
    'alpha_type': '알파 채널 해석', 'pixels': '이미지 픽셀', 'file_bytes': '파일 바이트 (압축·메타데이터 등 포함)',
    'extension': '파일 확장자', 'missing_left': 'A 파일 누락', 'missing_right': 'B 파일 누락'}


def difference_label(value):
    if ':' in value:
        prefix, code = value.split(':', 1)
        if prefix.startswith('frame_'):
            return f'{prefix[6:]}번째 프레임: {DIFFERENCES.get(code, code)}'
    return DIFFERENCES.get(value, value)


def display_value(value):
    if value is None:
        return '—'
    return format_display_value(value)


def file_size(value):
    try:
        size = int(value)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024 or unit == 'GB':
                return f'{size:,.0f} {unit}' if unit == 'B' else f'{size:,.1f} {unit}'
            size /= 1024
    except (ValueError, TypeError):
        pass
    return '—'


class SourcePanel(tk.Frame):
    def __init__(self, master, app, letter, title):
        super().__init__(master, bg='white', highlightthickness=1, highlightbackground='#dbe1ea')
        self.app, self.paths, self.locked = app, [], False
        heading = tk.Frame(self, bg='white')
        heading.pack(fill='x', padx=20, pady=(12, 8))
        tk.Label(heading, text=f' {letter} ', font=('Malgun Gothic', 12, 'bold'), bg='#eaf0ff', fg=BLUE).pack(side='left')
        tk.Label(heading, text=title, font=('Malgun Gothic', 12, 'bold'), fg=INK, bg='white').pack(side='left', padx=10)
        self.clear_btn = _compare_button(heading, text='비우기', width=7, command=self.clear)
        self.clear_btn.pack(side='right')
        self.drop = tk.Frame(self, bg='#f8faff', highlightbackground='#c8d4ef', highlightthickness=1, cursor='hand2')
        self.drop.pack(fill='both', expand=True, padx=20, pady=(0, 12))
        self.caption = tk.Label(self.drop, text='폴더 · ZIP · 이미지 파일을 여기에 놓으세요', bg='#f8faff', fg=INK,
                                font=('Malgun Gothic', 11, 'bold'), wraplength=440)
        self.caption.pack(padx=12, pady=(12, 5))
        self.description = tk.Label(self.drop, text='PNG / TIFF / JPEG  ·  여러 파일 선택 가능', bg='#f8faff', fg=MUTED,
                                    font=('Malgun Gothic', 9), wraplength=440)
        self.description.pack(padx=12, pady=(0, 12))
        for widget in (self.drop, self.caption, self.description):
            widget.bind('<Button-1>', lambda e: self.choose_files())
            if TkinterDnD:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind('<<Drop>>', self.on_drop)
                widget.dnd_bind('<<DropEnter>>', self.on_enter)
                widget.dnd_bind('<<DropLeave>>', lambda e: self.drop.configure(bg='#f8faff'))
        buttons = tk.Frame(self, bg='white')
        buttons.pack(fill='x', padx=20, pady=(0, 12))
        self.folder_btn = _compare_button(buttons, text='폴더 선택', command=self.choose_folder)
        self.folder_btn.pack(side='left')
        self.files_btn = _compare_button(buttons, text='파일 / ZIP 선택', command=self.choose_files)
        self.files_btn.pack(side='left', padx=8)

    def set_locked(self, value):
        self.locked = value
        for btn in (self.clear_btn, self.folder_btn, self.files_btn):
            btn.configure(state='disabled' if value else 'normal')

    def clear(self):
        if self.locked:
            return
        self.paths = []
        self.caption.configure(text='폴더 · ZIP · 이미지 파일을 여기에 놓으세요')
        self.description.configure(text='PNG / TIFF / JPEG  ·  여러 파일 선택 가능')
        self.app.invalidate()

    def set_paths(self, paths):
        if self.locked or not paths:
            return
        normalized = list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
        invalid = [p for p in normalized if not Path(p).exists()]
        if invalid:
            messagebox.showerror('경로 확인', f'다음 경로가 없습니다.\n{invalid[0]}', parent=self.app.root)
            return
        self.paths = normalized
        first = Path(normalized[0])
        self.caption.configure(text=first.name if len(normalized) == 1 else f'이미지 파일 {len(normalized):,}개 선택됨')
        self.description.configure(text=str(first) if len(normalized) == 1 else f'{first.name} 외 {len(normalized)-1:,}개\n새로 놓으면 선택이 교체됩니다.')
        self.app.invalidate()

    def on_drop(self, event):
        self.drop.configure(bg='#f8faff')
        if not self.locked:
            self.set_paths(self.tk.splitlist(event.data))
        return 'copy'

    def on_enter(self, event):
        if self.locked:
            return 'refuse_drop'
        self.drop.configure(bg='#eaf0ff')
        return 'copy'

    def choose_files(self):
        if not self.locked:
            self.set_paths(filedialog.askopenfilenames(parent=self.app.root, title='이미지 또는 ZIP 선택',
                filetypes=[('이미지와 ZIP', '*.png *.tif *.tiff *.jpg *.jpeg *.jpe *.zip'), ('모든 파일', '*.*')]))

    def choose_folder(self):
        if not self.locked:
            value = filedialog.askdirectory(parent=self.app.root, title='슬라이싱 이미지 폴더 선택')
            if value:
                self.set_paths([value])


class Application:
    def __init__(self, root):
        self.root = root
        self.root.title('Slice Compare Fast · 슬라이싱 파일 일치성 검사')
        width = min(1280, max(1020, self.root.winfo_screenwidth() - 60))
        height = min(930, max(760, self.root.winfo_screenheight() - 110))
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(1020, 760)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.latest_progress = None
        self.started_at = 0.0
        self._render_token = 0
        self._render_job = None
        self._search_job = None
        self.report = None
        self.rows = []
        self.visible_rows = {}
        self.preview_refs = []
        self.preview_metas = None
        self.preview_token = 0
        self.preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='slice-preview')
        self.preview_future = None
        self.preview_resize_job = None
        self.mode = tk.StringVar(value='상대 경로 / 파일명')
        self.verify_identical = tk.BooleanVar(value=False)
        self.filter = tk.StringVar(value='전체')
        self.search = tk.StringVar()
        self._style()
        self._build()
        self.root.after(100, self.poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.configure('SliceCompare.TLabel', font=('Malgun Gothic', 10))
        style.configure('SliceCompare.TFrame', background=BG)
        style.configure('SliceCompare.TButton', padding=(12, 6), background='white', foreground=INK)
        # Windows native buttons can ignore background colors; their label
        # must stay readable on the native light surface without changing the
        # shared theme used by the suite's other tools.
        accent_foreground = INK if style.theme_use() in {'vista', 'xpnative', 'winnative'} else 'white'
        style.configure('SliceCompare.Accent.TButton', background=BLUE, foreground=accent_foreground, padding=(22, 9), font=('Malgun Gothic', 11, 'bold'))
        style.map('SliceCompare.Accent.TButton',
                  background=[('disabled', '#9baae4'), ('active', '#2449ce')],
                  foreground=[('disabled', '#536178'), ('!disabled', accent_foreground)])
        style.configure('SliceCompare.Treeview', rowheight=30, background='white', fieldbackground='white', foreground=INK, borderwidth=0)
        style.configure('SliceCompare.Treeview.Heading', font=('Malgun Gothic', 9, 'bold'), padding=(6, 8), background='#e9edf4', foreground=INK)
        style.map('SliceCompare.Treeview', background=[('selected', '#dce6ff')], foreground=[('selected', INK)])
        style.configure('SliceCompare.TNotebook', background=BG, borderwidth=0)
        style.configure('SliceCompare.TNotebook.Tab', padding=(14, 6))

    def _build(self):
        header = tk.Frame(self.root, bg=INK)
        header.pack(fill='x')
        tk.Label(header, text='SLICE / COMPARE', font=('Segoe UI', 17, 'bold'), fg='white', bg=INK).pack(side='left', padx=26, pady=16)
        tk.Label(header, text='슬라이싱 이미지 묶음 일치성 검사', font=('Malgun Gothic', 11), fg='#c5d2e6', bg=INK).pack(side='left', padx=8)
        tk.Label(header, text='LOCAL  ·  파일 업로드 없음', font=('Malgun Gothic', 9), fg='#9de2c2', bg=INK).pack(side='right', padx=26)
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill='both', expand=True, padx=24, pady=(16, 12))
        sources = tk.Frame(body, bg=BG)
        sources.pack(fill='x')
        sources.columnconfigure((0, 1), weight=1, uniform='source')
        self.left = SourcePanel(sources, self, 'A', '기준 파일 묶음')
        self.right = SourcePanel(sources, self, 'B', '비교 파일 묶음')
        self.left.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        self.right.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill='x', pady=(13, 5))
        tk.Label(controls, text='짝짓기 기준', bg=BG, fg=INK, font=('Malgun Gothic', 10, 'bold')).pack(side='left', padx=(0, 10))
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode, values=['상대 경로 / 파일명', '레이어 순서 (숫자 정렬)'], state='readonly', width=25)
        self.mode_box.pack(side='left')
        self.mode_box.bind('<<ComboboxSelected>>', lambda e: self.invalidate())
        self.deep_check = ttk.Checkbutton(controls, text='정밀: 같은 파일도 전체 픽셀 해독',
            variable=self.verify_identical, command=self.invalidate)
        self.deep_check.pack(side='left', padx=14)
        self.run_btn = _compare_button(controls, text='일치성 검사', style='SliceCompare.Accent.TButton', command=self.start)
        self.run_btn.pack(side='right')
        self.cancel_btn = _compare_button(controls, text='중지', command=self.cancel, state='disabled')
        self.cancel_btn.pack(side='right', padx=8)
        tk.Label(body, text='경로 모드: 하위 폴더와 파일명이 같은 항목끼리 비교  /  순서 모드: 각 묶음을 숫자 순으로 정렬하여 비교',
                 bg=BG, fg=MUTED, font=('Malgun Gothic', 9), anchor='w').pack(fill='x', pady=(0, 7))
        self.progress = ttk.Progressbar(body, mode='determinate', maximum=100)
        self.progress.pack(fill='x')
        self.message = tk.StringVar(value='두 묶음을 선택하면 검사를 시작할 수 있습니다.')
        tk.Label(body, textvariable=self.message, anchor='w', bg=BG, fg=MUTED, font=('Malgun Gothic', 9)).pack(fill='x', pady=(5, 8))
        self.verdict = tk.Label(body, text='검사 대기', anchor='w', padx=14, pady=9, bg='#e9edf4', fg=INK, font=('Malgun Gothic', 11, 'bold'))
        self.verdict.pack(fill='x')
        self.count_text = tk.StringVar(value='이미지 · 해상도 · 비트 수 · 실제 형식 · 프레임 수 · 파일 해시를 확인합니다.')
        tk.Label(body, textvariable=self.count_text, bg=BG, fg=MUTED, anchor='w', font=('Malgun Gothic', 9)).pack(fill='x', pady=(6, 8))
        tools = tk.Frame(body, bg=BG)
        tools.pack(fill='x', pady=(0, 8))
        self.filter_box = ttk.Combobox(tools, values=['전체', '차이 / 누락', '오류 / 미지원', '일치'], textvariable=self.filter, state='readonly', width=15)
        self.filter_box.pack(side='left')
        self.filter_box.bind('<<ComboboxSelected>>', lambda e: self.render_rows())
        ttk.Entry(tools, textvariable=self.search, width=27).pack(side='left', padx=8)
        self.search.trace_add('write', self.schedule_search)
        tk.Label(tools, text='파일명 검색', bg=BG, fg=MUTED, font=('Malgun Gothic', 9)).pack(side='left')
        self.export_json = _compare_button(tools, text='JSON 저장', command=lambda: self.export('json'), state='disabled')
        self.export_json.pack(side='right')
        self.export_csv = _compare_button(tools, text='CSV 저장', command=lambda: self.export('csv'), state='disabled')
        self.export_csv.pack(side='right', padx=8)
        split = ttk.Panedwindow(body, orient='vertical')
        split.pack(fill='both', expand=True)
        tableframe = _compare_frame(split)
        split.add(tableframe, weight=3)
        columns = ('left', 'right', 'status', 'pixels', 'resolution', 'depth', 'format', 'bytes')
        self.table = _compare_treeview(tableframe, columns=columns, show='headings', selectmode='browse', height=8)
        headings = ['A 파일', 'B 파일', '종합 판정', '픽셀 일치', '해상도 A ↔ B', '비트/채널 A ↔ B', '실제 형식 A ↔ B', '바이트 일치']
        for col, title, width in zip(columns, headings, (185, 185, 100, 80, 145, 135, 145, 95)):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, minwidth=65, stretch=col in ('left', 'right'))
        yscroll = ttk.Scrollbar(tableframe, orient='vertical', command=self.table.yview)
        xscroll = ttk.Scrollbar(tableframe, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        yscroll.grid(row=0, column=1, sticky='ns')
        xscroll.grid(row=1, column=0, sticky='ew')
        tableframe.columnconfigure(0, weight=1)
        tableframe.rowconfigure(0, weight=1)
        self.table.tag_configure('different', foreground='#ad550b')
        self.table.tag_configure('error', foreground='#bf3346')
        self.table.tag_configure('unsupported', foreground='#846524')
        self.table.tag_configure('left_only', foreground='#ad550b')
        self.table.tag_configure('right_only', foreground='#ad550b')
        self.table.tag_configure('identical', foreground='#14754c')
        self.table.bind('<<TreeviewSelect>>', self.selected)
        notebook = _compare_notebook(split)
        self.notebook = notebook
        split.add(notebook, weight=2)
        detail_frame = _compare_frame(notebook)
        notebook.add(detail_frame, text='검사 상세')
        self.details = tk.Text(detail_frame, height=9, wrap='word', bg='white', fg=INK, font=('Malgun Gothic', 9), relief='flat', padx=12, pady=10)
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(side='left', fill='both', expand=True)
        detail_scroll.pack(side='right', fill='y')
        self._set_detail('목록에서 파일을 선택하면 차이와 상세 메타데이터를 확인할 수 있습니다.\n\n종합 일치: 픽셀·속성과 파일 바이트까지 모두 같음.\n픽셀 일치: 원본 샘플과 색상 해석이 같음.\n바이트 일치: 파일 전체 SHA-256이 같음. 픽셀이 같아도 압축·메타데이터에 따라 바이트는 다를 수 있습니다.')
        previewframe = _compare_frame(notebook)
        self.preview_frame = previewframe
        notebook.add(previewframe, text='이미지 미리보기')
        notebook.bind('<<NotebookTabChanged>>', lambda e: self.load_selected_preview())
        tk.Label(previewframe, text='미리보기는 첫 프레임을 화면용 8비트로 표시합니다. 실제 검사는 원본 비트 수와 전체 프레임을 사용합니다.', bg=BG, fg=MUTED, font=('Malgun Gothic', 9)).pack(fill='x', pady=4)
        self.preview_area = tk.Frame(previewframe, bg=BG)
        self.preview_area.pack(fill='both', expand=True)
        self.preview_area.bind('<Configure>', self.preview_resized)
        self.preview_labels = []
        for title in ('A · 기준', 'B · 비교'):
            panel = tk.Frame(self.preview_area, bg='#e4e9f1')
            panel.pack(side='left', fill='both', expand=True, padx=4, pady=2)
            label = tk.Label(panel, text=title + '\n파일을 선택하세요', bg='#e4e9f1', fg=MUTED)
            label.pack(fill='both', expand=True)
            self.preview_labels.append(label)
        self.preview_notice = tk.StringVar(value='빠른 검사: 파일 해시가 같으면 일치를 확정하고, 다른 파일만 원본 픽셀을 해독합니다. 원본은 수정하지 않습니다.')
        tk.Label(body, textvariable=self.preview_notice, bg=BG, fg=MUTED, font=('Malgun Gothic', 9), anchor='w').pack(fill='x', pady=(9, 0))

    def _set_detail(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def invalidate(self):
        if not self.running:
            self.report = None
            self.rows = []
            self.render_rows()
            self.export_csv.configure(state='disabled')
            self.export_json.configure(state='disabled')
            self.verdict.configure(text='검사 대기 · 입력이 변경되었습니다', bg='#e9edf4', fg=INK)
            self.progress['value'] = 0
            self.count_text.set('두 묶음을 선택한 후 일치성 검사를 눌러 주세요.')
            self.message.set('폴더 / ZIP 1개 또는 이미지 여러 개를 각 영역에 놓으세요.')
            self._set_detail('입력이 변경되었습니다. 새 검사를 실행하세요.')
            self.preview_refs.clear()
            self.preview_metas = None
            self.preview_token += 1
            for label in self.preview_labels:
                label.configure(image='', text='파일을 선택하세요')

    def start(self):
        if self.running:
            return
        if not self.left.paths or not self.right.paths:
            messagebox.showinfo('입력 필요', 'A와 B 파일 묶음을 모두 선택해 주세요.', parent=self.root)
            return
        try:
            left = SourceSpec.from_paths(self.left.paths)
            right = SourceSpec.from_paths(self.right.paths)
            options = CompareOptions(match_mode='sequence' if self.mode.get().startswith('레이어') else 'path',
                                     verify_identical=self.verify_identical.get())
        except Exception as exc:
            messagebox.showerror('입력 확인', str(exc), parent=self.root)
            return
        self.invalidate()
        self.running = True
        self.started_at = time.monotonic()
        self.latest_progress = None
        self.cancel_event.clear()
        self.left.set_locked(True)
        self.right.set_locked(True)
        self.mode_box.configure(state='disabled')
        self.deep_check.configure(state='disabled')
        self.run_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.verdict.configure(text='검사 중 · 원본 이미지를 비교하고 있습니다', bg='#eaf0ff', fg=BLUE)
        self.message.set('파일 목록을 읽는 중…')
        def work():
            try:
                report = compare_sources(left, right, options,
                    progress=self.receive_progress,
                    cancel_event=self.cancel_event)
                self.events.put(('done', report))
            except Exception as exc:
                self.events.put(('error', str(exc), traceback.format_exc()))
        self.worker = threading.Thread(target=work, daemon=True, name='slice-comparison')
        self.worker.start()

    def receive_progress(self, percent, message):
        # Coalesce progress. Thousands of fast files must not delay the done event.
        self.latest_progress = (percent, message)

    def cancel(self):
        self.cancel_event.set()
        self.cancel_btn.configure(state='disabled')
        self.message.set('중지 요청됨 · 현재 디코딩 작업이 끝나면 중지합니다.')

    def poll(self):
        progress, self.latest_progress = self.latest_progress, None
        if progress and self.running:
            self.progress['value'] = progress[0]
            elapsed = time.monotonic() - self.started_at
            self.message.set(f'{progress[1]}  ·  경과 {elapsed:,.1f}초')
        try:
            # Bound queue processing so long scans cannot starve UI input.
            for _ in range(100):
                event = self.events.get_nowait()
                if event[0] == 'progress':
                    self.progress['value'] = event[1]
                    self.message.set(event[2])
                elif event[0] == 'done':
                    self.finish(event[1])
                elif event[0] == 'error':
                    self.unlock()
                    self.verdict.configure(text='검사 실패 · 입력 파일을 확인해 주세요', bg='#fff0f2', fg='#bf3346')
                    self.message.set(event[1])
                    self._set_detail(event[2])
                elif event[0] == 'preview' and event[1] == self.preview_token:
                    self.preview_refs.clear()
                    for label, (image, error) in zip(self.preview_labels, event[2]):
                        if image is not None:
                            photo = ImageTk.PhotoImage(image)
                            self.preview_refs.append(photo)
                            label.configure(image=photo, text='')
                        else:
                            label.configure(image='', text=error or '미리보기 없음')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def unlock(self):
        self.running = False
        self.left.set_locked(False)
        self.right.set_locked(False)
        self.mode_box.configure(state='readonly')
        self.deep_check.configure(state='normal')
        self.run_btn.configure(state='normal')
        self.cancel_btn.configure(state='disabled')

    def finish(self, report):
        self.unlock()
        self.report = report
        # Metadata is immutable after completion; avoid deep-copying all frames.
        self.rows = [vars(row).copy() if hasattr(row, '__dict__') else dict(row) for row in report.rows]
        summary = report.summary
        counts = {status: sum(r.get('status') == status for r in self.rows) for status in STATUS}
        issues = counts['error'] + counts['unsupported']
        changes = counts['different'] + counts['left_only'] + counts['right_only']
        byte_only = sum(r.get('status') == 'different' and set(r.get('differences', [])) <= {'file_bytes'} for r in self.rows)
        pixel_matches = sum(r.get('pixels_equal') is True for r in self.rows)
        cancelled = bool(report.cancelled)
        source_issues = getattr(report, 'errors', [])
        success = (bool(summary.get('overall_identical', False)) and not cancelled
                   and bool(self.rows) and not issues and not source_issues and not changes)
        if cancelled:
            text, bg, fg = '검사 중지 · 아래 결과는 완료된 항목만 포함합니다', '#fff5e7', '#ad550b'
        elif success:
            text, bg, fg = '완전 일치 · 이미지·속성·파일 바이트가 모두 같습니다', '#e8f6ee', '#14754c'
        elif issues or source_issues or not self.rows:
            text, bg, fg = '확인 필요 · 차이 또는 검사할 수 없는 항목이 있습니다', '#fff0f2', '#bf3346'
        elif byte_only and byte_only == changes:
            text, bg, fg = '픽셀·속성 일치 · 파일 바이트에 차이가 있습니다', '#eaf0ff', BLUE
        else:
            text, bg, fg = '불일치 · 차이가 있는 항목을 확인해 주세요', '#fff5e7', '#ad550b'
        self.verdict.configure(text=text, bg=bg, fg=fg)
        self.count_text.set(f"총 {len(self.rows):,}쌍/항목  ·  완전 일치 {counts['identical']:,}  ·  차이 {counts['different']:,}  ·  A만 {counts['left_only']:,}  ·  B만 {counts['right_only']:,}  ·  오류 {counts['error']:,}  ·  미지원 {counts['unsupported']:,}  |  픽셀 일치 {pixel_matches:,}")
        if not cancelled:
            self.progress['value'] = 100
        elapsed = time.monotonic() - self.started_at if self.started_at else 0.0
        mode_text = '정밀' if self.verify_identical.get() else '빠른'
        self.message.set(f'검사 중지 · {elapsed:,.1f}초 · 완료된 항목만 저장됩니다.' if cancelled else
                         f'{mode_text} 검사 완료 · {elapsed:,.1f}초 · 행을 선택하면 상세 내용을 볼 수 있습니다.')
        self.export_csv.configure(state='normal')
        self.export_json.configure(state='normal')
        self.render_rows()
        if self.table.get_children():
            first = self.table.get_children()[0]
            self.table.selection_set(first)
        else:
            self._set_detail('현재 검색 / 필터에 맞는 결과가 없습니다.\n\n' +
                             format_display_value(summary) + '\n\n' +
                             '\n'.join(getattr(report, 'warnings', [])))

    @staticmethod
    def _resolution(meta):
        return f"{meta['width']}×{meta['height']}" if meta and meta.get('width') is not None else '—'

    @staticmethod
    def _pair(a, b):
        return a if a == b else f'{a} ↔ {b}'

    def schedule_search(self, *_args):
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(180, self._search_now)

    def _search_now(self):
        self._search_job = None
        self.render_rows()

    def render_rows(self):
        if not hasattr(self, 'table'):
            return
        self._render_token += 1
        token = self._render_token
        if self._render_job:
            self.root.after_cancel(self._render_job)
            self._render_job = None
        self.table.delete(*self.table.get_children())
        self.visible_rows = {}
        needle = self.search.get().casefold()
        selected_filter = self.filter.get()
        matches = []
        for index, row in enumerate(self.rows):
            status = row.get('status', 'error')
            if selected_filter == '차이 / 누락' and status not in ('different', 'left_only', 'right_only'):
                continue
            if selected_filter == '오류 / 미지원' and status not in ('error', 'unsupported'):
                continue
            if selected_filter == '일치' and status != 'identical':
                continue
            names = f"{row.get('left_name', '')} {row.get('right_name', '')}"
            if needle and needle not in names.casefold():
                continue
            matches.append((index, row))

        def insert_batch(offset=0):
            if token != self._render_token:
                return
            self._render_job = None
            for index, row in matches[offset:offset + 250]:
                self._insert_row(index, row)
            if offset + 250 < len(matches):
                self._render_job = self.root.after(1, lambda: insert_batch(offset + 250))
        insert_batch()

    def _insert_row(self, index, row):
        status = row.get('status', 'error')
        a, b = row.get('left') or {}, row.get('right') or {}
        hashes = bool(a.get('sha256')) and bool(b.get('sha256'))
        values = (row.get('left_name') or '—', row.get('right_name') or '—', STATUS.get(status, status),
                  '같음' if row.get('pixels_equal') is True else ('다름' if row.get('pixels_equal') is False else '—'),
                  self._pair(self._resolution(a), self._resolution(b)),
                  self._pair(display_value(a.get('bit_depth')), display_value(b.get('bit_depth'))),
                  self._pair(a.get('format', '—'), b.get('format', '—')),
                  ('같음' if a['sha256'] == b['sha256'] else '다름') if hashes else '—')
        iid = str(index)
        self.visible_rows[iid] = row
        self.table.insert('', 'end', iid=iid, values=values, tags=(status,))

    def selected(self, _event=None):
        selection = self.table.selection()
        if not selection:
            return
        row = self.visible_rows.get(selection[0])
        if not row:
            return
        text = [f"판정: {STATUS.get(row.get('status'), row.get('status'))}",
                f"A: {row.get('left_name') or '—'}", f"B: {row.get('right_name') or '—'}", '']
        if row.get('differences'):
            text += ['발견된 차이:'] + [f'  • {difference_label(value)}' for value in row['differences']] + ['']
        if row.get('error'):
            text += ['오류 / 확인 사항:', display_value(row['error']), '']
        if row.get('pixel_comparison') == 'identical_file_bytes':
            text += ['검사 방식: 파일 전체 바이트가 같아 픽셀 일치를 확인했습니다. 전체 픽셀 해독은 생략했습니다.', '']
        elif any((row.get(side) or {}).get('decode_validation') == 'header_only' for side in ('left', 'right')):
            text += ['검사 방식: 파일 헤더와 구조를 확인했습니다. 전체 픽셀 손상 검사는 정밀 모드에서 실행할 수 있습니다.', '']
        fields = [('format', '실제 파일 형식'), ('width', '가로 픽셀'), ('height', '세로 픽셀'),
                  ('bit_depth', '채널당 비트 수'), ('channels', '채널 수'), ('bits_per_pixel', '픽셀당 비트 수'),
                  ('mode', '색상 모드'), ('frame_count', '프레임 / 페이지 수'), ('byte_size', '파일 크기 (바이트)'),
                  ('dpi', 'DPI (참고)'), ('sha256', '파일 SHA-256')]
        a, b = row.get('left') or {}, row.get('right') or {}
        for title, meta in [('A', a), ('B', b)]:
            if meta and meta.get('extension_matches') is False:
                text += [f"주의: {title}의 확장자 {meta.get('extension')}와 실제 형식 {meta.get('format')}이 다릅니다.", '']
        for key, label in fields:
            text.append(f'{label}    A: {display_value(a.get(key))}    |    B: {display_value(b.get(key))}')
        text += ['', '전체 검사 데이터 (화면 표시: 소수 둘째 자리):', format_display_value(row)]
        if self.report and getattr(self.report, 'warnings', None):
            text += ['', '묶음 검사 참고:'] + list(self.report.warnings)
        self._set_detail('\n'.join(text))
        self.preview_metas = (a, b)
        self.preview_token += 1
        self.load_selected_preview()

    def load_selected_preview(self):
        if self.preview_metas and self.notebook.select() == str(self.preview_frame):
            self.update_previews(*self.preview_metas)

    def preview_resized(self, event):
        if self.preview_resize_job:
            self.root.after_cancel(self.preview_resize_job)
        self.preview_resize_job = self.root.after(180, self._preview_after_resize)

    def _preview_after_resize(self):
        self.preview_resize_job = None
        self.load_selected_preview()

    def update_previews(self, *metas):
        self.preview_token += 1
        token = self.preview_token
        if self.preview_future:
            self.preview_future.cancel()
        for label in self.preview_labels:
            label.configure(image='', text='미리보기를 읽는 중…')
        size = (max(80, min(600, self.preview_area.winfo_width() // 2 - 24)),
                max(40, min(320, self.preview_area.winfo_height() - 12)))
        self.preview_future = self.preview_pool.submit(self._decode_previews, metas, token, size)

    def _decode_previews(self, metas, token, size):
        results = []
        for meta in metas:
            path = meta.get('source_path')
            if not path:
                results.append((None, '미리보기 없음'))
                continue
            try:
                member = meta.get('archive_member')
                if member:
                    with zipfile.ZipFile(path) as archive:
                        info = archive.getinfo(member)
                        if info.file_size > 128 * 1024 * 1024:
                            raise ValueError('큰 파일은 미리보기를 생략합니다.')
                        source = io.BytesIO(archive.read(info))
                else:
                    source = path
                with Image.open(source) as image:
                    if image.width * image.height > 100_000_000:
                        raise ValueError('큰 이미지는 미리보기를 생략합니다.')
                    image.seek(0)
                    if image.getexif().get(274, 1) != 1:
                        image = ImageOps.exif_transpose(image)
                    image.thumbnail(size, Image.Resampling.LANCZOS)
                    if image.mode in ('I;16', 'I;16B', 'I;16L', 'I', 'F'):
                        import numpy as np
                        array = np.asarray(image, dtype='float64')
                        low, high = float(array.min()), float(array.max())
                        array = ((array - low) * (255 / (high - low)) if high > low else array * 0).clip(0, 255).astype('uint8')
                        image = Image.fromarray(array)
                    image = image.convert('RGB')
                results.append((image, None))
            except Exception as exc:
                results.append((None, f'미리보기 생략\n{str(exc)[:100]}'))
        self.events.put(('preview', token, results))

    def export(self, kind):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title='검사 결과 저장',
            defaultextension=f'.{kind}', initialfile=f'slice_compare_report.{kind}',
            filetypes=[(kind.upper(), f'*.{kind}')])
        if path:
            try:
                getattr(self.report, f'save_{kind}')(path)
                self.message.set(f'결과 저장 완료: {path}')
            except Exception as exc:
                messagebox.showerror('저장 실패', str(exc), parent=self.root)

    def close(self):
        self.cancel_event.set()
        self.preview_pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    app = Application(root)
    if len(sys.argv) == 3:
        app.left.set_paths([sys.argv[1]])
        app.right.set_paths([sys.argv[2]])
    if not TkinterDnD:
        app.preview_notice.set('드래그앤드롭 모듈이 없습니다. pip install tkinterdnd2로 설치하세요. 파일 선택 버튼은 사용할 수 있습니다.')
    root.mainloop()


if __name__ == '__main__':
    main()
