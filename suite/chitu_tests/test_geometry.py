import math

import numpy as np
import pytest
import trimesh

from chitu_overlap.geometry import MeshObject, check_overlaps
from chitu_overlap import geometry


def box(name="box", center=(0, 0, 0), extents=(1, 1, 1)):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return MeshObject(name, mesh)


def test_separate_objects_are_clear():
    offset = (2, 0, 0)
    result = check_overlaps([box("a"), box("b", center=offset)])
    assert result["status"] == "clear"
    assert result["complete"] is True
    assert result["overlaps"] == []


@pytest.mark.parametrize("offset", [(1, 0, 0), (1, 1, 0), (1, 1, 1)])
def test_face_edge_and_vertex_contact_are_forbidden(offset):
    result = check_overlaps([box("a"), box("b", center=offset)])
    assert result["status"] == "violation"
    assert result["pairs"][0]["status"] == "touching"
    assert result["pairs"][0]["distance_mm"] == 0


def test_partial_overlap_reports_volume():
    result = check_overlaps([box("a"), box("b", center=(0.5, 0, 0))])
    assert result["status"] == "violation"
    assert result["complete"] is True
    assert result["overlaps"][0]["intersection_volume"] == pytest.approx(0.5)


def test_full_containment_without_crossing_surfaces():
    result = check_overlaps([box("outer", extents=(4, 4, 4)), box("inner")])
    assert result["overlaps"][0]["intersection_volume"] == pytest.approx(1)


def test_identical_objects_overlap():
    result = check_overlaps([box("a"), box("b")])
    assert result["overlaps"][0]["intersection_volume"] == pytest.approx(1)


def test_aabb_overlap_does_not_imply_solid_overlap():
    # These tetrahedra occupy opposite corners of overlapping axis-aligned boxes.
    a = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
        process=False,
    )
    b = a.copy()
    b.apply_transform(np.diag([-1, -1, -1, 1]))
    b.apply_translation((0.8, 0.8, 0.8))
    # Reflection changes winding; apply_transform maintains outward winding.
    result = check_overlaps([MeshObject("a", a), MeshObject("b", b)])
    assert result["status"] == "clear"
    assert result["pairs"][0]["method"] == "manifold64+fcl"
    assert result["pairs"][0]["distance_mm"] > 0


def test_open_mesh_is_inconclusive():
    invalid = box("open").mesh
    invalid.update_faces(np.arange(len(invalid.faces) - 1))
    result = check_overlaps([box("good"), MeshObject("open", invalid)])
    assert result["status"] == "inconclusive"
    assert result["complete"] is False
    assert result["objects"][1]["errors"]


def test_inverted_mesh_is_inconclusive():
    invalid = box("inside-out").mesh
    invalid.invert()
    result = check_overlaps([MeshObject("inside-out", invalid)])
    assert result["status"] == "inconclusive"
    assert result["objects"][0]["volume"] < 0


def test_overlap_does_not_hide_an_unchecked_object():
    result = check_overlaps([box("a"), box("b"), MeshObject("empty", trimesh.Trimesh())])
    assert result["status"] == "violation"
    assert result["complete"] is False
    assert result["overlap_found"] is True
    assert len(result["overlaps"]) == 1


