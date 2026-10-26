"""Prepared CAD scenes preserve ordinary measurements and all gap failures."""
import pytest
import trimesh

from chitu_overlap import geometry
from chitu_overlap.geometry import MeshObject, check_overlaps
from chitu_tests.test_seams import add_collinear_seam


def box(name, x, size=1, seam=False):
    mesh = trimesh.creation.box(extents=(size, size, size))
    mesh.apply_translation((x, 0, 0))
    return MeshObject(name, add_collinear_seam(mesh) if seam else mesh)


def test_newly_usable_pairs_preserve_ordinary_distances_and_exact_global_minimum():
    ordinary = [box('a', 0), box('b', 5), box('c', 30)]
    reference = check_overlaps(ordinary)
    result = check_overlaps(ordinary + [box('cad', 7, seam=True)])
    assert result['complete'] and result['status'] == 'clear'
    assert result['minimum_distance_pair'] == [1, 3]
    assert result['minimum_distance_mm'] == pytest.approx(1)
    assert result['closest_distance_is_global']
    assert result['distance_mode'] == 'certified-clearance'
    assert not result['pair_distances_complete']
    original_pairs = {(p['index_a'], p['index_b']): p for p in reference['pairs']}
    for pair in result['pairs']:
        key = pair['index_a'], pair['index_b']
        if key in original_pairs:
            for field in ('status', 'distance_mm', 'intersection_volume', 'distance_is_exact'):
                assert pair[field] == original_pairs[key][field]
        elif key == (1, 3):
            assert pair['distance_is_exact'] and pair['distance_mm'] == pytest.approx(1)
        else:
            assert not pair['distance_is_exact'] and pair['distance_mm'] is None
            assert pair['distance_lower_bound_mm'] == pytest.approx({(0, 3): 6, (2, 3): 22}[key])


def test_finding_closest_pair_never_prunes_other_minimum_gap_failures():
    result = check_overlaps([box('a', 0), box('b', 5), box('c', 30),
                             box('cad', 7, seam=True)], min_gap_mm=10)
    assert result['complete'] and result['minimum_distance_mm'] == pytest.approx(1)
    assert {(p['index_a'], p['index_b']) for p in result['violations']} == {(0, 1), (0, 3), (1, 3)}
    assert all(p['distance_is_exact'] for p in result['violations'])


def test_positive_surface_distance_does_not_hide_containment_in_prepared_scene():
    result = check_overlaps([box('outer', 0, size=4), box('far', 20),
                             box('contained-cad', 0, seam=True)])
    assert result['complete'] and result['status'] == 'violation'
    assert result['minimum_distance_pair'] == [0, 2]
    assert result['overlaps'][0]['intersection_volume'] == pytest.approx(1)
    assert result['overlaps'][0]['distance_is_exact']


def test_native_solid_cache_and_zero_budget_preserve_independent_intersections(monkeypatch):
    inputs = [box('a', 0, size=2), box('b', .5, size=2),
              box('cad', .25, size=2, seam=True)]
    cached = check_overlaps(inputs)
    monkeypatch.setattr(geometry, '_MANIFOLD_CACHE_BYTES', 0)
    uncached = check_overlaps(inputs)
    expected = {(0, 1): 6, (0, 2): 7, (1, 2): 7}
    for result in (cached, uncached):
        assert result['complete'] and len(result['overlaps']) == 3
        assert {(p['index_a'], p['index_b']): p['intersection_volume']
                for p in result['overlaps']} == pytest.approx(expected)

