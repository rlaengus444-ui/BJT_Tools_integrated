"""Validated solid meshes, STEP tessellation, and shared FCL collision geometry.

All coordinates are millimetres.  STL triangles are the supplied geometry; STEP
uses a declared tessellation allowance, not a certified BRep Hausdorff bound.
No convex-hull or bounding-box approximation can produce a mesh-mode success.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Iterable
import math
import numbers
import json
import hashlib
import sys
from types import CodeType
from importlib.metadata import version, PackageNotFoundError

import fcl
import numpy as np
import trimesh
from rtree import index as rtree_index

from .cancellation import check_cancelled, report_progress
from .rotations import ROTATIONS
from .types import Model, Placement, Settings


class GeometryError(ValueError):
    """Input cannot be trusted as a closed, consistently oriented solid."""


_UNIT_SCALE = {"mm": 1., "cm": 10., "m": 1000., "in": 25.4,
               "inch": 25.4, "inches": 25.4, "um": .001, "µm": .001}

_stamp_token = _stamp_value = _dependency_versions = None


def _code_signature(code):
    """Portable code fingerprint, independent of marshal string interning."""
    def constant(value):
        if isinstance(value, CodeType): return ('code', _code_signature(value))
        if isinstance(value, tuple): return ('tuple', [constant(v) for v in value])
        if isinstance(value, frozenset): return ('frozenset', sorted((constant(v) for v in value), key=repr))
        if isinstance(value, bytes): return ('bytes', value.hex())
        if isinstance(value, float): return ('float', value.hex())
        return (type(value).__name__, repr(value))
    information = (code.co_code.hex(), [constant(v) for v in code.co_consts], code.co_names,
                   code.co_varnames, code.co_freevars, code.co_cellvars, code.co_argcount,
                   code.co_posonlyargcount, code.co_kwonlyargcount, code.co_flags,
                   code.co_exceptiontable.hex())
    return hashlib.sha256(json.dumps(information, ensure_ascii=True).encode()).hexdigest()


def validation_stamp():
    """Invalidate proofs when any loader/predicate code or dependency changes."""
    global _stamp_token, _stamp_value, _dependency_versions
    names = ('_geometry_epsilon', '_winding_numbers', '_polygon_clip', '_plane_section',
             '_orient2d_with_error', '_projected_vertex_separation', '_invalid_triangle_intersection',
             '_check_vertex_manifold', '_check_self_intersections', '_canonical_mesh', '_shell_information',
             'model_from_mesh', 'revalidate_model', '_weld_cad_vertices', '_conform_cad_collinear_facets',
             '_step_solids', '_require_bounded_step_solids', '_step_material_shape',
             '_read_step_shape', '_load_step', 'load_model',
             '_load_stl', '_position_array', '_code_signature')
    functions = [(name, globals()[name]) for name in names]
    functions.append(('Settings.boundary_margins', Settings.boundary_margins))
    engine = globals().get('CollisionEngine')
    if engine is not None:
        functions.extend(('CollisionEngine.' + name, value) for name, value in vars(engine).items()
                         if hasattr(value, '__code__'))
    functions.sort(key=lambda pair: pair[0])
    rotation_bytes = np.asarray(ROTATIONS).tobytes()
    unit_bytes = json.dumps(_UNIT_SCALE, sort_keys=True).encode()
    from .native import fingerprint
    from .interlocking import predicate_stamp
    backend_signature = fingerprint() + ':' + predicate_stamp()
    token = (tuple((name, id(value)) for name, value in functions), rotation_bytes, unit_bytes, backend_signature)
    if token != _stamp_token:
        if _dependency_versions is None:
            _dependency_versions = [str(sys.version_info[:3])]
            for package in ('numpy', 'trimesh', 'python-fcl', 'cadquery-ocp', 'rtree'):
                try: _dependency_versions.append(package + '=' + version(package))
                except PackageNotFoundError: _dependency_versions.append(package + '=unknown')
        digest = hashlib.sha256(('geometry-v1' + '|'.join(_dependency_versions)).encode())
        for name, function in functions:
            digest.update(name.encode()); digest.update(_code_signature(function.__code__).encode())
            expected_module = Settings.__module__ if name == 'Settings.boundary_margins' else __name__
            if function.__module__ != expected_module or function.__closure__:
                digest.update(str(id(function)).encode())
        digest.update(rotation_bytes); digest.update(unit_bytes)
        digest.update(backend_signature.encode())
        _stamp_token, _stamp_value = token, digest.hexdigest()
    return _stamp_value


def _position_array(position) -> np.ndarray:
    array = np.asarray(position)
    if (array.shape != (3,) or
            any(isinstance(v, (bool, np.bool_)) or not isinstance(v, numbers.Real) for v in position)):
        raise ValueError("Position must contain three real numeric coordinates, excluding booleans.")
    array = array.astype(float)
    if not np.isfinite(array).all():
        raise ValueError("Position coordinates must be finite.")
    return array


def _geometry_epsilon(vertices: np.ndarray) -> float:
    return max(1e-10, 64 * np.finfo(float).eps *
               max(1., float(np.abs(vertices).max())))


def _winding_numbers(triangles: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Oriented solid angles, including subtractive inward cavity shells."""
    points = np.asarray(points, dtype=float).reshape((-1, 3))
    result = np.zeros(len(points))
    # Bound temporary memory for large CAD triangulations and many components.
    for start in range(0, len(triangles), 8192):
        check_cancelled()
        chunk = triangles[start:start + 8192]
        for i, point in enumerate(points):
            a, b, c = (chunk[:, j] - point for j in range(3))
            la, lb, lc = (np.linalg.norm(v, axis=1) for v in (a, b, c))
            numerator = np.einsum("ij,ij->i", a, np.cross(b, c))
            denominator = (la * lb * lc +
                           np.einsum("ij,ij->i", a, b) * lc +
                           np.einsum("ij,ij->i", b, c) * la +
                           np.einsum("ij,ij->i", c, a) * lb)
            result[i] += np.sum(2 * np.arctan2(numerator, denominator))
    return result / (4 * np.pi)


def _polygon_clip(subject: list[np.ndarray], triangle: np.ndarray,
                  epsilon: float) -> list[np.ndarray]:
    """Convex 2D clipping used only by the import self-intersection check."""
    orient = np.sign(np.linalg.det(np.array([triangle[1] - triangle[0],
                                             triangle[2] - triangle[0]])))
    if not orient:
        return []
    for edge in range(3):
        if not subject:
            break
        first, last = triangle[edge], triangle[(edge + 1) % 3]
        direction = last - first
        length = np.linalg.norm(direction)

        def distance(p):
            q = p - first
            return orient * (direction[0] * q[1] - direction[1] * q[0]) / length

        output = []
        a = subject[-1]
        da = distance(a)
        for b in subject:
            db = distance(b)
            if (da >= -epsilon) != (db >= -epsilon):
                denominator = da - db
                if abs(denominator) > np.finfo(float).tiny:
                    output.append(a + np.clip(da / denominator, 0., 1.) * (b - a))
            if db >= -epsilon:
                output.append(b)
            a, da = b, db
        subject = output
    return subject


