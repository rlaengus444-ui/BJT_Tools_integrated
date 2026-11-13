"""Positive certificates of topological interlocking between disjoint solids.

Loops are actual mesh-edge cycles, produced by a primal tree / dual cotree.
A nonzero linking number between any two such loops proves that the two bodies
cannot be separated by continuous motion without intersecting or breaking one.
Zero linking numbers do NOT prove separability (Whitehead/Borromean/geometric
jams are counterexamples). This module never labels that case 'separable'.
"""
from collections import deque
from fractions import Fraction
import time

import numpy as np

from .cancellation import check_cancelled

_stamp_token = _stamp_value = None


PROJECTIONS = (
    ((1, 3, 7), (2, -5, 11)),
    ((3, -7, 2), (5, 4, 9)),
    ((7, 2, -3), (-4, 11, 5)),
    ((2, 11, 5), (7, -3, 13)),
)


def predicate_stamp():
    """Bind private validation proofs to this checker and its projections."""
    global _stamp_token, _stamp_value
    import hashlib
    from .geometry import _code_signature
    functions = (loop_basis, _cross2, approximate_linking, exact_linking, detect_link, predicate_stamp)
    token = (tuple(id(function) for function in functions), PROJECTIONS)
    if token != _stamp_token:
        digest = hashlib.sha256(repr(PROJECTIONS).encode())
        for function in functions:
            digest.update(_code_signature(function.__code__).encode())
            if function.__module__ != __name__ or function.__closure__:
                digest.update(str(id(function)).encode())
        _stamp_token, _stamp_value = token, digest.hexdigest()
    return _stamp_value


