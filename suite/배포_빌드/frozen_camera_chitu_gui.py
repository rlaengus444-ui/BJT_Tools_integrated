"""Opt-in GUI proof for the uploaded 49-object camera project.

The real controller performs every inspection and export. Dialog destinations
and browser opening are patched only to keep the proof local and unattended.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
from unittest import mock


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


# The historical seam transformation preserved the fixture surface set. These
# expected distances and pairs test the new direct surface path independently.
EXPECTED_DISTANCE_MM = 1.4999923706054688
EXPECTED_PAIRS = {0.0: set(), 1.0: set(), 1.5: {(5, 6)},
                  2.0: {(1, 4), (2, 4), (5, 6), (5, 7)}}


def complete_camera_result(result, gap):
    pairs = result.get("pairs", [])
    tolerance = result.get("distance_tolerance_mm")
    if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance):
        return False
    for pair in pairs:
        distance = pair.get("distance_mm")
        if not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance < 0:
            return False
        expected = ("contact_or_intersection" if distance <= tolerance else
                    "too_close" if distance + tolerance < gap else "surface_gap_satisfied")
        if pair.get("distance_is_exact") is not True or pair.get("status") != expected:
            return False
    violations = [pair for pair in pairs if pair["status"] != "surface_gap_satisfied"]
    return (result.get("inspection_scope") == "surface-clearance"
            and result.get("geometry_validation_performed") is False
            and result.get("volume_overlap_evaluated") is False
            and result.get("min_gap_mm") == gap and result.get("object_count") == 49
            and len(result.get("objects", [])) == 49 and len(pairs) == 1176
            and result.get("violations") == violations
            and {(pair["index_a"], pair["index_b"]) for pair in violations} == EXPECTED_PAIRS[gap]
            and abs(min(pair["distance_mm"] for pair in pairs) - EXPECTED_DISTANCE_MM) < 1e-9
            and result.get("processing_completed") is True and result.get("complete") is True
            and result.get("status") == ("violation" if violations else "surface_gap_satisfied")
            and not result.get("cancelled"))


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

    def inspect(gap):
        controller.gap_var.set(str(gap))
        invalidated = controller.last_result is None and controller.last_report is None
        checks[f"gap_{gap:g}_invalidates_previous_result"] = invalidated
        started = time.monotonic()
        controller.start()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        timings[f"gap_{gap:g}_worker"] = time.monotonic() - started
        result = controller.last_report
        checks[f"gap_{gap:g}_complete_expected_result"] = complete_camera_result(result, gap)
        checks[f"gap_{gap:g}_status_label"] = controller.status_var.get().startswith("FAIL" if result.get("violations") else "표면 간격 조건 충족")
        checks[f"gap_{gap:g}_progress100"] = float(controller.progress_bar.cget("value")) == 100
        checks[f"gap_{gap:g}_scope_notice"] = (
            "부피 겹침" in controller.status_var.get() and "내부 포함" in controller.status_var.get())
        thresholds.append({
            "min_gap_mm": gap,
            "status": result.get("status"),
            "complete": result.get("complete"),
            "processing_completed": result.get("processing_completed"),
            "pair_status_counts": dict(Counter(pair["status"] for pair in result.get("pairs", []))),
            "violation_pairs": [[pair["index_a"], pair["index_b"]] for pair in result.get("violations", [])],
            "minimum_surface_distance_mm": result.get("minimum_distance_mm"),
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

        inspect(0.0)
        inspect(1.0)
        result = inspect(1.5)
        controller.show_all_var.set(True)
        controller._populate_pairs()
        root.update()
        checks["all1176_pairs_visible"] = len(controller.tree.get_children()) == 1176
        selected = next(iter(controller._displayed_pairs))
        pair = controller._displayed_pairs[selected]
        expected_pair = (pair["index_a"], pair["index_b"])
        controller.tree.selection_set(selected)
        controller.tree.event_generate("<<TreeviewSelect>>")
        root.update()
        checks["selected_pair"] = controller._selected_pair() == expected_pair
        if args.screenshot:
            from PIL import ImageGrab
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab().save(args.screenshot)
            report["screenshot"] = str(args.screenshot)

        json_path = args.out.with_name("camera-gui-gap1.5.json")
        html_path = args.out.with_name("camera-gui-gap1.5.html")
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(json_path)):
            controller.save_json()
            wait_until(lambda: not controller.is_busy())
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        checks["json_preserves_surface_data"] = (
            complete_camera_result(exported, 1.5) and exported.get("selected_pair") == list(expected_pair))
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(html_path)), mock.patch("webbrowser.open", return_value=True) as browser:
            controller.show_3d()
            wait_until(lambda: not controller.is_busy())
            checks["html_open_requested_without_real_browser"] = browser.call_count == 1
        page = html_path.read_text(encoding="utf-8")
        payload = json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S).group(1))
        checks["html_preserves_surface_scope_and_pair"] = (
            complete_camera_result(payload["report"], 1.5)
            and payload["report"].get("selected_pair") == list(expected_pair)
            and "부피 겹침·완전 내부 포함은 검사하지 않음" in page
            and "Plotly.newPlot" in page and "<script src=" not in page)
        report["html_bytes"] = html_path.stat().st_size
        inspect(2.0)
        controller.start()
        controller.stop()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        checks["cancel_does_not_pass"] = (
            bool(controller.last_report.get("cancelled"))
            and not controller.last_report.get("complete")
            and not controller.last_report.get("processing_completed")
            and not controller.status_var.get().startswith("표면 간격 조건 충족")
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