def _plane_section(triangle: np.ndarray, distances: np.ndarray,
                   epsilon: float) -> np.ndarray:
    points = [triangle[i] for i in range(3) if abs(distances[i]) <= epsilon]
    for i, j in ((0, 1), (1, 2), (2, 0)):
        if (distances[i] < -epsilon and distances[j] > epsilon or
                distances[j] < -epsilon and distances[i] > epsilon):
            points.append(triangle[i] + distances[i] /
                          (distances[i] - distances[j]) * (triangle[j] - triangle[i]))
    return np.asarray(points)


def _orient2d_with_error(a: np.ndarray, b: np.ndarray,
                         c: np.ndarray) -> tuple[float, float]:
    """A determinant and a conservative binary64 roundoff bound.

    The bound covers the coordinate subtractions, two products and final
    subtraction (with room beyond the usual orient2d first-stage bound).
    An uncertain sign is never used as evidence of separation.
    """
    u, v = b - a, c - a
    left, right = float(u[0] * v[1]), float(u[1] * v[0])
    return left - right, 16 * np.finfo(float).eps * (abs(left) + abs(right))


def _projected_vertex_separation(a: np.ndarray, b: np.ndarray,
                                 vertex: np.ndarray, epsilon: float) -> bool:
    """Certify that two triangles can meet only at their common vertex.

    A projection with resolved, nonzero areas is injective on each facet.  If
    an edge through the common vertex separates their projected interiors,
    with the other facet's two nonshared vertices strictly on the opposite
    side, their only possible 3D intersection is the common vertex.  This
    proof does not depend on normals of skinny, almost coplanar triangles.
    """
    other_a = a[~np.all(a == vertex, axis=1)]
    other_b = b[~np.all(b == vertex, axis=1)]
    if len(other_a) != 2 or len(other_b) != 2:
        return False
    for axes in ((0, 1), (0, 2), (1, 2)):
        origin = vertex[list(axes)]
        projected = (other_a[:, axes], other_b[:, axes])
        resolved = True
        for triangle in projected:
            area, error = _orient2d_with_error(origin, *triangle)
            longest = max(np.linalg.norm(triangle[0] - origin),
                          np.linalg.norm(triangle[1] - origin),
                          np.linalg.norm(triangle[1] - triangle[0]))
            if not math.isfinite(area) or abs(area) <= error + epsilon * longest:
                resolved = False
                break
        if not resolved:
            continue
        for first, second in (projected, projected[::-1]):
            for edge, corner in (first, first[::-1]):
                area, error = _orient2d_with_error(origin, edge, corner)
                margin = epsilon * np.linalg.norm(edge - origin)
                if abs(area) <= error + margin:
                    continue
                side = math.copysign(1., area)
                distances = [_orient2d_with_error(origin, edge, point)
                             for point in second]
                if all(math.isfinite(value) and side * value < -bound - margin
                       for value, bound in distances):
                    return True
    return False


def _invalid_triangle_intersection(a: np.ndarray, b: np.ndarray,
                                   shared: np.ndarray, epsilon: float, *, _normals=None) -> bool:
    """Return true for intersection beyond a common topological edge/vertex.

    Plane sections handle skew triangles; coplanar convex clipping also catches
    folded adjacent facets and nonadjacent coplanar overlaps.  Features within
    epsilon cannot be certified and are rejected conservatively.
    """
    if _normals is None:
        na, nb = np.cross(a[1] - a[0], a[2] - a[0]), np.cross(b[1] - b[0], b[2] - b[0])
        na, nb = na / np.linalg.norm(na), nb / np.linalg.norm(nb)
    else:
        na, nb = _normals
    da, db = (a - b[0]) @ nb, (b - a[0]) @ na
    if (np.min(da) > epsilon or np.max(da) < -epsilon or
            np.min(db) > epsilon or np.max(db) < -epsilon):
        return False
    axis = np.cross(na, nb)
    axis_length = np.linalg.norm(axis)
    if np.max(np.abs(da)) <= epsilon and np.max(np.abs(db)) <= epsilon:
        drop = int(np.argmax(np.abs(na)))
        a2, b2 = np.delete(a, drop, axis=1), np.delete(b, drop, axis=1)
        polygon = _polygon_clip(list(a2), b2, epsilon)
        if not polygon:
            return False
        if not len(shared):
            return True
        shared2 = np.delete(shared, drop, axis=1)
        for point in polygon:
            if len(shared2) == 1:
                distance = np.linalg.norm(point - shared2[0])
            else:
                segment = shared2[1] - shared2[0]
                t = np.clip(np.dot(point - shared2[0], segment) /
                            np.dot(segment, segment), 0., 1.)
                distance = np.linalg.norm(point - shared2[0] - t * segment)
            if distance > 4 * epsilon:
                return True
        return False
    if axis_length <= 32 * np.finfo(float).eps:
        # A numerically unresolved plane relation is not evidence of a solid
        # being intersection-free.  Do not silently accept nearly parallel
        # crossing facets at very large scales.
        return (len(shared) != 1 or
                not _projected_vertex_separation(a, b, shared[0], epsilon))
    if len(shared) >= 2:
        # The intersection line is exactly the common edge.
        return False
    section_a, section_b = _plane_section(a, da, epsilon), _plane_section(b, db, epsilon)
    if not len(section_a) or not len(section_b):
        return False
    axis /= axis_length
    # Subtract a local origin to avoid losing interval precision far from zero.
    origin = a[0]
    pa, pb = (section_a - origin) @ axis, (section_b - origin) @ axis
    low, high = max(pa.min(), pb.min()), min(pa.max(), pb.max())
    if low > high + epsilon:
        return False
    if not len(shared):
        return True
    shared_projection = float(np.dot(shared[0] - origin, axis))
    beyond_vertex = max(abs(low - shared_projection), abs(high - shared_projection)) > 4 * epsilon
    # Normal directions of skinny facets can amplify input rounding into an
    # erroneous long plane section.  Require an independent, conservative
    # separation certificate before overturning a shared-vertex rejection.
    return beyond_vertex and not _projected_vertex_separation(a, b, shared[0], epsilon)


def _check_vertex_manifold(mesh: trimesh.Trimesh):
    """Each closed-surface vertex must have one connected circular face link.

    Edge watertightness alone permits two otherwise closed shells joined at a
    single vertex, or a pinched surface.  Halfedge cycles reject those cases.
    """
    faces = np.asarray(mesh.faces)
    edges = faces[:, [[0, 1], [1, 2], [2, 0]]].reshape((-1, 2))
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0,
                                   return_inverse=True, return_counts=True)
    if np.any(counts != 2):
        raise GeometryError("Every solid-surface edge must have exactly two incident faces.")
    paired = np.argsort(inverse).reshape((-1, 2))
    opposite = np.empty(len(edges), dtype=np.int64)
    opposite[paired[:, 0]], opposite[paired[:, 1]] = paired[:, 1], paired[:, 0]
    previous = np.arange(len(edges)).reshape((-1, 3))[:, [2, 0, 1]].ravel()
    successor = opposite[previous]
    visited, vertex_seen = np.zeros(len(edges), dtype=bool), np.zeros(len(mesh.vertices), dtype=bool)
    for start in range(len(edges)):
        check_cancelled()
        if visited[start]:
            continue
        vertex = edges[start, 0]
        if vertex_seen[vertex]:
            raise GeometryError("Nonmanifold vertex: separate closed shells touch at a point or the surface is pinched.")
        vertex_seen[vertex] = True
        current = start
        while not visited[current]:
            visited[current] = True
            current = successor[current]