def loop_basis(mesh):
    """Extract closed edge cycles of a closed manifold; preserve coordinates."""
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    edges = np.asarray(mesh.edges_unique, dtype=np.int64)
    face_edges = np.asarray(mesh.faces_unique_edges, dtype=np.int64)
    owners = [[] for _ in edges]
    adjacency = [[] for _ in vertices]
    for i, (a, b) in enumerate(edges):
        if i % 1024 == 0: check_cancelled()
        adjacency[a].append((int(b), i)); adjacency[b].append((int(a), i))
    for i, row in enumerate(face_edges):
        if i % 1024 == 0: check_cancelled()
        for edge in row: owners[edge].append(i)
    if any(len(row) != 2 for row in owners): raise ValueError('Interlocking requires a closed manifold mesh')
    parent = np.full(len(vertices), -1, dtype=np.int64)
    depth = np.zeros(len(vertices), dtype=np.int64); primal = np.zeros(len(edges), dtype=bool)
    components = 0
    for root in range(len(vertices)):
        if parent[root] >= 0: continue
        check_cancelled(); components += 1; parent[root] = root; queue = deque([root])
        while queue:
            a = queue.popleft()
            for b, edge in adjacency[a]:
                if parent[b] < 0:
                    parent[b] = a; depth[b] = depth[a] + 1; primal[edge] = True; queue.append(b)
    union = np.arange(len(faces), dtype=np.int64)
    def find(i):
        while union[i] != i:
            union[i] = union[union[i]]; i = int(union[i])
        return i
    dual = np.zeros(len(edges), dtype=bool)
    for edge, (a, b) in enumerate(owners):
        if edge % 1024 == 0: check_cancelled()
        if primal[edge]: continue
        first, second = find(a), find(b)
        if first != second:
            union[first] = second; dual[edge] = True
    leftover = np.flatnonzero(~primal & ~dual)
    expected = 2 * components - (len(vertices) - len(edges) + len(faces))
    if len(leftover) != expected or expected < 0 or expected % 2:
        raise ValueError('Mesh tree/cotree topology is inconsistent')
    loops = []
    for edge in leftover:
        a, b = map(int, edges[edge]); first = [a]; second = [b]
        while a != b:
            if depth[a] >= depth[b]: a = int(parent[a]); first.append(a)
            else: b = int(parent[b]); second.append(b)
        indices = first + second[-2::-1]
        indices.append(indices[0])
        loops.append({'vertex_indices': indices, 'points': vertices[indices].copy()})
    return {'loops': loops, 'components': components, 'genus_sum': expected // 2}


def _cross2(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def approximate_linking(first, second, projection):
    """Generic-projection crossing sum; None means numerically unresolved."""
    basis = np.asarray(projection, dtype=float); view = np.cross(*basis)
    a, b = np.asarray(first), np.asarray(second)
    pa, pb = a @ basis.T, b @ basis.T
    da, db = np.diff(pa, axis=0), np.diff(pb, axis=0)
    delta = pb[None, :-1, :] - pa[:-1, None, :]
    determinant = _cross2(da[:, None, :], db[None, :, :])
    scale = np.linalg.norm(da, axis=1)[:, None] * np.linalg.norm(db, axis=1)[None, :]
    usable = np.abs(determinant) > 128 * np.finfo(float).eps * scale
    numerator_a = _cross2(delta, db[None, :, :]); numerator_b = _cross2(delta, da[:, None, :])
    with np.errstate(divide='ignore', invalid='ignore'):
        ta = numerator_a / determinant; tb = numerator_b / determinant
    hits = usable & (ta >= -1e-9) & (ta <= 1 + 1e-9) & (tb >= -1e-9) & (tb <= 1 + 1e-9)
    if np.any(hits & ((ta <= 1e-8) | (ta >= 1 - 1e-8) | (tb <= 1e-8) | (tb >= 1 - 1e-8))):
        return None
    rows, cols = np.nonzero(hits)
    if not len(rows): return 0
    point_a = a[rows] + ta[rows, cols, None] * np.diff(a, axis=0)[rows]
    point_b = b[cols] + tb[rows, cols, None] * np.diff(b, axis=0)[cols]
    depth = (point_a - point_b) @ view
    if np.any(np.abs(depth) < 1e-8): return None
    total = int(np.sign(determinant[rows, cols] * depth).sum())
    return None if total % 2 else total // 2


def exact_linking(first, second, projection, *, deadline=None):
    """Exact Fraction crossing sum of the binary input coordinates.

    No tolerance/mesh coarsening is evidence of a linked pair. A projection
    through a curve vertex, or collinear projected edges, is retried instead.
    """
    basis = [[Fraction(x) for x in row] for row in projection]
    view = [basis[0][1]*basis[1][2]-basis[0][2]*basis[1][1],
            basis[0][2]*basis[1][0]-basis[0][0]*basis[1][2],
            basis[0][0]*basis[1][1]-basis[0][1]*basis[1][0]]
    def project(curve):
        points = [[Fraction(float(x)) for x in row] for row in curve]
        return points, [[sum(p[i]*axis[i] for i in range(3)) for axis in basis] for p in points]
    a, pa = project(first); b, pb = project(second)
    total = 0; crossings = []
    for i, (p, q) in enumerate(zip(pa, pa[1:])):
        check_cancelled(); da = [q[k] - p[k] for k in range(2)]
        if deadline is not None and time.perf_counter() >= deadline: return None
        for j, (u, v) in enumerate(zip(pb, pb[1:])):
            db = [v[k] - u[k] for k in range(2)]; delta = [u[k] - p[k] for k in range(2)]
            determinant = da[0]*db[1] - da[1]*db[0]
            if not determinant:
                if delta[0]*da[1]-delta[1]*da[0] == 0:
                    # Degenerate or collinear projections are harmless to a
                    # negative search, but cannot certify a positive result.
                    return None
                continue
            ta = (delta[0]*db[1]-delta[1]*db[0]) / determinant
            tb = (delta[0]*da[1]-delta[1]*da[0]) / determinant
            if not (0 <= ta <= 1 and 0 <= tb <= 1): continue
            if ta in (0, 1) or tb in (0, 1): return None
            depth = sum((a[i][k]+ta*(a[i+1][k]-a[i][k])-
                         b[j][k]-tb*(b[j+1][k]-b[j][k]))*view[k] for k in range(3))
            if not depth: return None
            sign = 1 if determinant * depth > 0 else -1; total += sign
            crossings.append({'edge_a': i, 'edge_b': j, 'sign': sign})
    if total % 2: return None
    return {'linking_number': total // 2, 'projection': projection, 'crossings': crossings}


def detect_link(first_basis, second_basis, *, seconds=10., transform_first=None, transform_second=None):
    """Find a positive certificate; no result is a proof of separability."""
    started = time.perf_counter(); tested = 0; uncertain = 0
    def transformed(basis, transform):
        if transform is None: return [row['points'] for row in basis['loops']]
        rotation, translation = transform
        return [row['points'] @ rotation.T + translation for row in basis['loops']]
    first = transformed(first_basis, transform_first); second = transformed(second_basis, transform_second)
    for i, a in enumerate(first):
        for j, b in enumerate(second):
            check_cancelled()
            if time.perf_counter() - started > seconds:
                return {'status': 'unknown_timeout', 'tested_loop_pairs': tested, 'seconds': time.perf_counter()-started}
            tested += 1
            for projection in PROJECTIONS:
                if time.perf_counter() - started > seconds:
                    return {'status': 'unknown_timeout', 'tested_loop_pairs': tested}
                value = approximate_linking(a, b, projection)
                if value is None:
                    uncertain += 1; continue
                if not value: break
                certificate = exact_linking(a, b, projection, deadline=started+seconds)
                if certificate is None: continue
                if certificate['linking_number']:
                    return {'status': 'linked', 'loop_a': i, 'loop_b': j,
                            'curve_a': a.tolist(), 'curve_b': b.tolist(),
                            'certificate': certificate, 'tested_loop_pairs': tested,
                            'seconds': time.perf_counter()-started}
                raise ValueError('Approximate and exact linking decisions disagree')
    return {'status': 'not_detected', 'separability_proven': False, 'tested_loop_pairs': tested,
            'uncertain_projections': uncertain, 'seconds': time.perf_counter()-started}
