"""The product measures unsigned surface gaps without manufacturing validation."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import trimesh

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE / 'sources'))
import chitu_checker as app


def surface_report():
    pair = {'index_a': 7, 'index_b': 8, 'object_a': 'mildo_cup', 'object_b': 'neighbor',
            'status': 'surface_gap_satisfied', 'distance_mm': 4.58727123456789,
            'distance_is_exact': True, 'aabb_relation': 'overlapping', 'error': None,
            'nearest_points_mm': [[.123456789, 0., 0.], [4.71072802356789, 0., 0.]]}
    return {'inspection_scope': 'surface-clearance', 'status': 'surface_gap_satisfied', 'complete': True,
            'geometry_validation_performed': False, 'volume_overlap_evaluated': False, 'containment_evaluated': False,
            'object_count': 2, 'total_pair_count': 1, 'min_gap_mm': .123456789, 'elapsed_seconds': 1.234567,
            'processing_completed': True, 'cancelled': False, 'violations': [], 'violation_found': False,
            'minimum_distance_mm': pair['distance_mm'], 'minimum_distance_pair': [7, 8],
            'closest_distance_is_global': True, 'pair_distances_complete': True, 'pairs': [pair],
            'objects': [{'index': i, 'name': name, 'query_available': True, 'query_error': None,
                         # Stale legacy fields must not turn into product warnings in this scope.
                         'valid': False, 'errors': ['not watertight'], 'normalization_error': 'legacy diagnostic'}
                        for i, name in ((7, 'mildo_cup'), (8, 'neighbor'))]}


def test_surface_ui_reports_actual_gap_without_legacy_quality_warnings():
    data = surface_report()
    before = copy.deepcopy(data)
    text = app._report_status_text(data)
    assert '표면 간격 조건 충족' in text and '전체 쌍 최소 표면 간격 4.59 mm' in text
    assert app.SURFACE_SCOPE_TEXT in text
    controller = SimpleNamespace(status_var=SimpleNamespace(set=Mock()), detail_var=SimpleNamespace(set=Mock()),
                                 progress_bar=Mock(), _populate_pairs=Mock())
    app.ChituCheckerApp._show_report(controller, app.InspectionResult([], data))
    details = controller.detail_var.set.call_args.args[0]
    for forbidden in ('형상 검증', '형상 미검증', 'PASS', 'not watertight', 'legacy diagnostic', '교차 부피'):
        assert forbidden not in text + details
    assert app.SURFACE_SCOPE_TEXT in details
    controller.tree = SimpleNamespace(selection=lambda: ('0',))
    controller._displayed_pairs = {'0': data['pairs'][0]}
    app.ChituCheckerApp._selection_changed(controller)
    selected = controller.detail_var.set.call_args.args[0]
    assert '간격 4.59 mm' in selected and '내부 포함 여부 미검사' in selected
    assert app.SURFACE_SCOPE_TEXT in selected and '형상' not in selected
    assert data == before


def test_surface_table_treats_success_as_clear_and_never_substitutes_a_bound():
    data = surface_report()
    failed = dict(data['pairs'][0], index_a=0, status='contact_or_intersection', distance_mm=0.)
    unknown = dict(data['pairs'][0], index_a=1, status='inconclusive', distance_mm=None,
                   distance_lower_bound_mm=3.4785022735595703)
    data['pairs'].extend((failed, unknown))
    tree = Mock()
    tree.get_children.return_value = []
    controller = SimpleNamespace(tree=tree, last_report=data, _displayed_pairs={},
                                 show_all_var=SimpleNamespace(get=lambda: False))
    app.ChituCheckerApp._populate_pairs(controller)
    rows = [call.kwargs['values'] for call in tree.insert.call_args_list]
    assert len(rows) == 2
    assert rows[0][0] == 'FAIL · 표면 접촉/교차' and rows[0][3] == '0.00'
    assert rows[1][0] == '거리 미판정' and rows[1][3] == '—'


def test_partial_surface_violation_remains_explicitly_incomplete():
    data = surface_report()
    failure = dict(data['pairs'][0], status='too_close', distance_mm=.123456789)
    unknown = dict(data['pairs'][0], index_a=0, status='inconclusive', distance_mm=None,
                   distance_is_exact=False, nearest_points_mm=None,
                   error='FCL query failed after surface preparation')
    data.update(status='violation', complete=False, pairs=[failure, unknown], violations=[failure],
                min_gap_mm=.2, total_pair_count=2, closest_distance_is_global=False, minimum_distance_mm=.123456789)
    before = copy.deepcopy(data)
    text = app._report_status_text(data)
    assert text.startswith('FAIL · 표면 간격 위반 · 검사 미완료')
    assert '거리 측정 1/2쌍' in text and '측정된 최소 표면 간격 0.12 mm' in text
    controller = SimpleNamespace(status_var=SimpleNamespace(set=Mock()), detail_var=SimpleNamespace(set=Mock()),
                                 progress_bar=Mock(), _populate_pairs=Mock())
    app.ChituCheckerApp._show_report(controller, app.InspectionResult([], data))
    assert '검사 미완료' in controller.status_var.set.call_args.args[0]
    assert data == before


def test_cli_rounding_note_explains_equal_display_at_a_failing_threshold(capsys):
    from chitu_overlap.cli import _print_summary
    data = surface_report()
    pair = data['pairs'][0]
    pair['status'] = 'too_close'
    data.update(status='violation', min_gap_mm=4.59, violations=[pair], violation_found=True)
    before = copy.deepcopy(data)
    _print_summary(data)
    printed = capsys.readouterr()
    assert app.DISTANCE_DISPLAY_NOTE in printed.out
    assert 'FAIL' in printed.out and '표면 간격 4.59 mm' in printed.out
    assert data == before and not printed.err


def test_new_controller_defaults_to_all_surface_distances_and_keeps_filter_choice():
    try:
        root = app.create_root()
    except app.tk.TclError:
        pytest.skip('Tk display is unavailable')
    root.withdraw()
    controller = app.ChituCheckerApp(root)
    try:
        def captions(widget):
            try:
                yield str(widget.cget('text'))
            except app.tk.TclError:
                pass
            for child in widget.winfo_children():
                yield from captions(child)
        assert any(app.DISTANCE_DISPLAY_NOTE in text for text in captions(root))
        assert controller.show_all_var.get() is True
        controller._show_report(app.InspectionResult([], surface_report()))
        rows = controller.tree.get_children()
        assert len(rows) == 1 and controller.tree.item(rows[0], 'values')[3] == '4.59'
        controller.show_all_var.set(False)
        controller._populate_pairs()
        assert controller.tree.get_children() == ()
        controller.show_all_var.set(True)
        controller._populate_pairs()
        assert len(controller.tree.get_children()) == 1
    finally:
        controller.close()


def test_ui_and_cli_never_call_solid_validation_for_open_nonmanifold_inputs(tmp_path, monkeypatch, capsys):
    from chitu_overlap import cli, geometry, inputs
    first, second = trimesh.creation.box(), trimesh.creation.box()
    first = trimesh.Trimesh(vertices=first.vertices.copy(), faces=first.faces[:-2], process=False)
    second = trimesh.Trimesh(vertices=second.vertices.copy(),
                            faces=np.vstack((second.faces, second.faces[0])), process=False)
    second.apply_translation((5.43210987654321, 0., 0.))
    paths = [tmp_path / 'open.stl', tmp_path / 'nonmanifold.stl']
    for mesh, path in zip((first, second), paths):
        mesh.export(path)
    before = [path.read_bytes() for path in paths]
    def forbidden(*args, **kwargs):
        raise AssertionError('Solid validation entered the surface product path')
    for name in ('check_overlaps', '_validate_object', '_strict_topology', '_manifold'):
        monkeypatch.setattr(geometry, name, forbidden)
    for name in ('is_watertight', 'is_winding_consistent', 'is_volume', 'volume'):
        monkeypatch.setattr(trimesh.Trimesh, name, property(forbidden))
    loader = inputs.load_objects
    received = []
    def load(paths, **kwargs):
        received.append(kwargs.get('preserve_surface'))
        return loader(paths, **kwargs)
    monkeypatch.setattr(inputs, 'load_objects', load)
    monkeypatch.setattr(cli, 'load_objects', load)
    result = app.inspect_inputs(paths, .123456789)
    assert result.report['status'] == 'surface_gap_satisfied' and result.report['complete']
    assert all(row['query_available'] for row in result.report['objects'])
    assert result.report['pairs'][0]['distance_mm'] > 4
    output = tmp_path / 'surface.json'
    assert cli.main([*map(str, paths), '--json', str(output), '--min-gap-mm', '.123456789']) == 0
    raw = json.loads(output.read_text(encoding='utf-8'))
    assert raw['inspection_scope'] == 'surface-clearance'
    assert raw['geometry_validation_performed'] is False and raw['volume_overlap_evaluated'] is False
    assert raw['min_gap_mm'] == .123456789 and raw['pairs'][0]['distance_is_exact'] is True
    assert 'intersection_volume' not in raw['pairs'][0] and 'overlap_found' not in raw
    assert received == [True, True] and [path.read_bytes() for path in paths] == before
    printed = capsys.readouterr()
    assert app.SURFACE_SCOPE_TEXT in printed.out
    assert '표면 간격 조건 충족' in printed.out and not printed.err
    for forbidden_text in ('형상 검증', '형상 미검증', '비다양체', 'PASS', '겹침·접촉 없음', '교차 부피'):
        assert forbidden_text not in printed.out


def test_nested_positive_surface_gap_is_success_with_neutral_scope_note(tmp_path, capsys):
    from chitu_overlap.cli import main
    paths = [tmp_path / 'outer.stl', tmp_path / 'inner.stl']
    trimesh.creation.box(extents=(10, 10, 10)).export(paths[0])
    trimesh.creation.box().export(paths[1])
    output = tmp_path / 'nested.json'
    assert main([*map(str, paths), '--min-gap-mm', '1', '--json', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    pair = report['pairs'][0]
    assert report['status'] == pair['status'] == 'surface_gap_satisfied'
    assert report['complete'] and pair['distance_mm'] == pytest.approx(4.5)
    assert pair['aabb_relation'] == 'overlapping' and pair['containment_evaluated'] is False
    printed = capsys.readouterr()
    assert '내부 포함 여부 미검사' in printed.out and '표면 간격 조건 충족' in printed.out
    assert not printed.err