_REFERENCE_TRIANGLE_FUNCTIONS = {name: globals()[name] for name in (
    '_invalid_triangle_intersection', '_polygon_clip', '_plane_section',
    '_projected_vertex_separation', '_orient2d_with_error')}


def _check_self_intersections(mesh: trimesh.Trimesh, epsilon: float) -> int:
    check_cancelled()
    triangles = np.asarray(mesh.triangles)
    # Identical scalar arithmetic to the uncached predicate; calculate a face
    # normal once, rather than twice for each of its many candidate neighbours.
    normals = np.empty((len(triangles), 3))
    for i, triangle in enumerate(triangles):
        check_cancelled()
        normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        normals[i] = normal / np.linalg.norm(normal)
    lows, highs = triangles.min(axis=1), triangles.max(axis=1)
    props = rtree_index.Property(dimension=3)
    tree = rtree_index.Index(((i, tuple(np.r_[lo, hi]), None)
                              for i, (lo, hi) in enumerate(zip(lows, highs))),
                             properties=props)
    tested = 0
    faces = np.asarray(mesh.faces)
    vertices = np.asarray(mesh.vertices)
    from . import native
    accelerated = (native.library() is not None and
                   all(globals()[name] is function for name, function in _REFERENCE_TRIANGLE_FUNCTIONS.items()))
    batch = native.TriangleBatch(triangles, normals, faces) if accelerated else None
    pending = []
    def check_batch():
        if not pending: return
        check_cancelled()
        flags = batch.classify(np.asarray(pending, dtype=np.int64), epsilon)
        for (i, j), flag in zip(pending, flags):
            check_cancelled()
            if flag == 2:
                common = sorted(set(map(int, faces[i])) & set(map(int, faces[j])))
                flag = _invalid_triangle_intersection(triangles[i], triangles[j], vertices[common], epsilon,
                                                     _normals=(normals[i], normals[j]))
            if flag == 1:
                raise GeometryError(f"Self-intersection or touching separate shells at triangles {i} and {j}; "
                                    "repair the source solid before importing it.")
        pending.clear()
    try:
        for i in range(len(triangles)):
            check_cancelled()
            if i % 256 == 0:
                if batch is not None: check_batch()
                report_progress(f'면 자기 교차 검사 {i:,}/{len(triangles):,}', completed=i, total_work=len(triangles))
            query = tuple(np.r_[lows[i] - epsilon, highs[i] + epsilon])
            face = tuple(map(int, faces[i]))
            for j in tree.intersection(query):
                check_cancelled()
                if j <= i:
                    continue
                if batch is not None:
                    tested += 1; pending.append((i, j))
                    if len(pending) >= 4096: check_batch()
                    continue
                b0, b1, b2 = map(int, faces[j])
                common = sorted(vertex for vertex in face if vertex == b0 or vertex == b1 or vertex == b2)
                tested += 1
                if _invalid_triangle_intersection(triangles[i], triangles[j],
                                                  vertices[common], epsilon,
                                                  _normals=(normals[i], normals[j])):
                    raise GeometryError(f"Self-intersection or touching separate shells at triangles {i} and {j}; "
                                        "repair the source solid before importing it.")
        if batch is not None: check_batch()
    finally:
        tree.close()
    return tested


def _canonical_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    check_cancelled()
    if not isinstance(mesh, trimesh.Trimesh):
        raise GeometryError("Expected a triangle mesh, not an empty file or a scene.")
    vertices, faces = np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1:] != (3,) or not len(vertices) or
            faces.ndim != 2 or faces.shape[1:] != (3,) or not len(faces)):
        raise GeometryError("The model has no usable triangular surface.")
    if not np.isfinite(vertices).all():
        raise GeometryError("The mesh contains NaN or infinite coordinates.")
    if (not np.issubdtype(faces.dtype, np.integer) or np.min(faces) < 0 or
            np.max(faces) >= len(vertices)):
        raise GeometryError("The mesh has invalid triangle vertex indices.")
    # STL repeats every facet's vertices.  Exact welding changes only indexing;
    # it never moves a point, fills a hole, flips a normal, or deletes a face.
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    used, reindex = np.unique(inverse[faces], return_inverse=True)
    return trimesh.Trimesh(vertices=unique[used], faces=reindex.reshape((-1, 3)), process=False)


def _shell_information(mesh: trimesh.Trimesh, epsilon: float) -> tuple[list[np.ndarray], dict]:
    components = list(trimesh.graph.connected_components(
        mesh.face_adjacency, nodes=np.arange(len(mesh.faces)), min_len=1))
    triangles = np.asarray(mesh.triangles)
    representatives = [triangles[group[0], 0] for group in components]
    volumes, depth = [], []
    for i, group in enumerate(components):
        check_cancelled()
        t = triangles[group]
        origin = t[0, 0]
        shifted = t - origin
        volume = float(np.einsum("ij,ij->i", shifted[:, 0],
                                np.cross(shifted[:, 1], shifted[:, 2])).sum() / 6)
        if abs(volume) <= epsilon ** 3:
            raise GeometryError("A closed shell has no numerically reliable enclosed volume.")
        volumes.append(volume)
        containers = 0
        for j, other in enumerate(components):
            if i != j:
                winding = abs(float(_winding_numbers(triangles[other], [representatives[i]])[0]))
                if abs(winding - .5) < 1e-6:
                    raise GeometryError("Shell nesting is numerically ambiguous.")
                containers += winding > .5
        depth.append(containers)
    outer_signs = {int(np.sign(v)) for v, d in zip(volumes, depth) if d == 0}
    if len(outer_signs) != 1:
        raise GeometryError("Disconnected outer shells use inconsistent winding directions.")
    orientation = next(iter(outer_signs))
    for volume, nesting in zip(volumes, depth):
        if np.sign(volume) != orientation * (-1) ** nesting:
            raise GeometryError("Nested shells have inconsistent winding: a hollow cavity must "
                                "face inward. Export a valid oriented solid from the source CAD.")
    return components, {"shell_count": len(components), "shell_depths": depth,
                        "signed_shell_volumes_mm3": volumes, "global_orientation": orientation,
                        "material_volume_mm3": abs(sum(volumes))}


