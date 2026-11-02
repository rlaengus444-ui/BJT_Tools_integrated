"""Bounded display-only simplification; inspection meshes never enter VTK by reference."""
from __future__ import annotations

import time

import numpy as np

MAX_INPUT_FACES = 2_000_000
MAX_INPUT_VERTICES = 1_200_000
MAX_INPUT_COPY_BYTES = 256 * 1024 * 1024
MAX_ATTEMPTS = 3
MAX_SECONDS = 15.0


class PreviewUnavailable(RuntimeError):
    """An explicit bounds fallback is required for this display geometry."""


def simplify(vertices, faces, face_budget, vertex_budget, *, cancel_event=None):
    """Return owned approximate triangles and checked display topology metadata.

    Limits are checked before importing VTK or materializing the source arrays.
    VTK receives deep copies. Reduction never selects random source triangles.
    """
    if (len(faces) > MAX_INPUT_FACES or len(vertices) > MAX_INPUT_VERTICES
            or 2 * (len(vertices) * 24 + len(faces) * 32) > MAX_INPUT_COPY_BYTES):
        raise PreviewUnavailable("source_memory_limit")
    if face_budget < 4 or vertex_budget < 4:
        raise PreviewUnavailable("display_budget")
    def cancelled():
        return cancel_event is not None and cancel_event.is_set()
    if cancelled():
        raise PreviewUnavailable("cancelled")
    if (not len(vertices) or not len(faces)
            or getattr(vertices, "ndim", 0) != 2 or tuple(vertices.shape[1:]) != (3,)
            or getattr(faces, "ndim", 0) != 2 or tuple(faces.shape[1:]) != (3,)):
        raise PreviewUnavailable("invalid_source_geometry")
    for start in range(0, len(vertices), 65_536):
        if not np.isfinite(np.asarray(vertices[start:start + 65_536])).all():
            raise PreviewUnavailable("invalid_source_geometry")
    for start in range(0, len(faces), 65_536):
        part = np.asarray(faces[start:start + 65_536])
        if not np.issubdtype(part.dtype, np.integer) or np.any(part < 0) or np.any(part >= len(vertices)):
            raise PreviewUnavailable("invalid_source_geometry")
    try:
        from vtkmodules.vtkCommonCore import vtkPoints
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
        from vtkmodules.vtkFiltersCore import vtkDecimatePro, vtkFeatureEdges, vtkPolyDataConnectivityFilter, vtkQuadricDecimation
        from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy
    except ImportError as exc:
        raise PreviewUnavailable("simplifier_unavailable") from exc

    # Quadric emits float32 points in VTK 9.3.1. Normalize a private display
    # copy first so a large placement offset cannot erase small local detail.
    # Restore the original world frame in float64 after validation below.
    raw_vertices = np.asarray(vertices)
    lower = raw_vertices.min(axis=0).astype(np.float64)
    upper = raw_vertices.max(axis=0).astype(np.float64)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        extent = upper - lower
        origin = lower + extent * .5
        scale = float(np.linalg.norm(extent))
    if not np.isfinite(origin).all() or not np.isfinite(scale) or scale <= 0:
        raise PreviewUnavailable("invalid_source_geometry")
    normalized = np.subtract(raw_vertices, origin, dtype=np.float64)
    normalized /= scale
    points = vtkPoints()
    points.SetData(numpy_to_vtk(normalized, deep=True))
    del normalized
    cells = np.empty((len(faces), 4), dtype=np.int64)
    cells[:, 0] = 3
    cells[:, 1:] = faces
    polygons = vtkCellArray()
    polygons.SetCells(len(faces), numpy_to_vtkIdTypeArray(cells.ravel(), deep=True))
    del cells
    source = vtkPolyData()
    source.SetPoints(points)
    source.SetPolys(polygons)
    deadline = time.monotonic() + MAX_SECONDS
    interrupted = []
    errors = []
    def check_progress(caller, _event):
        if cancelled() or time.monotonic() > deadline:
            interrupted.append("cancelled" if cancelled() else "simplification_timeout")
            caller.AbortExecuteOn()
    def observe(algorithm):
        algorithm.AddObserver("ProgressEvent", check_progress)
        algorithm.AddObserver("ErrorEvent", lambda *_: errors.append(True))
    def check_interruption():
        if interrupted or errors or cancelled() or time.monotonic() > deadline:
            reason = interrupted[0] if interrupted else "cancelled" if cancelled() else "simplification_failed" if errors else "simplification_timeout"
            raise PreviewUnavailable(reason)
    boundary_edges = vtkFeatureEdges()
    boundary_edges.SetInputData(source)
    boundary_edges.BoundaryEdgesOn()
    boundary_edges.NonManifoldEdgesOff()
    boundary_edges.FeatureEdgesOff()
    boundary_edges.ManifoldEdgesOff()
    observe(boundary_edges)
    boundary_edges.Update()
    original_boundary_count = boundary_edges.GetOutput().GetNumberOfCells()
    check_interruption()
    boundary_edges.BoundaryEdgesOff()
    boundary_edges.NonManifoldEdgesOn()
    boundary_edges.Update()
    check_interruption()
    if boundary_edges.GetOutput().GetNumberOfCells():
        raise PreviewUnavailable("source_topology_unsupported")
    # For a manifold triangle mesh, 3F = 2E - B. This avoids a full source
    # edge-sort allocation while allowing an independent Euler check below.
    original_euler = len(np.unique(faces)) - (3 * len(faces) + original_boundary_count) // 2 + len(faces)
    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetExtractionModeToAllRegions()
    connectivity.ColorRegionsOff()
    connectivity.SetInputData(source)
    observe(connectivity)
    connectivity.Update()
    original_components = connectivity.GetNumberOfExtractedRegions()
    check_interruption()
    if original_boundary_count:
        # Open surfaces keep their existing boundary vertices. Avoid Pro's
        # default 15-degree feature lock on densely tessellated interior faces.
        decimator = vtkDecimatePro()
        decimator.PreserveTopologyOn()
        decimator.BoundaryVertexDeletionOff()
        decimator.SplittingOff()
        decimator.SetFeatureAngle(180.0)
        decimator.AccumulateErrorOn()
        method = "vtkDecimatePro-boundary-preserving"
    else:
        # Closed mechanical surfaces can gain nonmanifold edges with Pro even
        # when PreserveTopology is enabled. Use VTK's quadric edge collapses and
        # verify manifold incidence, Euler and components independently.
        decimator = vtkQuadricDecimation()
        decimator.VolumePreservationOn()
        method = "vtkQuadricDecimation-volume-preserving"
    decimator.SetInputData(source)
    observe(decimator)
    ratio = min(face_budget / len(faces), vertex_budget / len(vertices), .99) * .94
    for _attempt in range(MAX_ATTEMPTS):
        if cancelled():
            raise PreviewUnavailable("cancelled")
        decimator.SetTargetReduction(1.0 - max(0.0, min(.99, ratio)))
        decimator.Update()
        check_interruption()
        output = decimator.GetOutput()
        count = output.GetNumberOfPolys()
        if (not count or output.GetNumberOfCells() != count or not output.GetPoints()
                or output.GetNumberOfPoints() > MAX_INPUT_VERTICES):
            raise PreviewUnavailable("invalid_simplified_geometry")
        data = vtk_to_numpy(output.GetPolys().GetData())
        if data.size != count * 4:
            raise PreviewUnavailable("invalid_simplified_geometry")
        cells = data.reshape((-1, 4))
        if np.any(cells[:, 0] != 3):
            raise PreviewUnavailable("invalid_simplified_geometry")
        out_faces = np.array(cells[:, 1:], dtype=np.int64, order="C", copy=True)
        out_vertices = np.array(vtk_to_numpy(output.GetPoints().GetData()), dtype=np.float64, order="C", copy=True)
        if (out_vertices.ndim != 2 or out_vertices.shape[1:] != (3,)
                or not np.isfinite(out_vertices).all() or np.any(out_faces < 0)
                or np.any(out_faces >= len(out_vertices))):
            raise PreviewUnavailable("invalid_simplified_geometry")
        used, inverse = np.unique(out_faces, return_inverse=True)
        out_vertices = out_vertices[used]
        out_faces = inverse.reshape((-1, 3))
        if len(out_faces) <= face_budget and len(out_vertices) <= vertex_budget:
            if np.any(out_faces[:, 0] == out_faces[:, 1]) or np.any(out_faces[:, 1] == out_faces[:, 2]) or np.any(out_faces[:, 0] == out_faces[:, 2]):
                raise PreviewUnavailable("invalid_simplified_geometry")
            triangles = out_vertices[out_faces]
            with np.errstate(over="ignore", invalid="ignore", under="ignore"):
                normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            if not np.isfinite(normals).all() or np.any(np.all(normals == 0, axis=1)):
                raise PreviewUnavailable("invalid_simplified_geometry")
            edges = np.sort(np.concatenate((out_faces[:, [0, 1]], out_faces[:, [1, 2]], out_faces[:, [2, 0]])), axis=1)
            unique, incidence = np.unique(edges, axis=0, return_counts=True)
            # Preserve flags alone are insufficient for all VTK inputs. Never
            # accept new boundary holes or nonmanifold edges in a preview.
            displayed_boundary_count = int(np.count_nonzero(incidence == 1))
            displayed_euler = len(out_vertices) - len(unique) + len(out_faces)
            connectivity.SetInputData(output)
            connectivity.Update()
            displayed_components = connectivity.GetNumberOfExtractedRegions()
            if (displayed_boundary_count != original_boundary_count or np.any(incidence > 2)
                    or displayed_euler != original_euler or displayed_components != original_components):
                raise PreviewUnavailable("simplification_topology_unattainable")
            check_interruption()
            world_vertices = out_vertices * scale + origin
            if not np.isfinite(world_vertices).all():
                raise PreviewUnavailable("invalid_simplified_geometry")
            # Restoring a very large world offset can collapse otherwise valid
            # local points through float64 rounding. Validate the actual final
            # coordinates too, scaling edge differences to avoid area overflow.
            world_triangles = world_vertices[out_faces]
            with np.errstate(over="ignore", invalid="ignore", under="ignore"):
                world_normals = np.cross((world_triangles[:, 1] - world_triangles[:, 0]) / scale,
                                         (world_triangles[:, 2] - world_triangles[:, 0]) / scale)
            if not np.isfinite(world_normals).all() or np.any(np.all(world_normals == 0, axis=1)):
                raise PreviewUnavailable("invalid_simplified_geometry")
            return world_vertices, out_faces, {"method": method, "topology_invariants_checked": True,
                "coordinate_frame": "normalized-local-to-original-world",
                "source_boundary_edges": original_boundary_count, "display_boundary_edges": displayed_boundary_count,
                "source_euler": original_euler, "display_euler": displayed_euler,
                "source_components": original_components, "display_components": displayed_components}
        ratio *= min(face_budget / len(out_faces), vertex_budget / len(out_vertices)) * .85
    raise PreviewUnavailable("simplification_budget_unattainable")
