"""STEP body material, strict STL rejection and immutable source regression."""
from hashlib import sha256
import io
from pathlib import Path
import sys

import numpy as np
import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from powdernest import geometry


def box(x=0., y=0., z=0., size=2.):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), size, size, size).Shape()


def compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    result = TopoDS_Compound()
    builder = BRep_Builder(); builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def write_step(directory, shape):
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    # Python renames the file so this also tests non-ASCII native import paths.
    path = directory / 'bodies.step'
    writer = STEPControl_Writer()
    assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path.rename(directory / '보수시편 M8 (1).STEP.step')


@pytest.mark.parametrize(('second', 'volume', 'solids'), [
    ((1., 0., 0., 2.), 12., 1),  # overlap
    ((2., 0., 0., 2.), 16., 1),  # shared complete face
    ((3., 0., 0., 2.), 16., 2),  # disjoint bodies remain disjoint
    ((.5, .5, .5, 1.), 8., 1),  # contained body adds no material
    ((0., 0., 0., 2.), 8., 1),  # duplicate body is not double counted
])
def test_step_uses_body_material_union(tmp_path, second, volume, solids):
    path = write_step(tmp_path, compound(box(), box(*second)))
    before = sha256(path.read_bytes()).hexdigest()
    model = geometry.load_model(path, use_cache=False)
    assert model.mesh.volume == pytest.approx(volume)
    assert model.diagnostics['source_solid_count'] == 2
    assert model.diagnostics['material_solid_count'] == solids
    assert model.diagnostics['step_material_union_applied'] is True
    assert model.diagnostics['step_material_union_fuzzy_value_mm'] == pytest.approx(1e-7)
    assert model.mesh.is_watertight and model.mesh.is_winding_consistent
    assert model.diagnostics['original_brep_modified'] is False
    assert len(geometry._step_solids(model.original_shape)) == 2
    assert sha256(path.read_bytes()).hexdigest() == before
    # The original CAD did not acquire the working copy's triangulations.
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    faces = TopExp_Explorer(model.original_shape, TopAbs_FACE)
    while faces.More():
        assert BRep_Tool.Triangulation_s(TopoDS.Face_s(faces.Current()), TopLoc_Location()) is None
        faces.Next()


def test_cavity_and_disconnected_insert_are_preserved(tmp_path):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    hollow = BRepAlgoAPI_Cut(box(size=6.), box(2., 2., 2., 2.)).Shape()
    path = write_step(tmp_path, compound(hollow, box(2.5, 2.5, 2.5, 1.)))
    model = geometry.load_model(path, use_cache=False)
    assert model.mesh.volume == pytest.approx(216. - 8. + 1.)
    assert sorted(model.diagnostics['shell_depths']) == [0, 1, 2]
    assert model.diagnostics['material_solid_count'] == 2


@pytest.mark.parametrize('reversed_bodies', [(True, False), (False, True), (True, True)])
def test_inverted_solids_cannot_turn_material_union_into_a_complement(reversed_bodies):
    first, second = box(), box(1.)
    if reversed_bodies[0]: first.Reverse()
    if reversed_bodies[1]: second.Reverse()
    with pytest.raises(geometry.GeometryError, match='inverted or unbounded'):
        geometry._step_material_shape(compound(first, second))


@pytest.mark.parametrize('offset', [(2., 2., 0.), (2., 2., 2.)])
def test_step_point_or_edge_touching_is_not_accepted_as_closed_material(tmp_path, offset):
    path = write_step(tmp_path, compound(box(), box(*offset)))
    with pytest.raises(geometry.GeometryError):
        geometry.load_model(path, use_cache=False)


def test_loose_geometry_is_not_silently_discarded(tmp_path):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln
    loose = BRepBuilderAPI_MakeFace(gp_Pln(), -1., 1., -1., 1.).Face()
    path = write_step(tmp_path, compound(box(), box(1.), loose))
    with pytest.raises(geometry.GeometryError, match='outside closed solids'):
        geometry.load_model(path, use_cache=False)


def test_overlapping_stl_still_fails_without_cad_union(tmp_path):
    first = trimesh.creation.box((2, 2, 2))
    second = first.copy(); second.apply_translation((1., .5, .5))
    mesh = trimesh.util.concatenate((first, second))
    with pytest.raises(geometry.GeometryError, match='Self-intersection'):
        geometry.model_from_mesh(mesh, diagnostics={'format': 'STEP'})
    path = tmp_path / 'overlap.stl'; mesh.export(path)
    with pytest.raises(geometry.GeometryError, match='Self-intersection'):
        geometry.load_model(path, use_cache=False)


@pytest.mark.parametrize('failure', ['exception', 'not_done', 'null', 'invalid', 'empty'])
def test_boolean_failures_reject_instead_of_using_unchecked_surfaces(monkeypatch, failure):
    import OCP.BRepAlgoAPI
    import OCP.BRepCheck
    from OCP.TopoDS import TopoDS_Shape
    class FailedUnion:
        def SetArguments(self, value): pass
        def SetTools(self, value): pass
        def SetNonDestructive(self, value): assert value is True
        def SetRunParallel(self, value): assert value is False
        def Build(self):
            if failure == 'exception': raise RuntimeError('OCCT failed')
        def IsDone(self): return failure != 'not_done'
        def Shape(self):
            if failure == 'null': return TopoDS_Shape()
            if failure == 'empty': return compound()
            return box()
    monkeypatch.setattr(OCP.BRepAlgoAPI, 'BRepAlgoAPI_Fuse', FailedUnion)
    if failure == 'invalid':
        class InvalidResult:
            def __init__(self, shape): pass
            def IsValid(self): return False
        monkeypatch.setattr(OCP.BRepCheck, 'BRepCheck_Analyzer', InvalidResult)
    with pytest.raises(geometry.GeometryError):
        geometry._step_material_shape(compound(box(), box(1.)))


def test_disk_cache_restores_union_and_original_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv('POWDERNEST_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.delenv('POWDERNEST_DISABLE_CACHE', raising=False)
    path = write_step(tmp_path, compound(box(), box(1.)))
    first = geometry.load_model(path)
    second = geometry.load_model(path)
    assert second.diagnostics['model_import_cache'] == 'disk'
    assert second.diagnostics['step_material_union_applied'] is True
    assert len(geometry._step_solids(second.original_shape)) == 2
    np.testing.assert_array_equal(first.mesh.vertices, second.mesh.vertices)
    np.testing.assert_array_equal(first.mesh.faces, second.mesh.faces)


def test_material_helpers_invalidate_geometry_proofs(monkeypatch):
    before = geometry.validation_stamp()
    original = geometry._step_material_shape
    def changed(shape): return original(shape)
    monkeypatch.setattr(geometry, '_step_material_shape', changed)
    assert geometry.validation_stamp() != before


def test_boolean_and_meshing_leave_original_brep_serialization_unchanged():
    from OCP.BRepTools import BRepTools
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    original = compound(box(), box(.5, .5, .5, 1.))
    before = io.BytesIO(); BRepTools.Write_s(original, before)
    material, information = geometry._step_material_shape(original)
    assert information['step_material_union_applied']
    assert BRepMesh_IncrementalMesh(material, .02, False, .2, False).IsDone()
    after = io.BytesIO(); BRepTools.Write_s(original, after)
    assert after.getvalue() == before.getvalue()