def model_from_mesh(mesh: trimesh.Trimesh, model_id: str = "model", name: str | None = None,
                    *, source_path: str = "", source_unit: str = "mm",
                    tessellation_error: float = 0., original_shape=None,
                    diagnostics: dict | None = None,
                    allowed_rotations: Iterable[int] | None = None,
                    use_cache: bool = False) -> Model:
    """Validate without geometric repair; mesh coordinates must already be mm."""
    check_cancelled()
    if not isinstance(mesh, trimesh.Trimesh):
        raise GeometryError('Expected a triangle mesh, not an empty file or a scene.')
    if not isinstance(model_id, str) or not model_id:
        raise GeometryError("A nonempty model identifier is required.")
    report_progress(f'모델 형상 검사: {name or model_id}', force=True)
    if (not isinstance(tessellation_error, numbers.Real) or isinstance(tessellation_error, (bool, np.bool_)) or
            not np.isfinite(tessellation_error) or tessellation_error < 0):
        raise GeometryError("Tessellation allowance must be a finite nonnegative number.")
    rotations = tuple(range(24)) if allowed_rotations is None else tuple(allowed_rotations)
    if (not rotations or len(set(rotations)) != len(rotations) or
            any(not isinstance(r, numbers.Integral) or isinstance(r, bool) or
                r < 0 or r >= len(ROTATIONS) for r in rotations)):
        raise GeometryError("Allowed rotations must be unique IDs from 0 through 23.")
    from . import cache
    stamp = validation_stamp()
    if use_cache:
        record = cache.get_geometry(stamp, mesh)
        if record is not None:
            vertices, faces = record.arrays()
            canonical = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            information = dict(diagnostics or {})
            information.update(json.loads(record.information))
            information['geometry_validation_cache'] = 'memory'
            result = Model(model_id=model_id, name=name or model_id, mesh=canonical,
                         source_path=str(source_path), source_unit=source_unit,
                         tessellation_error=float(tessellation_error), original_shape=original_shape,
                         diagnostics=information, allowed_rotations=rotations)
            cache.attach_receipt(result, stamp, record.information)
            return result
    original_digest = cache.mesh_digest(mesh) if cache.enabled() else None
    canonical = _canonical_mesh(mesh)
    epsilon = _geometry_epsilon(canonical.vertices)
    triangles = np.asarray(canonical.triangles)
    edge_scale = np.linalg.norm(triangles[:, [1, 2, 0]] - triangles, axis=2).max(axis=1)
    double_area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                         triangles[:, 2] - triangles[:, 0]), axis=1)
    if np.any(double_area <= epsilon * np.maximum(edge_scale, epsilon)):
        raise GeometryError("The mesh contains degenerate or numerically unresolved triangles.")
    if len(np.unique(np.sort(canonical.faces, axis=1), axis=0)) != len(canonical.faces):
        raise GeometryError("The mesh contains duplicate or coincident triangles.")
    if not canonical.is_watertight:
        raise GeometryError("The mesh is not closed and watertight (holes or nonmanifold edges). "
                            "Repair the source; no holes are filled automatically.")
    if not canonical.is_winding_consistent:
        raise GeometryError("Triangle winding is inconsistent; export a consistently oriented closed solid.")
    _check_vertex_manifold(canonical)
    tested = _check_self_intersections(canonical, epsilon)
    _, shell_info = _shell_information(canonical, epsilon)
    trusted = dict(shell_info)
    trusted.update({"watertight": True, "winding_consistent": True, "vertex_manifold": True,
                        "self_intersection_check": "triangle plane sections and coplanar clipping, R-tree candidates",
                        "self_intersection_candidate_pairs": tested,
                        "geometry_epsilon_mm": epsilon, "vertices": len(canonical.vertices),
                        "triangles": len(canonical.faces), "automatic_repairs": False})
    information = dict(diagnostics or {})
    information.update(trusted)
    information['geometry_validation_cache'] = 'fresh'
    # Only bind the raw alias if the caller left the source arrays unchanged.
    stable_alias = original_digest if cache.mesh_digest(mesh) == original_digest else None
    cache.remember_geometry(stamp, canonical, trusted, alias=stable_alias)
    result = Model(model_id=model_id, name=name or model_id, mesh=canonical,
                 source_path=str(source_path), source_unit=source_unit,
                 tessellation_error=float(tessellation_error), original_shape=original_shape,
                 diagnostics=information, allowed_rotations=rotations)
    cache.attach_receipt(result, stamp, trusted)
    return result


def revalidate_model(model: Model, model_id: str | None = None, *, use_cache=True) -> Model:
    """Check actual mesh bytes; reuse only a private, completed geometry proof."""
    check_cancelled()
    identifier = model.model_id if model_id is None else model_id
    if identifier != model.model_id:
        raise GeometryError("Model dictionary key must match model.model_id.")
    from . import cache
    if use_cache and cache.enabled() and isinstance(model.mesh, trimesh.Trimesh) and hasattr(model, '_geometry_receipt'):
        vertices, faces = np.asarray(model.mesh.vertices), np.asarray(model.mesh.faces)
        if (vertices.ndim != 2 or vertices.shape[1:] != (3,) or faces.ndim != 2 or faces.shape[1:] != (3,)
                or not np.issubdtype(faces.dtype, np.integer)):
            return model_from_mesh(model.mesh, model_id=identifier, name=model.name,
                source_path=model.source_path, source_unit=model.source_unit, tessellation_error=model.tessellation_error,
                original_shape=model.original_shape, diagnostics=model.diagnostics, allowed_rotations=model.allowed_rotations)
        snapshot = trimesh.Trimesh(vertices=vertices.copy(), faces=faces.copy(), process=False)
        stamp = validation_stamp()
        information = cache.receipt_information(model, stamp, snapshot)
        if information is not None:
            trusted = json.loads(information); diagnostics = dict(model.diagnostics)
            diagnostics.update(trusted); diagnostics['geometry_validation_cache'] = 'memory'
            result = Model(identifier, model.name, snapshot, source_path=model.source_path,
                           source_unit=model.source_unit, tessellation_error=model.tessellation_error,
                           original_shape=model.original_shape, diagnostics=diagnostics,
                           allowed_rotations=model.allowed_rotations)
            cache.attach_receipt(result, stamp, information)
            return result
    return model_from_mesh(model.mesh, model_id=identifier, name=model.name,
                           source_path=model.source_path, source_unit=model.source_unit,
                           tessellation_error=model.tessellation_error,
                           original_shape=model.original_shape, diagnostics=model.diagnostics,
                           allowed_rotations=model.allowed_rotations, use_cache=use_cache)


def _weld_cad_vertices(vertices: np.ndarray, faces: np.ndarray,
                       tolerance: float) -> tuple[np.ndarray, np.ndarray, float]:
    """Join CAD face seams within OCCT's numerical tolerance, recording motion."""
    from scipy.spatial import cKDTree
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    parents = np.arange(len(unique))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for a, b in cKDTree(unique).query_pairs(tolerance, output_type="ndarray"):
        ra, rb = find(a), find(b)
        if ra != rb:
            parents[max(ra, rb)] = min(ra, rb)
    roots = np.asarray([find(i) for i in range(len(unique))])
    displacement = float(np.linalg.norm(unique - unique[roots], axis=1).max(initial=0))
    root_vertices, indices = np.unique(roots, return_inverse=True)
    return unique[root_vertices], indices[inverse[faces]], displacement


