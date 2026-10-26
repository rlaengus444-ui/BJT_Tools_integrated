"""Bounded exact retriangulation of redundant zero-area CAD seam faces.

This changes triangle connectivity only. It never welds, fills holes, moves
vertices, guesses a solid from an open shell, or approves a native backend's
automatic geometry repair. Only a collinear triangle whose longest edge is an
edge of one surviving nondegenerate face is supported. That face is partitioned
at the already existing middle vertex, preserving the oriented surface chain.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib

import numpy as np
import trimesh

MAX_SEAMS = 64
MAX_REPAIR_FACES = 1_000_000
SCAN_FACES = 65_536


def exact_collinear_seam_repair(mesh: trimesh.Trimesh, cancel_event=None, *,
                               max_seams=MAX_SEAMS, max_faces=MAX_REPAIR_FACES):
    """Return a strictly valid copy and an exact geometric-equivalence audit.

    Call only after ordinary validation identifies degenerate triangles. An
    unsupported or ambiguous mesh raises ValueError; cancellation raises
    InterruptedError. No approximation or tolerance-based welding is used.
    """
    # Imported only at call time: geometry owns ordinary validation and calls
    # this fallback lazily, so neither import creates a module initialization
    # cycle nor does the normal valid-mesh path load Fraction repair work.
    from .geometry import MeshObject, _strict_topology, _validate_object

    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('Inspection cancelled during exact CAD seam retriangulation.')

    check_cancelled()
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if not len(faces) or len(faces) > max_faces:
        raise ValueError('Mesh is empty or exceeds the bounded seam-repair face budget')
    for start in range(0, len(vertices), SCAN_FACES * 4):
        check_cancelled()
        if not np.isfinite(vertices[start:start + SCAN_FACES * 4]).all():
            raise ValueError('Finite nonempty mesh required')
    zero_indices = []
    for start in range(0, len(faces), SCAN_FACES):
        check_cancelled()
        indices = faces[start:start + SCAN_FACES]
        if np.any(indices < 0) or np.any(indices >= len(vertices)):
            raise ValueError('Invalid indices')
        triangles = vertices[indices]
        with np.errstate(over='ignore', invalid='ignore', under='ignore'):
            cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            squared = np.einsum('ij,ij->i', cross, cross)
        if not np.isfinite(squared).all():
            raise ValueError('Nonfinite face computation')
        found = np.flatnonzero(squared == 0)
        # Common genuinely broken large meshes contain repeated-index faces.
        # Reject at the first affected chunk instead of scanning the scene.
        if len(found):
            candidate = indices[found]
            if np.any((candidate[:, 0] == candidate[:, 1])
                      | (candidate[:, 1] == candidate[:, 2])
                      | (candidate[:, 2] == candidate[:, 0])):
                raise ValueError('Repeated-index degeneracy is not an exact CAD seam')
        zero_indices.extend((found + start).tolist())
        if len(zero_indices) > max_seams:
            raise ValueError('Too many zero-area faces for bounded exact seam repair')
    if not len(zero_indices):
        raise ValueError('No zero-area seam candidates')
    check_cancelled()
    original_topology = _strict_topology(mesh, cancel_event)
    check_cancelled()
    if original_topology != (True, True):
        raise ValueError('Original indexed surface must already be closed and consistently oriented')
    rational = {}

    def point(index):
        check_cancelled()
        index = int(index)
        if index not in rational:
            rational[index] = tuple(Fraction(float(x)) for x in vertices[index])
        return rational[index]

    def subtract(a, b):
        return tuple(x - y for x, y in zip(a, b))

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def collinear(a, b, c):
        u, v = subtract(b, a), subtract(c, a)
        return (u[1] * v[2] == u[2] * v[1]
                and u[2] * v[0] == u[0] * v[2]
                and u[0] * v[1] == u[1] * v[0])

    zero_set = set(zero_indices)
    seams = []
    for number in zero_indices:
        check_cancelled()
        ids = [int(x) for x in faces[number]]
        if len(set(ids)) != 3:
            raise ValueError('Repeated-index degeneracy is not an exact CAD seam')
        p = [point(index) for index in ids]
        if not collinear(*p):
            # A floating cross product may underflow or round to zero. Never
            # promote that numerical result into an exact geometric claim.
            raise ValueError('Zero floating area is not exactly collinear')
        longest = max(((a, b) for a in range(3) for b in range(a)),
                      key=lambda ab: dot(subtract(p[ab[0]], p[ab[1]]),
                                         subtract(p[ab[0]], p[ab[1]])))
        middle = next(index for index in range(3) if index not in longest)
        left, right, mid = ids[longest[0]], ids[longest[1]], ids[middle]
        edge = subtract(point(right), point(left))
        t = dot(subtract(point(mid), point(left)), edge)
        length_squared = dot(edge, edge)
        if not 0 < t < length_squared:
            raise ValueError('Middle vertex must lie strictly within the exact segment')
        seams.append({'removed_face': int(number), 'endpoints': [left, right],
                      'middle_vertex': mid, 'middle_parameter': str(t / length_squared)})

    bits = max(1, (len(vertices) - 1).bit_length())
    if bits > 31:
        raise ValueError('Too many indexed vertices')
    edge_keys = {tuple(sorted(seam['endpoints'])) for seam in seams}
    target_edges = {edge: [] for edge in edge_keys}
    encoded_to_edge = {(a << bits) | b: (a, b) for a, b in edge_keys}
    target_codes = np.array(list(encoded_to_edge), dtype=np.uint64)
    for start in range(0, len(faces), SCAN_FACES):
        check_cancelled()
        chunk = faces[start:start + SCAN_FACES]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            check_cancelled()
            low = np.minimum(chunk[:, a], chunk[:, b]).astype(np.uint64)
            high = np.maximum(chunk[:, a], chunk[:, b]).astype(np.uint64)
            codes = (low << np.uint64(bits)) | high
            for local in np.flatnonzero(np.isin(codes, target_codes)):
                number = start + int(local)
                if number not in zero_set:
                    edge = encoded_to_edge[int(codes[local])]
                    target_edges[edge].append((number, [int(x) for x in chunk[local]]))
    removed = set(zero_set)
    extra_faces = {}
    next_face = len(faces)
    for seam in seams:
        check_cancelled()
        endpoints = set(seam['endpoints'])
        adjacent = [(number, face) for number, face in target_edges[tuple(sorted(endpoints))]
                    if number not in removed]
        adjacent += [(number, face) for number, face in extra_faces.items()
                     if endpoints.issubset(face)]
        if len(adjacent) != 1:
            raise ValueError('Longest seam segment must be an edge of exactly one retained face')
        number, face = adjacent[0]
        edge_at = next(i for i in range(3)
                       if {face[i], face[(i + 1) % 3]} == endpoints)
        a, b, other = (face[edge_at], face[(edge_at + 1) % 3], face[(edge_at + 2) % 3])
        if collinear(point(a), point(b), point(other)):
            raise ValueError('The retained face being partitioned must have exact positive area')
        middle = seam['middle_vertex']
        if number < len(faces):
            removed.add(number)
        else:
            del extra_faces[number]
        replacements = [[a, middle, other], [middle, b, other]]
        extra_faces[next_face], extra_faces[next_face + 1] = replacements
        seam.update(replaced_face=number, original_triangle=face,
                    replacement_triangles=replacements,
                    removed_image_is_retained_edge=True,
                    exact_oriented_triangle_partition=True)
        next_face += 2
    check_cancelled()
    keep = np.ones(len(faces), dtype=bool)
    keep[list(removed)] = False
    repaired_faces = np.concatenate((faces[keep], np.array(list(extra_faces.values()), dtype=np.int64)))
    repaired = trimesh.Trimesh(vertices=np.array(vertices, copy=True),
                               faces=repaired_faces, process=False)
    check_cancelled()
    validation = _validate_object(0, MeshObject('exact-seam-repair', repaired),
                                  cancel_event=cancel_event)
    check_cancelled()
    if not validation['valid']:
        raise ValueError('Retriangulated mesh failed ordinary strict validation: ' + '; '.join(validation['errors']))
    if not np.array_equal(vertices, repaired.vertices):
        raise ValueError('Retriangulation unexpectedly changed coordinates')
    checksum = hashlib.sha256()
    for start in range(0, len(vertices), SCAN_FACES * 4):
        check_cancelled()
        checksum.update(vertices[start:start + SCAN_FACES * 4].tobytes())
    proof = {'method': 'exact-collinear-seam-retriangulation',
             'original_topology': list(original_topology),
             'original_faces': len(faces), 'repaired_faces': len(repaired.faces),
             'coordinates_unchanged': True,
             'vertices_sha256': checksum.hexdigest(),
             'exact_surface_union_preserved': True,
             'exact_oriented_surface_chain_preserved': True,
             'zero_area_seams_removed': len(seams),
             'ordinary_validation': validation,
             'seams': seams}
    return repaired, proof
