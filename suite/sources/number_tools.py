"""Numbered raw-file duplication and filename-only missing-number inspection.

No image decoder is imported. These helpers preserve source bytes and operate
independently of the suite's image consistency checker.
"""
from __future__ import annotations

import json
import numbers
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

MAX_REPORT_ITEMS = 10_000
EXTENSION_CHOICES = ('(전체)', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp')


@dataclass(frozen=True)
class DuplicateJob:
    source_path: str
    min_number: int
    max_number: int
    output_dir: str | None = None
    conflict: str = 'skip'
    zero_pad: int = 0


@dataclass
class DuplicateItem:
    number: int
    path: str
    status: str
    message: str = ''


@dataclass
class DuplicateReport:
    source_path: str
    output_dir: str
    min_number: int
    max_number: int
    requested_count: int
    processed_count: int = 0
    copied_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    cancelled: bool = False
    completed: bool = False
    items: list[DuplicateItem] = field(default_factory=list)
    details_truncated: bool = False
    elapsed_seconds: float = 0.0

    @property
    def written(self):
        return [item for item in self.items if item.status == 'copied']

    @property
    def skipped(self):
        return [item for item in self.items if item.status in ('skipped', 'skipped_source')]

    @property
    def errors(self):
        return [item for item in self.items if item.status == 'error']

    def to_dict(self):
        return {'kind': 'duplicate', **asdict(self)}

    def _record(self, number, path, status, message=''):
        self.processed_count += 1
        if status == 'copied':
            self.copied_count += 1
        elif status == 'error':
            self.error_count += 1
        else:
            self.skipped_count += 1
        if len(self.items) < MAX_REPORT_ITEMS:
            self.items.append(DuplicateItem(number, str(path), status, message))
        else:
            self.details_truncated = True
            if status == 'error':
                # The first write error stops the job and must remain visible
                # even after the ordinary detail budget has been exhausted.
                self.items[-1] = DuplicateItem(number, str(path), status, message)


@dataclass
class MissingReport:
    folder: str
    extensions: list[str]
    min_number: int | None
    max_number: int | None
    scanned_count: int = 0
    numbered_count: int = 0
    unique_count: int = 0
    duplicates: dict[int, list[str]] = field(default_factory=dict)
    out_of_range: dict[int, list[str]] = field(default_factory=dict)
    unnumbered: list[str] = field(default_factory=list)
    missing_count: int | None = None
    missing_ranges: list[tuple[int, int]] = field(default_factory=list)
    missing_checked: bool = False
    cancelled: bool = False
    completed: bool = False
    errors: list[str] = field(default_factory=list)
    skipped_links: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self):
        return {'kind': 'missing_numbers', **asdict(self)}


def _cancelled(event):
    return event is not None and event.is_set()


def _notify(callback, percent, message):
    if callback is not None:
        callback(int(percent), str(message))


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(label + '은 정수여야 합니다.')
    return int(value)


def _range(min_number, max_number, *, optional=False):
    if optional and min_number is None and max_number is None:
        return None, None
    if min_number is None or max_number is None:
        raise ValueError('Min과 Max를 모두 입력하거나 모두 비워두세요.')
    low, high = _integer(min_number, 'Min'), _integer(max_number, 'Max')
    if low > high:
        raise ValueError('Min은 Max보다 클 수 없습니다.')
    return low, high


