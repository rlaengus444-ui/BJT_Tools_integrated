"""Exercise 1.7 inputs and UI in two independent frozen application processes.

The caller supplies a fresh verification directory. No real project or user
settings are opened. Native TkDND is tested through its registered Tcl callback;
this does not claim to synthesize a physical Explorer drag.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import time
import traceback


OPAQUE_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAF0lEQVR4nGNgYGD4////fwYQDcLVus4AR9wG5r+dsuMAAAAASUVORK5CYII=')


def _captions(widget):
    values = []
    try:
        values.append(str(widget.cget('text')))
    except Exception:
        pass
    for child in widget.winfo_children():
        values.extend(_captions(child))
    return values


def _native_image_check(work):
    from PIL import Image
    import m2_image_engine as engine
    source = work / '한글 입력 001.png'
    source.write_bytes(OPAQUE_PNG)
    report = engine.convert_images(engine.ConversionJob((source,), work / 'custom-image',
        width=13, height=9, dpi_x=203.25, dpi_y=612.5))
    assert report.completed, report.to_dict()
    info = report.items[0]['metadata']
    assert (info['width'], info['height'], info['dpi_x'], info['dpi_y']) == (13, 9, 203.25, 612.5)
    assert (info['bits_per_pixel'], info['compression'], info['resolution_unit']) == (4, 5, 2)
    hashes = []
    for index, dpi in enumerate(((400, 400), (203.2, 612.5))):
        output = work / ('dpi-only-' + str(index))
        item = engine.convert_images(engine.ConversionJob((source,), output,
            width=7, height=5, dpi_x=dpi[0], dpi_y=dpi[1]))
        assert item.completed, item.to_dict()
        with Image.open(output / '001.tif') as image:
            hashes.append(hashlib.sha256(image.tobytes()).hexdigest())
    assert hashes == ['85497e6e131c52302625ea385a48c7f7d9bd6c455b0faa1251cd30b86f9733df'] * 2
    assert source.read_bytes() == OPAQUE_PNG
    return {'passed': True, 'custom_output_tags': info,
            'dpi_only_pixel_sha256': hashes, 'input_unchanged': True}


def _drop_check(root, controller, work):
    import trimesh
    assert controller._dnd_available, 'Native TkDND did not initialize'
    targets = controller.drop_targets
    assert len(targets) >= 1
    target = targets[0]
    script = target.dnd_bind('<<Drop>>')
    assert script
    command = root.tk.splitlist(script)[0]
    paths = [work / name for name in ('기존 오브젝트.stl', '카메라 모듈 {기본}[8t].STL')]
    for path in paths:
        trimesh.creation.box().export(path)
    controller.set_inputs([str(paths[0])])
    root.tk.call('set', 'bjt_drop_values', tuple(map(str, paths)))
    payload = root.tk.eval('set bjt_drop_values')
    values = {'%D': payload, '%W': str(target), '%A': 'copy', '%a': 'copy',
              '%ST': 'DND_Files', '%TT': 'DND_Files', '%T': 'DND_Files'}
    args = [values.get(token, '0') for token in target._subst_format_dnd]
    assert root.tk.call(command, *args) == 'copy'
    assert controller.input_paths == list(map(str, paths))
    revision = controller._revision
    assert root.tk.call(command, *args) == 'copy'
    assert controller._revision == revision
    return {'passed': True, 'native_registered_targets': len(targets),
            'unicode_spaces_braces_multi_file': True, 'duplicates_ignored': True,
            'scope': 'Actual registered Tcl callback; physical OS mouse drag not simulated'}


def _language_check(root, controller):
    before = {key: str(var.get()) for key, var in controller._settings_variables().items()
              if key != 'language'}
    profile = [list(controller.profile_tree.item(iid, 'values'))
               for iid in controller.profile_tree.get_children()]
    labels = {}
    for language, expected in (('ko', '시뮬레이션 실행'), ('en', 'Run simulation'),
                               ('ko', '시뮬레이션 실행')):
        controller.language_var.set(language)
        root.update_idletasks()
        assert controller.run_btn.cget('text') == expected
        assert {key: str(var.get()) for key, var in controller._settings_variables().items()
                if key != 'language'} == before
        assert [list(controller.profile_tree.item(iid, 'values'))
                for iid in controller.profile_tree.get_children()] == profile
        labels[language] = controller.run_btn.cget('text')
    return {'passed': True, 'run_button_captions': labels,
            'all_numeric_model_inputs_preserved': True, 'oven_profile_preserved': True}


def _recoater_check(root, app):
    """Run the embedded simulator through its actual buttons in a small window."""
    from frozen_packing_gui import _resize_compact
    from recoater_views import chart_x_bounds
    full_size = root.geometry()
    app.select_tool('recoater')
    try:
        _resize_compact(root)
        root.update()
        tool = app.controllers['recoater']
        def await_gui(predicate):
            deadline = time.monotonic() + 12
            while not predicate() and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert predicate(), (tool.status.get(), tool.problem)

        await_gui(lambda: tool.model is not None)
        assert not tool.is_playing and tool.distance == 0
        assert tool.model.parameters.angle == 45
        assert [tool.aor_panel.fields[k].get() for k in ('test_speed','run_speed','test_outlet','run_outlet')] == ['220','220','2','2']
        assert tool.scene.winfo_viewable() and tool.scene.find_all()
        assert tool.scene.winfo_height() >= 110
        first = tool.model
        tool.seek(first.turn_progress)
        turn = first.at(tool.distance)
        assert turn.fed_distance == first.parameters.bed_length and not turn.feeding and not turn.compact_started
        assert [tool.fields[k].get() for k in ('spread_diameter','compact_diameter','spacing')] == ['20','20','46']
        assert first.aor.lead == 61 and first.contact_x > first.travel_end and tool.contact_button.instate(['disabled'])
        tool.direction.set('front_to_rear')
        await_gui(lambda: tool.model is not None and tool.model is not first)
        second = tool.model
        assert second.turn_progress is None
        tool.seek(second.compact_start + 10)
        during = second.at(tool.distance)
        assert during.phase == 'combined' and during.feeding and during.compact_started
        assert tool.scene.find_withtag('aor-stream')
        tool.seek(second.feed_end)
        assert not tool.scene.find_withtag('aor-stream')
        assert second.at(second.travel_end).compact_world_x == 0
        hovered = []
        for key, unit in (('flow','g/s'),('compare','%'),('height','mm')):
            tool.plots.select(tool.plot_pages[key])
            root.update()
            canvas = tool.canvases[key]
            await_gui(lambda: canvas.winfo_viewable() and getattr(canvas, '_recoater_hover_plot', None))
            bounds = canvas._recoater_hover_plot.bounds
            canvas.event_generate('<Motion>', x=int((bounds.left+bounds.right)/2), y=int((bounds.top+bounds.bottom)/2))
            root.update()
            labels = [canvas.itemcget(i, 'text') for i in canvas.find_withtag('recoater-hover') if canvas.type(i)=='text']
            assert len(labels) == 1 and unit in labels[0], (key,labels)
            canvas.event_generate('<Leave>')
            root.update()
            assert not canvas.find_withtag('recoater-hover')
            hovered.append(key)
        assert tool.chart.winfo_height() >= 80
        left, right = chart_x_bounds(tool.chart.winfo_width())
        x = (left + right) // 2
        tool.chart.event_generate('<Button-1>', x=x, y=40)
        root.update()
        assert abs(tool.distance - ((x-left)/(right-left))*second.travel_end) < 1e-8
        tool.play_button.invoke()
        start = tool.distance
        deadline = time.monotonic() + 4
        while tool.distance <= start and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert tool.is_playing and tool.distance > start
        app.stop_current()
        assert not tool.is_playing and tool._anim is None
        tool.play_button.invoke()
        app.notebook.select(app.home)
        root.update()
        assert not tool.is_playing and tool._anim is None
        app.select_tool('recoater')
        for key, value in (('compact_diameter','10'), ('spread_diameter','6'), ('spacing','9')):
            tool.fields[key].set(value)
        await_gui(lambda: tool.model is not None and tool.model.contact_x <= tool.model.travel_end)
        tool.contact_button.invoke()
        assert tool.distance == tool.model.contact_x and tool.model.at(tool.distance).contacted
        return {'passed': True, 'compact_size': [root.winfo_width(), root.winfo_height()],
                'default_roller_diameters_mm': [20,20], 'default_axis_spacing_mm': 46,
                'default_aor_lead_mm': 61, 'default_has_no_contact': True,
                'narrow_fixture_contact_travel_mm': tool.distance, 'chart_seek': True,
                'rear_to_front_return_compaction': True, 'front_to_rear_inline': True,
                'stream_stops_at_bed_edge': True, 'hovered_plots': hovered, 'animation_advanced': True,
                'suite_stop': True, 'tab_pause': True}
    finally:
        root.geometry(full_size)
        root.update()


def _seed_inputs(app, work):
    import numpy as np
    c = app.controllers
    image = work / '한글 입력 001.png'
    c['bjt'].input_var.set(str(work / '새 모델.stl'))
    c['bjt'].output_var.set(str(work / '격자 출력.stl'))
    c['bjt'].dpi_x_var.set('501')
    arrange = c['arrange']
    module = app.loader.modules['arrange']
    arrange.canvas_width_var.set(1234)
    arrange.sources = {'s1': module.SourceSet('s1', work, [image])}
    arrange.source_by_folder = {str(work): 's1'}
    arrange.placements = {'p1': module.Placement('p1', 's1', '첫 입력', 12, 34, False, 29)}
    arrange.placement_order = ['p1']
    arrange.selected_id = 'p1'
    arrange.output_folder_manual = True
    arrange.output_folder_var.set(str(work / '배치 출력'))
    c['overlay'].folders = [work]
    c['overlay'].threshold_var.set('127')
    c['exchanger'].a_folder_var.set(str(work / 'A'))
    c['exchanger'].b_folder_var.set(str(work / 'B'))
    c['exchanger'].a_start_var.set('22')
    c['alignment'].source = [str(image)]
    c['alignment'].vars['dpi'].set('501')
    c['alignment'].scan_mode.set('full')
    thermal = c['thermal']
    for name in ('box_mat', 'sic_type', 'k_mode', 'o2_tau_mode'):
        widget = getattr(thermal, name)
        alternative = next((value for value in getattr(widget, '_canonical_values', ())
                            if value != widget.get()), None)
        if alternative is not None:
            widget.set(alternative)
    thermal.language_var.set('en')
    thermal.nx.set('6')
    thermal.profile_tree.delete(*thermal.profile_tree.get_children())
    for point in (('0', '23.5'), ('10.25', '600'), ('30', '25')):
        thermal.profile_tree.insert('', 'end', values=point)
    thermal.tc_path = str(work / '측정.csv')
    thermal.tc_time_min = np.array([0., 1., 2.])
    thermal.tc_center_c = np.array([22., 25., 30.])
    c['compare'].left.set_paths([str(image)])
    c['compare'].right.set_paths([str(image)])
    c['compare'].verify_identical.set(True)
    c['number_tools'].source_var.set(str(work / '이미지001.tif'))
    c['number_tools'].dup_min_var.set('2')
    c['sequence_z'].initial_z_var.set('360.5')
    c['sequence_z'].end_var.set('200')
    converter = c['m2_converter']
    converter.input_paths = [str(image)]
    converter.mode_var.set('멀티')
    converter.output_var.set(str(work / '이미지 출력'))
    for field, value in (('width_var', '123'), ('height_var', '234'),
                         ('dpi_x_var', '501.25'), ('dpi_y_var', '502.5')):
        getattr(converter, field).set(value)
    c['chitu_checker'].gap_var.set('1.5')
    c['chitu_checker'].show_all_var.set(True)
    ir = c['ir_feedback']
    ir.target_temperature.set('71.25')
    ir.maximum_ir_percent.set('78.5')
    ir.slice_panel.radius.set('1.25')
    ir.slice_panel.dpi_x.set('450')
    ir.slice_panel.dpi_y.set('500')
    recoater = c['recoater']
    recoater.fields['layer'].set('0.125')
    recoater.fields['angle'].set('47.25')
    recoater.aor_panel.fields['test_speed'].set('215')
    recoater.aor_panel.fields['run_speed'].set('205.5')
    recoater.aor_panel.fields['run_outlet'].set('2.25')
    recoater.aor_panel.fields['period_variation'].set('29')
    recoater.aor_panel.set_calibration_open(True)
    recoater.direction.set('front_to_rear')
    recoater.plots.select(recoater.plot_pages['compare'])
    recoater.flow_view.set('distance')
    recoater.compare_metric.set('formed')
    recoater.compare_view.set('overlay')
    recoater.speed.set('12.5')
    recoater.camera.set('full')


def run(mode, directory):
    import tkinter as tk
    from suite_ui import SuiteApp, create_root
    from suite_state import capture_inputs
    import sys
    work = Path(directory).resolve()
    work.mkdir(parents=True, exist_ok=True)
    settings = work / 'profile/settings.json'
    expected_file = work / 'expected-inputs.json'
    report = {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)),
              'phase': mode, 'checks': {}, 'settings_path': str(settings)}
    checks, errors = report['checks'], []
    started = time.monotonic()
    root = create_root()
    root.withdraw()
    app = None
    try:
        app = SuiteApp(root, test_mode=True, settings_path=settings)
        root.report_callback_exception = lambda typ, value, tb: errors.append(
            ''.join(traceback.format_exception(typ, value, tb)))
        for key in app.specs:
            app.mount_tool(key, app.loader.load(key))
        root.update_idletasks()
        assert len(app.controllers) == len(app.specs) == 14
        assert app.specs['m2_converter']['label'] == '이미지 변환'
        assert app.specs['thermal']['label'] == '오븐 열확산 시뮬레이터'
        assert not any('하나의 공간' in text or '11가지 작업 도구' in text
                       for text in _captions(app.home))
        checks['all_14_tools_and_simple_titles'] = {'passed': True}
        if mode == 'save':
            from frozen_packing_gui import verify as verify_packing
            app.select_tool('nesting')
            root.geometry('1400x950+0+0');root.deiconify();root.update()
            checks['packing_inference_and_exports'] = verify_packing(root, app.controllers['nesting'], work / 'packing')
            checks['recoater_simulation'] = _recoater_check(root, app)
            assert not settings.exists(), 'A fresh verification profile is required'
            checks['native_image_custom_settings'] = _native_image_check(work)
            checks['native_drop_callback'] = _drop_check(root, app.controllers['chitu_checker'], work)
            checks['oven_language_switch'] = _language_check(root, app.controllers['thermal'])
            _seed_inputs(app, work)
            expected = {key: capture_inputs(key, value) for key, value in app.controllers.items()}
            expected_file.write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding='utf-8')
        elif mode == 'restore':
            expected = json.loads(expected_file.read_text(encoding='utf-8'))
            actual = {key: capture_inputs(key, value) for key, value in app.controllers.items()}
            mismatches = [key for key in expected if expected[key] != actual[key]]
            if mismatches:
                report['mismatches'] = {key: {'expected': expected[key], 'actual': actual[key]}
                                        for key in mismatches}
            assert not mismatches, 'Input restore mismatch: ' + repr(mismatches)
            assert app.controllers['thermal'].language_var.get() == 'en'
            assert app.controllers['thermal'].run_btn.cget('text') == 'Run simulation'
            recoater = app.controllers['recoater']
            assert recoater.distance == 0. and not recoater.is_playing
            assert recoater.fields['angle'].get() == '47.25'
            assert recoater.aor_panel.fields['run_speed'].get() == '205.5'
            assert recoater.direction.get() == 'front_to_rear' and recoater.compare_view.get() == 'overlay'
            assert recoater.aor_panel.calibration_open.get()
            checks['fresh_process_all_14_inputs_restored'] = {'passed': True,
                'tools': sorted(actual), 'oven_language': 'en'}
        else:
            raise ValueError('Unknown feature verification phase: ' + mode)
        assert not app.settings_warnings, app.settings_warnings
        app.request_close(confirm=False)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if not root.winfo_exists():
                    break
                root.update()
            except tk.TclError:
                break
            time.sleep(.01)
        else:
            raise AssertionError('Asynchronous tool cleanup did not complete')
        assert not app.close_errors, app.close_errors
        assert settings.is_file(), 'Normal close did not save the input profile'
        checks['normal_close_persisted'] = {'passed': True}
        checks['callback_errors'] = {'passed': not errors, 'errors': errors}
        report['passed'] = all(item['passed'] for item in checks.values())
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
