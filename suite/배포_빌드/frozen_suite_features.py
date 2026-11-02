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


def _chitu_placement_preview_check(work):
    """Exercise the packaged geometry/viewer with synthetic data and actual VTK.

    This runs inside the existing save-phase EXE. Browser JavaScript execution
    remains covered by mandatory source tests and source/frozen byte parity;
    this check reads the bundled HTML contract without adding a JS interpreter.
    """
    from decimal import Decimal
    import re
    import sys
    import numpy as np
    import trimesh
    from chitu_overlap.display_preview import simplify
    from chitu_overlap.geometry import MeshObject, check_overlaps
    from chitu_overlap.viewer import MAX_EXACT_FACES, MAX_EXACT_VERTICES, write_html_report
    from display_numbers import format_lower_bound, format_number

    directory = work / 'synthetic-chitu-preview'
    directory.mkdir()
    objects = []
    for index in range(3):
        mesh = trimesh.creation.icosphere(subdivisions=6)
        mesh.apply_translation((index * 5.43210987654321, .234567890123456, 0.))
        objects.append(MeshObject('synthetic closed sphere ' + str(index), mesh))
    invalid = trimesh.creation.box()
    # One open face plus one exact zero-area triangle: finite indexed source
    # geometry still proves far placement, but never becomes a validated solid.
    invalid = trimesh.Trimesh(vertices=invalid.vertices.copy(),
        faces=np.vstack((invalid.faces[:-1], [0, 0, 0])), process=False)
    invalid.apply_translation((30.1234567890123, .234567890123456, 0.))
    objects.append(MeshObject('synthetic invalid open box', invalid))
    def mesh_hashes():
        return [{'vertices': hashlib.sha256(obj.mesh.vertices.tobytes()).hexdigest(),
                 'faces': hashlib.sha256(obj.mesh.faces.tobytes()).hexdigest()}
                for obj in objects]
    for obj in objects:
        obj.mesh.vertices.flags.writeable = False
        obj.mesh.faces.flags.writeable = False
    original_hashes = mesh_hashes()
    source_faces = sum(len(obj.mesh.faces) for obj in objects)
    assert source_faces > MAX_EXACT_FACES, 'Synthetic scene did not trigger the global preview budget'
    requested_gap = .12345678901234567
    report = check_overlaps(objects, min_gap_mm=requested_gap)
    assert report['status'] == 'inconclusive' and report['complete'] is False
    assert report['placement_status'] == 'clear' and report['placement_complete'] is True
    assert report['processing_completed'] and not report['cancelled'] and not report['violations']
    assert all(row['valid'] for row in report['objects'][:3])
    invalid_record = report['objects'][3]
    assert invalid_record['valid'] is False and invalid_record['source_triangles_finite_indexed'] is True
    assert invalid_record['topology_diagnostics']['degenerate_face_count'] > 0
    far_pairs = [pair for pair in report['pairs'] if pair['index_b'] == 3]
    assert len(far_pairs) == 3
    for pair in far_pairs:
        assert pair['status'] == 'inconclusive' and pair['distance_mm'] is None
        assert pair['placement_status'] == 'clear' and pair['placement_method'] == 'original-triangle-aabb-bound'
        assert pair['placement_distance_lower_bound_mm'] > requested_gap
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    report_digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    json_path = directory / 'raw-report.json'
    json_path.write_text(raw, encoding='utf-8')
    cache_keys = [set(obj.mesh._cache.cache) for obj in objects]
    html_path = directory / 'preview.html'
    write_html_report(objects, report, html_path)
    assert mesh_hashes() == original_hashes, 'HTML preview changed original mesh arrays'
    assert all(not obj.mesh.vertices.flags.writeable and not obj.mesh.faces.flags.writeable for obj in objects)
    assert [set(obj.mesh._cache.cache) for obj in objects] == cache_keys, 'HTML preview changed original mesh caches'
    assert hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True,
        allow_nan=False).encode('utf-8')).hexdigest() == report_digest, 'HTML preview changed the raw report'
    page = html_path.read_text(encoding='utf-8')
    match = re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S)
    assert match, 'Frozen HTML has no inspection payload'
    payload = json.loads(match.group(1))
    assert payload['report'] == report == json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['report']['min_gap_mm'] == requested_gap != float(format_number(requested_gap))
    preview = payload['preview']
    assert payload['object_count'] == len(objects) == len(payload['traces']) == len(preview['objects'])
    assert not payload['skipped'] and preview['uses_bounds_proxies'] is False, 'Frozen preview fell back to bounds'
    assert all(trace['type'] == 'mesh3d' for trace in payload['traces'])
    assert preview['displayed_faces'] <= MAX_EXACT_FACES
    assert preview['displayed_vertices'] <= MAX_EXACT_VERTICES
    approximate = [item for item in preview['objects'] if item['mode'] == 'approximate']
    assert len(approximate) == 3, 'All three dense closed meshes must execute simplification'
    for item in approximate:
        proof = item['approximation']
        assert item['method'] == proof['method'] == 'vtkQuadricDecimation-volume-preserving'
        assert proof['topology_invariants_checked'] is True
        assert proof['source_boundary_edges'] == proof['display_boundary_edges'] == 0
        assert proof['source_euler'] == proof['display_euler'] == 2
        assert proof['source_components'] == proof['display_components'] == 1
        assert item['displayed_faces'] > 0 and item['displayed_vertices'] > 0
    assert preview['objects'][3]['mode'] == 'exact'
    for trace in payload['traces']:
        for axis in 'xyz':
            assert '%{' + axis + ':.2f}' in trace['hovertemplate']
        assert trace['colorbar']['tickformat'] == '.2f'
    own_script = page.rsplit('<script>', 1)[1].split('</script>', 1)[0]
    for contract in ('value.toFixed(2)', 'value.toExponential(2)', 'function formatLowerBound(value)',
                     'new DataView', 'BigInt', 'numerator/denominator', 'units-=1n',
                     'tickformat:".2f"', 'hoverformat:".2f"', '!Number.isFinite(pair.distance_mm)'):
        assert contract in own_script, 'Frozen numeric HTML contract is missing: ' + contract
    assert '근사 미리보기' in page and '형상 미검증' in own_script
    measured_gap = report['minimum_distance_mm']
    assert measured_gap > requested_gap and measured_gap != float(format_number(measured_gap))
    assert format_number(requested_gap) == '0.12'
    assert format_number(.000123) == '1.23e-04'
    floor_values = []
    for value in (.29, .58, far_pairs[0]['placement_distance_lower_bound_mm']):
        rendered = format_lower_bound(value)
        assert Decimal(rendered) <= Decimal.from_float(value), 'Displayed lower bound was strengthened'
        floor_values.append({'raw': value, 'display': rendered})
    assert floor_values[0]['display'] == '0.28' and floor_values[1]['display'] == '0.57'

    # The invalid box above stays exact, so it cannot prove the open Pro path.
    # Exercise that packaged adapter separately with two open boundary rings.
    angles = np.arange(32) * (2 * np.pi / 32)
    vertices = np.array([np.column_stack((np.cos(angles), np.sin(angles), np.full(32, z)))
                         for z in range(16)]).reshape((-1, 3))
    faces = []
    for ring in range(15):
        for edge in range(32):
            a, b = ring * 32 + edge, ring * 32 + (edge + 1) % 32
            faces.extend(((a, b, a + 32), (b, b + 32, a + 32)))
    faces = np.asarray(faces, dtype=np.int64)
    vertices.flags.writeable = faces.flags.writeable = False
    open_before = [hashlib.sha256(values.tobytes()).hexdigest() for values in (vertices, faces)]
    out_vertices, out_faces, open_proof = simplify(vertices, faces, 300, 200)
    assert open_proof['method'] == 'vtkDecimatePro-boundary-preserving'
    assert open_proof['topology_invariants_checked'] is True
    assert open_proof['source_boundary_edges'] == open_proof['display_boundary_edges'] == 64
    assert open_proof['source_euler'] == open_proof['display_euler'] == 0
    assert open_proof['source_components'] == open_proof['display_components'] == 1
    assert 0 < len(out_faces) <= 300 and 0 < len(out_vertices) <= 200
    assert [hashlib.sha256(values.tobytes()).hexdigest() for values in (vertices, faces)] == open_before
    return {'passed': True, 'frozen': bool(getattr(sys, 'frozen', False)),
        'scope': 'Generated synthetic meshes; VTK and inspection/viewer functions execute in the current process',
        'source_faces': source_faces, 'object_count': len(objects), 'shape_traces': len(payload['traces']),
        'approximate_meshes': len(approximate), 'bounds_proxies': 0,
        'displayed_faces': preview['displayed_faces'], 'displayed_vertices': preview['displayed_vertices'],
        'strict_status': report['status'], 'strict_complete': report['complete'],
        'placement_status': report['placement_status'], 'placement_complete': report['placement_complete'],
        'raw_report_sha256': report_digest, 'mesh_sha256': original_hashes,
        'source_geometry_and_report_unchanged': True, 'raw_json_precision_preserved': True,
        'html_sha256': hashlib.sha256(html_path.read_bytes()).hexdigest(),
        'closed_proofs': [item['approximation'] for item in approximate], 'open_proof': open_proof,
        'number_display': {'requested_gap_raw': requested_gap, 'requested_gap_display': format_number(requested_gap),
            'measured_gap_raw': measured_gap, 'measured_gap_display': format_number(measured_gap), 'lower_bounds': floor_values,
            'html_contract_checked': True, 'browser_javascript_executed': False,
            'javascript_execution_gate': 'Mandatory source Node tests plus frozen/source byte parity'}}


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
        assert len(app.controllers) == 11
        assert app.specs['m2_converter']['label'] == '이미지 변환'
        assert app.specs['thermal']['label'] == '오븐 열확산 시뮬레이터'
        assert not any('하나의 공간' in text or '11가지 작업 도구' in text
                       for text in _captions(app.home))
        checks['all_11_tools_and_simple_titles'] = {'passed': True}
        if mode == 'save':
            assert not settings.exists(), 'A fresh verification profile is required'
            checks['native_image_custom_settings'] = _native_image_check(work)
            checks['chitu_placement_preview'] = _chitu_placement_preview_check(work)
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
            checks['fresh_process_all_11_inputs_restored'] = {'passed': True,
                'tools': sorted(actual), 'oven_language': 'en'}
        else:
            raise ValueError('Unknown feature verification phase: ' + mode)
        assert not app.settings_warnings, app.settings_warnings
        app.request_close(confirm=False)
        assert not app.close_errors, app.close_errors
        assert settings.is_file(), 'Normal close did not save the input profile'
        checks['normal_close_persisted'] = {'passed': True}
        checks['callback_errors'] = {'passed': not errors, 'errors': errors}
        if mode == 'save':
            assert checks.get('chitu_placement_preview', {}).get('passed') is True, 'Required chitu preview check is absent or failed'
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
