"""Bounded-memory solid overlap, contact and placed-mesh spacing checks.

AABB bounds certify distant pairs in large scenes. They are never presented as
measured distances: every possible failure and the closest valid pair receive
exact triangle-surface queries.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from itertools import combinations
import math
from pathlib import Path
import tempfile
from typing import Iterable

import fcl
import manifold3d
import numpy as np
import trimesh

LARGE_SCENE_FACE_THRESHOLD = 1_000_000
VALIDATION_FACE_CHUNK = 65_536
FCL_BLOCK_FACES = 100_000
_FCL_CACHE_BYTES = 256 * 1024 * 1024
_MANIFOLD_CACHE_BYTES = 64 * 1024 * 1024
TOPOLOGY_SORT_BYTES = 64 * 1024 * 1024
TOPOLOGY_CHECK_PAIRS = 65_536


@dataclass(frozen=True)
class MeshObject:
    name: str
    mesh: trimesh.Trimesh


def _stopped(event):
    return event is not None and event.is_set()


def _geometry_key(mesh):
    # Filename/digest metadata alone cannot authorize validation reuse.
    try:
        from .chitubox import native_geometry_key
        return native_geometry_key(mesh)
    except ImportError:
        return None


def _strict_topology(mesh, cancel_event=None, *, diagnostics=None):
    """Keep strict edge certification and collect complete bounded diagnostics.

    Sorted indexed-edge runs must each have exactly two opposite directions.
    Run counts are reduced in fixed chunks, including runs crossing chunks;
    neither a four-incidence edge nor cancellation can become a closed solid.
    """
    vertex_count = len(mesh.vertices)
    face_count = len(mesh.faces)
    bits = max(1, (vertex_count - 1).bit_length())
    if bits > 31:
        raise ValueError("Mesh has too many indexed vertices for strict topology certification")
    edge_count = face_count * 3
    directory = None
    keys = view = target = left = right = None
    try:
        if edge_count * 8 > TOPOLOGY_SORT_BYTES:
            directory = tempfile.TemporaryDirectory(prefix="BJT_topology_")
            keys = np.memmap(Path(directory.name) / "edges.bin", mode="w+",
                             dtype=np.uint64, shape=(edge_count,))
        else:
            keys = np.empty(edge_count, dtype=np.uint64)
        for start in range(0, face_count, VALIDATION_FACE_CHUNK):
            if _stopped(cancel_event):
                raise InterruptedError("Inspection cancelled during topology validation.")
            faces = np.asarray(mesh.faces[start:start + VALIDATION_FACE_CHUNK])
            target = keys[start * 3:(start + len(faces)) * 3].reshape((-1, 3))
            for column, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
                left, right = faces[:, a], faces[:, b]
                low = np.minimum(left, right).astype(np.uint64)
                high = np.maximum(left, right).astype(np.uint64)
                target[:, column] = (((low << np.uint64(bits)) | high) << np.uint64(1)) | (left < right)
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled before topology sorting.")
        keys.sort(kind="quicksort")
        boundary = nonmanifold = inconsistent = unique = 0
        pending_base = None
        pending_count = pending_xor = 0
        for start in range(0, edge_count, max(2, 2 * TOPOLOGY_CHECK_PAIRS)):
            if _stopped(cancel_event):
                raise InterruptedError("Inspection cancelled during topology validation.")
            view = keys[start:start + max(2, 2 * TOPOLOGY_CHECK_PAIRS)]
            bases = view >> np.uint64(1)
            starts = np.r_[0, np.flatnonzero(bases[1:] != bases[:-1]) + 1]
            counts = np.diff(np.r_[starts, len(view)])
            xors = np.bitwise_xor.reduceat(view, starts)
            if pending_base is not None:
                if int(bases[0]) == pending_base:
                    counts[0] += pending_count
                    xors[0] ^= np.uint64(pending_xor)
                else:
                    boundary += pending_count == 1
                    nonmanifold += pending_count > 2
                    inconsistent += pending_count == 2 and pending_xor != 1
                    unique += 1
            finished = counts[:-1]
            boundary += int(np.count_nonzero(finished == 1))
            nonmanifold += int(np.count_nonzero(finished > 2))
            inconsistent += int(np.count_nonzero((finished == 2) & (xors[:-1] != 1)))
            unique += len(finished)
            pending_base = int(bases[-1])
            pending_count, pending_xor = int(counts[-1]), int(xors[-1])
        if pending_base is not None:
            boundary += pending_count == 1
            nonmanifold += pending_count > 2
            inconsistent += pending_count == 2 and pending_xor != 1
            unique += 1
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled during topology validation.")
        if diagnostics is not None:
            diagnostics.update(boundary_edge_count=int(boundary), nonmanifold_edge_count=int(nonmanifold),
                               inconsistent_winding_edge_count=int(inconsistent), unique_edge_count=int(unique),
                               edge_counts_complete=True)
        watertight = boundary == 0 and nonmanifold == 0
        return watertight, inconsistent == 0 if watertight else None
    finally:
        view = target = left = right = None
        if isinstance(keys, np.memmap):
            keys.flush()
            keys._mmap.close()
        keys = None
        if directory is not None:
            directory.cleanup()


def _validate_object(index, obj, topology_cache=None, cancel_event=None):
    record = {"index": index, "name": obj.name, "valid": False, "errors": [],
              "bounds": None, "volume": None, "original_triangle_bounds": None,
              "source_triangles_finite_indexed": False,
              "topology_diagnostics": {"degenerate_face_count": None, "invalid_area_face_count": None,
                                       "face_counts_complete": False, "edge_counts_complete": False,
                                       "boundary_edge_count": None, "nonmanifold_edge_count": None}}
    errors = record["errors"]
    diagnostics = record["topology_diagnostics"]
    mesh = obj.mesh
    if not isinstance(mesh, trimesh.Trimesh):
        errors.append("Object does not contain a triangle mesh.")
        return record
    vertices, faces = mesh.vertices, mesh.faces
    if not len(vertices) or not len(faces):
        errors.append("Mesh is empty.")
        return record
    try:
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3
                or not np.issubdtype(faces.dtype, np.integer)):
            errors.append("Mesh contains invalid face indices.")
            return record
        for start in range(0, len(vertices), VALIDATION_FACE_CHUNK * 4):
            if _stopped(cancel_event):
                raise InterruptedError("Inspection cancelled during mesh validation.")
            if not np.isfinite(vertices[start:start + VALIDATION_FACE_CHUNK * 4]).all():
                errors.append("Mesh contains non-finite coordinates.")
                return record
        first = np.asarray(faces[:1])
        if np.any(first < 0) or np.any(first >= len(vertices)):
            errors.append("Mesh contains invalid face indices.")
            return record
        origin = np.asarray(vertices[int(first[0, 0])], dtype=float)
        lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
        volumes = []
        degenerate = invalid_area = 0
        for start in range(0, len(faces), VALIDATION_FACE_CHUNK):
            if _stopped(cancel_event):
                raise InterruptedError("Inspection cancelled during mesh validation.")
            indices = np.asarray(faces[start:start + VALIDATION_FACE_CHUNK])
            if np.any(indices < 0) or np.any(indices >= len(vertices)):
                errors.append("Mesh contains invalid face indices.")
                return record
            triangle = np.asarray(vertices[indices], dtype=np.float64)
            lower = np.minimum(lower, triangle.min(axis=(0, 1)))
            upper = np.maximum(upper, triangle.max(axis=(0, 1)))
            with np.errstate(over="ignore", invalid="ignore", under="ignore"):
                cross = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
                squared = np.einsum("ij,ij->i", cross, cross)
                degenerate += int(np.count_nonzero(np.isfinite(squared) & (squared <= 0)))
                invalid_area += int(np.count_nonzero(~np.isfinite(squared)))
                centered = triangle - origin
                tetra = np.einsum("ij,ij->i", centered[:, 0], np.cross(centered[:, 1], centered[:, 2]))
                volumes.append(float(np.sum(tetra, dtype=np.float64) / 6.0))
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled during mesh validation.")
        # This certificate concerns original finite indexed triangles only. It
        # is not solid validation, and is never based on native auto-repair.
        record["original_triangle_bounds"] = [lower.tolist(), upper.tolist()]
        record["source_triangles_finite_indexed"] = True
        diagnostics.update(degenerate_face_count=degenerate, invalid_area_face_count=invalid_area,
                           face_counts_complete=True)
        if degenerate or invalid_area:
            errors.append("Mesh contains degenerate or invalid triangles.")
            _strict_topology(mesh, cancel_event, diagnostics=diagnostics)
            return record
        volume = math.fsum(volumes)
        record["bounds"] = [lower.tolist(), upper.tolist()]
        if math.isfinite(volume):
            record["volume"] = volume
        if not math.isfinite(volume) or volume <= 0:
            errors.append("Mesh must have finite positive volume and outward-facing faces.")
            _strict_topology(mesh, cancel_event, diagnostics=diagnostics)
            return record
        key = _geometry_key(mesh)
        cached = topology_cache.get(key) if topology_cache is not None and key is not None else None
        if cached is None:
            topology = {}
            try:
                watertight, winding = _strict_topology(mesh, cancel_event, diagnostics=topology)
                topology_errors = []
                if not watertight:
                    topology_errors.append("Mesh is not watertight; a solid volume is required.")
                if winding is False:
                    topology_errors.append("Mesh face winding is inconsistent.")
                if not topology_errors:
                    solid = _manifold(mesh)
                    del solid
                cached = (not topology_errors, tuple(topology_errors), topology)
            except InterruptedError:
                raise
            except Exception as exc:
                cached = (False, (f"Geometry backend rejected mesh: {type(exc).__name__}: {exc}",), topology)
            if topology_cache is not None and key is not None:
                topology_cache[key] = cached
        diagnostics.update(cached[2])
        if not cached[0]:
            errors.extend(cached[1])
    except Exception as exc:
        errors.append(f"Mesh validation failed: {type(exc).__name__}: {exc}")
    finally:
        mesh._cache.clear()
    record["valid"] = not errors
    return record


def _manifold(mesh):
    # Native-loader arrays are read-only, but nanobind requires writable input.
    solid = manifold3d.Manifold(manifold3d.Mesh64(
        vert_properties=np.array(mesh.vertices, dtype=np.float64, order="C", copy=True),
        tri_verts=np.array(mesh.faces, dtype=np.uint64, order="C", copy=True)))
    if solid.status() != manifold3d.Error.NoError:
        raise ValueError(f"Manifold rejected mesh (not watertight or invalid topology): {solid.status()}")
    return solid


def _collision_object(mesh):
    model = fcl.BVHModel()
    model.beginModel(len(mesh.faces), len(mesh.vertices))
    model.addSubModel(np.array(mesh.vertices, dtype=np.float64, order="C", copy=True),
                      np.array(mesh.faces, dtype=np.int32, order="C", copy=True))
    model.endModel()
    return fcl.CollisionObject(model)


def _surface_distance(a, b):
    collision = fcl.CollisionResult()
    if fcl.collide(a, b, fcl.CollisionRequest(), collision) > 0:
        return 0.0, None
    result = fcl.DistanceResult()
    distance = float(fcl.distance(a, b, fcl.DistanceRequest(enable_nearest_points=True), result))
    if not math.isfinite(distance) or distance < 0:
        raise ValueError(f"Surface-distance query returned invalid value: {distance}")
    points = np.asarray(result.nearest_points, dtype=float)
    nearest = points.tolist() if points.shape == (2, 3) and np.isfinite(points).all() else None
    return distance, nearest


def _bbox_distance(a, b):
    delta = np.maximum(np.maximum(a[0] - b[1], b[0] - a[1]), 0.0)
    return float(np.linalg.norm(delta))


def _placement_bbox_lower_bound(a, b):
    """Conservative finite AABB bound for original referenced triangles only."""
    components = []
    for axis in range(3):
        distance = max(float(a[0][axis]) - float(b[1][axis]),
                       float(b[0][axis]) - float(a[1][axis]), 0.0)
        if not math.isfinite(distance):
            return None
        # Round each subtraction and the final hypot toward zero. This new
        # certificate never rounds its bound upward; legacy queries are intact.
        components.append(math.nextafter(distance, 0.0) if distance > 0 else 0.0)
    distance = math.hypot(*components)
    if not math.isfinite(distance):
        return None
    return math.nextafter(distance, 0.0) if distance > 0 else 0.0


def _surface_upper_bound(a, b):
    # Only vertices of referenced triangles are guaranteed surface points.
    points = []
    for mesh in (a, b):
        indices = np.linspace(0, len(mesh.faces) - 1, min(128, len(mesh.faces)), dtype=np.int64)
        points.append(np.asarray(mesh.vertices[np.asarray(mesh.faces[indices]).reshape(-1)], dtype=float))
    delta = points[0][:, None, :] - points[1][None, :, :]
    squared = np.einsum("ijk,ijk->ij", delta, delta)
    i, j = np.unravel_index(np.argmin(squared), squared.shape)
    return math.sqrt(float(squared[i, j])), [points[0][i].tolist(), points[1][j].tolist()]


def _near_triangle_indices(mesh, opposite_bounds, upper_bound, margin, cancel_event):
    selected = []
    for start in range(0, len(mesh.faces), VALIDATION_FACE_CHUNK):
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled during surface-distance query.")
        triangle = np.asarray(mesh.vertices[np.asarray(mesh.faces[start:start + VALIDATION_FACE_CHUNK])], dtype=float)
        lo, hi = triangle.min(axis=1), triangle.max(axis=1)
        delta = np.maximum(np.maximum(opposite_bounds[0] - hi, lo - opposite_bounds[1]), 0.0)
        keep = np.einsum("ij,ij->i", delta, delta) <= (upper_bound + margin) ** 2
        if keep.any():
            selected.append(np.flatnonzero(keep).astype(np.int64) + start)
    if not selected:
        raise ValueError("No triangles satisfy the proved distance upper bound")
    return np.concatenate(selected)


def _blocks(mesh, face_indices, cancel_event):
    blocks = []
    for start in range(0, len(face_indices), FCL_BLOCK_FACES):
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled while partitioning triangles.")
        indices = face_indices[start:start + FCL_BLOCK_FACES]
        triangle = np.asarray(mesh.vertices[np.asarray(mesh.faces[indices])], dtype=float)
        blocks.append((indices, np.array([triangle.min(axis=(0, 1)), triangle.max(axis=(0, 1))])))
    return blocks


def _block_collision(mesh, face_indices):
    indices = np.asarray(mesh.faces[face_indices])
    used, inverse = np.unique(indices.reshape(-1), return_inverse=True)
    patch = trimesh.Trimesh(vertices=np.asarray(mesh.vertices[used]),
                            faces=inverse.reshape((-1, 3)), process=False)
    return _collision_object(patch)


def _bounded_surface_distance(a, b, bounds_a, bounds_b, cancel_event):
    upper, nearest = _surface_upper_bound(a, b)
    scale = max(1.0, float(np.max(np.abs(np.stack((bounds_a, bounds_b))))))
    margin = max(1e-9 * (1.0 + upper), float(np.spacing(scale)) * 16)
    # A closest triangle must have AABB distance to the other whole mesh's
    # AABB no greater than the true distance, itself <= the sampled upper bound.
    # Thus this crop retains skew edge/edge and triangle-interior minima too.
    ia = _near_triangle_indices(a, bounds_b, upper, margin, cancel_event)
    ib = _near_triangle_indices(b, bounds_a, upper, margin, cancel_event)
    ba, bb = _blocks(a, ia, cancel_event), _blocks(b, ib, cancel_event)
    candidates = sorted((_bbox_distance(x[1], y[1]), i, j)
                        for i, x in enumerate(ba) for j, y in enumerate(bb))
    cache = OrderedDict()
    best = upper
    queries = 0
    for lower, i, j in candidates:
        if _stopped(cancel_event):
            raise InterruptedError("Inspection cancelled during surface-distance query.")
        if lower > best + margin:
            break
        native = []
        for side, mesh, block, number in ((0, a, ba[i], i), (1, b, bb[j], j)):
            key = (side, number)
            obj = cache.pop(key) if key in cache else _block_collision(mesh, block[0])
            cache[key] = obj
            while len(cache) > 4:
                cache.popitem(last=False)
            native.append(obj)
        distance, points = _surface_distance(*native)
        queries += 1
        if distance < best:
            best, nearest = distance, points
        elif distance == best and points is not None:
            nearest = points
        if best == 0:
            break
    if not queries:
        raise ValueError("No exact surface-distance query was evaluated")
    return best, nearest


class _SmallCollisionCache:
    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, index, mesh):
        if index in self.entries:
            obj, estimate = self.entries.pop(index)
            self.entries[index] = (obj, estimate)
            return obj
        estimate = len(mesh.faces) * 600 + len(mesh.vertices) * 32
        obj = _collision_object(mesh)
        if estimate <= _FCL_CACHE_BYTES:
            while self.entries and self.bytes + estimate > _FCL_CACHE_BYTES:
                _, (_, removed) = self.entries.popitem(last=False)
                self.bytes -= removed
            self.entries[index] = (obj, estimate)
            self.bytes += estimate
        return obj


class _SmallManifoldCache:
    """Reuse nearby small solids with a conservative, bounded LRU budget."""

    def __init__(self):
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, index, mesh):
        if index in self.entries:
            solid, estimate = self.entries.pop(index)
            self.entries[index] = (solid, estimate)
            return solid
        solid = _manifold(mesh)
        estimate = len(mesh.faces) * 512 + len(mesh.vertices) * 64
        if estimate <= _MANIFOLD_CACHE_BYTES:
            while self.entries and self.bytes + estimate > _MANIFOLD_CACHE_BYTES:
                _, (_, removed) = self.entries.popitem(last=False)
                self.bytes -= removed
            self.entries[index] = (solid, estimate)
            self.bytes += estimate
        return solid


def check_overlaps(objects: Iterable[MeshObject], *, volume_tolerance=1e-6,
                   min_gap_mm=0.0, distance_tolerance_mm=1e-6,
                   cancel_event=None, progress=None) -> dict:
    """Classify all pairs without keeping scene-wide native BVHs or solids.

    Ordinary small-scene pairs retain exact distances. Large scenes and pairs
    involving a normalized CAD mesh may certify distant clear pairs with
    distance_mm=None and a separate lower bound.
    Sorted branch-and-bound still finds the exact closest valid pair and checks
    every possible gap/contact failure. Exactly provable collinear CAD seams
    may be retriangulated on a private copy; other invalid meshes remain
    inconclusive. Ordinary valid meshes do not run this preparation step.
    """
    parameters = {"volume_tolerance": float(volume_tolerance), "min_gap_mm": float(min_gap_mm),
                  "distance_tolerance_mm": float(distance_tolerance_mm)}
    for name, value in parameters.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite, non-negative number")
    volume_tolerance = parameters["volume_tolerance"]
    min_gap_mm = parameters["min_gap_mm"]
    distance_tolerance_mm = parameters["distance_tolerance_mm"]
    objects = list(objects)
    stopped = lambda: _stopped(cancel_event)
    large = sum(len(obj.mesh.faces) for obj in objects if isinstance(obj.mesh, trimesh.Trimesh)) > LARGE_SCENE_FACE_THRESHOLD
    topology_cache = {}
    records = []
    for i, obj in enumerate(objects):
        if stopped():
            records.append({"index": i, "name": obj.name, "valid": False,
                            "errors": ["Inspection cancelled before mesh validation."],
                            "bounds": None, "volume": None})
        else:
            if progress:
                progress(5 + 15 * i / max(1, len(objects)), f"오브젝트 {i + 1}/{len(objects)} 메쉬 검증 중입니다.")
            record = _validate_object(i, obj, topology_cache, cancel_event)
            if (not record["valid"] and not stopped()
                    and record["errors"] == ["Mesh contains degenerate or invalid triangles."]):
                try:
                    # Keep the normal valid-mesh path unchanged. This narrow
                    # fallback proves an exact triangle partition, never a
                    # tolerance weld, guessed hole fill or native auto-repair.
                    from .seams import exact_collinear_seam_repair

                    prepared, proof = exact_collinear_seam_repair(obj.mesh, cancel_event=cancel_event)
                    prepared_record = dict(proof["ordinary_validation"])
                    if not prepared_record["valid"]:
                        raise ValueError("Prepared mesh did not pass ordinary solid validation")
                    prepared_record.update(index=i, name=obj.name,
                                           source_errors=list(record["errors"]),
                                           original_triangle_bounds=record["original_triangle_bounds"],
                                           source_triangles_finite_indexed=record["source_triangles_finite_indexed"],
                                           source_topology_diagnostics=record["topology_diagnostics"],
                                           normalization={key: value for key, value in proof.items()
                                                          if key != "ordinary_validation"})
                    # The caller's placed objects/arrays are never replaced.
                    # Query only the separately certified, identical surface.
                    objects[i] = MeshObject(obj.name, prepared)
                    record = prepared_record
                except Exception as exc:
                    record["normalization_error"] = f"{type(exc).__name__}: {exc}"
            records.append(record)
    normalized_indices = {row["index"] for row in records if row.get("normalization")}
    pairs, candidates, attempted = [], [], set()
    total_pair_count = len(objects) * (len(objects) - 1) // 2
    for i, j in combinations(range(len(objects)), 2):
        if stopped():
            break
        pair = {"index_a": i, "index_b": j, "object_a": objects[i].name,
                "object_b": objects[j].name, "status": "inconclusive",
                "intersection_volume": None, "distance_mm": None, "distance_is_exact": False,
                "placement_status": "unknown", "placement_method": "unknown",
                "placement_distance_lower_bound_mm": None}
        pairs.append(pair)
        if (records[i].get("source_triangles_finite_indexed")
                and records[j].get("source_triangles_finite_indexed")):
            placement_lower = _placement_bbox_lower_bound(records[i]["original_triangle_bounds"],
                                                          records[j]["original_triangle_bounds"])
            if placement_lower is not None:
                pair.update(placement_method="original-triangle-aabb-bound",
                            placement_distance_lower_bound_mm=placement_lower)
                if (placement_lower > distance_tolerance_mm
                        and placement_lower + distance_tolerance_mm >= min_gap_mm):
                    pair["placement_status"] = "clear"
        if stopped():
            break
        if not records[i]["valid"] or not records[j]["valid"]:
            pair["error"] = "One or both objects failed solid-mesh validation."
            if not stopped():
                attempted.add(id(pair))
        else:
            bounds_a, bounds_b = np.asarray(records[i]["bounds"]), np.asarray(records[j]["bounds"])
            lower = _bbox_distance(bounds_a, bounds_b)
            candidates.append((lower, i, j, pair, bounds_a, bounds_b))
    if large:
        candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    elif normalized_indices:
        # Preserve every ordinary pair's exact measurement and source order.
        # Newly usable CAD objects add pairs that previously went unchecked.
        # Inspect those in bound order after the ordinary exact minimum is
        # known, and certify distant ones using the existing safe bound rule.
        ordinary = [row for row in candidates
                    if row[1] not in normalized_indices and row[2] not in normalized_indices]
        prepared = [row for row in candidates
                    if row[1] in normalized_indices or row[2] in normalized_indices]
        prepared.sort(key=lambda row: (row[0], row[1], row[2]))
        candidates = ordinary + prepared
    best, best_pair = math.inf, None
    collision_cache = _SmallCollisionCache()
    solid_cache = _SmallManifoldCache() if normalized_indices else None
    for done, (lower, i, j, pair, bounds_a, bounds_b) in enumerate(candidates):
        if stopped():
            break
        if progress:
            progress(20 + 80 * done / max(1, len(candidates)), f"오브젝트 쌍 {done:,}/{len(candidates):,} 검사 중입니다.")
        if stopped():
            break
        attempted.add(id(pair))
        safe_gap = lower > distance_tolerance_mm and lower + distance_tolerance_mm >= min_gap_mm
        use_bound = large or i in normalized_indices or j in normalized_indices
        if use_bound and safe_gap and lower >= best:
            pair.update(status="clear", intersection_volume=0.0,
                        distance_lower_bound_mm=lower, method="aabb-clearance-bound")
            # Keep the legacy result, but never let its rounded AABB value
            # override the independent conservative original-triangle proof.
            if pair["placement_status"] == "clear":
                pair["placement_method"] = "validated-solid-aabb-bound"
            continue
        try:
            extents = np.minimum(bounds_a[1], bounds_b[1]) - np.maximum(bounds_a[0], bounds_b[0])
            volume = 0.0
            if np.all(extents > 0):
                if solid_cache is None:
                    a, b = _manifold(objects[i].mesh), _manifold(objects[j].mesh)
                else:
                    a, b = solid_cache.get(i, objects[i].mesh), solid_cache.get(j, objects[j].mesh)
                intersection = a ^ b
                if intersection.status() != manifold3d.Error.NoError:
                    raise ValueError(f"Intersection failed: {intersection.status()}")
                volume = float(intersection.volume())
                del intersection, a, b
                if not math.isfinite(volume) or volume < 0:
                    raise ValueError("Boolean result has invalid intersection volume")
            if volume > 0:
                distance, nearest = 0.0, None
                status = "overlap" if volume > volume_tolerance else "touching"
            else:
                if large or max(len(objects[i].mesh.faces), len(objects[j].mesh.faces)) > FCL_BLOCK_FACES:
                    distance, nearest = _bounded_surface_distance(objects[i].mesh, objects[j].mesh,
                                                                 bounds_a, bounds_b, cancel_event)
                else:
                    distance, nearest = _surface_distance(collision_cache.get(i, objects[i].mesh),
                                                         collision_cache.get(j, objects[j].mesh))
                status = ("touching" if distance <= distance_tolerance_mm else
                          "too_close" if distance + distance_tolerance_mm < min_gap_mm else "clear")
            pair.update(status=status, intersection_volume=volume, distance_mm=distance,
                        distance_is_exact=True, method="manifold64+fcl")
            pair.update(placement_status="clear" if status == "clear" else "violation",
                        placement_method="validated-solid-query")
            if 0 < volume <= volume_tolerance:
                pair["reason"] = "Positive intersection at or below volume tolerance."
            if nearest is not None:
                pair["nearest_points_mm"] = nearest
            if distance < best:
                best, best_pair = distance, [i, j]
        except Exception as exc:
            pair["error"] = f"Geometry query failed: {type(exc).__name__}: {exc}"
    if stopped():
        # Preserve only attempted records, in source index order. Preallocated
        # but unvisited pairs must not masquerade as completed error results.
        pairs = [pair for pair in pairs if id(pair) in attempted]
    overlaps = [pair for pair in pairs if pair["status"] == "overlap"]
    violations = [pair for pair in pairs if pair["status"] in {"overlap", "touching", "too_close"}]
    complete = bool(objects) and not stopped() and all(row["valid"] for row in records) and all(pair["status"] != "inconclusive" for pair in pairs)
    valid_scope_complete = not stopped() and all(pair["status"] != "inconclusive" for pair in pairs
                                                if records[pair["index_a"]]["valid"] and records[pair["index_b"]]["valid"])
    placement_complete = (bool(objects) and not stopped() and len(pairs) == total_pair_count
                          and all(row.get("source_triangles_finite_indexed") for row in records)
                          and all(pair["placement_status"] != "unknown" for pair in pairs))
    placement_violation = any(pair["placement_status"] == "violation" for pair in pairs)
    placement_minimum_certified = (not stopped() and len(pairs) == total_pair_count
        and all(row.get("source_triangles_finite_indexed") for row in records)
        and math.isfinite(best) and all(
            pair["distance_is_exact"] or (pair["placement_distance_lower_bound_mm"] is not None
                and pair["placement_distance_lower_bound_mm"] >= best) for pair in pairs))
    return {"status": "violation" if violations else "clear" if complete else "inconclusive",
            "complete": complete, "overlap_found": bool(overlaps), "violation_found": bool(violations),
            "cancelled": stopped(), "total_pair_count": total_pair_count, "object_count": len(objects), **parameters,
            "processing_completed": not stopped(),
            "objects": records, "pairs": pairs, "overlaps": overlaps, "violations": violations,
            "placement_status": "violation" if placement_violation else "clear" if placement_complete else "unknown",
            "placement_complete": placement_complete,
            "placement_minimum_distance_mm": best if placement_minimum_certified else None,
            "placement_minimum_distance_pair": best_pair if placement_minimum_certified else None,
            "placement_minimum_distance_is_certified": placement_minimum_certified,
            "distance_mode": "certified-clearance" if large or normalized_indices else "exact-all-pairs",
            "pair_distances_complete": not stopped() and len(pairs) == total_pair_count and all(pair["distance_mm"] is not None for pair in pairs),
            "minimum_distance_mm": best if math.isfinite(best) else None,
            "minimum_distance_pair": best_pair,
            "closest_pair_scope": "all_objects" if all(row["valid"] for row in records) else "valid_objects",
            "closest_distance_is_global": math.isfinite(best) and complete,
            "closest_distance_is_global_for_scope": math.isfinite(best) and valid_scope_complete}
