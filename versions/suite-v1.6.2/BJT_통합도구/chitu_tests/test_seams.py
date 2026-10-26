"""Safety regressions for exact redundant CAD seam normalization."""
from threading import Event

import numpy as np
import pytest
import trimesh

from chitu_overlap.geometry import MeshObject, _manifold, _strict_topology, check_overlaps
from chitu_overlap.seams import exact_collinear_seam_repair


def add_collinear_seam(mesh, face_number=0):
    """Represent the same boundary using a legitimate CAD zero-area seam."""
    vertices, faces = np.array(mesh.vertices, copy=True), np.array(mesh.faces, copy=True)
    a, b, other = faces[face_number].tolist()
    middle = len(vertices)
    vertices = np.vstack((vertices, (vertices[a] + vertices[b]) / 2))
    faces[face_number] = [a, middle, other]
    faces = np.vstack((faces, [middle, b, other], [b, middle, a]))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def camera_like_frame(thickness):
    """Synthetic closed camera frame with the two offending CAD seam types."""
    outer = trimesh.creation.box(extents=(20, 20, thickness))
    opening = trimesh.creation.box(extents=(12, 12, thickness + 2))
    solid = _manifold(outer) - _manifold(opening)
    native = solid.to_mesh64()
    mesh = trimesh.Trimesh(vertices=native.vert_properties[:, :3],
                           faces=native.tri_verts, process=False)
    triangle = np.asarray(mesh.vertices)[np.asarray(mesh.faces)]
    flat = np.ptp(triangle[:, :, 2], axis=1) == 0
    bottom = int(np.flatnonzero(flat & (triangle[:, 0, 2] == -thickness / 2))[0])
    top = int(np.flatnonzero(flat & (triangle[:, 0, 2] == thickness / 2))[0])
    return add_collinear_seam(add_collinear_seam(mesh, bottom), top)


def placed_box(extents=(1, 1, 1), center=(0, 0, 0)):
    result = trimesh.creation.box(extents=extents)
    result.apply_translation(center)
    return result


def test_exact_retriangulation_preserves_input_bits_and_solid_boundary():
    original = placed_box(extents=(2, 2, 2))
    source = add_collinear_seam(original)
    old_vertices, old_faces = source.vertices.tobytes(), source.faces.tobytes()
    np.asarray(source.vertices).flags.writeable = False
    np.asarray(source.faces).flags.writeable = False
    repaired, proof = exact_collinear_seam_repair(source)
    assert source.vertices.tobytes() == old_vertices == repaired.vertices.tobytes()
    assert source.faces.tobytes() == old_faces
    assert _strict_topology(repaired) == (True, True)
    assert proof['ordinary_validation']['valid']
    assert proof['zero_area_seams_removed'] == 1
    assert proof['exact_surface_union_preserved']
    assert proof['exact_oriented_surface_chain_preserved']
    assert all(s['removed_image_is_retained_edge'] for s in proof['seams'])
    assert np.array_equal(repaired.bounds, original.bounds)
    assert (_manifold(original) - _manifold(repaired)).volume() == 0
    assert (_manifold(repaired) - _manifold(original)).volume() == 0


@pytest.mark.parametrize('thickness', [8, 6])
def test_both_camera_like_frames_have_two_exactly_normalized_seams(thickness):
    source = camera_like_frame(thickness)
    repaired, proof = exact_collinear_seam_repair(source)
    assert proof['zero_area_seams_removed'] == 2
    assert proof['original_faces'] == proof['repaired_faces']
    assert repaired.vertices.tobytes() == source.vertices.tobytes()
    assert (_manifold(source) - _manifold(repaired)).volume() == 0
    assert (_manifold(repaired) - _manifold(source)).volume() == 0