def _conform_cad_collinear_facets(vertices: np.ndarray, faces: np.ndarray,
                                cad_tolerance: float) -> tuple[np.ndarray, dict]:
    """Retriangulate numeric zero-area OCCT seam facets without moving points.

    A closed triangulation can contain ABC with B on edge AC. Simply deleting
    ABC opens a T junction. Replace the neighboring triangle ACD by ABD/BCD and
    remove ABC. This is restricted to roundoff-level collinearity, an interior
    segment point and one nondegenerate, oppositely oriented edge neighbor.
    Only the STEP adapter calls this after validating the unchanged BRep.
    STL and public raw meshes still undergo strict rejection, never cleanup.
    """
    check_cancelled()
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    epsilon = _geometry_epsilon(vertices)
    triangles = vertices[faces]
    lengths = np.linalg.norm(triangles[:, [1, 2, 0]] - triangles, axis=2)
    areas = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0],
                                    triangles[:, 2]-triangles[:, 0]), axis=1)
    bad = np.flatnonzero(areas <= epsilon*np.maximum(lengths.max(axis=1), epsilon))
    info = {'collinear_cad_facets_retriangulated': 0,
            'cad_retriangulation_vertex_motion_mm': 0.,
            'cad_retriangulation_measured_deviation_sum_mm': 0.,
            'cad_retriangulation_surface_change_bound_mm': 0.,
            'cad_retriangulation_operations': []}
    if not len(bad):
        return faces, info
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise GeometryError('OCCT triangulation has zero-area facets on a nonclosed or inconsistently oriented surface; '
                            'safe seam retriangulation is not possible.')
    candidates, needed = [], {}
    for index in bad:
        check_cancelled()
        face = faces[index]
        longest = int(np.argmax(lengths[index]))
        a, c, b = (int(face[(longest+k) % 3]) for k in range(3))
        direction = vertices[c]-vertices[a]
        squared = float(direction @ direction)
        if squared <= epsilon**2:
            raise GeometryError('OCCT triangulation has a collapsed non-seam facet.')
        parameter = float((vertices[b]-vertices[a]) @ direction / squared)
        deviation = float(np.linalg.norm(vertices[b]-(vertices[a]+parameter*direction)))
        bound = min(float(cad_tolerance), epsilon, 32*np.finfo(float).eps*
                    max(1., float(np.abs(vertices[face]).max()), math.sqrt(squared)))
        if not 0 < parameter < 1 or deviation > bound:
            raise GeometryError('OCCT triangulation has a degenerate facet beyond floating-point seam roundoff; '
                                'automatic geometry changes are not allowed.')
        key = tuple(sorted((a, c)))
        needed.setdefault(key, set())
        candidates.append((int(index), a, b, c, deviation, bound))
    # Index only the needed edges, not a second whole-mesh topology structure.
    def edges(row):
        return (tuple(sorted((int(row[k]), int(row[(k+1) % 3])))) for k in range(3))
    for index, row in enumerate(faces):
        if index % 256 == 0:
            check_cancelled()
        for key in edges(row):
            if key in needed:
                needed[key].add(index)
    rows, removed, bad_set = faces.tolist(), set(), set(map(int, bad))
    for index, a, b, c, deviation, bound in candidates:
        check_cancelled()
        owners = needed[tuple(sorted((a, c)))]
        if len(owners) != 2 or index not in owners:
            raise GeometryError('Ambiguous OCCT seam adjacency; safe retriangulation refused.')
        neighbor = next(i for i in owners if i != index)
        if neighbor in bad_set or neighbor in removed:
            raise GeometryError('Adjacent degenerate OCCT facets require a new CAD tessellation.')
        row = rows[neighbor]
        slot = next(k for k in range(3) if {row[k], row[(k+1) % 3]} == {a, c})
        start, end, opposite = row[slot], row[(slot+1) % 3], row[(slot+2) % 3]
        # The original degenerate triangle has oriented long edge a -> c.
        if start != c or end != a or opposite == b:
            raise GeometryError('OCCT seam edge orientation is inconsistent; retriangulation refused.')
        replacements = [[start, b, opposite], [b, end, opposite]]
        fresh = vertices[np.asarray(replacements)]
        fresh_areas = np.linalg.norm(np.cross(fresh[:, 1]-fresh[:, 0], fresh[:, 2]-fresh[:, 0]), axis=1)
        fresh_edges = np.linalg.norm(fresh[:, [1, 2, 0]]-fresh, axis=2).max(axis=1)
        if np.any(fresh_areas <= epsilon*np.maximum(fresh_edges, epsilon)):
            raise GeometryError('OCCT seam split would produce another unresolved triangle.')
        for old_index in (index, neighbor):
            removed.add(old_index)
            for key in edges(rows[old_index]):
                if key in needed:
                    needed[key].discard(old_index)
        for replacement in replacements:
            new_index = len(rows)
            rows.append(replacement)
            for key in edges(replacement):
                if key in needed:
                    needed[key].add(new_index)
        info['cad_retriangulation_operations'].append({
            'zero_area_triangle': index, 'neighbor_triangle': neighbor,
            'edge_vertices': [a, c], 'interior_vertex': b,
            'point_segment_deviation_mm': deviation, 'roundoff_bound_mm': bound})
        info['cad_retriangulation_measured_deviation_sum_mm'] += deviation
        # Include arithmetic uncertainty as well as the measured residual; a
        # rounded projection can otherwise report zero for a nonzero residual.
        info['cad_retriangulation_surface_change_bound_mm'] += deviation + bound
    result = np.asarray([row for index, row in enumerate(rows) if index not in removed], dtype=np.int64)
    info['collinear_cad_facets_retriangulated'] = len(candidates)
    return result, info


def _step_solids(shape):
    """Collect positioned solid leaves without discarding loose STEP geometry."""
    from OCP.TopAbs import TopAbs_SOLID, TopAbs_COMPOUND, TopAbs_COMPSOLID
    from OCP.TopoDS import TopoDS_Iterator
    check_cancelled()
    kind = shape.ShapeType()
    if kind == TopAbs_SOLID:
        return [shape]
    if kind not in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
        raise GeometryError('STEP contains geometry outside closed solids; export solid bodies only.')
    solids = []
    children = TopoDS_Iterator(shape)
    while children.More():
        solids.extend(_step_solids(children.Value()))
        children.Next()
    return solids


