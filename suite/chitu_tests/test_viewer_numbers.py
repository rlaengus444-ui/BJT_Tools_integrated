"""Run the offline viewer's numeric and placement presentation JavaScript."""
from decimal import Decimal, ROUND_FLOOR
import json
import re
import shutil
import subprocess

import numpy as np
import pytest

from chitu_overlap import viewer

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required to execute offline viewer JavaScript")


def test_two_decimal_numbers_keep_tiny_signals_and_floor_actual_binary_bounds():
    functions = re.search(r"  const format = value => \{(.*?)  const gapText", viewer._HTML, re.S)
    assert functions
    script = "  const format = value => {" + functions.group(1)
    ordinary = [1.234567, 0.0, -0.0, .000123, -1e-20, 1e25]
    bounds = [0.29, 0.58, 1.01, 1.02, np.nextafter(1.23, 0).item(), -.00001, 1e-300, 123.456789]
    script += "\nconst input=JSON.parse(require('fs').readFileSync(0,'utf8'));"
    script += "process.stdout.write(JSON.stringify({ordinary:input.ordinary.map(format),bounds:input.bounds.map(formatLowerBound)}));"
    result = subprocess.run([NODE, "-e", script], input=json.dumps({"ordinary": ordinary, "bounds": bounds}),
                            text=True, capture_output=True, check=True)
    output = json.loads(result.stdout)
    assert output["ordinary"] == ["1.23", "0.00", "0.00", "1.23e-4", "-1.00e-20", "1.00e+25"]
    expected = [f"{Decimal.from_float(value).quantize(Decimal('.01'), rounding=ROUND_FLOOR):.2f}" for value in bounds]
    assert output["bounds"] == expected
    for value, rendered in zip(bounds, output["bounds"]):
        assert Decimal(rendered) <= Decimal.from_float(value)


