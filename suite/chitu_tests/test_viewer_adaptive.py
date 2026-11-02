"""Display simplification keeps whole surfaces, global limits and raw inspection data."""
import json
import re
import threading

import numpy as np
import pytest
import trimesh

from chitu_overlap import display_preview, viewer
from chitu_overlap.geometry import MeshObject


def payload_from(path):
    page = path.read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S).group(1))


def test_multiple_large_objects_all_get_complete_approximate_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer, "MAX_EXACT_FACES", 1_800)
    monkeypatch.setattr(viewer, "MAX_EXACT_VERTICES", 1_000)
    objects = [MeshObject(str(index), trimesh.creation.icosphere(subdivisions=4)) for index in range(3)]
    objects.append(MeshObject("small", trimesh.creation.box()))
    for index, obj in enumerate(objects):
        obj.mesh.apply_translation((index * 5.0, 0, 0))
        obj.mesh.vertices.flags.writeable = False
        obj.mesh.faces.flags.writeable = False
    originals = [(obj.mesh.vertices.copy(), obj.mesh.faces.copy(), set(obj.mesh._cache.cache)) for obj in objects]
    report = {"status": "inconclusive", "complete": False, "selected_pair": [0, 1],
              "violations": [], "min_gap_mm": .123456789, "objects": []}
    before = json.dumps(report, sort_keys=True)
    path = tmp_path / "adaptive.html"
    viewer.write_html_report(objects, report, path)
    payload = payload_from(path)
    assert [item["mode"] for item in payload["preview"]["objects"]] == ["approximate"] * 3 + ["exact"]
    assert payload["preview"]["displayed_faces"] <= 1_800
    assert payload["preview"]["displayed_vertices"] <= 1_000
    assert payload["preview"]["uses_bounds_proxies"] is False
    for index, trace in enumerate(payload["traces"][:3]):
        vertices = np.column_stack([trace[axis] for axis in "xyz"])
        faces = np.column_stack([trace[axis] for axis in "ijk"])
        reduced = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        assert reduced.is_watertight  # Dropping scattered source faces would leave holes.
        assert reduced.euler_number == 2
        # Quadric points approximate the surface; they retain its placement
        # frame without promising that every display point lies on the source.
        radii = np.linalg.norm(vertices - [index * 5., 0, 0], axis=1)
        assert np.all(radii > .98) and np.all(radii < 1.03)
        proof = payload["preview"]["objects"][index]["approximation"]
        assert proof["source_euler"] == proof["display_euler"] == 2
        assert proof["source_components"] == proof["display_components"] == 1
        assert proof["source_boundary_edges"] == proof["display_boundary_edges"] == 0
    small = payload["traces"][3]
    assert small["x"] == objects[3].mesh.vertices[:, 0].tolist()
    assert small["i"] == objects[3].mesh.faces[:, 0].tolist()
    assert json.dumps(report, sort_keys=True) == before
    assert payload["report"] == report
    for obj, (vertices, faces, cache) in zip(objects, originals):
        np.testing.assert_array_equal(obj.mesh.vertices, vertices)
        np.testing.assert_array_equal(obj.mesh.faces, faces)
        assert set(obj.mesh._cache.cache) == cache
        assert not obj.mesh.vertices.flags.writeable and not obj.mesh.faces.flags.writeable


def test_open_boundaries_survive_real_vtk_simplification():
    # A curved open tube has two boundary rings. Keep those rings while reducing
    # interior axial tessellation; normalization/restoration can round display
    # coordinates by a few ulps, and must never cap or tear the surface.
    angles = np.arange(32) * (2 * np.pi / 32)
    rings = np.array([np.column_stack((np.cos(angles), np.sin(angles), np.full(32, z))) for z in range(16)])
    vertices = rings.reshape((-1, 3))
    faces = []
    for ring in range(15):
        for edge in range(32):
            a, b = ring * 32 + edge, ring * 32 + (edge + 1) % 32
            faces.extend(((a, b, a + 32), (b, b + 32, a + 32)))
    faces = np.asarray(faces, dtype=np.int64)
    out_vertices, out_faces, proof = display_preview.simplify(vertices, faces, 300, 200)
    def boundary_points(v, f):
        edges = np.sort(np.concatenate((f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]])), axis=1)
        unique, counts = np.unique(edges, axis=0, return_counts=True)
        return {tuple(point) for point in v[np.unique(unique[counts == 1])]}
    assert len(out_faces) <= 300 and len(out_vertices) <= 200
    actual = np.asarray(sorted(boundary_points(out_vertices, out_faces)))
    expected = np.asarray(sorted(boundary_points(vertices, faces)))
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-14)
    assert proof["source_euler"] == proof["display_euler"]
    assert proof["source_components"] == proof["display_components"]