def _require_bounded_step_solids(solids):
    """Refuse complemented solids: a valid BRep can enclose infinity."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.Precision import Precision
    from OCP.TopAbs import TopAbs_OUT
    for solid in solids:
        check_cancelled()
        classifier = BRepClass3d_SolidClassifier(solid)
        classifier.PerformInfinitePoint(float(Precision.Confusion_s()))
        if classifier.State() != TopAbs_OUT:
            raise GeometryError('STEP contains an inverted or unbounded solid; '
                                'export consistently outward-oriented CAD bodies before material union.')


def _step_material_shape(shape):
    """Union STEP body material before meshing; retain the source BRep itself.

    CAD compounds may contain overlapping bodies (including contained thread
    fragments). Concatenating their surfaces is not the boundary of material.
    Use OCCT's non-destructive Boolean union with its default tolerance, then
    subject the result to the same strict mesh checks as every other input.
    Raw STL meshes never enter this path. Point/edge contacts remain invalid.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopTools import TopTools_ListOfShape
    # Even non-destructive Booleans can update shared TopoDS flags. Isolate
    # topology and geometry before both the union and subsequent meshing.
    shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    solids = _step_solids(shape)
    if not solids:
        raise GeometryError('STEP contains no closed solid material.')
    information = {'source_solid_count': len(solids), 'material_solid_count': len(solids),
                   'step_material_union_applied': False,
                   'step_material_union_method': 'single solid; no Boolean operation'}
    if len(solids) == 1:
        return shape, information
    # OCCT treats an inverted closed solid as the complement of its material.
    # Such a Boolean can yield a valid but wrong finite mesh; do not rely on
    # mesh closure or signed-volume magnitudes to detect it afterward.
    _require_bounded_step_solids(solids)
    check_cancelled()
    report_progress(f'STEP 재료 영역 합치기: {len(solids)}개 솔리드', force=True)
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(solids[0])
    for solid in solids[1:]:
        tools.Append(solid)
    operation = BRepAlgoAPI_Fuse()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetNonDestructive(True)
    operation.SetRunParallel(False)
    try:
        operation.Build()
        check_cancelled()
        if not operation.IsDone():
            raise GeometryError('STEP material union failed; repair the source CAD bodies.')
        material = operation.Shape()
        if material.IsNull() or not BRepCheck_Analyzer(material).IsValid():
            raise GeometryError('STEP material union produced an invalid BRep; repair the source CAD bodies.')
        result_solids = _step_solids(material)
        count = len(result_solids)
        if not count:
            raise GeometryError('STEP material union produced no closed solid.')
        _require_bounded_step_solids(result_solids)
    except RuntimeError as exc:
        raise GeometryError('STEP material union failed; repair the source CAD bodies.') from exc
    information.update(material_solid_count=count, step_material_union_applied=True,
                       step_material_union_method='OCCT non-destructive material union; default tolerance',
                       step_material_union_fuzzy_value_mm=float(operation.FuzzyValue()))
    return material, information


def _read_step_shape(path: Path):
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise GeometryError("Open CASCADE could not read this STEP file.")
    length, angle, solid_angle = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.FileUnits(length, angle, solid_angle)
    source_units = [length.Value(i).ToCString() for i in range(1, length.Length() + 1)]
    if not source_units:
        raise GeometryError("STEP has no readable length-unit metadata; export with explicit units.")
    # OCCT system length is expressed in millimetres.  Set after ReadFile, before
    # TransferRoots, so STEP metadata is converted once and only once.
    reader.SetSystemLengthUnit(1.0)
    if reader.TransferRoots() <= 0:
        raise GeometryError("The STEP file contains no transferable shape.")
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeometryError("STEP BRep is invalid; repair the original CAD solid.")
    if not TopExp_Explorer(shape, TopAbs_SOLID).More():
        raise GeometryError("STEP contains surfaces but no closed solid.")
    return shape, source_units


def _load_step(path: Path, model_id: str, linear_deflection: float) -> Model:
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.Precision import Precision
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    if (not isinstance(linear_deflection, numbers.Real) or isinstance(linear_deflection, (bool, np.bool_)) or
            not math.isfinite(linear_deflection) or linear_deflection <= 0):
        raise GeometryError("STEP linear deflection must be a positive finite millimetre value.")
    shape, source_units = _read_step_shape(path)
    mesh_shape, material_information = _step_material_shape(shape)
    mesher = BRepMesh_IncrementalMesh(mesh_shape, linear_deflection, False, .2, False)
    if not mesher.IsDone():
        raise GeometryError("Open CASCADE could not tessellate the STEP solid.")
    vertices, faces = [], []
    max_cad_tolerance = max(float(Precision.Confusion_s()),
                            *(float(BRep_Tool.MaxTolerance_s(mesh_shape, kind))
                              for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)))
    explorer = TopExp_Explorer(mesh_shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        max_cad_tolerance = max(max_cad_tolerance, float(BRep_Tool.Tolerance_s(face)))
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None or triangulation.NbTriangles() == 0:
            raise GeometryError("A STEP face has no usable triangulation.")
        offset = len(vertices)
        transform = location.Transformation()
        for i in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(i).Transformed(transform)
            vertices.append((point.X(), point.Y(), point.Z()))
        reversed_face = face.Orientation() == TopAbs_REVERSED
        for i in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(i).Get()
            if reversed_face:
                b, c = c, b
            faces.append((offset + a - 1, offset + b - 1, offset + c - 1))
        explorer.Next()
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64)
    weld_tolerance = max(max_cad_tolerance, _geometry_epsilon(vertices))
    vertices, faces, displacement = _weld_cad_vertices(vertices, faces, weld_tolerance)
    # Periodic CAD surfaces can include a zero-area facet along the identified
    # seam or pole.  It becomes repeated indices when the same CAD point is
    # welded.  Removing only these collapsed CAD facets does not fill holes or
    # change the geometric surface; STL degenerate facets are still rejected.
    collapsed = ((faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) |
                 (faces[:, 2] == faces[:, 0]))
    removed_collapsed = int(collapsed.sum())
    faces = faces[~collapsed]
    faces, retriangulation = _conform_cad_collinear_facets(vertices, faces, max_cad_tolerance)
    allowance = float(linear_deflection + max_cad_tolerance + displacement +
                      retriangulation['cad_retriangulation_surface_change_bound_mm'])
    metadata = {"format": "STEP", "source_length_units": source_units, "working_unit": "mm",
                "occt_system_length_unit_mm": 1., "linear_deflection_mm": float(linear_deflection),
                "angular_deflection_radians": .2, "cad_tolerance_mm": max_cad_tolerance,
                "seam_weld_tolerance_mm": weld_tolerance, "max_seam_vertex_motion_mm": displacement,
                "collapsed_cad_seam_facets_removed": removed_collapsed,
                "tessellation_error_mm": allowance,
                "tessellation_bound_certified": False,
                "tessellation_caveat": "Declared allowance from meshing deflection, CAD tolerance and seam welding; "
                                       "not a certified exact BRep-to-mesh Hausdorff bound."}
    metadata.update(retriangulation)
    metadata.update(material_information)
    metadata['original_brep_modified'] = False
    unit_names = {"millimetre": "mm", "millimeter": "mm", "metre": "m", "meter": "m",
                  "centimetre": "cm", "centimeter": "cm", "inch": "in"}
    source_unit = unit_names.get(source_units[0].lower(), source_units[0]) if len(source_units) == 1 else "mixed STEP units"
    return model_from_mesh(trimesh.Trimesh(vertices=vertices, faces=faces, process=False),
                           model_id=model_id, name=path.name, source_path=str(path.resolve()),
                           source_unit=source_unit, tessellation_error=allowance,
                           original_shape=shape, diagnostics=metadata)


