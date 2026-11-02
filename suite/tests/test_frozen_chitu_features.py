"""Run the new synthetic frozen gate directly; no EXE or user project is needed."""
import importlib.util
import json
from pathlib import Path
import re

import pytest

BUILD = Path(__file__).resolve().parents[1] / '배포_빌드'
spec = importlib.util.spec_from_file_location('_test_frozen_chitu_features', BUILD / 'frozen_suite_features.py')
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)


def test_synthetic_frozen_gate_uses_both_actual_vtk_methods_and_preserves_raw_data(tmp_path):
    proof = features._chitu_placement_preview_check(tmp_path)
    assert proof['passed'] and proof['source_faces'] > 200_000
    assert proof['shape_traces'] == proof['object_count'] == 4
    assert proof['approximate_meshes'] == 3 and proof['bounds_proxies'] == 0
    assert proof['displayed_faces'] <= 200_000 and proof['displayed_vertices'] <= 120_000
    assert proof['source_geometry_and_report_unchanged'] and proof['raw_json_precision_preserved']
    assert proof['strict_status'] == 'inconclusive' and proof['strict_complete'] is False
    assert proof['placement_status'] == 'clear' and proof['placement_complete'] is True
    assert {item['method'] for item in proof['closed_proofs']} == {'vtkQuadricDecimation-volume-preserving'}
    assert proof['open_proof']['method'] == 'vtkDecimatePro-boundary-preserving'
    assert proof['number_display']['requested_gap_display'] == '0.12'
    assert proof['number_display']['browser_javascript_executed'] is False


@pytest.mark.parametrize('corruption', ['bounds_fallback', 'report_mutation'])
def test_synthetic_gate_rejects_fallback_or_raw_report_changes(tmp_path, monkeypatch, corruption):
    from chitu_overlap import viewer
    original = viewer.write_html_report
    def corrupt(objects, report, output):
        original(objects, report, output)
        if corruption == 'report_mutation':
            report['min_gap_mm'] = .12
        else:
            page = output.read_text(encoding='utf-8')
            match = re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S)
            payload = json.loads(match.group(1))
            payload['preview']['uses_bounds_proxies'] = True
            output.write_text(page[:match.start(1)] + json.dumps(payload) + page[match.end(1):], encoding='utf-8')
    monkeypatch.setattr(viewer, 'write_html_report', corrupt)
    reason = 'fell back to bounds' if corruption == 'bounds_fallback' else 'changed the raw report'
    with pytest.raises(AssertionError, match=reason):
        features._chitu_placement_preview_check(tmp_path)
