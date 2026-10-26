"""Accuracy and allocation-bound regressions for the large-scene path."""

import math

import numpy as np
import pytest
import trimesh

from chitu_overlap import geometry
from chitu_overlap.geometry import MeshObject, check_overlaps


def box(name, x):
    mesh = trimesh.creation.box()
    mesh.apply_translation((x, 0, 0))
    return MeshObject(name, mesh)


def test_certified_far_pairs_preserve_exact_global_minimum(monkeypatch):
    monkeypatch.setattr(geometry, "LARGE_SCENE_FACE_THRESHOLD", 0)
    native_faces = []
    original = geometry._collision_object

    def track(mesh):
        native_faces.append(len(mesh.faces))
        return original(mesh)

    monkeypatch.setattr(geometry, "_collision_object", track)
    result = check_overlaps([box("a", 0), box("b", 2), box("c", 10), box("d", 100)])
    assert result["status"] == "clear" and result["complete"]
    assert result["minimum_distance_mm"] == pytest.approx(1)
    assert result["minimum_distance_pair"] == [0, 1]
    assert result["closest_distance_is_global"]
    assert len(native_faces) == 2
    assert not result["pair_distances_complete"]
    skipped = [pair for pair in result["pairs"] if pair["distance_mm"] is None]
    assert len(skipped) == 5
    assert all(pair["status"] == "clear" and pair["distance_lower_bound_mm"] >= 1
               and pair["distance_is_exact"] is False for pair in skipped)


def test_every_failure_is_measured_even_after_closest_pair_found(monkeypatch):
    monkeypatch.setattr(geometry, "LARGE_SCENE_FACE_THRESHOLD", 0)
    result = check_overlaps([box("a", 0), box("b", 1.25), box("c", 100), box("d", 101.5)], min_gap_mm=1)
    assert result["complete"]
    assert {(pair["index_a"], pair["index_b"]) for pair in result["violations"]} == {(0, 1), (2, 3)}
    assert all(pair["distance_is_exact"] for pair in result["violations"])
    assert result["minimum_distance_mm"] == pytest.approx(.25)


def test_closest_pair_is_not_assumed_to_have_smallest_aabb_bound(monkeypatch):
    monkeypatch.setattr(geometry, "LARGE_SCENE_FACE_THRESHOLD", 0)
    a = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], process=False)
    b = a.copy()
    b.apply_transform(np.diag([-1, -1, -1, 1]))
    b.apply_translation((.8, .8, .8))
    result = check_overlaps([MeshObject("a", a), MeshObject("b", b), box("c", 100), box("d", 101.1)])
    assert result["complete"]
    assert result["minimum_distance_pair"] == [2, 3]
    assert result["minimum_distance_mm"] == pytest.approx(.1)
    assert result["pairs"][0]["distance_mm"] == pytest.approx(.4 / math.sqrt(3))


def test_invalid_geometry_does_not_get_certified_clear(monkeypatch):
    monkeypatch.setattr(geometry, "LARGE_SCENE_FACE_THRESHOLD", 0)
    opened = box("open", 1000).mesh
    opened.update_faces(np.arange(len(opened.faces) - 1))
    result = check_overlaps([box("a", 0), box("b", 2), MeshObject("open", opened)])
    assert result["status"] == "inconclusive"
    assert not result["complete"] and result["processing_completed"]
    assert result["closest_pair_scope"] == "valid_objects"
    assert not result["closest_distance_is_global"]
    assert result["closest_distance_is_global_for_scope"]
    assert all(pair["status"] == "inconclusive" for pair in result["pairs"] if pair["index_b"] == 2)


def test_validation_does_not_retain_full_triangle_or_edge_caches():
    objects = [MeshObject("sphere", trimesh.creation.icosphere(subdivisions=2)), box("distant", 10)]
    for obj in objects:
        _ = obj.mesh.is_watertight, obj.mesh.area_faces, obj.mesh.volume
    assert any(obj.mesh._cache.cache for obj in objects)
    result = check_overlaps(objects)
    assert result["complete"]
    for obj in objects:
        assert not {"triangles", "edges", "edges_sorted", "triangles_cross"}.intersection(obj.mesh._cache.cache)


