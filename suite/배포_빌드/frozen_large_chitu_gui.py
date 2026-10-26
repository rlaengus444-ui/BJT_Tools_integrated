"""Opt-in actual GUI proof for the largest 22-object process-record fixture."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback
from unittest import mock


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(args.app.resolve()))
    import tkinter as tk
    from suite_ui import SuiteApp,create_root

    root=app=None
    checks={};errors=[];timings={}
    source_hash=digest(args.project)
    report={'passed':False,'frozen':bool(getattr(sys,'frozen',False)),
            'checks':checks,'callback_errors':errors,'timings_seconds':timings}

    def wait_until(predicate,timeout=300):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            root.update()
            if predicate():return
            time.sleep(.02)
        raise TimeoutError('Large GUI operation did not finish')

    def check_report(result,gap):
        counts=dict(collections.Counter(pair['status'] for pair in result['pairs']))
        expected={'inconclusive':176,'clear':54,'too_close':1} if gap==1 else {'inconclusive':176,'clear':55}
        return result['object_count']==22 and len(result['pairs'])==231 and counts==expected and result.get('processing_completed') and not result['complete']

    try:
        root=create_root()
        root.tk.eval('proc bgerror {msg} {global bjtLargeBgErrors; lappend bjtLargeBgErrors $msg}')
        root.tk.eval('set bjtLargeBgErrors {}')
        app=SuiteApp(root,test_mode=True)
        root.report_callback_exception=lambda typ,value,tb:errors.append(''.join(traceback.format_exception(typ,value,tb)))
        root.geometry('1120x780');root.update()
        checks['eleven_registered_tools']=len(app.specs)==11
        app.tool_selector.current(list(app.specs).index('chitu_checker')+1)
        app.tool_selector.event_generate('<<ComboboxSelected>>')
        wait_until(lambda:'chitu_checker' in app.controllers or bool(app.failures))
        if app.failures:raise RuntimeError(repr(app.failures))
        controller=app.controllers['chitu_checker']
        checks['selector_opens_chitu']=app.active_tool()=='chitu_checker'
        controller.set_inputs([str(args.project.resolve())]);controller.gap_var.set('1')
        started=time.monotonic();controller.start()
        wait_until(lambda:not controller.is_busy() and controller.last_report is not None)
        timings['gap1_worker']=time.monotonic()-started
        first=controller.last_report
        checks['gap1_processed_with_bad_inputs']=check_report(first,1) and first['status']=='violation'
        invalid=[obj['index'] for obj in first['objects'] if not obj['valid']]
        checks['eleven_invalid_objects_preserved']=invalid==[0,2,3,7,8,9,10,12,13,14,15]
        pair=first['violations'][0] if first['violations'] else {}
        checks['known_pair_and_exact_gap']=(pair.get('index_a'),pair.get('index_b'))==(6,16) and abs(pair.get('distance_mm',0)-.6575593948364258)<1e-9 and pair.get('distance_is_exact') is True
        checks['fail_and_unknowns_visible']=controller.status_var.get().startswith('FAIL') and '미판정 176' in controller.status_var.get()
        checks['progress100_does_not_imply_pass']=float(controller.progress_bar.cget('value'))==100 and not first['complete']
        selected=next(identity for identity,value in controller._displayed_pairs.items() if (value['index_a'],value['index_b'])==(6,16))
        controller.tree.selection_set(selected);root.update()
        checks['selected_failed_pair']=controller._selected_pair()==(6,16)
        if args.screenshot:
            from PIL import ImageGrab
            ImageGrab.grab().save(args.screenshot)
            report['screenshot']=str(args.screenshot)
        json_path=args.out.with_name('large-gui-gap1.json')
        html_path=args.out.with_name('large-gui-gap1.html')
        started=time.monotonic()
        with mock.patch('tkinter.filedialog.asksaveasfilename',return_value=str(json_path)):
            controller.save_json();wait_until(lambda:not controller.is_busy())
        timings['json_export']=time.monotonic()-started
        exported=json.loads(json_path.read_text(encoding='utf-8'))
        checks['json_preserves_partial_failure']=check_report(exported,1) and exported['selected_pair']==[6,16]
        started=time.monotonic()
        with mock.patch('tkinter.filedialog.asksaveasfilename',return_value=str(html_path)),mock.patch('webbrowser.open',return_value=True) as browser:
            controller.show_3d();wait_until(lambda:not controller.is_busy())
            checks['html_open_requested_without_browser']=browser.call_count==1
        timings['html_export']=time.monotonic()-started
        page=html_path.read_text(encoding='utf-8')
        payload=json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>',page,re.S).group(1))
        traces={trace['meta']['object_index']:trace for trace in payload['traces']}
        checks['selected_pair_exact_180k_geometry']=payload['report']['selected_pair']==[6,16] and traces[6]['meta']['preview_kind']=='exact' and len(traces[6]['i'])==180326 and traces[16]['meta']['preview_kind']=='exact' and len(traces[16]['i'])==12
        checks['large_context_bounds_caption']=payload['preview']['uses_bounds_proxies'] and '외곽박스' in page
        checks['standalone_html_below20MiB']=html_path.stat().st_size<20*1024**2 and 'Plotly.newPlot' in page and '<script src=' not in page
        report['html_bytes']=html_path.stat().st_size
        page=payload=traces=None
        controller.gap_var.set('0.5')
        checks['settings_invalidate_old_result']=controller.last_report is None and controller.last_result is None
        started=time.monotonic();controller.start()
        wait_until(lambda:not controller.is_busy() and controller.last_report is not None)
        timings['gap05_worker']=time.monotonic()-started
        second=controller.last_report
        checks['gap05_inconclusive_not_pass']=check_report(second,.5) and second['status']=='inconclusive' and not controller.status_var.get().startswith('PASS')
        checks['gap05_progress100']=float(controller.progress_bar.cget('value'))==100
        controller.show_all_var.set(True);controller._populate_pairs();root.update()
        checks['all231_pairs_visible']=len(controller.tree.get_children())==231
        bounds_rows=[identity for identity,value in controller._displayed_pairs.items() if value.get('distance_mm') is None and value.get('distance_lower_bound_mm') is not None]
        checks['lower_bound_row_is_labeled']=bool(bounds_rows) and all(str(controller.tree.item(identity,'values')[3]).startswith('≥ ') for identity in bounds_rows)
        controller.start()
        wait_until(lambda:controller.is_busy() and float(controller.progress_bar.cget('value'))>0,timeout=30)
        controller.stop();wait_until(lambda:not controller.is_busy() and controller.last_report is not None)
        checks['cancel_during_read_not_pass']=bool(controller.last_report.get('cancelled')) and not controller.last_report['complete'] and not controller.status_var.get().startswith('PASS')
        controller.start()
        wait_until(lambda:controller.is_busy() and float(controller.progress_bar.cget('value'))>0,timeout=30)
        app.request_close(confirm=False)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                root.update()
                if not root.winfo_exists():break
            except tk.TclError:break
            time.sleep(.02)
        else:raise TimeoutError('Large GUI did not close after read cancellation')
        worker=controller.worker
        checks['close_waits_for_worker_cleanup']=(worker is None or not worker.is_alive()) and not app.close_errors
        report['tcl_bgerrors']=list(root.tk.splitlist(root.tk.eval('set bjtLargeBgErrors')))
        checks['no_callback_or_tcl_errors']=not errors and not app.close_errors and not report['tcl_bgerrors']
        checks['source_file_unchanged']=digest(args.project)==source_hash
        report.update(passed=all(checks.values()),project_name=args.project.name,project_sha256=source_hash,
                      invalid_indices=invalid,known_fail_pair=[6,16],known_gap_mm=pair.get('distance_mm'))
    except Exception:
        report['traceback']=traceback.format_exc()
    finally:
        if root is not None:
            try:
                if root.winfo_exists():app.request_close(confirm=False) if app is not None else root.destroy()
            except Exception:pass
        args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(args.out),'passed':report['passed'],'checks':checks},ensure_ascii=False))
    return 0 if report['passed'] else 2


if __name__=='__main__':raise SystemExit(main())
