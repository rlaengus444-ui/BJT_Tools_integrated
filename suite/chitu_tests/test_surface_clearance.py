"""Surface-only queries preserve source data and reject unsafe primitives."""
import json
from pathlib import Path
import threading
from unittest.mock import Mock

import numpy as np
import pytest
import trimesh

from chitu_overlap import geometry, inputs, seams, surface
from chitu_overlap.geometry import MeshObject
from chitu_overlap.surface import check_surface_clearances, prepare_surface_query


def box(name="box", x=0, size=1):
    mesh = trimesh.creation.box(extents=(size, size, size))
    mesh.apply_translation((x, 0, 0))
    return MeshObject(name, mesh)


def covered_box(*, soup=False):
    mesh = trimesh.creation.box()
    a, b, _ = mesh.faces[0]
    vertices = np.vstack((mesh.vertices, (mesh.vertices[a]+mesh.vertices[b])/2))
    faces = np.vstack((mesh.faces, [a, len(vertices)-1, b]))
    if soup:
        vertices = vertices[faces].reshape((-1, 3))
        faces = np.arange(len(vertices)).reshape((-1, 3))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def snapshot(mesh):
    def freeze(value):
        if isinstance(value, np.ndarray):
            return (value.dtype.str, value.shape, value.tobytes())
        if isinstance(value, dict):
            return tuple((key, freeze(part)) for key, part in value.items())
        if isinstance(value, (list, tuple)):
            return tuple(map(freeze, value))
        if value is None or isinstance(value, (str, int, float, bool, bytes)):
            return value
        return (type(value).__name__, id(value))
    return (mesh.vertices.tobytes(), mesh.faces.tobytes(), mesh.vertices.flags.writeable,
            mesh.faces.flags.writeable, freeze(mesh.metadata),
            {key: id(value) for key, value in mesh._cache.cache.items()})


@pytest.mark.parametrize("soup", [False, True])
def test_covered_zero_segment_proof_matches_coordinates_without_index_welding(soup):
    mesh = covered_box(soup=soup)
    mesh.metadata["user"] = {"unchanged": [1, 2, 3]}
    _ = mesh.bounds, mesh.triangles, mesh.area_faces
    before = snapshot(mesh)
    query, proof = prepare_surface_query(mesh)
    assert snapshot(mesh) == before
    assert query is not mesh and not np.shares_memory(query.faces, mesh.faces)
    assert np.shares_memory(query.vertices, mesh.vertices)
    assert not query.vertices.flags.writeable
    assert query.metadata == {}
    assert proof["removed_count"] == 1 and proof["query_triangle_count"] == 12
    assert proof["coordinates_unchanged"] and proof["exact_surface_union_preserved"]
    item = proof["coverage_proofs"][0]
    assert item["removed_face"] == 12 and item["retained_cover_face"] != 12
    assert item["exact_segment_subset_of_retained_edge"]
    if soup:
        assert not set(item["segment_vertex_indices"]) & set(item["retained_cover_vertex_indices"])
    np.testing.assert_array_equal(query.vertices[query.faces], mesh.vertices[mesh.faces[:-1]])


def test_nondegenerate_queries_borrow_mesh_without_scene_copy_or_cache_changes():
    obj = box()
    _ = obj.mesh.bounds, obj.mesh.area_faces, obj.mesh.triangles
    before = snapshot(obj.mesh)
    query, proof = prepare_surface_query(obj.mesh)
    assert query is obj.mesh
    assert proof["query_storage"] == "borrowed-original-triangles"
    assert snapshot(obj.mesh) == before
    report = check_surface_clearances([obj, box("far", 3)])
    assert report["complete"] and snapshot(obj.mesh) == before


