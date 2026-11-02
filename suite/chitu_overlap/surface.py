"""Original triangle-surface clearance queries, without solid validation.

Distances are unsigned distances between triangle sets. They neither measure
intersection volume nor exclude complete containment. "Exact" means a native
triangle query rather than an AABB bound, not exact real-number arithmetic.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
import math

import numpy as np
import trimesh

from . import geometry
from .geometry import MeshObject

FACE_CHUNK = 65_536
VERTEX_CHUNK = 262_144
MAX_COVERED_DEGENERATES = 64
MAX_PROOF_SCAN_FACES = 1_000_000
MAX_COVER_CANDIDATES = 4096


def _cancel(event):
    if event is not None and event.is_set():
        raise InterruptedError("Surface inspection cancelled.")


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b, c):
    u, v = _subtract(b, a), _subtract(c, a)
    return (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])


def prepare_surface_query(mesh, *, cancel_event=None):
    """Return a read-only triangle query and a surface-preservation proof.

    Finite/index checks are mathematical input requirements, not manufacturing
    quality checks. A numerically zero triangle is omitted only after Fraction
    arithmetic proves its entire segment belongs to a retained triangle edge.
    Coordinate equality is exact, so unshared STL vertex indices also work.
    Uncovered or unsupported point/segment primitives remain unqueryable.
    Ordinary meshes are borrowed without a scene-wide copy. Covered-zero faces
    use a private face array/wrapper over borrowed coordinates. These queries
    must not be mutated or used concurrently with source mutations. Source
    arrays, metadata, flags and derived caches are never changed.
    """
    _cancel(cancel_event)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("A triangle mesh is required for a surface query.")
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3
            or not len(vertices) or not len(faces)):
        raise ValueError("Nonempty triangle coordinates and indices are required.")
    if not np.issubdtype(faces.dtype, np.integer):
        raise ValueError("Triangle indices must be integers.")
    for start in range(0, len(vertices), VERTEX_CHUNK):
        _cancel(cancel_event)
        if not np.isfinite(vertices[start:start+VERTEX_CHUNK]).all():
            raise ValueError("Triangle coordinates must be finite.")
    lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
    zero = []
    for start in range(0, len(faces), FACE_CHUNK):
        _cancel(cancel_event)
        indices = faces[start:start+FACE_CHUNK]
        if np.any(indices < 0) or np.any(indices >= len(vertices)):
            raise ValueError("Triangle indices are outside the coordinate array.")
        triangles = np.asarray(vertices[indices], dtype=np.float64)
        lower = np.minimum(lower, triangles.min(axis=(0, 1)))
        upper = np.maximum(upper, triangles.max(axis=(0, 1)))
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
            squared = np.einsum("ij,ij->i", normal, normal)
        if not np.isfinite(squared).all():
            raise ValueError("Triangle arithmetic is not finite at the input scale.")
        zero.extend((np.flatnonzero(squared == 0) + start).tolist())
        if len(zero) > MAX_COVERED_DEGENERATES:
            raise ValueError("Too many degenerate primitives for bounded exact coverage proof.")
    _cancel(cancel_event)
    if zero and len(faces) > MAX_PROOF_SCAN_FACES:
        raise ValueError("Degenerate coverage proof exceeds the bounded face-scan budget.")
    zero_set = set(zero)
    rational = {}

    def point(index):
        _cancel(cancel_event)
        index = int(index)
        if index not in rational:
            rational[index] = tuple(Fraction(float(value)) for value in vertices[index])
        return rational[index]

    proofs = []
    examined = 0
    for number in zero:
        _cancel(cancel_event)
        ids = list(map(int, faces[number]))
        if any(_cross(*(point(index) for index in ids))):
            raise ValueError("Numerically degenerate triangle is not exactly collinear.")
        endpoints = max(combinations(ids, 2), key=lambda pair: _dot(_subtract(point(pair[0]), point(pair[1])),
                                                                  _subtract(point(pair[0]), point(pair[1]))))
        a, b = sorted(endpoints)
        edge = _subtract(point(b), point(a))
        length2 = _dot(edge, edge)
        if length2 == 0:
            raise ValueError("An unsupported point primitive cannot be silently removed.")
        parameters = [_dot(_subtract(point(index), point(a)), edge) / length2 for index in ids]
        if any(parameter < 0 or parameter > 1 for parameter in parameters):
            raise ValueError("Degenerate triangle does not lie in its exact segment hull.")
        cover = None
        left, right = np.asarray(vertices[a]), np.asarray(vertices[b])
        for start in range(0, len(faces), FACE_CHUNK):
            _cancel(cancel_event)
            indices = faces[start:start+FACE_CHUNK]
            triangles = np.asarray(vertices[indices])
            matches = np.any(np.all(triangles == left, axis=2), axis=1) & np.any(np.all(triangles == right, axis=2), axis=1)
            for local in np.flatnonzero(matches):
                _cancel(cancel_event)
                candidate = start + int(local)
                if candidate in zero_set:
                    continue
                examined += 1
                if examined > MAX_COVER_CANDIDATES:
                    raise ValueError("Exact coverage candidate budget exceeded.")
                if any(_cross(*(point(index) for index in faces[candidate]))):
                    cover = candidate
                    break
            if cover is not None:
                break
        if cover is None:
            raise ValueError("Degenerate segment is not covered by a retained triangle edge.")
        proofs.append({"removed_face": int(number), "segment_vertex_indices": [a, b],
                       "segment_coordinates": vertices[[a, b]].tolist(),
                       "exact_vertex_parameters": [str(value) for value in parameters],
                       "retained_cover_face": int(cover),
                       "retained_cover_vertex_indices": faces[cover].tolist(),
                       "exact_segment_subset_of_retained_edge": True})
    _cancel(cancel_event)
    if zero:
        keep = np.ones(len(faces), dtype=bool)
        keep[zero] = False
        copied_faces = np.ascontiguousarray(faces[keep], dtype=np.int64)
        if not len(copied_faces):
            raise ValueError("No supported surface triangles remain.")
        borrowed_vertices = vertices.view()
        borrowed_vertices.flags.writeable = False
        # No source metadata/provenance or caches are copied to this wrapper.
        query = trimesh.Trimesh(vertices=borrowed_vertices, faces=copied_faces, process=False)
    else:
        query = mesh
    _cancel(cancel_event)
    return query, {"method": "exact-covered-degenerate-query-copy" if zero else "original-triangle-borrow",
                   "raw_triangle_count": len(faces), "query_triangle_count": len(query.faces),
                   "removed_covered_degenerate_face_count": len(zero), "removed_count": len(zero),
                   "coverage_proofs": proofs, "coordinates_unchanged": True,
                   "query_storage": "private-faces-borrowed-coordinates" if zero else "borrowed-original-triangles",
                   "exact_surface_union_preserved": True, "bounds": [lower.tolist(), upper.tolist()]}


def _relation(a, b):
    return "disjoint" if np.any((a[1] < b[0]) | (b[1] < a[0])) else "overlapping"


def _checked_distance(distance, nearest, lower, relation, a, b):
    distance = float(distance)
    if not math.isfinite(distance) or distance < 0:
        raise ValueError("Surface query returned a nonfinite or negative distance.")
    if lower is not None and distance < lower:
        raise ValueError("Surface query distance contradicts the original AABB lower bound.")
    if distance == 0 and relation == "disjoint":
        raise ValueError("Zero surface distance contradicts disjoint original AABBs.")
    if nearest is None:
        if distance > 0:
            raise ValueError("Positive surface distance has no finite nearest-point witness.")
        return distance, None
    points = np.asarray(nearest, dtype=np.float64)
    if points.shape != (2, 3) or not np.isfinite(points).all():
        raise ValueError("Surface query nearest points are not finite 3D points.")
    with np.errstate(over="ignore", invalid="ignore"):
        witness = math.hypot(*(points[1]-points[0]))
    if not math.isfinite(witness) or (distance > 0 and witness == 0):
        raise ValueError("Surface query nearest-point distance is invalid.")
    scale = max(float(np.max(np.abs(points))), float(np.max(np.abs(a))), float(np.max(np.abs(b))), distance, witness)
    tolerance = math.ulp(scale) * 8
    if (np.any(points[0] < a[0]-tolerance) or np.any(points[0] > a[1]+tolerance)
            or np.any(points[1] < b[0]-tolerance) or np.any(points[1] > b[1]+tolerance)):
        raise ValueError("Surface query nearest points lie outside the original AABBs.")
    if not math.isclose(distance, witness, rel_tol=1e-8, abs_tol=tolerance):
        raise ValueError("Surface query distance and nearest-point norm disagree.")
    return distance, points.tolist()


def check_surface_clearances(objects, *, min_gap_mm=0.0, distance_tolerance_mm=1e-6,
                             cancel_event=None, progress=None):
    """Actually query every pair; completion refers only to surface conditions."""
    parameters = {"min_gap_mm": float(min_gap_mm), "distance_tolerance_mm": float(distance_tolerance_mm)}
    for name, value in parameters.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    objects = list(objects)
    records, queries = [], []
    interrupted = False
    for index, obj in enumerate(objects):
        record = {"index": index, "name": obj.name, "query_available": False, "query_error": None,
                  "bounds": None, "raw_triangle_count": len(obj.mesh.faces) if isinstance(obj.mesh, trimesh.Trimesh) else None,
                  "query_triangle_count": None, "preparation_proof": None}
        query = None
        try:
            if interrupted:
                raise InterruptedError("Surface inspection cancelled.")
            _cancel(cancel_event)
            if progress:
                progress(5 + 25 * index / max(1, len(objects)), f"오브젝트 {index+1}/{len(objects)} 표면 조회 준비 중입니다.")
            query, proof = prepare_surface_query(obj.mesh, cancel_event=cancel_event)
            record.update(query_available=True, bounds=proof["bounds"], query_triangle_count=proof["query_triangle_count"],
                          preparation_proof=proof)
        except InterruptedError as exc:
            interrupted = True
            record["query_error"] = str(exc)
        except Exception as exc:
            record["query_error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
        queries.append(query)
    pairs = []
    total = len(objects)*(len(objects)-1)//2
    large = sum(row.get("query_triangle_count") or 0 for row in records) > geometry.LARGE_SCENE_FACE_THRESHOLD
    cache = geometry._SmallCollisionCache()
    best, best_pair = math.inf, None
    for done, (i, j) in enumerate(combinations(range(len(objects)), 2)):
        if interrupted or (cancel_event is not None and cancel_event.is_set()):
            break
        if progress:
            progress(30 + 70 * done / max(1, total), f"표면 거리 {done}/{total}쌍 측정 중입니다.")
        if cancel_event is not None and cancel_event.is_set():
            interrupted = True
            break
        pair = {"index_a": i, "index_b": j, "object_a": objects[i].name, "object_b": objects[j].name,
                "status": "inconclusive", "distance_mm": None, "distance_is_exact": False,
                "nearest_points_mm": None, "distance_lower_bound_mm": None,
                "aabb_relation": "unknown", "containment_evaluated": False, "method": None, "error": None}
        pairs.append(pair)
        if queries[i] is None or queries[j] is None:
            pair["error"] = "One or both original triangle surfaces could not be prepared for a distance query."
            continue
        a, b = np.asarray(records[i]["bounds"]), np.asarray(records[j]["bounds"])
        pair["aabb_relation"] = _relation(a, b)
        lower = geometry._placement_bbox_lower_bound(a, b)
        pair["distance_lower_bound_mm"] = lower
        try:
            if large or max(len(queries[i].faces), len(queries[j].faces)) > geometry.FCL_BLOCK_FACES:
                distance, nearest = geometry._bounded_surface_distance(queries[i], queries[j], a, b, cancel_event)
                method = "bounded-fcl-triangle-surface-query"
            else:
                distance, nearest = geometry._surface_distance(cache.get(i, queries[i]), cache.get(j, queries[j]))
                method = "fcl-triangle-surface-query"
            _cancel(cancel_event)
            distance, nearest = _checked_distance(distance, nearest, lower, pair["aabb_relation"], a, b)
            status = ("contact_or_intersection" if distance <= parameters["distance_tolerance_mm"] else
                      "too_close" if distance + parameters["distance_tolerance_mm"] < parameters["min_gap_mm"] else
                      "surface_gap_satisfied")
            pair.update(status=status, distance_mm=distance, distance_is_exact=True, nearest_points_mm=nearest, method=method)
            if distance < best:
                best, best_pair = distance, [i, j]
        except InterruptedError as exc:
            pair["error"] = str(exc)
            interrupted = True
            break
        except Exception as exc:
            pair["error"] = f"Surface query failed: {type(exc).__name__}: {exc}"
    cancelled = interrupted or (cancel_event is not None and cancel_event.is_set())
    all_queried = (bool(objects) and not cancelled and len(pairs) == total
                   and all(row["query_available"] for row in records)
                   and all(pair["distance_is_exact"] for pair in pairs))
    violations = [pair for pair in pairs if pair["status"] in {"contact_or_intersection", "too_close"}]
    complete = all_queried and all(pair["status"] != "inconclusive" for pair in pairs)
    return {"inspection_scope": "surface-clearance", "status": "violation" if violations else "surface_gap_satisfied" if complete else "inconclusive",
            "complete": complete, "geometry_validation_performed": False, "volume_overlap_evaluated": False,
            "solid_overlap_evaluated": False, "containment_evaluated": False,
            "object_count": len(objects), "total_pair_count": total, "objects": records, "pairs": pairs,
            "violations": violations, "violation_found": bool(violations), "cancelled": cancelled,
            "processing_completed": bool(objects) and not cancelled and len(pairs) == total,
            "pair_distances_complete": all_queried, "minimum_distance_mm": best if math.isfinite(best) else None,
            "minimum_distance_pair": best_pair, "closest_distance_is_global": math.isfinite(best) and all_queried,
            "closest_pair_scope": "all_objects" if all_queried else "measured_pairs", **parameters}