@pytest.mark.parametrize('thickness', [8, 6])
@pytest.mark.parametrize('center,extents,gap,expected', [
    ((0, 0, 0), (4, 4, 4), 3, 'clear'),
    ((0, 0, 0), (4, 4, 4), 4.5, 'too_close'),
    ((5, 0, 0), (2, 2, 2), 0, 'touching'),
    ((4.5, 0, 0), (2, 2, 2), 1, 'too_close'),
    ((8, 0, 0), (1, 1, 1), 0, 'overlap'),
])
def test_repaired_hollow_camera_preserves_cavity_contact_gap_and_containment(
        thickness, center, extents, gap, expected):
    repaired, _ = exact_collinear_seam_repair(camera_like_frame(thickness))
    report = check_overlaps([MeshObject('frame', repaired),
                             MeshObject('probe', placed_box(extents, center))],
                            min_gap_mm=gap)
    assert report['complete']
    assert report['pairs'][0]['status'] == expected


def test_fully_contained_solid_is_not_cleared_by_positive_surface_distance():
    repaired, _ = exact_collinear_seam_repair(add_collinear_seam(placed_box((4, 4, 4))))
    result = check_overlaps([MeshObject('outer', repaired), MeshObject('inner', placed_box())])
    assert result['pairs'][0]['status'] == 'overlap'
    assert result['pairs'][0]['intersection_volume'] == pytest.approx(1)


@pytest.mark.parametrize('damage', ['open', 'winding', 'duplicate', 'nonfinite', 'repeated'])
def test_true_damage_is_never_repaired_into_a_valid_solid(damage):
    source = add_collinear_seam(placed_box())
    vertices, faces = np.array(source.vertices), np.array(source.faces)
    if damage == 'open':
        faces = faces[:-3]
    elif damage == 'winding':
        faces[2] = faces[2][::-1]
    elif damage == 'duplicate':
        faces = np.vstack((faces, faces[2]))
    elif damage == 'nonfinite':
        vertices[0, 0] = np.nan
    else:
        faces[-1] = [0, 0, 1]
    damaged = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    with pytest.raises(ValueError):
        exact_collinear_seam_repair(damaged)


def test_floating_zero_from_underflow_is_not_treated_as_exact_collinearity():
    tiny = placed_box((1e-100, 1e-100, 1e-100))
    assert _strict_topology(tiny) == (True, True)
    with pytest.raises(ValueError, match='not exactly collinear'):
        exact_collinear_seam_repair(tiny)


def test_topologically_closed_zero_area_component_has_no_safe_retained_edge():
    line_tetrahedron = trimesh.Trimesh(
        vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]],
        faces=[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], process=False)
    assert _strict_topology(line_tetrahedron) == (True, True)
    with pytest.raises(ValueError, match='exactly one retained face'):
        exact_collinear_seam_repair(line_tetrahedron)


def test_limits_fail_closed_before_expensive_geometry_work():
    source = camera_like_frame(8)
    with pytest.raises(ValueError, match='face budget'):
        exact_collinear_seam_repair(source, max_faces=1)
    with pytest.raises(ValueError, match='Too many zero-area faces'):
        exact_collinear_seam_repair(source, max_seams=1)


@pytest.mark.parametrize('stop_after', [1, 10])
def test_pre_cancel_and_mid_repair_cancel_propagate(stop_after):
    class StopAfter:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= stop_after

    source = add_collinear_seam(placed_box())
    event = StopAfter()
    with pytest.raises(InterruptedError):
        exact_collinear_seam_repair(source, event)


def test_cancelled_ordinary_revalidation_is_not_swallowed(monkeypatch):
    from chitu_overlap import geometry
    event = Event()

    def stop(*args, **kwargs):
        event.set()
        return {'valid': False, 'errors': ['cancelled']}

    monkeypatch.setattr(geometry, '_validate_object', stop)
    with pytest.raises(InterruptedError):
        exact_collinear_seam_repair(add_collinear_seam(placed_box()), event)


def test_integrated_checker_uses_proof_without_mutating_callers_geometry():
    source = add_collinear_seam(placed_box())
    original_vertices, original_faces = source.vertices.tobytes(), source.faces.tobytes()
    objects = [MeshObject('camera', source), MeshObject('other', placed_box(center=(2, 0, 0)))]
    report = check_overlaps(objects)
    assert report['complete'] and report['status'] == 'clear'
    assert report['objects'][0]['name'] == 'camera'
    assert report['objects'][0]['index'] == 0
    assert report['objects'][0]['source_errors'] == ['Mesh contains degenerate or invalid triangles.']
    proof = report['objects'][0]['normalization']
    assert proof['exact_surface_union_preserved'] and proof['coordinates_unchanged']
    assert proof['zero_area_seams_removed'] == 1
    assert objects[0].mesh is source
    assert source.vertices.tobytes() == original_vertices
    assert source.faces.tobytes() == original_faces