def run_ui(report, traces=None):
    runtime = viewer._HTML.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = """
const data=JSON.parse(require('fs').readFileSync(0,'utf8'));
const elements={};
const get=id=>elements[id]||(elements[id]={textContent:'',value:'',checked:true,hidden:true,
  children:[],classList:{add(){}},appendChild(obj){this.children.push(obj)},addEventListener(){}});
get('viewer-data').textContent=JSON.stringify(data);
global.document={getElementById:get,createElement:()=>({})};
let layout;const styles=[];
global.Plotly={restyle(plot,update,indices){styles.push({update,indices})},relayout(){},newPlot(plot,traces,options){layout=options;return {then(cb){cb()}}}};
"""
    harness += runtime
    harness += "\nprocess.stdout.write(JSON.stringify({status:get('status').textContent,summary:get('summary').textContent,details:get('details').textContent,scope:get('scope').textContent,warning:get('warning').textContent,pairs:get('pair').children.length,layout,styles}));"
    data = {"report": report, "traces": traces or [], "skipped": [], "object_count": 2, "preview": {"objects": []}}
    result = subprocess.run([NODE, "-e", harness], input=json.dumps(data), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_additive_placement_clear_never_relabels_incomplete_shape_as_pass():
    pair = {"index_a": 0, "index_b": 1, "object_a": "cup", "object_b": "box", "status": "inconclusive",
            "placement_status": "clear", "placement_method": "original-triangle-aabb-bound",
            "placement_distance_lower_bound_mm": .29}
    report = {"status": "inconclusive", "complete": False, "min_gap_mm": .1, "violations": [],
              "placement_status": "clear", "placement_complete": True, "selected_pair": [0, 1],
              "pairs": [pair], "objects": [{"index": 0, "name": "cup", "valid": False}]}
    output = run_ui(report)
    assert output["status"] == "판정 불가"
    assert "배치 분리 확인 · 형상 미검증" in output["summary"]
    assert "형상 판정: 판정 불가" in output["details"]
    assert "≥ 0.28 mm (보장 하한)" in output["details"]
    assert output["pairs"] == 1
    for axis in ("xaxis", "yaxis", "zaxis"):
        assert output["layout"]["scene"][axis]["tickformat"] == ".2f"
        assert output["layout"]["scene"][axis]["hoverformat"] == ".2f"
    for key in ("placement_status", "placement_complete"):
        legacy = {name: value for name, value in report.items() if name != key}
        assert "배치 분리 확인 · 형상 미검증" not in run_ui(legacy)["summary"]
    measured = dict(pair, status="clear", distance_mm=1.9782753934187156,
                    placement_distance_lower_bound_mm=0.)
    exact = run_ui(dict(report, status="clear", complete=True, pairs=[measured]))
    assert "간격: 1.98 mm" in exact["details"]
    assert "배치 분리 확인" in exact["details"]
    assert "≥ 0.00" not in exact["details"]


def test_cancelled_or_conflicting_placement_claim_stays_conservative():
    base = {"status": "inconclusive", "complete": False, "violations": [], "objects": [],
            "placement_status": "clear", "placement_complete": True}
    cancelled = run_ui(dict(base, cancelled=True))
    assert "배치 분리 확인" not in cancelled["summary"]
    assert cancelled["status"] == "판정 불가"
    conflict = run_ui(dict(base, status="violation", complete=True))
    assert conflict["status"] == "불합격"
    assert "배치 분리 확인" not in conflict["summary"]


def test_surface_report_uses_actual_distance_and_ignores_legacy_quality_flags():
    pair = {"index_a": 7, "index_b": 8, "object_a": "mildo_cup", "object_b": "neighbor",
            "status": "surface_gap_satisfied", "distance_mm": 4.58727123456789,
            "aabb_relation": "overlapping", "intersection_volume": 0.,
            "nearest_points_mm": [[.123456789, 0., 0.], [4.71072802356789, 0., 0.]]}
    report = {"inspection_scope": "surface-clearance", "status": "surface_gap_satisfied", "complete": True,
              "min_gap_mm": .123456789, "total_pair_count": 1, "selected_pair": [7, 8],
              "pairs": [pair], "violations": [],
              "objects": [{"index": i, "name": str(i), "query_available": True, "query_error": None,
                           "valid": False, "errors": ["not watertight"], "normalization": True} for i in (7, 8)]}
    output = run_ui(report)
    assert output["status"] == "표면 간격 조건 충족"
    assert "실제 거리 측정 1/1쌍" in output["summary"]
    assert "간격: 4.59 mm" in output["details"] and "내부 포함 여부 미검사" in output["details"]
    assert output["scope"].startswith("삼각형 표면 간격 검사; 부피 겹침·완전 내부 포함은 검사하지 않음")
    assert "수치는 소수 둘째 자리로 표시하며, 판정은 반올림 전 거리로 합니다." in output["scope"]
    assert output["warning"] == ""
    for text in (output["status"], output["summary"], output["details"]):
        assert "형상 미검증" not in text and "교차 부피" not in text and "PASS" not in text
    assert output["styles"][-1]["update"]["x"] == [[.123456789, 4.71072802356789]]
    all_view = {key: value for key, value in report.items() if key != "selected_pair"}
    traces = [{"type": "mesh3d", "meta": {"object_index": i}, "x": [0.], "y": [0.], "z": [0.]} for i in (7, 8)]
    rendered = run_ui(all_view, traces)
    assert rendered["styles"][0]["update"]["color"] == ["#9caec0", "#9caec0"]
    threshold_pair = dict(pair, status="too_close")
    threshold = run_ui(dict(report, status="violation", min_gap_mm=4.59,
                            pairs=[threshold_pair], violations=[threshold_pair]))
    assert threshold["status"].startswith("FAIL") and "요구 최소 간격 4.59 mm" in threshold["summary"]
    assert "간격: 4.59 mm" in threshold["details"] and "반올림 전 거리" in threshold["scope"]


def test_surface_missing_query_is_unknown_and_never_displays_aabb_bound_as_distance():
    pair = {"index_a": 0, "index_b": 1, "object_a": "a", "object_b": "b", "status": "inconclusive",
            "distance_mm": None, "distance_lower_bound_mm": 3.47, "aabb_relation": "disjoint"}
    report = {"inspection_scope": "surface-clearance", "status": "inconclusive", "complete": False,
              "min_gap_mm": 0., "selected_pair": [0, 1], "pairs": [pair], "violations": [],
              "objects": [{"index": 0, "name": "a", "query_available": False, "query_error": "No triangles available"}]}
    output = run_ui(report)
    assert output["status"] == "표면 거리 미판정"
    assert "간격: 거리 미판정" in output["details"] and "3.47" not in output["details"]
    assert "No triangles available" in output["warning"]


def test_selected_surface_query_error_is_visible_as_plain_text(tmp_path):
    error = 'FCL query failed </script><img src=x onerror="alert(1)"> & detail'
    pair = {"index_a": 0, "index_b": 1, "object_a": "a", "object_b": "b", "status": "inconclusive",
            "distance_mm": None, "aabb_relation": "disjoint", "error": error}
    report = {"inspection_scope": "surface-clearance", "status": "inconclusive", "complete": False,
              "min_gap_mm": .123456789, "selected_pair": [0, 1], "pairs": [pair], "violations": [],
              "objects": [{"index": i, "name": str(i), "query_available": True, "query_error": None} for i in (0, 1)]}
    output = run_ui(report)
    assert "거리 계산 오류: " + error in output["details"]
    assert "거리 미판정" in output["details"]
    path = tmp_path / 'query-error.html'
    viewer.write_html_report([], report, path)
    page = path.read_text(encoding='utf-8')
    assert error not in page  # The JSON script cannot be terminated by error text.
    payload = json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert payload['report']['pairs'][0]['error'] == error
    assert payload['report']['min_gap_mm'] == .123456789
