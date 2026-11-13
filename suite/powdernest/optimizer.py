"""Seeded mesh nesting: extreme points, mesh contacts, annealing and reinsertion.

All allowed rotation identities remain available, including identical AABBs.
This bounded heuristic provides no optimality certificate. Independent final
verification runs outside the search budget; elapsed includes that verification.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import heapq
import math
from numbers import Integral
import random
import threading
import time
from typing import Callable

import numpy as np

from .rotations import ROTATIONS
from .types import Model, Placement, RequestItem, Result, Settings
from . import native


@dataclass
class _Orientation:
    rotation_id: int
    extent: np.ndarray
    vertices: np.ndarray


@dataclass
class _Shape:
    model: Model
    orientations: dict[int, _Orientation]
    volume: float
    error: float
    box: bool


@dataclass
class _PlacedSnapshot:
    placements: tuple[Placement, ...]
    low: np.ndarray
    high: np.ndarray
    errors: np.ndarray
    boxes: np.ndarray
    world_vertices: list[np.ndarray]
    required: dict[str, np.ndarray]


class _Budget:
    def __init__(self, seconds: float, cancel: threading.Event | None):
        self.start = time.perf_counter()
        self.deadline = self.start + seconds
        self.cancel = cancel
        self.reason: str | None = None

    def available(self) -> bool:
        if self.cancel is not None and self.cancel.is_set():
            self.reason = 'cancelled'
            return False
        if time.perf_counter() >= self.deadline:
            self.reason = 'time_limit'
            return False
        return True


def _samples(vertices: np.ndarray, limit: int = 12) -> np.ndarray:
    """Keep extrema and interior-coordinate vertices that expose mesh cavities."""
    vertices = np.unique(vertices, axis=0)
    if len(vertices) <= limit:
        return vertices
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    interior = ((vertices > low) & (vertices < high)).sum(axis=1)
    selected = list(dict.fromkeys(
        [int(np.argmin(vertices[:, a])) for a in range(3)]
        + [int(np.argmax(vertices[:, a])) for a in range(3)]))
    candidates = np.flatnonzero(interior == interior.max())
    for i in np.linspace(0, len(candidates) - 1, min(limit, len(candidates)), dtype=int):
        if int(candidates[i]) not in selected:
            selected.append(int(candidates[i]))
        if len(selected) >= limit:
            break
    for i in np.linspace(0, len(vertices) - 1, limit, dtype=int):
        if int(i) not in selected:
            selected.append(int(i))
        if len(selected) >= limit:
            break
    return vertices[selected[:limit]]


def _prepare(model: Model) -> _Shape:
    allowed = tuple(model.allowed_rotations)
    if (not allowed or any(not isinstance(r, Integral) or isinstance(r, bool)
                           or not 0 <= int(r) < 24 for r in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError('allowed_rotations must contain unique rotation IDs in 0..23')
    error = float(model.tessellation_error)
    if not math.isfinite(error) or error < 0:
        raise ValueError('tessellation_error must be finite and nonnegative')
    vertices = np.asarray(model.mesh.vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all():
        raise ValueError('model mesh must have finite three dimensional vertices')
    sample = _samples(vertices)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    orientations = {}
    for rid in allowed:
        rotation = ROTATIONS[int(rid)]
        positive, negative = np.maximum(rotation, 0), np.minimum(rotation, 0)
        bounds = np.array([positive @ low + negative @ high,
                           positive @ high + negative @ low])
        extent = bounds[1] - bounds[0]
        if not np.isfinite(extent).all() or np.any(extent <= 0):
            raise ValueError('model must have positive extent on every axis')
        orientations[int(rid)] = _Orientation(
            int(rid), extent, sample @ ROTATIONS[int(rid)].T - bounds[0])
    lo, hi = low, high
    # Other shapes always retain mesh narrow phase. Recognise only exact cuboids.
    corners_only = bool(np.all((vertices == lo) | (vertices == hi)))
    box = bool(corners_only and len(np.unique(vertices, axis=0)) == 8
               and model.mesh.is_watertight and model.mesh.is_convex)
    # Fresh validation computes shell volumes after subtracting a local origin,
    # avoiding cancellation when imported coordinates are far from zero.
    volume = float(model.diagnostics['material_volume_mm3']
                   if 'material_volume_mm3' in model.diagnostics else abs(model.mesh.volume))
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError('model mesh must enclose positive finite material volume')
    return _Shape(model, orientations, volume, error, box)


class _Search:
    def __init__(self, models, items, settings, budget, progress):
        from .geometry import CollisionEngine
        self.settings = settings
        self.budget = budget
        self.rng = random.Random(int(settings.seed))
        self.progress = progress
        self.callback_errors = 0
        self.engine = CollisionEngine(models, settings)
        self.shapes = {key: _prepare(model) for key, model in models.items()}
        self.margins = {key: settings.boundary_margins(shape.error) for key, shape in self.shapes.items()}
        self.keys = [(item.model_id, i) for item in items for i in range(item.quantity)]
        self.requested = len(self.keys)
        nominal_heights = []
        for key in {k[0] for k in self.keys}:
            shape = self.shapes[key]
            usable = np.asarray(settings.size[:2]) - 2 * (settings.wall_gap + shape.error) + 2 * settings.tolerance
            heights = [o.extent[2] for o in shape.orientations.values() if np.all(o.extent[:2] <= usable)]
            if heights:
                nominal_heights.append(shape.error + min(heights))
        self.height_lower_bound = max(nominal_heights, default=0.) - settings.tolerance
        self.iterations = 0
        self.best: list[Placement] = []
        self.best_score = (0, 0.0)
        self.initial: list[Placement] = []
        self.initial_score = (0, 0.0)
        self.last_progress = 0.0
        self.candidate_checks = 0
        self._placed_snapshot: _PlacedSnapshot | None = None
        self._pair_cache: OrderedDict[tuple, bool] = OrderedDict()
        self.operator_weights = {'top': 4.0, 'cluster': 2.0, 'random': 1.0}
        self.operator_stats = {name: {'attempts': 0, 'completed': 0, 'improvements': 0,
                                     'interrupted': 0, 'failures': 0,
                                     'placed_gain': 0, 'height_gain_mm': 0.}
                               for name in self.operator_weights}
        self.quality_stats = {'repair_attempts': 0, 'height_target_attempts': 0, 'height_target_successes': 0,
                              'regret_insertions': 0, 'compaction_passes': 0,
                              'compaction_moves': 0, 'overlap_attempts': 0,
                              'compaction_height_gain_mm': 0.,
                              'overlap_moves': 0, 'overlap_successes': 0,
                              'overlap_height_gain_mm': 0., 'overlap_skipped_large': 0}
        self.impossible: set[str] = set()
        self.certificate = self._certificate(items)
        self.policy = None
        self.learn_updates = False
        self.starting = None
        self.rl_rng = random.Random(int(settings.seed) ^ 0x514C4541)
        self.stagnation = 0
        self.stage_report = {}
        self.rl_checkpoint = None
        self.learning_stats = {'enabled': False, 'policy_steps': 0, 'heuristic_steps': 0,
                               'td_updates': 0, 'reward': 0., 'actions': {}}

    def _certificate(self, items):
        size = np.asarray(self.settings.size)
        tol = self.settings.tolerance
        reasons = []
        for item in items:
            if not item.quantity:
                continue
            shape = self.shapes[item.model_id]
            available = size - 2 * self.margins[item.model_id] + 2 * tol
            if all(np.any(o.extent > available) for o in shape.orientations.values()):
                self.impossible.add(item.model_id)
                # CAD deflection is a declared conservative allowance, not a
                # certified Hausdorff bound. Failure of that envelope is not
                # evidence that the original CAD solid is impossible to fit.
                if shape.error == 0:
                    reasons.append({'type': 'individual_bounds', 'model_id': item.model_id,
                                    'allowed_rotations': list(shape.orientations),
                                    'available_extent': available.tolist(),
                                    'scope': 'validated tessellated material geometry'})
        # Closed, nonintersecting material cannot exceed the tolerance-expanded
        # usable container volume. Never substitute summed AABB volumes here.
        available = np.maximum(0.0, size - 2 * self.settings.boundary_margins() + 2 * tol)
        volume = math.prod(available)
        lower = math.fsum(item.quantity * max(
            0.0, self.shapes[item.model_id].volume
            - float(self.shapes[item.model_id].model.mesh.area) * tol
            - 64 * np.finfo(float).eps * max(1.0, self.shapes[item.model_id].volume))
                         for item in items)
        exact_meshes = all(not item.quantity or self.shapes[item.model_id].error == 0
                           for item in items)
        if exact_meshes and lower > volume + 64 * np.finfo(float).eps * max(1.0, volume):
            reasons.append({'type': 'material_volume', 'material_volume_lower_bound': lower,
                            'usable_volume_upper_bound': volume,
                            'scope': 'validated tessellated material geometry'})
        return reasons

    def extent(self, p):
        return self.shapes[p.model_id].orientations[p.rotation_id].extent

    def height(self, placements):
        return max((p.position[2] + self.extent(p)[2] for p in placements), default=0.0)

    def score(self, placements):
        return (-len(placements), float(self.height(placements)))

    def emit(self, force=False, status='searching'):
        now = time.perf_counter()
        if self.progress is None or (not force and now - self.last_progress < .1):
            return
        self.last_progress = now
        message = {'placed': len(self.best), 'requested': self.requested,
                   'height': float(self.height(self.best)), 'iterations': self.iterations,
                   'elapsed': now - self.budget.start, 'status': status,
                   'stage': ('precision' if getattr(self, 'precision_active', False) else
                             ('policy' if self.rl_checkpoint is None else 'algorithm') if self.stage_report else 'standard'),
                   'complete': len(self.best) == self.requested,
                   'objective': self.best_score, 'initial_score': self.initial_score}
        try:
            self.progress(message)
        except Exception:
            self.callback_errors += 1

    def retain(self, placements):
        score = self.score(placements)
        if score < self.best_score:
            self.best, self.best_score = list(placements), score
            self.emit(force=True)

    def clearance(self, a, b):
        gap = self.settings.gap + a.error + b.error
        return gap if gap > 0 else 2 * self.settings.tolerance

    def _placed(self, existing):
        """Reuse arrays only while the complete immutable placement tuple agrees.

        Restarts, removals, reordering and replacement within the same list all
        invalidate the snapshot. Models here are the private revalidated copies
        owned by this search; public mutable Model bounds remain uncached.
        """
        placements = tuple(existing)
        cached = self._placed_snapshot
        if cached is None or cached.placements != placements:
            low = np.asarray([p.position for p in placements], dtype=float).reshape((-1, 3))
            extents = np.asarray([self.extent(p) for p in placements], dtype=float).reshape((-1, 3))
            shapes = [self.shapes[p.model_id] for p in placements]
            cached = _PlacedSnapshot(
                placements, low, low + extents,
                np.asarray([s.error for s in shapes]),
                np.asarray([s.box for s in shapes], dtype=bool),
                [s.orientations[p.rotation_id].vertices + low[i]
                 for i, (p, s) in enumerate(zip(placements, shapes))], {})
            self._placed_snapshot = cached
        return cached

    def _required(self, shape, placed):
        if shape.model.model_id not in placed.required:
            placed.required[shape.model.model_id] = self.settings.gap + shape.error + placed.errors
        return placed.required[shape.model.model_id]

    @staticmethod
    def _mesh_contacts(world, own, gap, extent, wall):
        """Batch the original vertex/contact loops without changing their order."""
        accelerated = native.mesh_contacts(world, own, gap, extent, wall)
        if accelerated is not None: return accelerated
        points = np.empty((len(world), 8 + 5 * len(own), 3))
        corners = world[:, None, :2] + gap * np.asarray(((1, 1), (1, -1), (-1, 1), (-1, -1)))
        points[:, :8:2, :2] = corners
        points[:, 1:8:2, :2] = corners - extent[:2]
        points[:, :8, 2] = wall
        contacts = points[:, 8:].reshape((len(world), len(own), 5, 3))
        contacts[:] = (world[:, None, :] - own)[:, :, None, :]
        contacts[:, :, 0, 0] += gap
        contacts[:, :, 0, 1] += gap
        contacts[:, :, 1, 0] -= gap
        contacts[:, :, 1, 1] -= gap
        contacts[:, :, 2, 0] += gap
        contacts[:, :, 2, 1] -= gap
        contacts[:, :, 3, 0] -= gap
        contacts[:, :, 3, 1] += gap
        contacts[:, :, 4, 2] += gap
        return points.reshape((-1, 3))

    @staticmethod
    def _solid_positions(array, extent, low, high, required, tol):
        """Use axis separations to select pairs for the unchanged exact rule.

        Every penetrating AABB has zero separation on each axis. Every pair
        closer than the required Euclidean clearance is also within that
        clearance on each axis. The selection therefore only skips pairs that
        cannot reject a point; the distance/overlap arithmetic remains exact.
        """
        flags = native.solid_filter(array, extent, low, high, required, tol)
        if flags is not None:
            uncertain = np.flatnonzero(flags == 2)
            if len(uncertain):
                with native.backend('python'):
                    for row in uncertain:
                        flags[row] = bool(len(_Search._solid_positions(array[row:row+1], extent, low, high, required, tol)))
            return array[flags == 1]
        keep = []
        cutoff = np.maximum(required - tol, 0)
        for start in range(0, len(array), 256):
            pos = array[start:start + 256]
            possible = np.ones((len(pos), len(low)), dtype=bool)
            for axis in range(3):
                left = low[:, axis] - (pos[:, None, axis] + extent[axis])
                right = pos[:, None, axis] - high[:, axis]
                possible &= np.maximum(left, right) <= cutoff
            rows, cols = np.nonzero(possible)
            mask = np.ones(len(pos), dtype=bool)
            if len(rows):
                candidates = pos[rows]
                top = candidates + extent
                overlap = np.minimum(top, high[cols]) - np.maximum(candidates, low[cols])
                sep = np.maximum(np.maximum(low[cols] - top, candidates - high[cols]), 0)
                distance = np.linalg.norm(sep, axis=1)
                reject = np.all(overlap > 0, axis=1) | (distance < required[cols] - tol)
                mask[rows[reject]] = False
            keep.append(pos[mask])
        return np.concatenate(keep) if keep else np.empty((0, 3))

    def _pair_valid(self, a, b):
        # copy_index does not affect geometry. Preserve the exact ordered model,
        # rotation and coordinate identities; no rotation/AABB deduplication or
        # endpoint swapping is used. All misses retain the original FCL checks.
        key = (a.model_id, a.rotation_id, a.position, b.model_id, b.rotation_id, b.position)
        try:
            valid = self._pair_cache.pop(key)
        except KeyError:
            valid, _, _ = self.engine.check_pair(a, b)
            if (self.engine.last_interlocking or {}).get('status', '').startswith('unknown'):
                return False  # unfinished process checks are not cached as geometry facts
        self._pair_cache[key] = valid
        if len(self._pair_cache) > 8192:
            self._pair_cache.popitem(last=False)
        return valid

    def _positions(self, shape, orientation, existing, deep=False, extras=()):
        """Bounded extreme points plus contacts on actual interior mesh vertices."""
        tol = self.settings.tolerance
        margin = self.margins[shape.model.model_id]
        wall, floor = margin[0], margin[2]
        extent = orientation.extent
        points = [(wall, wall, floor), *extras]
        placed = self._placed(existing)
        xs, ys = [wall], [wall]
        complex_neighbors = []
        for i, p in enumerate(existing):
            other = self.shapes[p.model_id]
            low, high = placed.low[i], placed.high[i]
            gap = self.clearance(shape, other)
            x, y, z = low
            xx, yy, zz = high + gap
            points.extend(((xx, y, z), (x, yy, z), (x, y, zz),
                           (xx, wall, floor), (wall, yy, floor), (wall, wall, zz),
                           (x, y, floor), (x - extent[0] - gap, y, z),
                           (x, y - extent[1] - gap, z),
                           (x, y, z - extent[2] - gap)))
            xs.extend((float(xx), float(x)))
            ys.extend((float(yy), float(y)))
            if not other.box or not shape.box:
                complex_neighbors.append(i)
        for x in sorted(set(xs))[:20]:
            for y in sorted(set(ys))[:20]:
                points.append((x, y, floor))
        if self.settings.mode == 'mesh':
            # No orientation deduplication: transformed concave vertices determine
            # cavity access even when the bounding boxes are identical.
            complex_neighbors.sort(key=lambda i: (existing[i].position[2], existing[i].position[1],
                                                  existing[i].position[0]))
            if len(complex_neighbors) > 6:
                complex_neighbors = complex_neighbors[:3] + self.rng.sample(complex_neighbors[3:], 3)
            own = orientation.vertices
            if len(own) > 6:
                own = own[np.linspace(0, len(own) - 1, 6, dtype=int)]
            contact_arrays = []
            for i in complex_neighbors:
                p = existing[i]
                other = self.shapes[p.model_id]
                world = placed.world_vertices[i]
                gap = self.clearance(shape, other)
                contact_arrays.append(self._mesh_contacts(world, own, gap, extent, floor))
        else:
            contact_arrays = []
        # Keep the original extreme/grid/contact/deep insertion order: duplicate
        # removal retains the first point and heap tie ordering depends on it.
        arrays = [np.asarray(points, dtype=float).reshape((-1, 3)), *contact_arrays]
        points = []
        if deep:
            high = np.asarray(self.settings.size) - margin - extent
            layers = [floor] + sorted({float(p.position[2] + self.extent(p)[2]
                                             + self.clearance(shape, self.shapes[p.model_id]))
                                     for p in existing})[:3]
            for scale in (2.0, 1.0, .5):
                step = self.settings.search_step * scale
                nx = max(0, int((high[0] - wall) / step))
                ny = max(0, int((high[1] - wall) / step))
                stride = max(1, int(math.ceil(math.sqrt((nx + 1) * (ny + 1) / 196))))
                offset_x = 0 if stride == 1 else self.rng.randrange(stride)
                offset_y = 0 if stride == 1 else self.rng.randrange(stride)
                for z in layers:
                    for ix in range(offset_x, nx + 1, stride):
                        for iy in range(offset_y, ny + 1, stride):
                            points.append((wall + ix * step, wall + iy * step, z))
        if points:
            arrays.append(np.asarray(points, dtype=float).reshape((-1, 3)))
        array = np.concatenate(arrays)
        upper = np.asarray(self.settings.size) - margin - extent
        array = array[np.all((array >= margin - tol) & (array <= upper + tol), axis=1)]
        if not len(array):
            return array
        quantized = np.round(array / max(tol * .1, 1e-10)).astype(np.int64)
        # Only membership and the first index matter: raw fixed-width rows
        # avoid NumPy's slower three-field structured sort. Sorting the first
        # indices restores exactly the original candidate and RNG order.
        indices = native.unique_rows(quantized)
        if indices is None:
            row_keys = np.ascontiguousarray(quantized).view(np.dtype((np.void, 24))).ravel()
            _, indices = np.unique(row_keys, return_index=True)
            indices = np.sort(indices)
        array = array[indices]
        solid = np.ones(len(existing), dtype=bool) if self.settings.mode == 'aabb' else placed.boxes & shape.box
        if np.any(solid):
            low, high = placed.low[solid], placed.high[solid]
            required = self._required(shape, placed)[solid]
            array = self._solid_positions(array, extent, low, high, required, tol)
        return array

    def can_place(self, p, existing):
        self.candidate_checks += 1
        shape = self.shapes[p.model_id]
        low = np.asarray(p.position)
        high = low + self.extent(p)
        margin = self.margins[p.model_id]
        tol = self.settings.tolerance
        if np.any(low < margin - tol) or np.any(high > np.asarray(self.settings.size) - margin + tol):
            return False
        if not existing:
            return True
        placed = self._placed(existing)
        lows, highs = placed.low, placed.high
        required = self._required(shape, placed)
        # NumPy's small-vector C loops beat repeated FFI setup here. Rust's
        # batched query is reserved for larger neighbourhoods; both preserve
        # ordered candidates and delegate threshold ambiguity to the reference.
        broad = native.neighbors(low, high, lows, highs, required, tol) if len(lows) >= 2048 else None
        if broad is None:
            sep = np.maximum(np.maximum(lows - high, low - highs), 0)
            distance = np.linalg.norm(sep, axis=1)
            neighbors = np.flatnonzero(distance <= required + tol)
        else:
            neighbors, distance = broad
        for i in neighbors:
            q = existing[int(i)]
            if self.settings.mode == 'aabb':
                overlap = np.minimum(high, highs[i]) - np.maximum(low, lows[i])
                if np.all(overlap > 0) or distance[i] < required[i] - tol:
                    return False
            if not self._pair_valid(p, q):
                return False
        return True

    def _quality_points(self, shape, orientation, existing, height_cap):
        """Bounded cross-products of free-space faces at several Z levels.

        These are proposals, not voxel/AABB feasibility evidence. Every returned
        pose still passes can_place, including the mesh and containment checks.
        """
        margin = self.margins[shape.model.model_id]
        extent = orientation.extent
        upper = np.asarray(self.settings.size) - margin - extent
        if height_cap is not None:
            upper[2] = min(upper[2], height_cap - extent[2])
        if np.any(upper < margin):
            return np.empty((0, 3))
        coordinates = [[float(margin[a]), float(upper[a])] for a in range(3)]
        placed = self._placed(existing)
        for i, p in enumerate(existing):
            gap = self.clearance(shape, self.shapes[p.model_id])
            for axis in range(3):
                coordinates[axis].extend((float(placed.low[i, axis]),
                    float(placed.high[i, axis] + gap),
                    float(placed.low[i, axis] - extent[axis] - gap)))
        for axis, limit in enumerate((10, 10, 6)):
            values = sorted({x for x in coordinates[axis] if margin[axis] <= x <= upper[axis]})
            if len(values) > limit:
                # Include low contacts and high pockets instead of keeping only
                # the first three layers used by the legacy deep grid.
                indices = np.linspace(0, len(values) - 1, limit, dtype=int)
                values = [values[int(i)] for i in indices]
            coordinates[axis] = values
        return np.asarray([(x, y, z) for z in coordinates[2]
                           for y in coordinates[1] for x in coordinates[0]])

    def find_pose(self, key, existing, preferred=None, only_rotation=None, deep=False, extras=(),
                  height_cap=None, quality=False, limit=1):
        if key[0] in self.impossible:
            return [] if limit > 1 else None
        shape = self.shapes[key[0]]
        rotations = list(shape.orientations)
        if only_rotation is not None:
            rotations = [only_rotation]
        elif preferred in rotations:
            rotations.remove(preferred)
            rotations.insert(0, preferred)
        height = self.height(existing)
        heap, lists, found = [], {}, []
        xy = np.max(self._placed(existing).high[:, :2], axis=0) if existing else np.zeros(2)

        def priority_for(pos, orientation, priority):
            top = max(height, pos[2] + orientation.extent[2])
            if quality:
                area = float(np.prod(np.maximum(xy, pos[:2] + orientation.extent[:2])))
                return (top, area, pos[2], priority, pos[1], pos[0])
            return (top, pos[2], priority, pos[1], pos[0])

        for priority, rid in enumerate(rotations):
            if not self.budget.available():
                return found if limit > 1 else None
            orientation = shape.orientations[rid]
            proposed = extras
            if quality:
                proposed = [*extras, *self._quality_points(shape, orientation, existing, height_cap)]
            positions = self._positions(shape, orientation, existing, deep, proposed)
            if height_cap is not None:
                positions = positions[positions[:, 2] + orientation.extent[2] <= height_cap]
            if not len(positions):
                continue
            top = np.maximum(height, positions[:, 2] + orientation.extent[2])
            if quality:
                area = np.prod(np.maximum(xy, positions[:, :2] + orientation.extent[:2]), axis=1)
                order = np.lexsort((positions[:, 0], positions[:, 1], positions[:, 2], area, top))
            else:
                order = np.lexsort((positions[:, 0], positions[:, 1], positions[:, 2], top))
            positions = positions[order][:1536 if deep else 768]
            lists[rid] = positions
            pos = positions[0]
            heapq.heappush(heap, (priority_for(pos, orientation, priority), rid, 0, priority))
        checks_started = self.candidate_checks
        first_found_at = None
        while heap:
            if not self.budget.available():
                return found if limit > 1 else None
            if quality and (self.candidate_checks - checks_started >= 384 or
                            (first_found_at is not None and self.candidate_checks - first_found_at >= 32)):
                return found if limit > 1 else None
            _, rid, index, priority = heapq.heappop(heap)
            pos = lists[rid][index]
            p = Placement(key[0], key[1], rid, tuple(float(x) for x in pos))
            if self.can_place(p, existing):
                if limit == 1:
                    return p
                found.append(p)
                if first_found_at is None:
                    first_found_at = self.candidate_checks
                if len(found) >= limit:
                    return found
            index += 1
            if index < len(lists[rid]):
                pos = lists[rid][index]
                o = shape.orientations[rid]
                heapq.heappush(heap, (priority_for(pos, o, priority), rid, index, priority))
        if not found and not quality and not deep and self.settings.mode == 'mesh' and self.budget.available():
            return self.find_pose(key, existing, preferred, only_rotation, True, extras,
                                  height_cap, quality, limit)
        return found if limit > 1 else None

    def _compact(self, placements):
        """Coordinate descent with only individually FCL-verified moves."""
        self.quality_stats['compaction_passes'] += 1
        result = list(placements)
        order = sorted(placements, key=lambda p: (-p.position[2] - self.extent(p)[2],
                                                  -self.shapes[p.model_id].volume))[:32]
        axes = (2, 0, 1, 2) if self.iterations % 2 == 0 else (2, 1, 0, 2)
        minimum_move = max(1e-5, 8 * self.settings.tolerance)
        for original in order:
            if not self.budget.available():
                break
            index = result.index(original)
            p = original
            others = result[:index] + result[index + 1:]
            shape = self.shapes[p.model_id]
            margin = self.margins[p.model_id]
            if native.library() is not None:
                other_lows = np.asarray([q.position for q in others], dtype=float).reshape((-1, 3))
                other_extents = np.asarray([self.extent(q) for q in others], dtype=float).reshape((-1, 3))
                other_gaps = np.asarray([self.clearance(shape, self.shapes[q.model_id]) for q in others])
            for axis in axes:
                wall = float(margin[axis])
                upper = float(p.position[axis])
                if upper - wall < minimum_move:
                    continue
                values = (native.compaction_contacts(other_lows, other_extents, other_gaps, axis,
                          self.extent(p)[axis], wall, upper, minimum_move) if native.library() is not None else None)
                if values is None:
                    values = [wall]
                    for q in others:
                        gap = self.clearance(shape, self.shapes[q.model_id])
                        values.extend((q.position[axis] + self.extent(q)[axis] + gap,
                                       q.position[axis] - self.extent(p)[axis] - gap))
                    values = sorted({float(x) for x in values if wall <= x < upper - minimum_move})
                if len(values) > 24:
                    values = [values[int(i)] for i in np.linspace(0, len(values) - 1, 24, dtype=int)]
                valid = p
                lower = wall
                for value in values:
                    if not self.budget.available():
                        break
                    position = list(p.position)
                    position[axis] = value
                    candidate = Placement(p.model_id, p.copy_index, p.rotation_id, tuple(position))
                    if self.can_place(candidate, others):
                        valid = candidate
                        break
                    lower = value
                # Refine between a rejected sample and a known feasible pose.
                # Feasibility need not be monotone: only accepted endpoints move.
                upper = float(valid.position[axis])
                for _ in range(10):
                    if upper - lower < minimum_move or not self.budget.available():
                        break
                    middle = (lower + upper) * .5
                    position = list(p.position)
                    position[axis] = middle
                    candidate = Placement(p.model_id, p.copy_index, p.rotation_id, tuple(position))
                    if self.can_place(candidate, others):
                        valid, upper = candidate, middle
                    else:
                        lower = middle
                if p.position[axis] - valid.position[axis] >= minimum_move:
                    p = valid
                    self.quality_stats['compaction_moves'] += 1
            result[index] = p
            self.retain(result)
        self.quality_stats['compaction_height_gain_mm'] += max(
            0., float(self.height(placements) - self.height(result)))
        return result

    def _destroy_repair(self, placements, operator):
        """Height-targeted ALNS with a small regret-2 reinsertion neighborhood."""
        if not placements:
            return placements
        old_score = self.score(placements)
        complete = len(placements) == self.requested
        height = self.height(placements)
        lower = max((self.shapes[k[0]].error
                     + min(o.extent[2] for o in self.shapes[k[0]].orientations.values())
                     for k in self.keys), default=0.)
        target = max(lower, height - max(self.settings.search_step * .5, height * .05)) if complete else None
        if target is not None and target >= height - max(1e-5, self.settings.tolerance * 8):
            return placements
        self.quality_stats['repair_attempts'] += 1
        self.quality_stats['height_target_attempts'] += int(target is not None)
        statistics = self.operator_stats[operator]
        statistics['attempts'] += 1

        def finish(proposal, repaired=False, interrupted=False):
            improved = self.score(proposal) < old_score
            statistics['completed'] += int(repaired)
            statistics['interrupted'] = statistics.get('interrupted', 0) + int(interrupted)
            statistics['failures'] = statistics.get('failures', 0) + int(not repaired and not interrupted)
            if improved:
                statistics['improvements'] += 1
                statistics['placed_gain'] += max(0, len(proposal) - len(placements))
                if len(proposal) == len(placements) == self.requested:
                    statistics['height_gain_mm'] += max(0., height - self.height(proposal))
                self.quality_stats['height_target_successes'] += int(target is not None)
            if not interrupted:
                self.operator_weights[operator] = max(.25, min(100.,
                    .85 * self.operator_weights[operator] + .15 * (6. if improved else 1.)))
            return proposal

        top = sorted(placements, key=lambda p: p.position[2] + self.extent(p)[2], reverse=True)
        mandatory = [p for p in top if target is not None and p.position[2] + self.extent(p)[2] > target]
        seed = top[0] if operator == 'top' else self.rng.choice(top[:max(1, len(top) // 3)])
        center = np.asarray(seed.position) + self.extent(seed) * .5
        # Lowering the top layer usually also requires moving its supporting or
        # neighboring blockers. Removing only the top contributors repeats the
        # same infeasible subproblem, even with many more search iterations.
        fraction = .6 if operator == 'top' else self.rng.uniform(.3, .7)
        count = min(len(placements), max(len(mandatory) + 2,
                                        int(math.ceil(len(placements) * fraction))))
        if operator == 'random':
            ranking = self.rng.sample(list(placements), len(placements))
        elif operator == 'cluster':
            ranking = sorted(placements, key=lambda p: np.linalg.norm(
                (np.asarray(p.position) + self.extent(p) * .5 - center) * np.array([1., 1., .5])))
        else:
            ranking = top
        removed = list(mandatory)
        for p in ranking:
            if len(removed) >= count:
                break
            if p not in removed:
                removed.append(p)
        selected = {(p.model_id, p.copy_index) for p in removed}
        result = [p for p in placements if (p.model_id, p.copy_index) not in selected]
        retained = {(p.model_id, p.copy_index) for p in result}
        pending = [k for k in self.keys if k not in retained]
        while pending and self.budget.available():
            # Identical copies share a geometric subproblem, but allowed rotation
            # identities are never merged. Recompute options after each insertion.
            options = {}
            for key in pending:
                if key[0] not in options:
                    options[key[0]] = self.find_pose(key, result, height_cap=target,
                                                    quality=True, limit=2)
                if not self.budget.available():
                    return finish(placements, interrupted=True)
            if any(not poses for poses in options.values()):
                return finish(placements)

            def regret(key):
                poses = options[key[0]]
                costs = [p.position[2] + self.extent(p)[2] for p in poses]
                value = float('inf') if len(costs) < 2 else costs[1] - costs[0]
                shape = self.shapes[key[0]]
                return (value, shape.volume, key)

            chosen = max(pending, key=regret)
            pose = options[chosen[0]][0]
            pose = Placement(chosen[0], chosen[1], pose.rotation_id, pose.position)
            result.append(pose)
            pending.remove(chosen)
            self.quality_stats['regret_insertions'] += 1
            self.retain(result)
        return finish(result, True) if not pending else finish(placements, interrupted=True)

    @staticmethod
    def _overlap_cost(low, extent, other_low, other_extent, gap):
        """Squared displacement surrogate; never a feasibility certificate."""
        accelerated = native.overlap_cost(low, extent, other_low, other_extent, gap)
        if accelerated is not None: return accelerated
        high = low[:, None, :] + extent[:, None, :]
        other_high = other_low + other_extent
        sep = np.maximum(np.maximum(other_low - high, low[:, None, :] - other_high), 0)
        distance = np.linalg.norm(sep, axis=2)
        overlap = np.minimum(high, other_high) - np.maximum(low[:, None, :], other_low)
        penetration = np.minimum(high - other_low, other_high - low[:, None, :]) + gap[:, :, None]
        depth = np.maximum(0., np.min(penetration, axis=2))
        return np.where(np.all(overlap > 0, axis=2), depth ** 2,
                        np.maximum(gap - distance, 0.) ** 2)

    def _verified_checkpoint(self, proposal, height_cap):
        """The only route from an overlapping local state to an incumbent."""
        checked = []
        if (len(proposal) != self.requested
                or {(p.model_id, p.copy_index) for p in proposal} != set(self.keys)):
            return False
        for p in proposal:
            position = np.asarray(p.position, dtype=float)
            if (position.shape != (3,) or not np.isfinite(position).all()
                    or not isinstance(p.rotation_id, Integral) or isinstance(p.rotation_id, bool)
                    or p.rotation_id not in self.shapes[p.model_id].orientations):
                return False
            if (not self.budget.available() or p.position[2] + self.extent(p)[2] > height_cap
                    or not self.can_place(p, checked)):
                return False
            checked.append(p)
        if not self.budget.available():
            return False
        self.retain(proposal)
        return True

    def _relax_height(self, placements):
        """Contract translations, then repair weighted overlaps on local copies.

        Inspired by shrink/repair strip-packing methods. This bounded discrete
        coordinate search is not the voxel method or a reproduction of sparrow.
        Meshes never scale; only local positions and allowed rotation IDs vary.
        """
        if len(placements) != self.requested or len(placements) < 2:
            return placements
        # Global relaxation is a bounded small-layout operator. Larger layouts
        # retain ALNS/compaction; do not allocate quadratic all-layout candidates.
        if len(placements) > 64:
            self.quality_stats['overlap_skipped_large'] += 1
            return placements
        height = self.height(placements)
        reduction = (.06, .03, .015)[self.quality_stats['overlap_attempts'] % 3]
        target = height - max(height * reduction, self.settings.search_step * .05)
        margins = np.asarray([self.margins[p.model_id] for p in placements])
        extents = np.asarray([self.extent(p) for p in placements])
        if np.any(extents[:, 2] + margins[:, 2] > target):
            return placements
        self.quality_stats['overlap_attempts'] += 1
        local = list(placements)
        low = np.asarray([p.position for p in local], dtype=float)
        for i, p in enumerate(local):
            # Compress only translations. Each part keeps its exact mesh size.
            low[i, 2] = min(low[i, 2], target - extents[i, 2])
            span = height - margins[i, 2] - extents[i, 2]
            if span > 0:
                low[i, 2] = margins[i, 2] + (p.position[2] - margins[i, 2]) * max(
                    0., target - margins[i, 2] - extents[i, 2]) / span
            local[i] = Placement(p.model_id, p.copy_index, p.rotation_id, tuple(map(float, low[i])))
        errors = np.asarray([self.shapes[p.model_id].error for p in local])
        gaps = self.settings.gap + errors[:, None] + errors[None, :]
        gaps = np.maximum(gaps, 2 * self.settings.tolerance)
        weights = np.ones((len(local), len(local)))
        np.fill_diagonal(weights, 0.)
        boxes = np.asarray([self.shapes[p.model_id].box for p in local])
        if self.settings.mode == 'aabb':
            boxes[:] = True
        visited = [[] for _ in local]
        for step in range(256):
            if not self.budget.available():
                return placements
            costs = self._overlap_cost(low, extents, low, extents, gaps)
            np.fill_diagonal(costs, 0.)
            # Valid concave interlocks are not penalized merely because their
            # AABBs intersect. Reuse only exact, ordered mesh checks here.
            if not boxes.all():
                for i, j in zip(*np.nonzero(np.triu(costs > 0, 1))):
                    if not self.budget.available():
                        return placements
                    if not (boxes[i] and boxes[j]) and self._pair_valid(local[i], local[j]):
                        costs[i, j] = costs[j, i] = 0.
            if float(np.max(costs)) <= (self.settings.tolerance * .5) ** 2:
                if self._verified_checkpoint(local, target):
                    self.quality_stats['overlap_successes'] += 1
                    self.quality_stats['overlap_height_gain_mm'] += float(height - self.height(local))
                    return local
                return placements
            weighted = costs * weights
            burdens = weighted.sum(axis=1)
            if step % 8 == 7:
                weights += (costs > 0) * .5
            index = int(np.argmax(burdens)) if step % 3 else self.rng.choices(
                range(len(local)), weights=burdens.tolist())[0]
            p = local[index]
            shape = self.shapes[p.model_id]
            rotations = list(shape.orientations)
            self.rng.shuffle(rotations)
            candidates, candidate_extents, rotation_ids = [], [], []
            for rid in rotations:
                extent = shape.orientations[rid].extent
                upper = np.asarray(self.settings.size) - margins[index] - extent
                upper[2] = min(upper[2], target - extent[2])
                if np.any(upper < margins[index]):
                    continue
                center = low[index] + extents[index] * .5 - extent * .5
                position = np.clip(center, margins[index], upper)
                points = [position[None, :], low[index][None, :],
                          np.asarray([(x, y, z) for x in (margins[index, 0], upper[0])
                                      for y in (margins[index, 1], upper[1])
                                      for z in (margins[index, 2], upper[2])])]
                for axis in range(3):
                    values = np.r_[low[:, axis] - extent[axis] - gaps[index],
                                   low[:, axis] + extents[:, axis] + gaps[index]]
                    contacts = np.broadcast_to(position, (len(values), 3)).copy()
                    contacts[:, axis] = values
                    points.append(contacts)
                points.append(np.asarray([[self.rng.uniform(margins[index, a], float(upper[a]))
                                           for a in range(3)] for _ in range(4)]))
                array = np.clip(np.concatenate(points), margins[index], upper)
                candidates.append(array)
                candidate_extents.append(np.broadcast_to(extent, array.shape))
                rotation_ids.extend([rid] * len(array))
            if not candidates:
                return placements
            candidates = np.concatenate(candidates)
            sizes = np.concatenate(candidate_extents)
            penalties = np.empty((len(candidates), len(local)))
            for start in range(0, len(candidates), 128):
                if not self.budget.available():
                    return placements
                stop = min(start + 128, len(candidates))
                candidate_gaps = np.broadcast_to(gaps[index], (stop - start, len(local)))
                penalties[start:stop] = self._overlap_cost(
                    candidates[start:stop], sizes[start:stop], low, extents, candidate_gaps)
            penalties[:, index] = 0.
            scores = (penalties * weights[index]).sum(axis=1)
            # Soft tabu penalties encourage a different coordinate arrangement
            # when pairwise descent jams. They change proposal ranking only;
            # every rotation ID stays in the candidate pool.
            for position in visited[index]:
                repeated = np.all(np.abs(candidates - position) <= 4 * self.settings.tolerance, axis=1)
                scores[repeated] += max(1e-4, float(np.min(extents[index])) ** 2 * .05)
            ranking = np.argsort(scores, kind='stable')
            # On concave models the surrogate only orders a bounded shortlist;
            # the chosen move is scored with actual pair feasibility as well.
            shortlist = ranking[:8] if not boxes.all() else ranking[:1]
            best = None
            for candidate_index in shortlist:
                if not self.budget.available():
                    return placements
                pose = Placement(p.model_id, p.copy_index, rotation_ids[candidate_index],
                                 tuple(map(float, candidates[candidate_index])))
                score = float(scores[candidate_index])
                if not boxes.all():
                    row = penalties[candidate_index].copy()
                    for j in np.flatnonzero(row > 0):
                        if not self.budget.available():
                            return placements
                        if not (boxes[index] and boxes[j]) and self._pair_valid(pose, local[j]):
                            row[j] = 0.
                    score = float(row @ weights[index]) + float(
                        scores[candidate_index] - penalties[candidate_index] @ weights[index])
                if best is None or score < best[0]:
                    best = (score, int(candidate_index), pose)
            if best is None:
                return placements
            _, chosen, pose = best
            visited[index].append(low[index].copy())
            visited[index] = visited[index][-8:]
            local[index] = pose
            low[index] = candidates[chosen]
            extents[index] = sizes[chosen]
            self.quality_stats['overlap_moves'] += 1
        return placements

    def build(self, order, base=(), preferred=None, deep=False):
        placements = list(base)
        for key in order:
            if not self.budget.available():
                break
            p = self.find_pose(key, placements, (preferred or {}).get(key), deep=deep)
            if p is not None:
                placements.append(p)
                self.retain(placements)
            self.emit()
        return placements

    def _cuboid_move(self, step, current):
        """Keep inexpensive, diverse legacy neighborhoods for exact cuboids."""
        if step == 0:
            order = sorted(self.keys, key=lambda k: (
                -max(next(iter(self.shapes[k[0]].orientations.values())).extent),
                -self.shapes[k[0]].volume, k))
            return self.build(order)
        if step == 1 or step % 9 == 0:
            order = list(self.keys)
            self.rng.shuffle(order)
            preferred = {k: self.rng.choice(list(self.shapes[k[0]].orientations)) for k in order}
            return self.build(order, preferred=preferred, deep=(step % 9 == 0))
        if step % 3 == 0 and current:
            removed = self.rng.choice(current)
            base = [p for p in current if p is not removed]
            rid = self.rng.choice(list(self.shapes[removed.model_id].orientations))
            delta = self.settings.search_step / (1 + (step // 20) % 4)
            extras = [tuple(np.asarray(removed.position) + delta * np.asarray(d))
                      for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                                (0, 0, 1), (0, 0, -1))]
            p = self.find_pose((removed.model_id, removed.copy_index), base,
                               only_rotation=rid, deep=(step % 6 == 0), extras=extras)
            return base + ([p] if p is not None else [])
        remove_count = max(1, int(len(current) * self.rng.uniform(.12, .35)))
        removed = self.rng.sample(current, min(remove_count, len(current)))
        selected = {(p.model_id, p.copy_index) for p in removed}
        base = [p for p in current if (p.model_id, p.copy_index) not in selected]
        retained = {(p.model_id, p.copy_index) for p in base}
        order = [k for k in self.keys if k not in retained]
        self.rng.shuffle(order)
        preferred = {k: self.rng.choice(list(self.shapes[k[0]].orientations)) for k in order}
        return self.build(order, base, preferred, deep=(step % 7 == 0))

    def _heuristic_move(self, step, current, cuboids, height_order):
        # The disabled-learning path keeps the original schedule and RNG calls.
        if cuboids:
            action = 'compact' if step % 16 == 15 else 'legacy'
            proposal = (self._compact(self.best) if step % 16 == 15
                        else self._cuboid_move(step, current))
        elif step == 0:
            action = 'height_restart'
            proposal = self.build(height_order)
        elif step % 6 == 0 and current:
            action = 'relax'
            proposal = self._relax_height(self.best)
        elif step % 6 in (2, 4) and current:
            operator = self.rng.choices(list(self.operator_weights),
                                        weights=list(self.operator_weights.values()))[0]
            action = operator
            proposal = self._destroy_repair(self.best, operator)
        elif step % 6 == 1 and current:
            action = 'compact'
            proposal = self._compact(self.best)
        elif step % 6 == 3:
            action = 'legacy'
            order = sorted(self.keys, key=lambda k: (
                -max(next(iter(self.shapes[k[0]].orientations.values())).extent),
                -self.shapes[k[0]].volume, k))
            proposal = self.build(order)
        elif step % 12 == 5:
            action = 'random_restart'
            order = list(self.keys)
            self.rng.shuffle(order)
            preferred = {k: self.rng.choice(list(self.shapes[k[0]].orientations)) for k in order}
            proposal = self.build(order, preferred=preferred)
        else:
            action = 'legacy'
            remove_count = max(1, int(len(current) * self.rng.uniform(.12, .35)))
            removed = self.rng.sample(current, min(remove_count, len(current)))
            selected = {(p.model_id, p.copy_index) for p in removed}
            base = [p for p in current if (p.model_id, p.copy_index) not in selected]
            retained = {(p.model_id, p.copy_index) for p in base}
            order = [k for k in self.keys if k not in retained]
            self.rng.shuffle(order)
            preferred = {k: self.rng.choice(list(self.shapes[k[0]].orientations)) for k in order}
            proposal = self.build(order, base, preferred, deep=(step % 7 == 0))
        return action, proposal

    def _learning_state(self, current):
        height = max(1., self.settings.size[2])
        total_volume = sum(self.shapes[k[0]].volume for k in self.keys)
        phase = (self.iterations / max(1, self.settings.iterations)
                 if self.settings.iterations is not None else
                 (time.perf_counter() - self.budget.start) / max(1e-9, self.settings.time_limit))
        state = [1., len(current) / max(1, self.requested), len(self.best) / max(1, self.requested),
                 self.height(current) / height, self.height(self.best) / height,
                 total_volume / max(1., math.prod(self.settings.size)),
                 sum(not self.shapes[k[0]].box for k in self.keys) / max(1, self.requested),
                 len(self.shapes) / max(1, self.requested), min(1., self.requested / 100),
                 min(1., self.stagnation / 10), phase,
                 self.settings.gap / max(1., min(self.settings.size)),
                 float(len(self.best) == self.requested)]
        return np.clip(state, 0., 1.)

    def _available_actions(self):
        from .rl import ACTIONS
        return [i for i, name in enumerate(ACTIONS)
                if name != 'relax' or len(self.best) == self.requested and 2 <= self.requested <= 64]

    def _policy_move(self, action, step, current, height_order):
        if action == 'compact':
            return self._compact(self.best)
        if action == 'relax':
            return self._relax_height(self.best)
        if action in ('top', 'cluster', 'random'):
            return self._destroy_repair(self.best, action)
        if action == 'height_restart':
            return self.build(height_order)
        if action == 'random_restart':
            order = list(self.keys)
            self.rng.shuffle(order)
            preferred = {k: self.rng.choice(list(self.shapes[k[0]].orientations)) for k in order}
            return self.build(order, preferred=preferred)
        cuboids = all(self.shapes[k[0]].box for k in self.keys)
        return self._heuristic_move(step, current, cuboids, height_order)[1]

    def _finish_policy_stage(self):
        self.rl_checkpoint = list(self.best)
        self.stage_report['reinforcement'] = {
            'placed': len(self.best), 'height_mm': self.height(self.best),
            'objective': self.score(self.best), 'iterations': self.iterations,
            'policy_steps': self.learning_stats['policy_steps'],
            'seed_only': self.learning_stats['policy_steps'] == 0,
            'placements': [asdict(p) for p in self.best],
            'elapsed_seconds': time.perf_counter() - self.budget.start}

    def _finish_staged(self):
        if self.rl_checkpoint is None:
            self._finish_policy_stage()
        self.stage_report['algorithm'] = {
            'placed': len(self.best), 'height_mm': self.height(self.best),
            'objective': self.score(self.best),
            'iterations': self.iterations - self.stage_report['reinforcement']['iterations']}
        self.stage_report['selected'] = ('algorithm' if self.score(self.best) < self.score(self.rl_checkpoint)
                                         else 'reinforcement')

    def _height_bound_reached(self):
        rounding = 32 * np.finfo(float).eps * max(1., abs(self.height_lower_bound))
        return (self.settings.early_stop and not self.learn_updates and self.settings.iterations is None and self.requested > 0
                and len(self.best) == self.requested
                and self.height(self.best) <= self.height_lower_bound + self.settings.tolerance + rounding)

    def run(self):
        volume_order = sorted(self.keys,
                              key=lambda k: (-self.shapes[k[0]].volume,
                                             -max(next(iter(self.shapes[k[0]].orientations.values())).extent), k))
        # Keep the fast volume greedy as a feasible floor. Decreasing minimum
        # attainable height supplies a different layer-packing restart order.
        height_order = sorted(self.keys, key=lambda k: (
            -min(o.extent[2] for o in self.shapes[k[0]].orientations.values()),
            -self.shapes[k[0]].volume, k))
        cuboids = all(self.shapes[k[0]].box for k in self.keys)
        staged = self.policy is not None and not self.learn_updates
        if staged:
            self.stage_report = {'strategy': 'policy_then_algorithm',
                                 'comparison': 'maximum placed count, then minimum height',
                                 'policy_kind': 'learned operator selection over geometric search'}
        self.initial_strategy = 'legacy_volume'
        current = self.build(volume_order) if self.starting is None else list(self.starting)
        if self.starting is not None:
            self.initial_strategy = 'validated_training_project'
        self.initial = list(current)
        self.initial_score = self.score(current)
        self.retain(current)
        self.emit(force=True)
        policy_deadline = time.perf_counter() + max(0., self.budget.deadline - time.perf_counter()) * .5
        policy_moves = ((self.settings.iterations + 1) // 2
                        if self.settings.iterations is not None else None)
        if self.certificate or all(k[0] in self.impossible for k in self.keys):
            # Preserve a useful partial construction, then report the already
            # established necessary condition rather than run futile restarts.
            if self.budget.reason is None:
                self.budget.reason = 'necessary_bounds'
            if staged:
                self._finish_staged()
            return self.best
        while self.budget.available():
            if self._height_bound_reached():
                self.budget.reason = 'height_bound'
                break
            if self.settings.iterations is not None and self.iterations >= self.settings.iterations:
                break
            step = self.iterations
            learning_state = self._learning_state(current) if self.policy is not None else None
            before = self.best_score
            policy_turn = (staged and self.rl_checkpoint is None and time.perf_counter() < policy_deadline
                           and (policy_moves is None or step < policy_moves))
            if staged and not policy_turn and self.rl_checkpoint is None:
                self._finish_policy_stage()
                current = list(self.best)
                self.emit(force=True, status='algorithm_refinement')
            if policy_turn or not staged and self.policy is not None and step % 2 == 1:
                from .rl import ACTIONS
                action_index = self.policy.choose(learning_state, self.rl_rng, self._available_actions(),
                                                  epsilon=.25 if self.learn_updates else .05)
                action = ACTIONS[action_index]
                if staged:
                    # Operators check this same budget internally. A long
                    # policy repair must leave the second stage its own time.
                    overall_deadline = self.budget.deadline
                    try:
                        self.budget.deadline = min(overall_deadline, policy_deadline)
                        proposal = self._policy_move(action, step, current, height_order)
                    finally:
                        self.budget.deadline = overall_deadline
                        if self.budget.reason == 'time_limit' and time.perf_counter() < overall_deadline:
                            self.budget.reason = None
                else:
                    proposal = self._policy_move(action, step, current, height_order)
                self.learning_stats['policy_steps'] += 1
            else:
                # The heuristic lane has its own clock: 0,1,2,..., not
                # 0,2,4,... . Otherwise all odd restart/compaction moves vanish.
                heuristic_step = (step - self.stage_report['reinforcement']['iterations'] if staged else
                                  step // 2 if self.policy is not None else step)
                action, proposal = self._heuristic_move(heuristic_step, current, cuboids, height_order)
                if self.policy is not None:
                    self.learning_stats['heuristic_steps'] += 1
            self.iterations += 1
            self.retain(proposal)
            old, new = self.score(current), self.score(proposal)
            penalty = max(self.settings.size[2], 1.0) * 2
            delta = (new[0] - old[0]) * penalty + new[1] - old[1]
            if self.settings.iterations is not None:
                fraction = self.iterations / max(self.settings.iterations, 1)
            else:
                fraction = (time.perf_counter() - self.budget.start) / max(self.settings.time_limit, 1e-9)
            temperature = max(1e-9, self.settings.size[2] * .12 * (1 - min(fraction, .999)) ** 2)
            if delta <= 0 or self.rng.random() < math.exp(-min(delta / temperature, 745.0)):
                current = proposal
            if self.policy is not None:
                from .rl import ACTIONS, reward
                gained = reward(before, self.best_score, self.requested, self.settings.size[2])
                self.stagnation = 0 if self.best_score < before else self.stagnation + 1
                self.learning_stats['reward'] += gained
                record = self.learning_stats['actions'].setdefault(action, {'attempts': 0, 'reward': 0., 'improvements': 0})
                record['attempts'] += 1
                record['reward'] += gained
                record['improvements'] += int(self.best_score < before)
                if self.learn_updates:
                    terminal = ((self.settings.iterations is not None and self.iterations >= self.settings.iterations)
                                or not self.budget.available())
                    self.policy.update(learning_state, ACTIONS.index(action), gained,
                                       self._learning_state(current), terminal=terminal,
                                       available=self._available_actions())
                    self.learning_stats['td_updates'] += 1
            self.emit()
        if staged:
            self._finish_staged()
        return self.best


def optimize(models: dict[str, Model], items: list[RequestItem], settings: Settings,
             cancel: threading.Event | None = None,
             progress: Callable[[dict], None] | None = None, *,
             policy=None, learn: bool = False, initial_placements=None) -> Result:
    """Return the best independently verified lexicographic layout.

    ``iterations`` fixes improvement moves after one greedy construction. With
    a sufficient time ceiling, identical inputs and seed reproduce the layout.
    Cancellation/time limit can interrupt construction and preserve the incumbent.
    Status describes termination; metadata ``complete`` distinguishes full/partial.
    """
    from .geometry import revalidate_model
    from .validation import validate

    if not isinstance(settings, Settings):
        raise TypeError('settings must be Settings')
    settings = Settings(**asdict(settings))
    if isinstance(settings.iterations, bool):
        raise ValueError('iterations must be a nonnegative integer, excluding booleans')
    if not isinstance(settings.seed, Integral) or isinstance(settings.seed, bool):
        raise ValueError('seed must be an integer')
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, RequestItem):
            raise TypeError('items must be RequestItem records')
        if (not isinstance(item.quantity, Integral) or isinstance(item.quantity, bool)
                or item.quantity < 0):
            raise ValueError('quantities must be nonnegative integers')
        if item.model_id not in models:
            raise ValueError(f'unknown model: {item.model_id}')
        counts[item.model_id] = counts.get(item.model_id, 0) + int(item.quantity)
    for key, model in models.items():
        if key != model.model_id:
            raise ValueError('model dictionary keys must equal model_id')
    requests = [RequestItem(key, count) for key, count in counts.items()]
    overall_start = time.perf_counter()
    native_before = native.status()['kernels']
    budget = _Budget(settings.time_limit, cancel)
    # Raw Model records and mutable imported meshes must pass solid validation
    # before they can support fit/volume certificates or collision decisions.
    checked_models = {key: revalidate_model(model, model_id=key)
                      for key, model in models.items()}
    search_class = _Search
    if settings.precision:
        from .precision import PrecisionSearch
        search_class = PrecisionSearch
    search = search_class(checked_models, requests, settings, budget, progress)
    if initial_placements is not None:
        try:
            initial_report = validate(models, requests, settings, initial_placements)
        except BaseException:
            search.engine.close()
            raise
        if not initial_report.get('valid', False):
            search.engine.close()
            raise ValueError('학습 시작 배치 검증 실패: ' + '; '.join(initial_report.get('errors', [])))
        search.starting = list(initial_placements)
    if settings.learning:
        from .rl import default_policy, OperatorPolicy
        if policy is not None and not isinstance(policy, OperatorPolicy):
            search.engine.close()
            raise TypeError('policy must be an OperatorPolicy')
        search.policy = default_policy() if policy is None else policy
        search.learn_updates = bool(learn)
        search.learning_stats.update(enabled=True, policy_before=search.policy.digest,
                                     requested=True, checkpoint_updates=search.policy.updates, alternating=bool(learn))
        context = search._learning_state([])
        if learn:
            search.policy.observe_context(context)
        else:
            supported, distance = search.policy.supports(context)
            search.learning_stats.update(context_distance=distance, context_supported=supported)
            if not supported:
                search.learning_stats.update(enabled=False, fallback='outside_training_context')
                search.policy = None
    # A complex but valid mesh may need longer to validate than the requested
    # packing time. Input/starting-layout checks must not exhaust the search
    # allowance before the first placement is even attempted.
    budget = _Budget(settings.time_limit, cancel)
    search.budget = budget
    preparation_elapsed = budget.start-overall_start
    try:
        best = search.run()
    finally:
        search.engine.close()
    search_elapsed = time.perf_counter() - budget.start
    reason = budget.reason or ('iterations' if settings.iterations is not None else 'finished')

    balance_checkpoint, balanced_proposal = None, None
    balance_report = {}
    if settings.xy_balance != 'off' and not learn:
        from .balance import balance_xy
        balance_checkpoint = list(best)
        search.emit(force=True, status='xy_balancing')
        try:
            best, balance_report = balance_xy(checked_models, settings, best, engine=search.engine,
                                             cancel=cancel, progress=progress)
            # The postprocessor cannot trade inventory, poses or Z positions
            # for symmetry, even if a future implementation makes a mistake.
            signature = lambda layout: sorted((p.model_id, p.copy_index, p.rotation_id, p.position[2])
                                               for p in layout)
            if signature(best) != signature(balance_checkpoint):
                raise ValueError('XY adjustment changed inventory, a rotation or a Z coordinate')
            balanced_proposal = list(best)
        except Exception as exc:
            best = list(balance_checkpoint)
            balance_report = {'enabled': True, 'mode': settings.xy_balance, 'applied': False,
                              'fallback': str(exc), 'final_validation_passed': False}
        finally:
            search.engine.close()
    elif settings.xy_balance != 'off':
        balance_report = {'enabled': False, 'skipped': 'training_episode'}

    # Fresh independent count/transform/wall/material/gap checks are mandatory,
    # outside the search deadline. A geometry failure is never exported as valid.
    verification_started = time.perf_counter()
    search.emit(force=True, status='validating')
    validation = validate(models, requests, settings, best)
    validation_recovery = False
    if not validation.get('valid') and balance_checkpoint is not None and list(best) != balance_checkpoint:
        balance_report['fallback'] = 'postprocessing_failed_independent_validation'
        balance_report['rejected_validation'] = validation
        best = list(balance_checkpoint)
        validation = validate(models, requests, settings, best)
        validation_recovery = True
    precision_checkpoint = getattr(search, 'precision_checkpoint', None)
    if precision_checkpoint is not None:
        standard_validation = (validation if list(best) == precision_checkpoint else
                               validate(models, requests, settings, precision_checkpoint))
        search.precision_stats['standard_validation'] = standard_validation
        if standard_validation.get('valid') and (
                not validation.get('valid') or search.score(precision_checkpoint) < search.score(best)):
            best, validation = list(precision_checkpoint), standard_validation
            validation_recovery = True
            search.precision_stats['selected'] = 'standard_checkpoint'
            if search.stage_report:
                search.stage_report['selected'] = 'standard_checkpoint'
    checkpoint = search.rl_checkpoint
    if checkpoint is not None:
        search.stage_report['algorithm']['validation'] = (standard_validation if precision_checkpoint is not None else validation)
        checkpoint_validation = validate(models, requests, settings, checkpoint)
        search.stage_report['reinforcement']['validation'] = checkpoint_validation
        if checkpoint_validation.get('valid') and (
                not validation.get('valid') or search.score(checkpoint) < search.score(best)):
            best, validation = list(checkpoint), checkpoint_validation
            validation_recovery = True
            search.stage_report['selected'] = 'reinforcement'
            search.stage_report['algorithm_rejected_by_final_verification'] = True
        elif not checkpoint_validation.get('valid'):
            search.stage_report['reinforcement']['rejected_by_final_verification'] = True
            search.stage_report['selected'] = 'algorithm' if validation.get('valid') else 'none'
    if not validation.get('valid', False):
        validation_recovery = True
        baseline_validation = validate(models, requests, settings, search.initial)
        if baseline_validation.get('valid', False):
            best, validation = search.initial, baseline_validation
            if search.stage_report:
                search.stage_report['selected'] = 'initial'
        else:
            best = []
            validation = validate(models, requests, settings, best)
            if search.stage_report:
                search.stage_report['selected'] = 'empty'
    if balance_report.get('enabled'):
        balance_report['applied'] = balanced_proposal is not None and list(best) == balanced_proposal
        balance_report['final_validation_passed'] = bool(validation.get('valid'))
        if not balance_report['applied']:
            if list(best) == balance_checkpoint and 'before' in balance_report:
                balance_report['after'] = balance_report['before']
            else:
                try:
                    from .balance import XYBalancer
                    scorer = XYBalancer(checked_models, settings, best, engine=search.engine)
                    points = np.asarray([p.position[:2] for p in best], dtype=float).reshape((-1, 2))
                    balance_report['after'] = scorer.metrics(points)
                except Exception as exc:
                    balance_report.pop('after', None)
                    balance_report['metrics_error'] = str(exc)
            balance_report['changed'] = False
            balance_report.setdefault('fallback', 'search_checkpoint_selected_by_final_verification')
    placed_counts: dict[str, int] = {}
    if precision_checkpoint is not None:
        search.precision_stats['final_objective'] = search.score(best)
    for p in best:
        placed_counts[p.model_id] = placed_counts.get(p.model_id, 0) + 1
    unplaced = {key: count - placed_counts.get(key, 0) for key, count in counts.items()
                if count > placed_counts.get(key, 0)}
    valid = bool(validation.get('valid', False))
    complete = valid and not unplaced
    if cancel is not None and cancel.is_set():
        reason = 'cancelled'
    if not valid:
        status = 'validation_failed'
    elif reason == 'cancelled':
        status = 'cancelled'
    elif search.certificate:
        status = 'proven_infeasible'
    elif reason == 'time_limit':
        status = 'time_limit'
    else:
        status = 'best_found' if best or complete else 'not_found'
    feasibility = ('unknown' if not valid else 'feasible' if complete else 'proven_infeasible' if search.certificate
                   else 'partial' if best else 'unknown')
    native_report = native.status()
    native_report['kernels'] = {key: value - native_before.get(key, 0)
                                for key, value in native_report['kernels'].items()
                                if value != native_before.get(key, 0)}
    result = Result(
        placements=list(best), unplaced=unplaced, status=status,
        elapsed=time.perf_counter() - overall_start, height=float(search.height(best)),
        seed=int(settings.seed), iterations=search.iterations,
        initial_score=search.initial_score, validation=validation,
        metadata={'requested': search.requested, 'placed': len(best), 'complete': complete,
                  'objective': search.score(best), 'termination': reason,
                  'native_backend': native_report,
                  'feasibility': feasibility, 'optimality_proven': False,
                  'height_lower_bound_mm': max(0., search.height_lower_bound),
                  'early_stop_at_height_bound': reason == 'height_bound',
                  'infeasibility_certificate': search.certificate,
                  'conservative_envelope_blocked_models': sorted(search.impossible),
                  'search_elapsed': search_elapsed,
                  'preparation_elapsed': preparation_elapsed,
                  'input_preparation_outside_search_budget': True,
                  'verification_elapsed': time.perf_counter() - verification_started,
                  'verification_outside_search_budget': True,
                  'candidate_checks': search.candidate_checks,
                  'quality_search': dict(search.quality_stats),
                  'initial_strategy': search.initial_strategy,
                  'reinforcement_learning': dict(search.learning_stats),
                  'staged_search': dict(search.stage_report),
                  'precision_search': dict(getattr(search, 'precision_stats', {})),
                  'xy_balance': balance_report,
                  'destroy_operator_weights': dict(search.operator_weights),
                  'destroy_operators': {key: dict(value) for key, value in search.operator_stats.items()},
                  'callback_errors': search.callback_errors,
                  'validation_recovery': validation_recovery,
                  'mode': settings.mode,
                  'rotation_policy': 'all allowed rotation identities; no AABB deduplication',
                  'deterministic_if_budget_sufficient': settings.iterations is not None,
                  'limitations': ['Finite extreme-point/contact/grid candidates; narrow cavities may be missed.',
                                  'Seeded local search gives no global optimality certificate.',
                                  'AABB mode is conservative; final mesh verification is mandatory.',
                                  'Geometry claims apply to tessellated meshes and recorded CAD error margins.']})
    search.best, search.best_score = list(best), search.score(best)
    search.emit(force=True, status=status)
    return result
