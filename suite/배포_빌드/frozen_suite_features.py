"""Exercise suite inputs and UI in two independent frozen application processes.

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


def _chitu_surface_distance_preview_check(work):
    """Exercise packaged surface distances and shape previews with synthetic data.

    The frozen executable runs the real distance backend and both VTK adapters.
    Browser JavaScript execution remains a separate mandatory source test, with
    source/frozen byte parity; this helper checks the bundled HTML contract.
    """
    from contextlib import ExitStack
    from decimal import Decimal
    import importlib
    import re
    import sys
    from unittest.mock import patch
    import numpy as np
    import trimesh
    from chitu_overlap.display_preview import simplify
    from chitu_overlap.geometry import MeshObject
    from chitu_overlap.surface import check_surface_clearances
    from chitu_overlap.viewer import MAX_EXACT_FACES, MAX_EXACT_VERTICES, write_html_report
    from display_numbers import format_lower_bound, format_number

    directory = work / 'synthetic-chitu-surface-distance-preview'
    directory.mkdir()
    legacy_modules = [importlib.import_module('chitu_overlap.' + name)
                      for name in ('geometry', 'surface', 'cli', 'seams')]
    forbidden = ('check_overlaps', '_validate_object', '_strict_topology', '_manifold',
                 'exact_collinear_seam_repair')
    blocked_checks = set()

    def inspect(objects, gap):
        # A caught legacy failure must fail this gate too, so check call counts
        # after the entry returns instead of relying on its exception policy.
        with ExitStack() as stack:
            guards = []
            for module in legacy_modules:
                for name in forbidden:
                    if hasattr(module, name):
                        label = module.__name__ + '.' + name
                        guard = stack.enter_context(patch.object(module, name,
                            side_effect=AssertionError('Legacy shape validation called: ' + label)))
                        guards.append((label, guard))
                        blocked_checks.add(label)
            result = check_surface_clearances(objects, min_gap_mm=gap)
            for label, guard in guards:
                assert guard.call_count == 0, 'Legacy shape validation called: ' + label
        assert result['inspection_scope'] == 'surface-clearance'
        assert result['geometry_validation_performed'] is False
        assert result['volume_overlap_evaluated'] is False
        assert result['containment_evaluated'] is False
        # No surface-only result may silently assert absence of volume overlap.
        for record in [result, *result['pairs']]:
            for key in ('overlap_found', 'overlaps', 'intersection_volume',
                        'intersection_volume_mm3', 'overlap_volume_mm3'):
                assert key not in record or record[key] is None, 'Surface report made a volume claim: ' + key
        return result

    def snapshot(objects):
        def value_digest(value):
            if isinstance(value, np.ndarray):
                return ('array', str(value.dtype), value.shape, value.strides,
                        value.flags.writeable, hashlib.sha256(value.tobytes()).hexdigest())
            if isinstance(value, dict):
                return {str(key): value_digest(part) for key, part in value.items()}
            if isinstance(value, (tuple, list)):
                return [value_digest(part) for part in value]
            return repr(value)
        return [{'vertices': hashlib.sha256(obj.mesh.vertices.tobytes()).hexdigest(),
                 'faces': hashlib.sha256(obj.mesh.faces.tobytes()).hexdigest(),
                 'vertices_writeable': obj.mesh.vertices.flags.writeable,
                 'faces_writeable': obj.mesh.faces.flags.writeable,
                 'cache': value_digest(obj.mesh._cache.cache)} for obj in objects]

    def measured_pair(result):
        assert result['processing_completed'] and not result['cancelled']
        assert result['complete'] and result['pair_distances_complete']
        assert result['closest_distance_is_global']
        assert len(result['pairs']) == 1
        pair = result['pairs'][0]
        assert pair['distance_is_exact'] is True
        assert np.isfinite(pair['distance_mm']) and pair['distance_mm'] >= 0
        assert pair['containment_evaluated'] is False
        return pair

    controls = {}
    base = trimesh.creation.box(extents=(2., 2., 2.))
    for name, offset, expected_status, expected_distance in (
            ('short_gap', 2.5, 'too_close', .5),
            ('exact_touch', 2., 'contact_or_intersection', 0.)):
        second = base.copy()
        second.apply_translation((offset, 0., 0.))
        result = inspect([MeshObject(name + ' A', base), MeshObject(name + ' B', second)], 1.)
        pair = measured_pair(result)
        assert result['status'] == 'violation' and result['violation_found']
        assert pair['status'] == expected_status and abs(pair['distance_mm'] - expected_distance) <= 1e-8
        controls[name] = {'status': result['status'], 'pair_status': pair['status'],
                          'distance_mm': pair['distance_mm']}
    triangles = [trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2]], process=False)
                 for vertices in (((-1., -1., 0.), (1., -1., 0.), (0., 1., 0.)),
                                  ((0., -.5, -1.), (0., -.5, 1.), (0., 1., 0.)))]
    result = inspect([MeshObject('intersecting triangle ' + str(i), mesh)
                      for i, mesh in enumerate(triangles)], 1.)
    pair = measured_pair(result)
    assert result['status'] == 'violation' and pair['status'] == 'contact_or_intersection'
    assert pair['distance_mm'] <= result['distance_tolerance_mm']
    controls['triangle_intersection'] = {'status': result['status'], 'pair_status': pair['status'],
                                         'distance_mm': pair['distance_mm']}
    outer, inner = trimesh.creation.box(extents=(4., 4., 4.)), trimesh.creation.box()
    result = inspect([MeshObject('outer box', outer), MeshObject('inner box', inner)], 1.)
    pair = measured_pair(result)
    assert result['status'] == pair['status'] == 'surface_gap_satisfied'
    assert not result['violation_found'] and abs(pair['distance_mm'] - 1.5) <= 1e-8
    assert pair['aabb_relation'] == 'overlapping', 'Containment scope warning metadata was lost'
    controls['contained_boxes'] = {'status': result['status'], 'pair_status': pair['status'],
        'distance_mm': pair['distance_mm'], 'aabb_relation': pair['aabb_relation'],
        'volume_overlap_evaluated': result['volume_overlap_evaluated'],
        'containment_evaluated': pair['containment_evaluated']}
    accepted_surfaces = []
    # Keep the closest +X surface for the open case. A duplicate face creates
    # nonmanifold incidence, and reversed faces change winding without moving it.
    back_face = int(np.argmin(np.asarray(base.vertices)[np.asarray(base.faces)].mean(axis=1)[:, 0]))
    for name, faces in (
            ('open', np.delete(base.faces, back_face, axis=0)),
            ('nonmanifold', np.vstack((base.faces, base.faces[back_face]))),
            ('reversed_winding', np.asarray(base.faces)[:, ::-1])):
        source = trimesh.Trimesh(vertices=base.vertices.copy(), faces=faces.copy(), process=False)
        second = base.copy()
        second.apply_translation((2.5, 0., 0.))
        objects = [MeshObject(name, source), MeshObject('separated reference', second)]
        for obj in objects:
            obj.mesh.vertices.flags.writeable = obj.mesh.faces.flags.writeable = False
        before = snapshot(objects)
        result = inspect(objects, .25)
        pair = measured_pair(result)
        assert result['status'] == pair['status'] == 'surface_gap_satisfied'
        assert abs(pair['distance_mm'] - .5) <= 1e-8
        assert all(row['query_available'] for row in result['objects'])
        assert snapshot(objects) == before, 'Surface query changed original mesh arrays, flags or caches'
        accepted_surfaces.append(name)

    objects = []
    for index in range(3):
        mesh = trimesh.creation.icosphere(subdivisions=6)
        mesh.apply_translation((index * 5.43210987654321, .234567890123456, 0.))
        objects.append(MeshObject('synthetic closed sphere ' + str(index), mesh))
    open_box = trimesh.creation.box()
    # This degenerate segment is wholly covered by a retained triangle edge.
    # An isolated zero-area point is intentionally unsupported by the query.
    edge = np.asarray(open_box.faces[0, :2])
    midpoint = np.asarray(open_box.vertices)[edge].mean(axis=0)
    open_box = trimesh.Trimesh(vertices=np.vstack((open_box.vertices, midpoint)),
        faces=np.vstack((open_box.faces[:-1], [edge[0], edge[1], len(open_box.vertices)])), process=False)
    open_box.apply_translation((30.1234567890123, .234567890123456, 0.))
    objects.append(MeshObject('synthetic open box with a covered zero-area face', open_box))
    for obj in objects:
        obj.mesh.vertices.flags.writeable = obj.mesh.faces.flags.writeable = False
    original = snapshot(objects)
    source_faces = sum(len(obj.mesh.faces) for obj in objects)
    assert source_faces > MAX_EXACT_FACES, 'Synthetic scene did not trigger the global preview budget'
    requested_gap = .12345678901234567
    report = inspect(objects, requested_gap)
    assert report['status'] == 'surface_gap_satisfied' and report['complete']
    assert report['processing_completed'] and not report['cancelled'] and not report['violations']
    assert report['pair_distances_complete'] and report['closest_distance_is_global']
    assert len(report['pairs']) == report['total_pair_count'] == 6
    assert all(row['query_available'] for row in report['objects'])
    prepared = report['objects'][3]
    proof = prepared['preparation_proof']
    assert proof['removed_covered_degenerate_face_count'] == 1 and proof['coverage_proofs']
    assert proof['exact_surface_union_preserved'] and proof['coordinates_unchanged']
    assert prepared['raw_triangle_count'] - prepared['query_triangle_count'] == 1
    for pair in report['pairs']:
        assert pair['status'] == 'surface_gap_satisfied' and pair['distance_is_exact']
        assert np.isfinite(pair['distance_mm']) and pair['distance_mm'] > requested_gap
        assert pair['aabb_relation'] == 'disjoint'
        nearest = np.asarray(pair['nearest_points_mm'], dtype=float)
        assert nearest.shape == (2, 3) and np.isfinite(nearest).all()
        assert np.isclose(np.linalg.norm(nearest[1] - nearest[0]), pair['distance_mm'], rtol=1e-6, atol=1e-6)
    assert snapshot(objects) == original, 'Surface query changed original mesh arrays, flags or caches'
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    report_digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    json_path = directory / 'raw-report.json'
    json_path.write_text(raw, encoding='utf-8')
    html_path = directory / 'preview.html'
    write_html_report(objects, report, html_path)
    assert snapshot(objects) == original, 'HTML preview changed original mesh arrays, flags or caches'
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
                     'tickformat:".2f"', 'hoverformat:".2f"', 'surface-clearance',
                     '표면 간격 조건 충족', '부피 겹침·완전 내부 포함은 검사하지 않음', '내부 포함 여부 미검사'):
        assert contract in own_script, 'Frozen numeric/surface HTML contract is missing: ' + contract
    assert '근사 미리보기' in page
    measured_gap = report['minimum_distance_mm']
    assert measured_gap > requested_gap and measured_gap != float(format_number(measured_gap))
    assert format_number(requested_gap) == '0.12'
    assert format_number(.000123) == '1.23e-04'
    floor_values = []
    for value in (.29, .58, measured_gap):
        rendered = format_lower_bound(value)
        assert Decimal(rendered) <= Decimal.from_float(value), 'Displayed lower bound was strengthened'
        floor_values.append({'raw': value, 'display': rendered})
    assert floor_values[0]['display'] == '0.28' and floor_values[1]['display'] == '0.57'

    # Exercise the packaged open-surface display adapter with two boundary rings.
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
    assert not vertices.flags.writeable and not faces.flags.writeable
    return {'passed': True, 'frozen': bool(getattr(sys, 'frozen', False)),
        'scope': 'Generated synthetic meshes; original-triangle surface distances and display-only VTK execute in this process',
        'inspection_scope': report['inspection_scope'], 'geometry_validation_performed': False,
        'volume_overlap_evaluated': False, 'containment_evaluated': False,
        'surface_status': report['status'], 'surface_complete': report['complete'],
        'pair_count': len(report['pairs']), 'pair_distances_complete': report['pair_distances_complete'],
        'all_pair_distances_finite': True, 'covered_degenerate_query_preparation': prepared['preparation_proof'],
        'legacy_shape_validation_called': False, 'blocked_legacy_functions': sorted(blocked_checks),
        'controlled_surface_cases': controls, 'accepted_surface_inputs': accepted_surfaces,
        'source_faces': source_faces, 'object_count': len(objects), 'shape_traces': len(payload['traces']),
        'approximate_meshes': len(approximate), 'bounds_proxies': 0,
        'displayed_faces': preview['displayed_faces'], 'displayed_vertices': preview['displayed_vertices'],
        'raw_report_sha256': report_digest, 'mesh_sha256': [
            {'vertices': item['vertices'], 'faces': item['faces']} for item in original],
        'source_geometry_and_report_unchanged': True, 'source_caches_and_flags_unchanged': True,
        'raw_json_precision_preserved': True, 'html_sha256': hashlib.sha256(html_path.read_bytes()).hexdigest(),
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
            checks['chitu_surface_distance_preview'] = _chitu_surface_distance_preview_check(work)
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
            assert checks.get('chitu_surface_distance_preview', {}).get('passed') is True, 'Required chitu surface-distance preview check is absent or failed'
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