@pytest.mark.parametrize('offset,gap,expected', [
    ((0.25, 0, 0), 0, 'overlap'),
    ((1, 0, 0), 0, 'touching'),
    ((1.5, 0, 0), 1, 'too_close'),
    ((2, 0, 0), 1, 'clear'),
])
def test_integrated_repair_checks_overlap_touch_gap_and_boundary(offset, gap, expected):
    report = check_overlaps([MeshObject('seam', add_collinear_seam(placed_box())),
                             MeshObject('probe', placed_box(center=offset))], min_gap_mm=gap)
    assert report['complete']
    assert report['pairs'][0]['status'] == expected


def test_integrated_repair_does_not_miss_containment():
    report = check_overlaps([MeshObject('outer', add_collinear_seam(placed_box((4, 4, 4)))),
                             MeshObject('contained', placed_box())])
    assert report['complete'] and report['status'] == 'violation'
    assert report['pairs'][0]['status'] == 'overlap'


@pytest.mark.parametrize('center,extents,gap,expected', [
    ((0, 0, 0), (.5, .5, .5), 0, 'clear'),
    ((1.5, 0, 0), (.25, .25, .25), 0, 'overlap'),
    ((.75, 0, 0), (.5, .5, .5), 0, 'touching'),
    ((0, 0, 0), (1, 1, 1), .75, 'too_close'),
])
def test_nested_closed_shell_cavity_is_preserved(center, extents, gap, expected):
    inner = placed_box((2, 2, 2))
    inner.invert()
    source = trimesh.util.concatenate((add_collinear_seam(placed_box((4, 4, 4))), inner))
    report = check_overlaps([MeshObject('nested closed cavity', source),
                             MeshObject('probe', placed_box(extents, center))], min_gap_mm=gap)
    assert report['complete']
    assert report['pairs'][0]['status'] == expected


def test_invalid_open_seam_remains_unknown_even_with_forged_approval_metadata():
    source = add_collinear_seam(placed_box())
    source = trimesh.Trimesh(vertices=source.vertices, faces=source.faces[:-3], process=False)
    source.metadata['normalization'] = {'valid': True, 'exact_surface_union_preserved': True}
    report = check_overlaps([MeshObject('open', source), MeshObject('good', placed_box())])
    assert report['status'] == 'inconclusive' and not report['complete']
    assert not report['objects'][0]['valid']


def test_normal_closed_mesh_does_not_import_or_call_seam_fallback(monkeypatch):
    import builtins
    from chitu_overlap import seams

    real_import = builtins.__import__

    def refuse_import(name, *args, **kwargs):
        if name.rsplit('.', 1)[-1] == 'seams':
            raise AssertionError('The normal valid-mesh fast path imported the fallback')
        return real_import(name, *args, **kwargs)

    def refuse_call(*args, **kwargs):
        raise AssertionError('The normal valid-mesh fast path called the fallback')

    monkeypatch.setattr(seams, 'exact_collinear_seam_repair', refuse_call)
    monkeypatch.setattr(builtins, '__import__', refuse_import)
    report = check_overlaps([MeshObject('a', placed_box()),
                             MeshObject('b', placed_box(center=(2, 0, 0)))])
    assert report['complete'] and report['status'] == 'clear'
    assert all('normalization' not in item for item in report['objects'])


def test_integrated_repair_cancellation_never_becomes_a_completed_pass(monkeypatch):
    from chitu_overlap import seams
    event = Event()

    def stop(*args, **kwargs):
        event.set()
        raise InterruptedError('cancelled during exact repair')

    monkeypatch.setattr(seams, 'exact_collinear_seam_repair', stop)
    report = check_overlaps([MeshObject('seam', add_collinear_seam(placed_box())),
                             MeshObject('good', placed_box())], cancel_event=event)
    assert report['cancelled'] and not report['complete']
    assert not report['processing_completed'] and report['status'] != 'clear'