def _link(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _signature(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _exists(path):
    return os.path.lexists(path)


def _same_file(source, destination):
    try:
        return os.path.samefile(source, destination)
    except (FileNotFoundError, OSError):
        return os.path.normcase(str(source.resolve())) == os.path.normcase(str(destination.resolve()))


def _validate_job(job):
    if not isinstance(job, DuplicateJob):
        raise TypeError('job은 DuplicateJob이어야 합니다.')
    low, high = _range(job.min_number, job.max_number)
    if job.conflict not in ('skip', 'overwrite', 'rename'):
        raise ValueError('충돌 처리는 skip, overwrite, rename 중 하나여야 합니다.')
    padding = _integer(job.zero_pad, '자릿수')
    if not 0 <= padding <= 32:
        raise ValueError('자릿수는 0~32여야 합니다.')
    source = Path(job.source_path).expanduser().absolute()
    if _link(source):
        raise ValueError('링크 파일을 원본으로 사용할 수 없습니다.')
    if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError('원본은 읽을 수 있는 일반 파일이어야 합니다.')
    output = Path(job.output_dir).expanduser().absolute() if job.output_dir else source.parent
    if _link(output):
        raise ValueError('링크 또는 연결점 폴더를 출력 폴더로 사용할 수 없습니다.')
    if output.exists() and not output.is_dir():
        raise ValueError('출력 경로가 폴더가 아닙니다.')
    return source, output, low, high, padding


def _copy_atomic(source, destination, source_signature, conflict):
    """Copy into the destination filesystem, then publish a complete file."""
    temporary = None
    descriptor = None
    try:
        if _link(source) or _signature(source) != source_signature:
            raise RuntimeError('복제 도중 원본 파일이 변경되었습니다.')
        descriptor, name = tempfile.mkstemp(prefix='.number-copy-', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        with source.open('rb') as reader, os.fdopen(descriptor, 'wb') as writer:
            descriptor = None
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, temporary, follow_symlinks=False)
        if _link(source) or _signature(source) != source_signature:
            raise RuntimeError('복제 도중 원본 파일이 변경되어 현재 출력을 저장하지 않았습니다.')
        if conflict == 'overwrite':
            if _exists(destination) and (_link(destination) or destination.is_dir()):
                raise ValueError('링크 또는 폴더는 덮어쓰지 않습니다.')
            if _same_file(source, destination):
                raise ValueError('원본 파일에는 덮어쓸 수 없습니다.')
            os.replace(temporary, destination)
        else:
            # Windows rename() never replaces an existing path and removes
            # the temporary name even for read-only source metadata. POSIX
            # link() provides the corresponding atomic no-overwrite operation.
            if os.name == 'nt':
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                # copystat may have copied the Windows read-only attribute.
                # At this point an extant temporary name was not published by
                # Windows rename(), so clearing it cannot alter an output.
                if os.name == 'nt':
                    temporary.chmod(temporary.stat().st_mode | stat.S_IWRITE)
                    temporary.unlink(missing_ok=True)
                else:
                    raise


def duplicate_files(job, cancel_event=None, progress=None):
    """Copy number+suffix files inclusively; cancel between complete files.

    The first write failure stops the job. Existing destination data survives
    failed atomic overwrite attempts. Metadata is preserved with copystat;
    security descriptors and filesystem-specific metadata follow Python's
    copy2/copystat support. Detail rows are capped; all counters remain exact.
    """
    started = time.monotonic()
    source, output, low, high, padding = _validate_job(job)
    report = DuplicateReport(str(source), str(output), low, high, high - low + 1)
    _notify(progress, 0, '복제를 준비합니다.')
    if _cancelled(cancel_event):
        report.cancelled = True
        report.elapsed_seconds = time.monotonic() - started
        return report
    output.mkdir(parents=True, exist_ok=True)
    if _link(output):
        raise ValueError('링크 출력 폴더는 사용하지 않습니다.')
    source_signature = _signature(source)
    for number in range(low, high + 1):
        if _cancelled(cancel_event):
            report.cancelled = True
            break
        stem = f'{number:0{padding}d}' if padding else str(number)
        base = output / (stem + source.suffix)
        target = base
        try:
            if _link(source) or _signature(source) != source_signature:
                raise RuntimeError('복제 도중 원본 파일이 변경되었습니다.')
            if job.conflict != 'rename' and _same_file(source, target):
                report._record(number, target, 'skipped_source', '원본 파일 보호')
            elif job.conflict == 'skip' and _exists(target):
                report._record(number, target, 'skipped', '이미 존재하는 경로')
            else:
                suffix_index = 0
                while True:
                    if job.conflict == 'rename':
                        while _exists(target):
                            suffix_index += 1
                            target = output / (f'{stem}_{suffix_index}' + source.suffix)
                    try:
                        _copy_atomic(source, target, source_signature, job.conflict)
                        report._record(number, target, 'copied')
                        break
                    except FileExistsError:
                        if job.conflict == 'skip':
                            report._record(number, target, 'skipped', '다른 작업에서 먼저 생성한 경로')
                            break
                        if job.conflict != 'rename':
                            raise
                        # A concurrent creator won this rename candidate.
                        suffix_index += 1
                        target = output / (f'{stem}_{suffix_index}' + source.suffix)
        except Exception as exc:
            report._record(number, target, 'error', f'{type(exc).__name__}: {exc}')
            break
        finally:
            percent = report.processed_count * 100 // report.requested_count
            _notify(progress, percent, f'{report.processed_count:,}/{report.requested_count:,} · 복제 {report.copied_count:,}, 건너뜀 {report.skipped_count:,}')
    report.cancelled = report.cancelled or _cancelled(cancel_event)
    report.completed = not report.cancelled and report.error_count == 0 and report.processed_count == report.requested_count
    report.elapsed_seconds = round(time.monotonic() - started, 6)
    return report


def parse_leading_number(name):
    match = re.match(r'^(\d+)', Path(name).stem)
    return int(match.group(1)) if match else None


def _extensions(values):
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r'[,;\s]+', values.strip())
    result = []
    for value in values:
        value = str(value).strip().lower()
        if value in ('', '*', '*.*', '(전체)', '전체'):
            return []
        value = value[1:] if value.startswith('*.') else value
        if not value.startswith('.'):
            value = '.' + value
        if '/' in value or '\\' in value or value == '.':
            raise ValueError('확장자 필터를 확인하세요.')
        if value not in result:
            result.append(value)
    return result


def scan_missing_numbers(folder, extensions=None, min_number=None, max_number=None, cancel_event=None, progress=None):
    """Inspect non-recursive filenames only; expected gaps stay as ranges.

    With both bounds omitted this returns an inventory without claiming which
    numbers should exist. Cancelled or unreadable scans never claim complete
    missing-number results. Memory depends on existing files, not range size.
    """
    started = time.monotonic()
    low, high = _range(min_number, max_number, optional=True)
    filters = _extensions(extensions)
    if not str(folder).strip():
        raise ValueError('검사할 폴더를 선택하세요.')
    directory = Path(folder).expanduser().absolute()
    if not directory.is_dir():
        raise ValueError('검사할 폴더를 선택하세요.')
    report = MissingReport(str(directory), filters, low, high)
    number_files = {}
    visited = 0
    _notify(progress, 0, '파일 이름을 읽는 중입니다.')
    with os.scandir(directory) as entries:
        for entry in entries:
            if _cancelled(cancel_event):
                report.cancelled = True
                break
            visited += 1
            try:
                if entry.is_symlink():
                    report.skipped_links.append(entry.name)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if filters and Path(entry.name).suffix.lower() not in filters:
                    continue
                report.scanned_count += 1
                number = parse_leading_number(entry.name)
                if number is None:
                    report.unnumbered.append(entry.name)
                else:
                    report.numbered_count += 1
                    number_files.setdefault(number, []).append(entry.name)
            except OSError as exc:
                report.errors.append(entry.name + ': ' + str(exc))
            finally:
                _notify(progress, 0, f'항목 {visited:,}개 확인 · 대상 파일 {report.scanned_count:,}개')
    report.cancelled = report.cancelled or _cancelled(cancel_event)
    report.unique_count = len(number_files)
    report.duplicates = {number: sorted(names) for number, names in sorted(number_files.items()) if len(names) > 1}
    report.unnumbered.sort(key=str.casefold)
    report.skipped_links.sort(key=str.casefold)
    if low is not None:
        report.out_of_range = {number: sorted(names) for number, names in sorted(number_files.items()) if number < low or number > high}
    report.completed = not report.cancelled and not report.errors
    if report.completed and low is not None:
        cursor = low
        for number in sorted(number_files):
            if number < low:
                continue
            if number > high:
                break
            if cursor < number:
                report.missing_ranges.append((cursor, number - 1))
            cursor = number + 1
        if cursor <= high:
            report.missing_ranges.append((cursor, high))
        report.missing_count = sum(end - start + 1 for start, end in report.missing_ranges)
        report.missing_checked = True
    report.elapsed_seconds = round(time.monotonic() - started, 6)
    _notify(progress, 100 if report.completed else 0, '검사 완료' if report.completed else '부분 검사 결과')
    return report


class NumberToolsApp:
    """Two bounded workflows sharing one worker and the suite stop event."""
    def __init__(self, root):
        self.root = root
        self.worker = None
        self.cancel_event = threading.Event()
        self.events = queue.Queue()
        self.last_report = None
        self._closed = False
        self._close_requested = False
        self._poll_id = None
        self._controls = []
        self._states = {}
        self.root.title('번호 파일 도구 · 복제 / Missing No.')
        self.root.geometry('1050x760')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self._build_ui()
        self.root.bind('<Destroy>', self._destroyed, add='+')
        self._poll_id = self.root.after(75, self._poll)

    def is_busy(self):
        return self.worker is not None and self.worker.is_alive()

    def _entry_row(self, parent, label, variable, command, button='찾기…'):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text=label, width=28).pack(side='left')
        entry = ttk.Entry(row, textvariable=variable)
        entry.pack(side='left', fill='x', expand=True, padx=6)
        picker = ttk.Button(row, text=button, command=command)
        picker.pack(side='left')
        self._controls.extend((entry, picker))
        return entry

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='번호 파일 복제와 파일명 기반 누락 번호 검사', font=('맑은 고딕', 13, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='복제는 원본 바이트와 수정 시간을 보존합니다. 누락 검사는 폴더 바로 아래의 파일 이름만 읽습니다.').pack(anchor='w', pady=(5, 12))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='x')
        duplicate = ttk.Frame(self.tabs, padding=12)
        missing = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(duplicate, text='번호 파일 복제')
        self.tabs.add(missing, text='Missing No. 검사')
        self.source_var = tk.StringVar(master=self.root)
        self.output_var = tk.StringVar(master=self.root)
        self.dup_min_var = tk.StringVar(master=self.root, value='1')
        self.dup_max_var = tk.StringVar(master=self.root, value='10')
        self.policy_var = tk.StringVar(master=self.root, value='건너뛰기 (기본)')
        source_entry = self._entry_row(duplicate, '원본 파일', self.source_var, self._choose_source)
        output_entry = self._entry_row(duplicate, '출력 폴더 (비우면 원본 폴더)', self.output_var, self._choose_output, '폴더 선택…')
        self._range_row(duplicate, self.dup_min_var, self.dup_max_var)
        policy_row = ttk.Frame(duplicate)
        policy_row.pack(fill='x', pady=6)
        ttk.Label(policy_row, text='충돌 처리', width=28).pack(side='left')
        policy = ttk.Combobox(policy_row, textvariable=self.policy_var, state='readonly', width=25,
                              values=('건너뛰기 (기본)', '덮어쓰기', '이름 바꿔 저장'))
        policy.pack(side='left', padx=6)
        self._controls.append(policy)
        ttk.Label(duplicate, text='예: 원본 sample.TIFF, 3~5 → 3.TIFF · 4.TIFF · 5.TIFF\n덮어쓰기는 완성된 임시 파일로 교체하며, 원본 자체는 보호합니다.').pack(anchor='w', pady=5)
        self.duplicate_button = ttk.Button(duplicate, text='복제 시작', command=self._start_duplicate)
        self.duplicate_button.pack(anchor='w', pady=7)
        self._controls.append(self.duplicate_button)
        self.folder_var = tk.StringVar(master=self.root)
        self.ext_var = tk.StringVar(master=self.root, value='(전체)')
        self.scan_min_var = tk.StringVar(master=self.root)
        self.scan_max_var = tk.StringVar(master=self.root)
        folder_entry = self._entry_row(missing, '검사 폴더', self.folder_var, self._choose_folder, '폴더 선택…')
        filter_row = ttk.Frame(missing)
        filter_row.pack(fill='x', pady=5)
        ttk.Label(filter_row, text='확장자 필터', width=28).pack(side='left')
        extension = ttk.Combobox(filter_row, textvariable=self.ext_var, values=EXTENSION_CHOICES, state='readonly', width=20)
        extension.pack(side='left', padx=6)
        self._controls.append(extension)
        self._range_row(missing, self.scan_min_var, self.scan_max_var)
        ttk.Label(missing, text='Min/Max를 모두 비우면 파일명 목록만 집계합니다. 범위를 입력해야 누락 번호를 판정합니다.\n15_1.png, 0015.png는 모두 번호 15입니다. 하위 폴더와 링크 파일은 읽지 않습니다.').pack(anchor='w', pady=5)
        self.scan_button = ttk.Button(missing, text='누락 번호 검사', command=self._start_scan)
        self.scan_button.pack(anchor='w', pady=7)
        self._controls.append(self.scan_button)
        action = ttk.Frame(outer)
        action.pack(fill='x', pady=(12, 6))
        self.stop_button = ttk.Button(action, text='중지', command=self._stop, state='disabled')
        self.stop_button.pack(side='left')
        self.open_button = ttk.Button(action, text='출력 폴더 열기', command=self._open_output, state='disabled')
        self.open_button.pack(side='left', padx=6)
        self.save_button = ttk.Button(action, text='결과 저장(JSON)', command=self._save_result, state='disabled')
        self.save_button.pack(side='right')
        ttk.Button(action, text='로그 저장(TXT)', command=self._save_log).pack(side='right', padx=6)
        self.status_var = tk.StringVar(master=self.root, value='대기 중 · 원본과 출력 경로를 확인하고 시작하세요.')
        ttk.Label(outer, textvariable=self.status_var).pack(anchor='w', pady=4)
        self.progress_bar = ttk.Progressbar(outer, maximum=100, mode='determinate')
        self.progress_bar.pack(fill='x', pady=(0, 8))
        log_frame = ttk.Frame(outer)
        log_frame.pack(fill='both', expand=True)
        self.log_text = tk.Text(log_frame, wrap='none', height=13, state='disabled')
        self.log_text.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_text.yview)
        scroll.pack(side='right', fill='y')
        self.log_text.configure(yscrollcommand=scroll.set)
        self._bind_drop(source_entry, 'source')
        self._bind_drop(output_entry, 'output')
        self._bind_drop(folder_entry, 'folder')

    def _range_row(self, parent, min_var, max_var):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='Min (포함)', width=28).pack(side='left')
        low = ttk.Entry(row, width=16, textvariable=min_var)
        low.pack(side='left', padx=6)
        ttk.Label(row, text='Max (포함)').pack(side='left', padx=(20, 6))
        high = ttk.Entry(row, width=16, textvariable=max_var)
        high.pack(side='left')
        self._controls.extend((low, high))

    def _bind_drop(self, widget, kind):
        top = self.root.winfo_toplevel()
        if not getattr(top, '_suite_dnd_available', False):
            return
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', lambda event: self._drop(event, kind))
        except (ImportError, AttributeError, tk.TclError):
            pass

    def _drop(self, event, kind):
        if self.is_busy():
            return 'break'
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1:
            self.status_var.set('파일 또는 폴더를 한 개씩 넣어주세요.')
            return 'break'
        path = Path(paths[0])
        if kind == 'source':
            if not path.is_file():
                self.status_var.set('원본에는 파일 한 개를 넣어주세요.')
                return 'break'
            self.source_var.set(str(path))
        else:
            folder = path if path.is_dir() else path.parent
            (self.output_var if kind == 'output' else self.folder_var).set(str(folder))
        return 'break'

    def _choose_source(self):
        value = filedialog.askopenfilename(parent=self.root, title='복제할 원본 파일 선택')
        if value:
            self.source_var.set(value)

    def _choose_output(self):
        value = filedialog.askdirectory(parent=self.root, title='출력 폴더 선택')
        if value:
            self.output_var.set(value)

    def _choose_folder(self):
        value = filedialog.askdirectory(parent=self.root, title='누락 번호 검사 폴더')
        if value:
            self.folder_var.set(value)

    def _log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', str(message) + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def _set_busy(self, busy):
        if busy:
            self._states = {widget: str(widget.cget('state')) for widget in self._controls}
            for widget in self._controls:
                widget.configure(state='disabled')
        else:
            for widget, state in self._states.items():
                widget.configure(state=state)
            self._states.clear()
        self.stop_button.configure(state='normal' if busy else 'disabled')

    def _start_duplicate(self):
        if self.is_busy():
            return
        try:
            conflict = {'건너뛰기 (기본)': 'skip', '덮어쓰기': 'overwrite', '이름 바꿔 저장': 'rename'}[self.policy_var.get()]
            job = DuplicateJob(self.source_var.get().strip(), int(self.dup_min_var.get()), int(self.dup_max_var.get()),
                               self.output_var.get().strip() or None, conflict)
            _validate_job(job)
        except (ValueError, KeyError, OSError) as exc:
            messagebox.showerror('입력 확인', str(exc), parent=self.root)
            return
        self._launch(lambda callback: duplicate_files(job, self.cancel_event, callback), 'duplicate')

    def _start_scan(self):
        if self.is_busy():
            return
        try:
            low_text, high_text = self.scan_min_var.get().strip(), self.scan_max_var.get().strip()
            low, high = _range(int(low_text) if low_text else None, int(high_text) if high_text else None, optional=True)
            folder, extension = self.folder_var.get().strip(), self.ext_var.get()
            if not Path(folder).is_dir() or not folder:
                raise ValueError('검사할 폴더를 선택하세요.')
        except (ValueError, OSError) as exc:
            messagebox.showerror('입력 확인', str(exc), parent=self.root)
            return
        self._launch(lambda callback: scan_missing_numbers(folder, extension, low, high, self.cancel_event, callback), 'missing')

    def _launch(self, function, kind):
        self.cancel_event.clear()
        self.last_report = None
        self.save_button.configure(state='disabled')
        self.open_button.configure(state='disabled')
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')
        self._set_busy(True)
        self.progress_bar.configure(value=0, mode='indeterminate' if kind == 'missing' else 'determinate')
        if kind == 'missing':
            self.progress_bar.start(15)
        self.status_var.set('파일 작업을 시작합니다.')
        def worker():
            last = [0.0]
            def callback(percent, message):
                now = time.monotonic()
                if percent == 100 or now - last[0] >= .1:
                    last[0] = now
                    self.events.put(('progress', (percent, message)))
            try:
                report = function(callback)
                self.events.put(('done', report))
            except Exception as exc:
                self.events.put(('error', f'{type(exc).__name__}: {exc}'))
        self.worker = threading.Thread(target=worker, name='number-tools-worker', daemon=True)
        self.worker.start()

    def _stop(self):
        if self.is_busy():
            self.cancel_event.set()
            self.status_var.set('중지 요청 · 현재 파일 처리를 마친 뒤 멈춥니다.')

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            for _ in range(50):
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    percent, message = value
                    if str(self.progress_bar.cget('mode')) == 'determinate':
                        self.progress_bar.configure(value=percent)
                    if not self.cancel_event.is_set():
                        self.status_var.set(message)
                elif kind == 'done':
                    self.progress_bar.stop()
                    self.progress_bar.configure(mode='determinate')
                    self.last_report = value
                    self._set_busy(False)
                    self.save_button.configure(state='normal')
                    self._show_report(value)
                else:
                    self.progress_bar.stop()
                    self._set_busy(False)
                    self.status_var.set('오류로 중지되었습니다.')
                    self._log(value)
        except queue.Empty:
            pass
        if self._close_requested and not self.is_busy():
            self.close()
            return
        if not self._closed:
            self._poll_id = self.root.after(75, self._poll)

    def _show_report(self, report):
        state = '취소됨 · 부분 결과' if report.cancelled else ('완료' if report.completed else '오류 · 부분 결과')
        self.status_var.set(f'{state} · {report.elapsed_seconds:.2f}초')
        if report.completed:
            self.progress_bar.configure(value=100)
        self._log(state)
        if isinstance(report, DuplicateReport):
            self._log(f'원본: {report.source_path}\n출력: {report.output_dir}\n범위: {report.min_number}~{report.max_number} (양 끝 포함)')
            self._log(f'요청 {report.requested_count:,} · 처리 {report.processed_count:,} · 복제 {report.copied_count:,} · 건너뜀 {report.skipped_count:,} · 오류 {report.error_count:,}')
            for item in report.items[:2000]:
                self._log(f'[{item.status}] {Path(item.path).name}' + (' · ' + item.message if item.message else ''))
            if report.details_truncated or len(report.items) > 2000:
                self._log('화면의 상세 목록은 앞 2,000개입니다. JSON은 최대 10,000개 상세와 전체 집계를 저장합니다.')
            for item in report.errors:
                if item not in report.items[:2000]:
                    self._log(f'[error] {Path(item.path).name} · {item.message}')
            if Path(report.output_dir).is_dir():
                self.open_button.configure(state='normal')
        else:
            self._log(f'검사 폴더: {report.folder}\n확장자: {", ".join(report.extensions) or "(전체)"} · 하위 폴더 제외')
            self._log(f'대상 파일 {report.scanned_count:,} · 번호 파일 {report.numbered_count:,} · 고유 번호 {report.unique_count:,} · 중복 번호 {len(report.duplicates):,}')
            if report.missing_checked:
                self._log(f'예상 범위 {report.min_number}~{report.max_number} · 누락 {report.missing_count:,}개')
                self._log('누락 범위: ' + (', '.join(str(a) if a == b else f'{a}~{b}' for a, b in report.missing_ranges[:1000]) or '없음'))
                if len(report.missing_ranges) > 1000:
                    self._log('나머지 누락 범위는 JSON 결과에서 확인하세요.')
            else:
                self._log('누락 번호를 판정하지 않았습니다. 범위 미지정 또는 취소/읽기 오류가 있는 부분 결과입니다.')
            for number, names in list(report.duplicates.items())[:1000]:
                self._log(f'[중복 {number}] ' + ', '.join(names))
            for label, values in (('범위 밖', [str(n) for n in report.out_of_range]), ('선행 번호 없음', report.unnumbered), ('링크 제외', report.skipped_links), ('읽기 오류', report.errors)):
                if values:
                    self._log(f'[{label}] ' + ', '.join(values[:1000]) + (' … (JSON에 전체 기록)' if len(values) > 1000 else ''))

    def _open_output(self):
        if not isinstance(self.last_report, DuplicateReport):
            return
        path = self.last_report.output_dir
        if os.name == 'nt':
            os.startfile(path)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])

    def _save_result(self):
        if self.last_report is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title='번호 도구 결과 저장', defaultextension='.json',
                                          initialfile='번호도구_결과.json', filetypes=[('JSON', '*.json')])
        if path:
            try:
                Path(path).write_text(json.dumps(self.last_report.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('저장 오류', str(exc), parent=self.root)

    def _save_log(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='번호 도구 로그 저장', defaultextension='.txt',
                                          initialfile='번호도구_로그.txt', filetypes=[('Text', '*.txt')])
        if path:
            try:
                Path(path).write_text(self.log_text.get('1.0', 'end-1c'), encoding='utf-8')
            except OSError as exc:
                messagebox.showerror('저장 오류', str(exc), parent=self.root)

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
            self.status_var.set('현재 파일 처리를 마치고 종료합니다.')
            return
        self._closed = True
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.progress_bar.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    NumberToolsApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
