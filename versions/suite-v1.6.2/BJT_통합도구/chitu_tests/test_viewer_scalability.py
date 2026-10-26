import json
import re
from types import SimpleNamespace

import numpy as np
import trimesh

from chitu_overlap.geometry import MeshObject
from chitu_overlap import viewer


def payload_from(path):
    page = path.read_text(encoding="utf-8")
    value = re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S)
    return page, json.loads(value.group(1))


class ShapeOnly:
    """Prove an inspected large object's arrays are never materialized."""

    ndim = 2

    def __init__(self, rows):
        self.shape = (rows, 3)

    def __len__(self):
        return self.shape[0]

    def __array__(self, *args, **kwargs):
        raise AssertionError("The large proxy must use inspected bounds.")


def test_giant_object_uses_wire_bounds_without_materializing_arrays(tmp_path):
    giant = MeshObject("large", SimpleNamespace(vertices=ShapeOnly(18_000_000), faces=ShapeOnly(36_000_000)))
    report = {
        "status": "inconclusive", "complete": False, "min_gap_mm": 1,
        "objects": [{"index": 0, "name": "large", "valid": False,
                     "errors": ["Degenerate triangle"], "bounds": [[100, 200, 300], [150, 250, 350]]}],
        "violations": [],
    }
    target = tmp_path / "large.html"
    viewer.write_html_report([giant], report, target)
    page, payload = payload_from(target)
    trace = payload["traces"][0]
    assert trace["type"] == "scatter3d"
    assert trace["mode"] == "lines"
    assert trace["meta"]["object_index"] == 0
    assert trace["meta"]["bounds"] == report["objects"][0]["bounds"]
    assert len(trace["x"]) == 36
    assert payload["preview"]["objects"][0]["original_faces"] == 36_000_000
    assert payload["preview"]["uses_bounds_proxies"]
    assert payload["preview"]["exact_faces"] == 0
    assert "외곽박스로 표시합니다" in page
    assert target.stat().st_size < 7 * 1024 * 1024
    assert payload["report"]["complete"] is False


def test_global_budget_prioritizes_selected_failure_without_mutating_meshes(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer, "MAX_EXACT_FACES", 24)
    monkeypatch.setattr(viewer, "MAX_EXACT_VERTICES", 16)
    objects = [MeshObject(str(index), trimesh.creation.box()) for index in range(4)]
    originals = [(obj.mesh.vertices.copy(), obj.mesh.faces.copy(), set(obj.mesh._cache.cache)) for obj in objects]
    pair = {"index_a": 2, "index_b": 3, "object_a": "2", "object_b": "3",
            "status": "touching", "distance_mm": 0, "intersection_volume": 0}
    report = {"status": "violation", "complete": True, "min_gap_mm": 0,
              "selected_pair": [2, 3], "violations": [pair], "objects": []}
    target = tmp_path / "selected.html"
    viewer.write_html_report(objects, report, target)
    _, payload = payload_from(target)
    assert [trace["meta"]["object_index"] for trace in payload["traces"]] == [0, 1, 2, 3]
    assert [trace["type"] for trace in payload["traces"]] == ["scatter3d", "scatter3d", "mesh3d", "mesh3d"]
    assert payload["preview"]["exact_faces"] == 24
    assert payload["preview"]["exact_vertices"] == 16
    assert payload["report"]["selected_pair"] == [2, 3]
    for obj, (vertices, faces, cache) in zip(objects, originals):
        np.testing.assert_array_equal(obj.mesh.vertices, vertices)
        np.testing.assert_array_equal(obj.mesh.faces, faces)
        assert set(obj.mesh._cache.cache) == cache


def test_vertex_budget_also_prevents_oversized_json(tmp_path):
    mesh = SimpleNamespace(vertices=np.zeros((viewer.MAX_EXACT_VERTICES + 1, 3)),
                           faces=np.array([[0, 1, 2]], dtype=np.int64))
    report = {"status": "inconclusive", "complete": False, "min_gap_mm": 0,
              "objects": [{"index": 0, "name": "unused", "valid": False,
                           "errors": ["degenerate"], "bounds": [[0, 0, 0], [0, 0, 0]]}], "violations": []}
    target = tmp_path / "vertex-budget.html"
    viewer.write_html_report([MeshObject("unused", mesh)], report, target)
    _, payload = payload_from(target)
    assert payload["traces"][0]["type"] == "scatter3d"
    assert payload["preview"]["exact_vertices"] == 0


