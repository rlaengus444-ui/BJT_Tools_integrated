"""Optional XY postprocessing; poses, Z coordinates and inventory stay fixed.

Symmetry is a proxy over material centroids, volume weights and one-to-one
matching of copies of each model. It is not a claim that irregular surfaces
become mirror images. Reflections generate target *positions*, never geometry.
Only the common translation and collision-checked individual XY moves survive;
the caller must independently validate the returned layout before using it.
"""
from __future__ import annotations

from dataclasses import replace
import time

import numpy as np
from scipy.optimize import linear_sum_assignment
from trimesh.triangles import mass_properties

from .cancellation import check_cancelled
from .geometry import CollisionEngine
from .rotations import ROTATIONS


class XYBalancer:
    SIGNS = ((-1., 1.), (1., -1.), (-1., -1.))

    def __init__(self, models, settings, placements, *, engine=None, cancel=None, progress=None):
        self.started = time.perf_counter()
        self.models, self.settings = models, settings
        self.engine = engine or CollisionEngine(models, settings)
        self.owns_engine = engine is None
        self.cancel, self.progress = cancel, progress
        self.center = np.asarray(settings.size[:2], dtype=float) / 2
        self.offsets, self.extents, self.errors, self.weights = [], [], [], []
        self.groups = {}
        properties, oriented = {}, {}
        for i, p in enumerate(placements):
            check_cancelled()
            model = models[p.model_id]
            if p.model_id not in properties:
                vertices = np.asarray(model.mesh.vertices)
                origin = vertices.min(axis=0)
                # Work near zero so imported CAD origins do not spoil moments.
                props = mass_properties(vertices[np.asarray(model.mesh.faces)] - origin, skip_inertia=True)
                properties[p.model_id] = (props['center_mass'] + origin, abs(float(props['volume'])))
            centroid, volume = properties[p.model_id]
            bounds = self.engine.bounds(p)
            key = (p.model_id, p.rotation_id)
            if key not in oriented:
                oriented[key] = model.bounds_for(p.rotation_id)
            original = oriented[key]
            self.offsets.append((ROTATIONS[p.rotation_id] @ centroid - original[0])[:2])
            self.extents.append((bounds[1] - bounds[0])[:2])
            self.errors.append(float(model.tessellation_error))
            self.weights.append(volume)
            self.groups.setdefault(p.model_id, []).append(i)
        self.offsets = np.asarray(self.offsets).reshape((-1, 2))
        self.extents = np.asarray(self.extents).reshape((-1, 2))
        self.errors = np.asarray(self.errors)
        self.weights = np.asarray(self.weights, dtype=float)
        if len(self.weights):
            self.weights /= self.weights.sum()
        self.groups = [np.asarray(indices) for indices in self.groups.values()]
        self.attempts = self.accepted = self.callback_errors = 0
        self.reason = 'finished'

    def envelope(self, xy):
        return np.array([(xy - self.errors[:, None]).min(axis=0),
                         (xy + self.extents + self.errors[:, None]).max(axis=0)])

    def centered(self, xy):
        bounds = self.envelope(xy)
        return xy + self.center - bounds.mean(axis=0)

    def metrics(self, xy, *, assignments=False):
        if not len(xy):
            return {'score_mm': 0., 'envelope_center_offset_mm': 0., 'volume_center_offset_mm': 0.,
                    'x_reflection_rms_mm': 0., 'y_reflection_rms_mm': 0., 'point_reflection_rms_mm': 0.}
        relative = xy + self.offsets - self.center
        mass_offset = float(np.linalg.norm(self.weights @ relative))
        envelope_offset = float(np.linalg.norm(self.envelope(xy).mean(axis=0) - self.center))
        distances, matches = [], []
        for signs in self.SIGNS:
            squared = 0.
            mapping = np.arange(len(xy))
            for group in self.groups:
                points = relative[group]
                cost = ((points[:, None, :] - points[None, :, :] * signs) ** 2).sum(axis=2)
                rows, columns = linear_sum_assignment(cost)
                squared += float(np.dot(self.weights[group[rows]], cost[rows, columns]))
                mapping[group[rows]] = group[columns]
            distances.append(float(np.sqrt(max(0., squared))))
            matches.append(mapping)
        # A common translation by d can worsen the last two terms by at most
        # 3*|d|. Weight 4 ensures that centering the envelope improves the score.
        result = {'score_mm': 4 * envelope_offset + mass_offset + sum(distances) / 3,
                  'envelope_center_offset_mm': envelope_offset, 'volume_center_offset_mm': mass_offset,
                  'x_reflection_rms_mm': distances[0], 'y_reflection_rms_mm': distances[1],
                  'point_reflection_rms_mm': distances[2]}
        return (result, matches) if assignments else result

    def available(self):
        check_cancelled()
        if self.cancel is not None and self.cancel.is_set():
            self.reason = 'cancelled'
            return False
        if time.perf_counter() >= self.deadline:
            self.reason = 'time_limit'
            return False
        if self.attempts >= self.max_attempts:
            self.reason = 'move_limit'
            return False
        return True

    def emit(self, info):
        if self.progress is not None:
            try:
                self.progress({'status': 'xy_balancing', 'stage': 'xy_balance', **info})
            except Exception:
                self.callback_errors += 1

    @staticmethod
    def placements_at(placements, xy):
        return [replace(p, position=(float(point[0]), float(point[1]), p.position[2]))
                for p, point in zip(placements, xy)]

    def can_place(self, p, existing):
        if self.settings.mode == 'aabb':
            a = self.engine.bounds(p)
            for q in existing:
                b = self.engine.bounds(q)
                separation = np.maximum(0., np.maximum(a[0] - b[1], b[0] - a[1]))
                required = (self.settings.gap + self.models[p.model_id].tessellation_error
                            + self.models[q.model_id].tessellation_error)
                if (np.all(np.minimum(a[1], b[1]) - np.maximum(a[0], b[0]) > 0)
                        or np.linalg.norm(separation) < required - self.settings.tolerance):
                    return False
        return self.engine.can_place(p, existing)

    def try_move(self, placements, xy, current, changes, footprint):
        if not self.available():
            return xy, current
        self.attempts += 1
        trial = xy.copy()
        changed = []
        for i, target in changes.items():
            if np.linalg.norm(trial[i] - target) > 1e-9:
                trial[i] = target
                changed.append(i)
        if not changed or not np.isfinite(trial).all():
            return xy, current
        envelope = self.envelope(trial)
        if np.any(envelope[1] - envelope[0] > footprint + 1e-9):
            return xy, current
        trial = self.centered(trial)
        measured = self.metrics(trial)
        if measured['score_mm'] >= current['score_mm'] - 1e-8:
            return xy, current
        candidate = self.placements_at(placements, trial)
        changed_set = set(changed)
        existing = [p for i, p in enumerate(candidate) if i not in changed_set]
        # Unchanged copies undergo one common translation, so their pairwise
        # geometry is invariant. Check every affected pair with cached real BVHs.
        for i in changed:
            if not self.available() or not self.can_place(candidate[i], existing):
                return xy, current
            existing.append(candidate[i])
        if not all(self.engine.in_bounds(p) for p in candidate):
            return xy, current
        self.accepted += 1
        self.emit({'attempts': self.attempts, 'accepted_moves': self.accepted,
                   'score_mm': measured['score_mm'], 'placed': len(placements)})
        return trial, measured

    def run(self, placements):
        started = self.started
        self.deadline = started + self.settings.xy_balance_time
        self.max_attempts = min(4096, max(512, len(placements) * 32))
        xy = np.asarray([p.position[:2] for p in placements], dtype=float).reshape((-1, 2))
        before = self.metrics(xy)
        report = {'enabled': True, 'mode': self.settings.xy_balance,
                  'criterion': 'volume-weighted material centroids and per-model one-to-one reflected XY matches',
                  'geometry_reflected': False, 'z_and_rotations_preserved': True,
                  'xy_footprint_not_increased': True, 'before': before,
                  'time_limit_seconds': self.settings.xy_balance_time,
                  'global_symmetry_optimality_proven': False}
        if self.cancel is not None and self.cancel.is_set():
            self.reason = 'cancelled'
        elif len(xy):
            centered = self.centered(xy)
            candidate = self.placements_at(placements, centered)
            if all(self.engine.in_bounds(p) for p in candidate):
                # All pairs are unchanged by a common XY translation.
                xy = centered
            else:
                self.reason = 'centering_out_of_bounds'
        current = self.metrics(xy)
        report['centered'] = current
        report['common_translation_xy_mm'] = (xy[0] - np.asarray(placements[0].position[:2])).tolist() if len(xy) else [0., 0.]
        self.emit({'placed': len(placements), 'attempts': 0, 'accepted_moves': 0,
                   'score_mm': current['score_mm']})
        if self.settings.xy_balance == 'symmetric' and len(xy) > 1 and self.reason == 'finished':
            footprint = np.diff(self.envelope(xy), axis=0)[0]
            # Reflection targets coordinate single and paired moves. Pattern
            # steps then escape discretisation without changing any Z or pose.
            for level in range(5):
                if not self.available() or current['score_mm'] < 1e-8:
                    break
                _, matches = self.metrics(xy, assignments=True)
                for signs, mapping in zip(self.SIGNS, matches):
                    for i, j in enumerate(mapping):
                        if not self.available():
                            break
                        centers = xy + self.offsets
                        target = self.center + (centers[j] - self.center) * signs
                        xy, current = self.try_move(placements, xy, current,
                                                    {i: target - self.offsets[i]}, footprint)
                        if i != j and self.available():
                            centers = xy + self.offsets
                            middle = (centers[i] + self.center + (centers[j] - self.center) * signs) / 2
                            paired = self.center + (middle - self.center) * signs
                            xy, current = self.try_move(placements, xy, current,
                                {i: middle - self.offsets[i], int(j): paired - self.offsets[j]}, footprint)
                step = min(float(footprint.max()) / 8, self.settings.search_step) / 2 ** level
                for i in range(len(xy)):
                    for axis in (0, 1):
                        for sign in (-1, 1):
                            if not self.available():
                                break
                            target = xy[i].copy()
                            target[axis] += step * sign
                            xy, current = self.try_move(placements, xy, current, {i: target}, footprint)
        result = self.placements_at(placements, xy)
        report.update(after=current, attempts=self.attempts, accepted_moves=self.accepted,
                      termination=self.reason, elapsed_seconds=time.perf_counter() - started,
                      callback_errors=self.callback_errors, changed=result != list(placements),
                      final_validation_passed=False)
        return result, report


def balance_xy(models, settings, placements, *, engine=None, cancel=None, progress=None):
    if settings.xy_balance == 'off':
        return list(placements), {'enabled': False, 'mode': 'off'}
    balancer = XYBalancer(models, settings, placements, engine=engine, cancel=cancel, progress=progress)
    try:
        return balancer.run(placements)
    finally:
        if balancer.owns_engine:
            balancer.engine.close()
