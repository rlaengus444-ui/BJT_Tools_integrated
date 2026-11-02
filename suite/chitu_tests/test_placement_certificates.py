"""Original-triangle separation is independent of strict solid acceptance."""
from collections import Counter
import threading

import numpy as np
import pytest
import trimesh

from chitu_overlap import geometry
from chitu_overlap.geometry import MeshObject, check_overlaps


def box(name, x):
    mesh = trimesh.creation.box()
    mesh.apply_translation((x, 0, 0))
    return MeshObject(name, mesh)


def invalid_soup(name="invalid", x=0):
    first, second = trimesh.creation.box(), trimesh.creation.box()
    second.apply_translation((1, 1, 0))
    mesh = trimesh.util.concatenate((first, second))
    mesh.merge_vertices()
    # Preserve a true four-incidence edge and add a zero-area face. Neither
    # raw Manifold acceptance nor discarding this face certifies the soup.
    faces = np.vstack((mesh.faces, [0, 0, 0]))
    mesh = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=faces, process=False)
    mesh.apply_translation((x, 0, 0))
    return MeshObject(name, mesh)


def test_nineteen_objects_keep_legacy_66_unknown_pairs_but_certify_placement():
    invalid = {7, 13, 15, 17}
    objects = [invalid_soup(f"cup {i}", i * 10) if i in invalid else box(f"object {i}", 2 if i == 1 else i * 10)
               for i in range(19)]
    before = [(obj.mesh.vertices.tobytes(), obj.mesh.faces.tobytes()) for obj in objects]
    report = check_overlaps(objects)
    assert report["status"] == "inconclusive" and not report["complete"]
    assert {obj["index"] for obj in report["objects"] if not obj["valid"]} == invalid
    assert len(report["pairs"]) == 171
    assert sum(pair["status"] == "inconclusive" for pair in report["pairs"]) == 66
    assert report["closest_pair_scope"] == "valid_objects" and not report["closest_distance_is_global"]
    assert report["placement_status"] == "clear" and report["placement_complete"]
    assert all(pair["placement_status"] == "clear" for pair in report["pairs"])
    assert report["placement_minimum_distance_is_certified"]
    assert report["placement_minimum_distance_mm"] == pytest.approx(1)
    assert report["placement_minimum_distance_pair"] == [0, 1]
    assert "placement_pairs" not in report
    assert len({(p["index_a"], p["index_b"]) for p in report["pairs"]}) == 171
    for obj, expected in zip(objects, before):
        assert (obj.mesh.vertices.tobytes(), obj.mesh.faces.tobytes()) == expected


def test_degenerate_and_nonmanifold_counts_are_complete_and_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(geometry, "TOPOLOGY_CHECK_PAIRS", 1)
    monkeypatch.setattr(geometry, "VALIDATION_FACE_CHUNK", 2)
    monkeypatch.setattr(geometry, "TOPOLOGY_SORT_BYTES", 1)
    original = geometry.tempfile.TemporaryDirectory
    monkeypatch.setattr(geometry.tempfile, "TemporaryDirectory", lambda *a, **kw: original(*a, dir=tmp_path, **kw))
    obj = invalid_soup()
    counts = Counter(tuple(sorted((int(face[a]), int(face[b]))))
                     for face in obj.mesh.faces for a, b in ((0, 1), (1, 2), (2, 0)))
    report = check_overlaps([obj, box("far", 20)])
    record = report["objects"][0]
    assert not record["valid"] and record["bounds"] is None
    assert record["source_triangles_finite_indexed"] and record["original_triangle_bounds"] is not None
    diagnostic = record["topology_diagnostics"]
    assert diagnostic["face_counts_complete"] and diagnostic["edge_counts_complete"]
    assert diagnostic["degenerate_face_count"] == 1
    assert diagnostic["boundary_edge_count"] == sum(n == 1 for n in counts.values())
    assert diagnostic["nonmanifold_edge_count"] == sum(n > 2 for n in counts.values())
    assert diagnostic["unique_edge_count"] == len(counts)
    assert report["pairs"][0]["placement_method"] == "original-triangle-aabb-bound"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["nan", "bad-index", "empty"])
def test_untrusted_or_empty_triangles_cannot_complete_placement(kind, monkeypatch):
    monkeypatch.setattr(geometry, "VALIDATION_FACE_CHUNK", 1)
    mesh = trimesh.creation.box()
    if kind == "nan":
        mesh.vertices[-1, 0] = np.nan
    elif kind == "bad-index":
        mesh.faces[-1, 0] = len(mesh.vertices) + 9
    else:
        mesh = trimesh.Trimesh()
    report = check_overlaps([MeshObject(kind, mesh), box("far", 100)])
    assert not report["placement_complete"] and report["placement_status"] == "unknown"
    assert not report["placement_minimum_distance_is_certified"]
    assert report["placement_minimum_distance_mm"] is None
    assert report["pairs"][0]["placement_status"] == "unknown"
    assert not report["objects"][0]["source_triangles_finite_indexed"]