def load_model(path, stl_unit: str = "mm", model_id: str | None = None,
               linear_deflection: float = .02, *, use_cache=True) -> Model:
    check_cancelled()
    path = Path(path)
    if not path.is_file():
        raise GeometryError(f"Model file does not exist: {path}")
    identifier = model_id or path.stem
    is_step = path.suffix.lower() in ('.step', '.stp')
    if not is_step and path.suffix.lower() != ".stl":
        raise GeometryError("Supported model formats are STL and STEP (.stl, .step, .stp).")
    unit = str(stl_unit).lower().strip()
    if not is_step and unit not in _UNIT_SCALE:
        raise GeometryError("STL has no units; select mm, cm, m, in, or um explicitly.")
    if is_step and (not isinstance(linear_deflection, numbers.Real) or isinstance(linear_deflection, (bool, np.bool_))
                    or not math.isfinite(linear_deflection) or linear_deflection <= 0):
        raise GeometryError('STEP linear deflection must be a positive finite millimetre value.')
    from . import cache
    descriptor = None
    if use_cache and cache.enabled():
        descriptor = {'source_sha256': cache.file_digest(path), 'format': 'STEP' if is_step else 'STL',
                      'unit': 'STEP metadata' if is_step else unit,
                      'linear_deflection': float(linear_deflection) if is_step else None,
                      'checker': validation_stamp()}
        restored = cache.load_disk(descriptor)
        if restored is not None:
            record, metadata = restored
            vertices, faces = record.arrays()
            shape = None
            if is_step:
                shape, source_units = _read_step_shape(path)
                if source_units != metadata['diagnostics']['source_length_units']:
                    raise GeometryError('STEP unit metadata changed while loading the cached geometry.')
            if cache.file_digest(path) != descriptor['source_sha256']:
                raise GeometryError('The source file changed while loading it; try importing again.')
            diagnostics = dict(metadata['diagnostics'])
            diagnostics.update(json.loads(record.information))
            diagnostics.update(model_import_cache='disk', geometry_validation_cache='disk')
            model = Model(identifier, path.name, trimesh.Trimesh(vertices=vertices, faces=faces, process=False),
                          source_path=str(path.resolve()), source_unit=metadata['source_unit'],
                          tessellation_error=metadata['tessellation_error'], original_shape=shape,
                          diagnostics=diagnostics)
            cache.put_geometry((descriptor['checker'], cache.mesh_digest(model.mesh)), record)
            cache.attach_receipt(model, descriptor['checker'], record.information)
            return model
    if is_step:
        model = _load_step(path, identifier, linear_deflection)
    else:
        model = _load_stl(path, identifier, unit)
    model.diagnostics['model_import_cache'] = 'miss' if descriptor is not None else 'disabled'
    if descriptor is not None:
        if cache.file_digest(path) != descriptor['source_sha256']:
            raise GeometryError('The source file changed while loading it; try importing again.')
        record = cache.get_geometry(descriptor['checker'], model.mesh)
        if record is not None:
            cache.save_disk(descriptor, model, json.loads(record.information))
    return model


def _load_stl(path, identifier, unit):
    try:
        mesh = trimesh.load_mesh(str(path), file_type="stl", process=False)
    except Exception as exc:
        raise GeometryError(f"Could not read the STL triangles: {exc}") from exc
    if not isinstance(mesh, trimesh.Trimesh):
        raise GeometryError("STL contains no usable triangle mesh.")
    scaled = trimesh.Trimesh(vertices=np.asarray(mesh.vertices) * _UNIT_SCALE[unit],
                             faces=mesh.faces, process=False)
    return model_from_mesh(scaled, model_id=identifier, name=path.name,
                           source_path=str(path.resolve()), source_unit=unit,
                           diagnostics={"format": "STL", "stl_unit_assumption": unit,
                                        "scale_to_mm": _UNIT_SCALE[unit], "working_unit": "mm",
                                        "tessellation_error_mm": 0.,
                                        "geometry_meaning": "STL triangles are the supplied geometry; original CAD accuracy is unknown."})


def placement_bounds(model: Model, placement: Placement) -> np.ndarray:
    bounds = model.bounds_for(placement.rotation_id)
    position = np.asarray(placement.position, dtype=float)
    return np.array([position, position + bounds[1] - bounds[0]])