def test_surface_inspector_never_invokes_legacy_quality_or_solid_functions(monkeypatch):
    a = covered_box()
    opened = box("open", 4).mesh
    opened.update_faces(np.arange(len(opened.faces)-1))
    reverse = box("reverse", 8).mesh
    reverse.faces = reverse.faces[:, ::-1]
    nonmanifold = box("nonmanifold", 12).mesh
    nonmanifold.faces = np.vstack((nonmanifold.faces, nonmanifold.faces[:1]))
    objects = [MeshObject(str(i), mesh) for i, mesh in enumerate((a, opened, reverse, nonmanifold))]
    before = [snapshot(obj.mesh) for obj in objects]
    traps = []
    for name in ("check_overlaps", "_validate_object", "_strict_topology", "_manifold"):
        trap = Mock(side_effect=AssertionError("legacy geometry validation called"))
        monkeypatch.setattr(geometry, name, trap)
        traps.append(trap)
    trap = Mock(side_effect=AssertionError("solid repair called"))
    monkeypatch.setattr(seams, "exact_collinear_seam_repair", trap)
    traps.append(trap)
    report = check_surface_clearances(objects)
    assert report["complete"] and report["pair_distances_complete"]
    assert len(report["pairs"]) == 6
    assert all(row["distance_is_exact"] for row in report["pairs"])
    assert not report["geometry_validation_performed"] and not report["volume_overlap_evaluated"]
    assert all("valid" not in row and "errors" not in row for row in report["objects"])
    assert all(snapshot(obj.mesh) == previous for obj, previous in zip(objects, before))
    for trap in traps:
        trap.assert_not_called()


@pytest.mark.parametrize("offset,gap,tolerance,expected", [
    (1.5, 1., 1e-6, "too_close"), (1.5, .5, 1e-6, "surface_gap_satisfied"),
    (1., 0., 1e-6, "contact_or_intersection"), (.5, 0., 1e-6, "contact_or_intersection"),
    (1.9999995, 1., 1e-6, "surface_gap_satisfied"), (1.5, 0., .5, "contact_or_intersection")])
def test_actual_surface_classification_uses_unrounded_distance(offset, gap, tolerance, expected):
    report = check_surface_clearances([box("a"), box("b", offset)], min_gap_mm=gap, distance_tolerance_mm=tolerance)
    assert report["inspection_scope"] == "surface-clearance" and report["complete"]
    assert report["pairs"][0]["status"] == expected
    assert "overlap_found" not in report and "overlaps" not in report
    assert "intersection_volume" not in report["pairs"][0]


def test_containment_has_positive_surface_distance_without_no_overlap_claim():
    report = check_surface_clearances([box("outer", size=4), box("inner", size=1)], min_gap_mm=1)
    assert report["status"] == "surface_gap_satisfied" and report["complete"]
    pair = report["pairs"][0]
    assert pair["distance_mm"] == pytest.approx(1.5)
    assert pair["aabb_relation"] == "overlapping" and not pair["containment_evaluated"]
    assert not report["volume_overlap_evaluated"] and not report["containment_evaluated"]
    assert report["closest_distance_is_global"] and report["minimum_distance_mm"] == pytest.approx(1.5)