@pytest.mark.parametrize("x,gap", [(0, 0), (2, 10)])
def test_overlapping_or_insufficient_bounds_do_not_infer_violation_or_clear(x, gap):
    report = check_overlaps([invalid_soup(), box("probe", x)], min_gap_mm=gap)
    assert report["pairs"][0]["status"] == "inconclusive"
    assert report["pairs"][0]["placement_status"] == "unknown"
    assert not report["placement_complete"]


def test_all_placement_pairs_clear_does_not_fabricate_global_minimum():
    opened = box("open", 2).mesh
    opened.update_faces(np.arange(len(opened.faces) - 1))
    report = check_overlaps([box("a", 0), box("b", 10), MeshObject("open", opened)])
    assert report["placement_complete"] and report["placement_status"] == "clear"
    assert report["minimum_distance_mm"] == pytest.approx(9)
    assert not report["placement_minimum_distance_is_certified"]
    assert report["placement_minimum_distance_mm"] is None
    assert report["placement_minimum_distance_pair"] is None


def test_exact_violations_are_exposed_in_both_independent_results():
    report = check_overlaps([box("a", 0), box("b", .5)])
    assert report["pairs"][0]["status"] == "overlap"
    assert report["pairs"][0]["placement_status"] == "violation"
    assert report["placement_complete"] and report["placement_status"] == "violation"


def test_geometry_backend_failure_does_not_erase_independent_aabb_proof(monkeypatch):
    def reject(*args):
        raise RuntimeError("distance backend failed")
    monkeypatch.setattr(geometry, "_surface_distance", reject)
    report = check_overlaps([box("a", 0), box("b", 10)])
    assert report["status"] == "inconclusive" and not report["complete"]
    assert report["placement_complete"] and report["placement_status"] == "clear"
    assert report["placement_minimum_distance_mm"] is None


def test_pair_preparation_stops_at_first_cancelled_bound(monkeypatch):
    event = threading.Event()
    calls = []
    original = geometry._placement_bbox_lower_bound
    def cancel(*args):
        calls.append(True)
        event.set()
        return original(*args)
    monkeypatch.setattr(geometry, "_placement_bbox_lower_bound", cancel)
    report = check_overlaps([box(str(i), i * 10) for i in range(30)], cancel_event=event)
    assert len(calls) == 1
    assert report["cancelled"] and not report["complete"] and not report["placement_complete"]
    assert report["pairs"] == []


def test_bounds_round_conservatively_and_contact_tolerance_is_not_clear():
    lower = geometry._placement_bbox_lower_bound([[0, 0, 0], [1, 1, 1]], [[2, 2, 1], [3, 3, 2]])
    assert 0 < lower <= np.sqrt(2)
    source = invalid_soup()
    report = check_overlaps([source, box("far", 10)], distance_tolerance_mm=100)
    assert report["pairs"][0]["placement_status"] == "unknown"


@pytest.mark.parametrize("best,gap,placement,certified", [(.5, 1., "unknown", True), (1., 0., "clear", False)])
def test_legacy_rounded_aabb_cannot_override_conservative_placement(monkeypatch, best, gap, placement, certified):
    monkeypatch.setattr(geometry, "LARGE_SCENE_FACE_THRESHOLD", 0)
    def placed_bounds(name, lower, upper):
        mesh = trimesh.creation.box()
        for axis in range(3):
            mesh.vertices[:, axis] = np.where(mesh.vertices[:, axis] < 0, lower[axis], upper[axis])
        return MeshObject(name, mesh)
    a = placed_bounds("a", [-1., -1., -1.], [0., 0., 0.])
    close = placed_bounds("first exact", [-2.-best, -1., -1.], [-1.-best, 0., 0.])
    dy = np.nextafter(.6, 0.)
    rounded = placed_bounds("rounded bound", [.8, dy, -1.], [1.8, 1.6, 0.])
    assert geometry._bbox_distance(a.mesh.bounds, rounded.mesh.bounds) == 1.
    assert geometry._placement_bbox_lower_bound(a.mesh.bounds, rounded.mesh.bounds) < 1.
    report = check_overlaps([a, close, rounded], min_gap_mm=gap, distance_tolerance_mm=0.)
    pair = next(row for row in report["pairs"] if (row["index_a"], row["index_b"]) == (0, 2))
    assert pair["status"] == "clear" and pair["distance_mm"] is None
    assert pair["method"] == "aabb-clearance-bound"
    assert pair["placement_status"] == placement
    assert report["placement_minimum_distance_is_certified"] is certified