class CollisionEngine:
    """One immutable FCL BVH per model; only transforms vary between copies."""

    def __init__(self, models: dict[str, Model], settings: Settings):
        self.models = models
        self.settings = settings
        self._bvhs = {}
        self._triangles = {}
        self._representatives = {}
        self._rotated_bounds = {}
        self._objects: OrderedDict[tuple, object] = OrderedDict()
        self._index = None
        self._index_placements: list[Placement] = []
        self._index_identity = None
        self._loop_bases = {}
        self.last_interlocking = None
        self.statistics = {"bvh_models": 0, "aabb_rejections": 0, "fcl_queries": 0,
                           "containment_queries": 0, "spatial_candidates": 0}

    def _prepare(self, model_id: str):
        check_cancelled()
        if model_id in self._bvhs:
            return
        model = self.models[model_id]
        vertices, faces = np.asarray(model.mesh.vertices), np.asarray(model.mesh.faces)
        bvh = fcl.BVHModel()
        bvh.beginModel(num_tris_=len(faces), num_vertices_=len(vertices))
        bvh.addSubModel(vertices, faces)
        bvh.endModel()
        components = trimesh.graph.connected_components(
            model.mesh.face_adjacency, nodes=np.arange(len(faces)), min_len=1)
        self._bvhs[model_id] = bvh
        self._triangles[model_id] = np.asarray(model.mesh.triangles)
        self._representatives[model_id] = np.asarray([vertices[faces[group[0], 0]] for group in components])
        self.statistics["bvh_models"] += 1

    def bounds(self, placement: Placement) -> np.ndarray:
        model = self.models[placement.model_id]
        key = (placement.model_id, placement.rotation_id)
        if key not in self._rotated_bounds:
            rotated = np.asarray(model.mesh.vertices) @ ROTATIONS[placement.rotation_id].T
            self._rotated_bounds[key] = np.array([rotated.min(axis=0), rotated.max(axis=0)])
        rotated_bounds = self._rotated_bounds[key]
        position = np.asarray(placement.position, dtype=float)
        return np.array([position, position + rotated_bounds[1] - rotated_bounds[0]])

    def _transform(self, placement: Placement) -> tuple[np.ndarray, np.ndarray]:
        self.bounds(placement)
        rotation = ROTATIONS[placement.rotation_id].astype(float)
        translation = np.asarray(placement.position, dtype=float) - self._rotated_bounds[
            (placement.model_id, placement.rotation_id)][0]
        return rotation, translation

    def _object(self, placement: Placement):
        self._prepare(placement.model_id)
        key = (placement.model_id, int(placement.rotation_id), tuple(map(float, placement.position)))
        try:
            result = self._objects.pop(key)
        except KeyError:
            rotation, translation = self._transform(placement)
            result = fcl.CollisionObject(self._bvhs[placement.model_id], fcl.Transform(rotation, translation))
        self._objects[key] = result
        if len(self._objects) > 512:
            self._objects.popitem(last=False)
        return result

    def _well_formed(self, p: Placement) -> bool:
        try:
            _position_array(p.position)
            return (p.model_id in self.models and
                    isinstance(p.rotation_id, numbers.Integral) and not isinstance(p.rotation_id, bool) and
                    0 <= p.rotation_id < 24 and p.rotation_id in self.models[p.model_id].allowed_rotations)
        except (AttributeError, ValueError, TypeError, IndexError):
            return False

    def in_bounds(self, placement: Placement) -> bool:
        if not self._well_formed(placement):
            return False
        bounds = self.bounds(placement)
        margin = self.settings.boundary_margins(self.models[placement.model_id].tessellation_error)
        tolerance = self.settings.tolerance
        return bool(np.all(bounds[0] >= margin - tolerance) and
                    np.all(bounds[1] <= np.asarray(self.settings.size) - margin + tolerance))

    def _contains_shell_vertex(self, a: Placement, b: Placement) -> bool:
        """With disjoint surfaces, every shell lies entirely in one material region."""
        ra, ta = self._transform(a)
        rb, tb = self._transform(b)
        points = (self._representatives[a.model_id] @ ra.T + ta - tb) @ rb
        triangles = self._triangles[b.model_id]
        # Cheap bounds elimination before O(facets) exact solid-angle sums.
        model_bounds = np.asarray(self.models[b.model_id].mesh.bounds)
        points = points[np.all(points > model_bounds[0], axis=1) &
                        np.all(points < model_bounds[1], axis=1)]
        if not len(points):
            return False
        self.statistics["containment_queries"] += 1
        winding = np.abs(_winding_numbers(triangles, points))
        return bool(np.any(winding > .5))

    def _interlocking_pair(self, a, b):
        from .interlocking import loop_basis, detect_link
        for key in (a.model_id, b.model_id):
            if key not in self._loop_bases:
                self._loop_bases[key] = loop_basis(self.models[key].mesh)
        first, second = self._loop_bases[a.model_id], self._loop_bases[b.model_id]
        if not first['loops'] or not second['loops']: return None
        report_progress('고리 얽힘 검사: ' + a.model_id + ' / ' + b.model_id)
        try:
            result = detect_link(first, second, seconds=2.,
                                 transform_first=self._transform(a), transform_second=self._transform(b))
        except (ValueError, ArithmeticError) as exc:
            return {'status': 'unknown_error', 'reason': str(exc)}
        # Curves and exact crossings are reproducible from the immutable mesh
        # and recorded cycle IDs; do not retain large per-candidate point sets.
        summary = {key: result[key] for key in ('status', 'loop_a', 'loop_b', 'tested_loop_pairs') if key in result}
        if result.get('certificate'):
            summary['linking_number'] = result['certificate']['linking_number']
            summary['projection'] = result['certificate']['projection']
            summary['proof'] = 'exact Fraction signed crossing sum of closed mesh-edge cycles'
        return summary

    def check_pair(self, a: Placement, b: Placement, *, exact_distance: bool = False) -> tuple[bool, float, str]:
        check_cancelled()
        self.last_interlocking = None
        if not self._well_formed(a) or not self._well_formed(b):
            return False, math.nan, "Invalid model, rotation, or nonfinite placement."
        ab, bb = self.bounds(a), self.bounds(b)
        separation = np.maximum(0., np.maximum(ab[0] - bb[1], bb[0] - ab[1]))
        lower_distance = float(np.linalg.norm(separation))
        required = (self.settings.gap + self.models[a.model_id].tessellation_error +
                    self.models[b.model_id].tessellation_error)
        # Overlapping/touching AABBs must always reach surface+containment tests,
        # even when the requested clearance is zero.
        if not exact_distance and lower_distance > 0 and lower_distance >= required - self.settings.tolerance:
            self.statistics["aabb_rejections"] += 1
            return True, lower_distance, "AABB distance lower bound meets the required clearance."
        oa, ob = self._object(a), self._object(b)
        self.statistics["fcl_queries"] += 1
        collision = fcl.CollisionResult()
        fcl.collide(oa, ob, fcl.CollisionRequest(enable_contact=False), collision)
        if collision.is_collision:
            return False, 0., "Triangle surfaces intersect or touch (contact is conservatively rejected)."
        distance = float(fcl.distance(oa, ob, fcl.DistanceRequest(), fcl.DistanceResult()))
        if not math.isfinite(distance) or distance < 0:
            return False, distance, "FCL could not establish a reliable nonnegative distance."
        # FCL triangle distance alone cannot detect a wholly enclosed solid.
        if self._contains_shell_vertex(a, b) or self._contains_shell_vertex(b, a):
            return False, 0., "One model occupies the material interior of the other (solid containment)."
        if distance < required - self.settings.tolerance:
            return False, distance, f"Surface clearance {distance:.9g} mm is less than required {required:.9g} mm."
        if (self.settings.check_interlocking and
                np.all(np.minimum(ab[1], bb[1]) > np.maximum(ab[0], bb[0]))):
            self.last_interlocking = self._interlocking_pair(a, b)
            if self.last_interlocking:
                status = self.last_interlocking['status']
                if status == 'linked':
                    number = self.last_interlocking['linking_number']
                    return False, distance, f'Topological interlocking (고리 얽힘): Lk={number}. 충돌하지 않아도 부품을 부수지 않고 분리할 수 없습니다.'
                if status.startswith('unknown'):
                    return False, distance, '고리 얽힘 검사를 완료하지 못했습니다. 분리 불가능이 증명된 것은 아닙니다.'
        return True, distance, "Mesh surfaces and solid containment satisfy the required clearance."

    def _sync_index(self, existing):
        # The optimizer normally appends to one list.  Rebuild for a replaced
        # list or nonappend edits; equal immutable prefixes can reuse the tree.
        if (self._index is None or self._index_identity is not existing or
                len(existing) < len(self._index_placements) or
                any(a != b for a, b in zip(existing, self._index_placements))):
            if self._index is not None:
                self._index.close()
            self._index = rtree_index.Index(properties=rtree_index.Property(dimension=3))
            self._index_placements = []
            self._index_identity = existing
        for i in range(len(self._index_placements), len(existing)):
            p = existing[i]
            self._index.insert(i, tuple(self.bounds(p).ravel()))
            self._index_placements.append(p)

    def can_place(self, placement: Placement, existing: Iterable[Placement]) -> bool:
        if not self.in_bounds(placement):
            return False
        if not isinstance(existing, (list, tuple)):
            existing = list(existing)
        try:
            self._sync_index(existing)
            maximum_error = max((m.tessellation_error for m in self.models.values()), default=0.)
            margin = self.settings.gap + self.models[placement.model_id].tessellation_error + maximum_error
            bounds = self.bounds(placement)
            query = tuple(np.r_[bounds[0] - margin, bounds[1] + margin])
            candidates = list(self._index.intersection(query))
            self.statistics["spatial_candidates"] += len(candidates)
            for i in candidates:
                check_cancelled()
                if not self.check_pair(placement, existing[i])[0]:
                    return False
        except (KeyError, ValueError, TypeError, AttributeError):
            return False
        return True

    def close(self):
        if self._index is not None:
            self._index.close()
            self._index = None

