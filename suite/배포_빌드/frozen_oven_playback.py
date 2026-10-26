"""Actual integrated/frozen 3D controls: viewport bounds and animation semantics."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import sys
import time
import traceback


def fully_visible(widget):
    if not widget.winfo_ismapped() or min(widget.winfo_width(), widget.winfo_height()) <= 1:
        return False
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    right, bottom = x + widget.winfo_width(), y + widget.winfo_height()
    parent = widget.master
    while parent is not None:
        if not parent.winfo_ismapped():
            return False
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        if (x < px - 1 or y < py - 1 or right > px + parent.winfo_width() + 1
                or bottom > py + parent.winfo_height() + 1):
            return False
        parent = parent.master
    return True


def controls(controller, oxygen=False):
    prefix = 'o2_field' if oxygen else 'field'
    return {name: getattr(controller, prefix + '_' + name) for name in
            ('prev_btn', 'play_btn', 'pause_btn', 'stop_btn', 'next_btn', 'slider', 'time_label')}


def caption_metrics(widget):
    from tkinter import font as tkfont, ttk
    try:
        text = str(widget.cget('text'))
    except Exception:
        return None
    if not text:
        return None
    try:
        font = widget.cget('font')
    except Exception:
        style = str(widget.cget('style')) or widget.winfo_class()
        font = ttk.Style(widget).lookup(style, 'font') or 'TkDefaultFont'
    measured = tkfont.Font(root=widget, font=font).measure(text)
    return {'caption': text, 'caption_pixels': measured,
            'available_pixels': widget.winfo_width(),
            'fits': measured <= widget.winfo_width() - 6}


def pump(root, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.01)


def run(report_path=None):
    import tkinter as tk
    import numpy as np
    from suite_ui import SuiteApp, create_root
    from suite_loader import verify_sources
    started = time.monotonic()
    report = {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)),
              'checks': {}, 'views': [], 'callback_errors': [], 'screenshot_notes': []}
    checks = report['checks']
    root = create_root()
    app = None
    try:
        root.tk.eval('set bjtOvenPlaybackBgErrors {}')
        root.tk.eval('proc bgerror {msg} {global bjtOvenPlaybackBgErrors; lappend bjtOvenPlaybackBgErrors $msg}')
        app = SuiteApp(root, test_mode=True)
        root.report_callback_exception = lambda typ, value, tb: report['callback_errors'].append(
            ''.join(traceback.format_exception(typ, value, tb)))
        source_checks = verify_sources()
        assert source_checks and all(item['valid'] for item in source_checks)
        checks['strict_source_hashes'] = True
        # Load through the genuine hash-verifying tool loader and suite adapter.
        # No development-only source recompilation or hash override is used.
        module = app.loader.load('thermal')
        controller = app.mount_tool('thermal', module)
        app.select_tool('thermal')
        root.geometry('1660x860+0+0')
        root.update()
        for oxygen, tab in ((False, 1), (True, 3)):
            controller.right_notebook.select(tab)
            root.update()
            for name, widget in controls(controller, oxygen).items():
                if name != 'time_label':
                    assert str(widget.cget('state')) == 'disabled', ('no result', oxygen, name)
        checks['no_result_controls_disabled'] = True
        inputs = replace(controller._collect_inputs(), nx=3, ny=3, nz=3,
            oven_profile_min_c=[(0.0, 25.0), (0.05, 30.0)],
            max_dt_s=.1, record_interval_s=.1, snapshot_interval_s=.1)
        result = module.simulate(inputs, progress_cb=None, stop_event=None)
        controller.result = result
        controller._initialize_3d_field(result)
        controller._initialize_o2_3d_field(result)
        original_worker = controller.worker
        original_stop = controller.stop_event.is_set()
        temperatures = result.snapshots_c.copy()
        oxygen_values = result.o2_snapshots_percent.copy()
        screenshots = Path(report_path).with_suffix('') if report_path is not None else None
        if screenshots is not None:
            screenshots.mkdir(parents=True, exist_ok=True)
        for width, height in ((1660, 860), (1120, 780)):
            root.geometry(f'{width}x{height}+0+0')
            for language in ('ko', 'en'):
                controller.language_var.set(language)
                for oxygen, tab in ((False, 1), (True, 3)):
                    controller.right_notebook.select(tab)
                    root.update()
                    field = controls(controller, oxygen)
                    detail = {'width': width, 'height': height, 'language': language,
                              'field': 'oxygen' if oxygen else 'temperature', 'controls': {}}
                    for name, widget in field.items():
                        visible = fully_visible(widget)
                        detail['controls'][name] = {'fully_visible': visible,
                            'width': widget.winfo_width(), 'height': widget.winfo_height()}
                        assert visible, (detail['field'], width, height, language, name)
                        # Time labels intentionally wrap across several lines;
                        # their full allocated rectangle is checked above.
                        metrics = caption_metrics(widget) if name.endswith('_btn') else None
                        if metrics is not None:
                            detail['controls'][name].update(metrics)
                            assert metrics['fits'], (detail['field'], language, name, metrics)
                    assert field['slider'].winfo_width() > 40
                    expected = ('재생', '일시정지', '정지') if language == 'ko' else ('Play', 'Pause', 'Stop playback')
                    assert tuple(field[name].cget('text') for name in ('play_btn', 'pause_btn', 'stop_btn')) == expected
                    report['views'].append(detail)
                    if screenshots is not None:
                        try:
                            from PIL import ImageGrab
                            image = ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height()))
                            target = screenshots / f'{width}x{height}-{language}-{detail["field"]}.png'
                            image.save(target)
                            detail['screenshot'] = str(target)
                        except (OSError, ImportError) as exc:
                            # Some CI desktops deny screen capture; the real Tk
                            # ancestor bounds remain mandatory and are reported.
                            report['screenshot_notes'].append(str(exc))
        checks['all_controls_inside_all_eight_viewports'] = len(report['views']) == 8
        for oxygen, tab in ((False, 1), (True, 3)):
            controller.right_notebook.select(tab)
            root.update()
            prefix = 'o2_field' if oxygen else 'field'
            frame = getattr(controller, prefix + '_slider_var')
            field = controls(controller, oxygen)
            field['stop_btn'].invoke()
            assert frame.get() == 0
            field['play_btn'].invoke()
            pump(root, .48)
            assert frame.get() > 1
            assert getattr(controller, prefix + '_playing') is True
            assert getattr(controller, prefix + '_after_id') is not None
            field['pause_btn'].invoke()
            held = frame.get()
            assert not getattr(controller, prefix + '_playing')
            assert getattr(controller, prefix + '_after_id') is None
            pump(root, .24)
            assert frame.get() == held
            field['play_btn'].invoke()
            assert frame.get() > held
            field['stop_btn'].invoke()
            assert frame.get() == 0
            assert not getattr(controller, prefix + '_playing')
            assert getattr(controller, prefix + '_after_id') is None
        checks['play_advances_pause_holds_stop_rewinds_both_fields'] = True
        assert controller.worker is original_worker
        assert controller.stop_event.is_set() == original_stop
        assert controller.result is result
        assert np.array_equal(result.snapshots_c, temperatures)
        assert np.array_equal(result.o2_snapshots_percent, oxygen_values)
        checks['solver_worker_stop_and_field_results_unchanged'] = True
        controller._play_3d_animation()
        controller._play_o2_3d_animation()
        timers = (controller.field_after_id, controller.o2_field_after_id)
        assert all(timers)
        app.request_close(confirm=False)
        assert not app.close_errors
        for timer in timers:
            try:
                root.tk.call('after', 'info', timer)
            except tk.TclError:
                continue
            raise AssertionError('Playback timer survives close: ' + str(timer))
        checks['both_playback_timers_cancelled_on_close'] = True
        report['tcl_bgerrors'] = list(root.tk.splitlist(root.tk.eval('set bjtOvenPlaybackBgErrors')))
        assert not report['callback_errors'] and not report['tcl_bgerrors']
        checks['no_python_or_tcl_callback_errors'] = True
        report['passed'] = all(checks.values())
    except Exception:
        report['traceback'] = traceback.format_exc()
    finally:
        try:
            if app is not None and not app.closing:
                app.request_close(confirm=False)
            elif root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass
        report['seconds'] = round(time.monotonic() - started, 3)
    return report
