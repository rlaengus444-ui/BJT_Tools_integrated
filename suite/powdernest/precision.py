"""Optional surface-contact search; the standard solver remains the default.

Features and coarse grids only propose endpoints. No sampled field, convex
hull, normal, or bounding box can certify feasibility: can_place and the fresh
final validator enforce the original mesh/containment/distance rules.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import heapq
import time

import numpy as np

from .cancellation import check_cancelled
from .optimizer import _Search
from .rotations import ROTATIONS
from .types import Placement


def _spread(points, limit):
    """Deterministic farthest-point coverage, with a bounded candidate pool."""
    if len(points) <= limit:
        return np.arange(len(points))
    pool = np.unique(np.r_[np.linspace(0, len(points)-1, min(1024, len(points)), dtype=int),
                            np.argmin(points, axis=0), np.argmax(points, axis=0)])
    p = points[pool]
    selected = [int(np.argmin(p[:, 2]))]
    distance = np.full(len(p), np.inf)
    for _ in range(limit-1):
        check_cancelled()
        distance = np.minimum(distance, np.sum((p-p[selected[-1]])**2, axis=1))
        next_index = int(np.argmax(distance))
        if distance[next_index] <= 1e-20:
            break
        selected.append(next_index)
    return pool[selected]


class PrecisionSearch(_Search):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.precision_active = False
        self.precision_checkpoint = None
        self._features = {}
        self._oriented_features = {}
        self._finding = False
        self._skip_surfaces = False
        self.precision_stats = {'enabled': True, 'extra_time_seconds': self.settings.precision_time,
                                'contact_positions': 0, 'surface_slides': 0,
                                'local_moves': 0, 'beam_states': 0, 'refinement_moves': 0,
                                'selected': 'standard_checkpoint',
                                'feature_points_per_model_limit': 72,
                                'final_rule': 'unchanged exact mesh + solid containment verification'}

    def _surface(self, model_id, rid):
        key = (model_id, rid)
        if key in self._oriented_features:
            return self._oriented_features[key]
        if model_id not in self._features:
            check_cancelled()
            mesh = self.shapes[model_id].model.mesh
            vertices = np.asarray(mesh.vertices, dtype=float)
            anchor = vertices.min(axis=0)
            local = vertices-anchor
            corners = np.unique(local, axis=0)
            corners = corners[_spread(corners, 24)]
            centers = local[np.asarray(mesh.faces)].mean(axis=1)
            normals = np.asarray(mesh.face_normals, dtype=float)
            areas = np.asarray(mesh.area_faces, dtype=float)
            # Coplanar face centroids expose the centre of a shelf or cavity
            # floor, which vertex-only samples tend to miss. Grouping is a
            # proposal simplification and never a geometric acceptance rule.
            pool = np.unique(np.r_[np.linspace(0, len(centers)-1, min(4096, len(centers)), dtype=int),
                                    np.argsort(-areas, kind='stable')[:64]])
            c, n, w = centers[pool], normals[pool], areas[pool]
            planes = np.round(np.c_[n, np.sum(c*n, axis=1)], 5)
            _, first, inverse = np.unique(planes, axis=0, return_index=True, return_inverse=True)
            weighted = np.zeros((len(first), 3)); weights = np.zeros(len(first))
            np.add.at(weighted, inverse, c*w[:, None]); np.add.at(weights, inverse, w)
            grouped = weighted/weights[:, None]
            chosen = _spread(grouped, 32)
            raw = _spread(centers, 16)
            points = np.vstack((corners, grouped[chosen], centers[raw]))
            normals = np.vstack((np.zeros_like(corners), n[first[chosen]], normals[raw]))
            self._features[model_id] = (points, normals, local.max(axis=0))
        points, normals, extent = self._features[model_id]
        rotation = ROTATIONS[rid]
        low = np.minimum(rotation, 0) @ extent
        result = (points @ rotation.T-low, normals @ rotation.T)
        for array in result:
            array.setflags(write=False)
        self._oriented_features[key] = result
        return result

    def _surface_contacts(self, shape, orientation, existing):
        if not existing or self.settings.mode != 'mesh' or not self.budget.available():
            return np.empty((0, 3))
        own, own_n = self._surface(shape.model.model_id, orientation.rotation_id)
        margin = self.margins[shape.model.model_id]
        upper = np.asarray(self.settings.size)-margin-orientation.extent
        candidates = []
        # Bound contact work independently of build-box volume/copy count.
        neighbors = sorted(existing, key=lambda p: (p.position[2], p.position[1], p.position[0]))
        if len(neighbors) > 4:
            neighbors = neighbors[:2] + self.rng.sample(neighbors[2:], 2)
        for other in neighbors:
            if not self.budget.available():
                break
            world, normal = self._surface(other.model_id, other.rotation_id)
            world = world + other.position
            a, b = np.nonzero(normal @ own_n.T < -.94)
            if not len(a):
                continue
            gap = self.clearance(shape, self.shapes[other.model_id])+4*self.settings.tolerance
            points = world[a] + gap*normal[a]-own[b]
            points = points[np.all((points >= margin) & (points <= upper), axis=1)]
            if len(points):
                points = np.unique(np.round(points, 9), axis=0)
                chosen = np.unique(np.r_[np.argsort(points[:, 2], kind='stable')[:32],
                                         _spread(points, 32)])
                candidates.append(points[chosen])
        result = np.concatenate(candidates) if candidates else np.empty((0, 3))
        self.precision_stats['contact_positions'] += len(result)
        return result

    def _positions(self, shape, orientation, existing, deep=False, extras=()):
        if not self.precision_active:
            return super()._positions(shape, orientation, existing, deep, extras)
        contacts = self._surface_contacts(shape, orientation, existing)
        return super()._positions(shape, orientation, existing, deep, [*extras, *contacts])

    def find_pose(self, key, existing, preferred=None, only_rotation=None, deep=False, extras=(),
                  height_cap=None, quality=False, limit=1):
        if not self.precision_active or self._finding or self.settings.mode != 'mesh' or not existing:
            return super().find_pose(key, existing, preferred, only_rotation, deep, extras, height_cap, quality, limit)
        self._finding = True
        try:
            shape = self.shapes[key[0]]
            rotations = [only_rotation] if only_rotation is not None else list(shape.orientations)
            if preferred in rotations:
                rotations.remove(preferred); rotations.insert(0, preferred)
            heap = []
            for priority, rid in enumerate(rotations):
                if not self.budget.available():
                    break
                orientation = shape.orientations[rid]
                points = self._surface_contacts(shape, orientation, existing)
                if height_cap is not None:
                    points = points[points[:, 2]+orientation.extent[2] <= height_cap]
                if len(points):
                    ranking = np.lexsort((points[:, 0], points[:, 1], points[:, 2]))
                    for rank, index in enumerate(ranking[:16]):
                        position = tuple(float(x) for x in points[index])
                        # First offer each allowed orientation a surface pose;
                        # thousands of equal-AABB contacts must not starve it.
                        heapq.heappush(heap, (rank, position[2]+orientation.extent[2], priority,
                                              position[1], position[0], rid, position))
            found = []
            for _ in range(min(128, len(heap))):
                if not self.budget.available():
                    break
                *_, rid, position = heapq.heappop(heap)
                pose = Placement(key[0], key[1], rid, position)
                if self.can_place(pose, existing):
                    found.append(pose)
                    if len(found) >= limit:
                        break
            ordinary = super().find_pose(key, existing, preferred, only_rotation, deep, extras,
                                         height_cap, quality, limit)
            found.extend(ordinary if limit > 1 else [ordinary] if ordinary is not None else [])
            unique = {(p.rotation_id, p.position): p for p in found}
            ranked = sorted(unique.values(), key=lambda p: (p.position[2]+self.extent(p)[2],
                                                            p.position[2], p.position[1], p.position[0], p.rotation_id))
            return ranked[:limit] if limit > 1 else ranked[0] if ranked else None
        finally:
            self._finding = False

    def _slide(self, p, others, axis):
        shape = self.shapes[p.model_id]
        wall = float(self.margins[p.model_id][axis])
        upper = p.position[axis]
        if upper <= wall+1e-5:
            return p
        own, own_n = self._surface(p.model_id, p.rotation_id)
        values = [wall]
        for q in others:
            if not self.budget.available():
                return p
            points, normal = self._surface(q.model_id, q.rotation_id)
            points = points + q.position
            matching = ((normal @ own_n.T < -.999999)
                        & (np.abs(normal[:, axis, None]) > .999999))
            a, b = np.nonzero(matching)
            gap = self.clearance(shape, self.shapes[q.model_id])+4*self.settings.tolerance
            values.extend((points[a, axis]+gap*normal[a, axis]-own[b, axis]).tolist())
        values = sorted({float(x) for x in values if wall <= x < upper-1e-5})
        # Thin pockets may occur between the old AABB endpoints. Samples are
        # independently tested; a rejected point never implies monotonicity.
        if len(values) > 64:
            values = [values[int(i)] for i in np.linspace(0, len(values)-1, 64, dtype=int)]
        best = p
        for value in values:
            if not self.budget.available():
                break
            position = list(p.position); position[axis] = value
            candidate = replace(p, position=tuple(position))
            if self.can_place(candidate, others):
                best = candidate
                self.precision_stats['surface_slides'] += 1
                break
        return best

    def _surface_compact(self, placements):
        result = list(placements)
        order = sorted(range(len(result)), key=lambda i: (
            -result[i].position[2]-self.extent(result[i])[2], i))[:24]
        for index in order:
            if not self.budget.available():
                break
            p = result[index]; others = result[:index]+result[index+1:]
            for axis in (2, 0, 1, 2):
                p = self._slide(p, others, axis)
            # A bounded multiresolution XY relocation followed by a surface Z
            # descent can enter a cavity even when a straight descent cannot.
            if index in order[:6]:
                for scale in (1., .5, .25, .125):
                    if not self.budget.available():
                        break
                    incumbent = p
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
                        if not self.budget.available():
                            break
                        delta = self.settings.search_step*scale
                        pose = replace(p, position=(p.position[0]+dx*delta, p.position[1]+dy*delta, p.position[2]))
                        if self.can_place(pose, others):
                            pose = self._slide(pose, others, 2)
                            if (pose.position[2]+self.extent(pose)[2], sum(pose.position)) < (
                                    incumbent.position[2]+self.extent(incumbent)[2], sum(incumbent.position)):
                                incumbent = pose
                    p = incumbent
                if self.budget.available():
                    # All allowed orientation IDs stay in the geometric search.
                    pose = self.find_pose((p.model_id, p.copy_index), others, preferred=p.rotation_id,
                                          extras=[p.position], height_cap=p.position[2]+self.extent(p)[2],
                                          quality=True)
                    if pose is not None and (pose.position[2]+self.extent(pose)[2], sum(pose.position)) < (
                            p.position[2]+self.extent(p)[2], sum(p.position)):
                        p = pose
            self.precision_stats['local_moves'] += int(p != result[index])
            result[index] = p
            self.retain(result)
        return result

    def _beam_repair(self, placements):
        if len(placements) < 2:
            return placements
        ranked = sorted(placements, key=lambda p: (-p.position[2]-self.extent(p)[2], p.model_id, p.copy_index))
        count = min(len(ranked), 2+self.precision_stats['refinement_moves']%2)
        removed = {(p.model_id, p.copy_index) for p in ranked[:count]}
        base = [p for p in placements if (p.model_id, p.copy_index) not in removed]
        retained = {(p.model_id, p.copy_index) for p in base}
        pending = tuple(k for k in self.keys if k not in retained)
        if len(pending) > 4:
            return placements
        height_cap = self.height(placements)
        beam = [(base, pending)]
        for _ in range(len(pending)):
            successors = []
            for placed, todo in beam:
                choices = []
                for key in sorted(todo, key=lambda k: (-self.shapes[k[0]].volume, k)):
                    if key[0] not in {k[0] for k in choices}:
                        choices.append(key)
                for key in choices[:2]:
                    if not self.budget.available():
                        return self.best
                    poses = self.find_pose(key, placed, height_cap=height_cap, quality=True, limit=3)
                    for pose in poses:
                        proposal = placed+[pose]
                        remaining = tuple(k for k in todo if k != key)
                        successors.append((proposal, remaining))
                        self.precision_stats['beam_states'] += 1
                        self.retain(proposal)
            if not successors:
                return placements
            successors.sort(key=lambda pair: (self.score(pair[0]),
                sum(p.position[2]+self.extent(p)[2] for p in pair[0]),
                sum(p.position[0]+p.position[1] for p in pair[0])))
            beam = successors[:4]
        return min((p for p, todo in beam if not todo), key=self.score, default=placements)

    def run(self):
        original_iterations = self.settings.iterations
        self._skip_surfaces = (self.settings.mode == 'aabb' or all(self.shapes[k[0]].box for k in self.keys))
        if self._skip_surfaces:
            self.precision_stats['fallback'] = ('aabb_mode' if self.settings.mode == 'aabb' else 'all_exact_cuboids')
        # Preserve the entire standard run and its RNG sequence. Refinement has
        # a separately declared extra budget, never borrowed from the baseline.
        baseline = super().run()
        self.precision_checkpoint = list(baseline)
        self.precision_stats['standard_objective'] = self.score(baseline)
        self.precision_stats['standard_iterations'] = self.iterations
        self.precision_stats['standard_height_mm'] = self.height(baseline)
        self.precision_stats['standard_placed'] = len(baseline)
        self.precision_stats['standard_search_elapsed_seconds'] = time.perf_counter()-self.budget.start
        self.precision_stats['standard_placements'] = [asdict(p) for p in baseline]
        if self._height_bound_reached():
            self.precision_stats['skipped_height_bound'] = True
            return baseline
        if self._skip_surfaces or self.certificate or original_iterations == 0 or self.settings.precision_time == 0 or not self.keys or all(k[0] in self.impossible for k in self.keys):
            return baseline
        if self.budget.cancel is not None and self.budget.cancel.is_set():
            return baseline
        self.budget.deadline = time.perf_counter()+self.settings.precision_time
        if self.budget.reason == 'time_limit':
            self.budget.reason = None
        self.precision_active = True
        current = list(self.best)
        while self.budget.available() and (original_iterations is None or self.precision_stats['refinement_moves'] < original_iterations):
            if self._height_bound_reached():
                self.budget.reason = 'height_bound'
                break
            step = self.precision_stats['refinement_moves']
            overall = self.budget.deadline
            if original_iterations is None:
                self.budget.deadline = min(overall, time.perf_counter()+min(2.5, max(.1, (overall-time.perf_counter())*.45)))
            try:
                if len(self.best) < self.requested:
                    present = {(p.model_id, p.copy_index) for p in current}
                    missing = sorted((k for k in self.keys if k not in present),
                                     key=lambda k: (-self.shapes[k[0]].volume, k))
                    proposal = self.build(missing, current, deep=True)
                elif step%4 in (0, 3):
                    proposal = self._surface_compact(current)
                elif step%4 == 1:
                    proposal = self._beam_repair(current)
                else:
                    proposal = self._destroy_repair(current, 'top' if step%8 == 2 else 'cluster')
                self.retain(proposal)
                current = list(proposal if self.score(proposal) <= self.score(current) else self.best)
            finally:
                self.budget.deadline = overall
                if self.budget.reason == 'time_limit' and time.perf_counter() < overall:
                    self.budget.reason = None
            self.iterations += 1
            self.precision_stats['refinement_moves'] += 1
            self.emit()
        self.precision_stats.update(final_objective=self.score(self.best),
                                    cached_models=len(self._features),
                                    cached_orientations=len(self._oriented_features))
        if self.score(self.best) < self.score(baseline):
            self.precision_stats['selected'] = 'precision'
            if self.stage_report:
                self.stage_report['selected'] = 'precision'
        return self.best
