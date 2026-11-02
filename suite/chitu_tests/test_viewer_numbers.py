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


def run_ui(report):
    runtime = viewer._HTML.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = """
const data=JSON.parse(require('fs').readFileSync(0,'utf8'));
const elements={};
const get=id=>elements[id]||(elements[id]={textContent:'',value:'',checked:true,hidden:true,
  children:[],classList:{add(){}},appendChild(obj){this.children.push(obj)},addEventListener(){}});
get('viewer-data').textContent=JSON.stringify(data);
global.document={getElementById:get,createElement:()=>({})};
let layout;
global.Plotly={restyle(){},relayout(){},newPlot(plot,traces,options){layout=options;return {then(cb){cb()}}}};
"""
    harness += runtime
    harness += "\nprocess.stdout.write(JSON.stringify({status:get('status').textContent,summary:get('summary').textContent,details:get('details').textContent,pairs:get('pair').children.length,layout}));"
    data = {"report": report, "traces": [], "skipped": [], "object_count": 2, "preview": {"objects": []}}
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