def test_local_normalization_preserves_display_detail_at_large_world_offset():
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=2.)
    offset = np.array([1e9, -1e9, 1e9])
    mesh.apply_translation(offset)
    original = mesh.vertices.copy()
    vertices, faces, proof = display_preview.simplify(mesh.vertices, mesh.faces, 400, 250)
    radii = np.linalg.norm(vertices - offset, axis=1)
    assert np.all(radii > 1.9) and np.all(radii < 2.1)
    assert len(faces) <= 400 and len(vertices) <= 250
    assert proof["coordinate_frame"] == "normalized-local-to-original-world"
    assert proof["source_euler"] == proof["display_euler"] == 2
    np.testing.assert_array_equal(mesh.vertices, original)


def test_world_restoration_rejects_rounding_collapsed_native_output(monkeypatch):
    from vtkmodules.util import numpy_support
    original_conversion = numpy_support.vtk_to_numpy
    def tiny_local_points(array):
        result = original_conversion(array)
        # Keep valid local triangles/topology but make every reconstructed
        # coordinate round to the same 1e9 offset in the final world array.
        return result * 1e-18 if array.GetNumberOfComponents() == 3 else result
    monkeypatch.setattr(numpy_support, "vtk_to_numpy", tiny_local_points)
    mesh = trimesh.creation.icosphere(subdivisions=3)
    mesh.apply_translation((1e9, 1e9, 1e9))
    with pytest.raises(display_preview.PreviewUnavailable, match="invalid_simplified_geometry"):
        display_preview.simplify(mesh.vertices, mesh.faces, 400, 250)


@pytest.mark.parametrize("failure", ["cancelled", "unavailable", "over_budget", "nonfinite"])
def test_simplifier_failure_is_explicit_bounds_with_raw_report_preserved(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(viewer, "MAX_EXACT_FACES", 1_000)
    monkeypatch.setattr(viewer, "MAX_EXACT_VERTICES", 600)
    mesh = trimesh.creation.icosphere(subdivisions=4)
    bounds = [mesh.vertices.min(axis=0).tolist(), mesh.vertices.max(axis=0).tolist()]
    report = {"status": "inconclusive", "complete": False, "violations": [],
              "objects": [{"index": 0, "bounds": bounds}]}
    event = threading.Event()
    if failure == "cancelled":
        event.set()
    elif failure == "unavailable":
        def unavailable(*args, **kwargs):
            raise display_preview.PreviewUnavailable("simplifier_unavailable")
        monkeypatch.setattr(display_preview, "simplify", unavailable)
    elif failure == "over_budget":
        monkeypatch.setattr(display_preview, "simplify", lambda *a, **k: (mesh.vertices.copy(), mesh.faces.copy(), {}))
    else:
        monkeypatch.setattr(display_preview, "simplify", lambda *a, **k: (np.full((4, 3), np.nan), np.array([[0, 1, 2]]), {}))
    target = tmp_path / (failure + ".html")
    viewer.write_html_report([MeshObject("surface", mesh)], report, target, cancel_event=event)
    payload = payload_from(target)
    assert payload["traces"][0]["type"] == "scatter3d"
    item = payload["preview"]["objects"][0]
    assert item["mode"] == "bounds" and item["reason"]
    assert item["displayed_faces"] == 0 and item["displayed_vertices"] == 0
    assert payload["report"] == report


def test_native_adapter_rejects_bad_source_indices_before_vtk():
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    faces = np.array([[0, 1, 900]], dtype=np.int64)
    with pytest.raises(display_preview.PreviewUnavailable, match="invalid_source_geometry"):
        display_preview.simplify(vertices, faces, 10, 10)