def test_small_read_only_mesh_keeps_exact_geometry_and_cache_empty(tmp_path):
    mesh = trimesh.creation.box()
    mesh.vertices.flags.writeable = False
    mesh.faces.flags.writeable = False
    cache = set(mesh._cache.cache)
    report = {"status": "clear", "complete": True, "min_gap_mm": 0, "violations": [], "objects": []}
    target = tmp_path / "exact.html"
    viewer.write_html_report([MeshObject("small", mesh)], report, target)
    _, payload = payload_from(target)
    trace = payload["traces"][0]
    assert trace["type"] == "mesh3d"
    assert trace["x"] == mesh.vertices[:, 0].tolist()
    assert trace["i"] == mesh.faces[:, 0].tolist()
    assert not payload["preview"]["uses_bounds_proxies"]
    assert set(mesh._cache.cache) == cache
    assert not mesh.vertices.flags.writeable and not mesh.faces.flags.writeable


def dense_display_mesh(vertex_count, face_count):
    index = np.arange(vertex_count, dtype=np.float64)
    vertices = np.column_stack((np.sin(index * .017) * 100,
                                np.cos(index * .013) * 100,
                                np.sin(index * .011) * 50))
    faces = np.arange(face_count * 3, dtype=np.int64).reshape((-1, 3)) % vertex_count
    return SimpleNamespace(vertices=vertices, faces=faces)


def test_ordinary_six_megabyte_scene_stays_exact(tmp_path):
    mesh = dense_display_mesh(83_000, 165_116)
    report = {"status": "clear", "complete": True, "min_gap_mm": 1, "violations": [], "objects": []}
    target = tmp_path / "ordinary.html"
    viewer.write_html_report([MeshObject("ordinary", mesh)], report, target)
    _, payload = payload_from(target)
    assert payload["traces"][0]["type"] == "mesh3d"
    assert payload["preview"]["exact_faces"] == 165_116
    assert not payload["preview"]["uses_bounds_proxies"]
    np.testing.assert_array_equal(payload["traces"][0]["i"], mesh.faces[:, 0])


def test_selected_180k_face_failure_stays_exact_with_wire_context(tmp_path):
    first = dense_display_mesh(90_165, 180_326)
    second = trimesh.creation.box()
    context = SimpleNamespace(vertices=ShapeOnly(43_000), faces=ShapeOnly(86_000))
    pair = {"index_a": 0, "index_b": 1, "object_a": "impeller", "object_b": "cube",
            "status": "touching", "distance_mm": 0, "intersection_volume": 0}
    report = {"status": "violation", "complete": True, "min_gap_mm": 1,
              "selected_pair": [0, 1], "violations": [pair],
              "objects": [{"index": 2, "name": "context", "valid": True,
                           "errors": [], "bounds": [[100, 100, 100], [200, 200, 200]]}]}
    target = tmp_path / "selected-180k.html"
    viewer.write_html_report([MeshObject("impeller", first), MeshObject("cube", second),
                              MeshObject("context", context)], report, target)
    _, payload = payload_from(target)
    assert [trace["type"] for trace in payload["traces"]] == ["mesh3d", "mesh3d", "scatter3d"]
    assert payload["preview"]["exact_faces"] == 180_338
    assert payload["preview"]["exact_vertices"] == 90_173
    np.testing.assert_array_equal(payload["traces"][0]["x"], first.vertices[:, 0])
    assert target.stat().st_size < 20 * 1024 * 1024


def test_maximum_budget_with_long_float_strings_fits_twenty_mib(tmp_path):
    values = np.full((120_000, 3), -1.2345678901234567e-200)
    faces = np.arange(200_000 * 3, dtype=np.int64).reshape((-1, 3)) % len(values)
    mesh = SimpleNamespace(vertices=values, faces=faces)
    report = {"status": "inconclusive", "complete": False, "min_gap_mm": 1,
              "violations": [], "objects": []}
    target = tmp_path / "maximum-budget.html"
    viewer.write_html_report([MeshObject("maximum", mesh)], report, target)
    _, payload = payload_from(target)
    assert payload["preview"]["exact_faces"] == 200_000
    assert payload["preview"]["exact_vertices"] == 120_000
    assert payload["traces"][0]["type"] == "mesh3d"
    assert target.stat().st_size < 20 * 1024 * 1024
