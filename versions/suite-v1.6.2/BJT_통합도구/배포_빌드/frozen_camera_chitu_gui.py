"""Opt-in GUI proof for the uploaded 49-object camera project.

The real controller performs every inspection and export. Dialog destinations
and browser opening are patched only to keep the proof local and unattended.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback
from unittest import mock


EXPECTED_DISTANCE_MM = 1.4999923706054688
EXPECTED_NORMALIZED_INDICES = [4, 5]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized_records(result):
    return [obj for obj in result.get("objects", []) if obj.get("normalization")]


def exact_normalization_notices(result):
    records = normalized_records(result)
    return (
        [obj["index"] for obj in records] == EXPECTED_NORMALIZED_INDICES
        and all(
            obj["normalization"].get("method") == "exact-collinear-seam-retriangulation"
            and obj["normalization"].get("zero_area_seams_removed") == 2
            and obj["normalization"].get("coordinates_unchanged") is True
            and obj["normalization"].get("exact_surface_union_preserved") is True
            and obj["normalization"].get("exact_oriented_surface_chain_preserved") is True
            for obj in records
        )
    )


def complete_camera_result(result, gap, expected_failures):
    pairs = result.get("pairs", [])
    failures = {(pair["index_a"], pair["index_b"]) for pair in result.get("violations", [])}
    expected_counts = {"clear": 1176 - len(expected_failures)}
    if expected_failures:
        expected_counts["too_close"] = len(expected_failures)
    return (
        result.get("min_gap_mm") == gap
        and result.get("object_count") == 49
        and len(result.get("objects", [])) == 49
        and all(obj.get("valid") for obj in result["objects"])
        and len(pairs) == 1176
        and dict(Counter(pair["status"] for pair in pairs)) == expected_counts
        and failures == expected_failures
        and result.get("processing_completed") is True
        and result.get("complete") is True
        and result.get("status") == ("violation" if expected_failures else "clear")
        and not result.get("cancelled")
        and exact_normalization_notices(result)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.app.resolve()))
    import tkinter as tk
    from suite_ui import SuiteApp, create_root

    root = app = None
    checks, errors, timings, thresholds = {}, [], {}, []
    source_hash = digest(args.project)
    report = {
        "passed": False,
        "frozen": bool(getattr(sys, "frozen", False)),
        "checks": checks,
        "callback_errors": errors,
        "timings_seconds": timings,
        "threshold_results": thresholds,
        "project_name": args.project.name,
        "project_sha256": source_hash,
    }

    def wait_until(predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(.02)
        raise TimeoutError("Camera GUI operation did not finish before timeout")

    def inspect(gap, failures):
        controller.gap_var.set(str(gap))
        invalidated = controller.last_result is None and controller.last_report is None
        checks[f"gap_{gap:g}_invalidates_previous_result"] = invalidated
        started = time.monotonic()
        controller.start()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        timings[f"gap_{gap:g}_worker"] = time.monotonic() - started
        result = controller.last_report
        checks[f"gap_{gap:g}_complete_expected_result"] = complete_camera_result(result, gap, failures)
        checks[f"gap_{gap:g}_status_label"] = controller.status_var.get().startswith("FAIL" if failures else "PASS")
        checks[f"gap_{gap:g}_progress100"] = float(controller.progress_bar.cget("value")) == 100
        checks[f"gap_{gap:g}_two_normalization_notices"] = (
            "형상 유지 정리 2개" in controller.status_var.get()
            and all(
                f"[{obj['index']}] {obj['name']}: 원본 형상을 유지한 검사용 연결면 정리 완료"
                in controller.detail_var.get()
                for obj in normalized_records(result)
            )
        )
        thresholds.append({
            "min_gap_mm": gap,
            "status": result.get("status"),
            "complete": result.get("complete"),
            "processing_completed": result.get("processing_completed"),
            "pair_status_counts": dict(Counter(pair["status"] for pair in result.get("pairs", []))),
            "violation_pairs": [[pair["index_a"], pair["index_b"]] for pair in result.get("violations", [])],
            "normalized_indices": [obj["index"] for obj in normalized_records(result)],
        })
        return result

    try:
        root = create_root()
        root.tk.eval("proc bgerror {msg} {global bjtCameraBgErrors; lappend bjtCameraBgErrors $msg}")
        root.tk.eval("set bjtCameraBgErrors {}")
        app = SuiteApp(root, test_mode=True)
        root.report_callback_exception = lambda typ, value, tb: errors.append("".join(traceback.format_exception(typ, value, tb)))
        root.geometry("1120x780")
        root.update()
        checks["eleven_registered_tools"] = len(app.specs) == 11
        checks["selector_visible_at_1120"] = (
            root.winfo_width() == 1120
            and bool(app.tool_selector.winfo_ismapped())
            and app.tool_selector.winfo_rootx() >= root.winfo_rootx()
            and app.tool_selector.winfo_rootx() + app.tool_selector.winfo_width()
            <= root.winfo_rootx() + root.winfo_width()
        )
        app.tool_selector.current(list(app.specs).index("chitu_checker") + 1)
        app.tool_selector.event_generate("<<ComboboxSelected>>")
        wait_until(lambda: "chitu_checker" in app.controllers or bool(app.failures))
        if app.failures:
            raise RuntimeError(repr(app.failures))
        controller = app.controllers["chitu_checker"]
        checks["selector_opens_chitu_tab"] = app.active_tool() == "chitu_checker"
        controller.set_inputs([str(args.project.resolve())])

        inspect(0.0, set())
        inspect(1.0, set())
        result = inspect(1.5, {(5, 6)})
        pair = result["violations"][0] if result.get("violations") else {}
        checks["gap15_exact_boundary_distance"] = (
            pair.get("distance_mm") == EXPECTED_DISTANCE_MM
            and pair.get("distance_is_exact") is True
        )
        checks["gap15_one_failure_row"] = len(controller.tree.get_children()) == 1
        selected = next(
            identity for identity, value in controller._displayed_pairs.items()
            if (value["index_a"], value["index_b"]) == (5, 6)
        )
        controller.tree.selection_set(selected)
        controller.tree.event_generate("<<TreeviewSelect>>")
        root.update()
        checks["selected_failed_pair"] = controller._selected_pair() == (5, 6)
        checks["selected_pair_detail_visible"] = "FAIL" in controller.detail_var.get() and "1.49999237" in controller.detail_var.get()
        if args.screenshot:
            from PIL import ImageGrab
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab().save(args.screenshot)
            report["screenshot"] = str(args.screenshot)

        json_path = args.out.with_name("camera-gui-gap1.5.json")
        html_path = args.out.with_name("camera-gui-gap1.5.html")
        started = time.monotonic()
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(json_path)):
            controller.save_json()
            wait_until(lambda: not controller.is_busy())
        timings["json_export"] = time.monotonic() - started
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        checks["json_preserves_selected_pair_and_normalization"] = (
            complete_camera_result(exported, 1.5, {(5, 6)})
            and exported.get("selected_pair") == [5, 6]
            and exported["violations"][0].get("distance_mm") == EXPECTED_DISTANCE_MM
        )
        started = time.monotonic()
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(html_path)), mock.patch("webbrowser.open", return_value=True) as browser:
            controller.show_3d()
            wait_until(lambda: not controller.is_busy())
            checks["html_open_requested_without_real_browser"] = browser.call_count == 1
        timings["html_export"] = time.monotonic() - started
        page = html_path.read_text(encoding="utf-8")
        payload = json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S).group(1))
        checks["html_preserves_selected_pair_and_normalization"] = (
            complete_camera_result(payload["report"], 1.5, {(5, 6)})
            and payload["report"].get("selected_pair") == [5, 6]
            and {5, 6}.issubset({trace["meta"]["object_index"] for trace in payload["traces"]})
        )
        checks["html_offline_and_normalization_notice"] = (
            "Plotly.newPlot" in page and "<script src=" not in page
            and "원본 형상을 유지한 검사용 연결면 정리 완료" in page
            and "정점 좌표 변경 없음" in page
        )
        report["html_bytes"] = html_path.stat().st_size
        page = payload = exported = None

        inspect(2.0, {(1, 4), (2, 4), (5, 6), (5, 7)})
        checks["gap2_four_failure_rows"] = len(controller.tree.get_children()) == 4
        controller.show_all_var.set(True)
        controller._populate_pairs()
        root.update()
        checks["all1176_pairs_visible"] = len(controller.tree.get_children()) == 1176

        controller.start()
        controller.stop()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        checks["cancel_does_not_pass"] = (
            bool(controller.last_report.get("cancelled"))
            and not controller.last_report.get("complete")
            and not controller.last_report.get("processing_completed")
            and not controller.status_var.get().startswith("PASS")
        )
        controller.start()
        app.request_close(confirm=False)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                root.update()
                if not root.winfo_exists():
                    break
            except tk.TclError:
                break
            time.sleep(.02)
        else:
            raise TimeoutError("Camera GUI did not close after cancellation")
        worker = controller.worker
        checks["close_waits_for_worker_cleanup"] = (worker is None or not worker.is_alive()) and not app.close_errors
        report["tcl_bgerrors"] = list(root.tk.splitlist(root.tk.eval("set bjtCameraBgErrors")))
        checks["no_callback_or_tcl_errors"] = not errors and not app.close_errors and not report["tcl_bgerrors"]
    except Exception:
        report["traceback"] = traceback.format_exc()
    finally:
        if root is not None:
            try:
                if root.winfo_exists():
                    app.request_close(confirm=False) if app is not None else root.destroy()
            except Exception:
                pass
        checks["source_file_unchanged"] = digest(args.project) == source_hash
        report["passed"] = bool(checks) and all(checks.values()) and "traceback" not in report
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.out), "passed": report["passed"], "checks": checks}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