def test_distance_failure_is_inconclusive(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("deliberate failure")

    monkeypatch.setattr(geometry, "_surface_distance", fail)
    result = check_overlaps([box("a"), box("b", center=(2, 0, 0))])
    assert result["status"] == "inconclusive"
    assert "deliberate failure" in result["pairs"][0]["error"]


def test_volume_tolerance_is_applied():
    result = check_overlaps([box("a"), box("b")], volume_tolerance=1)
    assert result["status"] == "violation"
    assert result["pairs"][0]["status"] == "touching"
    assert result["pairs"][0]["intersection_volume"] == pytest.approx(1)


@pytest.mark.parametrize("tolerance", [-1, math.nan, math.inf])
def test_invalid_tolerance_rejected(tolerance):
    with pytest.raises(ValueError):
        check_overlaps([box()], volume_tolerance=tolerance)


def test_empty_input_is_inconclusive():
    result = check_overlaps([])
    assert result["status"] == "inconclusive"
    assert result["complete"] is False


def test_nonfinite_coordinates_are_inconclusive():
    invalid = box().mesh
    invalid.vertices[0, 0] = np.nan
    result = check_overlaps([MeshObject("nonfinite", invalid)])
    assert result["status"] == "inconclusive"


def test_preserves_distinct_placed_objects_and_input_meshes():
    first, second = box("one"), box("two", center=(0.25, 0.25, 0.25))
    vertices = [first.mesh.vertices.copy(), second.mesh.vertices.copy()]
    result = check_overlaps([first, second])
    assert result["object_count"] == 2
    assert result["pairs"][0]["object_a"] == "one"
    assert result["pairs"][0]["object_b"] == "two"
    np.testing.assert_array_equal(first.mesh.vertices, vertices[0])
    np.testing.assert_array_equal(second.mesh.vertices, vertices[1])


@pytest.mark.parametrize(
    ("gap", "minimum", "expected"),
    [(0.5, 1, "too_close"), (1, 1, "clear"), (1.1, 1, "clear"), (0.9999995, 1, "clear")],
)
def test_minimum_gap_threshold_with_numeric_tolerance(gap, minimum, expected):
    result = check_overlaps(
        [box("a"), box("b", center=(1 + gap, 0, 0))], min_gap_mm=minimum
    )
    assert result["pairs"][0]["status"] == expected
    assert result["pairs"][0]["distance_mm"] == pytest.approx(gap)
    assert result["violation_found"] is (expected != "clear")


def test_distance_is_euclidean_and_nearest_points_match():
    result = check_overlaps([box("a"), box("b", center=(2, 2, 0))], min_gap_mm=1.5)
    pair = result["pairs"][0]
    assert pair["status"] == "too_close"
    assert pair["distance_mm"] == pytest.approx(math.sqrt(2))
    points = np.asarray(pair["nearest_points_mm"])
    assert np.linalg.norm(points[1] - points[0]) == pytest.approx(pair["distance_mm"])


def test_rotated_face_contact_is_forbidden():
    a, b = box("a"), box("b", center=(1, 0, 0))
    rotation = trimesh.transformations.rotation_matrix(math.pi / 4, [0, 0, 1])
    a.mesh.apply_transform(rotation)
    b.mesh.apply_transform(rotation)
    result = check_overlaps([a, b])
    assert result["status"] == "violation"
    assert result["pairs"][0]["status"] == "touching"


def test_small_positive_intersection_is_never_clear():
    result = check_overlaps([box("a"), box("b", center=(1 - 1e-8, 0, 0))])
    assert result["status"] == "violation"
    assert result["pairs"][0]["status"] in {"touching", "overlap"}
    assert result["pairs"][0]["distance_mm"] == 0


def test_large_coordinates_preserve_subunit_intersection():
    result = check_overlaps(
        [box("a", center=(1e8, 0, 0)), box("b", center=(1e8 + 0.5, 0, 0))]
    )
    assert result["overlap_found"] is True
    assert result["overlaps"][0]["intersection_volume"] == pytest.approx(0.5)


@pytest.mark.parametrize("parameter", ["min_gap_mm", "distance_tolerance_mm"])
@pytest.mark.parametrize("value", [-1, math.inf, math.nan])
def test_invalid_distance_parameters_rejected(parameter, value):
    with pytest.raises(ValueError):
        check_overlaps([box()], **{parameter: value})


def test_backend_mesh_rejection_is_inconclusive(monkeypatch):
    def reject(mesh):
        raise ValueError("deliberate backend rejection")

    monkeypatch.setattr(geometry, "_manifold", reject)
    result = check_overlaps([box("a"), box("b", center=(2, 0, 0))])
    assert result["status"] == "inconclusive"
    assert "backend rejection" in result["objects"][0]["errors"][-1]
