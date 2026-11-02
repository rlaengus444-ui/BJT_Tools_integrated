"""CHITUBOX triangle-surface clearance inspection hosted by the BJT suite.

The UI imports native geometry modules only inside its worker. One source file
is one object for STL fallback; native projects preserve their own instances.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

try:
    from display_numbers import format_number, format_lower_bound
except ModuleNotFoundError:
    import importlib.util
    _display_spec = importlib.util.spec_from_file_location("_chitu_display_numbers", Path(__file__).resolve().parents[1] / "display_numbers.py")
    _display_module = importlib.util.module_from_spec(_display_spec)
    _display_spec.loader.exec_module(_display_module)
    format_number, format_lower_bound = _display_module.format_number, _display_module.format_lower_bound


DEFAULT_MIN_GAP_MM = 0.0
SURFACE_SCOPE_TEXT = "삼각형 표면 간격 검사; 부피 겹침·완전 내부 포함은 검사하지 않음"
DISTANCE_DISPLAY_NOTE = "수치는 소수 둘째 자리로 표시하며, 판정은 반올림 전 거리로 합니다."
DROP_EXTENSIONS = frozenset({".chitubox", ".chitu", ".stl"})
PAIR_LABELS = {
    "contact_or_intersection": "FAIL · 표면 접촉/교차",
    "surface_gap_satisfied": "표면 간격 조건 충족",
    "overlap": "FAIL · 부피 겹침",
    "touching": "FAIL · 표면 접촉",
    "too_close": "FAIL · 표면 간격 미달",
    "clear": "PASS",
    "inconclusive": "판정 불가",
}


def _pair_label(pair, *, surface_only=False):
    if surface_only and pair.get("status") == "inconclusive":
        return "거리 미판정"
    if not surface_only and pair.get("status") == "inconclusive" and pair.get("placement_status") == "clear":
        return "배치 분리 · 형상 미완료"
    return PAIR_LABELS.get(pair.get("status"), "판정 불가")


def _object_diagnostic_lines(obj, *, surface_only=False):
    if surface_only or "query_available" in obj:
        return [f"[{obj['index']}] {obj['name']}: 표면 거리 계산 불가 · {obj['query_error']}"] if obj.get("query_error") else []
    translations = {
        "Mesh contains degenerate or invalid triangles.": "퇴화되었거나 면적 수치가 유효하지 않은 삼각형이 있습니다.",
        "Mesh is not watertight; a solid volume is required.": "닫힌 다양체 메쉬가 아니어서 입체 형상을 확정하지 못했습니다.",
        "Mesh face winding is inconsistent.": "면 방향이 일관되지 않습니다.",
        "Mesh contains non-finite coordinates.": "좌표에 NaN 또는 무한대가 있습니다.",
        "Mesh contains invalid face indices.": "삼각형의 정점 인덱스가 유효하지 않습니다.",
        "Mesh is empty.": "비어 있는 메쉬입니다.",
        "Mesh must have finite positive volume and outward-facing faces.": "유한한 양의 체적과 바깥쪽 면 방향을 확인하지 못했습니다.",
    }
    lines = []
    prefix = f"[{obj['index']}] {obj['name']}"
    if obj.get("errors"):
        lines.append(prefix + ": " + "; ".join(translations.get(error, error) for error in obj["errors"]))
        diagnostic = obj.get("topology_diagnostics", {})
        if diagnostic.get("face_counts_complete"):
            lines.append("  진단: 퇴화면 " + format_number(diagnostic.get("degenerate_face_count"))
                         + "개 · 면적 수치 오류 " + format_number(diagnostic.get("invalid_area_face_count"))
                         + "개 · 비다양체 edge " + format_number(diagnostic.get("nonmanifold_edge_count"))
                         + "개 · 경계 edge " + format_number(diagnostic.get("boundary_edge_count")) + "개")
    elif obj.get("normalization"):
        lines.append(prefix + ": 원본 형상을 유지한 검사용 연결면 정리 완료")
    if obj.get("normalization_error"):
        lines.append("  연결면 정리 보류: 원본 표면·체적 보존을 증명하지 못해 자동 수정하지 않았습니다.")
        lines.append("  세부 원인: " + obj["normalization_error"])
    return lines


def _report_status_text(report):
    if report.get("inspection_scope") == "surface-clearance":
        if report.get("cancelled"):
            title = "중단 · 표면 거리 검사 미완료"
        elif report["status"] == "violation":
            title = "FAIL · 표면 간격 위반" + (" · 검사 미완료" if not report.get("complete") else "")
        elif report["status"] == "surface_gap_satisfied" and report.get("complete"):
            title = "표면 간격 조건 충족"
        else:
            title = "표면 거리 미판정"
        pairs = report.get("pairs", [])
        measured = sum(pair.get("distance_mm") is not None for pair in pairs)
        text = f"{title} · 오브젝트 {report.get('object_count', 0)}개 · 거리 측정 {measured}/{report.get('total_pair_count', len(pairs))}쌍"
        if report.get("minimum_distance_mm") is not None:
            label = "전체 쌍 최소 표면 간격" if report.get("closest_distance_is_global") else "측정된 최소 표면 간격"
            text += f" · {label} {format_number(report['minimum_distance_mm'])} mm"
        return text + f" · {format_number(report.get('elapsed_seconds', 0.0))}초\n" + SURFACE_SCOPE_TEXT
    invalid = sum(not row.get("valid") for row in report.get("objects", []))
    if report.get("cancelled"):
        title = "중단 · 검사 미완료"
    elif report.get("placement_complete") and report.get("placement_status") == "clear" and not report.get("complete"):
        title = f"배치 분리 확인 · 형상 검증 미완료 {invalid}개"
    elif report["status"] == "violation":
        title = "FAIL" + (f" · 형상 검증 미완료 {invalid}개" if not report["complete"] else "")
    elif report["status"] == "clear" and report["complete"]:
        title = "PASS"
    else:
        title = "판정 불가"
    text = f"{title} · 오브젝트 {report.get('object_count', 0)}개 · 불합격 {len(report.get('violations', []))}쌍"
    unknown = sum(pair.get("status") == "inconclusive" for pair in report.get("pairs", []))
    if unknown:
        text += f" · 형상 미판정 {unknown}쌍"
    if report.get("placement_status") == "unknown" and not report.get("cancelled"):
        text += " · 배치 분리 미확인"
    normalized = sum(bool(obj.get("normalization")) for obj in report.get("objects", []))
    if normalized:
        text += f" · 형상 유지 정리 {normalized}개"
    if report.get("placement_minimum_distance_is_certified"):
        text += f" · 배치 전체 최소 간격 {format_number(report['placement_minimum_distance_mm'])} mm (인증)"
    else:
        measured = [pair["distance_mm"] for pair in report.get("pairs", []) if pair.get("distance_mm") is not None]
        if measured:
            text += f" · 측정된 최소 간격 {format_number(min(measured))} mm"
            if not report.get("complete"):
                text += " (검사된 유효 메쉬 쌍에 한함)"
    return text + f" · {format_number(report.get('elapsed_seconds', 0.0))}초"


@dataclass
class InspectionResult:
    objects: list
    report: dict


@dataclass(frozen=True)
class DropSelection:
    paths: tuple[str, ...]
    added_count: int
    duplicate_count: int
    rejected: tuple[str, ...]


def parse_dropped_paths(tk_root, data):
    """Decode the Tcl file list supplied by Explorer, without shell parsing."""
    try:
        paths = tk_root.tk.splitlist(data)
    except (tk.TclError, TypeError, ValueError) as exc:
        raise ValueError("끌어놓은 파일 경로를 읽지 못했습니다. ‘파일 선택’을 이용하세요.") from exc
    if not paths:
        raise ValueError("끌어놓은 파일이 없습니다.")
    return paths


def collect_dropped_inputs(existing, dropped):
    """Append supported files only; no file contents or geometry are read."""
    paths = list(existing)
    seen = {os.path.normcase(os.path.realpath(path)) for path in paths}
    duplicates = 0
    rejected = []
    for value in dropped:
        try:
            path = Path(value)
            if not path.is_file():
                rejected.append(f"{path.name or value}: 파일이 없거나 폴더입니다.")
                continue
            if path.suffix.lower() not in DROP_EXTENSIONS:
                rejected.append(f"{path.name}: 지원하지 않는 형식입니다.")
                continue
            identity = os.path.normcase(os.path.realpath(path))
        except (OSError, TypeError, ValueError):
            rejected.append("읽을 수 없는 파일 경로입니다.")
            continue
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        paths.append(str(path))
    added = len(paths) - len(existing)
    if added and len(paths) > 1 and any(Path(path).suffix.lower() != ".stl" for path in paths):
        raise ValueError(
            "프로젝트는 한 개씩 검사합니다. STL과 함께 넣을 수 없습니다. "
            "다른 입력으로 바꾸려면 ‘파일 선택’ 또는 ‘STL 폴더’를 이용하세요."
        )
    return DropSelection(tuple(paths), added, duplicates, tuple(rejected))


def parse_gap(value):
    try:
        gap = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("최소 간격에 0 이상의 유한한 mm 값을 입력하세요.") from exc
    if not math.isfinite(gap) or gap < 0:
        raise ValueError("최소 간격에 0 이상의 유한한 mm 값을 입력하세요.")
    return gap


def inconclusive_report(paths, gap, error=None, *, cancelled=False):
    report = {
        "status": "inconclusive", "complete": False,
        "inspection_scope": "surface-clearance", "geometry_validation_performed": False,
        "volume_overlap_evaluated": False, "containment_evaluated": False,
        "violation_found": None,
        "object_count": 0, "min_gap_mm": gap,
        "objects": [], "pairs": [], "violations": [], "total_pair_count": 0,
        "minimum_distance_mm": None, "minimum_distance_pair": None,
        "closest_distance_is_global": False, "pair_distances_complete": False,
        "processing_completed": False,
        "inputs": [str(path) for path in paths], "cancelled": cancelled,
        "units": "mm (assumed)",
    }
    if error:
        report["error"] = str(error)
    return report


def inspect_inputs(paths, min_gap_mm=DEFAULT_MIN_GAP_MM, cancel_event=None, progress=None):
    """Inspect one project or placed STL inputs without touching any source.

    Cancellation remains an incomplete result, including a request arriving
    during the last native geometry call. Geometry callbacks run on this same
    worker thread and must not touch Tk.
    """
    paths = [Path(path) for path in paths]
    gap = parse_gap(min_gap_mm)
    if not paths:
        raise ValueError("검사할 프로젝트 또는 개별 STL 파일을 선택하세요.")
    started = time.monotonic()
    if cancel_event is not None and cancel_event.is_set():
        return InspectionResult([], inconclusive_report(paths, gap, cancelled=True))
    objects = []
    try:
        if progress:
            progress(0, "파일의 오브젝트와 배치 좌표를 읽는 중입니다.")
        from chitu_overlap.inputs import load_objects
        from chitu_overlap.surface import check_surface_clearances
        def load_progress(percent, message):
            if progress:
                progress(0.45 * percent, message)

        def inspect_progress(percent, message):
            if progress:
                progress(45 + 0.55 * percent, message)

        objects, source_format = load_objects(paths, cancel_event=cancel_event, progress=load_progress, preserve_surface=True)
        report = check_surface_clearances(
            objects, min_gap_mm=gap, cancel_event=cancel_event, progress=inspect_progress
        )
        report.update(
            inputs=[str(path) for path in paths], source_format=source_format,
            units="mm (assumed)",
        )
    except Exception as exc:
        message = "검사에 필요한 메모리가 부족합니다." if isinstance(exc, MemoryError) else str(exc)
        report = inconclusive_report(paths, gap, f"{type(exc).__name__}: {message}")
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    if cancel_event is not None and cancel_event.is_set():
        report["cancelled"] = True
        report["complete"] = False
        report["processing_completed"] = False
        report["pair_distances_complete"] = False
        report["closest_distance_is_global"] = False
        if not report.get("violation_found"):
            report["status"] = "inconclusive"
    return InspectionResult(objects, report)


def validate_output(path, inputs):
    output = Path(path).resolve()
    for source in inputs:
        source = Path(source).resolve()
        if output == source or (
            source.is_dir() and output.parent == source and output.suffix.lower() == ".stl"
        ):
            raise ValueError("검사 결과는 입력 파일과 다른 경로에 저장하세요.")
    if not output.parent.is_dir():
        raise ValueError("결과를 저장할 폴더가 없습니다.")
    return output


def write_result(result, destination, *, kind="json", selected_pair=None, cancel_event=None):
    """Publish one result atomically, preserving an existing file on failure."""
    path = validate_output(destination, result.report.get("inputs", []))
    if kind not in {"json", "html"}:
        raise ValueError("지원하지 않는 결과 형식입니다.")
    if cancel_event is not None and cancel_event.is_set():
        return False
    descriptor, name = tempfile.mkstemp(prefix=".bjt-chitu-", suffix="." + kind, dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        report = dict(result.report)
        if selected_pair is not None:
            report["selected_pair"] = list(selected_pair)
        if kind == "json":
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        else:
            from chitu_overlap.viewer import write_html_report
            write_html_report(result.objects, report, temporary, cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return False
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


class ChituCheckerApp:
    """Single background job and one immutable input/settings snapshot."""
    def __init__(self, root):
        self.root = root
        self.input_paths = []
        self.worker = None
        self.running = False
        self.cancel_event = threading.Event()
        self.stop_event = self.cancel_event
        self.events = queue.Queue()
        self.last_result = None
        self.last_report = None
        self.last_html_path = None
        self._revision = 0
        self._closed = False
        self._close_requested = False
        self._poll_id = None
        self._controls = []
        self._displayed_pairs = {}
        self._dnd_available = False
        self._drop_was_busy = False
        self.drop_targets = ()
        root.title("Chitu 표면 간격 검사")
        root.geometry("1120x780")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.input_var = tk.StringVar(master=root, value="검사할 프로젝트를 선택하세요.")
        self.gap_var = tk.StringVar(master=root, value="0.0")
        self.show_all_var = tk.BooleanVar(master=root, value=True)
        self.status_var = tk.StringVar(master=root, value="미검사 · 표면 접촉/교차는 최소 간격과 관계없이 FAIL입니다.\n" + SURFACE_SCOPE_TEXT)
        self.detail_var = tk.StringVar(master=root, value="오브젝트 쌍을 선택하면 실제 표면 간격을 확인할 수 있습니다.\n" + SURFACE_SCOPE_TEXT)
        self.drop_hint_var = tk.StringVar(master=root)
        self._build_ui()
        self._enable_drag_drop()
        self.gap_var.trace_add("write", self._changed)
        self.root.bind("<Destroy>", self._destroyed, add="+")
        self._refresh_actions()
        self._poll_id = self.root.after(75, self._poll)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(16, 10))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1, minsize=140)
        inputs = ttk.LabelFrame(outer, text="1. 검사 파일", padding=(12, 8))
        inputs.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        inputs.columnconfigure(0, weight=1)
        self.input_label = ttk.Label(inputs, textvariable=self.input_var, wraplength=820)
        self.input_label.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.files_button = ttk.Button(inputs, text="파일 선택", command=self.choose_files)
        self.files_button.grid(row=0, column=1)
        self.folder_button = ttk.Button(inputs, text="STL 폴더", command=self.choose_folder)
        self.folder_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Label(inputs, text="CHITUBOX Basic 프로젝트 1개 또는 배치 좌표가 유지된 개별 STL을 선택합니다.",
                  style="Suite.Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(7, 0))
        self.drop_hint_label = ttk.Label(inputs, textvariable=self.drop_hint_var,
                                        style="Suite.Muted.TLabel", wraplength=1000)
        self.drop_hint_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        self.drop_targets = (inputs, self.input_label, self.drop_hint_label)
        settings = ttk.LabelFrame(outer, text="2. 간격 조건", padding=(12, 8))
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(settings, text="최소 간격 (mm)").pack(side="left")
        self.gap_entry = ttk.Entry(settings, textvariable=self.gap_var, width=12)
        self.gap_entry.pack(side="left", padx=(10, 20))
        ttk.Label(settings, text=f"표면 접촉/교차는 항상 FAIL · 기본 거리 오차 {format_number(1e-6)} mm\n{DISTANCE_DISPLAY_NOTE}",
                  style="Suite.Muted.TLabel").pack(side="left")
        actions = ttk.Frame(outer)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.run_button = ttk.Button(actions, text="검사 시작", style="Suite.Primary.TButton", command=self.start)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="중단", style="Suite.Danger.TButton", command=self.stop)
        self.stop_button.pack(side="left", padx=8)
        self.json_button = ttk.Button(actions, text="JSON 저장", command=self.save_json)
        self.json_button.pack(side="right")
        self.html_button = ttk.Button(actions, text="3D 배치 보기", command=self.show_3d)
        self.html_button.pack(side="right", padx=(0, 8))
        ttk.Checkbutton(actions, text="전체 쌍 표시", variable=self.show_all_var,
                        command=self._populate_pairs).pack(side="left", padx=16)
        frame = ttk.LabelFrame(outer, text="3. 검사 결과 · 오브젝트 인덱스는 0부터 시작합니다", padding=8)
        frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("state", "a", "b", "gap"), show="headings", selectmode="browse")
        for column, label, width, stretch in (
            ("state", "판정", 170, False), ("a", "오브젝트 A", 330, True),
            ("b", "오브젝트 B", 330, True), ("gap", "간격 (mm)", 150, False),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=90, stretch=stretch,
                             anchor="e" if column == "gap" else "w")
        self.tree.tag_configure("violation", foreground="#b52840")
        self.tree.tag_configure("unknown", foreground="#8b6000")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        details = ttk.Frame(outer)
        details.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        details.columnconfigure(0, weight=1)
        self.details_text = tk.Text(details, height=5, wrap="word", font="TkDefaultFont",
                                    state="disabled", borderwidth=1, relief="solid")
        self.details_text.grid(row=0, column=0, sticky="ew")
        detail_scroll = ttk.Scrollbar(details, orient="vertical", command=self.details_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.details_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_var.trace_add("write", self._update_details)
        self._update_details()
        self.progress_bar = ttk.Progressbar(outer, maximum=100)
        self.progress_bar.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        self.status_label = ttk.Label(outer, textvariable=self.status_var, wraplength=1030, justify="left")
        self.status_label.grid(row=6, column=0, sticky="ew")
        ttk.Label(outer, text=SURFACE_SCOPE_TEXT, style="Suite.Muted.TLabel",
                  wraplength=1030).grid(row=7, column=0, sticky="ew", pady=(4, 0))
        self._controls = [self.files_button, self.folder_button, self.gap_entry]

    def _enable_drag_drop(self):
        registered = []
        try:
            if getattr(self.root.winfo_toplevel(), "_suite_dnd_available", None) is False:
                raise RuntimeError("Drag and drop is unavailable in the current root.")
            from tkinterdnd2 import DND_FILES
            for target in self.drop_targets:
                target.drop_target_register(DND_FILES)
                registered.append(target)
                target.dnd_bind("<<Drop>>", self._drop_files)
        except (ImportError, AttributeError, tk.TclError, RuntimeError):
            for target in registered:
                try:
                    target.drop_target_unregister()
                except (AttributeError, tk.TclError):
                    pass
            # Every input method remains usable when this platform cannot load
            # the optional native drop target. Avoid a misleading drop prompt.
            self._dnd_available = False
            self.drop_hint_var.set("이 환경에서는 끌어놓기를 사용할 수 없습니다. ‘파일 선택’을 이용하세요.")
        else:
            self._dnd_available = True
            self.drop_hint_var.set("파일을 이 영역에 끌어놓으세요. STL은 기존 목록에 추가됩니다.")

    def _drop_files(self, event):
        if self.is_busy():
            self.drop_hint_var.set("검사 중에는 파일을 추가할 수 없습니다. 검사가 끝난 뒤 다시 끌어놓으세요.")
            return "refuse_drop"
        try:
            dropped = parse_dropped_paths(self.root, event.data)
            selection = collect_dropped_inputs(self.input_paths, dropped)
        except (AttributeError, ValueError) as exc:
            self.drop_hint_var.set(str(exc))
            return "refuse_drop"
        if selection.added_count:
            self.set_inputs(selection.paths)
        messages = []
        if selection.added_count:
            messages.append(f"{selection.added_count}개 파일 추가")
        if selection.duplicate_count:
            messages.append(f"이미 추가된 파일 {selection.duplicate_count}개")
        if selection.rejected:
            messages.append(f"추가하지 않은 항목 {len(selection.rejected)}개: " + selection.rejected[0])
        self.drop_hint_var.set(" · ".join(messages))
        return "copy" if selection.added_count or selection.duplicate_count else "refuse_drop"

    def is_busy(self):
        return self.running or self.worker is not None and self.worker.is_alive()

    def _refresh_actions(self):
        busy = self.is_busy()
        if self._dnd_available and self._drop_was_busy and not busy:
            self.drop_hint_var.set("파일을 이 영역에 끌어놓으세요. STL은 기존 목록에 추가됩니다.")
        self._drop_was_busy = busy
        for widget in self._controls:
            widget.configure(state="disabled" if busy else "normal")
        self.run_button.configure(state="normal" if self.input_paths and not busy else "disabled")
        self.stop_button.configure(state="normal" if busy and not self.cancel_event.is_set() else "disabled")
        ready = not busy and self.last_result is not None
        self.json_button.configure(state="normal" if ready else "disabled")
        self.html_button.configure(state="normal" if ready else "disabled")

    def _changed(self, *unused):
        self._revision += 1
        self.last_result = self.last_report = None
        self.last_html_path = None
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._displayed_pairs.clear()
        self.detail_var.set("입력 또는 간격이 바뀌었습니다. 현재 조건으로 다시 검사하세요.")
        if not self.is_busy():
            self.status_var.set("미검사 · 현재 입력과 최소 간격으로 다시 검사하세요.")
        self._refresh_actions()

    def set_inputs(self, paths):
        if self.is_busy():
            return
        self.input_paths = [str(Path(path)) for path in paths]
        self.input_var.set("\n".join(self.input_paths[:3]) + (f"\n외 {len(self.input_paths)-3}개" if len(self.input_paths) > 3 else "")
                           if self.input_paths else "검사할 프로젝트를 선택하세요.")
        self._changed()

    def choose_files(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Chitu 프로젝트 또는 개별 STL 선택",
                filetypes=(("Chitu 프로젝트 / STL", "*.chitubox *.chitu *.stl"), ("모든 파일", "*.*")))
        if paths:
            self.set_inputs(paths)

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="배치 좌표를 유지한 개별 STL 폴더")
        if folder:
            self.set_inputs([folder])

    def start(self):
        if self.is_busy():
            return
        try:
            gap = parse_gap(self.gap_var.get())
            paths = tuple(self.input_paths)
            if not paths:
                raise ValueError("검사할 파일을 선택하세요.")
        except ValueError as exc:
            messagebox.showerror("검사 조건", str(exc), parent=self.root)
            return
        self.last_result = self.last_report = None
        self.last_html_path = None
        self._populate_pairs()
        self.cancel_event.clear()
        self.running = True
        self.progress_bar.configure(value=0)
        self.status_var.set("검사 준비 중 · 오브젝트 배치를 읽고 있습니다.")
        self.detail_var.set("검사 중입니다.")
        revision = self._revision
        def work():
            def progress(percent, message):
                self.events.put(("progress", percent, message))
            try:
                result = inspect_inputs(paths, gap, cancel_event=self.cancel_event, progress=progress)
                self.events.put(("done", result, revision))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}", revision))
        self.worker = threading.Thread(target=work, name="chitu-inspection-worker", daemon=True)
        self._refresh_actions()
        self.worker.start()

    def stop(self):
        if self.is_busy():
            self.cancel_event.set()
            self.status_var.set("중단 요청 · 현재 표면 거리 연산과 파일 정리 후 중단합니다.")
            self._refresh_actions()

    def _show_report(self, result):
        self.last_result = result
        self.last_report = result.report
        report = result.report
        surface_only = report.get("inspection_scope") == "surface-clearance"
        self.status_var.set(_report_status_text(report))
        self.progress_bar.configure(value=100 if report.get("processing_completed", report.get("complete")) else 0)
        errors = []
        if report.get("error"):
            errors.append(report["error"])
        for obj in report.get("objects", []):
            errors.extend(_object_diagnostic_lines(obj, surface_only=surface_only))
        if not surface_only and report.get("placement_complete") and not report.get("complete"):
            errors.insert(0, "배치 분리는 확인했지만 형상 검증은 미완료입니다. 닫힌 입체·메쉬 품질의 PASS가 아닙니다.")
        if report.get("object_count") == 1:
            errors.append("인식된 오브젝트가 1개이므로 비교할 오브젝트 쌍이 없습니다.")
        default = "오브젝트 쌍을 선택하거나 3D 배치 보기를 눌러 실제 표면 간격을 확인하세요." if surface_only else "문제 쌍을 선택하거나 3D 배치 보기를 눌러 실제 배치를 확인하세요."
        self.detail_var.set(("\n".join(errors) if errors else default) + ("\n" + SURFACE_SCOPE_TEXT if surface_only else ""))
        self._populate_pairs()

    def _update_details(self, *unused):
        if self._closed:
            return
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", self.detail_var.get())
        self.details_text.configure(state="disabled")

    def _populate_pairs(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._displayed_pairs.clear()
        if self.last_report is None:
            return
        pairs = self.last_report.get("pairs", [])
        surface_only = self.last_report.get("inspection_scope") == "surface-clearance"
        success = {"clear", "surface_gap_satisfied"}
        visible = pairs if self.show_all_var.get() else [pair for pair in pairs if pair["status"] not in success]
        for index, pair in enumerate(visible[:10000]):
            identity = str(index)
            if pair.get("distance_mm") is not None:
                gap = format_number(pair["distance_mm"])
            elif not surface_only and pair.get("distance_lower_bound_mm") is not None:
                gap = "≥ " + format_lower_bound(pair["distance_lower_bound_mm"])
            elif not surface_only and pair.get("placement_distance_lower_bound_mm") is not None:
                gap = "≥ " + format_lower_bound(pair["placement_distance_lower_bound_mm"])
            else:
                gap = "—"
            tag = "unknown" if pair["status"] == "inconclusive" else "violation" if pair["status"] not in success else ""
            self.tree.insert("", "end", iid=identity, values=(_pair_label(pair, surface_only=surface_only),
                f"[{pair['index_a']}] {pair['object_a']}", f"[{pair['index_b']}] {pair['object_b']}", gap), tags=(tag,))
            self._displayed_pairs[identity] = pair
        if len(visible) > 10000:
            note = "화면에는 앞 10,000쌍을 표시합니다. 전체 결과는 JSON에 저장됩니다."
            if note not in self.detail_var.get():
                self.detail_var.set(self.detail_var.get() + "\n" + note)

    def _selection_changed(self, *unused):
        selection = self.tree.selection()
        pair = self._displayed_pairs.get(selection[0]) if selection else None
        if pair is not None:
            surface_only = (getattr(self, "last_report", None) or {}).get("inspection_scope") == "surface-clearance"
            text = f"[{pair['index_a']}] {pair['object_a']} ↔ [{pair['index_b']}] {pair['object_b']}\n{_pair_label(pair, surface_only=surface_only)}"
            if pair.get("distance_mm") is not None:
                text += f" · 간격 {format_number(pair['distance_mm'])} mm"
            elif not surface_only and pair.get("distance_lower_bound_mm") is not None:
                text += f" · 간격 ≥ {format_lower_bound(pair['distance_lower_bound_mm'])} mm (확인된 거리 하한)"
            elif not surface_only and pair.get("placement_distance_lower_bound_mm") is not None:
                text += f" · 원본 AABB 간격 ≥ {format_lower_bound(pair['placement_distance_lower_bound_mm'])} mm (거리 하한)"
            if not surface_only and pair.get("intersection_volume"):
                text += f" · 교차 부피 {format_number(pair['intersection_volume'])} mm³"
            if surface_only:
                if pair.get("aabb_relation") == "overlapping" and pair.get("distance_mm") is not None and pair["distance_mm"] > 0:
                    text += "\n내부 포함 여부 미검사"
                text += "\n" + SURFACE_SCOPE_TEXT
            elif pair.get("status") == "inconclusive" and pair.get("placement_status") == "clear":
                text += "\n원본 삼각형의 경계 상자로 분리를 확인했습니다. 형상 유효성의 PASS는 아닙니다."
            if pair.get("error"):
                text += "\n" + pair["error"]
            for obj in (getattr(self, "last_report", None) or {}).get("objects", []):
                if obj["index"] in (pair["index_a"], pair["index_b"]):
                    lines = _object_diagnostic_lines(obj, surface_only=surface_only)
                    if lines:
                        text += "\n" + "\n".join(lines)
            self.detail_var.set(text)

    def _selected_pair(self):
        selection = self.tree.selection()
        pair = self._displayed_pairs.get(selection[0]) if selection else None
        return (pair["index_a"], pair["index_b"]) if pair else None

    def save_json(self):
        self._save("json")

    def show_3d(self):
        self._save("html")

    def _save(self, kind):
        if self.is_busy() or self.last_result is None:
            return
        stem = Path(self.input_paths[0]).stem if self.input_paths else "chitu"
        destination = filedialog.asksaveasfilename(parent=self.root,
            title="3D 배치 결과 저장" if kind == "html" else "검사 결과 JSON 저장",
            initialfile=stem + "_검사결과." + kind, defaultextension="." + kind,
            filetypes=(("HTML" if kind == "html" else "JSON", "*." + kind),))
        if not destination:
            return
        try:
            validate_output(destination, self.input_paths)
        except ValueError as exc:
            messagebox.showerror("결과 저장", str(exc), parent=self.root)
            return
        result = self.last_result
        selected_pair = self._selected_pair()
        self.cancel_event.clear()
        self.running = True
        self.status_var.set("3D HTML 생성 중입니다." if kind == "html" else "JSON 저장 중입니다.")
        def work():
            try:
                completed = write_result(result, destination, kind=kind, selected_pair=selected_pair,
                                         cancel_event=self.cancel_event)
                self.events.put(("saved", destination, kind, completed))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}", self._revision))
        self.worker = threading.Thread(target=work, name="chitu-result-worker", daemon=True)
        self._refresh_actions()
        self.worker.start()

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            for _ in range(100):
                event = self.events.get_nowait()
                if event[0] == "progress":
                    if not self.cancel_event.is_set():
                        self.progress_bar.configure(value=event[1])
                        self.status_var.set(event[2])
                elif event[0] == "done":
                    self.running = False
                    self.worker = None
                    if event[2] == self._revision:
                        self._show_report(event[1])
                    else:
                        self.last_result = self.last_report = None
                        self.status_var.set("입력 조건이 바뀌었습니다. 다시 검사하세요.")
                    self._refresh_actions()
                elif event[0] == "saved":
                    self.running = False
                    self.worker = None
                    if event[3]:
                        self.status_var.set("결과 저장 완료: " + event[1])
                        if event[2] == "html":
                            self.last_html_path = event[1]
                            if not self._close_requested:
                                try:
                                    opened = webbrowser.open(Path(event[1]).resolve().as_uri())
                                    if not opened:
                                        self.status_var.set("HTML 저장 완료 · 브라우저에서 열어주세요: " + event[1])
                                except Exception:
                                    self.status_var.set("HTML 저장 완료 · 브라우저에서 열어주세요: " + event[1])
                    else:
                        self.status_var.set("결과 저장 중단 · 기존 파일은 유지했습니다.")
                    self._refresh_actions()
                elif event[0] == "error":
                    self.running = False
                    self.worker = None
                    self.status_var.set("작업 실패: " + event[1])
                    self.detail_var.set(event[1])
                    self._refresh_actions()
        except queue.Empty:
            pass
        if self._close_requested and not self.is_busy():
            self.close()
            return
        self._poll_id = self.root.after(75, self._poll)

    def _destroyed(self, event):
        if event.widget is self.root:
            self._closed = True
            self.cancel_event.set()

    def close(self):
        if self._closed:
            return
        self.cancel_event.set()
        if self.is_busy():
            self._close_requested = True
            self.status_var.set("현재 검사 단계와 파일 정리가 끝나면 종료합니다.")
            return
        self._closed = True
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.root.destroy()


def main(argv=None):
    import sys
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        from chitu_overlap.cli import main as cli_main
        return cli_main(arguments)
    root = create_root()
    ChituCheckerApp(root)
    root.mainloop()
    return 0


def create_root():
    """Enable Explorer drops for standalone use, with the same safe fallback."""
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root._suite_dnd_available = True
    except Exception:
        partial = getattr(tk, "_default_root", None)
        if partial is not None:
            try:
                partial.destroy()
            except tk.TclError:
                pass
        root = tk.Tk()
        root._suite_dnd_available = False
    return root


if __name__ == "__main__":
    raise SystemExit(main())
