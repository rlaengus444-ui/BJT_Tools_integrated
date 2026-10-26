#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slice Folder N-Way Overlay Tool

목적
----
슬라이싱 이미지 폴더를 N개 입력받아,
각 폴더의 1번, 2번, 3번... 이미지를 모두 겹쳐서
새로운 슬라이싱 이미지 폴더를 생성한다.

예)
A/1.png + B/1.png + C/1.png -> OUT/1.png
A/2.png + B/2.png + C/2.png -> OUT/2.png
...

지원
----
- 폴더 N개 드래그앤드롭
- 폴더 추가/삭제/전체 삭제
- 자연 정렬: 1.png, 2.png, 10.png 순서
- 같은 파일명 기준 매칭 또는 정렬 순서 기준 매칭
- grayscale max overlay
- grayscale min overlay
- binary union overlay
- add clamp overlay
- 이미지 크기 다를 때 오류/패딩/크롭 선택
- 로그, 진행률, 중단 버튼

필요 패키지
----------
pip install pillow numpy

드래그앤드롭 사용 시
pip install tkinterdnd2

실행
----
python slice_folder_overlay_N_gui.py
"""

from __future__ import annotations

import re
import time
import threading
import traceback
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import numpy as np
from PIL import Image, ImageOps

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


SUPPORTED_EXTS = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg"}


class CancelledError(Exception):
    pass


def natural_key(path_or_name):
    name = path_or_name.name if isinstance(path_or_name, Path) else str(path_or_name)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def find_images(folder: Path) -> List[Path]:
    if not folder.exists():
        raise FileNotFoundError(f"폴더가 없습니다: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"폴더가 아닙니다: {folder}")
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    files.sort(key=natural_key)
    return files


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def clamp_uint8(v: int) -> int:
    return max(0, min(255, int(v)))


def load_gray(path: Path) -> np.ndarray:
    img = Image.open(path)
    gray = ImageOps.grayscale(img)
    return np.asarray(gray, dtype=np.uint8)


def align_arrays(arrays: List[np.ndarray], mode: str, bg_value: int) -> List[np.ndarray]:
    """
    N개 이미지 크기 처리.
    - 오류: 크기가 하나라도 다르면 에러
    - 작은 이미지 패딩: 가장 큰 캔버스에 좌상단 기준 배치
    - 큰 이미지 크롭: 가장 작은 공통 영역으로 크롭
    """
    if not arrays:
        raise ValueError("align할 이미지 배열이 없습니다.")

    shapes = [a.shape for a in arrays]
    if len(set(shapes)) == 1:
        return arrays

    mode_key = mode.strip().lower()
    bg = clamp_uint8(bg_value)

    heights = [s[0] for s in shapes]
    widths = [s[1] for s in shapes]

    if "오류" in mode_key or "error" in mode_key:
        shape_text = ", ".join([f"{w}x{h}" for h, w in shapes])
        raise ValueError(f"이미지 크기가 서로 다릅니다: {shape_text}")

    if "패딩" in mode_key or "pad" in mode_key:
        h = max(heights)
        w = max(widths)
        out = []
        for a in arrays:
            hh, ww = a.shape
            canvas = np.full((h, w), bg, dtype=np.uint8)
            canvas[:hh, :ww] = a
            out.append(canvas)
        return out

    if "크롭" in mode_key or "crop" in mode_key:
        h = min(heights)
        w = min(widths)
        return [a[:h, :w] for a in arrays]

    raise ValueError(f"알 수 없는 크기 처리 옵션: {mode}")


def overlay_arrays(
    arrays: List[np.ndarray],
    overlay_mode: str,
    threshold: int,
    material_is_white: bool,
    background_value: int,
    material_value: int,
) -> np.ndarray:
    """
    overlay_mode:
    - grayscale max: 모든 이미지 중 최대 밝기값
    - grayscale min: 모든 이미지 중 최소 밝기값
    - binary union: threshold 기준 material mask OR
    - add clamp: 모든 이미지 합산 후 255 clamp
    """
    if not arrays:
        raise ValueError("겹칠 이미지가 없습니다.")

    mode = overlay_mode.strip().lower()
    t = clamp_uint8(threshold)
    bg = clamp_uint8(background_value)
    mat = clamp_uint8(material_value)

    stack = np.stack(arrays, axis=0)

    if "binary" in mode or "union" in mode or mode == "or":
        if material_is_white:
            mask = np.any(stack >= t, axis=0)
        else:
            mask = np.any(stack <= t, axis=0)
        out = np.full_like(arrays[0], bg, dtype=np.uint8)
        out[mask] = mat
        return out

    if "add" in mode or "더하기" in mode:
        return np.clip(stack.astype(np.uint16).sum(axis=0), 0, 255).astype(np.uint8)

    if "min" in mode or "어두" in mode:
        return np.min(stack, axis=0).astype(np.uint8)

    return np.max(stack, axis=0).astype(np.uint8)


@dataclass(frozen=True)
class Params:
    folders: List[Path]
    output_dir: Path
    match_mode: str
    overlay_mode: str
    size_mode: str
    threshold: int
    material_is_white: bool
    background_value: int
    material_value: int
    output_name_mode: str
    overwrite: bool


class Logger:
    def __init__(self, text_widget: tk.Text, progress_var=None, progress_label_var=None):
        self.text_widget = text_widget
        self.progress_var = progress_var
        self.progress_label_var = progress_label_var

    def write(self, msg: str):
        self.text_widget.after(0, self._append, str(msg))

    def _append(self, msg: str):
        self.text_widget.insert(tk.END, msg + "\n")
        self.text_widget.see(tk.END)

    def progress(self, percent: float, msg: str = ""):
        p = max(0.0, min(100.0, float(percent)))
        if self.progress_var is not None:
            self.text_widget.after(0, self.progress_var.set, p)
        if self.progress_label_var is not None:
            label = f"{p:.1f}%"
            if msg:
                label += f" - {msg}"
            self.text_widget.after(0, self.progress_label_var.set, label)


def format_seconds(sec: float) -> str:
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.0f}s"
    m, s = divmod(int(sec), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def build_jobs(folder_files: List[List[Path]], match_mode: str, output_name_mode: str) -> List[Tuple[List[Path], str]]:
    """
    반환: [([folder1_img, folder2_img, ...], output_name), ...]
    """
    mode = match_mode.strip().lower()
    out_mode = output_name_mode.strip().lower()

    if "파일명" in mode or "name" in mode:
        dicts = [{p.name: p for p in files} for files in folder_files]
        common = set(dicts[0].keys())
        for d in dicts[1:]:
            common &= set(d.keys())

        names = sorted(common, key=natural_key)
        if not names:
            raise ValueError("모든 폴더에 공통으로 존재하는 파일명이 없습니다.")

        jobs = []
        for name in names:
            paths = [d[name] for d in dicts]
            if "순번" in out_mode or "index" in out_mode:
                suffix = paths[0].suffix.lower()
                out_name = f"{len(jobs) + 1}{suffix}"
            else:
                out_name = name
            jobs.append((paths, out_name))
        return jobs

    # 기본: 정렬 순서 기준
    n = min(len(files) for files in folder_files)
    if n == 0:
        raise ValueError("처리할 이미지 쌍/묶음이 없습니다.")

    jobs = []
    for i in range(n):
        paths = [files[i] for files in folder_files]
        if "순번" in out_mode or "index" in out_mode:
            out_name = f"{i + 1}{paths[0].suffix.lower()}"
        else:
            out_name = paths[0].name
        jobs.append((paths, out_name))
    return jobs


def run_overlay(params: Params, log: Logger, cancel_event: threading.Event):
    if len(params.folders) < 2:
        raise ValueError("입력 폴더는 최소 2개 이상 필요합니다.")

    folder_files = [find_images(folder) for folder in params.folders]

    for folder, files in zip(params.folders, folder_files):
        if not files:
            raise FileNotFoundError(f"이미지가 없는 폴더입니다: {folder}")

    jobs = build_jobs(folder_files, params.match_mode, params.output_name_mode)
    ensure_dir(params.output_dir)

    log.write("========== Slice Folder N-Way Overlay Tool ==========")
    log.write(f"입력 폴더 수: {len(params.folders):,}")
    for i, (folder, files) in enumerate(zip(params.folders, folder_files), start=1):
        log.write(f"[{i}] {folder}  | 이미지 수: {len(files):,}")
    log.write(f"출력 폴더: {params.output_dir}")
    log.write(f"처리 이미지 묶음 수: {len(jobs):,}")
    log.write(f"매칭 방식: {params.match_mode}")
    log.write(f"겹침 방식: {params.overlay_mode}")
    log.write(f"크기 처리: {params.size_mode}")
    log.write("====================================================")

    counts = [len(files) for files in folder_files]
    if len(set(counts)) != 1:
        log.write("WARNING: 폴더별 이미지 개수가 서로 다릅니다.")
        if "정렬" in params.match_mode:
            log.write(f"         정렬 순서 기준에서는 가장 적은 개수인 {min(counts):,}개만 처리됩니다.")

    total = len(jobs)
    start = time.time()
    input_material_total = 0
    output_material_total = 0

    for idx, (paths, out_name) in enumerate(jobs, start=1):
        if cancel_event.is_set():
            raise CancelledError("작업이 사용자에 의해 중단되었습니다.")

        arrays = [load_gray(p) for p in paths]
        arrays = align_arrays(arrays, params.size_mode, params.background_value)

        out = overlay_arrays(
            arrays,
            params.overlay_mode,
            params.threshold,
            params.material_is_white,
            params.background_value,
            params.material_value,
        )

        dst = params.output_dir / out_name
        if dst.exists() and not params.overwrite:
            raise FileExistsError(f"출력 파일이 이미 존재합니다: {dst}")

        Image.fromarray(out, mode="L").save(dst)

        # 통계
        t = clamp_uint8(params.threshold)
        if params.material_is_white:
            input_material_total += sum(int((a >= t).sum()) for a in arrays)
            output_material_total += int((out >= t).sum())
        else:
            input_material_total += sum(int((a <= t).sum()) for a in arrays)
            output_material_total += int((out <= t).sum())

        elapsed = time.time() - start
        per = elapsed / max(1, idx)
        remain = per * (total - idx)
        pct = idx / total * 100.0
        log.progress(pct, f"{idx:,}/{total:,} ETA {format_seconds(remain)}")

        if idx == 1:
            h, w = out.shape
            log.write("--- 첫 출력 이미지 기준 ---")
            log.write("입력:")
            for p in paths:
                log.write(f"  - {p.name}")
            log.write(f"출력: {out_name}")
            log.write(f"출력 이미지 크기: {w} x {h}")
            log.write("--------------------------")

        if idx % 20 == 0 or idx == total:
            log.write(f"progress {idx:,}/{total:,}")

    elapsed = time.time() - start
    log.write("")
    log.write("================ 최종 요약 ================")
    log.write(f"입력 폴더 수: {len(params.folders):,}")
    log.write(f"처리 이미지 묶음 수: {total:,}")
    log.write(f"입력 material pixel 합산: {input_material_total:,}")
    log.write(f"출력 material pixel: {output_material_total:,}")
    if input_material_total > 0:
        log.write(f"출력/입력 material 비율 참고: {output_material_total / input_material_total:.4f}")
    log.write(f"소요 시간: {format_seconds(elapsed)}")
    log.write("==========================================")
    log.progress(100, "complete")


BaseTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title("Slice Folder N-Way Overlay Tool")
        self.geometry("1080x780")

        self.folders: List[Path] = []
        self.output_dir_var = tk.StringVar()

        self.match_mode_var = tk.StringVar(value="정렬 순서 기준")
        self.overlay_mode_var = tk.StringVar(value="grayscale max")
        self.size_mode_var = tk.StringVar(value="오류")
        self.threshold_var = tk.StringVar(value="128")
        self.material_is_white_var = tk.BooleanVar(value=True)
        self.background_value_var = tk.StringVar(value="0")
        self.material_value_var = tk.StringVar(value="255")
        self.output_name_mode_var = tk.StringVar(value="첫 번째 폴더 파일명")
        self.overwrite_var = tk.BooleanVar(value=True)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_label_var = tk.StringVar(value="0.0%")

        self.cancel_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._setup_dnd()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}

        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(8, weight=1)

        folder_frame = ttk.LabelFrame(root, text="입력 폴더 목록 - 폴더 N개 드래그앤드롭 가능")
        folder_frame.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
        folder_frame.columnconfigure(0, weight=1)
        folder_frame.rowconfigure(0, weight=1)

        self.folder_listbox = tk.Listbox(folder_frame, height=8, selectmode=tk.EXTENDED)
        self.folder_listbox.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)

        folder_scroll = ttk.Scrollbar(folder_frame, orient="vertical", command=self.folder_listbox.yview)
        folder_scroll.grid(row=0, column=1, sticky="ns", pady=8)
        self.folder_listbox.configure(yscrollcommand=folder_scroll.set)

        folder_buttons = ttk.Frame(folder_frame)
        folder_buttons.grid(row=0, column=2, sticky="ns", padx=8, pady=8)

        ttk.Button(folder_buttons, text="폴더 추가", command=self.add_folders_dialog).pack(fill="x", pady=3)
        ttk.Button(folder_buttons, text="선택 삭제", command=self.remove_selected_folders).pack(fill="x", pady=3)
        ttk.Button(folder_buttons, text="전체 삭제", command=self.clear_folders).pack(fill="x", pady=3)

        ttk.Label(root, text="출력 폴더").grid(row=1, column=0, sticky="w", **pad)
        self.entry_out = ttk.Entry(root, textvariable=self.output_dir_var)
        self.entry_out.grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(root, text="찾기", command=self.browse_out).grid(row=1, column=2, **pad)

        settings = ttk.LabelFrame(root, text="겹치기 설정")
        settings.grid(row=2, column=0, columnspan=3, sticky="we", padx=8, pady=8)
        for c in range(6):
            settings.columnconfigure(c, weight=1)

        ttk.Label(settings, text="매칭 방식").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(
            settings,
            textvariable=self.match_mode_var,
            values=["정렬 순서 기준", "파일명 기준"],
            state="readonly",
        ).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(settings, text="겹침 방식").grid(row=0, column=2, sticky="w", **pad)
        ttk.Combobox(
            settings,
            textvariable=self.overlay_mode_var,
            values=["grayscale max", "grayscale min", "binary union", "add clamp"],
            state="readonly",
        ).grid(row=0, column=3, sticky="we", **pad)

        ttk.Label(settings, text="크기 처리").grid(row=0, column=4, sticky="w", **pad)
        ttk.Combobox(
            settings,
            textvariable=self.size_mode_var,
            values=["오류", "작은 이미지 패딩", "큰 이미지 크롭"],
            state="readonly",
        ).grid(row=0, column=5, sticky="we", **pad)

        ttk.Label(settings, text="threshold").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.threshold_var, width=12).grid(row=1, column=1, sticky="we", **pad)

        ttk.Checkbutton(settings, text="프린팅 영역이 밝은색/흰색", variable=self.material_is_white_var).grid(row=1, column=2, columnspan=2, sticky="w", **pad)

        ttk.Label(settings, text="background 값").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.background_value_var, width=12).grid(row=2, column=1, sticky="we", **pad)

        ttk.Label(settings, text="material 값(binary)").grid(row=2, column=2, sticky="w", **pad)
        ttk.Entry(settings, textvariable=self.material_value_var, width=12).grid(row=2, column=3, sticky="we", **pad)

        ttk.Label(settings, text="출력 파일명").grid(row=2, column=4, sticky="w", **pad)
        ttk.Combobox(
            settings,
            textvariable=self.output_name_mode_var,
            values=["첫 번째 폴더 파일명", "순번 파일명"],
            state="readonly",
        ).grid(row=2, column=5, sticky="we", **pad)

        ttk.Checkbutton(settings, text="덮어쓰기", variable=self.overwrite_var).grid(row=3, column=0, sticky="w", **pad)

        help_text = (
            "사용법: 위 목록 영역에 슬라이싱 이미지 폴더를 여러 개 드래그앤드롭하거나 [폴더 추가]로 여러 폴더를 추가하세요.\n"
            "정렬 순서 기준: 각 폴더의 자연 정렬 순서대로 1번째 이미지들, 2번째 이미지들을 겹칩니다.\n"
            "파일명 기준: 모든 폴더에 공통으로 존재하는 파일명만 겹칩니다.\n"
            "흰색 material 슬라이싱 이미지라면 보통 grayscale max 또는 binary union을 사용하세요."
        )
        ttk.Label(root, text=help_text, foreground="#555555").grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=6)

        button_frame = ttk.Frame(root)
        button_frame.grid(row=4, column=0, columnspan=3, sticky="we", padx=8, pady=8)
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        self.run_button = ttk.Button(button_frame, text="겹치기 시작", command=self.start)
        self.run_button.grid(row=0, column=0, sticky="we", padx=(0, 5))

        self.cancel_button = ttk.Button(button_frame, text="작업 중단", command=self.cancel, state=tk.DISABLED)
        self.cancel_button.grid(row=0, column=1, sticky="we", padx=(5, 0))

        progress_frame = ttk.Frame(root)
        progress_frame.grid(row=5, column=0, columnspan=3, sticky="we", padx=8, pady=5)
        progress_frame.columnconfigure(0, weight=1)
        ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100).grid(row=0, column=0, sticky="we", padx=(0, 8))
        ttk.Label(progress_frame, textvariable=self.progress_label_var, width=36).grid(row=0, column=1, sticky="e")

        ttk.Label(root, text="로그").grid(row=8, column=0, sticky="nw", **pad)
        log_frame = ttk.Frame(root)
        log_frame.grid(row=8, column=1, columnspan=2, sticky="nsew", padx=8, pady=5)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=22)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _setup_dnd(self):
        if TkinterDnD is None or DND_FILES is None:
            return
        try:
            self.folder_listbox.drop_target_register(DND_FILES)
            self.folder_listbox.dnd_bind("<<Drop>>", self.on_drop_folders)
            self.entry_out.drop_target_register(DND_FILES)
            self.entry_out.dnd_bind("<<Drop>>", self.on_drop_out)
        except Exception:
            pass

    def _parse_drop_paths(self, data: str) -> List[Path]:
        data = str(data).strip()
        paths = []
        cur = ""
        in_brace = False
        for ch in data:
            if ch == "{":
                in_brace = True
                cur = ""
            elif ch == "}":
                in_brace = False
                if cur:
                    paths.append(Path(cur))
                    cur = ""
            elif ch == " " and not in_brace:
                if cur:
                    paths.append(Path(cur))
                    cur = ""
            else:
                cur += ch
        if cur:
            paths.append(Path(cur))
        return paths

    def refresh_folder_listbox(self):
        self.folder_listbox.delete(0, tk.END)
        for i, folder in enumerate(self.folders, start=1):
            self.folder_listbox.insert(tk.END, f"{i}. {folder}")

    def add_folder_paths(self, folders: List[Path]):
        added = 0
        for f in folders:
            if f.is_dir() and f not in self.folders:
                self.folders.append(f)
                added += 1
        self.refresh_folder_listbox()

        if not self.output_dir_var.get().strip() and self.folders:
            self.output_dir_var.set(str(self.folders[0].with_name(self.folders[0].name + "_N_overlay")))

        if added:
            self.log_text.insert(tk.END, f"폴더 {added}개 추가됨\n")
            self.log_text.see(tk.END)

    def add_folders_dialog(self):
        # tkinter 기본 filedialog는 askdirectory가 다중 선택을 지원하지 않아 반복 추가 방식.
        d = filedialog.askdirectory(title="입력 폴더 선택")
        if d:
            self.add_folder_paths([Path(d)])

    def remove_selected_folders(self):
        selected = list(self.folder_listbox.curselection())
        if not selected:
            return
        selected_set = set(selected)
        self.folders = [f for i, f in enumerate(self.folders) if i not in selected_set]
        self.refresh_folder_listbox()

    def clear_folders(self):
        self.folders = []
        self.refresh_folder_listbox()

    def on_drop_folders(self, event):
        paths = self._parse_drop_paths(event.data)
        folders = []
        for p in paths:
            if p.is_dir():
                folders.append(p)
            elif p.is_file():
                folders.append(p.parent)
        self.add_folder_paths(folders)

    def on_drop_out(self, event):
        paths = self._parse_drop_paths(event.data)
        if paths:
            p = paths[0]
            if p.is_file():
                p = p.parent
            self.output_dir_var.set(str(p))

    def browse_out(self):
        d = filedialog.askdirectory(title="출력 폴더 선택")
        if d:
            self.output_dir_var.set(d)

    def parse_params(self) -> Params:
        if len(self.folders) < 2:
            raise ValueError("입력 폴더를 최소 2개 이상 추가하세요.")

        output_dir = Path(self.output_dir_var.get().strip())
        if not str(output_dir):
            raise ValueError("출력 폴더를 지정하세요.")

        return Params(
            folders=list(self.folders),
            output_dir=output_dir,
            match_mode=self.match_mode_var.get(),
            overlay_mode=self.overlay_mode_var.get(),
            size_mode=self.size_mode_var.get(),
            threshold=int(float(self.threshold_var.get())),
            material_is_white=bool(self.material_is_white_var.get()),
            background_value=int(float(self.background_value_var.get())),
            material_value=int(float(self.material_value_var.get())),
            output_name_mode=self.output_name_mode_var.get(),
            overwrite=bool(self.overwrite_var.get()),
        )

    def start(self):
        self.log_text.delete("1.0", tk.END)
        self.progress_var.set(0.0)
        self.progress_label_var.set("0.0%")
        self.cancel_event.clear()

        try:
            params = self.parse_params()
        except Exception as exc:
            messagebox.showerror("입력 오류", str(exc))
            return

        self.run_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)

        log = Logger(self.log_text, self.progress_var, self.progress_label_var)

        def worker():
            try:
                run_overlay(params, log, self.cancel_event)
                self.after(0, lambda: messagebox.showinfo("완료", "N개 이미지 폴더 겹치기가 완료되었습니다."))
            except CancelledError as exc:
                log.write(str(exc))
                log.progress(self.progress_var.get(), "cancelled")
                self.after(0, lambda: messagebox.showinfo("중단", "작업이 중단되었습니다."))
            except Exception as exc:
                log.write("ERROR:")
                log.write(str(exc))
                log.write(traceback.format_exc())
                self.after(0, lambda msg=str(exc): messagebox.showerror("오류", msg))
            finally:
                self.after(0, lambda: self.run_button.config(state=tk.NORMAL))
                self.after(0, lambda: self.cancel_button.config(state=tk.DISABLED))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel(self):
        self.cancel_event.set()
        self.cancel_button.config(state=tk.DISABLED)
        self.progress_label_var.set("중단 요청됨")


if __name__ == "__main__":
    app = App()
    app.mainloop()