def test_distance_blocks_have_fixed_face_bound_and_match_full_bvh(monkeypatch):
    a, b = box("a", 0).mesh, box("b", 2.25).mesh
    expected, _ = geometry._surface_distance(geometry._collision_object(a), geometry._collision_object(b))
    monkeypatch.setattr(geometry, "FCL_BLOCK_FACES", 3)
    sizes = []
    original = geometry._collision_object

    def track(mesh):
        sizes.append(len(mesh.faces))
        return original(mesh)

    monkeypatch.setattr(geometry, "_collision_object", track)
    actual, points = geometry._bounded_surface_distance(a, b, a.bounds, b.bounds, None)
    assert actual == pytest.approx(expected)
    assert np.linalg.norm(np.asarray(points[1]) - points[0]) == pytest.approx(actual)
    assert max(sizes) <= 3


def test_crop_handles_triangle_interior_minimum_and_unused_vertices():
    a = trimesh.Trimesh(vertices=[[-10, -10, 0], [10, -10, 0], [0, 10, 0], [0, 0, 1]],
                        faces=[[0, 1, 2]], process=False)
    b = trimesh.Trimesh(vertices=[[0, 0, 1], [0, 0, 2], [1, 0, 2]], faces=[[0, 1, 2]], process=False)
    actual, _ = geometry._bounded_surface_distance(a, b, a.bounds, b.bounds, None)
    assert actual == pytest.approx(1)


def test_native_readonly_clone_proof_reuses_topology_not_mutated_metadata(monkeypatch):
    from chitu_tests.test_chitubox import project_blob
    from chitu_overlap.chitubox import native_geometry_key, parse_chitubox

    objects = parse_chitubox(project_blob(((0, 0, 0), (3, 0, 0)), shared_geometry=True))
    assert native_geometry_key(objects[0].mesh) == native_geometry_key(objects[1].mesh)
    count = []
    original = geometry._manifold

    def track(mesh):
        count.append(1)
        return original(mesh)

    monkeypatch.setattr(geometry, "_manifold", track)
    assert check_overlaps(objects)["complete"]
    assert len(count) == 1
    changed = objects[1].mesh.copy()
    changed.update_faces(np.arange(len(changed.faces) - 1))
    assert native_geometry_key(changed) is None
    result = check_overlaps([objects[0], MeshObject("changed", changed)])
    assert result["status"] == "inconclusive" and not result["objects"][1]["valid"]


def test_small_scene_native_cache_evicts_at_its_byte_budget(monkeypatch):
    monkeypatch.setattr(geometry, "_FCL_CACHE_BYTES", 10_000)
    cache = geometry._SmallCollisionCache()
    a, b = box("a", 0).mesh, box("b", 2).mesh
    first = cache.get(0, a)
    assert cache.get(0, a) is first
    _ = cache.get(1, b)
    assert cache.bytes <= 10_000
    assert list(cache.entries) == [1]


@pytest.mark.parametrize("offset", [(1, 1, 0), (1, 0, 0), (0, 0, 0)])
def test_native_acceptance_does_not_certify_nonmanifold_edge_counts(offset, monkeypatch):
    # Manifold accepts these unions/soups, although shared edges have four or
    # more incidences. The original strict mesh contract rejects all three.
    monkeypatch.setattr(geometry, "TOPOLOGY_CHECK_PAIRS", 1)
    first = trimesh.creation.box()
    second = first.copy()
    second.apply_translation(offset)
    joined = trimesh.util.concatenate([first, second])
    joined.merge_vertices()
    assert not joined.is_watertight
    assert geometry._manifold(joined).status().name == "NoError"
    result = check_overlaps([MeshObject("nonmanifold", joined), box("distant", 10)])
    assert result["status"] == "inconclusive"
    assert not result["objects"][0]["valid"]
    assert any("not watertight" in error for error in result["objects"][0]["errors"])


def test_strict_topology_checks_direction_and_chunk_boundaries(monkeypatch):
    monkeypatch.setattr(geometry, "TOPOLOGY_CHECK_PAIRS", 1)
    mesh = trimesh.creation.box()
    assert geometry._strict_topology(mesh) == (True, True)
    mesh.faces[0] = mesh.faces[0][::-1]
    assert mesh.is_watertight and not mesh.is_winding_consistent
    assert geometry._strict_topology(mesh) == (True, False)


def test_large_edge_sort_mapping_is_closed_and_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(geometry, "TOPOLOGY_SORT_BYTES", 1)
    original = geometry.tempfile.TemporaryDirectory
    created = []

    def directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        result = original(*args, **kwargs)
        created.append(result.name)
        return result

    monkeypatch.setattr(geometry.tempfile, "TemporaryDirectory", directory)
    assert geometry._strict_topology(trimesh.creation.box()) == (True, True)
    assert created
    assert not list(tmp_path.iterdir())
