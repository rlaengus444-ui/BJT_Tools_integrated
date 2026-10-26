"""Exercise the integrated Chitu tab with a real project under Windows/Wine.

No browser is launched. All exported reports stay beside --out, and the source
project is compared before and after. The suite's test_mode isolates settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from unittest import mock


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

    root = None
    app = None
    checks = {}
    errors = []
    source_hash = hashlib.sha256(args.project.read_bytes()).hexdigest()
    report = {"passed": False, "checks": checks, "callback_errors": errors}

    def wait_until(predicate, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(.02)
        raise TimeoutError("GUI operation did not finish before timeout")

    try:
        root = create_root()
        root.tk.eval('proc bgerror {msg} {global bjtAuditBgErrors; lappend bjtAuditBgErrors $msg}')
        root.tk.eval('set bjtAuditBgErrors {}')
        app = SuiteApp(root, test_mode=True)
        root.report_callback_exception = lambda typ, value, tb: errors.append(''.join(traceback.format_exception(typ, value, tb)))
        root.geometry("1120x780")
        root.update()
        checks["eleven_registered_tools"] = len(app.specs) == 11
        checks["selector_visible_at_1120"] = (
            root.winfo_width() == 1120
            and bool(app.tool_selector.winfo_ismapped())
            and app.tool_selector.winfo_rootx() >= root.winfo_rootx()
            and app.tool_selector.winfo_rootx() + app.tool_selector.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        )
        index = list(app.specs).index("chitu_checker") + 1
        app.tool_selector.current(index)
        app.tool_selector.event_generate("<<ComboboxSelected>>")
        wait_until(lambda: "chitu_checker" in app.controllers or bool(app.failures))
        if app.failures:
            raise RuntimeError(repr(app.failures))
        controller = app.controllers["chitu_checker"]
        checks["selector_opens_chitu_tab"] = app.active_tool() == "chitu_checker"
        controller.set_inputs([str(args.project.resolve())])
        controller.gap_var.set("3")
        controller.start()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        result = controller.last_report
        pair = result["violations"][0] if result.get("violations") else {}
        checks["real_gap3_fail_complete"] = result["status"] == "violation" and result["complete"] and result["object_count"] == 63
        checks["real_gap3_expected_pair"] = len(result["violations"]) == 1 and (pair.get("index_a"), pair.get("index_b")) == (15, 31)
        checks["real_gap3_expected_distance"] = abs(pair.get("distance_mm", 0) - 2.924621820449829) < 1e-9
        checks["fail_label_and_row"] = controller.status_var.get().startswith("FAIL") and len(controller.tree.get_children()) == 1
        controller.tree.selection_set(controller.tree.get_children()[0])
        root.update()
        checks["selected_failed_pair"] = controller._selected_pair() == (15, 31)
        if args.screenshot:
            try:
                from PIL import ImageGrab
                image = ImageGrab.grab()
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                image.save(args.screenshot)
                report["screenshot"] = str(args.screenshot)
            except Exception as exc:
                report["screenshot_error"] = str(exc)
        json_path = args.out.with_name("gui-real-gap3.json")
        html_path = args.out.with_name("gui-real-gap3.html")
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(json_path)):
            controller.save_json()
            wait_until(lambda: not controller.is_busy())
        exported = json.loads(json_path.read_text(encoding="utf-8"))
        checks["json_export_settings_and_pair"] = exported["min_gap_mm"] == 3 and exported["violations"][0]["distance_mm"] == pair["distance_mm"]
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(html_path)), mock.patch("webbrowser.open", return_value=True) as browser:
            controller.show_3d()
            wait_until(lambda: not controller.is_busy())
            checks["html_open_requested_without_real_browser"] = browser.call_count == 1
        page = html_path.read_text(encoding="utf-8")
        checks["html_selected_pair_and_inline_viewer"] = '"selected_pair":[15,31]' in page and "Plotly.newPlot" in page and "<script src=" not in page
        controller.gap_var.set("1")
        checks["changed_settings_invalidate_previous_result"] = controller.last_report is None and controller.last_result is None and not controller.tree.get_children() and str(controller.html_button.cget("state")) == "disabled"
        controller.start()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        result = controller.last_report
        checks["real_gap1_pass_complete"] = result["status"] == "clear" and result["complete"] and controller.status_var.get().startswith("PASS")
        controller.start()
        controller.stop()
        wait_until(lambda: not controller.is_busy() and controller.last_report is not None)
        checks["cancel_does_not_pass"] = controller.last_report.get("cancelled") and not controller.last_report["complete"] and not controller.status_var.get().startswith("PASS")
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
            raise TimeoutError("Suite did not close after cancellation")
        worker = controller.worker
        checks["close_waits_for_worker_cleanup"] = (worker is None or not worker.is_alive()) and not app.close_errors
        report["tcl_bgerrors"] = list(root.tk.splitlist(root.tk.eval('set bjtAuditBgErrors')))
        checks["no_callback_or_tcl_errors"] = not errors and not app.close_errors and not report["tcl_bgerrors"]
        checks["source_file_unchanged"] = hashlib.sha256(args.project.read_bytes()).hexdigest() == source_hash
        report.update(passed=all(checks.values()), project_sha256=source_hash, project_name=args.project.name)
    except Exception:
        report["traceback"] = traceback.format_exc()
    finally:
        if root is not None:
            try:
                if root.winfo_exists():
                    app.request_close(confirm=False) if app is not None else root.destroy()
            except Exception:
                pass
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.out), "passed": report["passed"], "checks": checks}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
