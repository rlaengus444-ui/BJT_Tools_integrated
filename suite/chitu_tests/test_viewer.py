import json
import re

import trimesh

from chitu_overlap.geometry import MeshObject
from chitu_overlap.viewer import write_html_report


def payload_from(page):
    return json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S).group(1))


def test_offline_report_preserves_geometry_pair_and_safe_names(tmp_path):
    mesh=trimesh.creation.box()
    mesh.apply_translation((7, -3, 2))
    unsafe='cube</script><script>alert("x")</script>'
    pair={"index_a":0,"index_b":1,"object_a":unsafe,"object_b":"cube", "status":"too_close","distance_mm":.2,"intersection_volume":0,"nearest_points_mm":[[7.5,-3,2],[7.7,-3,2]]}
    report={"status":"violation","complete":True,"min_gap_mm":1,"violations":[pair],"objects":[{"index":0,"name":unsafe,"valid":True,"errors":[]},{"index":1,"name":"cube","valid":True,"errors":[]}]}
    output=tmp_path / "report.html"
    write_html_report([MeshObject(unsafe,mesh),MeshObject("cube",mesh.copy())], report, output)
    page=output.read_text(encoding="utf-8")
    payload=payload_from(page)
    assert payload["report"]["violations"][0]["object_a"]==unsafe
    assert payload["traces"][0]["x"]==mesh.vertices[:,0].tolist()
    assert payload["traces"][0]["i"]==mesh.faces[:,0].tolist()
    assert payload["traces"][1]["meta"]["object_index"]==1
    assert unsafe not in page
    assert '<script src=' not in page
    assert len(page)>1_000_000  # Plotly is bundled, not fetched from a CDN.
    assert "선택 쌍 확대" in page


def test_unrenderable_geometry_is_not_silently_dropped(tmp_path):
    report={"status":"inconclusive","complete":False,"min_gap_mm":0,"violations":[],"objects":[{"index":0,"name":"empty","valid":False,"errors":["Mesh is empty."]}]}
    output=tmp_path / "report.html"
    write_html_report([MeshObject("empty",trimesh.Trimesh())],report,output)
    payload=payload_from(output.read_text(encoding="utf-8"))
    assert payload["skipped"]==[{"index":0,"name":"empty"}]
    assert payload["report"]["complete"] is False
    assert payload["traces"]==[]


def test_loader_failure_can_be_reported_without_any_meshes(tmp_path):
    report={"status":"inconclusive","complete":False,"violation_found":None,"error":"Unsupported format"}
    output=tmp_path / "failed.html"
    write_html_report([],report,output)
    payload=payload_from(output.read_text(encoding="utf-8"))
    assert payload["object_count"]==0
    assert payload["traces"]==[]
    assert payload["report"]["error"]=="Unsupported format"