@pytest.mark.parametrize("kind", ["empty", "nan", "index", "point", "uncovered", "false-zero", "overflow"])
def test_unsafe_or_unsupported_primitives_are_not_silently_removed(kind):
    mesh = trimesh.creation.box()
    if kind == "empty":
        mesh = trimesh.Trimesh()
    elif kind == "nan":
        mesh.vertices[-1, 0] = np.nan
    elif kind == "index":
        mesh.faces[-1, 0] = len(mesh.vertices)+3
    elif kind == "point":
        mesh.faces = np.vstack((mesh.faces, [0, 0, 0]))
    elif kind == "uncovered":
        vertices = np.vstack((mesh.vertices, [[2, 0, 0], [2.5, 0, 0], [3, 0, 0]]))
        mesh = trimesh.Trimesh(vertices=vertices, faces=np.vstack((mesh.faces, [8, 9, 10])), process=False)
    elif kind == "false-zero":
        mesh = trimesh.Trimesh(vertices=[[0, 0, 0], [1e-200, 0, 0], [0, 1e-200, 0]], faces=[[0, 1, 2]], process=False)
    else:
        mesh = trimesh.Trimesh(vertices=[[1e308, 0, 0], [0, 1e308, 0], [0, 0, 1e308]], faces=[[0, 1, 2]], process=False)
    report = check_surface_clearances([MeshObject(kind, mesh), box("far", 10)])
    assert report["status"] == "inconclusive" and not report["complete"]
    assert not report["objects"][0]["query_available"] and report["objects"][0]["query_error"]
    assert report["pairs"][0]["distance_mm"] is None
    assert not report["closest_distance_is_global"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("distance,points", [
    (float("nan"), None), (float("inf"), None), (-1., None), (0., None),
    (.25, [[.5, 0, 0], [.75, 0, 0]]), (2., None),
    (2., [[.5, 0, 0], [float("nan"), 0, 0]]),
    (2., [[.5, 0, 0], [3.5, 0, 0]]),
    (1.5, [[.5, 0, 0], [1.5, 0, 0]]),
    (1., [[100, 0, 0], [101, 0, 0]])])
def test_backend_inconsistencies_cannot_become_completed_measurements(monkeypatch, distance, points):
    monkeypatch.setattr(geometry, "_surface_distance", lambda *args: (distance, points))
    report = check_surface_clearances([box("a"), box("b", 2)])
    assert report["status"] == "inconclusive" and not report["complete"]
    assert not report["pairs"][0]["distance_is_exact"] and report["pairs"][0]["distance_mm"] is None
    assert report["pairs"][0]["error"]
    assert not report["pair_distances_complete"] and not report["closest_distance_is_global"]


def test_cancellation_before_and_during_native_query_cannot_complete(monkeypatch):
    event = threading.Event()
    event.set()
    report = check_surface_clearances([box("a"), box("b", 2)], cancel_event=event)
    assert report["cancelled"] and not report["complete"] and not report["pairs"]
    event.clear()
    def stop(*args):
        event.set()
        return 1., [[.5, 0, 0], [1.5, 0, 0]]
    monkeypatch.setattr(geometry, "_surface_distance", stop)
    report = check_surface_clearances([box("a"), box("b", 2)], cancel_event=event)
    assert report["cancelled"] and not report["complete"] and not report["closest_distance_is_global"]
    assert report["pairs"][0]["distance_mm"] is None


@pytest.mark.parametrize("budget", ["MAX_COVERED_DEGENERATES", "MAX_PROOF_SCAN_FACES", "MAX_COVER_CANDIDATES"])
def test_exact_proof_work_is_capped(budget, monkeypatch):
    monkeypatch.setattr(surface, budget, 0)
    with pytest.raises(ValueError, match="bounded|budget"):
        prepare_surface_query(covered_box(soup=True))


def zero_bits(array):
    result = np.array(array, dtype=np.float64, copy=True)
    result[result == 0] = 0.
    return result.view(np.uint64)


def test_surface_stl_loader_exactly_indexes_soup_and_preserves_f64(monkeypatch, tmp_path):
    mesh = covered_box(soup=True)
    mesh.vertices[0, 0] = np.float64(.10000000000000003)
    mesh.vertices[1, 0] = np.float64(.1)
    mesh.vertices[2, 1] = -0.
    before = snapshot(mesh)
    loader = Mock(return_value=mesh)
    monkeypatch.setattr(inputs.trimesh, "load_mesh", loader)
    result, _ = inputs.load_objects([tmp_path / "source.stl"], preserve_surface=True)
    loader.assert_called_once_with(str(tmp_path / "source.stl"), file_type="stl", process=False)
    indexed = result[0].mesh
    assert indexed.vertices.dtype == np.float64 and len(indexed.faces) == len(mesh.faces)
    np.testing.assert_array_equal(zero_bits(indexed.vertices[indexed.faces]), zero_bits(mesh.vertices[mesh.faces]))
    assert snapshot(mesh) == before
    assert indexed.metadata["surface_indexing"]["method"] == "exact-float64-position-indexing"
    assert "_native_geometry_provenance" not in indexed.metadata


def test_index_workspace_fallback_keeps_soup_queryable_without_sort(monkeypatch, tmp_path):
    mesh = covered_box(soup=True)
    before = snapshot(mesh)
    monkeypatch.setattr(inputs, "SURFACE_INDEX_WORKSPACE_BYTES", 0)
    monkeypatch.setattr(inputs.trimesh, "load_mesh", lambda *a, **kw: mesh)
    monkeypatch.setattr(inputs.np, "unique", Mock(side_effect=AssertionError("unbudgeted indexing")))
    result, _ = inputs.load_objects([tmp_path / "source.stl"], preserve_surface=True)
    soup = result[0].mesh
    assert np.shares_memory(soup.vertices, mesh.vertices) and np.shares_memory(soup.faces, mesh.faces)
    assert soup.metadata["surface_indexing"]["method"] == "original-soup-workspace-fallback"
    assert snapshot(mesh) == before
    query, proof = prepare_surface_query(soup)
    assert proof["removed_count"] == 1 and len(query.faces) == 12


def test_legacy_stl_loading_default_is_unchanged(monkeypatch, tmp_path):
    mesh = trimesh.creation.box()
    loader = Mock(return_value=mesh)
    monkeypatch.setattr(inputs.trimesh, "load_mesh", loader)
    result, _ = inputs.load_objects([tmp_path / "source.stl"])
    loader.assert_called_once_with(str(tmp_path / "source.stl"), file_type="stl", process=True)
    assert result[0].mesh is mesh


def test_ascii_stl_retains_coordinates_beyond_float32_and_nearby_vertices(tmp_path):
    path = tmp_path / "float64.stl"
    text = "solid precise\n"
    for x in ("0.10000000000000003", "0.1"):
        text += ("facet normal 0 0 1\nouter loop\n"
                 f"vertex {x} 0 0\nvertex 1.1 0 0\nvertex {x} 1 0\n"
                 "endloop\nendfacet\n")
    text += "endsolid precise\n"
    path.write_text(text)
    before = path.read_bytes()
    objects, _ = inputs.load_objects([path], preserve_surface=True)
    triangles = objects[0].mesh.vertices[objects[0].mesh.faces]
    assert triangles[0, 0, 0] == float("0.10000000000000003")
    assert triangles[1, 0, 0] == .1
    assert triangles[0, 0, 0] != triangles[1, 0, 0]
    assert triangles[0, 0, 0] != float(np.float32(triangles[0, 0, 0]))
    assert path.read_bytes() == before


def test_exact_index_cancellation_after_sort_keeps_original_data(monkeypatch, tmp_path):
    mesh = covered_box(soup=True)
    before = snapshot(mesh)
    event = threading.Event()
    monkeypatch.setattr(inputs.trimesh, "load_mesh", lambda *a, **kw: mesh)
    original = np.unique
    def cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result
    monkeypatch.setattr(inputs.np, "unique", cancel)
    with pytest.raises(inputs.ChituboxCancelled):
        inputs.load_objects([tmp_path / "source.stl"], preserve_surface=True, cancel_event=event)
    assert snapshot(mesh) == before


def test_native_project_loading_keeps_original_exact_loader(monkeypatch, tmp_path):
    from chitu_overlap.chitubox import MAGIC_BYTES
    source = tmp_path / "source.chitubox"
    source.write_bytes(MAGIC_BYTES)
    original = [box()]
    reader = Mock(return_value=original)
    monkeypatch.setattr(inputs, "read_chitubox", reader)
    result, source_format = inputs.load_objects([source], preserve_surface=True)
    assert result is original and source_format == "chitubox-basic"
    reader.assert_called_once()


def test_empty_and_bad_distance_parameters_remain_inconclusive_or_rejected():
    assert not check_surface_clearances([])["complete"]
    for name in ("min_gap_mm", "distance_tolerance_mm"):
        for value in (-1., float("nan"), float("inf")):
            with pytest.raises(ValueError):
                check_surface_clearances([box()], **{name: value})
