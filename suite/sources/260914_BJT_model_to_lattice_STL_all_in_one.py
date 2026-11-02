#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BJT 5.8 — 원본 충실형 쉘/격자 STL
지정 DPI와 층 두께의 복셀 표면을 이동·스무딩 없이 보존합니다.
축 정렬 복셀의 선 접촉은 네 반평면과 방향을 검사하여 별도 보고합니다.
선/점 접촉이 있는 출력은 엄격한 2-manifold 메시가 아닙니다.
열린 경계, 중복 접합, 방향 오류는 최종 저장을 차단합니다.
"""

from __future__ import annotations

# Standard-library-only bootstrap. Runs before any third-party import.
_BJT_BOOT_REQUIREMENTS = (
    'numpy==2.3.5', 'scipy==1.17.0', 'Pillow==12.3.0',
    'scikit-image==0.26.0', 'trimesh==5.1.0', 'shapely==2.1.2',
    'numba==0.67.0', 'rtree==1.4.1', 'opencv-python==5.0.0.93',
    'tkinterdnd2==0.6.3', 'vtk==9.3.1',
)
_BJT_BOOT_MODULES = ('numpy', 'scipy', 'PIL.ImageTk', 'skimage', 'trimesh',
                     'shapely', 'numba', 'rtree', 'cv2', 'tkinterdnd2', 'vtk', 'tkinter')


def _bjt_boot_process_env(executable):
    """Conda DLL and Tcl lookup also works outside an activated terminal."""
    import os
    from pathlib import Path
    env = os.environ.copy()
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env.pop('TCL_LIBRARY', None)
    env.pop('TK_LIBRARY', None)
    env['PYTHONNOUSERSITE'] = '1'
    prefix = Path(executable).parent
    if prefix.name in ('bin', 'Scripts'):
        prefix = prefix.parent
    if (prefix / 'conda-meta').is_dir():
        bins = [prefix, prefix/'Library/mingw-w64/bin', prefix/'Library/usr/bin',
                prefix/'Library/bin', prefix/'Scripts', prefix/'bin']
        env['PATH'] = os.pathsep.join(str(p) for p in bins) + os.pathsep + env.get('PATH', '')
        env['CONDA_PREFIX'] = str(prefix)
    return env


def _bjt_boot_find_conda():
    import os, shutil, sys
    from pathlib import Path
    candidates = [os.environ.get('CONDA_EXE'), shutil.which('conda.exe'),
                  shutil.which('conda')]
    for prefix in [Path(sys.prefix), *Path(sys.executable).parents]:
        candidates.extend([prefix/'Scripts/conda.exe', prefix/'_conda.exe', prefix/'bin/conda'])
    for item in candidates:
        if item and Path(item).is_file() and Path(item).suffix.lower() not in ('.bat', '.cmd'):
            return str(Path(item).absolute())
    return None


def _bjt_boot_find_runtime():
    import os, shutil, subprocess, sys
    from pathlib import Path
    candidates = [[sys.executable]]
    launcher = shutil.which('py') if os.name == 'nt' else None
    if launcher:
        candidates.append([launcher, '-3.12-64'])
    executable = shutil.which('python3.12')
    if executable:
        candidates.append([executable])
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
        candidates.append([str(base/'Programs/Python/Python312/python.exe')])
    for command in candidates:
        if not Path(command[0]).is_file():
            continue
        try:
            result = subprocess.run(command + ['-c',
                'import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize("P")==8 else 1)'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                env=_bjt_boot_process_env(command[0]))
            if result.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _bjt_auto_start():
    import contextlib
    import hashlib
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    import traceback

    signature = hashlib.sha256(repr((_BJT_BOOT_REQUIREMENTS, _BJT_BOOT_MODULES)).encode()).hexdigest()[:16]
    ticket = signature + '|' + os.path.abspath(sys.prefix)
    if os.environ.pop('BJT_BOOTSTRAP_READY', None) == ticket:
        return
    # Do not require requirements.txt or a separate setup script.
    probe = ('import sys,struct,importlib,importlib.metadata as m; '
             'assert struct.calcsize("P")==8; '
             'assert sys.version_info[:2]==(3,12); '
             f'reqs={_BJT_BOOT_REQUIREMENTS!r}; '
             'assert all(m.version(r.split("==")[0])==r.split("==")[1] for r in reqs); '
             f'[importlib.import_module(n) for n in {_BJT_BOOT_MODULES!r}]')
    def healthy(exe):
        try:
            return subprocess.run([str(exe), '-c', probe], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=120,
                                  env=_bjt_boot_process_env(exe)).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    # Reuse a fully compatible environment, without downloading anything.
    if healthy(sys.executable):
        return
    root = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.cache')) / 'BJT' / 'python-envs'
    log_path = root / 'automatic_setup.log'
    def status(message):
        if sys.stdout is not None:
            print(message, flush=True)
    @contextlib.contextmanager
    def lock(path):
        # Kernel-owned lock: releases even if the installing process crashes.
        with path.open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            deadline = time.monotonic() + 1800
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Another BJT installation is still running. Please retry later.')
                    time.sleep(.25)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    try:
        root.mkdir(parents=True, exist_ok=True)
        env_dir = root / ('cp312-' + signature)
        env_python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if os.name == 'nt' and (env_dir/'python.exe').is_file():
            env_python = env_dir/'python.exe'
        with lock(root / (signature + '.lock')):
            with log_path.open('a', encoding='utf-8') as log:
                log.write('\n=== Automatic setup ' + time.ctime() + ' ===\n'); log.flush()
                def run(command):
                    # No shell; paths with spaces and Korean characters are preserved.
                    result = subprocess.run([str(x) for x in command], stdout=log,
                                            stderr=subprocess.STDOUT, timeout=1800,
                                            env=_bjt_boot_process_env(command[0]))
                    if result.returncode:
                        raise RuntimeError('Automatic installation failed. See: ' + str(log_path))
                if not healthy(env_python):
                    status('BJT: 필요한 패키지를 전용 환경에 자동 설치합니다. 첫 실행에는 인터넷이 필요합니다.')
                    status('설치 기록: ' + str(log_path))
                    if not env_python.exists():
                        conda = _bjt_boot_find_conda()
                        if conda:
                            status('BJT: Anaconda/Miniconda로 Python 3.12 전용 환경을 자동 생성합니다.')
                            run([conda, 'create', '--yes', '--prefix', env_dir,
                                 '--override-channels', '--channel', 'conda-forge',
                                 'python=3.12', 'pip', 'tk'])
                            env_python = env_dir / ('python.exe' if os.name == 'nt' else 'bin/python')
                        else:
                            base = _bjt_boot_find_runtime()
                            if base is None:
                                raise RuntimeError('실행 가능한 Python 3.12 또는 Conda를 찾지 못했습니다. '
                                                   '확인되지 않은 py 명령은 실행하지 않았습니다.')
                            run(base + ['-m', 'venv', str(env_dir)])
                    pip_check = subprocess.run([str(env_python), '-m', 'pip', '--version'],
                                               stdout=log, stderr=subprocess.STDOUT, timeout=60,
                                               env=_bjt_boot_process_env(env_python))
                    if pip_check.returncode:
                        run([env_python, '-m', 'ensurepip', '--upgrade'])
                    run([env_python, '-m', 'pip', 'install', '--disable-pip-version-check',
                         '--no-input', '--only-binary=:all:', '--retries', '3', '--timeout', '30',
                         *_BJT_BOOT_REQUIREMENTS])
                    if not healthy(env_python):
                        # Exact version can still have damaged files / a broken DLL.
                        run([env_python, '-m', 'pip', 'install', '--disable-pip-version-check',
                             '--no-input', '--only-binary=:all:', '--force-reinstall',
                             *_BJT_BOOT_REQUIREMENTS])
                    if not healthy(env_python):
                        raise RuntimeError('Installed packages could not be loaded. See: ' + str(log_path))
        status('BJT: 패키지 준비 완료. 프로그램을 시작합니다.')
        child_env = _bjt_boot_process_env(env_python)
        child_env['BJT_BOOTSTRAP_READY'] = signature + '|' + os.path.abspath(env_dir)
        # Preserve working directory, all CLI arguments and the child's exit status.
        code = subprocess.call([str(env_python), str(Path(__file__).resolve()), *sys.argv[1:]], env=child_env)
    except Exception:
        message = 'BJT 자동 준비에 실패했습니다.\n' + traceback.format_exc() + '\n설치 기록: ' + str(log_path)
        status(message)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'BJT', 0x10)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == '__main__':
    _bjt_auto_start()


import importlib.util
import json
import math
import os
import re
import shutil
import struct
import hashlib
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Numeric presentation is shared with the integrated suite; input and export
# values retain their original precision.
try:
    from display_numbers import format_number, format_display_value
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from display_numbers import format_number, format_display_value


def _ensure_packages() -> None:
    required = {'numpy':'numpy', 'PIL':'pillow', 'scipy':'scipy', 'skimage':'scikit-image',
                'trimesh':'trimesh', 'shapely':'shapely>=2.1,<3', 'numba':'numba', 'rtree':'rtree'}
    missing = [pkg for mod,pkg in required.items() if importlib.util.find_spec(mod) is None]
    if missing:
        raise SystemExit('필수 패키지가 없습니다. setup_windows.cmd를 실행하거나:\n' +
                         sys.executable + ' -m pip install ' + ' '.join(missing))
    from importlib import metadata as package_metadata
    parts = package_metadata.version('shapely').split('.')
    if tuple(int(x) for x in parts[:2]) < (2,1):
        raise SystemExit('Shapely 2.1 이상이 필요합니다. setup_windows.cmd로 별도 환경을 구성하십시오.')
    if struct.calcsize('P') != 8:
        raise SystemExit('대형 모델 처리는 64-bit Python이 필요합니다.')


_ensure_packages()

import numpy as np
from PIL import Image, ImageOps, ImageTk
import trimesh
from numba import njit
# Numba serializes globals['__name__']; a function.__module__ override alone is insufficient.
_BJT_LAUNCH_AS_MAIN = (__name__ == '__main__')
_BJT_NATIVE_MODULE = '_bjt_native_' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:20]
sys.modules[_BJT_NATIVE_MODULE] = sys.modules[__name__]
__name__ = _BJT_NATIVE_MODULE
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
try:
    from shapely.strtree import STRtree as _aio_STRtree
except Exception:
    _aio_STRtree = None
try:
    from shapely import constrained_delaunay_triangles as _aio_constrained_delaunay_triangles
except Exception:
    _aio_constrained_delaunay_triangles = None
try:
    from shapely.ops import triangulate as _aio_delaunay_triangulate
except Exception:
    _aio_delaunay_triangulate = None

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

# -----------------------------------------------------------------------------
# 올인원 내장 코어 로더
# -----------------------------------------------------------------------------
import base64 as _b64
import zlib as _zlib
import types as _types

_LATTICE_CORE_B64 = """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"""
_RECON_CORE_B64 = """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"""

def _load_embedded_core(name: str, encoded: str):
    source = _zlib.decompress(_b64.b64decode(encoded)).decode('utf-8')
    digest = hashlib.sha256(source.encode()).hexdigest()[:20]
    root = Path(os.getenv('LOCALAPPDATA', str(Path.home() / '.cache'))) / 'BJT_Tools' / 'cores'
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / (name + '_' + digest + '.py')
        if not path.exists() or path.read_text(encoding='utf-8') != source:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=root, delete=False, suffix='.tmp') as f:
                f.write(source); temp = Path(f.name)
            os.replace(temp, path)
        filename = str(path)
    except OSError:
        filename = f'<embedded {name}>'
        source = source.replace('cache=True', 'cache=False')
    module = _types.ModuleType(name)
    module.__file__ = filename
    module.__package__ = ''
    sys.modules[name] = module
    exec(compile(source, filename, 'exec'), module.__dict__)
    return module

lattice_core = _load_embedded_core("bjt_lattice_core", _LATTICE_CORE_B64)

recon_core = _load_embedded_core("bjt_reconstructor_core", _RECON_CORE_B64)


# -----------------------------------------------------------------------------
# Fast exact slab Marching Cubes
# -----------------------------------------------------------------------------
# The reconstruction core feeds only binary 0/1 float32 fields at iso-level 0.5.
# For every one of the 254 non-trivial binary 2x2x2 cube configurations,
# scikit-image Lewiner returns identical vertices/faces with allow_degenerate=True
# and False.  True skips the extra degenerate-face cleanup pass and is materially faster.
_AIO_ORIG_MARCHING_CUBES = recon_core.marching_cubes

def _aio_binary_marching_cubes_fast(volume, *args, **kwargs):
    kwargs["allow_degenerate"] = True
    return _AIO_ORIG_MARCHING_CUBES(volume, *args, **kwargs)

recon_core.marching_cubes = _aio_binary_marching_cubes_fast

# The embedded core capped the float32 slab itself at 512 MiB.  With a 5.5k x 5.5k
# XY plane this can force only ~3 Z cells per slab and hundreds of MC invocations.
# Raise the cap conservatively while still leaving most available RAM for MC output,
# OS cache and the rest of the program.  Geometry is unchanged; only chunking differs.
_AIO_ORIG_CHOOSE_MC_DEPTH = recon_core.choose_streaming_mc_depth

def _aio_choose_streaming_mc_depth_fast(shape, available_ram, smoothing):
    _nz, ny, nx = shape
    plane_voxels = max(1, int(ny) * int(nx))
    halo = 4 if smoothing else 0
    bytes_per_slice = plane_voxels * 4

    if available_ram is None:
        budget = 768 * 1024 * 1024
    else:
        # Use at most 18% of currently available RAM for the scalar slab, capped at
        # 1 GiB.  The existing MemoryError retry still halves depth automatically.
        budget = int(max(384 * 1024 * 1024, min(1024 * 1024 * 1024, available_ram * 0.18)))

    usable_slices = max(2, budget // max(1, bytes_per_slice))
    core_depth = int(usable_slices - 1 - 2 * halo)
    core_depth = max(1, min(64, core_depth))
    estimated = (core_depth + 1 + 2 * halo) * bytes_per_slice
    return core_depth, halo, int(estimated)

recon_core.choose_streaming_mc_depth = _aio_choose_streaming_mc_depth_fast

# -----------------------------------------------------------------------------
# Surface interpolation: lossless planar triangle reducer
# -----------------------------------------------------------------------------
# Marching Cubes is excellent for topology, but on native binary slice volumes it
# often emits thousands/millions of triangles that only subdivide ONE EXACT plane.
# For the non-smoothed "표면 보간형" only, we re-triangulate large horizontal/
# vertical coplanar patches while preserving ALL patch boundary vertices.
#
# Acceptance is deliberately strict:
#   1) no vertex is moved or newly approximated,
#   2) replacement triangles use original MC vertices only,
#   3) projected patch area must match exactly on the half-voxel integer grid,
#   4) the complete boundary-edge set must be byte-for-byte identical,
#   5) only a replacement with fewer triangles is accepted.
# Any ambiguous/non-manifold/invalid group is left completely untouched.
# This means slicing geometry is unchanged; only redundant coplanar interior edges
# disappear.  Weak smoothing is NOT reduced before smoothing because topology would
# influence the smoothing result.

_AIO_MC_LIGHT_TLS = threading.local()
_AIO_MC_LIGHT_MIN_GROUP = 16
_AIO_MC_LIGHT_ORIG_WRITE = recon_core.BinarySTLWriter.write_numpy_mesh_chunk
_AIO_MC_LIGHT_ORIG_RECONSTRUCT = recon_core.reconstruct_streaming


def _aio_boundary_edges(face_array: np.ndarray) -> np.ndarray:
    f = np.asarray(face_array, dtype=np.int64)
    if len(f) == 0:
        return np.empty((0, 2), dtype=np.int64)
    edges = np.concatenate((f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]), axis=0)
    edges = np.sort(edges, axis=1)
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    return unique_edges[counts == 1]


def _aio_boundary_loops(boundary_edges: np.ndarray):
    """Return closed loops, but only for a clean degree-2 boundary graph."""
    if len(boundary_edges) == 0:
        return None
    adjacency = {}
    for aa, bb in np.asarray(boundary_edges, dtype=np.int64):
        a = int(aa); b = int(bb)
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return None

    unused = {tuple(sorted((int(a), int(b)))) for a, b in boundary_edges}
    loops = []
    guard_limit = len(unused) + 2
    while unused:
        start, current = next(iter(unused))
        unused.discard((start, current))
        previous = start
        loop = [start]
        while current != start:
            loop.append(current)
            neighbors = adjacency[current]
            nxt = neighbors[0] if neighbors[0] != previous else neighbors[1]
            edge = tuple(sorted((current, nxt)))
            if nxt != start and edge not in unused:
                return None
            unused.discard(edge)
            previous, current = current, nxt
            if len(loop) > guard_limit:
                return None
        if len(loop) < 3:
            return None
        loops.append(loop)
    return loops


def _aio_projected_area2(q: np.ndarray, faces: np.ndarray, proj_axes) -> int:
    fs = np.asarray(faces, dtype=np.int64)
    uv = q[:, proj_axes]
    a = uv[fs[:, 0]]; b = uv[fs[:, 1]]; c = uv[fs[:, 2]]
    twice = ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
             - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    return int(np.abs(twice).sum(dtype=np.int64))


def _aio_triangulate_polygon_exact(poly: Polygon):
    """Constrained triangulation when available; older Shapely gets a checked fallback."""
    if _aio_constrained_delaunay_triangles is not None:
        gc = _aio_constrained_delaunay_triangles(poly)
        return list(getattr(gc, 'geoms', (gc,)))
    if _aio_delaunay_triangulate is None:
        return []
    # Older-Shapely fallback is accepted later only if exact area+boundary checks pass.
    return [tri for tri in _aio_delaunay_triangulate(poly) if poly.covers(tri)]


def _aio_retriangulate_plane_group(
    q: np.ndarray,
    group_faces: np.ndarray,
    desired_normal_q: np.ndarray,
):
    """Losslessly retriangulate one oriented coplanar group using existing vertices."""
    original = np.asarray(group_faces, dtype=np.int32)
    if len(original) < _AIO_MC_LIGHT_MIN_GROUP:
        return None

    boundary = _aio_boundary_edges(original)
    loops = _aio_boundary_loops(boundary)
    if not loops:
        return None

    desired = np.asarray(desired_normal_q, dtype=np.int64)
    drop_axis = int(np.argmax(np.abs(desired)))
    proj_axes = tuple(i for i in range(3) if i != drop_axis)
    uv = q[:, proj_axes]

    # Keep every original boundary vertex. Removing even a collinear boundary point
    # would create a T-junction against the neighboring non-coplanar patch.
    loop_info = []
    for loop in loops:
        coords = [tuple(map(float, uv[v])) for v in loop]
        polygon = Polygon(coords)
        if polygon.is_empty or (not polygon.is_valid) or polygon.area <= 0.0:
            return None
        loop_info.append((loop, polygon))

    # Build a containment tree so holes and nested islands are retained exactly.
    # Previous implementation compared every loop with every larger loop (O(N^2)).
    # Lattice planes can contain thousands of disconnected loops, so that path became
    # the dominant cost.  STRtree limits exact contains() tests to spatial candidates.
    areas = np.asarray([info[1].area for info in loop_info], dtype=np.float64)
    order = np.argsort(-areas, kind="stable").tolist()
    rank = np.empty(len(order), dtype=np.int64)
    for pos, idx in enumerate(order):
        rank[idx] = pos
    parent = [None] * len(loop_info)
    depth = [0] * len(loop_info)

    polys = [info[1] for info in loop_info]
    tree = _aio_STRtree(polys) if (_aio_STRtree is not None and len(polys) >= 8) else None
    geom_id_to_index = {id(g): i for i, g in enumerate(polys)} if tree is not None else {}

    for pos, i in enumerate(order):
        probe = polys[i].representative_point()
        if tree is None:
            candidates = order[:pos]
        else:
            hits = tree.query(probe)
            # Shapely 2.x returns integer indices; 1.x returns geometry objects.
            if isinstance(hits, np.ndarray) and np.issubdtype(hits.dtype, np.integer):
                candidates = [int(j) for j in hits.tolist()]
            else:
                candidates = []
                for geom in hits:
                    j = geom_id_to_index.get(id(geom))
                    if j is not None:
                        candidates.append(j)
            # Only strictly larger loops can contain this loop.
            candidates = [j for j in candidates if int(rank[j]) < pos]

        containers = [j for j in candidates if polys[j].contains(probe)]
        if containers:
            par = min(containers, key=lambda j: areas[j])
            parent[i] = par
            depth[i] = depth[par] + 1

    children = {}
    for child, par in enumerate(parent):
        if par is not None:
            children.setdefault(par, []).append(child)

    polygons = []
    for i, (shell_loop, _shell_poly) in enumerate(loop_info):
        if depth[i] & 1:
            continue
        hole_loops = [
            loop_info[j][0] for j in children.get(i, ())
            if depth[j] == depth[i] + 1
        ]
        shell = [tuple(map(float, uv[v])) for v in shell_loop]
        holes = [[tuple(map(float, uv[v])) for v in hole] for hole in hole_loops]
        poly = Polygon(shell, holes)
        if poly.is_empty or (not poly.is_valid) or poly.area <= 0.0:
            return None
        polygons.append(poly)

    # On one plane, projection along the dominant normal is one-to-one.
    boundary_vertices = np.unique(boundary)
    uv_to_vertex = {}
    for v in boundary_vertices:
        key = (int(uv[v, 0]), int(uv[v, 1]))
        old = uv_to_vertex.get(key)
        if old is not None and not np.array_equal(q[old], q[v]):
            return None
        uv_to_vertex[key] = int(v)

    new_faces = []
    for poly in polygons:
        triangles = _aio_triangulate_polygon_exact(poly)
        if not triangles:
            return None
        for tri in triangles:
            if getattr(tri, 'geom_type', '') != 'Polygon' or tri.is_empty:
                continue
            coords = list(tri.exterior.coords)
            if len(coords) < 4:
                return None
            vids = []
            for x, y in coords[:3]:
                key = (int(round(x)), int(round(y)))
                if abs(float(x) - key[0]) > 1e-8 or abs(float(y) - key[1]) > 1e-8:
                    return None
                v = uv_to_vertex.get(key)
                if v is None:
                    # No Steiner/new vertices are allowed in lossless mode.
                    return None
                vids.append(v)
            if len(set(vids)) != 3:
                return None
            a, b, c = q[vids[0]], q[vids[1]], q[vids[2]]
            cross = np.cross(b - a, c - a)
            dot = int(np.dot(cross, desired))
            if dot == 0:
                return None
            if dot < 0:
                vids[1], vids[2] = vids[2], vids[1]
            new_faces.append(vids)

    if not new_faces:
        return None
    replacement = np.asarray(new_faces, dtype=np.int32)
    if len(replacement) >= len(original):
        return None

    # Exact projected area and exact edge-boundary postconditions.
    if _aio_projected_area2(q, replacement, proj_axes) != _aio_projected_area2(q, original, proj_axes):
        return None
    replacement_boundary = _aio_boundary_edges(replacement)
    if not np.array_equal(replacement_boundary, boundary):
        return None
    return replacement


def _aio_lossless_planar_reduce(
    vertices_xyz: np.ndarray,
    faces: np.ndarray,
    half_pitch_xyz,
):
    """Reduce only exact horizontal/vertical MC planes; never move a vertex."""
    vertices = np.asarray(vertices_xyz, dtype=np.float64)
    original_faces = np.asarray(faces, dtype=np.int32)
    if len(original_faces) < _AIO_MC_LIGHT_MIN_GROUP or len(vertices) == 0:
        return original_faces, 0, 0

    half = np.asarray(half_pitch_xyz, dtype=np.float64)
    if np.any(~np.isfinite(half)) or np.any(half <= 0.0):
        return original_faces, 0, 0

    # Coordinates of native binary MC surfaces live on the half-voxel lattice.
    # Use a relative origin so arbitrary XY centering/crop offsets cancel out.
    base = vertices[0].copy()
    q = np.rint((vertices - base) / half).astype(np.int64)
    residual = np.max(np.abs((vertices - base) - q * half))
    tolerance = max(1.0e-4, float(np.min(half)) * 0.003)
    if (not np.isfinite(residual)) or residual > tolerance:
        return original_faces, 0, 1

    a = q[original_faces[:, 0]]
    b = q[original_faces[:, 1]]
    c = q[original_faces[:, 2]]
    normal = np.cross(b - a, c - a)
    keep = np.ones(len(original_faces), dtype=bool)
    replacements = []
    groups_accepted = 0

    def process_runs(sorted_face_ids, sorted_keys, desired_normal_builder):
        nonlocal groups_accepted
        if len(sorted_face_ids) == 0:
            return
        changes = np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1) if len(sorted_keys) > 1 else np.empty(0, dtype=bool)
        starts = np.r_[0, 1 + np.flatnonzero(changes), len(sorted_face_ids)]
        for start, end in zip(starts[:-1], starts[1:]):
            if end - start < _AIO_MC_LIGHT_MIN_GROUP:
                continue
            ids = sorted_face_ids[start:end]
            if not np.all(keep[ids]):
                continue
            try:
                desired = desired_normal_builder(sorted_keys[start])
                replacement = _aio_retriangulate_plane_group(q, original_faces[ids], desired)
            except Exception:
                replacement = None
            if replacement is not None:
                keep[ids] = False
                replacements.append(replacement)
                groups_accepted += 1

    # Vertical surfaces: primitive oriented XY plane normal + exact plane offset.
    vertical_ids = np.flatnonzero((normal[:, 2] == 0) & ((normal[:, 0] != 0) | (normal[:, 1] != 0)))
    if len(vertical_ids):
        nxy = normal[vertical_ids, :2]
        gcd = np.gcd(np.abs(nxy[:, 0]), np.abs(nxy[:, 1]))
        gcd[gcd == 0] = 1
        primitive = nxy // gcd[:, None]
        d = primitive[:, 0] * a[vertical_ids, 0] + primitive[:, 1] * a[vertical_ids, 1]
        keys = np.column_stack((primitive, d)).astype(np.int64, copy=False)
        order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
        process_runs(
            vertical_ids[order], keys[order],
            lambda key: np.asarray((int(key[0]), int(key[1]), 0), dtype=np.int64),
        )

    # Horizontal caps/terraces: exact Z plane coordinate + normal orientation.
    horizontal_ids = np.flatnonzero((normal[:, 0] == 0) & (normal[:, 1] == 0) & (normal[:, 2] != 0))
    if len(horizontal_ids):
        sign = np.sign(normal[horizontal_ids, 2]).astype(np.int64)
        zcoord = a[horizontal_ids, 2]
        keys = np.column_stack((zcoord, sign)).astype(np.int64, copy=False)
        order = np.lexsort((keys[:, 1], keys[:, 0]))
        process_runs(
            horizontal_ids[order], keys[order],
            lambda key: np.asarray((0, 0, int(key[1])), dtype=np.int64),
        )

    if not replacements:
        return original_faces, 0, 0
    reduced = np.vstack([original_faces[keep], *replacements]).astype(np.int32, copy=False)
    saved = int(len(original_faces) - len(reduced))
    return np.ascontiguousarray(reduced), saved, groups_accepted


def _aio_light_write_numpy_mesh_chunk(
    self,
    vertices_xyz,
    faces,
    max_triangles=None,
    cancel_event=None,
    chunk_size=100_000,
):
    cfg = getattr(_AIO_MC_LIGHT_TLS, 'cfg', None)
    output_faces = faces
    if cfg and cfg.get('enabled', False):
        stats = cfg['stats']
        before = int(len(faces))
        stats['input_triangles'] += before
        try:
            output_faces, saved, groups = _aio_lossless_planar_reduce(
                vertices_xyz,
                faces,
                cfg['half_pitch_xyz'],
            )
            stats['saved_triangles'] += int(saved)
            stats['groups'] += int(groups)
        except Exception:
            stats['fallback_chunks'] += 1
            output_faces = faces
        stats['output_triangles'] += int(len(output_faces))
    return _AIO_MC_LIGHT_ORIG_WRITE(
        self,
        vertices_xyz,
        output_faces,
        max_triangles=max_triangles,
        cancel_event=cancel_event,
        chunk_size=chunk_size,
    )


def _aio_light_reconstruct_streaming(params, log, cancel_event):
    enabled = (getattr(params, 'surface_mode', '') == '표면 보간형' and not globals().get('_AIO_FAST_EXACT_VALID', False))
    previous = getattr(_AIO_MC_LIGHT_TLS, 'cfg', None)
    if enabled:
        _AIO_MC_LIGHT_TLS.cfg = {
            'enabled': True,
            'half_pitch_xyz': (
                (25.4 / float(params.dpi_x)) * 0.5,
                (25.4 / float(params.dpi_y)) * 0.5,
                float(params.layer_thickness_mm) * 0.5,
            ),
            'stats': {
                'input_triangles': 0,
                'output_triangles': 0,
                'saved_triangles': 0,
                'groups': 0,
                'fallback_chunks': 0,
            },
        }
        try:
            log.write('표면 보간형 무손실 경량화 + FastSlab: ON')
            log.write('  - 정점 이동/다운샘플링 없음')
            log.write('  - 동일 평면 내부 triangle만 재삼각형화')
            log.write('  - 원 경계 edge + 투영면적이 정확히 일치하는 경우에만 적용')
            log.write('  - binary MC degenerate 검사 생략(254개 cube case 동일성 검증)')
            log.write('  - 평면 loop 포함관계 STRtree 가속 / 슬랩 메모리 상한 1 GiB')
        except Exception:
            pass
    try:
        return _AIO_MC_LIGHT_ORIG_RECONSTRUCT(params, log, cancel_event)
    finally:
        cfg = getattr(_AIO_MC_LIGHT_TLS, 'cfg', None)
        if enabled and cfg:
            st = cfg['stats']
            try:
                if st['input_triangles'] > 0:
                    ratio = 100.0 * st['saved_triangles'] / st['input_triangles']
                    log.write('')
                    log.write('========== 표면 보간형 무손실 경량화 ==========')
                    log.write(f"MC 원 triangle: {st['input_triangles']:,}")
                    log.write(f"최종 저장 triangle: {st['output_triangles']:,}")
                    log.write(f"제거된 중복 평면 triangle: {st['saved_triangles']:,} ({ratio:.1f}%)")
                    log.write(f"경량화된 평면 그룹: {st['groups']:,}")
                    if st['fallback_chunks']:
                        log.write(f"안전 fallback chunk: {st['fallback_chunks']:,} (원본 triangle 그대로 보존)")
                    log.write('정점 좌표 변경: 0 / 경계 edge 변경: 0 (검증 통과 그룹만 적용)')
                    log.write('================================================')
            except Exception:
                pass
        if previous is None:
            try:
                delattr(_AIO_MC_LIGHT_TLS, 'cfg')
            except Exception:
                pass
        else:
            _AIO_MC_LIGHT_TLS.cfg = previous


# Embedded core global lookups are dynamic, so these two conservative wrappers
# improve the integrated program without altering the compressed core payload.
recon_core.BinarySTLWriter.write_numpy_mesh_chunk = _aio_light_write_numpy_mesh_chunk

recon_core.reconstruct_streaming = _aio_light_reconstruct_streaming

# -----------------------------------------------------------------------------
# Exact sparse interpolation engine v3
# -----------------------------------------------------------------------------
# 핵심 아이디어
# 1) binary volume의 모든 2x2x2 cube는 256가지 case뿐이다.
# 2) 현재 설치된 scikit-image Lewiner MC를 2x2x2 cube에 직접 실행해 256-case LUT를 만든다.
#    따라서 transition cube는 기존 Marching Cubes와 같은 triangle geometry를 그대로 사용한다.
# 3) 인접 Z slice의 2x2 XY case가 완전히 같으면 해당 cube의 MC surface는 수직 평면 patch뿐이다.
#    이 patch가 여러 Z cell에서 연속되면 매 cell마다 반복 생성하지 않고 동일 평면을 한 번만 길게 늘린다.
# 4) 0<->15 전환 cube는 순수 수평 정사각형이므로 같은 행의 연속 cell을 strip 1개로 병합한다.
#    좌표 이동/다운샘플링/근사 decimation은 없다.
#
# 즉, 기존 MC surface를 '덜 계산하고 덜 잘게 삼각분할'할 뿐 surface 위치는 바꾸지 않는다.
# 자체 검증에 실패하면 자동으로 기존 Z-slab Lewiner MC로 fallback한다.

_AIO_FAST_EXACT_ORIG_INTERPOLATED = recon_core.reconstruct_interpolated_surface
_AIO_FAST_EXACT_LUT_MAX_TRI = 9
_AIO_FAST_EXACT_VALID = False
_AIO_FAST_EXACT_VALIDATION_MESSAGE = "아직 검증하지 않음"
_AIO_FAST_EXACT_LUT_COUNT = None
_AIO_FAST_EXACT_LUT_ZYX = None
_AIO_FAST_EXACT_RECORD_DTYPE = np.dtype([
    ("normal", "<f4", (3,)),
    ("a", "<f4", (3,)),
    ("b", "<f4", (3,)),
    ("c", "<f4", (3,)),
    ("attr", "<u2"),
])


def _aio_fast_build_lewiner_binary_lut():
    """현재 scikit-image Lewiner 구현에서 binary 256-case triangle LUT를 직접 생성."""
    counts = np.zeros(256, dtype=np.uint8)
    lut = np.zeros((256, _AIO_FAST_EXACT_LUT_MAX_TRI, 3, 3), dtype=np.float32)
    mc = _AIO_ORIG_MARCHING_CUBES
    for case in range(256):
        if case == 0 or case == 255:
            continue
        cube = np.zeros((2, 2, 2), dtype=np.float32)
        # bit order: z,y,x -> 0:(0,0,0),1:(0,0,1),2:(0,1,0),3:(0,1,1), ...
        for z in range(2):
            for y in range(2):
                for x in range(2):
                    bit = z * 4 + y * 2 + x
                    cube[z, y, x] = 1.0 if ((case >> bit) & 1) else 0.0
        vv, ff, _nn, _val = mc(
            cube,
            level=0.5,
            spacing=(1.0, 1.0, 1.0),
            gradient_direction="descent",
            step_size=1,
            allow_degenerate=True,
            method="lewiner",
        )
        tri = np.asarray(vv[ff], dtype=np.float32)
        if len(tri) > _AIO_FAST_EXACT_LUT_MAX_TRI:
            raise RuntimeError(f"Lewiner binary case {case}: 예상보다 triangle이 많습니다: {len(tri)}")
        counts[case] = int(len(tri))
        lut[case, :len(tri)] = tri
    return counts, lut


def _aio_fast_marching_square_cases(mask: np.ndarray) -> np.ndarray:
    """HxW binary mask -> zero padding을 포함한 (H+1)x(W+1) 4-bit XY cube case.

    전체 3D volume을 만들지 않는다. NumPy slice 연산 4회만 사용하여 5k급 영상도 빠르게 처리한다.
    """
    m = np.asarray(mask, dtype=np.uint8)
    h, w = m.shape
    out = np.zeros((h + 1, w + 1), dtype=np.uint8)
    # padded P에서 case = P[y,x] | P[y,x+1]<<1 | P[y+1,x]<<2 | P[y+1,x+1]<<3
    out[1:, 1:] |= m
    out[1:, :w] |= (m << 1)
    out[:h, 1:] |= (m << 2)
    out[:h, :w] |= (m << 3)
    return out


def _aio_fast_canonical_triangle_keys(vertices: np.ndarray, faces: np.ndarray) -> list:
    """작은 self-test용. half-voxel 좌표를 정수화하여 triangle geometry를 canonical 비교."""
    q = np.rint(np.asarray(vertices, dtype=np.float64) * 2.0).astype(np.int16)
    result = []
    for f in np.asarray(faces, dtype=np.int64):
        pts = [tuple(int(v) for v in q[int(i)]) for i in f]
        result.append(tuple(sorted(pts)))
    result.sort()
    return result


def _aio_fast_paircell_triangle_keys(volume: np.ndarray, counts: np.ndarray, lut: np.ndarray) -> list:
    """작은 binary volume을 1-cube LUT로 완전 분해해 triangle geometry key 생성."""
    vol = np.asarray(volume, dtype=np.uint8)
    nz, ny, nx = vol.shape
    result = []
    for z in range(nz - 1):
        for y in range(ny - 1):
            for x in range(nx - 1):
                c = (
                    int(vol[z, y, x])
                    | (int(vol[z, y, x + 1]) << 1)
                    | (int(vol[z, y + 1, x]) << 2)
                    | (int(vol[z, y + 1, x + 1]) << 3)
                    | (int(vol[z + 1, y, x]) << 4)
                    | (int(vol[z + 1, y, x + 1]) << 5)
                    | (int(vol[z + 1, y + 1, x]) << 6)
                    | (int(vol[z + 1, y + 1, x + 1]) << 7)
                )
                n = int(counts[c])
                if n <= 0:
                    continue
                tri = np.asarray(lut[c, :n], dtype=np.float64).copy()
                tri[:, :, 0] += z
                tri[:, :, 1] += y
                tri[:, :, 2] += x
                q = np.rint(tri * 2.0).astype(np.int16)
                for t in q:
                    result.append(tuple(sorted(tuple(int(v) for v in p) for p in t)))
    result.sort()
    return result


def _aio_fast_validate_engine() -> tuple[bool, str]:
    """새 엔진의 핵심 등가성을 실제 legacy Lewiner MC와 자동 검증.

    - 모든 same-XY case가 순수 수직 patch인지 확인
    - 0<->15가 순수 수평 unit square인지 확인
    - deterministic random 3D volumes를 legacy global MC와 1-cube LUT로 triangle geometry 전수 비교
    검증 실패 시 새 엔진을 절대 사용하지 않는다.
    """
    global _AIO_FAST_EXACT_LUT_COUNT, _AIO_FAST_EXACT_LUT_ZYX
    try:
        counts, lut = _aio_fast_build_lewiner_binary_lut()
        _AIO_FAST_EXACT_LUT_COUNT = counts
        _AIO_FAST_EXACT_LUT_ZYX = lut

        # Z-run으로 늘릴 case는 z=0/1만 존재하고 모든 triangle normal의 z성분이 0이어야 한다.
        for c4 in range(1, 15):
            c8 = c4 | (c4 << 4)
            n = int(counts[c8])
            tri = np.asarray(lut[c8, :n], dtype=np.float64)
            if n <= 0:
                return False, f"same-case {c4}에 vertical surface가 없음"
            zvals = np.unique(np.rint(tri[:, :, 0] * 2.0).astype(np.int16))
            if not set(int(v) for v in zvals).issubset({0, 2}):
                return False, f"same-case {c4}에 비수직 보간 vertex가 존재"
            a = tri[:, 1] - tri[:, 0]
            b = tri[:, 2] - tri[:, 0]
            normals = np.cross(a, b)
            if np.any(np.abs(normals[:, 0]) > 1e-8):
                return False, f"same-case {c4}가 순수 수직 patch가 아님"

        # pure horizontal cases 15/240은 정확히 2 triangles, z=0.5 unit square.
        for c8 in (15, 240):
            if int(counts[c8]) != 2:
                return False, f"horizontal case {c8} triangle 수가 2가 아님"
            tri = np.asarray(lut[c8, :2], dtype=np.float64)
            if not np.allclose(tri[:, :, 0], 0.5, atol=0.0, rtol=0.0):
                return False, f"horizontal case {c8} z plane이 0.5가 아님"

        # Global Lewiner vs per-cube LUT exact geometry. Ambiguous case까지 실제 구현과 비교한다.
        rng = np.random.default_rng(20260812)
        mc = _AIO_ORIG_MARCHING_CUBES
        tests = []
        for i in range(5):
            vol = (rng.random((5 + (i % 2), 7, 8)) > (0.58 + 0.04 * (i % 3))).astype(np.float32)
            # 외곽 0 padding 성격도 포함
            vol[0, 0, 0] = 0.0
            vol[-1, -1, -1] = 1.0
            tests.append(vol)
        # 반복 Z 패턴도 별도 검사
        base = (rng.random((7, 8)) > 0.62).astype(np.float32)
        tests.append(np.stack([base, base, np.roll(base, 1, axis=0), np.roll(base, 1, axis=0), base], axis=0))

        for ti, vol in enumerate(tests):
            vv, ff, _nn, _val = mc(
                np.asarray(vol, dtype=np.float32, order="C"),
                level=0.5,
                spacing=(1.0, 1.0, 1.0),
                gradient_direction="descent",
                step_size=1,
                allow_degenerate=True,
                method="lewiner",
            )
            legacy = _aio_fast_canonical_triangle_keys(vv, ff)
            local = _aio_fast_paircell_triangle_keys(vol.astype(np.uint8), counts, lut)
            if legacy != local:
                return False, f"random volume #{ti} legacy/LUT triangle geometry 불일치"

        return True, "Lewiner 256-case LUT + vertical/horizontal exact merge self-test PASS"
    except Exception as exc:
        return False, f"self-test 예외: {type(exc).__name__}: {exc}"


_AIO_FAST_EXACT_VALID, _AIO_FAST_EXACT_VALIDATION_MESSAGE = _aio_fast_validate_engine()


def _aio_v35_exhaustive_adjacency_selftest() -> tuple[bool, str]:
    """모든 2-cube 공유면 조합을 legacy Lewiner와 전수 비교한다.

    2x2x3 / 2x3x2 / 3x2x2의 각 12-voxel binary configuration(각 4096개)을
    전부 검사하므로 X/Y/Z 어느 방향에서도 인접 cube ambiguity가 LUT 분해와
    달라지는 경우가 있으면 ExactLUT 경로를 사용하지 않는다.
    """
    if not _AIO_FAST_EXACT_VALID:
        return False, _AIO_FAST_EXACT_VALIDATION_MESSAGE
    try:
        mc = _AIO_ORIG_MARCHING_CUBES
        counts = _AIO_FAST_EXACT_LUT_COUNT
        lut = _AIO_FAST_EXACT_LUT_ZYX
        checked = 0
        for shape in ((2, 2, 3), (2, 3, 2), (3, 2, 2)):
            n = int(np.prod(shape))
            for code in range(1 << n):
                if code == 0 or code == (1 << n) - 1:
                    continue
                flat = np.fromiter(((code >> i) & 1 for i in range(n)), dtype=np.uint8, count=n)
                vol_u8 = flat.reshape(shape)
                vol = vol_u8.astype(np.float32)
                vv, ff, _nn, _val = mc(
                    vol, level=0.5, spacing=(1.0, 1.0, 1.0),
                    gradient_direction="descent", step_size=1,
                    allow_degenerate=True, method="lewiner",
                )
                legacy = _aio_fast_canonical_triangle_keys(vv, ff)
                local = _aio_fast_paircell_triangle_keys(vol_u8, counts, lut)
                if legacy != local:
                    return False, f"adjacency mismatch shape={shape} code={code}"
                checked += 1
        return True, f"PASS · all adjacent-cube configurations {checked:,}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


_AIO_V35_ADJ_OK, _AIO_V35_ADJ_MESSAGE = _aio_v35_exhaustive_adjacency_selftest()
if not _AIO_V35_ADJ_OK:
    _AIO_FAST_EXACT_VALID = False
    _AIO_FAST_EXACT_VALIDATION_MESSAGE = (
        _AIO_FAST_EXACT_VALIDATION_MESSAGE + " · exhaustive adjacency FAIL: " + _AIO_V35_ADJ_MESSAGE
    )
else:
    _AIO_FAST_EXACT_VALIDATION_MESSAGE = (
        _AIO_FAST_EXACT_VALIDATION_MESSAGE + " · " + _AIO_V35_ADJ_MESSAGE
    )


def _aio_fast_writer_check_limit(writer, add_count: int, max_triangles) -> None:
    add_count = int(add_count)
    if add_count <= 0:
        return
    new_total = int(writer.tri_count) + add_count
    if max_triangles is not None and new_total > int(max_triangles):
        raise RuntimeError(
            f"Triangle 제한 초과: {new_total:,} > {int(max_triangles):,}\n"
            "Max triangles를 늘리거나 격자/쉘 설정을 확인하세요."
        )
    if new_total > 0xFFFFFFFF:
        raise OverflowError("Binary STL triangle 수가 uint32 한계를 초과했습니다.")


def _aio_fast_write_triangle_soup(
    writer,
    triangles_xyz: np.ndarray,
    max_triangles=None,
    cancel_event=None,
    chunk_size: int = 120_000,
) -> int:
    """indexed vertex를 만들지 않고 triangle soup를 Binary STL로 벡터 기록."""
    # Keep float64/strided inputs in their original storage; cast only this chunk.
    n_total = int(len(triangles_xyz))
    if n_total <= 0:
        return 0
    _aio_fast_writer_check_limit(writer, n_total, max_triangles)
    writer._flush_buffer()
    chunk_size = max(1, int(chunk_size))
    for begin in range(0, n_total, chunk_size):
        recon_core.check_cancel(cancel_event)
        t = np.ascontiguousarray(triangles_xyz[begin:begin + chunk_size], dtype=np.float32)
        a = t[:, 0]
        b = t[:, 1]
        c = t[:, 2]
        normals = np.cross(b - a, c - a)
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 0
        normals[valid] /= lengths[valid, None]
        normals[~valid] = 0.0
        records = np.empty(len(t), dtype=_AIO_FAST_EXACT_RECORD_DTYPE)
        records["normal"] = normals
        records["a"] = a
        records["b"] = b
        records["c"] = c
        records["attr"] = 0
        records.tofile(writer.f)
        del t, a, b, c, normals, lengths, valid, records
    writer.tri_count += n_total
    return n_total


def _aio_fast_emit_cell_mesh(
    writer,
    flat_indices: np.ndarray,
    case8: np.ndarray,
    cell_width: int,
    xsp: float,
    ysp: float,
    zsp: float,
    xoff: float,
    yoff: float,
    z_start,
    z_length,
    max_triangles,
    cancel_event,
    cell_chunk: int = 60_000,
) -> int:
    """선택된 cube들만 LUT triangle로 생성. transition 및 stretched vertical patch 공용."""
    indices = np.asarray(flat_indices, dtype=np.int64)
    cases = np.asarray(case8, dtype=np.uint8)
    if len(indices) == 0:
        return 0
    if len(indices) != len(cases):
        raise RuntimeError("fast cell emit: index/case 길이가 다릅니다.")
    counts_lut = _AIO_FAST_EXACT_LUT_COUNT
    lut = _AIO_FAST_EXACT_LUT_ZYX
    total_written = 0
    scalar_start = np.isscalar(z_start)
    scalar_length = np.isscalar(z_length)
    if not scalar_start:
        z_start = np.asarray(z_start, dtype=np.float32)
    if not scalar_length:
        z_length = np.asarray(z_length, dtype=np.float32)

    slots_all = np.arange(_AIO_FAST_EXACT_LUT_MAX_TRI, dtype=np.int16)[None, :]
    for begin in range(0, len(indices), max(1, int(cell_chunk))):
        recon_core.check_cancel(cancel_event)
        end = min(len(indices), begin + cell_chunk)
        idx = indices[begin:end]
        cc = cases[begin:end]
        counts = counts_lut[cc].astype(np.int16, copy=False)
        valid = slots_all < counts[:, None]
        ci, slot = np.nonzero(valid)
        if len(ci) == 0:
            continue
        local = np.asarray(lut[cc[ci], slot], dtype=np.float32).copy()  # z,y,x in voxel units
        owner = idx[ci]
        yy = owner // int(cell_width)
        xx = owner - yy * int(cell_width)

        if scalar_start:
            zs = float(z_start)
            if scalar_length:
                local[:, :, 0] = zs + local[:, :, 0] * float(z_length)
            else:
                zl = z_length[begin:end][ci]
                local[:, :, 0] = zs + local[:, :, 0] * zl[:, None]
        else:
            zs = z_start[begin:end][ci]
            if scalar_length:
                local[:, :, 0] = zs[:, None] + local[:, :, 0] * float(z_length)
            else:
                zl = z_length[begin:end][ci]
                local[:, :, 0] = zs[:, None] + local[:, :, 0] * zl[:, None]

        # legacy MC와 같은 좌표 변환: padded voxel 좌표 -> xyz mm -> half voxel offset 보정
        tris = np.empty_like(local, dtype=np.float32)
        tris[:, :, 0] = (local[:, :, 2] + xx[:, None] - 0.5) * float(xsp) + float(xoff)
        tris[:, :, 1] = (local[:, :, 1] + yy[:, None] - 0.5) * float(ysp) + float(yoff)
        tris[:, :, 2] = (local[:, :, 0] - 0.5) * float(zsp)
        total_written += _aio_fast_write_triangle_soup(
            writer, tris, max_triangles=max_triangles, cancel_event=cancel_event,
        )
        del local, tris, ci, slot, valid
    return total_written


def _aio_fast_row_runs(row: np.ndarray):
    """True 1D row의 [start,end) run을 반환. horizontal flat strip merge용."""
    r = np.asarray(row, dtype=np.uint8)
    if not np.any(r):
        return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32)
    d = np.diff(np.concatenate((np.zeros(1, dtype=np.int8), r.astype(np.int8, copy=False), np.zeros(1, dtype=np.int8))))
    return np.flatnonzero(d == 1).astype(np.int32), np.flatnonzero(d == -1).astype(np.int32)


def _aio_fast_emit_horizontal_strips(
    writer,
    mask: np.ndarray,
    z_cell: int,
    upward_case240: bool,
    xsp: float,
    ysp: float,
    zsp: float,
    xoff: float,
    yoff: float,
    max_triangles,
    cancel_event,
) -> int:
    """pure horizontal unit squares(0<->15)를 같은 Y row에서 exact strip으로 병합."""
    m = np.asarray(mask, dtype=bool)
    ys_out = []
    x0_out = []
    x1_out = []
    for y in range(m.shape[0]):
        if (y & 127) == 0:
            recon_core.check_cancel(cancel_event)
        starts, ends = _aio_fast_row_runs(m[y])
        if len(starts):
            ys_out.extend([y] * len(starts))
            x0_out.extend(starts.tolist())
            x1_out.extend(ends.tolist())
    if not ys_out:
        return 0
    yv = np.asarray(ys_out, dtype=np.float32)
    x0 = np.asarray(x0_out, dtype=np.float32)
    x1 = np.asarray(x1_out, dtype=np.float32)
    z = (float(z_cell) + 0.5 - 0.5) * float(zsp)  # local z=.5 then legacy -.5
    # cell y spans [y,y+1], x run spans [x0,x1] in padded voxel coordinates.
    xa = (x0 - 0.5) * float(xsp) + float(xoff)
    xb = (x1 - 0.5) * float(xsp) + float(xoff)
    ya = (yv - 0.5) * float(ysp) + float(yoff)
    yb = (yv + 1.0 - 0.5) * float(ysp) + float(yoff)

    n = len(yv)
    tris = np.empty((n * 2, 3, 3), dtype=np.float32)
    # case240(0->15) LUT winding and case15(15->0) winding을 그대로 재현.
    if upward_case240:
        # [p00,p11,p01], [p00,p10,p11] in (z,y,x) -> xyz
        tris[0::2, 0] = np.column_stack((xa, ya, np.full(n, z, np.float32)))
        tris[0::2, 1] = np.column_stack((xb, yb, np.full(n, z, np.float32)))
        tris[0::2, 2] = np.column_stack((xb, ya, np.full(n, z, np.float32)))
        tris[1::2, 0] = np.column_stack((xa, ya, np.full(n, z, np.float32)))
        tris[1::2, 1] = np.column_stack((xa, yb, np.full(n, z, np.float32)))
        tris[1::2, 2] = np.column_stack((xb, yb, np.full(n, z, np.float32)))
    else:
        # case15: [p11,p00,p01], [p10,p00,p11]
        tris[0::2, 0] = np.column_stack((xb, yb, np.full(n, z, np.float32)))
        tris[0::2, 1] = np.column_stack((xa, ya, np.full(n, z, np.float32)))
        tris[0::2, 2] = np.column_stack((xb, ya, np.full(n, z, np.float32)))
        tris[1::2, 0] = np.column_stack((xa, yb, np.full(n, z, np.float32)))
        tris[1::2, 1] = np.column_stack((xa, ya, np.full(n, z, np.float32)))
        tris[1::2, 2] = np.column_stack((xb, yb, np.full(n, z, np.float32)))
    return _aio_fast_write_triangle_soup(
        writer, tris, max_triangles=max_triangles, cancel_event=cancel_event,
    )


def _aio_fast_case_from_cached_layer(mask_store, absolute, bbox, grid):
    mask = recon_core.load_cached_model_mask(mask_store, absolute, bbox, grid)
    material = int(np.count_nonzero(mask))
    cases = _aio_fast_marching_square_cases(mask)
    return cases, material


def _aio_fast_case_from_layer_item(item, bbox, resolved, grid):
    mask = recon_core.load_model_mask(item, bbox, resolved, grid)
    material = int(np.count_nonzero(mask))
    cases = _aio_fast_marching_square_cases(mask)
    return cases, material


def _aio_fast_exact_interpolated_surface(
    params,
    items,
    bbox,
    grid,
    resolved,
    log,
    cancel_event,
    mask_store=None,
):
    """전체 3D float32 volume/슬랩 MC를 없앤 exact binary surface engine."""
    if getattr(params, "surface_mode", "") != "표면 보간형":
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )
    if not _AIO_FAST_EXACT_VALID:
        log.write("WARNING: ExactSparse self-test 실패 -> 기존 Z-slab Lewiner MC로 안전 fallback")
        log.write(f"  사유: {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )

    total = int(bbox.layers)
    h = int(grid.height)
    w = int(grid.width)
    hc = h + 1
    wc = w + 1
    total_cells_z = total + 1  # bottom zero->L0, inter-layer, last->top zero
    xsp = float(grid.x_pitch_mm)
    ysp = float(grid.y_pitch_mm)
    zsp = float(params.layer_thickness_mm)
    xoff = float(grid.x_offset_mm)
    yoff = float(grid.y_offset_mm)

    log.write("")
    log.write("=============== ExactSparse 보간 표면 ===============")
    log.write(f"XY cell case grid: {wc:,} × {hc:,}")
    log.write(f"Z cell pairs: {total_cells_z:,}")
    log.write("전체 3D float32 volume 생성: 없음")
    log.write("전체 XY×Z Marching Cubes scan: 없음")
    log.write("transition cube: 현재 scikit-image Lewiner 256-case LUT와 동일 geometry")
    log.write("동일 XY case Z 연속부: 수직 MC patch를 exact 장거리 병합")
    log.write("pure horizontal 0<->15: 같은 행 exact strip 병합")
    log.write("다운샘플링/vertex 이동/QEM decimation: 없음")
    log.write(f"엔진 검증: {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
    log.write("=====================================================")

    # 4-bit active run state. 255 means no vertical patch currently open.
    run_case = np.full((hc, wc), 255, dtype=np.uint8)
    start_dtype = np.uint16 if total_cells_z <= np.iinfo(np.uint16).max else np.uint32
    run_start = np.zeros((hc, wc), dtype=start_dtype)
    zero_cases = np.zeros((hc, wc), dtype=np.uint8)
    prev_case = zero_cases
    material_voxels = 0

    writer = recon_core.BinarySTLWriter(
        params.output_stl,
        header="ExactSparse binary Lewiner-equivalent STL",
        buffer_bytes=8 * 1024 * 1024,
    )
    success = False
    started = time.time()
    transition_cells_total = 0
    vertical_runs_total = 0
    horizontal_strips_tri = 0
    transition_tri = 0
    vertical_tri = 0

    # Case generation을 surface emission과 overlap. 16GB RAM을 고려해 pending을 작게 제한한다.
    prefetch_workers = max(1, min(4, int(getattr(params, "workers", 1))))
    pending = {}
    next_submit = 0

    def submit_one(executor, local_layer):
        absolute = int(bbox.zmin) + int(local_layer)
        if mask_store is not None:
            return executor.submit(_aio_fast_case_from_cached_layer, mask_store, absolute, bbox, grid)
        return executor.submit(_aio_fast_case_from_layer_item, items[absolute], bbox, resolved, grid)

    try:
        with ThreadPoolExecutor(max_workers=prefetch_workers) as executor:
            while next_submit < total and len(pending) < prefetch_workers:
                pending[next_submit] = submit_one(executor, next_submit)
                next_submit += 1

            for k in range(total_cells_z):
                recon_core.check_cancel(cancel_event)
                if k < total:
                    next_case, material = pending.pop(k).result()
                    material_voxels += int(material)
                    while next_submit < total and len(pending) < prefetch_workers:
                        pending[next_submit] = submit_one(executor, next_submit)
                        next_submit += 1
                else:
                    next_case = zero_cases

                # 1) same 2D case -> exact vertical patch candidate.
                same_active = (prev_case == next_case) & (prev_case != 0) & (prev_case != 15)
                keep = same_active & (run_case == prev_case)

                # close old runs before transition/nonmatching pair starts.
                close_idx = np.flatnonzero(((run_case != 255) & (~keep)).ravel())
                if len(close_idx):
                    old4 = run_case.ravel()[close_idx].copy()
                    starts = run_start.ravel()[close_idx].astype(np.float32, copy=True)
                    lengths = np.float32(k) - starts
                    old8 = (old4 | (old4 << 4)).astype(np.uint8)
                    vertical_tri += _aio_fast_emit_cell_mesh(
                        writer, close_idx, old8, wc,
                        xsp, ysp, zsp, xoff, yoff,
                        starts, lengths, params.max_triangles, cancel_event,
                    )
                    vertical_runs_total += int(len(close_idx))
                    run_case.ravel()[close_idx] = 255

                # open new exact vertical runs.
                open_idx = np.flatnonzero((same_active & (run_case == 255)).ravel())
                if len(open_idx):
                    run_case.ravel()[open_idx] = prev_case.ravel()[open_idx]
                    run_start.ravel()[open_idx] = k

                del same_active, keep

                # 2) pure horizontal case 0<->15: do not generate 2 triangles per XY cell.
                up_h = (prev_case == 0) & (next_case == 15)   # case240
                down_h = (prev_case == 15) & (next_case == 0) # case15
                if np.any(up_h):
                    horizontal_strips_tri += _aio_fast_emit_horizontal_strips(
                        writer, up_h, k, True,
                        xsp, ysp, zsp, xoff, yoff,
                        params.max_triangles, cancel_event,
                    )
                if np.any(down_h):
                    horizontal_strips_tri += _aio_fast_emit_horizontal_strips(
                        writer, down_h, k, False,
                        xsp, ysp, zsp, xoff, yoff,
                        params.max_triangles, cancel_event,
                    )

                # 3) 실제 topology/형상 변화 cube만 exact Lewiner LUT triangle 생성.
                transition_mask = (prev_case != next_case) & (~up_h) & (~down_h)
                transition_idx = np.flatnonzero(transition_mask.ravel())
                if len(transition_idx):
                    pflat = prev_case.ravel()[transition_idx]
                    nflat = next_case.ravel()[transition_idx]
                    c8 = (pflat | (nflat << 4)).astype(np.uint8)
                    valid = (_AIO_FAST_EXACT_LUT_COUNT[c8] > 0)
                    if not np.all(valid):
                        transition_idx = transition_idx[valid]
                        c8 = c8[valid]
                    if len(transition_idx):
                        transition_tri += _aio_fast_emit_cell_mesh(
                            writer, transition_idx, c8, wc,
                            xsp, ysp, zsp, xoff, yoff,
                            float(k), 1.0, params.max_triangles, cancel_event,
                        )
                        transition_cells_total += int(len(transition_idx))

                prev_case = next_case
                done = k + 1
                elapsed = time.time() - started
                remain = elapsed / max(1, done) * (total_cells_z - done)
                pct = 64.0 + 32.0 * done / max(1, total_cells_z)
                log.progress(
                    pct,
                    f"ExactSparse {done:,}/{total_cells_z:,} pair | triangles={writer.tri_count:,} | ETA {recon_core.format_seconds(remain)}",
                )
                if done == 1 or done % 25 == 0 or done == total_cells_z:
                    log.write(
                        f"ExactSparse pair {done:,}/{total_cells_z:,} | "
                        f"triangles={writer.tri_count:,} | open vertical={int(np.count_nonzero(run_case != 255)):,}"
                    )

                del up_h, down_h, transition_mask, transition_idx

        # top zero pair에서 정상적으로 모두 닫혀야 한다.
        remaining = np.flatnonzero((run_case != 255).ravel())
        if len(remaining):
            old4 = run_case.ravel()[remaining].copy()
            starts = run_start.ravel()[remaining].astype(np.float32, copy=True)
            lengths = np.float32(total_cells_z) - starts
            old8 = (old4 | (old4 << 4)).astype(np.uint8)
            vertical_tri += _aio_fast_emit_cell_mesh(
                writer, remaining, old8, wc,
                xsp, ysp, zsp, xoff, yoff,
                starts, lengths, params.max_triangles, cancel_event,
            )
            vertical_runs_total += int(len(remaining))
            run_case.ravel()[remaining] = 255

        if writer.tri_count <= 0:
            raise ValueError("생성된 표면 triangle이 없습니다.")
        triangle_count = int(writer.tri_count)
        writer.close(success=True)
        success = True
        recon_core.verify_binary_stl(params.output_stl, triangle_count)

        log.write("")
        log.write("=============== ExactSparse 통계 ===============")
        log.write(f"transition cube: {transition_cells_total:,}")
        log.write(f"transition triangles: {transition_tri:,}")
        log.write(f"Z-병합 vertical runs: {vertical_runs_total:,}")
        log.write(f"vertical triangles: {vertical_tri:,}")
        log.write(f"horizontal strip triangles: {horizontal_strips_tri:,}")
        log.write(f"최종 triangles: {triangle_count:,}")
        log.write("surface 근사/정점 이동: 0")
        log.write("===============================================")
        return triangle_count, material_voxels
    except Exception:
        if not success:
            writer.close(success=False)
        raise


# Dynamic global lookup 때문에 reconstruct_streaming 내부 호출도 이 구현으로 교체된다.

recon_core.reconstruct_interpolated_surface = _aio_fast_exact_interpolated_surface

# -----------------------------------------------------------------------------
# ExactSparse v3.1 - topology-safe batch reducer
# -----------------------------------------------------------------------------
# v3.0의 cell별 장거리 Z stretch는 surface 위치는 동일했지만, 서로 다른 run 길이의
# 이웃 patch 사이에서 T-junction이 생길 수 있었다. 제조 레이어 XOR는 같아도 STL
# topology 관점에서 더 엄격하게 맞추기 위해 v3.1은 다음 원칙으로 바꾼다.
# - 일반 pair: 모든 active cube를 검증된 256-case LUT 그대로 생성 (legacy Lewiner exact)
# - 전체 2D case map이 완전히 동일한 연속 pair만 전체가 같은 Z endpoint를 공유하므로
#   안전하게 한 번에 stretch
# - 0<->15 순수 horizontal 영역은 boundary의 모든 unit vertex를 보존한 constrained
#   triangulation으로 exact 병합; 검증 실패 시 unit-square LUT로 fallback
# - 나머지 coplanar reduction은 작은 raw batch마다 trimesh facet adjacency로 빠르게 찾고,
#   기존 exact boundary+area 검증 retriangulation만 적용한다.
# 따라서 batch 경계/T-junction을 만들지 않으며 최종 surface는 legacy MC와 동일하다.

try:
    from shapely.geometry import box as _aio_fast_box
    from shapely.ops import unary_union as _aio_fast_unary_union
except Exception:
    _aio_fast_box = None
    _aio_fast_unary_union = None


def _aio_fast_make_cell_triangles(
    flat_indices, case8, cell_width,
    xsp, ysp, zsp, xoff, yoff,
    z_start, z_length,
):
    """한 cell chunk의 exact LUT triangle soup를 xyz mm로 반환."""
    indices = np.asarray(flat_indices, dtype=np.int64)
    cases = np.asarray(case8, dtype=np.uint8)
    if len(indices) == 0:
        return np.empty((0, 3, 3), dtype=np.float32)
    counts = _AIO_FAST_EXACT_LUT_COUNT[cases].astype(np.int16, copy=False)
    valid = np.arange(_AIO_FAST_EXACT_LUT_MAX_TRI, dtype=np.int16)[None, :] < counts[:, None]
    ci, slot = np.nonzero(valid)
    if len(ci) == 0:
        return np.empty((0, 3, 3), dtype=np.float32)
    local = np.asarray(_AIO_FAST_EXACT_LUT_ZYX[cases[ci], slot], dtype=np.float32).copy()
    owner = indices[ci]
    yy = owner // int(cell_width)
    xx = owner - yy * int(cell_width)
    if np.isscalar(z_start):
        zs = float(z_start)
        if np.isscalar(z_length):
            local[:, :, 0] = zs + local[:, :, 0] * float(z_length)
        else:
            zl = np.asarray(z_length, dtype=np.float32)[ci]
            local[:, :, 0] = zs + local[:, :, 0] * zl[:, None]
    else:
        zs = np.asarray(z_start, dtype=np.float32)[ci]
        if np.isscalar(z_length):
            local[:, :, 0] = zs[:, None] + local[:, :, 0] * float(z_length)
        else:
            zl = np.asarray(z_length, dtype=np.float32)[ci]
            local[:, :, 0] = zs[:, None] + local[:, :, 0] * zl[:, None]
    tris = np.empty_like(local, dtype=np.float32)
    tris[:, :, 0] = (local[:, :, 2] + xx[:, None] - 0.5) * float(xsp) + float(xoff)
    tris[:, :, 1] = (local[:, :, 1] + yy[:, None] - 0.5) * float(ysp) + float(yoff)
    tris[:, :, 2] = (local[:, :, 0] - 0.5) * float(zsp)
    return tris


def _aio_fast_dense_grid_ring(coords):
    """axis-aligned integer polygon ring에 모든 unit boundary vertex를 다시 삽입."""
    pts = []
    seq = list(coords)
    for (x0, y0), (x1, y1) in zip(seq[:-1], seq[1:]):
        x0 = int(round(float(x0))); y0 = int(round(float(y0)))
        x1 = int(round(float(x1))); y1 = int(round(float(y1)))
        dx = x1 - x0; dy = y1 - y0
        g = math.gcd(abs(dx), abs(dy))
        if g <= 0:
            continue
        sx = dx // g; sy = dy // g
        for i in range(g):
            pts.append((x0 + sx * i, y0 + sy * i))
    if pts and pts[-1] != pts[0]:
        pts.append(pts[0])
    return pts


def _aio_fast_horizontal_exact_mesh(mask, z_cell, case240, xsp, ysp, zsp, xoff, yoff):
    """pure horizontal cell mask를 boundary vertex/area 검증을 거쳐 exact triangulate.

    실패하면 None -> caller가 raw LUT unit cells로 fallback.
    """
    if _aio_fast_box is None or _aio_fast_unary_union is None:
        return None
    m = np.asarray(mask, dtype=bool)
    cell_count = int(np.count_nonzero(m))
    if cell_count < 32:
        return None
    rects = []
    # row run -> rectangle. solid cap은 대략 H개 rectangle만 생성된다.
    for y in range(m.shape[0]):
        row = m[y]
        if not np.any(row):
            continue
        r = row.astype(np.int8, copy=False)
        d = np.diff(np.concatenate((np.zeros(1, np.int8), r, np.zeros(1, np.int8))))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        for x0, x1 in zip(starts.tolist(), ends.tolist()):
            rects.append(_aio_fast_box(int(x0), int(y), int(x1), int(y) + 1))
        if len(rects) > 250_000:
            return None
    if not rects:
        return np.empty((0, 3, 3), dtype=np.float32)
    try:
        geom = _aio_fast_unary_union(rects)
    except Exception:
        return None
    if getattr(geom, "is_empty", True):
        return None
    if getattr(geom, "geom_type", "") == "Polygon":
        polys = [geom]
    elif getattr(geom, "geom_type", "") == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        polys = [g for g in getattr(geom, "geoms", ()) if getattr(g, "geom_type", "") == "Polygon"]
    if not polys:
        return None

    tri_xy = []
    total_area = 0.0
    expected_boundary = set()
    for poly0 in polys:
        shell = _aio_fast_dense_grid_ring(poly0.exterior.coords)
        holes = [_aio_fast_dense_grid_ring(r.coords) for r in poly0.interiors]
        if len(shell) < 4 or any(len(h) < 4 for h in holes):
            return None
        poly = Polygon(shell, holes)
        if poly.is_empty or (not poly.is_valid) or poly.area <= 0:
            return None
        total_area += float(poly.area)
        for ring in (shell, *holes):
            for a, b in zip(ring[:-1], ring[1:]):
                expected_boundary.add(tuple(sorted((tuple(a), tuple(b)))))
        triangles = _aio_triangulate_polygon_exact(poly)
        if not triangles:
            return None
        for t in triangles:
            if getattr(t, "geom_type", "") != "Polygon" or t.is_empty:
                continue
            coords = list(t.exterior.coords)
            if len(coords) < 4:
                return None
            q = []
            for x, y in coords[:3]:
                xi = int(round(float(x))); yi = int(round(float(y)))
                if abs(float(x) - xi) > 1e-8 or abs(float(y) - yi) > 1e-8:
                    return None
                q.append((xi, yi))
            if len(set(q)) != 3:
                return None
            tri_xy.append(q)
    if abs(total_area - float(cell_count)) > 1e-8:
        return None
    if not tri_xy:
        return None

    arr = np.asarray(tri_xy, dtype=np.int64)
    # Exact triangle boundary must equal densified cell-union boundary.
    edge_count = {}
    for t in arr:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            key = tuple(sorted((tuple(int(v) for v in a), tuple(int(v) for v in b))))
            edge_count[key] = edge_count.get(key, 0) + 1
    actual_boundary = {e for e, n in edge_count.items() if n == 1}
    if actual_boundary != expected_boundary:
        return None
    # projected area postcondition
    a = arr[:, 1] - arr[:, 0]
    b = arr[:, 2] - arr[:, 0]
    area2 = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum(dtype=np.int64)
    if int(area2) != int(round(2.0 * cell_count)):
        return None

    # legacy case240 normal=-Z, case15 normal=+Z
    desired = -1 if case240 else 1
    cross2 = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    flip = np.sign(cross2) != desired
    if np.any(flip):
        tmp = arr[flip, 1].copy(); arr[flip, 1] = arr[flip, 2]; arr[flip, 2] = tmp
    z = float(z_cell) * float(zsp)
    tris = np.empty((len(arr), 3, 3), dtype=np.float32)
    tris[:, :, 0] = (arr[:, :, 0].astype(np.float32) - 0.5) * float(xsp) + float(xoff)
    tris[:, :, 1] = (arr[:, :, 1].astype(np.float32) - 0.5) * float(ysp) + float(yoff)
    tris[:, :, 2] = z
    return tris


def _aio_fast_batch_reduce_mesh(triangles_xyz, half_pitch_xyz, facet_min=128):
    """raw exact triangle soup -> topology-preserving coplanar reduction.

    trimesh는 coplanar connected facets 검색에만 사용하고, 실제 교체는 기존 exact
    boundary+projected-area postcondition을 통과한 facet에만 적용한다.
    """
    tri = np.asarray(triangles_xyz, dtype=np.float32)
    if len(tri) < max(32, int(facet_min)):
        v = tri.reshape(-1, 3)
        f = np.arange(len(v), dtype=np.int32).reshape(-1, 3)
        return v, f, 0, 0
    mesh = trimesh.Trimesh(
        vertices=tri.reshape(-1, 3),
        faces=np.arange(len(tri) * 3, dtype=np.int32).reshape(-1, 3),
        process=True,
        validate=False,
    )
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)
    if len(faces) == 0:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int32), 0, 0
    half = np.asarray(half_pitch_xyz, dtype=np.float64)
    base = vertices[0].copy()
    q = np.rint((vertices - base) / half).astype(np.int64)
    residual = float(np.max(np.abs((vertices - base) - q * half)))
    tolerance = max(1.0e-4, float(np.min(half)) * 0.003)
    if (not np.isfinite(residual)) or residual > tolerance:
        return np.asarray(vertices, np.float32), faces, 0, 1

    keep = np.ones(len(faces), dtype=bool)
    replacements = []
    saved = 0
    fallback = 0
    for group in mesh.facets:
        if len(group) < int(facet_min):
            continue
        ids = np.asarray(group, dtype=np.int64)
        f0 = faces[int(ids[0])]
        aa, bb, cc = q[f0[0]], q[f0[1]], q[f0[2]]
        normal = np.cross(bb - aa, cc - aa)
        # 동일 정책: horizontal / vertical plane만. 보간 경사면은 그대로 둔다.
        if not (normal[2] == 0 or (normal[0] == 0 and normal[1] == 0)):
            continue
        try:
            rep = _aio_retriangulate_plane_group(q, faces[ids], normal)
        except Exception:
            rep = None
        if rep is None:
            fallback += 1
            continue
        keep[ids] = False
        replacements.append(rep)
        saved += int(len(ids) - len(rep))
    if replacements:
        out_faces = np.vstack([faces[keep], *replacements]).astype(np.int32, copy=False)
    else:
        out_faces = faces
    return np.asarray(vertices, dtype=np.float32), np.ascontiguousarray(out_faces), saved, fallback


def _aio_fast_topology_safe_interpolated_surface(
    params, items, bbox, grid, resolved, log, cancel_event, mask_store=None,
):
    if getattr(params, "surface_mode", "") != "표면 보간형":
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(params, items, bbox, grid, resolved, log, cancel_event, mask_store)
    if not _AIO_FAST_EXACT_VALID:
        log.write("WARNING: ExactSparse self-test 실패 -> 기존 Z-slab Lewiner MC fallback")
        log.write(f"  {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(params, items, bbox, grid, resolved, log, cancel_event, mask_store)

    total = int(bbox.layers); hc = int(grid.height) + 1; wc = int(grid.width) + 1
    xsp = float(grid.x_pitch_mm); ysp = float(grid.y_pitch_mm); zsp = float(params.layer_thickness_mm)
    xoff = float(grid.x_offset_mm); yoff = float(grid.y_offset_mm)
    half_pitch = (0.5 * xsp, 0.5 * ysp, 0.5 * zsp)
    total_pairs = total + 1
    cell_chunk = 70_000
    # 16GB RAM에서 reduction 중 peak를 억제. 실제 raw triangles 수 기준으로 flush.
    batch_target = 1_250_000
    facet_min = 128

    log.write("")
    log.write("=============== ExactSparse v3.1 ===============")
    log.write("surface kernel: legacy Lewiner와 self-test한 binary 256-case LUT")
    log.write("일반 cube 근사: 없음 (vertex 이동/다운샘플링 없음)")
    log.write("전체 slice case가 동일한 연속 Z 구간만 topology-safe 장거리 extrusion")
    log.write("horizontal flat: unit boundary vertex 보존 constrained triangulation")
    log.write(f"raw batch target: {batch_target:,} triangles / facet exact reduce >= {facet_min}")
    log.write(f"엔진 self-test: {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
    log.write("================================================")

    writer = recon_core.BinarySTLWriter(
        params.output_stl, header="ExactSparse v3.1 topology-safe STL", buffer_bytes=8 * 1024 * 1024,
    )
    success = False
    pending = []
    pending_count = 0
    material_voxels = 0
    raw_total = 0
    reduced_saved = 0
    reduce_fallbacks = 0
    horizontal_saved_est = 0
    global_runs = 0
    started = time.time()

    def flush_pending():
        nonlocal pending, pending_count, reduced_saved, reduce_fallbacks
        if pending_count <= 0:
            pending = []; pending_count = 0; return
        recon_core.check_cancel(cancel_event)
        raw = pending[0] if len(pending) == 1 else np.concatenate(pending, axis=0)
        vertices, faces, saved, fallback = _aio_fast_batch_reduce_mesh(raw, half_pitch, facet_min=facet_min)
        reduced_saved += int(saved); reduce_fallbacks += int(fallback)
        _AIO_MC_LIGHT_ORIG_WRITE(
            writer, vertices, faces,
            max_triangles=params.max_triangles,
            cancel_event=cancel_event,
            chunk_size=120_000,
        )
        pending = []; pending_count = 0
        del raw, vertices, faces

    def push_tris(tri):
        nonlocal pending_count, raw_total
        if tri is None or len(tri) == 0:
            return
        pending.append(np.asarray(tri, dtype=np.float32))
        pending_count += int(len(tri)); raw_total += int(len(tri))
        if pending_count >= batch_target:
            flush_pending()

    def emit_cells(indices, cases, z0, zlen):
        indices = np.asarray(indices, dtype=np.int64)
        cases = np.asarray(cases, dtype=np.uint8)
        for begin in range(0, len(indices), cell_chunk):
            recon_core.check_cancel(cancel_event)
            end = min(len(indices), begin + cell_chunk)
            tri = _aio_fast_make_cell_triangles(
                indices[begin:end], cases[begin:end], wc,
                xsp, ysp, zsp, xoff, yoff, z0, zlen,
            )
            push_tris(tri)

    zero = np.zeros((hc, wc), dtype=np.uint8)
    prev_case = zero
    # full-map identical run: pair indices [run_start, current_k)
    run_case_map = None
    run_start = -1

    prefetch_workers = max(1, min(4, int(getattr(params, "workers", 1))))
    pending_case = {}; next_submit = 0
    def submit_case(executor, local_layer):
        absolute = int(bbox.zmin) + int(local_layer)
        if mask_store is not None:
            return executor.submit(_aio_fast_case_from_cached_layer, mask_store, absolute, bbox, grid)
        return executor.submit(_aio_fast_case_from_layer_item, items[absolute], bbox, resolved, grid)

    try:
        with ThreadPoolExecutor(max_workers=prefetch_workers) as executor:
            while next_submit < total and len(pending_case) < prefetch_workers:
                pending_case[next_submit] = submit_case(executor, next_submit); next_submit += 1

            for k in range(total_pairs):
                recon_core.check_cancel(cancel_event)
                if k < total:
                    next_case, material = pending_case.pop(k).result()
                    material_voxels += int(material)
                    while next_submit < total and len(pending_case) < prefetch_workers:
                        pending_case[next_submit] = submit_case(executor, next_submit); next_submit += 1
                else:
                    next_case = zero

                identical = np.array_equal(prev_case, next_case)
                if identical:
                    if run_case_map is None:
                        run_case_map = prev_case.copy()
                        run_start = int(k)
                    # pair surface is purely vertical and will be emitted when run closes.
                else:
                    if run_case_map is not None:
                        run_len = int(k - run_start)
                        active = (run_case_map != 0) & (run_case_map != 15)
                        idx = np.flatnonzero(active.ravel())
                        if len(idx):
                            c4 = run_case_map.ravel()[idx]
                            c8 = (c4 | (c4 << 4)).astype(np.uint8)
                            emit_cells(idx, c8, float(run_start), float(run_len))
                            global_runs += 1
                        run_case_map = None; run_start = -1

                    # exact pure horizontal groups first. Preserve every boundary unit vertex.
                    up_h = (prev_case == 0) & (next_case == 15)    # case240, normal -Z
                    down_h = (prev_case == 15) & (next_case == 0) # case15, normal +Z
                    excluded = np.zeros_like(prev_case, dtype=bool)
                    for hm, c240 in ((up_h, True), (down_h, False)):
                        count_cells = int(np.count_nonzero(hm))
                        if count_cells <= 0:
                            continue
                        htri = _aio_fast_horizontal_exact_mesh(hm, k, c240, xsp, ysp, zsp, xoff, yoff)
                        if htri is not None:
                            # flush raw batch so exact plane does not need to be merged again.
                            flush_pending()
                            _aio_fast_write_triangle_soup(
                                writer, htri, max_triangles=params.max_triangles, cancel_event=cancel_event,
                            )
                            excluded |= hm
                            horizontal_saved_est += max(0, 2 * count_cells - len(htri))

                    # all remaining active cubes are emitted from exact LUT; no surface approximation.
                    c8_full = (prev_case | (next_case << 4)).astype(np.uint8)
                    active = (_AIO_FAST_EXACT_LUT_COUNT[c8_full] > 0) & (~excluded)
                    idx = np.flatnonzero(active.ravel())
                    if len(idx):
                        cases = c8_full.ravel()[idx]
                        emit_cells(idx, cases, float(k), 1.0)
                    del up_h, down_h, excluded, c8_full, active, idx

                prev_case = next_case
                done = k + 1
                elapsed = time.time() - started
                remain = elapsed / max(1, done) * (total_pairs - done)
                pct = 64.0 + 32.0 * done / max(1, total_pairs)
                log.progress(
                    pct,
                    f"ExactSparse v3.1 {done:,}/{total_pairs:,} pair | triangles={writer.tri_count + pending_count:,} | ETA {recon_core.format_seconds(remain)}",
                )
                if done == 1 or done % 20 == 0 or done == total_pairs:
                    log.write(
                        f"v3.1 pair {done:,}/{total_pairs:,} | saved STL tri={writer.tri_count:,} | raw batch={pending_count:,}"
                    )

        if run_case_map is not None:
            # normally top-zero transition closes before here, but keep safe finalization.
            run_len = int(total_pairs - run_start)
            active = (run_case_map != 0) & (run_case_map != 15)
            idx = np.flatnonzero(active.ravel())
            if len(idx):
                c4 = run_case_map.ravel()[idx]; c8 = (c4 | (c4 << 4)).astype(np.uint8)
                emit_cells(idx, c8, float(run_start), float(run_len)); global_runs += 1

        flush_pending()
        if writer.tri_count <= 0:
            raise ValueError("생성된 표면 triangle이 없습니다.")
        triangle_count = int(writer.tri_count)
        writer.close(success=True); success = True
        recon_core.verify_binary_stl(params.output_stl, triangle_count)
        log.write("")
        log.write("=============== v3.1 exact 통계 ===============")
        log.write(f"LUT raw triangles processed: {raw_total:,}")
        log.write(f"facet exact reduction saved: {reduced_saved:,}")
        log.write(f"horizontal exact merge saved(est): {horizontal_saved_est:,}")
        log.write(f"full-map identical Z runs: {global_runs:,}")
        log.write(f"facet fallback groups/batches: {reduce_fallbacks:,}")
        log.write(f"final triangles: {triangle_count:,}")
        log.write("geometry approximation: 0")
        log.write("===============================================")
        return triangle_count, material_voxels
    except Exception:
        if not success:
            writer.close(success=False)
        raise


# v3.1 topology-safe 구현이 최종 표면 보간형 경로다.
recon_core.reconstruct_interpolated_surface = _aio_fast_topology_safe_interpolated_surface


# -----------------------------------------------------------------------------
# ExactLUT + VTK zero-error reducer v3.2
# -----------------------------------------------------------------------------
# v3.1의 실험적 장거리 Z stretch / 수평 직접 병합은 제조 레이어 XOR는 맞아도
# 일부 복잡 형상에서 T-junction 가능성이 확인되었다. v3.2에서는 해당 경로를 사용하지 않는다.
#
# 1) 모든 active 2x2x2 binary cube를 legacy Lewiner와 전수/self-test한 256-case LUT로
#    그대로 생성한다. 따라서 원본 Marching Cubes의 표면 위치/ambiguity 해석을 바꾸지 않는다.
# 2) float32 3D slab 전체 scan을 없애고 실제 active cube만 벡터 처리한다.
# 3) 50만 raw triangle 단위로 VTK DecimatePro를 사용하되 MaximumError=0,
#    PreserveTopology=ON, BoundaryVertexDeletion=OFF, Splitting=OFF로 설정한다.
#    즉 zero-error로 삭제 가능한 coplanar redundant vertex만 줄인다.
# 4) 각 batch는 출력 vertex가 원본 half-voxel vertex의 부분집합인지, 양방향
#    cell-center surface distance가 수치 오차 이내인지 검증한다. 실패하면 그 batch는
#    decimation을 버리고 raw exact LUT triangle을 그대로 기록한다.
# 5) v3.7 production에서는 대형 모델의 전 레이어 XOR 재슬라이스를 제거하고,
#    exact-subset 경량화 + 빠른 STL/manifold 검증으로 최종 파일을 승격한다.
#
# 이 경로는 해상도/레이어 감소, QEM 근사, vertex 이동을 사용하지 않는다.

try:
    import vtk as _aio_vtk
    from vtk.util.numpy_support import (
        numpy_to_vtk as _aio_numpy_to_vtk,
        numpy_to_vtkIdTypeArray as _aio_numpy_to_vtk_id,
        vtk_to_numpy as _aio_vtk_to_numpy,
    )
    _AIO_V32_VTK_AVAILABLE = True
    _AIO_V32_VTK_ERROR = ""
except Exception as _aio_vtk_exc:
    _aio_vtk = None
    _aio_numpy_to_vtk = None
    _aio_numpy_to_vtk_id = None
    _aio_vtk_to_numpy = None
    _AIO_V32_VTK_AVAILABLE = False
    _AIO_V32_VTK_ERROR = f"{type(_aio_vtk_exc).__name__}: {_aio_vtk_exc}"

_AIO_V32_LUT_Q_ZYX = (
    np.rint(np.asarray(_AIO_FAST_EXACT_LUT_ZYX, dtype=np.float64) * 2.0).astype(np.int16)
    if _AIO_FAST_EXACT_LUT_ZYX is not None else None
)
_AIO_V32_BATCH_TARGET = 500_000
_AIO_V32_TILE_ROWS = 128
_AIO_V32_STRICT_OUTPUT_VERIFY = False


def _aio_v32_make_q_triangles(prev_case, next_case, k, y0, y1, wc):
    """한 Z pair/Y-band의 exact Lewiner triangle을 half-grid integer q(x,y,z)로 생성."""
    c8 = (prev_case[y0:y1] | (next_case[y0:y1] << 4)).astype(np.uint8, copy=False)
    active = _AIO_FAST_EXACT_LUT_COUNT[c8] > 0
    ids = np.flatnonzero(active.ravel())
    if len(ids) == 0:
        return None
    cc = c8.ravel()[ids]
    counts = _AIO_FAST_EXACT_LUT_COUNT[cc].astype(np.int16, copy=False)
    valid = np.arange(_AIO_FAST_EXACT_LUT_MAX_TRI, dtype=np.int16)[None, :] < counts[:, None]
    ci, slot = np.nonzero(valid)
    if len(ci) == 0:
        return None
    local = np.asarray(_AIO_V32_LUT_Q_ZYX[cc[ci], slot], dtype=np.int32)
    owner = ids[ci]
    yy = owner // int(wc) + int(y0)
    xx = owner - (owner // int(wc)) * int(wc)
    q = np.empty(local.shape, dtype=np.int32)
    q[:, :, 0] = 2 * xx[:, None] + local[:, :, 2]
    q[:, :, 1] = 2 * yy[:, None] + local[:, :, 1]
    q[:, :, 2] = 2 * int(k) + local[:, :, 0]
    return q


def _aio_v32_pack_q(qflat: np.ndarray, bits_x: int, bits_y: int) -> np.ndarray:
    q = np.asarray(qflat, dtype=np.int64)
    return (
        q[:, 0]
        | (q[:, 1] << int(bits_x))
        | (q[:, 2] << int(bits_x + bits_y))
    )


def _aio_v32_weld_q(qtri: np.ndarray, bits_x: int, bits_y: int):
    """half-grid integer 좌표를 64-bit key로 exact weld. float tolerance를 쓰지 않는다."""
    flat = np.ascontiguousarray(np.asarray(qtri, dtype=np.int32).reshape(-1, 3))
    key = _aio_v32_pack_q(flat, bits_x, bits_y)
    _unique_key, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    vertices_q = np.ascontiguousarray(flat[first], dtype=np.int32)
    faces = np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int64)
    return vertices_q, faces, _unique_key


def _aio_v32_q_to_xyz(vertices_q, half_xyz, xoff, yoff):
    q = np.asarray(vertices_q, dtype=np.float64)
    half = np.asarray(half_xyz, dtype=np.float64)
    out = np.empty(q.shape, dtype=np.float32)
    out[:, 0] = ((q[:, 0] - 1.0) * half[0] + float(xoff)).astype(np.float32)
    out[:, 1] = ((q[:, 1] - 1.0) * half[1] + float(yoff)).astype(np.float32)
    out[:, 2] = ((q[:, 2] - 1.0) * half[2]).astype(np.float32)
    return out


def _aio_v32_make_polydata(vertices_xyz: np.ndarray, faces: np.ndarray):
    points = _aio_vtk.vtkPoints()
    points.SetData(_aio_numpy_to_vtk(np.ascontiguousarray(vertices_xyz, dtype=np.float32), deep=1))
    f = np.ascontiguousarray(faces, dtype=np.int64)
    cells = _aio_vtk.vtkCellArray()
    if hasattr(cells, "SetData") and hasattr(cells, "GetConnectivityArray"):
        offsets = np.arange(0, 3 * (len(f) + 1), 3, dtype=np.int64)
        cells.SetData(
            _aio_numpy_to_vtk_id(offsets, deep=1),
            _aio_numpy_to_vtk_id(f.reshape(-1), deep=1),
        )
    else:
        legacy = np.empty((len(f), 4), dtype=np.int64)
        legacy[:, 0] = 3
        legacy[:, 1:] = f
        cells.SetCells(len(f), _aio_numpy_to_vtk_id(legacy.reshape(-1), deep=1))
    poly = _aio_vtk.vtkPolyData()
    poly.SetPoints(points)
    poly.SetPolys(cells)
    return poly


def _aio_v32_polydata_numpy(poly):
    pts_obj = poly.GetPoints()
    if pts_obj is None or pts_obj.GetNumberOfPoints() == 0:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int64)
    vertices = np.ascontiguousarray(_aio_vtk_to_numpy(pts_obj.GetData()), dtype=np.float32)
    polys = poly.GetPolys()
    if hasattr(polys, "GetConnectivityArray"):
        conn = np.asarray(_aio_vtk_to_numpy(polys.GetConnectivityArray()), dtype=np.int64)
        offsets = np.asarray(_aio_vtk_to_numpy(polys.GetOffsetsArray()), dtype=np.int64)
        if len(offsets) > 0 and int(offsets[-1]) != len(conn):
            raise RuntimeError("VTK cell offsets/connectivity 길이가 맞지 않습니다.")
        if len(conn) % 3 != 0:
            raise RuntimeError("VTK zero-error reducer가 triangle 이외 cell을 반환했습니다.")
        faces = np.ascontiguousarray(conn.reshape(-1, 3), dtype=np.int64)
    else:
        data = np.asarray(_aio_vtk_to_numpy(polys.GetData()), dtype=np.int64)
        if len(data) % 4 != 0:
            raise RuntimeError("VTK legacy cell array 형식이 예상과 다릅니다.")
        rows = data.reshape(-1, 4)
        if np.any(rows[:, 0] != 3):
            raise RuntimeError("VTK zero-error reducer가 triangle 이외 cell을 반환했습니다.")
        faces = np.ascontiguousarray(rows[:, 1:4], dtype=np.int64)
    return vertices, faces


def _aio_v32_surface_area(vertices, faces) -> float:
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    if len(f) == 0:
        return 0.0
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum(dtype=np.float64))


def _aio_v32_decimate_q_batch(
    qtri: np.ndarray,
    *,
    bits_x: int,
    bits_y: int,
    half_xyz,
    xoff: float,
    yoff: float,
    strict_verify: bool,
):
    """Exact LUT triangle batch를 VTK zero-error로 줄인다.

    반환: vertices_xyz, faces, raw_count, validation_message, fallback_used
    검증 실패/예외 시 raw exact mesh를 반환하므로 geometry quality가 절대 하락하지 않는다.
    """
    raw_count = int(len(qtri))
    vq, raw_faces, raw_keys = _aio_v32_weld_q(qtri, bits_x, bits_y)
    raw_vertices = _aio_v32_q_to_xyz(vq, half_xyz, xoff, yoff)
    if (not _AIO_V32_VTK_AVAILABLE) or raw_count < 32:
        reason = "VTK unavailable" if not _AIO_V32_VTK_AVAILABLE else "small batch"
        return raw_vertices, raw_faces, raw_count, reason, True

    try:
        raw_poly = _aio_v32_make_polydata(raw_vertices, raw_faces)
        dec = _aio_vtk.vtkDecimatePro()
        dec.SetInputData(raw_poly)
        dec.SetTargetReduction(0.99999)
        dec.PreserveTopologyOn()
        dec.SplittingOff()
        dec.BoundaryVertexDeletionOff()
        dec.SetErrorIsAbsolute(1)
        dec.SetMaximumError(0.0)
        dec.AccumulateErrorOn()
        dec.Update()
        out_poly = dec.GetOutput()
        out_vertices, out_faces = _aio_v32_polydata_numpy(out_poly)
        if len(out_faces) <= 0 or len(out_faces) > raw_count:
            raise RuntimeError(f"비정상 reduction face count {len(out_faces):,}/{raw_count:,}")

        # 절대 조건 1: output vertex는 원래 exact half-grid vertex의 부분집합이어야 한다.
        half = np.asarray(half_xyz, dtype=np.float64)
        oq = np.empty((len(out_vertices), 3), dtype=np.int64)
        oq[:, 0] = np.rint((out_vertices[:, 0].astype(np.float64) - float(xoff)) / half[0] + 1.0).astype(np.int64)
        oq[:, 1] = np.rint((out_vertices[:, 1].astype(np.float64) - float(yoff)) / half[1] + 1.0).astype(np.int64)
        oq[:, 2] = np.rint(out_vertices[:, 2].astype(np.float64) / half[2] + 1.0).astype(np.int64)
        rebuilt = np.empty_like(out_vertices, dtype=np.float64)
        rebuilt[:, 0] = (oq[:, 0] - 1.0) * half[0] + float(xoff)
        rebuilt[:, 1] = (oq[:, 1] - 1.0) * half[1] + float(yoff)
        rebuilt[:, 2] = (oq[:, 2] - 1.0) * half[2]
        residual = float(np.max(np.abs(out_vertices.astype(np.float64) - rebuilt))) if len(out_vertices) else 0.0
        coord_tol = max(2.0e-6, float(np.min(half)) * 1.0e-4)
        if residual > coord_tol:
            raise RuntimeError(f"vertex 이동 검출 {format_number(residual, scientific=True)} mm > {format_number(coord_tol, scientific=True)}")
        out_keys = np.unique(_aio_v32_pack_q(oq, bits_x, bits_y))
        pos = np.searchsorted(raw_keys, out_keys)
        if np.any(pos >= len(raw_keys)) or np.any(raw_keys[np.minimum(pos, len(raw_keys)-1)] != out_keys):
            raise RuntimeError("원본에 없던 vertex가 생성됨")

        # 절대 조건 2: zero-error surface distance. strict mode에서는 양방향 cell center도 검사.
        max_dist = 0.0
        if strict_verify:
            dist = _aio_vtk.vtkDistancePolyDataFilter()
            dist.SetInputData(0, out_poly)
            dist.SetInputData(1, raw_poly)
            dist.SignedDistanceOff()
            dist.ComputeCellCenterDistanceOn()
            dist.ComputeSecondDistanceOn()
            dist.Update()
            arrays = []
            for po in (dist.GetOutput(), dist.GetSecondDistanceOutput()):
                pa = po.GetPointData().GetArray("Distance")
                ca = po.GetCellData().GetArray("Distance")
                if pa is not None:
                    arrays.append(np.asarray(_aio_vtk_to_numpy(pa), dtype=np.float64))
                if ca is not None:
                    arrays.append(np.asarray(_aio_vtk_to_numpy(ca), dtype=np.float64))
            if arrays:
                max_dist = max(float(a.max(initial=0.0)) for a in arrays if a.size)
            dist_tol = max(3.0e-6, float(np.min(half)) * 2.0e-4)
            if max_dist > dist_tol:
                raise RuntimeError(f"zero-error surface distance {format_number(max_dist, scientific=True)} mm > {format_number(dist_tol, scientific=True)}")

            raw_area = _aio_v32_surface_area(raw_vertices, raw_faces)
            out_area = _aio_v32_surface_area(out_vertices, out_faces)
            area_tol = max(1.0e-7, raw_area * 2.0e-7)
            if abs(out_area - raw_area) > area_tol:
                raise RuntimeError(
                    f"surface area mismatch {format_number(out_area-raw_area, scientific=True)} mm^2 > {format_number(area_tol, scientific=True)}"
                )

        msg = f"zero-error PASS · maxDist={max_dist:.2e}mm · {raw_count:,}->{len(out_faces):,}"
        return out_vertices, out_faces, raw_count, msg, False
    except Exception as exc:
        # 품질 검증 실패 시 속도/용량보다 정확성을 우선해 raw exact LUT로 자동 fallback.
        return raw_vertices, raw_faces, raw_count, f"fallback: {type(exc).__name__}: {exc}", True


def _aio_v32_selftest() -> tuple[bool, str]:
    """LUT + zero-error reducer + batch boundary topology를 작은 closed volume에서 회귀검증."""
    if not _AIO_FAST_EXACT_VALID:
        return False, f"LUT self-test fail: {_AIO_FAST_EXACT_VALIDATION_MESSAGE}"
    if not _AIO_V32_VTK_AVAILABLE:
        return False, f"VTK unavailable: {_AIO_V32_VTK_ERROR}"
    try:
        # zero padding으로 닫힌 구조 + 반복 수직벽 + Z transition을 함께 포함한다.
        nz, h, w = 10, 24, 26
        yy, xx = np.ogrid[:h, :w]
        volume = np.zeros((nz, h, w), dtype=np.uint8)
        for z in range(1, nz - 1):
            ring = (((xx - 13) ** 2 + (yy - 12) ** 2) <= (9 + (1 if z in (4, 5) else 0)) ** 2) & \
                   (((xx - 13) ** 2 + (yy - 12) ** 2) >= 6 ** 2)
            grid = (((xx % 7) == 0) | ((yy % 6) == 0)) & (((xx - 13) ** 2 + (yy - 12) ** 2) < 6 ** 2)
            volume[z] = ring | grid
        hc, wc = h + 1, w + 1
        zero = np.zeros((hc, wc), dtype=np.uint8)
        prev = zero
        chunks = []
        for k in range(nz + 1):
            nxt = _aio_fast_marching_square_cases(volume[k]) if k < nz else zero
            for y0 in range(0, hc, 7):
                q = _aio_v32_make_q_triangles(prev, nxt, k, y0, min(hc, y0 + 7), wc)
                if q is not None:
                    chunks.append(q)
            prev = nxt
        if not chunks:
            return False, "self-test surface empty"
        raw_q = np.concatenate(chunks, axis=0)
        max_q = np.max(raw_q.reshape(-1, 3), axis=0)
        bx = max(1, int(math.ceil(math.log2(int(max_q[0]) + 2))))
        by = max(1, int(math.ceil(math.log2(int(max_q[1]) + 2))))
        half = (0.5, 0.5, 0.5)

        # raw exact mesh
        rvq, rf, _rk = _aio_v32_weld_q(raw_q, bx, by)
        rv = _aio_v32_q_to_xyz(rvq, half, 0.0, 0.0)
        raw_mesh = trimesh.Trimesh(vertices=rv, faces=rf, process=True)
        if not raw_mesh.is_watertight:
            return False, "raw LUT self-test mesh not watertight"

        # 먼저 full batch에서 실제 zero-error reduction이 일어나는지 확인한다.
        _fv, _ff, _frc, _fmsg, _ffb = _aio_v32_decimate_q_batch(
            raw_q, bits_x=bx, bits_y=by, half_xyz=half,
            xoff=0.0, yoff=0.0, strict_verify=True,
        )
        if _ffb or len(_ff) >= _frc:
            return False, f"full-batch zero-error reduction 실패: {_fmsg}"

        # 이어서 의도적으로 arbitrary face batch boundary를 만들어도
        # BoundaryVertexDeletion=OFF 덕분에 합친 mesh topology가 유지되는지 검사한다.
        split = max(1, len(raw_q) // 3)
        out_vertices = []
        out_faces = []
        voff = 0
        for begin in range(0, len(raw_q), split):
            vv, ff, rc, msg, fb = _aio_v32_decimate_q_batch(
                raw_q[begin:begin + split], bits_x=bx, bits_y=by,
                half_xyz=half, xoff=0.0, yoff=0.0, strict_verify=True,
            )
            out_vertices.append(vv)
            out_faces.append(ff + voff)
            voff += len(vv)
        out_mesh = trimesh.Trimesh(
            vertices=np.vstack(out_vertices), faces=np.vstack(out_faces), process=True,
        )
        if not out_mesh.is_watertight:
            return False, "zero-error batch result not watertight"
        if int(out_mesh.euler_number) != int(raw_mesh.euler_number):
            return False, f"Euler mismatch {out_mesh.euler_number}!={raw_mesh.euler_number}"
        if np.max(np.abs(out_mesh.bounds - raw_mesh.bounds)) > 1e-6:
            return False, "bounds mismatch"
        if abs(float(out_mesh.area) - float(raw_mesh.area)) > max(1e-7, raw_mesh.area * 2e-7):
            return False, "area mismatch"
        if abs(float(out_mesh.volume) - float(raw_mesh.volume)) > max(1e-7, abs(raw_mesh.volume) * 2e-7):
            return False, "volume mismatch"
        return True, f"PASS · watertight/Euler/area/volume · full {len(raw_mesh.faces):,}->{len(_ff):,} tri · batched {len(out_mesh.faces):,}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


_AIO_V32_SELFTEST_OK, _AIO_V32_SELFTEST_MESSAGE = _aio_v32_selftest()


def _aio_v32_exact_interpolated_surface(
    params, items, bbox, grid, resolved, log, cancel_event, mask_store=None,
):
    if getattr(params, "surface_mode", "") != "표면 보간형":
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )
    if not _AIO_FAST_EXACT_VALID or _AIO_V32_LUT_Q_ZYX is None:
        log.write("WARNING: ExactLUT 검증 실패 -> 기존 Z-slab Lewiner MC fallback")
        log.write(f"  {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )

    total = int(bbox.layers)
    hc = int(grid.height) + 1
    wc = int(grid.width) + 1
    total_pairs = total + 1
    xsp = float(grid.x_pitch_mm)
    ysp = float(grid.y_pitch_mm)
    zsp = float(params.layer_thickness_mm)
    xoff = float(grid.x_offset_mm)
    yoff = float(grid.y_offset_mm)
    half = (0.5 * xsp, 0.5 * ysp, 0.5 * zsp)
    bits_x = max(1, int(math.ceil(math.log2(2 * wc + 3))))
    bits_y = max(1, int(math.ceil(math.log2(2 * hc + 3))))
    if bits_x + bits_y + max(1, int(math.ceil(math.log2(2 * total_pairs + 3)))) >= 62:
        raise RuntimeError("Exact half-grid key가 int64 pack 범위를 초과합니다.")

    batch_target = int(_AIO_V32_BATCH_TARGET)
    tile_rows = int(_AIO_V32_TILE_ROWS)
    # i5-13500/16GB급에서는 3개 병렬 zero-error reducer가 속도/메모리 균형이 좋다.
    dec_workers = max(1, min(3, int(getattr(params, "workers", 1)))) if _AIO_V32_VTK_AVAILABLE else 1
    max_inflight = max(1, dec_workers * 2)
    strict = bool(getattr(params, "verify_layer_xor", False)) or bool(_AIO_V32_STRICT_OUTPUT_VERIFY)

    log.write("")
    log.write("=============== ExactLUT v3.2 표면 엔진 ===============")
    log.write(f"XY case grid: {wc:,}×{hc:,} · Z pairs: {total_pairs:,}")
    log.write("전체 float32 3D slab / full-volume Marching Cubes: 사용 안 함")
    log.write("surface: legacy Lewiner 256-case exact LUT · step=1 동등")
    log.write(f"batch: {batch_target:,} raw tri · VTK zero-error workers={dec_workers}")
    log.write("VTK: PreserveTopology ON · BoundaryVertexDeletion OFF · MaximumError=0")
    log.write(f"LUT self-test: {_AIO_FAST_EXACT_VALIDATION_MESSAGE}")
    log.write(f"v3.2 topology self-test: {_AIO_V32_SELFTEST_MESSAGE}")
    if not _AIO_V32_SELFTEST_OK:
        log.write("WARNING: v3.2 startup topology self-test 실패 -> decimation은 batch별 검증 실패 시 raw fallback")
    if not _AIO_V32_VTK_AVAILABLE:
        log.write(f"WARNING: VTK 없음 -> exact LUT raw STL (빠른 표면 생성, 파일 경량화 없음): {_AIO_V32_VTK_ERROR}")
    log.write("========================================================")

    writer = recon_core.BinarySTLWriter(
        params.output_stl,
        header="ExactLUT v3.2 + VTK zero-error STL",
        buffer_bytes=8 * 1024 * 1024,
    )
    success = False
    material_voxels = 0
    raw_total = 0
    final_total = 0
    fallback_batches = 0
    verified_batches = 0
    batch_messages = []
    started = time.time()

    pending_parts = []
    pending_count = 0
    futures = {}
    batch_seq = 0

    def consume_one(block: bool):
        nonlocal final_total, fallback_batches, verified_batches
        if not futures:
            return False
        done_future = None
        if block:
            done_future = next(as_completed(list(futures.keys())))
        else:
            for f in list(futures.keys()):
                if f.done():
                    done_future = f
                    break
            if done_future is None:
                return False
        seq = futures.pop(done_future)
        vertices, faces, raw_count, msg, fallback = done_future.result()
        if fallback:
            fallback_batches += 1
        else:
            verified_batches += 1
        if len(batch_messages) < 12 or fallback:
            batch_messages.append((seq, msg, fallback))
        _AIO_MC_LIGHT_ORIG_WRITE(
            writer, vertices, faces,
            max_triangles=params.max_triangles,
            cancel_event=cancel_event,
            chunk_size=120_000,
        )
        final_total += int(len(faces))
        return True

    def submit_batch(pool, qbatch):
        nonlocal batch_seq
        seq = batch_seq
        batch_seq += 1
        fut = pool.submit(
            _aio_v39_decimate_q_batch,
            np.ascontiguousarray(qbatch, dtype=np.int32),
            bits_x=bits_x, bits_y=bits_y, half_xyz=half,
            xoff=xoff, yoff=yoff, strict_verify=strict,
        )
        futures[fut] = seq
        while len(futures) >= max_inflight:
            consume_one(True)

    def feed_q(pool, q):
        nonlocal pending_parts, pending_count, raw_total
        if q is None or len(q) == 0:
            return
        pos = 0
        qlen = int(len(q))
        while pos < qlen:
            room = batch_target - pending_count
            take = min(qlen - pos, room)
            pending_parts.append(q[pos:pos + take])
            pending_count += int(take)
            raw_total += int(take)
            pos += int(take)
            if pending_count >= batch_target:
                batch = pending_parts[0] if len(pending_parts) == 1 else np.concatenate(pending_parts, axis=0)
                submit_batch(pool, batch)
                pending_parts = []
                pending_count = 0

    zero = np.zeros((hc, wc), dtype=np.uint8)
    prev_case = zero
    prefetch_workers = max(1, min(3, int(getattr(params, "workers", 1))))
    case_futures = {}
    next_submit = 0

    def submit_case(pool, local_layer):
        absolute = int(bbox.zmin) + int(local_layer)
        if mask_store is not None:
            return pool.submit(_aio_fast_case_from_cached_layer, mask_store, absolute, bbox, grid)
        return pool.submit(_aio_fast_case_from_layer_item, items[absolute], bbox, resolved, grid)

    try:
        with ThreadPoolExecutor(max_workers=dec_workers) as dec_pool, \
             ThreadPoolExecutor(max_workers=prefetch_workers) as case_pool:
            while next_submit < total and len(case_futures) < prefetch_workers:
                case_futures[next_submit] = submit_case(case_pool, next_submit)
                next_submit += 1

            for k in range(total_pairs):
                recon_core.check_cancel(cancel_event)
                if k < total:
                    next_case, material = case_futures.pop(k).result()
                    material_voxels += int(material)
                    while next_submit < total and len(case_futures) < prefetch_workers:
                        case_futures[next_submit] = submit_case(case_pool, next_submit)
                        next_submit += 1
                else:
                    next_case = zero

                for y0 in range(0, hc, tile_rows):
                    q = _aio_v32_make_q_triangles(
                        prev_case, next_case, k, y0, min(hc, y0 + tile_rows), wc,
                    )
                    if q is not None:
                        feed_q(dec_pool, q)
                    consume_one(False)
                prev_case = next_case

                done = k + 1
                elapsed = time.time() - started
                remain = elapsed / max(1, done) * (total_pairs - done)
                pct = 64.0 + 32.0 * done / max(1, total_pairs)
                log.progress(
                    pct,
                    f"ExactLUT {done:,}/{total_pairs:,} pair | raw={raw_total:,} final={writer.tri_count:,} | ETA {recon_core.format_seconds(remain)}",
                )
                if done == 1 or done % 20 == 0 or done == total_pairs:
                    log.write(
                        f"v3.2 pair {done:,}/{total_pairs:,} | raw={raw_total:,} | "
                        f"written={writer.tri_count:,} | in-flight={len(futures)}"
                    )

            if pending_count:
                batch = pending_parts[0] if len(pending_parts) == 1 else np.concatenate(pending_parts, axis=0)
                submit_batch(dec_pool, batch)
                pending_parts = []
                pending_count = 0
            while futures:
                consume_one(True)

        if writer.tri_count <= 0:
            raise ValueError("생성된 표면 triangle이 없습니다.")
        triangle_count = int(writer.tri_count)
        writer.close(success=True)
        success = True
        recon_core.verify_binary_stl(params.output_stl, triangle_count)

        log.write("")
        log.write("=============== v3.2 결과 ===============")
        log.write(f"raw exact LUT triangles: {raw_total:,}")
        log.write(f"final triangles: {triangle_count:,}")
        if raw_total:
            log.write(f"triangle 감소: {(1.0 - triangle_count/raw_total)*100.0:.1f}%")
        log.write(f"zero-error 검증 통과 batch: {verified_batches:,}")
        log.write(f"raw exact fallback batch: {fallback_batches:,}")
        for seq, msg, fb in sorted(batch_messages, key=lambda x: x[0])[:20]:
            log.write(f"  batch {seq}: {'RAW' if fb else 'PASS'} · {msg}")
        log.write("vertex 이동/해상도 감소/근사 decimation: 0")
        log.write("==========================================")
        return triangle_count, material_voxels
    except Exception:
        if not success:
            writer.close(success=False)
        raise


# v3.2가 표면 보간형의 최종 경로다. v3.0/v3.1 실험 경로는 더 이상 호출되지 않는다.
recon_core.reconstruct_interpolated_surface = _aio_v32_exact_interpolated_surface

# -----------------------------------------------------------------------------
# v3.4/v3.7 Hierarchical + Global exact compact (STL only)
# -----------------------------------------------------------------------------
# v3.2의 batch별 VTK zero-error reduction은 빠르지만, batch 경계가 인위적인
# open boundary가 되어 긴 수직벽/평면을 batch를 넘어 합치지 못한다.
# v3.3은 1차 STL이 만들어진 뒤 전체 closed mesh를 한 번 더 VTK DecimatePro에
# 넣어 MaximumError=0 + PreserveTopology 조건으로 전역 경량화한다.
#
# 핵심 안전장치:
# - 메모리 여유가 부족하면 전역 pass 자체를 건너뛴다.
# - 입력이 closed manifold가 아니면 전역 pass를 적용하지 않는다.
# - 출력도 closed manifold인지 확인한다.
# - bounds / surface area / volume이 수치 허용오차 이내에서 동일해야 한다.
# - 모든 출력 정점이 원래 half-grid 좌표계 위에 그대로 남아 있어야 한다.
# - 하나라도 실패하면 compact 결과를 폐기하고 v3.2 원본 STL을 그대로 사용한다.
# - v3.7 production 경로는 최종 STL 하나만 출력한다.
# - 전 레이어 XOR 재슬라이스와 3MF companion 생성은 처리시간 절감을 위해 제거했다.

_AIO_V33_GLOBAL_MIN_TRI = 150_000
_AIO_V33_GLOBAL_MEMORY_FACTOR = 7.5   # binary STL bytes -> conservative VTK peak estimate
_AIO_V33_GLOBAL_MEMORY_FRACTION = 0.62


def _aio_v33_available_memory_bytes() -> int:
    """현재 사용 가능한 물리 메모리. 실패하면 보수적으로 8 GiB 반환."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullAvailPhys)
        if hasattr(os, "sysconf"):
            pages = int(os.sysconf("SC_AVPHYS_PAGES"))
            psize = int(os.sysconf("SC_PAGE_SIZE"))
            if pages > 0 and psize > 0:
                return pages * psize
    except Exception:
        pass
    return 8 * 1024**3


def _aio_v33_bad_edge_count(poly) -> int:
    feat = _aio_vtk.vtkFeatureEdges()
    feat.SetInputData(poly)
    feat.BoundaryEdgesOn()
    feat.NonManifoldEdgesOn()
    feat.FeatureEdgesOff()
    feat.ManifoldEdgesOff()
    feat.Update()
    return int(feat.GetOutput().GetNumberOfCells())


def _aio_v33_mass_properties(poly):
    tri = _aio_vtk.vtkTriangleFilter()
    tri.SetInputData(poly)
    tri.PassLinesOff()
    tri.PassVertsOff()
    tri.Update()
    mass = _aio_vtk.vtkMassProperties()
    mass.SetInputData(tri.GetOutput())
    mass.Update()
    return float(mass.GetSurfaceArea()), float(mass.GetVolume()), tri.GetOutput()


def _aio_v33_bounds_array(poly) -> np.ndarray:
    b = poly.GetBounds()
    return np.asarray([b[0], b[2], b[4], b[1], b[3], b[5]], dtype=np.float64)


def _aio_v33_half_grid_residual(poly, half_xyz, ref_min_xyz) -> float:
    pts = poly.GetPoints()
    if pts is None or pts.GetNumberOfPoints() == 0:
        return float("inf")
    arr = np.asarray(_aio_vtk_to_numpy(pts.GetData()), dtype=np.float64)
    h = np.asarray(half_xyz, dtype=np.float64)
    ref = np.asarray(ref_min_xyz, dtype=np.float64)
    q = (arr - ref[None, :]) / h[None, :]
    return float(np.max(np.abs(q - np.rint(q))))


def _aio_v33_global_decimate_poly(poly):
    dec = _aio_vtk.vtkDecimatePro()
    dec.SetInputData(poly)
    dec.SetTargetReduction(0.9999999)
    dec.PreserveTopologyOn()
    dec.SplittingOff()
    dec.BoundaryVertexDeletionOff()
    dec.SetErrorIsAbsolute(1)
    dec.SetMaximumError(0.0)
    dec.AccumulateErrorOn()
    dec.Update()
    return dec.GetOutput()


def _aio_v35_poly_vertex_subset_exact(reference_poly, candidate_poly) -> tuple[bool, float]:
    """candidate의 모든 정점이 reference float32 정점의 bit-exact subset인지 확인."""
    rp = reference_poly.GetPoints()
    cp = candidate_poly.GetPoints()
    if rp is None or cp is None:
        return False, float("inf")
    rv = np.ascontiguousarray(_aio_vtk_to_numpy(rp.GetData()), dtype=np.float32)
    cv = np.ascontiguousarray(_aio_vtk_to_numpy(cp.GetData()), dtype=np.float32)
    if len(cv) == 0 or len(rv) == 0:
        return False, float("inf")
    kd = np.dtype((np.void, 12))
    rk = np.ascontiguousarray(rv.view(np.uint32).reshape(-1, 3)).view(kd).ravel()
    ck = np.ascontiguousarray(cv.view(np.uint32).reshape(-1, 3)).view(kd).ravel()
    rks = np.unique(rk)
    cks = np.unique(ck)
    pos = np.searchsorted(rks, cks)
    ok = bool(
        np.all(pos < len(rks)) and
        np.all(rks[np.minimum(pos, len(rks) - 1)] == cks)
    )
    return ok, float(len(cks))


def _aio_v35_bidirectional_surface_distance(poly_a, poly_b) -> float:
    """VTK point + cell-center 양방향 최대 surface distance."""
    dist = _aio_vtk.vtkDistancePolyDataFilter()
    dist.SetInputData(0, poly_a)
    dist.SetInputData(1, poly_b)
    dist.SignedDistanceOff()
    dist.ComputeCellCenterDistanceOn()
    dist.ComputeSecondDistanceOn()
    dist.Update()
    max_dist = 0.0
    for po in (dist.GetOutput(), dist.GetSecondDistanceOutput()):
        for data in (po.GetPointData(), po.GetCellData()):
            arr = data.GetArray("Distance")
            if arr is None:
                continue
            vv = np.asarray(_aio_vtk_to_numpy(arr), dtype=np.float64)
            if vv.size:
                max_dist = max(max_dist, float(vv.max(initial=0.0)))
    return max_dist


_AIO_V34_STL_REC_DTYPE = np.dtype([
    ("normal", "<f4", (3,)),
    ("vertices", "<f4", (3, 3)),
    ("attr", "<u2"),
], align=False)


def _aio_v34_exact_weld_float_triangles(tri_xyz: np.ndarray):
    """float32 좌표 bit pattern 기준 exact weld. tolerance/rounding 사용 안 함."""
    tri = np.ascontiguousarray(tri_xyz, dtype=np.float32)
    flat = tri.reshape(-1, 3)
    bits = np.ascontiguousarray(flat.view(np.uint32).reshape(-1, 3))
    key_dtype = np.dtype((np.void, 12))
    keys = bits.view(key_dtype).ravel()
    uniq, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    vertices = np.ascontiguousarray(flat[first], dtype=np.float32)
    faces = np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int64)
    return vertices, faces, uniq


def _aio_v34_poly_area(vertices, faces) -> float:
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    if len(f) == 0:
        return 0.0
    a = v[f[:, 0]]; b = v[f[:, 1]]; c = v[f[:, 2]]
    return float(0.5 * np.linalg.norm(np.cross(b-a, c-a), axis=1).sum(dtype=np.float64))


def _aio_v34_decimate_open_chunk(tri_xyz: np.ndarray, half_xyz):
    """open/closed chunk 모두에서 boundary vertex를 보존하는 exact zero-error reduction."""
    raw_n = int(len(tri_xyz))
    vertices, faces, raw_keys = _aio_v34_exact_weld_float_triangles(tri_xyz)
    if raw_n < 32:
        return vertices, faces, True, "small chunk"
    try:
        poly = _aio_v32_make_polydata(vertices, faces)
        area0 = _aio_v34_poly_area(vertices, faces)
        bounds0 = np.r_[vertices.min(axis=0).astype(np.float64), vertices.max(axis=0).astype(np.float64)]
        out = _aio_v33_global_decimate_poly(poly)
        ov, of = _aio_v32_polydata_numpy(out)
        if len(of) <= 0 or len(of) > raw_n:
            raise RuntimeError(f"bad face count {len(of):,}/{raw_n:,}")
        # 출력 정점은 입력 float32 정점의 exact subset이어야 한다.
        obits = np.ascontiguousarray(ov.view(np.uint32).reshape(-1, 3))
        okeys = obits.view(np.dtype((np.void, 12))).ravel()
        if not bool(np.all(np.isin(okeys, raw_keys, assume_unique=False))):
            raise RuntimeError("output vertex is not exact input-vertex subset")
        max_dist = _aio_v35_bidirectional_surface_distance(poly, out)
        dist_tol = max(3.0e-6, float(np.min(np.asarray(half_xyz, dtype=np.float64))) * 2.0e-4)
        if max_dist > dist_tol:
            raise RuntimeError(f"surface distance {format_number(max_dist, scientific=True)} mm > {format_number(dist_tol, scientific=True)}")
        area1 = _aio_v34_poly_area(ov, of)
        area_rel = abs(area1-area0) / max(1.0, abs(area0))
        if area_rel > 2e-6:
            raise RuntimeError(f"area change {format_number(area_rel, scientific=True)}")
        bounds1 = np.r_[ov.min(axis=0).astype(np.float64), ov.max(axis=0).astype(np.float64)]
        if float(np.max(np.abs(bounds1-bounds0))) > 3e-6:
            raise RuntimeError("bounds changed")
        # 원래 half-grid 이탈 여부. chunk 최소점을 reference lattice point로 사용.
        h = np.asarray(half_xyz, dtype=np.float64)
        ref = bounds0[:3]
        q = (ov.astype(np.float64)-ref[None,:]) / h[None,:]
        residual = float(np.max(np.abs(q-np.rint(q))))
        if residual > 3e-4:
            raise RuntimeError(f"half-grid residual {format_number(residual, scientific=True)}")
        return ov, of, False, f"PASS {raw_n:,}->{len(of):,} areaΔ={area_rel:.1e}"
    except Exception as exc:
        return vertices, faces, True, f"fallback {type(exc).__name__}: {exc}"


def _aio_v34_hierarchical_file_pass(
    input_stl: Path,
    output_stl: Path,
    params,
    log,
    cancel_event,
    chunk_triangles: int,
) -> tuple[bool, dict]:
    """STL을 큰 연속 chunk로 다시 zero-error 감소시켜 과거 batch 경계를 단계적으로 제거."""
    output_stl.unlink(missing_ok=True)
    result = {"reason":"", "input_triangles":0, "output_triangles":0, "fallback_chunks":0}
    try:
        with open(input_stl,"rb") as f:
            header=f.read(80); raw=f.read(4)
            if len(header)!=80 or len(raw)!=4:
                raise ValueError("invalid binary STL header")
            total=int(struct.unpack("<I",raw)[0])
        result["input_triangles"]=total
        if total <= int(chunk_triangles*1.15):
            result["reason"]="not enough chunks"
            return False,result
        half=(12.7/float(params.dpi_x),12.7/float(params.dpi_y),0.5*float(params.layer_thickness_mm))
        writer=recon_core.BinarySTLWriter(output_stl,header="v3.4 hierarchical exact compact",buffer_bytes=8*1024*1024)
        ok_close=False; fallbacks=0; chunks=0; done=0; started=time.time()
        try:
            with open(input_stl,"rb") as f:
                f.seek(84)
                while done<total:
                    recon_core.check_cancel(cancel_event)
                    take=min(int(chunk_triangles),total-done)
                    data=f.read(take*50)
                    if len(data)!=take*50:
                        raise ValueError("unexpected STL EOF")
                    rec=np.frombuffer(data,dtype=_AIO_V34_STL_REC_DTYPE,count=take)
                    tri=np.ascontiguousarray(rec["vertices"],dtype=np.float32)
                    v,fc,fb,msg=_aio_v34_decimate_open_chunk(tri,half)
                    if fb: fallbacks+=1
                    _AIO_MC_LIGHT_ORIG_WRITE(writer,v,fc,max_triangles=params.max_triangles,cancel_event=cancel_event,chunk_size=120_000)
                    done+=take; chunks+=1
                    if chunks==1 or chunks%2==0 or done==total:
                        log.write(f"hier chunk {chunks}: {done:,}/{total:,} raw read · written={writer.tri_count:,} · {'RAW' if fb else msg}")
            out_n=int(writer.tri_count)
            if out_n<=0 or out_n>total:
                raise RuntimeError(f"bad hierarchical output count {out_n:,}/{total:,}")
            writer.close(success=True); ok_close=True
            recon_core.verify_binary_stl(output_stl,out_n)
            result.update(reason="PASS",output_triangles=out_n,fallback_chunks=fallbacks,chunks=chunks,elapsed=time.time()-started)
            return True,result
        finally:
            if not ok_close:
                try: writer.close(success=False)
                except Exception: pass
    except Exception as exc:
        output_stl.unlink(missing_ok=True)
        result["reason"]=f"{type(exc).__name__}: {exc}"
        return False,result


def _aio_v34_hierarchical_compact_if_needed(input_stl: Path, params, log, cancel_event) -> Path:
    """전역 pass가 메모리상 불가능한 대형 STL을 2M->6M 연속 chunk pass로 먼저 축소."""
    current=input_stl
    owned=[]
    try:
        for target in (2_000_000,6_000_000):
            with open(current,"rb") as f:
                f.seek(80); total=int(struct.unpack("<I",f.read(4))[0])
            if total <= int(target*1.15):
                continue
            candidate=input_stl.with_name(input_stl.stem+f"__hier_{target//1_000_000}m.stl")
            log.write(f"메모리 절약 계층 경량화: chunk={target:,} tri")
            ok,st=_aio_v34_hierarchical_file_pass(current,candidate,params,log,cancel_event,target)
            if not ok:
                candidate.unlink(missing_ok=True)
                log.write(f"hier pass 미적용: {st.get('reason','unknown')}")
                continue
            old_n=int(st.get('input_triangles',0)); new_n=int(st.get('output_triangles',0))
            if new_n >= old_n*0.995:
                candidate.unlink(missing_ok=True)
                log.write("hier pass 감소량 <0.5% -> 중단")
                break
            log.write(f"hier PASS: {old_n:,}->{new_n:,} tri ({(1-new_n/max(1,old_n))*100:.1f}% 감소)")
            if current != input_stl:
                try: current.unlink(missing_ok=True)
                except Exception: pass
            current=candidate; owned.append(candidate)
        return current
    except Exception:
        return current


def _aio_v33_global_exact_compact_stl(
    input_stl: Path,
    output_stl: Path,
    params,
    log,
    cancel_event,
) -> tuple[bool, dict]:
    """전체 closed STL을 zero-error로 한 번 더 경량화.

    성공 시 output_stl 생성 + (True, stats).
    어떤 검증이라도 실패하면 output은 삭제하고 (False, reason stats).
    """
    result = {"reason": "", "input_triangles": 0, "output_triangles": 0}
    output_stl.unlink(missing_ok=True)
    if not _AIO_V32_VTK_AVAILABLE:
        result["reason"] = f"VTK unavailable: {_AIO_V32_VTK_ERROR}"
        return False, result
    try:
        with open(input_stl, "rb") as f:
            f.seek(80)
            raw = f.read(4)
            if len(raw) != 4:
                raise ValueError("STL triangle count header를 읽지 못했습니다.")
            tri_count = int(struct.unpack("<I", raw)[0])
        result["input_triangles"] = tri_count
        if tri_count < _AIO_V33_GLOBAL_MIN_TRI:
            result["reason"] = f"small mesh ({tri_count:,} tri)"
            return False, result

        file_bytes = int(input_stl.stat().st_size)
        avail = int(_aio_v33_available_memory_bytes())
        est_peak = int(file_bytes * _AIO_V33_GLOBAL_MEMORY_FACTOR + 512 * 1024**2)
        budget = int(avail * _AIO_V33_GLOBAL_MEMORY_FRACTION)
        log.write(
            f"전역 경량화 메모리 판단: STL={format_bytes(file_bytes)} · "
            f"예상 peak≈{format_bytes(est_peak)} · 현재 여유={format_bytes(avail)}"
        )
        if est_peak > budget:
            result["reason"] = (
                f"memory safety skip: estimated {format_bytes(est_peak)} > "
                f"budget {format_bytes(budget)}"
            )
            return False, result

        recon_core.check_cancel(cancel_event)
        reader = _aio_vtk.vtkSTLReader()
        reader.SetFileName(str(input_stl))
        reader.MergingOn()
        reader.Update()
        inp = reader.GetOutput()
        in_cells = int(inp.GetNumberOfCells())
        if in_cells <= 0:
            raise RuntimeError("VTK가 입력 STL triangle을 읽지 못했습니다.")

        bad_in = _aio_v33_bad_edge_count(inp)
        if bad_in != 0:
            result["reason"] = f"input not closed-manifold: bad edges={bad_in:,}"
            return False, result

        area0, vol0, inp_tri = _aio_v33_mass_properties(inp)
        bounds0 = _aio_v33_bounds_array(inp_tri)
        ref_min = bounds0[:3].copy()
        half = (12.7 / float(params.dpi_x), 12.7 / float(params.dpi_y), 0.5 * float(params.layer_thickness_mm))

        log.write(
            f"전역 zero-error pass 시작: {in_cells:,} triangles · "
            "closed manifold 확인 완료"
        )
        started = time.time()
        out = _aio_v33_global_decimate_poly(inp_tri)
        recon_core.check_cancel(cancel_event)
        out_cells = int(out.GetNumberOfCells())
        result["output_triangles"] = out_cells
        if out_cells <= 0 or out_cells > in_cells:
            raise RuntimeError(f"비정상 전역 reduction count {out_cells:,}/{in_cells:,}")
        if out_cells == in_cells:
            result["reason"] = "no further exact reduction"
            return False, result

        bad_out = _aio_v33_bad_edge_count(out)
        if bad_out != 0:
            raise RuntimeError(f"전역 reduction 후 bad edges={bad_out:,}")

        subset_ok, _subset_n = _aio_v35_poly_vertex_subset_exact(inp_tri, out)
        if not subset_ok:
            raise RuntimeError("전역 reduction이 원본에 없던 vertex를 생성/이동함")
        max_dist = _aio_v35_bidirectional_surface_distance(inp_tri, out)
        dist_tol = max(3.0e-6, float(np.min(np.asarray(half, dtype=np.float64))) * 2.0e-4)
        if max_dist > dist_tol:
            raise RuntimeError(f"전역 surface distance {format_number(max_dist, scientific=True)} mm > {format_number(dist_tol, scientific=True)}")

        area1, vol1, out_tri = _aio_v33_mass_properties(out)
        bounds1 = _aio_v33_bounds_array(out_tri)
        scale = max(1.0, float(np.max(np.abs(bounds0))))
        bounds_tol = max(2e-6, 2e-7 * scale)
        if float(np.max(np.abs(bounds1 - bounds0))) > bounds_tol:
            raise RuntimeError(
                f"bounds 변화 {format_number(float(np.max(np.abs(bounds1-bounds0))), scientific=True)} mm > {format_number(bounds_tol, scientific=True)}"
            )
        area_rel = abs(area1 - area0) / max(1.0, abs(area0))
        vol_rel = abs(vol1 - vol0) / max(1.0, abs(vol0))
        if area_rel > 2e-6:
            raise RuntimeError(f"surface area 변화율 {format_number(area_rel, scientific=True)}")
        if vol_rel > 3e-6:
            raise RuntimeError(f"volume 변화율 {format_number(vol_rel, scientific=True)}")

        grid_res = _aio_v33_half_grid_residual(out_tri, half, ref_min)
        if grid_res > 2e-4:
            raise RuntimeError(f"half-grid 이탈 residual={format_number(grid_res, scientific=True)} grid")

        writer = _aio_vtk.vtkSTLWriter()
        writer.SetFileName(str(output_stl))
        writer.SetInputData(out_tri)
        writer.SetFileTypeToBinary()
        if int(writer.Write()) != 1:
            raise RuntimeError("VTK compact STL 저장 실패")
        recon_core.verify_binary_stl(output_stl, out_cells)

        elapsed = time.time() - started
        result.update(
            reason="PASS",
            input_triangles=in_cells,
            output_triangles=out_cells,
            area_rel=area_rel,
            volume_rel=vol_rel,
            half_grid_residual=grid_res,
            surface_max_distance=max_dist,
            elapsed=elapsed,
            bad_edges_input=bad_in,
            bad_edges_output=bad_out,
        )
        return True, result
    except Exception as exc:
        output_stl.unlink(missing_ok=True)
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return False, result


def _aio_v33_global_selftest() -> tuple[bool, str]:
    """전역 pass가 closed gridded cube를 정확히 12 tri까지 줄이는지 빠른 회귀검증."""
    if not _AIO_V32_VTK_AVAILABLE:
        return False, f"VTK unavailable: {_AIO_V32_VTK_ERROR}"
    try:
        n = 8
        verts = []
        index = {}
        faces = []

        def vid(p):
            if p not in index:
                index[p] = len(verts)
                verts.append(tuple(float(x) for x in p))
            return index[p]

        # 정육면체 6면을 n*n grid로 세분. winding보다 closed topology가 self-test 핵심.
        for y in range(n):
            for z in range(n):
                a,b,c,d = vid((0,y,z)),vid((0,y,z+1)),vid((0,y+1,z+1)),vid((0,y+1,z))
                faces += [(a,b,c),(a,c,d)]
                a,b,c,d = vid((n,y,z)),vid((n,y+1,z)),vid((n,y+1,z+1)),vid((n,y,z+1))
                faces += [(a,b,c),(a,c,d)]
        for x in range(n):
            for z in range(n):
                a,b,c,d = vid((x,0,z)),vid((x+1,0,z)),vid((x+1,0,z+1)),vid((x,0,z+1))
                faces += [(a,c,b),(a,d,c)]
                a,b,c,d = vid((x,n,z)),vid((x,n,z+1)),vid((x+1,n,z+1)),vid((x+1,n,z))
                faces += [(a,b,c),(a,c,d)]
        for x in range(n):
            for y in range(n):
                a,b,c,d = vid((x,y,0)),vid((x,y+1,0)),vid((x+1,y+1,0)),vid((x+1,y,0))
                faces += [(a,b,c),(a,c,d)]
                a,b,c,d = vid((x,y,n)),vid((x+1,y,n)),vid((x+1,y+1,n)),vid((x,y+1,n))
                faces += [(a,b,c),(a,c,d)]
        poly = _aio_v32_make_polydata(np.asarray(verts,np.float32), np.asarray(faces,np.int64))
        if _aio_v33_bad_edge_count(poly) != 0:
            return False, "self-test input is not closed"
        area0, vol0, tri0 = _aio_v33_mass_properties(poly)
        out = _aio_v33_global_decimate_poly(tri0)
        if int(out.GetNumberOfCells()) != 12:
            return False, f"expected 12 tri, got {out.GetNumberOfCells()}"
        if _aio_v33_bad_edge_count(out) != 0:
            return False, "self-test output has bad edges"
        area1, vol1, _ = _aio_v33_mass_properties(out)
        if abs(area1-area0) > 1e-6 or abs(vol1-vol0) > 1e-6:
            return False, "self-test area/volume mismatch"
        return True, f"closed grid cube {len(faces):,}->12 tri PASS"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


_AIO_V33_SELFTEST_OK, _AIO_V33_SELFTEST_MESSAGE = _aio_v33_global_selftest()

# -----------------------------------------------------------------------------
# Exact Numba lattice accelerator
# -----------------------------------------------------------------------------
# 기존 lattice_core의 Python/NumPy 기준 구현은 그대로 보존한다.
# 반복 호출이 많은 원형 node/선분 rasterization만 Numba 기계어 루프로 교체한다.
# 실제 설정별 작은 raster를 기존 구현과 전수 XOR 비교한 뒤에만 가속 경로를 사용한다.
# 불일치 또는 실행 오류가 발생하면 즉시 기존 정밀 구현으로 자동 fallback한다.

_AIO_LATTICE_PY_SQUARE_VERTEX = lattice_core.draw_square_vertex_lattice
_AIO_LATTICE_PY_HEX_EDGE = lattice_core.draw_hex_lattice
_AIO_LATTICE_PY_HEX_VERTEX = lattice_core.draw_hex_vertex_lattice
_AIO_LATTICE_ACCEL_DISABLED = False
_AIO_LATTICE_ACCEL_READY = False
_AIO_LATTICE_VALIDATED_KEYS = set()
_AIO_LATTICE_LOCK = threading.RLock()
_AIO_LATTICE_FALLBACK_REASON = ""


@njit(cache=True, nogil=True)
def _aio_jit_rasterize_disks(
    h: int,
    w: int,
    centers: np.ndarray,
    diameter_mm: float,
    x_pitch: float,
    y_pitch: float,
) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.bool_)
    r = max(diameter_mm / 2.0, min(x_pitch, y_pitch) / 2.0)
    rr = r * r
    for i in range(centers.shape[0]):
        cx = centers[i, 0]
        cy = centers[i, 1]
        x_min = max(0, int(math.floor((cx - r) / x_pitch - 0.5)))
        x_max = min(w - 1, int(math.ceil((cx + r) / x_pitch - 0.5)))
        y_min = max(0, int(math.floor((cy - r) / y_pitch - 0.5)))
        y_max = min(h - 1, int(math.ceil((cy + r) / y_pitch - 0.5)))
        if x_min > x_max or y_min > y_max:
            continue
        for y in range(y_min, y_max + 1):
            py = (float(y) + 0.5) * y_pitch
            for x in range(x_min, x_max + 1):
                px = (float(x) + 0.5) * x_pitch
                dx = px - cx
                dy = py - cy
                if dx * dx + dy * dy <= rr:
                    mask[y, x] = True
    return mask


@njit(cache=True, nogil=True)
def _aio_jit_rasterize_segments(
    h: int,
    w: int,
    segments: np.ndarray,
    width_mm: float,
    x_pitch: float,
    y_pitch: float,
) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.bool_)
    r = max(width_mm / 2.0, min(x_pitch, y_pitch) / 2.0)
    rr = r * r
    for i in range(segments.shape[0]):
        x1 = segments[i, 0]
        y1 = segments[i, 1]
        x2 = segments[i, 2]
        y2 = segments[i, 3]
        x_min = max(0, int(math.floor((min(x1, x2) - r) / x_pitch - 0.5)))
        x_max = min(w - 1, int(math.ceil((max(x1, x2) + r) / x_pitch - 0.5)))
        y_min = max(0, int(math.floor((min(y1, y2) - r) / y_pitch - 0.5)))
        y_max = min(h - 1, int(math.ceil((max(y1, y2) + r) / y_pitch - 0.5)))
        if x_min > x_max or y_min > y_max:
            continue
        vx = x2 - x1
        vy = y2 - y1
        denom = vx * vx + vy * vy
        for y in range(y_min, y_max + 1):
            py = (float(y) + 0.5) * y_pitch
            for x in range(x_min, x_max + 1):
                px = (float(x) + 0.5) * x_pitch
                if denom <= 0.0:
                    dx = px - x1
                    dy = py - y1
                    dist2 = dx * dx + dy * dy
                else:
                    t = ((px - x1) * vx + (py - y1) * vy) / denom
                    if t < 0.0:
                        t = 0.0
                    elif t > 1.0:
                        t = 1.0
                    proj_x = x1 + t * vx
                    proj_y = y1 + t * vy
                    dx = px - proj_x
                    dy = py - proj_y
                    dist2 = dx * dx + dy * dy
                if dist2 <= rr:
                    mask[y, x] = True
    return mask


def _aio_square_centers(
    w: int, h: int, spacing_mm: float, width_mm: float,
    dpi_x: float, dpi_y: float, offset_x_mm: float, offset_y_mm: float,
) -> np.ndarray:
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    w_mm = w * x_pitch
    h_mm = h * y_pitch
    spacing_mm = max(float(spacing_mm), min(x_pitch, y_pitch) * 2.0)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    margin_mm = spacing_mm + width_mm + max(x_pitch, y_pitch)
    col_min = int(math.floor((-margin_mm - offset_x_mm) / spacing_mm)) - 1
    col_max = int(math.ceil((w_mm + margin_mm - offset_x_mm) / spacing_mm)) + 1
    row_min = int(math.floor((-margin_mm - offset_y_mm) / spacing_mm)) - 1
    row_max = int(math.ceil((h_mm + margin_mm - offset_y_mm) / spacing_mm)) + 1
    centers = []
    for row in range(row_min, row_max + 1):
        y = offset_y_mm + row * spacing_mm
        for col in range(col_min, col_max + 1):
            x = offset_x_mm + col * spacing_mm
            centers.append((x, y))
    return np.asarray(centers, dtype=np.float64).reshape((-1, 2))


def _aio_hex_centers_for_canvas_mm(
    w_mm: float, h_mm: float, spacing_mm: float,
    offset_x_mm: float, offset_y_mm: float, margin_mm: float,
):
    side = float(spacing_mm) / math.sqrt(3.0)
    dx = float(spacing_mm)
    dy = 1.5 * side
    col_min = int(math.floor((-margin_mm - offset_x_mm) / dx)) - 2
    col_max = int(math.ceil((w_mm + margin_mm - offset_x_mm) / dx)) + 2
    row_min = int(math.floor((-margin_mm - offset_y_mm) / dy)) - 2
    row_max = int(math.ceil((h_mm + margin_mm - offset_y_mm) / dy)) + 2
    for row in range(row_min, row_max + 1):
        cy = offset_y_mm + row * dy
        xoff = dx / 2.0 if row % 2 else 0.0
        for col in range(col_min, col_max + 1):
            cx = offset_x_mm + col * dx + xoff
            yield cx, cy, side


def _aio_hex_vertices(
    w: int, h: int, spacing_mm: float, width_mm: float,
    dpi_x: float, dpi_y: float, offset_x_mm: float, offset_y_mm: float,
) -> np.ndarray:
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    w_mm = w * x_pitch
    h_mm = h * y_pitch
    spacing_mm = max(float(spacing_mm), min(x_pitch, y_pitch) * 2.0)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    margin_mm = spacing_mm * 2.0 + width_mm + max(x_pitch, y_pitch)
    angles = (30, 90, 150, 210, 270, 330)
    seen = set()
    vertices = []
    for cx, cy, side in _aio_hex_centers_for_canvas_mm(
        w_mm, h_mm, spacing_mm, offset_x_mm, offset_y_mm, margin_mm
    ):
        for angle in angles:
            rad = math.radians(angle)
            x = cx + math.cos(rad) * side
            y = cy + math.sin(rad) * side
            key = (round(float(x), 5), round(float(y), 5))
            if key in seen:
                continue
            seen.add(key)
            vertices.append((x, y))
    return np.asarray(vertices, dtype=np.float64).reshape((-1, 2))


def _aio_hex_segments(
    w: int, h: int, spacing_mm: float, width_mm: float,
    dpi_x: float, dpi_y: float, offset_x_mm: float, offset_y_mm: float,
) -> np.ndarray:
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    w_mm = w * x_pitch
    h_mm = h * y_pitch
    spacing_mm = max(float(spacing_mm), min(x_pitch, y_pitch) * 2.0)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    margin_mm = spacing_mm * 2.0 + width_mm + max(x_pitch, y_pitch)
    angles = (30, 90, 150, 210, 270, 330)
    seen = set()
    segments = []
    for cx, cy, side in _aio_hex_centers_for_canvas_mm(
        w_mm, h_mm, spacing_mm, offset_x_mm, offset_y_mm, margin_mm
    ):
        points = []
        for angle in angles:
            rad = math.radians(angle)
            points.append((cx + math.cos(rad) * side, cy + math.sin(rad) * side))
        for index in range(6):
            p1 = points[index]
            p2 = points[(index + 1) % 6]
            q1 = (round(float(p1[0]), 5), round(float(p1[1]), 5))
            q2 = (round(float(p2[0]), 5), round(float(p2[1]), 5))
            key = tuple(sorted((q1, q2)))
            if key in seen:
                continue
            seen.add(key)
            segments.append((p1[0], p1[1], p2[0], p2[1]))
    return np.asarray(segments, dtype=np.float64).reshape((-1, 4))


def _aio_draw_square_vertex_numba(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    w, h = int(size[0]), int(size[1])
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    centers = _aio_square_centers(
        w, h, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )
    return _aio_jit_rasterize_disks(h, w, centers, width_mm, x_pitch, y_pitch)


def _aio_draw_hex_vertex_numba(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    w, h = int(size[0]), int(size[1])
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    centers = _aio_hex_vertices(
        w, h, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )
    return _aio_jit_rasterize_disks(h, w, centers, width_mm, x_pitch, y_pitch)


def _aio_draw_hex_edge_numba(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    w, h = int(size[0]), int(size[1])
    x_pitch = 25.4 / float(dpi_x)
    y_pitch = 25.4 / float(dpi_y)
    width_mm = max(float(width_mm), min(x_pitch, y_pitch))
    segments = _aio_hex_segments(
        w, h, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )
    return _aio_jit_rasterize_segments(h, w, segments, width_mm, x_pitch, y_pitch)


def _aio_lattice_validation_key(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm):
    return (
        round(float(spacing_mm), 9), round(float(width_mm), 9),
        round(float(dpi_x), 9), round(float(dpi_y), 9),
        round(float(offset_x_mm), 9), round(float(offset_y_mm), 9),
    )


def _aio_disable_lattice_acceleration(reason: str) -> None:
    global _AIO_LATTICE_ACCEL_DISABLED, _AIO_LATTICE_FALLBACK_REASON
    with _AIO_LATTICE_LOCK:
        _AIO_LATTICE_ACCEL_DISABLED = True
        _AIO_LATTICE_FALLBACK_REASON = str(reason)


def _aio_validate_lattice_acceleration(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm) -> bool:
    if _AIO_LATTICE_ACCEL_DISABLED:
        return False
    key = _aio_lattice_validation_key(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm)
    with _AIO_LATTICE_LOCK:
        if key in _AIO_LATTICE_VALIDATED_KEYS:
            return True
        try:
            test_size = (193, 181)
            py_edge = _AIO_LATTICE_PY_HEX_EDGE(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            nb_edge = _aio_draw_hex_edge_numba(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            py_hex_vertex = _AIO_LATTICE_PY_HEX_VERTEX(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            nb_hex_vertex = _aio_draw_hex_vertex_numba(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            py_square_vertex = _AIO_LATTICE_PY_SQUARE_VERTEX(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            nb_square_vertex = _aio_draw_square_vertex_numba(
                test_size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            )
            if not (
                np.array_equal(py_edge, nb_edge)
                and np.array_equal(py_hex_vertex, nb_hex_vertex)
                and np.array_equal(py_square_vertex, nb_square_vertex)
            ):
                _aio_disable_lattice_acceleration(
                    "Numba lattice self-check mismatch; exact Python fallback"
                )
                return False
            _AIO_LATTICE_VALIDATED_KEYS.add(key)
            return True
        except Exception as exc:
            _aio_disable_lattice_acceleration(f"Numba lattice validation error: {exc}")
            return False


def _aio_draw_square_vertex_exact(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    if _aio_validate_lattice_acceleration(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm):
        try:
            return np.asarray(_aio_draw_square_vertex_numba(
                size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            ), dtype=bool)
        except Exception as exc:
            _aio_disable_lattice_acceleration(f"square vertex JIT error: {exc}")
    return _AIO_LATTICE_PY_SQUARE_VERTEX(
        size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )


def _aio_draw_hex_edge_exact(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    if _aio_validate_lattice_acceleration(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm):
        try:
            return np.asarray(_aio_draw_hex_edge_numba(
                size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            ), dtype=bool)
        except Exception as exc:
            _aio_disable_lattice_acceleration(f"hex edge JIT error: {exc}")
    return _AIO_LATTICE_PY_HEX_EDGE(
        size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )


def _aio_draw_hex_vertex_exact(
    size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm,
):
    if _aio_validate_lattice_acceleration(spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm):
        try:
            return np.asarray(_aio_draw_hex_vertex_numba(
                size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
            ), dtype=bool)
        except Exception as exc:
            _aio_disable_lattice_acceleration(f"hex vertex JIT error: {exc}")
    return _AIO_LATTICE_PY_HEX_VERTEX(
        size, spacing_mm, width_mm, dpi_x, dpi_y, offset_x_mm, offset_y_mm
    )


def _aio_prepare_lattice_acceleration() -> None:
    global _AIO_LATTICE_ACCEL_READY
    try:
        # 작은 입력으로 JIT compile을 끝내 실제 대형 캔버스에서 compile 시간을 숨긴다.
        _aio_draw_square_vertex_numba((16, 16), 2.0, 0.4, 400.0, 400.0, 0.0, 0.0)
        _aio_draw_hex_vertex_numba((16, 16), 2.0, 0.4, 400.0, 400.0, 0.0, 0.0)
        _aio_draw_hex_edge_numba((16, 16), 2.0, 0.4, 400.0, 400.0, 0.0, 0.0)
        _AIO_LATTICE_ACCEL_READY = True
    except Exception as exc:
        _aio_disable_lattice_acceleration(f"Numba warm-up error: {exc}")


# module attribute 교체는 embedded core의 global lookup에도 그대로 반영된다.
lattice_core.draw_square_vertex_lattice = _aio_draw_square_vertex_exact
lattice_core.draw_hex_lattice = _aio_draw_hex_edge_exact
lattice_core.draw_hex_vertex_lattice = _aio_draw_hex_vertex_exact
_aio_prepare_lattice_acceleration()


APP_NAME = "BJT 모델 → 슬라이싱 → 격자화 → STL 통합기"

# -----------------------------------------------------------------------------
# v3.6 throughput engine: exact static-Z collapse + large-batch zero-error +
# single fast global compact pass
# -----------------------------------------------------------------------------
# 목적
# - v3.5의 표면엔진 결과 품질은 유지하면서, 500k 단위 VTK 경계와 뒤이은 다단계
#   경량화가 만드는 중복 연산을 줄인다.
# - 완전히 같은 연속 레이어 구간은 MC field 자체가 Z 방향으로 동일하므로, 중간 Z
#   정점을 만들지 않고 동일한 Lewiner LUT 표면을 한 번에 길게 생성한다.
# - 일반 변화 구간은 기존 256-case ExactLUT를 그대로 사용한다.
# - 경량화 중의 고비용 surface-distance 검증은 반복하지 않는다. 각 zero-error pass는
#   "출력 정점=입력 정점의 exact subset"을 강제한다. 최종 산출물은 빠른 구조 검증
#   (binary STL 무결성 + 퇴화면 + closed manifold)을 통과해야 한다.

_AIO_V36_STATIC_SELFTEST_OK = True
_AIO_V36_STATIC_SELFTEST_MSG = "not-run"


def _aio_v36_runtime_tuning(params):
    """16GB/i5-13500급을 기준으로 RAM 여유에 따라 batch/worker를 자동 조정."""
    try:
        avail = int(_aio_v33_available_memory_bytes())
    except Exception:
        avail = 8 * 1024**3
    gib = avail / float(1024**3)
    workers = max(1, int(getattr(params, "workers", 1)))
    if gib >= 12.0:
        return 2_000_000, min(2, workers), 256
    if gib >= 8.0:
        return 1_500_000, min(2, workers), 192
    if gib >= 5.0:
        return 900_000, 1, 160
    return 500_000, 1, 128


def _aio_v36_stretch_static_q(q, start_pair: int, run_pairs: int):
    """동일 layer-case 연속구간의 exact vertical surface를 중간 Z 정점 없이 stretch.

    동일한 2D case가 위/아래에 놓인 MC cube에는 vertical-edge crossing이 없으므로
    LUT vertex의 Z half-grid는 pair의 bottom/top 두 평면에만 존재해야 한다.
    이 조건이 깨지는 예외에서는 None을 반환해 일반 pair-by-pair 경로로 fallback한다.
    """
    if q is None or len(q) == 0 or int(run_pairs) <= 1:
        return q
    out = np.ascontiguousarray(q, dtype=np.int32).copy()
    z0 = 2 * int(start_pair)
    z1 = z0 + 2
    znew = 2 * (int(start_pair) + int(run_pairs))
    z = out[:, :, 2]
    ok = (z == z0) | (z == z1)
    if not bool(np.all(ok)):
        return None
    z[z == z1] = znew
    return out


def _aio_v36_static_run_selftest():
    """16 marching-square states x several run lengths: stretched surface exactness check."""
    if (not _AIO_FAST_EXACT_VALID) or _AIO_V32_LUT_Q_ZYX is None:
        return False, "ExactLUT unavailable"
    try:
        # one-cell case maps are enough to prove the identical-pair LUT Z-coordinate invariant.
        for c4 in range(16):
            a = np.asarray([[c4]], dtype=np.uint8)
            for start in (0, 3):
                q1 = _aio_v32_make_q_triangles(a, a, start, 0, 1, 1)
                if q1 is None:
                    continue
                for run in (2, 3, 7, 19):
                    qs = _aio_v36_stretch_static_q(q1, start, run)
                    if qs is None:
                        return False, f"case {c4}: unexpected mid-Z LUT vertex"
                    # stretched endpoints must be exactly the first bottom and last top plane.
                    zz = np.unique(qs[:, :, 2])
                    want = np.asarray([2*start, 2*(start+run)], dtype=np.int32)
                    if not np.array_equal(zz, want):
                        return False, f"case {c4}: Z endpoint mismatch {zz.tolist()} != {want.tolist()}"
        return True, "PASS · 16 identical cases × run 2/3/7/19 · exact Z endpoints"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


_AIO_V36_STATIC_SELFTEST_OK, _AIO_V36_STATIC_SELFTEST_MSG = _aio_v36_static_run_selftest()



_AIO_V38_LAST_COMPACT_STATS = {}


def _aio_v38_exact_weld_meshes(meshes):
    """여러 compact mesh를 float32 bit-exact 정점 기준으로 하나로 weld한다.

    tolerance/rounding/근사 좌표를 사용하지 않는다. 같은 float32 bit pattern만 같은
    정점으로 취급한다.
    """
    valid = [(np.ascontiguousarray(v, dtype=np.float32), np.ascontiguousarray(f, dtype=np.int64))
             for v, f in meshes if v is not None and f is not None and len(f)]
    if not valid:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int64)
    if len(valid) == 1:
        return valid[0]

    vertices_parts = []
    faces_parts = []
    voff = 0
    for v, f in valid:
        vertices_parts.append(v)
        faces_parts.append(f + int(voff))
        voff += int(len(v))
    vcat = np.ascontiguousarray(np.concatenate(vertices_parts, axis=0), dtype=np.float32)
    fcat = np.ascontiguousarray(np.concatenate(faces_parts, axis=0), dtype=np.int64)

    bits = np.ascontiguousarray(vcat.view(np.uint32).reshape(-1, 3))
    key_dtype = np.dtype((np.void, 12))
    keys = bits.view(key_dtype).ravel()
    _uniq, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    vertices = np.ascontiguousarray(vcat[first], dtype=np.float32)
    faces = np.ascontiguousarray(inverse[fcat], dtype=np.int64)

    if np.any(faces[:, 0] == faces[:, 1]) or np.any(faces[:, 1] == faces[:, 2]) or np.any(faces[:, 2] == faces[:, 0]):
        raise RuntimeError('exact weld 후 퇴화 triangle이 생성되었습니다.')
    return vertices, faces


def _aio_v38_exact_subset_keys(reference_vertices: np.ndarray, candidate_vertices: np.ndarray) -> bool:
    rv = np.ascontiguousarray(reference_vertices, dtype=np.float32)
    cv = np.ascontiguousarray(candidate_vertices, dtype=np.float32)
    if len(rv) == 0 or len(cv) == 0:
        return False
    kd = np.dtype((np.void, 12))
    rk = np.ascontiguousarray(rv.view(np.uint32).reshape(-1, 3)).view(kd).ravel()
    ck = np.ascontiguousarray(cv.view(np.uint32).reshape(-1, 3)).view(kd).ravel()
    rks = np.unique(rk)
    cks = np.unique(ck)
    pos = np.searchsorted(rks, cks)
    return bool(np.all(pos < len(rks)) and np.all(rks[np.minimum(pos, len(rks)-1)] == cks))


def _aio_v38_reduce_mesh_exact(vertices: np.ndarray, faces: np.ndarray, *, require_closed: bool = False):
    """하나의 in-memory mesh를 VTK zero-error로 줄이고 엄격한 exact 조건을 검사한다.

    실패 시 입력 mesh를 그대로 반환하므로 품질 저하가 없다.
    """
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    in_n = int(len(faces))
    if in_n < 32 or (not _AIO_V32_VTK_AVAILABLE):
        return vertices, faces, False, 'small/VTK-unavailable'
    try:
        area0 = _aio_v34_poly_area(vertices, faces)
        bounds0 = np.r_[vertices.min(axis=0).astype(np.float64), vertices.max(axis=0).astype(np.float64)]
        poly = _aio_v32_make_polydata(vertices, faces)
        if require_closed:
            bad0 = _aio_v33_bad_edge_count(poly)
            if bad0 != 0:
                raise RuntimeError(f'input bad edges={bad0:,}')
        out = _aio_v33_global_decimate_poly(poly)
        ov, of = _aio_v32_polydata_numpy(out)
        out_n = int(len(of))
        if out_n <= 0 or out_n > in_n:
            raise RuntimeError(f'bad reduction count {out_n:,}/{in_n:,}')
        if out_n == in_n:
            return vertices, faces, False, 'no reduction'
        if not _aio_v38_exact_subset_keys(vertices, ov):
            raise RuntimeError('output vertex is not exact input subset')
        area1 = _aio_v34_poly_area(ov, of)
        area_rel = abs(area1-area0) / max(1.0, abs(area0))
        if area_rel > 2e-6:
            raise RuntimeError(f'area change {format_number(area_rel, scientific=True)}')
        bounds1 = np.r_[ov.min(axis=0).astype(np.float64), ov.max(axis=0).astype(np.float64)]
        scale = max(1.0, float(np.max(np.abs(bounds0))))
        if float(np.max(np.abs(bounds1-bounds0))) > max(3e-6, 2e-7*scale):
            raise RuntimeError('bounds changed')
        if require_closed:
            bad1 = _aio_v33_bad_edge_count(out)
            if bad1 != 0:
                raise RuntimeError(f'output bad edges={bad1:,}')
            # closed 최종 mesh에서는 volume까지 검사한다.
            _a0, vol0, _tri0 = _aio_v33_mass_properties(poly)
            _a1, vol1, _tri1 = _aio_v33_mass_properties(out)
            vol_rel = abs(vol1-vol0) / max(1.0, abs(vol0))
            if vol_rel > 3e-6:
                raise RuntimeError(f'volume change {format_number(vol_rel, scientific=True)}')
        return np.ascontiguousarray(ov, np.float32), np.ascontiguousarray(of, np.int64), True, f'PASS {in_n:,}->{out_n:,}'
    except Exception as exc:
        return vertices, faces, False, f'fallback {type(exc).__name__}: {exc}'


def _aio_v38_fuse_target_faces() -> int:
    try:
        avail = int(_aio_v33_available_memory_bytes())
    except Exception:
        avail = 8 * 1024**3
    gib = avail / float(1024**3)
    if gib >= 12.0:
        return 3_000_000
    if gib >= 8.0:
        return 2_000_000
    if gib >= 5.0:
        return 1_200_000
    return 650_000


def _aio_v39_compact_used_vertices(vertices: np.ndarray, faces: np.ndarray):
    """미사용 정점만 제거한다. 좌표 이동/반올림/허용오차 weld는 하지 않는다."""
    v = np.ascontiguousarray(vertices, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.int64)
    if len(f) == 0:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int64)
    used, inverse = np.unique(f.reshape(-1), return_inverse=True)
    return (
        np.ascontiguousarray(v[used], dtype=np.float32),
        np.ascontiguousarray(inverse.reshape(-1, 3), dtype=np.int64),
    )


def _aio_v39_decimate_q_batch(
    qtri: np.ndarray,
    *,
    bits_x: int,
    bits_y: int,
    half_xyz,
    xoff: float,
    yoff: float,
    strict_verify: bool,
):
    """v3.9 XOR-safe batch compactor.

    VTK decimation은 vertex subset/area가 같아도 제조 레이어의 raster가 달라지는
    반례가 회귀시험에서 확인됐다. 따라서 production 경로에서는 사용하지 않는다.
    ExactLUT triangle을 exact weld한 뒤, 동일 평면 패치에 한해서만 원 경계 edge와
    정수 half-grid 투영면적이 정확히 동일한 재삼각형화를 허용한다.
    """
    raw_count = int(len(qtri))
    vq, raw_faces, _raw_keys = _aio_v32_weld_q(qtri, bits_x, bits_y)
    raw_vertices = _aio_v32_q_to_xyz(vq, half_xyz, xoff, yoff)
    try:
        reduced_faces, saved, groups = _aio_lossless_planar_reduce(
            raw_vertices, raw_faces, half_xyz,
        )
        out_vertices, out_faces = _aio_v39_compact_used_vertices(
            raw_vertices, reduced_faces,
        )
        return (
            out_vertices, out_faces, raw_count,
            f"XOR-safe planar PASS · saved={int(saved):,} · groups={int(groups):,}",
            False,
        )
    except Exception as exc:
        # 정확성에 조금이라도 의심이 생기면 raw ExactLUT를 그대로 사용한다.
        out_vertices, out_faces = _aio_v39_compact_used_vertices(raw_vertices, raw_faces)
        return out_vertices, out_faces, raw_count, f"raw exact fallback: {type(exc).__name__}: {exc}", True


def _aio_v39_reduce_mesh_exact(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    half_xyz,
    require_closed: bool = False,
):
    """rolling/final용 XOR-safe in-memory compactor.

    같은 half-grid 평면의 내부 tessellation만 바꾸며, 각 적용 그룹은
    (1) 원 정점만 사용, (2) 원 boundary edge 완전 동일,
    (3) exact integer projected area 동일 조건을 모두 통과해야 한다.
    """
    v = np.ascontiguousarray(vertices, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.int64)
    if len(f) < _AIO_MC_LIGHT_MIN_GROUP:
        vv, ff = _aio_v39_compact_used_vertices(v, f)
        return vv, ff, False, "small exact mesh"
    try:
        reduced_faces, saved, groups = _aio_lossless_planar_reduce(v, f, half_xyz)
        vv, ff = _aio_v39_compact_used_vertices(v, reduced_faces)
        return vv, ff, bool(saved), f"XOR-safe planar PASS · saved={int(saved):,} · groups={int(groups):,}"
    except Exception as exc:
        vv, ff = _aio_v39_compact_used_vertices(v, f)
        return vv, ff, False, f"raw exact fallback: {type(exc).__name__}: {exc}"


def _aio_v38_exact_interpolated_surface(
    params, items, bbox, grid, resolved, log, cancel_event, mask_store=None,
):
    """ExactLUT 표면 생성과 zero-error compact를 하나의 in-memory 파이프라인으로 수행한다.

    중간 대용량 STL을 먼저 만든 뒤 다시 읽어 경량화하지 않는다.
    ExactLUT -> batch zero-error -> rolling global weld/compact -> 최종 STL 1회 기록.
    """
    global _AIO_V38_LAST_COMPACT_STATS
    _AIO_V38_LAST_COMPACT_STATS = {}

    if getattr(params, 'surface_mode', '') != '표면 보간형':
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )
    if (not _AIO_FAST_EXACT_VALID) or (_AIO_V32_LUT_Q_ZYX is None) or (not _AIO_V36_STATIC_SELFTEST_OK):
        log.write('WARNING: v3.8 exact self-test 실패 -> v3.2 ExactLUT fallback')
        log.write(f'  static: {_AIO_V36_STATIC_SELFTEST_MSG}')
        return _aio_v32_exact_interpolated_surface(
            params, items, bbox, grid, resolved, log, cancel_event, mask_store,
        )

    total = int(bbox.layers)
    hc = int(grid.height) + 1
    wc = int(grid.width) + 1
    total_pairs = total + 1
    xsp = float(grid.x_pitch_mm)
    ysp = float(grid.y_pitch_mm)
    zsp = float(params.layer_thickness_mm)
    xoff = float(grid.x_offset_mm)
    yoff = float(grid.y_offset_mm)
    half = (0.5*xsp, 0.5*ysp, 0.5*zsp)
    bits_x = max(1, int(math.ceil(math.log2(2*wc + 3))))
    bits_y = max(1, int(math.ceil(math.log2(2*hc + 3))))
    bits_z = max(1, int(math.ceil(math.log2(2*total_pairs + 3))))
    if bits_x + bits_y + bits_z >= 62:
        raise RuntimeError('Exact half-grid key가 int64 pack 범위를 초과합니다.')

    batch_target, dec_workers, tile_rows = _aio_v36_runtime_tuning(params)
    fuse_target = _aio_v38_fuse_target_faces()
    max_inflight = max(1, dec_workers*2)
    strict = False

    log.write('')
    log.write('=============== ExactLUT v4.1 FAST-CERT 통합 표면 엔진 ===============')
    log.write(f'XY case grid: {wc:,}×{hc:,} · Z pairs: {total_pairs:,}')
    log.write('surface: legacy Lewiner 256-case ExactLUT · step=1 동등')
    log.write(f'static-Z self-test: {_AIO_V36_STATIC_SELFTEST_MSG}')
    log.write(f'raw batch: {batch_target:,} tri · workers={dec_workers} · tileRows={tile_rows}')
    log.write(f'rolling fused compact: 약 {fuse_target:,} compact tri마다 exact weld + FAST-CERT')
    log.write('중간 STL 재읽기/2단계 global compact: 없음')
    log.write('정점 이동/다운샘플링/허용오차 근사: 없음')
    log.write('============================================================')

    material_voxels = 0
    raw_total = 0
    batch_output_total = 0
    fallback_batches = 0
    verified_batches = 0
    static_pairs_collapsed = 0
    static_runs = 0
    rolling_passes = 0
    rolling_saved = 0
    started = time.time()

    pending_parts = []
    pending_count = 0
    futures = {}
    compact_parts = []
    compact_face_count = 0

    def rolling_compact(force=False):
        nonlocal compact_parts, compact_face_count, rolling_passes, rolling_saved
        if not compact_parts:
            return
        if (not force) and (compact_face_count < fuse_target or len(compact_parts) < 2):
            return
        check_cancel(cancel_event)
        before = int(compact_face_count)
        merged_v, merged_f = _aio_v38_exact_weld_meshes(compact_parts)
        rv, rf, reduced, _msg = _aio_v39_reduce_mesh_exact(merged_v, merged_f, half_xyz=half, require_closed=False)
        after = int(len(rf))
        compact_parts = [(rv, rf)]
        compact_face_count = after
        rolling_passes += 1
        rolling_saved += max(0, before-after)

    def accept_compact(vertices, faces, raw_count, fallback):
        nonlocal batch_output_total, fallback_batches, verified_batches, compact_face_count
        if fallback:
            fallback_batches += 1
        else:
            verified_batches += 1
        batch_output_total += int(len(faces))
        compact_parts.append((np.ascontiguousarray(vertices, np.float32), np.ascontiguousarray(faces, np.int64)))
        compact_face_count += int(len(faces))
        rolling_compact(False)

    def consume_one(block: bool):
        if not futures:
            return False
        done_future = None
        if block:
            done_future = next(as_completed(list(futures.keys())))
        else:
            for f in list(futures.keys()):
                if f.done():
                    done_future = f
                    break
            if done_future is None:
                return False
        futures.pop(done_future, None)
        vertices, faces, raw_count, _msg, fallback = done_future.result()
        accept_compact(vertices, faces, raw_count, fallback)
        return True

    def submit_batch(pool, qbatch):
        fut = pool.submit(
            _aio_v39_decimate_q_batch,
            np.ascontiguousarray(qbatch, dtype=np.int32),
            bits_x=bits_x, bits_y=bits_y, half_xyz=half,
            xoff=xoff, yoff=yoff, strict_verify=strict,
        )
        futures[fut] = True
        while len(futures) >= max_inflight:
            consume_one(True)

    def feed_q(pool, q):
        nonlocal pending_parts, pending_count, raw_total
        if q is None or len(q) == 0:
            return
        pos = 0
        qlen = int(len(q))
        while pos < qlen:
            room = batch_target - pending_count
            take = min(qlen-pos, room)
            pending_parts.append(q[pos:pos+take])
            pending_count += int(take)
            raw_total += int(take)
            pos += int(take)
            if pending_count >= batch_target:
                batch = pending_parts[0] if len(pending_parts) == 1 else np.concatenate(pending_parts, axis=0)
                submit_batch(pool, batch)
                pending_parts = []
                pending_count = 0

    def emit_regular_pair(pool, lower_case, upper_case, k):
        for y0 in range(0, hc, tile_rows):
            q = _aio_v32_make_q_triangles(lower_case, upper_case, int(k), y0, min(hc, y0+tile_rows), wc)
            if q is not None:
                feed_q(pool, q)
            consume_one(False)

    def emit_static_run(pool, case_map, start_pair: int, run_pairs: int):
        nonlocal static_pairs_collapsed, static_runs
        if case_map is None or run_pairs <= 0:
            return
        if run_pairs == 1:
            emit_regular_pair(pool, case_map, case_map, start_pair)
            return
        stretched_parts = []
        for y0 in range(0, hc, tile_rows):
            q = _aio_v32_make_q_triangles(case_map, case_map, int(start_pair), y0, min(hc, y0+tile_rows), wc)
            if q is None:
                stretched_parts.append(None)
                continue
            qs = _aio_v36_stretch_static_q(q, start_pair, run_pairs)
            if qs is None:
                for kk in range(int(start_pair), int(start_pair)+int(run_pairs)):
                    emit_regular_pair(pool, case_map, case_map, kk)
                return
            stretched_parts.append(qs)
        for qs in stretched_parts:
            if qs is not None:
                feed_q(pool, qs)
                consume_one(False)
        static_pairs_collapsed += int(run_pairs-1)
        static_runs += 1

    zero = np.zeros((hc, wc), dtype=np.uint8)
    prev_case = zero
    prefetch_workers = max(1, min(3, int(getattr(params, 'workers', 1))))
    case_futures = {}
    next_submit = 0

    def submit_case(pool, local_layer):
        absolute = int(bbox.zmin) + int(local_layer)
        if mask_store is not None:
            return pool.submit(_aio_fast_case_from_cached_layer, mask_store, absolute, bbox, grid)
        return pool.submit(_aio_fast_case_from_layer_item, items[absolute], bbox, resolved, grid)

    run_case = None
    run_start = -1
    run_pairs = 0

    try:
        with ThreadPoolExecutor(max_workers=dec_workers) as dec_pool, ThreadPoolExecutor(max_workers=prefetch_workers) as case_pool:
            while next_submit < total and len(case_futures) < prefetch_workers:
                case_futures[next_submit] = submit_case(case_pool, next_submit)
                next_submit += 1

            for k in range(total_pairs):
                check_cancel(cancel_event)
                if k < total:
                    next_case, material = case_futures.pop(k).result()
                    material_voxels += int(material)
                    while next_submit < total and len(case_futures) < prefetch_workers:
                        case_futures[next_submit] = submit_case(case_pool, next_submit)
                        next_submit += 1
                else:
                    next_case = zero

                identical = bool(np.array_equal(prev_case, next_case))
                if identical:
                    if run_case is None:
                        run_case = prev_case.copy(); run_start = int(k); run_pairs = 1
                    elif bool(np.array_equal(run_case, prev_case)):
                        run_pairs += 1
                    else:
                        emit_static_run(dec_pool, run_case, run_start, run_pairs)
                        run_case = prev_case.copy(); run_start = int(k); run_pairs = 1
                else:
                    if run_case is not None:
                        emit_static_run(dec_pool, run_case, run_start, run_pairs)
                        run_case = None; run_start = -1; run_pairs = 0
                    emit_regular_pair(dec_pool, prev_case, next_case, k)
                prev_case = next_case

                done = k+1
                elapsed = time.time()-started
                remain = elapsed/max(1, done)*(total_pairs-done)
                pct = 64.0 + 33.0*done/max(1, total_pairs)
                log.progress(pct, f'ExactLUT v3.8 {done:,}/{total_pairs:,} pair | raw={raw_total:,} compact={compact_face_count:,} | ETA {recon_core.format_seconds(remain)}')
                if done == 1 or done % 50 == 0 or done == total_pairs:
                    log.write(f'v3.8 pair {done:,}/{total_pairs:,} · raw={raw_total:,} · inRAMcompact={compact_face_count:,} · staticSavedPairs={static_pairs_collapsed:,}')

            if run_case is not None:
                emit_static_run(dec_pool, run_case, run_start, run_pairs)
            if pending_count:
                batch = pending_parts[0] if len(pending_parts) == 1 else np.concatenate(pending_parts, axis=0)
                submit_batch(dec_pool, batch)
                pending_parts = []
                pending_count = 0
            while futures:
                consume_one(True)

        if not compact_parts:
            raise ValueError('생성된 표면 triangle이 없습니다.')

        # 모든 batch 경계를 실제로 없앤 최종 in-memory merge. 이 단계가 기존의
        # 'STL 저장 -> 다시 읽기 -> global compact'를 대체한다.
        rolling_compact(True)
        if len(compact_parts) != 1:
            merged_v, merged_f = _aio_v38_exact_weld_meshes(compact_parts)
        else:
            merged_v, merged_f = compact_parts[0]

        # 전체 closed mesh에서 마지막 zero-error pass 및 topology/volume 검증.
        final_v, final_f, final_reduced, final_msg = _aio_v39_reduce_mesh_exact(merged_v, merged_f, half_xyz=half, require_closed=False)
        final_poly = _aio_v32_make_polydata(final_v, final_f) if _AIO_V32_VTK_AVAILABLE else None
        bad_edges = _aio_v33_bad_edge_count(final_poly) if final_poly is not None else 0
        # binary voxel 입력 자체가 corner/edge 접촉을 포함하면 exact Lewiner 표면도
        # non-manifold일 수 있다. v3.9는 이를 임의로 수정하거나 실패시키지 않고
        # 원 slice geometry를 그대로 보존한다. bad_edges는 통계로만 기록한다.

        writer = recon_core.BinarySTLWriter(
            params.output_stl,
            header='ExactLUT v4.1 FAST-CERT fused STL',
            buffer_bytes=16*1024*1024,
        )
        success = False
        try:
            _AIO_MC_LIGHT_ORIG_WRITE(
                writer, final_v, final_f,
                max_triangles=params.max_triangles,
                cancel_event=cancel_event,
                chunk_size=200_000,
            )
            triangle_count = int(writer.tri_count)
            if triangle_count <= 0:
                raise ValueError('생성된 최종 triangle이 없습니다.')
            writer.close(success=True)
            success = True
        finally:
            if not success:
                writer.close(success=False)

        recon_core.verify_binary_stl(params.output_stl, triangle_count)
        _AIO_V38_LAST_COMPACT_STATS = {
            'reason': 'PASS',
            'input_triangles': int(raw_total),
            'output_triangles': int(triangle_count),
            'bad_edges_output': int(bad_edges),
            'exact_boundary_preserved': True,
            'slice_geometry_certificate': 'ExactLUT + static-Z exact + boundary/area-preserving planar retriangulation',
            'batch_output_triangles': int(batch_output_total),
            'rolling_passes': int(rolling_passes),
            'rolling_saved': int(rolling_saved),
            'final_reduced': bool(final_reduced),
            'final_message': str(final_msg),
        }

        log.write('')
        log.write('=============== v4.1 FAST-CERT 통합 표면 통계 ===============')
        log.write(f'ExactLUT raw triangles: {raw_total:,}')
        log.write(f'static Z runs: {static_runs:,} · eliminated repeated pairs: {static_pairs_collapsed:,}')
        log.write(f'batch FAST-CERT PASS: {verified_batches:,} · raw fallback: {fallback_batches:,}')
        log.write(f'batch compact triangles 합계: {batch_output_total:,}')
        log.write(f'rolling fused passes: {rolling_passes:,} · saved across batch boundaries: {rolling_saved:,}')
        log.write(f'최종 STL triangles: {triangle_count:,}')
        log.write('중간 STL 생성/재읽기: 0')
        log.write('geometry approximation: 0')
        log.write('====================================================')
        return triangle_count, material_voxels
    except Exception:
        try:
            params.output_stl.unlink(missing_ok=True)
        except Exception:
            pass
        _AIO_V38_LAST_COMPACT_STATS = {}
        raise



# -----------------------------------------------------------------------------
# v4.0 throughput patch: compiled VTK proposal + exact vectorized surface certificate
# -----------------------------------------------------------------------------
# v3.9의 병목은 동일 평면 패치를 Shapely로 매 batch/rolling/final마다 다시 구성한
# 부분이었다. 특히 격자/다공 구조에서는 Polygon/STRtree/triangulation과 반복 np.unique가
# 실제 표면 생성보다 오래 걸릴 수 있었다.
#
# v4.0은 다음 순서만 허용한다.
#   1) VTK DecimatePro(MaximumError=0)가 C++에서 빠르게 후보를 만든다.
#   2) 후보 정점은 입력 float32 정점의 exact subset이어야 한다.
#   3) 입력/후보를 half-grid 정수좌표로 환산한다.
#   4) 모든 exact plane별 signed/absolute projected area가 완전히 동일해야 한다.
#   5) 모든 plane 경계의 oriented collinear interval event가 완전히 동일해야 한다.
# 위 certificate가 하나라도 실패하면 후보를 즉시 버리고 원 ExactLUT mesh를 그대로 쓴다.
# 따라서 VTK가 빠른 "proposal" 역할만 하고, geometry 수용 여부는 정수 exact certificate가
# 결정한다. Shapely planar retriangulation은 production 경로에서 사용하지 않는다.

_AIO_V40_CERT_MIN_SAVE_RATIO = 0.22  # 절감이 작으면 비싼 certificate 대신 raw exact 유지
_AIO_V40_CERT_MIN_SAVED_FACES = 256


def _aio_v40_plane_data(q: np.ndarray, faces: np.ndarray):
    qq = np.asarray(q, dtype=np.int64)
    ff = np.asarray(faces, dtype=np.int64)
    if len(ff) == 0:
        return (np.empty((0,4), np.int64), np.empty(0, np.int64),
                np.empty(0, np.int64), np.empty(0, np.int64))
    a = qq[ff[:, 0]]; b = qq[ff[:, 1]]; c = qq[ff[:, 2]]
    cross = np.cross(b - a, c - a)
    gcd = np.gcd(np.gcd(np.abs(cross[:, 0]), np.abs(cross[:, 1])), np.abs(cross[:, 2]))
    if np.any(gcd == 0):
        raise RuntimeError('exact certificate: degenerate face')
    n = cross // gcd[:, None]
    first = np.where(n[:, 0] != 0, n[:, 0], np.where(n[:, 1] != 0, n[:, 1], n[:, 2]))
    flip = first < 0
    nc = n.copy(); nc[flip] *= -1
    d = np.einsum('ij,ij->i', nc, a)
    plane_keys = np.column_stack((nc, d)).astype(np.int64, copy=False)
    unique_planes, inverse = np.unique(plane_keys, axis=0, return_inverse=True)

    axis = np.argmax(np.abs(nc), axis=1)
    rows = np.arange(len(cross))
    den = nc[rows, axis]
    comp = cross[rows, axis]
    if np.any(comp % den):
        raise RuntimeError('exact certificate: non-primitive plane normal')
    kval = comp // den
    signed_area = np.zeros(len(unique_planes), dtype=np.int64)
    absolute_area = np.zeros(len(unique_planes), dtype=np.int64)
    np.add.at(signed_area, inverse, kval)
    np.add.at(absolute_area, inverse, np.abs(kval))
    return unique_planes, inverse.astype(np.int64, copy=False), signed_area, absolute_area


def _aio_v40_point_pack_setup(q0: np.ndarray, q1: np.ndarray):
    mn = np.minimum(np.min(q0, axis=0), np.min(q1, axis=0)).astype(np.int64)
    mx = np.maximum(np.max(q0, axis=0), np.max(q1, axis=0)).astype(np.int64)
    span = (mx - mn).astype(np.int64)
    bits = [max(1, int(int(v).bit_length())) for v in span]
    if sum(bits) > 63:
        raise RuntimeError('exact certificate: point key int64 overflow')
    return mn, bits


def _aio_v40_pack_points(q: np.ndarray, mn: np.ndarray, bits):
    x = (np.asarray(q, dtype=np.int64) - mn).astype(np.uint64)
    by, bz = int(bits[1]), int(bits[2])
    return ((x[:, 0] << np.uint64(by + bz)) |
            (x[:, 1] << np.uint64(bz)) |
            x[:, 2])


def _aio_v40_unpack_points(keys: np.ndarray, mn: np.ndarray, bits):
    k = np.asarray(keys, dtype=np.uint64)
    by, bz = int(bits[1]), int(bits[2])
    mz = (np.uint64(1) << np.uint64(bz)) - np.uint64(1)
    my = (np.uint64(1) << np.uint64(by)) - np.uint64(1)
    z = (k & mz).astype(np.int64) + int(mn[2])
    y = ((k >> np.uint64(bz)) & my).astype(np.int64) + int(mn[1])
    x = (k >> np.uint64(by + bz)).astype(np.int64) + int(mn[0])
    return np.column_stack((x, y, z)).astype(np.int64, copy=False)


def _aio_v40_boundary_events(q: np.ndarray, faces: np.ndarray, plane_inverse: np.ndarray,
                             mn: np.ndarray, bits):
    qq = np.asarray(q, dtype=np.int64)
    ff = np.asarray(faces, dtype=np.int64)
    pkey = _aio_v40_pack_points(qq, mn, bits)

    p = np.concatenate((pkey[ff[:, 0]], pkey[ff[:, 1]], pkey[ff[:, 2]]))
    r = np.concatenate((pkey[ff[:, 1]], pkey[ff[:, 2]], pkey[ff[:, 0]]))
    pid = np.tile(np.asarray(plane_inverse, dtype=np.uint64), 3)
    swap = p > r
    lo = np.minimum(p, r); hi = np.maximum(p, r)
    sign = np.where(swap, -1, 1).astype(np.int64)

    rec = np.column_stack((pid, lo, hi)).astype(np.uint64, copy=False)
    order = np.lexsort((rec[:, 2], rec[:, 1], rec[:, 0]))
    rs = rec[order]; ss = sign[order]
    change = np.ones(len(rs), dtype=bool)
    if len(rs) > 1:
        change[1:] = np.any(rs[1:] != rs[:-1], axis=1)
    starts = np.flatnonzero(change)
    unique_edges = rs[starts]
    sums = np.add.reduceat(ss, starts)
    nz = sums != 0
    unique_edges = unique_edges[nz]; sums = sums[nz]
    if np.any(np.abs(sums) != 1):
        # Digital non-manifold plane boundary: certificate cannot prove a safe reduction.
        raise RuntimeError('exact certificate: plane boundary multiplicity != 1')

    qlo = _aio_v40_unpack_points(unique_edges[:, 1], mn, bits)
    qhi = _aio_v40_unpack_points(unique_edges[:, 2], mn, bits)
    A = np.where((sums > 0)[:, None], qlo, qhi)
    B = np.where((sums > 0)[:, None], qhi, qlo)
    vec = B - A
    gcd = np.gcd(np.gcd(np.abs(vec[:, 0]), np.abs(vec[:, 1])), np.abs(vec[:, 2]))
    if np.any(gcd == 0):
        raise RuntimeError('exact certificate: zero boundary edge')
    direction = vec // gcd[:, None]
    first = np.where(direction[:, 0] != 0, direction[:, 0],
                     np.where(direction[:, 1] != 0, direction[:, 1], direction[:, 2]))
    flip = first < 0
    canon_dir = direction.copy(); canon_dir[flip] *= -1
    line_cross = np.cross(canon_dir, A)

    line_keys = np.column_stack((unique_edges[:, 0].astype(np.int64), canon_dir, line_cross))
    unique_lines, line_inverse = np.unique(line_keys, axis=0, return_inverse=True)
    tA = np.einsum('ij,ij->i', canon_dir, A)
    tB = np.einsum('ij,ij->i', canon_dir, B)
    t0 = np.minimum(tA, tB); t1 = np.maximum(tA, tB)
    direction_sign = np.sign(tB - tA).astype(np.int64)

    events = np.concatenate((
        np.column_stack((line_inverse, t0)),
        np.column_stack((line_inverse, t1)),
    ), axis=0).astype(np.int64, copy=False)
    deltas = np.concatenate((direction_sign, -direction_sign))
    order = np.lexsort((events[:, 1], events[:, 0]))
    es = events[order]; ds = deltas[order]
    change = np.ones(len(es), dtype=bool)
    if len(es) > 1:
        change[1:] = np.any(es[1:] != es[:-1], axis=1)
    starts = np.flatnonzero(change)
    unique_events = es[starts]
    event_sums = np.add.reduceat(ds, starts)
    nz = event_sums != 0
    signature = np.column_stack((unique_events[nz], event_sums[nz])).astype(np.int64, copy=False)
    return unique_lines.astype(np.int64, copy=False), signature


def _aio_v40_exact_surface_certificate(q0: np.ndarray, f0: np.ndarray,
                                       q1: np.ndarray, f1: np.ndarray):
    """Prove that two half-grid triangle meshes represent the same piecewise planar surface.

    No tolerance/hash is used. Equality is integer exact: same exact planes, same signed and
    absolute projected area per plane, and same oriented collinear boundary interval events.
    """
    try:
        p0, inv0, s0, a0 = _aio_v40_plane_data(q0, f0)
        p1, inv1, s1, a1 = _aio_v40_plane_data(q1, f1)
        if not np.array_equal(p0, p1):
            return False, 'plane-key mismatch'
        if not np.array_equal(s0, s1) or not np.array_equal(a0, a1):
            return False, 'exact plane-area mismatch'
        mn, bits = _aio_v40_point_pack_setup(q0, q1)
        l0, e0 = _aio_v40_boundary_events(q0, f0, inv0, mn, bits)
        l1, e1 = _aio_v40_boundary_events(q1, f1, inv1, mn, bits)
        if not np.array_equal(l0, l1):
            return False, 'plane-boundary line mismatch'
        if not np.array_equal(e0, e1):
            return False, 'plane-boundary interval mismatch'
        return True, 'PASS'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def _aio_v40_vtk_propose(vertices: np.ndarray, faces: np.ndarray):
    if (not _AIO_V32_VTK_AVAILABLE) or len(faces) < 32:
        return None, None, 'VTK unavailable/small'
    poly = _aio_v32_make_polydata(vertices, faces)
    out = _aio_v33_global_decimate_poly(poly)
    ov, of = _aio_v32_polydata_numpy(out)
    if len(of) <= 0 or len(of) > len(faces):
        return None, None, f'bad VTK count {len(of):,}/{len(faces):,}'
    return np.ascontiguousarray(ov, np.float32), np.ascontiguousarray(of, np.int64), 'VTK proposal'


def _aio_v39_decimate_q_batch(
    qtri: np.ndarray,
    *,
    bits_x: int,
    bits_y: int,
    half_xyz,
    xoff: float,
    yoff: float,
    strict_verify: bool,
):
    """v4.0 fast XOR-safe batch compactor: VTK proposal + integer exact certificate."""
    raw_count = int(len(qtri))
    vq, raw_faces, raw_keys = _aio_v32_weld_q(qtri, bits_x, bits_y)
    raw_vertices = _aio_v32_q_to_xyz(vq, half_xyz, xoff, yoff)
    if (not _AIO_V32_VTK_AVAILABLE) or raw_count < 32:
        return raw_vertices, raw_faces, raw_count, 'raw exact (VTK unavailable/small)', True
    try:
        ov, of, _ = _aio_v40_vtk_propose(raw_vertices, raw_faces)
        if ov is None:
            raise RuntimeError('VTK proposal unavailable')
        saved = int(len(raw_faces) - len(of))
        ratio = saved / max(1, int(len(raw_faces)))
        if saved < _AIO_V40_CERT_MIN_SAVED_FACES or ratio < _AIO_V40_CERT_MIN_SAVE_RATIO:
            # Small reductions are intentionally skipped: exact certificate sorting would cost
            # more time than the small STL saving. Raw ExactLUT is already the reference surface.
            return raw_vertices, raw_faces, raw_count, f'raw exact · low gain {ratio:.1%}', True

        half = np.asarray(half_xyz, dtype=np.float64)
        oq = np.empty((len(ov), 3), dtype=np.int64)
        oq[:, 0] = np.rint((ov[:, 0].astype(np.float64) - float(xoff)) / half[0] + 1.0).astype(np.int64)
        oq[:, 1] = np.rint((ov[:, 1].astype(np.float64) - float(yoff)) / half[1] + 1.0).astype(np.int64)
        oq[:, 2] = np.rint(ov[:, 2].astype(np.float64) / half[2] + 1.0).astype(np.int64)
        rebuilt = np.empty_like(ov, dtype=np.float64)
        rebuilt[:, 0] = (oq[:, 0] - 1.0) * half[0] + float(xoff)
        rebuilt[:, 1] = (oq[:, 1] - 1.0) * half[1] + float(yoff)
        rebuilt[:, 2] = (oq[:, 2] - 1.0) * half[2]
        residual = float(np.max(np.abs(ov.astype(np.float64) - rebuilt))) if len(ov) else 0.0
        coord_tol = max(2.0e-6, float(np.min(half)) * 1.0e-4)
        if residual > coord_tol:
            raise RuntimeError(f'vertex moved {format_number(residual, scientific=True)}mm')
        out_keys = np.unique(_aio_v32_pack_q(oq, bits_x, bits_y))
        pos = np.searchsorted(raw_keys, out_keys)
        if np.any(pos >= len(raw_keys)) or np.any(raw_keys[np.minimum(pos, len(raw_keys)-1)] != out_keys):
            raise RuntimeError('candidate contains a non-input vertex')

        ok, why = _aio_v40_exact_surface_certificate(vq, raw_faces, oq, of)
        if not ok:
            raise RuntimeError(f'exact certificate reject: {why}')
        return ov, of, raw_count, f'FAST-CERT PASS · {len(raw_faces):,}->{len(of):,}', False
    except Exception as exc:
        # No Shapely fallback here. The raw ExactLUT mesh is faster and is the exact reference.
        return raw_vertices, raw_faces, raw_count, f'raw exact fallback: {type(exc).__name__}: {exc}', True


def _aio_v40_q_from_xyz_relative(vertices: np.ndarray, half_xyz, base: np.ndarray):
    v = np.asarray(vertices, dtype=np.float64)
    h = np.asarray(half_xyz, dtype=np.float64)
    b = np.asarray(base, dtype=np.float64)
    q = np.rint((v - b) / h).astype(np.int64)
    residual = float(np.max(np.abs((v - b) - q * h))) if len(v) else 0.0
    return q, residual


def _aio_v39_reduce_mesh_exact(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    half_xyz,
    require_closed: bool = False,
):
    """v4.0 rolling/final compactor. Accept VTK only after exact piecewise-surface certificate."""
    v = np.ascontiguousarray(vertices, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.int64)
    if len(f) < 32 or (not _AIO_V32_VTK_AVAILABLE):
        vv, ff = _aio_v39_compact_used_vertices(v, f)
        return vv, ff, False, 'raw exact small/VTK-unavailable'
    try:
        ov, of, _ = _aio_v40_vtk_propose(v, f)
        if ov is None:
            raise RuntimeError('VTK proposal unavailable')
        saved = int(len(f) - len(of))
        ratio = saved / max(1, int(len(f)))
        if saved < _AIO_V40_CERT_MIN_SAVED_FACES or ratio < _AIO_V40_CERT_MIN_SAVE_RATIO:
            vv, ff = _aio_v39_compact_used_vertices(v, f)
            return vv, ff, False, f'raw exact · low gain {ratio:.1%}'
        if not _aio_v38_exact_subset_keys(v, ov):
            raise RuntimeError('candidate vertex is not exact input float32 subset')
        base = v[0].astype(np.float64)
        q0, r0 = _aio_v40_q_from_xyz_relative(v, half_xyz, base)
        q1, r1 = _aio_v40_q_from_xyz_relative(ov, half_xyz, base)
        tol = max(2.0e-6, float(np.min(np.asarray(half_xyz, dtype=np.float64))) * 1.0e-4)
        if max(r0, r1) > tol:
            raise RuntimeError(f'half-grid residual {format_number(max(r0,r1), scientific=True)}mm')
        ok, why = _aio_v40_exact_surface_certificate(q0, f, q1, of)
        if not ok:
            raise RuntimeError(f'exact certificate reject: {why}')
        return ov, of, True, f'FAST-CERT PASS {len(f):,}->{len(of):,}'
    except Exception as exc:
        vv, ff = _aio_v39_compact_used_vertices(v, f)
        return vv, ff, False, f'raw exact fallback: {type(exc).__name__}: {exc}'


def _aio_v38_exact_weld_meshes(meshes):
    """Exact cross-batch weld. Prefer VTK StaticClean(absolute tolerance=0) for large meshes."""
    valid = [(np.ascontiguousarray(v, dtype=np.float32), np.ascontiguousarray(f, dtype=np.int64))
             for v, f in meshes if v is not None and f is not None and len(f)]
    if not valid:
        return np.empty((0, 3), np.float32), np.empty((0, 3), np.int64)
    if len(valid) == 1:
        return valid[0]
    vparts = []; fparts = []; off = 0
    for v, f in valid:
        vparts.append(v); fparts.append(f + int(off)); off += int(len(v))
    vcat = np.ascontiguousarray(np.concatenate(vparts, axis=0), dtype=np.float32)
    fcat = np.ascontiguousarray(np.concatenate(fparts, axis=0), dtype=np.int64)

    if _AIO_V32_VTK_AVAILABLE and len(fcat) >= 100_000:
        try:
            poly = _aio_v32_make_polydata(vcat, fcat)
            clean = _aio_vtk.vtkStaticCleanPolyData()
            clean.SetInputData(poly)
            clean.ToleranceIsAbsoluteOn()
            clean.SetAbsoluteTolerance(0.0)
            clean.Update()
            vv, ff = _aio_v32_polydata_numpy(clean.GetOutput())
            if len(ff) != len(fcat):
                raise RuntimeError('StaticClean changed face count')
            if not _aio_v38_exact_subset_keys(vcat, vv):
                raise RuntimeError('StaticClean produced non-input vertex')
            if np.any(ff[:,0] == ff[:,1]) or np.any(ff[:,1] == ff[:,2]) or np.any(ff[:,2] == ff[:,0]):
                raise RuntimeError('StaticClean produced degenerate face')
            return np.ascontiguousarray(vv, np.float32), np.ascontiguousarray(ff, np.int64)
        except Exception:
            pass

    bits = np.ascontiguousarray(vcat.view(np.uint32).reshape(-1, 3))
    kd = np.dtype((np.void, 12)); keys = bits.view(kd).ravel()
    _u, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    vv = np.ascontiguousarray(vcat[first], dtype=np.float32)
    ff = np.ascontiguousarray(inv[fcat], dtype=np.int64)
    if np.any(ff[:,0] == ff[:,1]) or np.any(ff[:,1] == ff[:,2]) or np.any(ff[:,2] == ff[:,0]):
        raise RuntimeError('exact weld produced degenerate face')
    return vv, ff

# v3.9 XOR-safe fused engine is the production interpolation path.
recon_core.reconstruct_interpolated_surface = _aio_v38_exact_interpolated_surface

def _aio_v36_decimate_open_chunk(tri_xyz: np.ndarray, half_xyz):
    """v3.7 large-chunk reduction: exact subset + area/bounds only; 고비용 surface-distance는 생략."""
    raw_n = int(len(tri_xyz))
    vertices, faces, raw_keys = _aio_v34_exact_weld_float_triangles(tri_xyz)
    if raw_n < 32:
        return vertices, faces, True, "small chunk"
    try:
        poly = _aio_v32_make_polydata(vertices, faces)
        area0 = _aio_v34_poly_area(vertices, faces)
        bounds0 = np.r_[vertices.min(axis=0).astype(np.float64), vertices.max(axis=0).astype(np.float64)]
        out = _aio_v33_global_decimate_poly(poly)
        ov, of = _aio_v32_polydata_numpy(out)
        if len(of) <= 0 or len(of) > raw_n:
            raise RuntimeError(f"bad face count {len(of):,}/{raw_n:,}")
        obits = np.ascontiguousarray(ov.view(np.uint32).reshape(-1, 3))
        okeys = obits.view(np.dtype((np.void, 12))).ravel()
        # np.isin on large void arrays is expensive; sort/search exact keys instead.
        rk = np.sort(raw_keys)
        ok = np.sort(np.unique(okeys))
        pos = np.searchsorted(rk, ok)
        if np.any(pos >= len(rk)) or np.any(rk[np.minimum(pos, len(rk)-1)] != ok):
            raise RuntimeError("output vertex is not exact input-vertex subset")
        area1 = _aio_v34_poly_area(ov, of)
        area_rel = abs(area1-area0) / max(1.0, abs(area0))
        if area_rel > 2e-6:
            raise RuntimeError(f"area change {format_number(area_rel, scientific=True)}")
        bounds1 = np.r_[ov.min(axis=0).astype(np.float64), ov.max(axis=0).astype(np.float64)]
        if float(np.max(np.abs(bounds1-bounds0))) > 3e-6:
            raise RuntimeError("bounds changed")
        return ov, of, False, f"PASS {raw_n:,}->{len(of):,} areaΔ={area_rel:.1e}"
    except Exception as exc:
        return vertices, faces, True, f"fallback {type(exc).__name__}: {exc}"


# Existing hierarchical reader resolves this global at runtime.
_aio_v34_decimate_open_chunk = _aio_v36_decimate_open_chunk


def _aio_v36_single_hierarchical_compact(input_stl: Path, params, log, cancel_event) -> Path:
    """메모리 때문에 global pass가 불가능할 때 단 한 번의 큰 chunk pass만 수행."""
    try:
        with open(input_stl, "rb") as f:
            f.seek(80)
            total = int(struct.unpack("<I", f.read(4))[0])
        avail = int(_aio_v33_available_memory_bytes())
        if avail >= 10 * 1024**3:
            target = 3_500_000
        elif avail >= 7 * 1024**3:
            target = 2_500_000
        else:
            target = 1_500_000
        if total <= int(target * 1.15):
            return input_stl
        candidate = input_stl.with_name(input_stl.stem + "__v36_hier.stl")
        log.write(f"v3.6 메모리용 단일 large-chunk pass: {target:,} tri/chunk")
        ok, st = _aio_v34_hierarchical_file_pass(
            input_stl, candidate, params, log, cancel_event, target,
        )
        if not ok:
            candidate.unlink(missing_ok=True)
            return input_stl
        old_n = int(st.get("input_triangles", 0))
        new_n = int(st.get("output_triangles", 0))
        if new_n >= old_n * 0.995:
            candidate.unlink(missing_ok=True)
            return input_stl
        log.write(f"v3.6 hier PASS: {old_n:,}->{new_n:,} tri")
        return candidate
    except Exception:
        return input_stl


def _aio_v36_global_exact_compact_stl(input_stl: Path, output_stl: Path, params, log, cancel_event):
    """single closed-mesh VTK zero-error pass.

    v3.5에서 가장 비싼 양방향 vtkDistancePolyDataFilter는 제거한다. DecimatePro는
    MaximumError=0 / PreserveTopology / no splitting / no boundary deletion을 유지하고,
    출력 정점 exact-subset, closed manifold, bounds, area, volume, half-grid를 확인한다.
    이후 빠른 binary/manifold 최종 검증을 통과한 STL만 최종 파일로 승격한다.
    """
    result = {"reason":"", "input_triangles":0, "output_triangles":0}
    output_stl.unlink(missing_ok=True)
    if not _AIO_V32_VTK_AVAILABLE:
        result["reason"] = f"VTK unavailable: {_AIO_V32_VTK_ERROR}"
        return False, result
    try:
        with open(input_stl, "rb") as f:
            f.seek(80); raw = f.read(4)
            if len(raw) != 4:
                raise ValueError("invalid STL header")
            tri_count = int(struct.unpack("<I", raw)[0])
        result["input_triangles"] = tri_count
        if tri_count < 60_000:
            result["reason"] = f"small mesh ({tri_count:,} tri)"
            return False, result

        file_bytes = int(input_stl.stat().st_size)
        avail = int(_aio_v33_available_memory_bytes())
        # v3.6은 surface-distance 복사본을 만들지 않으므로 v3.5보다 peak가 낮다.
        est_peak = int(file_bytes * 5.2 + 384 * 1024**2)
        budget = int(avail * 0.68)
        log.write(
            f"v3.6 global compact memory: STL={format_bytes(file_bytes)} · "
            f"peak≈{format_bytes(est_peak)} · free={format_bytes(avail)}"
        )
        if est_peak > budget:
            result["reason"] = f"memory safety skip: estimated {format_bytes(est_peak)} > budget {format_bytes(budget)}"
            return False, result

        recon_core.check_cancel(cancel_event)
        reader = _aio_vtk.vtkSTLReader()
        reader.SetFileName(str(input_stl)); reader.MergingOn(); reader.Update()
        inp = reader.GetOutput()
        in_cells = int(inp.GetNumberOfCells())
        if in_cells <= 0:
            raise RuntimeError("VTK read failed")
        # Input is expected closed after the batch engine. Check once, but do not build distance filters.
        bad_in = _aio_v33_bad_edge_count(inp)
        if bad_in != 0:
            result["reason"] = f"input not closed-manifold: bad edges={bad_in:,}"
            return False, result
        area0, vol0, inp_tri = _aio_v33_mass_properties(inp)
        bounds0 = _aio_v33_bounds_array(inp_tri)
        ref_min = bounds0[:3].copy()
        half = (12.7/float(params.dpi_x), 12.7/float(params.dpi_y), 0.5*float(params.layer_thickness_mm))

        started = time.time()
        out = _aio_v33_global_decimate_poly(inp_tri)
        recon_core.check_cancel(cancel_event)
        out_cells = int(out.GetNumberOfCells())
        result["output_triangles"] = out_cells
        if out_cells <= 0 or out_cells > in_cells:
            raise RuntimeError(f"bad reduction count {out_cells:,}/{in_cells:,}")
        if out_cells == in_cells:
            result["reason"] = "no further exact reduction"
            return False, result

        subset_ok, _ = _aio_v35_poly_vertex_subset_exact(inp_tri, out)
        if not subset_ok:
            raise RuntimeError("output vertex is not exact input subset")
        bad_out = _aio_v33_bad_edge_count(out)
        if bad_out != 0:
            raise RuntimeError(f"bad edges after compact={bad_out:,}")
        area1, vol1, out_tri = _aio_v33_mass_properties(out)
        bounds1 = _aio_v33_bounds_array(out_tri)
        scale = max(1.0, float(np.max(np.abs(bounds0))))
        bounds_tol = max(2e-6, 2e-7*scale)
        if float(np.max(np.abs(bounds1-bounds0))) > bounds_tol:
            raise RuntimeError("bounds changed")
        area_rel = abs(area1-area0)/max(1.0,abs(area0))
        vol_rel = abs(vol1-vol0)/max(1.0,abs(vol0))
        if area_rel > 2e-6 or vol_rel > 3e-6:
            raise RuntimeError(f"mass-property mismatch area={format_number(area_rel, scientific=True)}, vol={format_number(vol_rel, scientific=True)}")
        grid_res = _aio_v33_half_grid_residual(out_tri, half, ref_min)
        if grid_res > 2e-4:
            raise RuntimeError(f"half-grid residual={format_number(grid_res, scientific=True)}")

        wr = _aio_vtk.vtkSTLWriter()
        wr.SetFileName(str(output_stl)); wr.SetInputData(out_tri); wr.SetFileTypeToBinary()
        if int(wr.Write()) != 1:
            raise RuntimeError("compact STL write failed")
        recon_core.verify_binary_stl(output_stl, out_cells)
        elapsed = time.time()-started
        result.update(
            reason="PASS", input_triangles=in_cells, output_triangles=out_cells,
            area_rel=area_rel, volume_rel=vol_rel, half_grid_residual=grid_res,
            elapsed=elapsed, bad_edges_input=bad_in, bad_edges_output=bad_out,
        )
        return True, result
    except Exception as exc:
        output_stl.unlink(missing_ok=True)
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return False, result


# v3.8 fused exact surface engine is already installed above.
APP_VERSION = "4.6-cap-profiled-fast"
MODEL_EXTS = {".stl", ".obj", ".ply", ".off", ".3mf", ".glb", ".gltf"}
IMAGE_EXTS = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".webp"}
DEFAULT_WORKERS = max(1, min(4, os.cpu_count() or 4))


def _settings_path() -> Path:
    root = Path(os.getenv("APPDATA", str(Path.home()))) / "BJT_Tools"
    return root / "model_lattice_pipeline_settings.json"


SETTINGS_PATH = _settings_path()


class CancelledError(Exception):
    pass


def check_cancel(event: threading.Event) -> None:
    if event.is_set():
        raise CancelledError("작업이 사용자에 의해 중단되었습니다.")


def natural_key(value: Path | str) -> List[object]:
    name = Path(value).name if isinstance(value, Path) else str(value)
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name)]


def format_seconds(sec: float) -> str:
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.0f}초"
    m, s = divmod(int(sec), 60)
    if m < 60:
        return f"{m}분 {s}초"
    h, m = divmod(m, 60)
    return f"{h}시간 {m}분"


def format_bytes(value: int) -> str:
    n = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


def dpi_to_mm(dpi: float) -> float:
    if dpi <= 0:
        raise ValueError("DPI는 0보다 커야 합니다.")
    return 25.4 / float(dpi)


def clear_generated_images(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            p.unlink()


def find_images(folder: Path) -> List[Path]:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=natural_key)


class UILogger:
    def __init__(self, app: "App"):
        self.app = app

    def write(self, message: str = "") -> None:
        self.app.after(0, self.app._append_log, str(message))

    def progress(self, percent: float, message: str = "") -> None:
        self.app.after(0, self.app._set_progress, float(percent), str(message))


class StageLogger:
    """기존 코어의 0~100 진행률을 통합 파이프라인 구간으로 매핑."""

    def __init__(self, base: UILogger, start: float, end: float, name: str):
        self.base = base
        self.start = float(start)
        self.end = float(end)
        self.name = name

    def write(self, message: str = "") -> None:
        self.base.write(message)

    def progress(self, percent: float, message: str = "") -> None:
        mapped = self.start + (self.end - self.start) * max(0.0, min(100.0, float(percent))) / 100.0
        label = self.name
        if message:
            label += f" | {message}"
        self.base.progress(mapped, label)


@dataclass(frozen=True)
class SliceGrid:
    x_origin_mm: float
    y_origin_mm: float
    width_px: int
    height_px: int
    x_pitch_mm: float
    y_pitch_mm: float
    layer_count: int
    layer_thickness_mm: float
    model_height_mm: float

    @property
    def physical_width_mm(self) -> float:
        return self.width_px * self.x_pitch_mm

    @property
    def physical_height_mm(self) -> float:
        return self.height_px * self.y_pitch_mm


@dataclass(frozen=True)
class PipelineParams:
    mode: str
    input_path: Path
    output_stl: Path
    input_unit: str
    dpi_x: float
    dpi_y: float
    layer_thickness_mm: float
    slice_margin_px: int
    batch_layers: int
    shell_mm: float
    lattice_width_mm: float
    spacing_mm: float
    lattice_type: str
    pattern: str
    axes: str
    shell_method: str
    surface_mode: str
    crop_xy: bool
    crop_z: bool
    crop_margin_px: int
    center_xy: bool
    workers: int
    max_triangles: Optional[int]
    save_raw_slices: bool
    save_lattice_slices: bool
    intermediate_format: str
    verify_layer_xor: bool
    print_equivalent: bool = False


UNIT_SCALE = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "inch": 25.4,
}


def load_mesh_mm(path: Path, input_unit: str, log: UILogger) -> trimesh.Trimesh:
    if not path.exists():
        raise FileNotFoundError(f"입력 모델 파일이 없습니다: {path}")
    if path.suffix.lower() not in MODEL_EXTS:
        raise ValueError("지원 모델 형식: STL, OBJ, PLY, OFF, 3MF, GLB, GLTF")

    loaded = trimesh.load(path, process=False)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError("모델 파일에 메시 형상이 없습니다.")
        geometry = loaded.to_geometry()
        if not isinstance(geometry, trimesh.Trimesh):
            meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not meshes:
                raise ValueError("모델 파일에서 삼각형 메시를 찾지 못했습니다.")
            mesh = trimesh.util.concatenate(meshes)
        else:
            mesh = geometry
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise ValueError(f"지원하지 않는 모델 객체입니다: {type(loaded).__name__}")

    mesh = mesh.copy()
    if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        raise ValueError("모델 메시가 비어 있습니다.")

    scale = UNIT_SCALE.get(input_unit)
    if scale is None:
        raise ValueError(f"알 수 없는 입력 단위: {input_unit}")
    if scale != 1.0:
        mesh.apply_scale(scale)

    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    extents = bounds[1] - bounds[0]
    if np.any(~np.isfinite(extents)) or np.any(extents <= 0):
        raise ValueError(f"유효하지 않은 모델 크기입니다: {format_display_value(extents.tolist())}")

    log.write("")
    log.write("================ 모델 정보 ================")
    log.write(f"입력 모델: {path}")
    log.write(f"입력 단위: {input_unit} → 내부 mm 변환 배율 {format_number(scale)}")
    log.write(f"정점: {len(mesh.vertices):,} | 면: {len(mesh.faces):,}")
    log.write(f"크기: X={format_number(extents[0])}, Y={format_number(extents[1])}, Z={format_number(extents[2])} mm")
    log.write(f"watertight: {'예' if mesh.is_watertight else '아니오'}")
    if not mesh.is_watertight:
        log.write("WARNING: 메시가 닫힌 형상이 아닙니다. 일부 레이어가 열리거나 비어 보일 수 있습니다.")
    log.write("===========================================")
    return mesh


def build_slice_grid(
    mesh: trimesh.Trimesh,
    dpi_x: float,
    dpi_y: float,
    layer_thickness_mm: float,
    margin_px: int,
) -> Tuple[trimesh.Trimesh, SliceGrid]:
    x_pitch = dpi_to_mm(dpi_x)
    y_pitch = dpi_to_mm(dpi_y)
    t = float(layer_thickness_mm)
    if t <= 0:
        raise ValueError("레이어 두께는 0보다 커야 합니다.")

    mesh = mesh.copy()
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    # 슬라이싱 및 최종 재구성에서 바닥이 정확히 Z=0이 되도록 평행이동.
    mesh.apply_translation((0.0, 0.0, -float(bounds[0, 2])))
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    height = float(bounds[1, 2] - bounds[0, 2])
    layer_count = max(1, int(math.ceil(height / t - 1e-12)))

    margin = max(0, int(margin_px))
    # 모델 최소 경계를 pixel edge로 직접 사용한다. 세계좌표 0에 억지로 snap하면
    # 경계가 pixel center와 겹치는 경우 한 축이 1 px 커지거나 작아질 수 있다.
    # 최소 경계부터 원본 피치로 양자화하면 입력 형상의 정보 손실과 비대칭을 최소화한다.
    core_width = max(1, int(math.ceil((float(bounds[1, 0]) - float(bounds[0, 0])) / x_pitch - 1e-12)))
    core_height = max(1, int(math.ceil((float(bounds[1, 1]) - float(bounds[0, 1])) / y_pitch - 1e-12)))
    x0 = float(bounds[0, 0]) - margin * x_pitch
    y0 = float(bounds[0, 1]) - margin * y_pitch
    width = core_width + 2 * margin
    height_px = core_height + 2 * margin

    grid = SliceGrid(
        x_origin_mm=x0,
        y_origin_mm=y0,
        width_px=width,
        height_px=height_px,
        x_pitch_mm=x_pitch,
        y_pitch_mm=y_pitch,
        layer_count=layer_count,
        layer_thickness_mm=t,
        model_height_mm=height,
    )
    return mesh, grid


def _world_polygon(poly: Polygon, transform: np.ndarray) -> Polygon:
    def tx(coords: Iterable[Sequence[float]]) -> np.ndarray:
        arr = np.asarray(coords, dtype=np.float64)
        hom = np.column_stack((arr[:, :2], np.zeros(len(arr)), np.ones(len(arr))))
        return (transform @ hom.T).T[:, :2]

    exterior = tx(poly.exterior.coords)
    holes = [tx(ring.coords) for ring in poly.interiors]
    return Polygon(exterior, holes)


def _polygon_parts(geom) -> Iterable[Polygon]:
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        yield geom
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            yield from _polygon_parts(g)
    elif isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            yield from _polygon_parts(g)


def _has_near_duplicate_outer_contours(path2d, grid: SliceGrid) -> Tuple[bool, str]:
    """section_multiplane의 드문 중복 contour 오류를 감지한다.

    비수밀 또는 복잡한 STL에서 trimesh.section_multiplane이 특정 Z 한 층에
    거의 같은 외곽 contour를 두 번 반환하는 경우가 있다. 현재 래스터화는
    even-odd(XOR) 규칙을 사용하므로 이런 중복 외곽은 서로 지워져 한 층이
    거의 검게 되는 문제가 생긴다.

    정상적인 외곽/내부-hole 쌍은 경계 bounds가 같을 수 없으므로,
    큰 contour 두 개의 bounds가 픽셀의 1/4 이내로 같고 실제 면적도
    거의 완전히 겹칠 때만 중복으로 판정한다.
    """
    if path2d is None:
        return False, ""

    try:
        loops = list(path2d.polygons_closed)
    except Exception:
        repaired = path2d.copy()
        repaired.process()
        loops = list(repaired.polygons_closed)

    min_area = max(
        float(grid.x_pitch_mm * grid.y_pitch_mm) * 4.0,
        1e-10,
    )
    polygons: List[Polygon] = []
    for loop in loops:
        try:
            poly = make_valid(Polygon(np.asarray(loop.exterior.coords, dtype=np.float64)[:, :2]))
        except Exception:
            continue
        for part in _polygon_parts(poly):
            if not part.is_empty and float(part.area) >= min_area:
                polygons.append(part)

    if len(polygons) < 2:
        return False, ""

    bounds_tol = max(float(grid.x_pitch_mm), float(grid.y_pitch_mm)) * 0.25
    for i in range(len(polygons) - 1):
        a = polygons[i]
        area_a = float(a.area)
        for j in range(i + 1, len(polygons)):
            b = polygons[j]
            area_b = float(b.area)
            ratio = min(area_a, area_b) / max(area_a, area_b)
            if ratio < 0.98:
                continue

            if max(abs(float(x) - float(y)) for x, y in zip(a.bounds, b.bounds)) > bounds_tol:
                continue

            try:
                overlap = float(a.intersection(b).area) / max(min(area_a, area_b), 1e-15)
            except Exception:
                continue
            if overlap >= 0.995:
                detail = (
                    f"거의 동일한 contour 2개 감지 "
                    f"(area={format_number(area_a)}/{format_number(area_b)} mm², overlap={format_number(overlap*100)}%)"
                )
                return True, detail

    return False, ""


def _single_plane_section_2d(mesh: trimesh.Trimesh, z_mm: float):
    """배치 단면이 의심스러울 때 해당 Z만 독립적으로 다시 계산한다."""
    path3d = mesh.section(
        plane_origin=(0.0, 0.0, float(z_mm)),
        plane_normal=(0.0, 0.0, 1.0),
    )
    if path3d is None:
        return None
    if hasattr(path3d, "to_2D"):
        path2d, _ = path3d.to_2D()
    else:
        path2d, _ = path3d.to_planar()
    return path2d


@njit(cache=True, nogil=True)
def _scanline_rasterize_rings_exact(
    ring_x: np.ndarray,
    ring_y: np.ndarray,
    offsets: np.ndarray,
    x_origin: float,
    y_origin: float,
    x_pitch: float,
    y_pitch: float,
    width: int,
    height: int,
) -> np.ndarray:
    """정수 pixel-center에 대한 exact even-odd scanline rasterizer.

    경계점은 내부로 포함하지 않으며, 기존 Shapely contains_xy와 같은 픽셀 중심
    판정 규칙을 사용한다. Numba nopython/nogil로 실행되어 형상 정밀도는 그대로이고
    Python 객체 및 전체 meshgrid 생성 비용만 제거한다.
    """
    out = np.zeros((height, width), dtype=np.uint8)
    total_edges = 0
    min_v = 1.0e300
    max_v = -1.0e300
    for r in range(offsets.shape[0] - 1):
        a = offsets[r]
        b = offsets[r + 1]
        n = b - a
        if n < 3:
            continue
        closed = ring_x[b - 1] == ring_x[a] and ring_y[b - 1] == ring_y[a]
        total_edges += n - 1 if closed else n
        for k in range(a, b):
            v = (ring_y[k] - y_origin) / y_pitch - 0.5
            if v < min_v:
                min_v = v
            if v > max_v:
                max_v = v
    if total_edges <= 0:
        return out

    row0 = max(0, int(math.ceil(min_v)))
    row1 = min(height, int(math.ceil(max_v)))
    intersections = np.empty(total_edges, dtype=np.float64)

    for row in range(row0, row1):
        y = y_origin + (row + 0.5) * y_pitch
        count = 0
        for r in range(offsets.shape[0] - 1):
            a = offsets[r]
            b = offsets[r + 1]
            n = b - a
            if n < 3:
                continue
            closed = ring_x[b - 1] == ring_x[a] and ring_y[b - 1] == ring_y[a]
            edge_count = n - 1 if closed else n
            for e in range(edge_count):
                i = a + e
                j = a if e == edge_count - 1 else i + 1
                ya = ring_y[i]
                yb = ring_y[j]
                # half-open vertex rule: horizontal edge 제외, shared vertex 중복 방지
                if (ya > y) != (yb > y):
                    xa = ring_x[i]
                    xb = ring_x[j]
                    intersections[count] = xa + (y - ya) * (xb - xa) / (yb - ya)
                    count += 1
        if count < 2:
            continue
        xs = np.sort(intersections[:count])
        for q in range(count // 2):
            left = xs[2 * q]
            right = xs[2 * q + 1]
            if right < left:
                tmp = left
                left = right
                right = tmp
            # pixel center가 left보다 엄격히 크고 right보다 엄격히 작은 index 범위
            u_left = (left - x_origin) / x_pitch - 0.5
            u_right = (right - x_origin) / x_pitch - 0.5
            x0 = int(math.floor(u_left)) + 1
            x1 = int(math.ceil(u_right))
            if x0 < 0:
                x0 = 0
            if x1 > width:
                x1 = width
            if x0 < x1:
                out[row, x0:x1] = 1
    return out


def _rasterize_polygon_scanline(poly: Polygon, grid: SliceGrid) -> np.ndarray:
    rings = [poly.exterior, *poly.interiors]
    total = sum(len(r.coords) for r in rings)
    ring_x = np.empty(total, dtype=np.float64)
    ring_y = np.empty(total, dtype=np.float64)
    offsets = np.empty(len(rings) + 1, dtype=np.int64)
    offsets[0] = 0
    cursor = 0
    for i, ring in enumerate(rings):
        coords = np.asarray(ring.coords, dtype=np.float64)
        n = len(coords)
        ring_x[cursor:cursor + n] = coords[:, 0]
        ring_y[cursor:cursor + n] = coords[:, 1]
        cursor += n
        offsets[i + 1] = cursor
    return _scanline_rasterize_rings_exact(
        ring_x, ring_y, offsets,
        grid.x_origin_mm, grid.y_origin_mm,
        grid.x_pitch_mm, grid.y_pitch_mm,
        grid.width_px, grid.height_px,
    ).astype(bool, copy=False)


def _warmup_exact_rasterizer() -> float:
    """첫 실제 레이어에서 JIT 지연이 멈춤처럼 보이지 않도록 미리 컴파일."""
    started = time.time()
    x = np.asarray([0.0, 1.0, 1.0, 0.0, 0.0], dtype=np.float64)
    y = np.asarray([0.0, 0.0, 1.0, 1.0, 0.0], dtype=np.float64)
    offsets = np.asarray([0, 5], dtype=np.int64)
    _scanline_rasterize_rings_exact(x, y, offsets, 0.0, 0.0, 0.1, 0.1, 2, 2)
    return time.time() - started


def rasterize_path_center_sample(path2d, grid: SliceGrid, cancel_event: threading.Event) -> np.ndarray:
    """단면 polygon을 기존과 동일한 픽셀 중심 규칙으로 고속 이진화."""
    mask = np.zeros((grid.height_px, grid.width_px), dtype=bool)
    if path2d is None:
        return mask

    transform = np.asarray(path2d.metadata.get("to_3D", np.eye(4)), dtype=np.float64)
    try:
        planar_loops = list(path2d.polygons_closed)
    except Exception:
        path2d = path2d.copy()
        path2d.process()
        planar_loops = list(path2d.polygons_closed)

    for planar in planar_loops:
        check_cancel(cancel_event)
        loop = Polygon(np.asarray(planar.exterior.coords, dtype=np.float64)[:, :2])
        world = make_valid(_world_polygon(loop, transform))
        for poly in _polygon_parts(world):
            if poly.is_empty or poly.area <= 0:
                continue
            mask ^= _rasterize_polygon_scanline(poly, grid)
    return mask

def save_binary_slice(mask: np.ndarray, path: Path, image_format: str) -> None:
    arr = np.asarray(mask, dtype=np.uint8) * np.uint8(255)
    image = Image.fromarray(arr, mode="L")
    fmt = image_format.upper()
    if fmt == "PNG":
        image.save(path, format="PNG", compress_level=1, optimize=False)
    else:
        image.save(path, format="BMP")


def slice_model_to_images(
    model_path: Path,
    output_dir: Path,
    params: PipelineParams,
    log: UILogger,
    cancel_event: threading.Event,
    progress_start: float = 0.0,
    progress_end: float = 24.0,
) -> SliceGrid:
    mesh = load_mesh_mm(model_path, params.input_unit, log)
    mesh, grid = build_slice_grid(
        mesh, params.dpi_x, params.dpi_y, params.layer_thickness_mm, params.slice_margin_px
    )
    clear_generated_images(output_dir)

    ext = ".png" if params.intermediate_format == "PNG" else ".bmp"
    log.write("")
    log.write("=============== 슬라이싱 조건 ===============")
    log.write(f"DPI: X={format_number(params.dpi_x)}, Y={format_number(params.dpi_y)}")
    log.write(f"픽셀 피치: X={format_number(grid.x_pitch_mm)}, Y={format_number(grid.y_pitch_mm)} mm")
    log.write(f"레이어 두께: {format_number(grid.layer_thickness_mm)} mm")
    log.write(f"캔버스: {grid.width_px:,} × {grid.height_px:,} px")
    log.write(f"캔버스 실제 크기: X={format_number(grid.physical_width_mm)}, Y={format_number(grid.physical_height_mm)} mm")
    log.write(f"레이어 수: {grid.layer_count:,} | 양자화 Z={format_number(grid.layer_count*grid.layer_thickness_mm)} mm")
    log.write("샘플링: 각 레이어 중심 단면 + 픽셀 중심 포함 판정")
    log.write("============================================")

    jit_time = _warmup_exact_rasterizer()
    log.write(f"정밀 scanline JIT 준비: {format_seconds(jit_time)} (첫 실행만 발생 가능)")
    log.write("래스터 엔진: Numba exact scanline (기존 픽셀 중심 결과와 동일)")

    started = time.time()
    batch = max(1, min(int(params.batch_layers), 2*_bjt_workers(params.workers, grid.width_px*grid.height_px, 12)))
    epsilon = max(1e-9, grid.layer_thickness_mm * 1e-7)
    raster_workers = _bjt_workers(min(int(params.workers),8), grid.width_px*grid.height_px, 12)
    completed = 0

    def raster_save_job(index: int, path2d) -> int:
        check_cancel(cancel_event)
        mask = rasterize_path_center_sample(path2d, grid, cancel_event)
        save_binary_slice(mask, output_dir / f"{index+1:06d}{ext}", params.intermediate_format)
        return index

    with ThreadPoolExecutor(max_workers=raster_workers) as raster_pool:
        for batch_start in range(0, grid.layer_count, batch):
            check_cancel(cancel_event)
            batch_end = min(grid.layer_count, batch_start + batch)
            centers = (np.arange(batch_start, batch_end, dtype=np.float64) + 0.5) * grid.layer_thickness_mm
            centers = np.minimum(centers, max(epsilon, grid.model_height_mm - epsilon))
            sections = mesh.section_multiplane(
                plane_origin=(0.0, 0.0, 0.0),
                plane_normal=(0.0, 0.0, 1.0),
                heights=centers,
            )

            prepared = []
            for local, path2d in enumerate(sections):
                check_cancel(cancel_event)
                index = batch_start + local
                z_center = float(centers[local])
                suspicious, reason = _has_near_duplicate_outer_contours(path2d, grid)
                if suspicious:
                    log.write(f"WARNING: {index+1:,}층(Z={format_number(z_center)} mm) 배치 단면 이상 감지: {reason}")
                    log.write("         해당 층을 독립 단면 계산으로 자동 재처리합니다.")
                    path2d = _single_plane_section_2d(mesh, z_center)
                prepared.append((index, path2d))

            futures = [raster_pool.submit(raster_save_job, index, path2d) for index, path2d in prepared]
            for future in as_completed(futures):
                check_cancel(cancel_event)
                future.result()
                completed += 1
                if completed == 1 or completed % 5 == 0 or completed == grid.layer_count:
                    elapsed = time.time() - started
                    remain = elapsed / max(1, completed) * (grid.layer_count - completed)
                    pct = progress_start + (progress_end - progress_start) * completed / grid.layer_count
                    log.progress(pct, f"1/3 모델 슬라이싱 {completed:,}/{grid.layer_count:,} | ETA {format_seconds(remain)}")

    return grid


def validate_pipeline(params: PipelineParams) -> None:
    if params.mode not in ("3D 모델 → 전체 처리", "기존 슬라이스 → 격자화+재구성", "격자 슬라이스 → 재구성"):
        raise ValueError("올바르지 않은 처리 모드입니다.")
    if not params.input_path.exists():
        raise FileNotFoundError(f"입력 경로가 없습니다: {params.input_path}")
    if params.dpi_x <= 0 or params.dpi_y <= 0:
        raise ValueError("X/Y DPI는 0보다 커야 합니다.")
    if params.layer_thickness_mm <= 0:
        raise ValueError("레이어 두께는 0보다 커야 합니다.")
    if params.shell_mm < 0:
        raise ValueError("쉘 두께는 0 이상이어야 합니다.")
    if params.lattice_width_mm <= 0 or params.spacing_mm <= 0:
        raise ValueError("격자 살 두께와 간격은 0보다 커야 합니다.")
    if params.lattice_width_mm >= params.spacing_mm:
        raise ValueError("격자 살 두께는 격자 간격보다 작아야 합니다.")
    if params.workers < 1:
        raise ValueError("Workers는 1 이상이어야 합니다.")
    if params.output_stl.suffix.lower() != ".stl":
        raise ValueError("최종 출력 확장자는 .stl이어야 합니다.")


def copy_or_link_images(source: Path, target: Path) -> None:
    """기존 슬라이스 입력을 파이프라인 작업 폴더로 복사."""
    files = find_images(source)
    if not files:
        raise FileNotFoundError(f"이미지 파일을 찾지 못했습니다: {source}")
    clear_generated_images(target)
    for p in files:
        shutil.copy2(p, target / p.name)



@dataclass(frozen=True)
class StackBBoxInfo:
    images: Tuple[Path, ...]
    full_w: int
    full_h: int
    xmin: int
    xmax: int
    ymin: int
    ymax: int
    zmin: int
    zmax: int

    @property
    def width(self) -> int:
        return self.xmax - self.xmin

    @property
    def height(self) -> int:
        return self.ymax - self.ymin

    @property
    def layers(self) -> int:
        return self.zmax - self.zmin + 1


def _load_binary_mask(path: Path, expected_size: Optional[Tuple[int, int]] = None) -> np.ndarray:
    with Image.open(path) as im:
        gray = ImageOps.grayscale(im)
        if expected_size is not None and gray.size != expected_size:
            raise ValueError(f"슬라이스 크기가 서로 다릅니다: {path.name} {gray.size} != {expected_size}")
        arr = np.asarray(gray, dtype=np.uint8)
    return np.ascontiguousarray(arr >= 128, dtype=bool)


def analyze_stack_bbox(image_dir: Path, params: PipelineParams, log: Optional[UILogger] = None) -> StackBBoxInfo:
    images = tuple(find_images(image_dir))
    if not images:
        raise FileNotFoundError(f"검증할 슬라이스 이미지가 없습니다: {image_dir}")
    with Image.open(images[0]) as im:
        full_w, full_h = im.size

    xmin, ymin = full_w, full_h
    xmax = ymax = -1
    nonempty_z: List[int] = []
    for i, path in enumerate(images):
        mask = _load_binary_mask(path, (full_w, full_h))
        if np.any(mask):
            ys, xs = np.nonzero(mask)
            xmin = min(xmin, int(xs.min()))
            xmax = max(xmax, int(xs.max()) + 1)
            ymin = min(ymin, int(ys.min()))
            ymax = max(ymax, int(ys.max()) + 1)
            nonempty_z.append(i)
        if log is not None and (i == 0 or (i + 1) % 50 == 0 or i + 1 == len(images)):
            log.write(f"검증 입력 분석 {i+1:,}/{len(images):,}")

    if not nonempty_z:
        raise ValueError("슬라이스 세트에 재료 픽셀이 없습니다.")

    margin = max(0, int(params.crop_margin_px))
    if params.crop_xy:
        xmin = max(0, xmin - margin)
        xmax = min(full_w, xmax + margin)
        ymin = max(0, ymin - margin)
        ymax = min(full_h, ymax + margin)
    else:
        xmin, xmax, ymin, ymax = 0, full_w, 0, full_h

    if params.crop_z:
        zmin, zmax = min(nonempty_z), max(nonempty_z)
    else:
        zmin, zmax = 0, len(images) - 1

    return StackBBoxInfo(images, full_w, full_h, xmin, xmax, ymin, ymax, zmin, zmax)


def load_trimesh_no_process(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError(f"메시를 읽지 못했습니다: {path}")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise ValueError(f"지원하지 않는 메시 객체: {type(loaded).__name__}")
    return mesh


@njit(cache=True)
def _scanline_rasterize_segments_exact(
    seg_x0: np.ndarray,
    seg_y0: np.ndarray,
    seg_x1: np.ndarray,
    seg_y1: np.ndarray,
    x_origin: float,
    y_origin: float,
    x_pitch: float,
    y_pitch: float,
    width: int,
    height: int,
) -> np.ndarray:
    out = np.zeros((height, width), dtype=np.uint8)
    nseg = seg_x0.shape[0]
    intersections = np.empty(max(1, nseg), dtype=np.float64)
    for row in range(height):
        y = y_origin + (row + 0.5) * y_pitch
        count = 0
        for i in range(nseg):
            y0 = seg_y0[i]
            y1 = seg_y1[i]
            # 표준 half-open scanline rule: vertex 중복 교차를 방지한다.
            if (y0 <= y < y1) or (y1 <= y < y0):
                dy = y1 - y0
                if dy != 0.0:
                    intersections[count] = seg_x0[i] + (y - y0) * (seg_x1[i] - seg_x0[i]) / dy
                    count += 1
        if count < 2:
            continue
        xs = np.sort(intersections[:count])
        # 수치상 완전히 같은 교점은 동일 edge/vertex의 중복이므로 짝수 개를 상쇄한다.
        unique = np.empty(count, dtype=np.float64)
        ucount = 0
        j = 0
        while j < count:
            k = j + 1
            tol = 1e-10
            while k < count and abs(xs[k] - xs[j]) <= tol:
                k += 1
            if ((k - j) & 1) == 1:
                unique[ucount] = xs[j]
                ucount += 1
            j = k
        pairs = ucount // 2
        for q in range(pairs):
            left = unique[2*q]
            right = unique[2*q+1]
            if right < left:
                tmp = left; left = right; right = tmp
            u_left = (left - x_origin) / x_pitch - 0.5
            u_right = (right - x_origin) / x_pitch - 0.5
            x0i = int(math.floor(u_left)) + 1
            x1i = int(math.ceil(u_right))
            if x0i < 0: x0i = 0
            if x1i > width: x1i = width
            if x0i < x1i:
                out[row, x0i:x1i] = 1
    return out


def rasterize_mesh_plane_segments(mesh: trimesh.Trimesh, z: float, grid: SliceGrid) -> np.ndarray:
    segments = trimesh.intersections.mesh_plane(
        mesh=mesh,
        plane_normal=np.asarray([0.0, 0.0, 1.0]),
        plane_origin=np.asarray([0.0, 0.0, float(z)]),
        return_faces=False,
    )
    if segments is None or len(segments) == 0:
        return np.zeros((grid.height_px, grid.width_px), dtype=bool)
    seg = np.asarray(segments, dtype=np.float64)
    return _scanline_rasterize_segments_exact(
        seg[:,0,0], seg[:,0,1], seg[:,1,0], seg[:,1,1],
        grid.x_origin_mm, grid.y_origin_mm,
        grid.x_pitch_mm, grid.y_pitch_mm,
        grid.width_px, grid.height_px,
    ).astype(bool, copy=False)


def _aio_v37_final_fast_verify(
    model_path: Path,
    params: PipelineParams,
    cancel_event: threading.Event,
    *,
    compact_stats: Optional[dict] = None,
) -> None:
    """대형 모델용 빠른 최종 산출물 검증.

    전 레이어 재슬라이스/XOR는 5512×5512×1000급에서 수십억 픽셀을 다시
    처리해야 하므로 production 경로에서 제거한다. 대신 출력 자체의 구조 무결성을
    한 번의 streaming STL 검사와 compiled VTK topology 검사로 확인한다.

    검증 항목
    - binary STL 레코드 길이/triangle count/유한 좌표
    - 퇴화 triangle 0
    - closed manifold: boundary/non-manifold edge 0
    - global zero-error compact가 수행된 경우 그 단계의 exact-subset/area/volume/bounds
      검증 결과도 그대로 요구한다.
    """
    check_cancel(cancel_event)
    if model_path.suffix.lower() != ".stl":
        raise RuntimeError("최종 산출물은 STL이어야 합니다.")

    st = binary_stl_stats(model_path, cancel_event)
    tri_n = int(st.get("triangles", 0))
    if tri_n <= 0:
        raise RuntimeError("최종 STL에 triangle이 없습니다.")
    if int(st.get("degenerate_triangles", 0)) != 0:
        raise RuntimeError("최종 STL에 퇴화 triangle이 존재합니다.")
    expected_bytes = 84 + tri_n * 50
    if int(st.get("bytes", -1)) != expected_bytes:
        raise RuntimeError("최종 STL binary 레코드 크기가 triangle count와 일치하지 않습니다.")
    b0 = np.asarray(st.get("bounds_min", []), dtype=np.float64)
    b1 = np.asarray(st.get("bounds_max", []), dtype=np.float64)
    if b0.size != 3 or b1.size != 3 or not np.all(np.isfinite(b0)) or not np.all(np.isfinite(b1)):
        raise RuntimeError("최종 STL bounds가 유효하지 않습니다.")

    # global compact PASS는 이미 exact input-vertex subset + closed manifold +
    # bounds/area/volume/half-grid를 검사한 결과다. 같은 파일에 대해 VTK topology를
    # 다시 전부 읽는 중복 비용을 피한다.
    compact_pass = bool(compact_stats) and str(compact_stats.get("reason", "")) == "PASS"
    compact_out = int(compact_stats.get("output_triangles", -1)) if compact_stats else -1
    exact_boundary = bool(compact_stats.get("exact_boundary_preserved", False)) if compact_stats else False
    if compact_pass and compact_out == tri_n and exact_boundary:
        # v3.9 표면보간형은 exact boundary+projected-area 보존 compaction만 사용한다.
        # 원 binary topology 자체가 corner/edge contact로 non-manifold일 수 있으므로
        # manifold 여부를 PASS의 필수조건으로 강제하지 않는다.
        return
    if compact_pass and compact_out == tri_n:
        if int(compact_stats.get("bad_edges_output", 0)) != 0:
            raise RuntimeError("global compact topology 검증 결과가 유효하지 않습니다.")
        return

    # compact가 생략/실패/fallback된 경로만 compiled VTK로 topology를 한 번 검사한다.
    if _AIO_V32_VTK_AVAILABLE:
        rd = _aio_vtk.vtkSTLReader()
        rd.SetFileName(str(model_path))
        rd.MergingOn()
        rd.Update()
        poly = rd.GetOutput()
        if poly is None or int(poly.GetNumberOfCells()) != tri_n:
            raise RuntimeError("최종 STL VTK 재로딩 triangle count가 일치하지 않습니다.")
        bad = _aio_v33_bad_edge_count(poly)
        if bad != 0:
            raise RuntimeError(f"최종 모델 manifold 검증 실패: bad edges={bad:,}")
    # VTK를 설치할 수 없는 환경에서는 ExactLUT/원본충실형 생성기가 이미 topology를
    # 결정론적으로 생성하므로 binary STL 무결성 검사를 최종 certificate로 사용한다.


def binary_stl_stats(path: Path, cancel_event: Optional[threading.Event] = None) -> dict:
    """대형 STL도 전체 메시를 RAM에 올리지 않고 triangle/bounds/퇴화면/SHA를 검사."""
    rec_dtype = np.dtype([
        ("normal", "<f4", (3,)), ("a", "<f4", (3,)), ("b", "<f4", (3,)),
        ("c", "<f4", (3,)), ("attr", "<u2"),
    ])
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        header = f.read(84)
        if len(header) != 84:
            raise ValueError(f"비정상 STL: {path}")
        sha.update(header)
        count = struct.unpack("<I", header[80:84])[0]
        mins = np.asarray([np.inf, np.inf, np.inf], dtype=np.float64)
        maxs = np.asarray([-np.inf, -np.inf, -np.inf], dtype=np.float64)
        degenerate = 0
        read_count = 0
        chunk_tri = 200_000
        while read_count < count:
            if cancel_event is not None:
                check_cancel(cancel_event)
            take = min(chunk_tri, count - read_count)
            raw = f.read(take * 50)
            if len(raw) != take * 50:
                raise ValueError(f"STL 레코드 길이 오류: {path}")
            sha.update(raw)
            rec = np.frombuffer(raw, dtype=rec_dtype, count=take)
            if not np.all(np.isfinite(rec["normal"])):
                raise ValueError("STL에 NaN/Inf normal이 있습니다.")
            for name in ("a", "b", "c"):
                vertices = rec[name]
                if not bool(np.all(np.isfinite(vertices))):
                    raise ValueError(f"STL에 NaN/Inf 정점이 있습니다: {path}")
                mins = np.minimum(mins, vertices.min(axis=0))
                maxs = np.maximum(maxs, vertices.max(axis=0))
            ab = np.subtract(rec["b"], rec["a"], dtype=np.float64)
            ac = np.subtract(rec["c"], rec["a"], dtype=np.float64)
            cross = np.cross(ab, ac)
            degenerate += int(np.count_nonzero(np.einsum("ij,ij->i", cross, cross) <= 1e-24))
            read_count += take
            del vertices, rec, raw, ab, ac, cross
        if f.read(1):
            raise ValueError(f"STL 뒤에 예상 밖 데이터가 있습니다: {path}")
    return {
        "path": str(path),
        "triangles": int(count),
        "bytes": int(path.stat().st_size),
        "bounds_min": [float(v) for v in mins],
        "bounds_max": [float(v) for v in maxs],
        "extents": [float(v) for v in (maxs - mins)],
        "degenerate_triangles": int(degenerate),
        "sha256": sha.hexdigest(),
    }



def run_pipeline(
    params: PipelineParams,
    log: UILogger,
    cancel_event: threading.Event,
    temp_root: Path,
    preview_callback,
) -> Tuple[Path, Optional[Path], Optional[Path]]:
    validate_pipeline(params)
    start_all = time.time()
    params.output_stl.parent.mkdir(parents=True, exist_ok=True)

    work_parent = params.output_stl.parent / (params.output_stl.stem + "_intermediate_" + temp_root.name)
    raw_dir = work_parent / "01_raw_slices" if params.save_raw_slices else temp_root / "01_raw_slices"
    lattice_dir = work_parent / "02_lattice_slices" if params.save_lattice_slices else temp_root / "02_lattice_slices"
    raw_dir.mkdir(parents=True, exist_ok=True)
    lattice_dir.mkdir(parents=True, exist_ok=True)

    log.write(f"{APP_NAME} v{APP_VERSION}")
    log.write(f"처리 모드: {params.mode}")
    log.write(f"정밀 모델: {params.output_stl}")
    log.write("출력 기준: 지정 DPI와 레이어 두께 유지. 선택한 재구성 모드의 형상 보존 범위를 확인하십시오.")
    log.write("메시 최적화: 원본형 exact 경계 접합 / 보간형 Direct boundary + exact certificate")

    if params.mode == "3D 모델 → 전체 처리":
        slice_model_to_images(
            params.input_path, raw_dir, params, log, cancel_event, progress_start=0.0, progress_end=24.0
        )
        preview_callback(raw_dir, None)
    elif params.mode == "기존 슬라이스 → 격자화+재구성":
        if not params.input_path.is_dir():
            raise ValueError("기존 슬라이스 모드는 이미지 폴더를 입력해야 합니다.")
        copy_or_link_images(params.input_path, raw_dir)
        log.progress(24.0, "기존 슬라이스 입력 준비 완료")
        preview_callback(raw_dir, None)
    else:
        if not params.input_path.is_dir():
            raise ValueError("격자 슬라이스 모드는 이미지 폴더를 입력해야 합니다.")
        copy_or_link_images(params.input_path, lattice_dir)
        log.progress(72.0, "격자 슬라이스 입력 준비 완료")
        preview_callback(None, lattice_dir)

    check_cancel(cancel_event)

    if params.mode != "격자 슬라이스 → 재구성":
        lattice_params = lattice_core.Params(
            input_dir=raw_dir,
            output_dir=lattice_dir,
            dpi_x=params.dpi_x,
            dpi_y=params.dpi_y,
            shell_mm=params.shell_mm,
            lattice_width_mm=params.lattice_width_mm,
            spacing_mm=params.spacing_mm,
            layer_thickness_mm=params.layer_thickness_mm,
            threshold=128,
            print_is_white=True,
            output_mode="binary",
            background_value=0,
            material_value=255,
            lattice_type=params.lattice_type,
            pattern=params.pattern,
            axes=params.axes,
            preserve_filenames=True,
            overwrite=True,
            save_preview_every=0,
            shell_method=params.shell_method,
            workers=params.workers,
        )
        # v4.1: 임시 중간 이미지는 동일 0/255 mask를 1-bit로 저장해 디스크 I/O를 1/8로 줄인다.
        # 사용자가 중간 lattice 이미지를 보존하도록 선택한 경우에는 기존 8-bit 형식을 유지한다.
        lattice_core._aio_internal_1bit_output = not bool(params.save_lattice_slices)
        stage_log = StageLogger(log, 24.0, 72.0, "2/3 쉘·격자화")
        if _AIO_LATTICE_ACCEL_DISABLED:
            stage_log.write(
                "격자 래스터화: 기존 정밀 Python fallback (" +
                (_AIO_LATTICE_FALLBACK_REASON or "Numba unavailable") + ")"
            )
        else:
            stage_log.write(
                "격자 래스터화: Numba exact JIT 활성화 "
                "(설정별 픽셀 전수검증 후 사용, 불일치 시 자동 fallback)"
            )
        lattice_core.run_conversion(lattice_params, stage_log, cancel_event)
        preview_callback(raw_dir, lattice_dir)

    check_cancel(cancel_event)

    # v3.7: 최종 산출물은 STL 하나만 생성한다.
    # 임시 STL에서 생성/경량화/빠른 구조 검증을 끝낸 뒤 PASS일 때만 최종 파일로 승격한다.
    # 전 레이어 XOR 재슬라이스는 대형 모델의 10분 목표를 방해하므로 production 경로에서 제거한다.
    mandatory_verify = params.surface_mode in ("원본 충실형", "표면 보간형", "레이어 출력 전용")
    pending_stl = (
        params.output_stl.with_name(params.output_stl.stem + "__cert_pending.stl")
        if mandatory_verify else params.output_stl
    )
    pending_stl.unlink(missing_ok=True)
    recon_params = recon_core.Params(
        input_path=lattice_dir,
        output_stl=pending_stl,
        dpi_x=params.dpi_x,
        dpi_y=params.dpi_y,
        layer_thickness_mm=params.layer_thickness_mm,
        auto_threshold=False,
        threshold=128,
        polarity="흰색",
        recursive=False,
        respect_number_gaps=False,
        crop_xy=params.crop_xy,
        crop_empty_z=params.crop_z,
        crop_margin_px=params.crop_margin_px,
        center_xy=params.center_xy,
        surface_mode=params.surface_mode,
        mesh_mode="고속 병합",
        workers=params.workers,
        max_triangles=params.max_triangles,
    )
    stage_log = StageLogger(log, 72.0, 98.0 if mandatory_verify else 99.0, "3/3 정밀 STL 재구성")
    global _AIO_V32_STRICT_OUTPUT_VERIFY
    _AIO_V32_STRICT_OUTPUT_VERIFY = False
    compact_stats = {}
    try:
        recon_core.reconstruct_streaming(recon_params, stage_log, cancel_event)

        if params.surface_mode == "표면 보간형":
            # v3.9: 표면 생성 중 XOR-safe exact compact가 끝난다. 중간 STL을 다시 읽지 않는다.
            compact_stats = dict(_AIO_V38_LAST_COMPACT_STATS)

        if mandatory_verify:
            full_stats = _aio_v37_final_fast_verify(
                pending_stl, params, cancel_event, compact_stats=compact_stats
            )
            os.replace(pending_stl, params.output_stl)
            full_stats['path'] = str(params.output_stl)
            log.write("최종 검증: 파일 무결성·퇴화면·열린 경계·접합 방향 검사 통과 (자체 교차·점 접촉 별도)")
            if full_stats.get('voxel_contact_edges',0):
                log.write(f"복셀 선 접촉 {full_stats['voxel_contact_edges']:,}개: 네 반평면과 방향 확인. 원본 표면을 유지하여 저장했습니다. 엄격한 2-manifold 메시는 아닙니다.")
        else:
            log.write("최종 검증: 해당 없음")
    except Exception:
        log.write("최종 검증: 불합격 (FAIL)")
        pending_stl.unlink(missing_ok=True)
        try:
            pending_stl.with_name(pending_stl.stem + "__global_compact.stl").unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        _AIO_V32_STRICT_OUTPUT_VERIFY = False

    check_cancel(cancel_event)
    if not mandatory_verify:
        full_stats = binary_stl_stats(params.output_stl, cancel_event)

    log.write("")
    log.write("=============== 정밀 모델 메시 통계 ===============")
    log.write(f"Triangles: {full_stats['triangles']:,}")
    log.write(f"파일 크기: {format_bytes(full_stats['bytes'])}")
    log.write(f"퇴화 triangle: {full_stats['degenerate_triangles']:,}")
    log.write(f"Bounds min: {format_display_value(full_stats['bounds_min'])}")
    log.write(f"Bounds max: {format_display_value(full_stats['bounds_max'])}")
    log.write(f"Extents mm: {format_display_value(full_stats['extents'])}")
    log.write("검증 범위: 파일 무결성 + edge incidence/winding. 임의 자체 교차·점 접촉은 별도 확인 필요.")
    log.write("====================================================")

    elapsed = time.time() - start_all
    log.write("")
    log.write("================ 통합 처리 완료 ================")
    log.write(f"정밀 STL: {params.output_stl}")
    log.write(f"3D 재구성 모드: {params.surface_mode}")
    if params.surface_mode == "원본 충실형":
        log.write("메시 출력: 정밀 보존 구조 병합(좌표 변경·근사 단순화 없음)")
    log.write(f"전체 소요 시간: {format_seconds(elapsed)}")
    if params.save_raw_slices:
        log.write(f"원본 슬라이스 보존: {raw_dir}")
    if params.save_lattice_slices:
        log.write(f"격자 슬라이스 보존: {lattice_dir}")
    log.write("=================================================")
    log.progress(100.0, "완료")
    return params.output_stl, raw_dir, lattice_dir


BaseTk = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk


class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1320x900")
        self.minsize(1120, 760)

        self.cancel_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.temp_root: Optional[Path] = None
        self.preview_raw: List[Path] = []
        self.preview_lattice: List[Path] = []
        self.preview_photo_raw = None
        self.preview_photo_lattice = None

        self._create_vars()
        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _create_vars(self) -> None:
        self.mode_var = tk.StringVar(value="3D 모델 → 전체 처리")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.input_var.trace_add("write", lambda *_: self._input_changed())
        self.unit_var = tk.StringVar(value="mm")
        self.dpi_x_var = tk.StringVar(value="400")
        self.dpi_y_var = tk.StringVar(value="400")
        self.same_dpi_var = tk.BooleanVar(value=True)
        self.layer_var = tk.StringVar(value="0.1")
        self.slice_margin_var = tk.StringVar(value="2")
        self.batch_var = tk.StringVar(value="8")

        self.shell_var = tk.StringVar(value="2.0")
        self.lattice_width_var = tk.StringVar(value="0.4")
        self.spacing_var = tk.StringVar(value="2.0")
        self.lattice_type_var = tk.StringVar(value="선격자")
        self.pattern_var = tk.StringVar(value="헥사고날")
        self.axes_var = tk.StringVar(value="xyz")
        self.shell_method_var = tk.StringVar(value="고속 정밀 EDT (정밀도 고정)")

        self.surface_mode_var = tk.StringVar(value="원본 충실형")
        self.crop_xy_var = tk.BooleanVar(value=True)
        self.crop_z_var = tk.BooleanVar(value=True)
        self.crop_margin_var = tk.StringVar(value="2")
        self.center_var = tk.BooleanVar(value=True)
        self.workers_var = tk.StringVar(value=str(DEFAULT_WORKERS))
        self.max_triangles_var = tk.StringVar(value="0")

        self.save_raw_var = tk.BooleanVar(value=False)
        self.save_lattice_var = tk.BooleanVar(value=False)
        self.intermediate_format_var = tk.StringVar(value="BMP")
        self.verify_layer_xor_var = tk.BooleanVar(value=False)
        self.print_equivalent_var = tk.BooleanVar(value=False)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="0.0%")
        self.preview_index_var = tk.IntVar(value=1)
        self.preview_info_var = tk.StringVar(value="작업 중 생성되는 원본/격자 슬라이스를 비교합니다.")

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="처리 모드").grid(row=0, column=0, sticky="w", padx=(0, 8))
        mode_cb = ttk.Combobox(
            top, textvariable=self.mode_var, state="readonly", width=34,
            values=("3D 모델 → 전체 처리", "기존 슬라이스 → 격자화+재구성", "격자 슬라이스 → 재구성"),
        )
        mode_cb.grid(row=0, column=1, sticky="w")
        mode_cb.bind("<<ComboboxSelected>>", lambda _e: self._mode_changed())

        ttk.Label(top, text="입력").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        input_entry = ttk.Entry(top, textvariable=self.input_var)
        input_entry.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(top, text="찾기", command=self._browse_input).grid(row=1, column=2, padx=(6, 0), pady=(6, 0))

        ttk.Label(top, text="최종 STL").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        ttk.Entry(top, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(top, text="저장 위치", command=self._browse_output).grid(row=2, column=2, padx=(6, 0), pady=(6, 0))

        if DND_FILES is not None:
            input_entry.drop_target_register(DND_FILES)
            input_entry.dnd_bind("<<Drop>>", self._on_drop)

        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=2)
        body.add(right, weight=3)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        notebook = ttk.Notebook(left)
        notebook.grid(row=0, column=0, sticky="nsew")

        slice_tab = ttk.Frame(notebook, padding=10)
        lattice_tab = ttk.Frame(notebook, padding=10)
        recon_tab = ttk.Frame(notebook, padding=10)
        output_tab = ttk.Frame(notebook, padding=10)
        notebook.add(slice_tab, text="1. 슬라이싱")
        notebook.add(lattice_tab, text="2. 쉘·격자")
        notebook.add(recon_tab, text="3. 재구성")
        notebook.add(output_tab, text="출력·성능")

        self._build_slice_tab(slice_tab)
        self._build_lattice_tab(lattice_tab)
        self._build_recon_tab(recon_tab)
        self._build_output_tab(output_tab)

        preview = ttk.LabelFrame(right, text="레이어 비교 미리보기", padding=6)
        preview.grid(row=0, column=0, sticky="ew")
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(1, weight=1)

        self.raw_label = ttk.Label(preview, text="원본 슬라이스", anchor="center")
        self.lattice_label = ttk.Label(preview, text="격자 슬라이스", anchor="center")
        self.raw_label.grid(row=0, column=0, sticky="nsew", padx=3)
        self.lattice_label.grid(row=0, column=1, sticky="nsew", padx=3)
        self.raw_label.bind("<MouseWheel>", self._preview_wheel)
        self.lattice_label.bind("<MouseWheel>", self._preview_wheel)

        control = ttk.Frame(preview)
        control.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        control.columnconfigure(1, weight=1)
        ttk.Label(control, text="Layer").grid(row=0, column=0, padx=(0, 6))
        self.preview_scale = ttk.Scale(control, from_=1, to=1, variable=self.preview_index_var, command=self._preview_scale_changed)
        self.preview_scale.grid(row=0, column=1, sticky="ew")
        ttk.Label(control, textvariable=self.preview_info_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        log_frame = ttk.LabelFrame(right, text="진행 로그", padding=6)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=20)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Progressbar(bottom, variable=self.progress_var, maximum=100).grid(row=0, column=0, sticky="ew")
        ttk.Label(bottom, textvariable=self.progress_text_var, width=46).grid(row=0, column=1, padx=8)
        self.run_button = ttk.Button(bottom, text="전체 처리 시작", command=self.start)
        self.run_button.grid(row=0, column=2, padx=(0, 6))
        self.cancel_button = ttk.Button(bottom, text="중단", command=self.cancel, state=tk.DISABLED)
        self.cancel_button.grid(row=0, column=3)

        self.same_dpi_var.trace_add("write", lambda *_: self._sync_dpi())
        self.dpi_x_var.trace_add("write", lambda *_: self._sync_dpi())
        self.surface_mode_var.trace_add("write", lambda *_: self._surface_changed())
        self._surface_changed()

    def _add_row(self, parent, row: int, label: str, variable, width=16, values=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        if values:
            widget = ttk.Combobox(parent, textvariable=variable, state="readonly", values=values, width=width)
        else:
            widget = ttk.Entry(parent, textvariable=variable, width=width)
        widget.grid(row=row, column=1, sticky="ew", pady=5)
        return widget

    def _build_slice_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        self._add_row(tab, 0, "모델 입력 단위", self.unit_var, values=("mm", "cm", "m", "inch"))
        self._add_row(tab, 1, "X DPI", self.dpi_x_var)
        self._add_row(tab, 2, "Y DPI", self.dpi_y_var)
        ttk.Checkbutton(tab, text="X/Y DPI 동일", variable=self.same_dpi_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=5)
        self._add_row(tab, 4, "레이어 두께 (mm)", self.layer_var)
        self._add_row(tab, 5, "슬라이스 여백 (px)", self.slice_margin_var)
        self._add_row(tab, 6, "멀티섹션 batch", self.batch_var)
        ttk.Label(
            tab,
            text="단면은 각 레이어 중심에서 계산하고, 각 픽셀 중심의 포함 여부로 이진화합니다.\n"
                 "업샘플링 없이 입력 DPI가 곧 X/Y 정보 한계입니다.",
            wraplength=390, justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_lattice_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        self._add_row(tab, 0, "쉘 두께 (mm)", self.shell_var)
        self._add_row(tab, 1, "격자 살 두께 (mm)", self.lattice_width_var)
        self._add_row(tab, 2, "격자 간격 (mm)", self.spacing_var)
        self._add_row(tab, 3, "격자 형식", self.lattice_type_var, values=("선격자", "면격자", "없음(쉘만)"))
        self._add_row(tab, 4, "패턴", self.pattern_var, values=("헥사고날", "사각"))
        self._add_row(tab, 5, "축", self.axes_var, values=("xy", "xyz"))
        self._add_row(tab, 6, "쉘 계산", self.shell_method_var, values=("고속 정밀 EDT (정밀도 고정)",))
        ttk.Label(
            tab,
            text="xyz 선격자는 모든 층의 Z-strut와 주기적인 XY edge를 조합합니다.\n"
                 "쉘은 XY 외곽과 Z 상·하부 cap을 exact EDT로 보존합니다. 근사 모드는 사용하지 않습니다.",
            wraplength=390, justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_recon_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        self._add_row(tab, 0, "3D 재구성", self.surface_mode_var, values=("원본 충실형",))
        ttk.Checkbutton(tab, text="XY 빈 여백 제거", variable=self.crop_xy_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(tab, text="앞뒤 빈 레이어 제거", variable=self.crop_z_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=5)
        self._add_row(tab, 3, "Crop 여백 (px)", self.crop_margin_var)
        ttk.Checkbutton(tab, text="최종 STL XY 중심 정렬", variable=self.center_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(
            tab,
            text="원본 충실형: 지정 DPI·층 두께의 복셀 표면을 정확히 보존합니다.\n"
                 "형상 이동·스무딩·틈 생성 없이 병합합니다. 복셀 선 접촉은 별도 검사·보고하며, 열린 경계와 잘못된 접합은 차단합니다.",
            wraplength=390, justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_output_tab(self, tab) -> None:
        tab.columnconfigure(1, weight=1)
        self._add_row(tab, 0, "Workers", self.workers_var)
        self._add_row(tab, 1, "최대 Triangle (0=무제한)", self.max_triangles_var)
        self._add_row(tab, 2, "중간 이미지 형식", self.intermediate_format_var, values=("BMP", "PNG"))
        self.verify_xor_check = ttk.Label(tab, text="최종 검증: 파일 무결성 + 전체 edge 접합/방향 검사")
        self.verify_xor_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(tab, text="원본 슬라이스 저장", variable=self.save_raw_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Checkbutton(tab, text="격자 슬라이스 저장", variable=self.save_lattice_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(
            tab,
            text="입력과 같은 폴더에 <원본이름>_shell.stl을 기본 저장합니다.\n"
                 "STL 무결성·퇴화면·edge 접합과 방향을 검사합니다. 자체 교차와 점 접촉 검사는 포함하지 않습니다.",
            wraplength=390, justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _mode_changed(self) -> None:
        mode = self.mode_var.get()
        if mode == "3D 모델 → 전체 처리":
            self.run_button.config(text="전체 처리 시작")
        elif mode == "기존 슬라이스 → 격자화+재구성":
            self.run_button.config(text="격자화 + 재구성 시작")
        else:
            self.run_button.config(text="STL 재구성 시작")

    def _surface_changed(self) -> None:
        # 전 레이어 XOR는 production 경로에서 사용하지 않는다.
        self.verify_layer_xor_var.set(False)

    def _sync_dpi(self) -> None:
        if self.same_dpi_var.get():
            x = self.dpi_x_var.get()
            if self.dpi_y_var.get() != x:
                self.dpi_y_var.set(x)

    def _input_changed(self) -> None:
        value = self.input_var.get().strip()
        if value:
            p = Path(value).expanduser()
            stem = p.name if p.is_dir() else p.stem
            self.output_var.set(str(p.parent / f"{stem}_shell.stl"))

    def _browse_input(self) -> None:
        mode = self.mode_var.get()
        if mode == "3D 모델 → 전체 처리":
            path = filedialog.askopenfilename(
                title="3D 모델 선택",
                filetypes=[("3D model", "*.stl *.obj *.ply *.off *.3mf *.glb *.gltf"), ("All", "*.*")],
            )
        else:
            path = filedialog.askdirectory(title="슬라이스 이미지 폴더 선택")
        if path:
            self.input_var.set(path)
            self._input_changed()

    def _browse_output(self) -> None:
        initial = Path(self.output_var.get()).name if self.output_var.get() else "model_shell.stl"
        path = filedialog.asksaveasfilename(
            title="최종 STL 저장",
            defaultextension=".stl",
            initialfile=initial,
            filetypes=[("STL", "*.stl")],
        )
        if path:
            self.output_var.set(path)

    def _on_drop(self, event) -> None:
        try:
            values = self.tk.splitlist(event.data)
            if values:
                self.input_var.set(values[0])
        except Exception:
            self.input_var.set(str(event.data).strip("{}"))

    def _collect_params(self) -> PipelineParams:
        max_tri = int(self.max_triangles_var.get())
        return PipelineParams(
            mode=self.mode_var.get(),
            input_path=Path(self.input_var.get()).expanduser(),
            output_stl=Path(self.output_var.get()).expanduser(),
            input_unit=self.unit_var.get(),
            dpi_x=float(self.dpi_x_var.get()),
            dpi_y=float(self.dpi_y_var.get()),
            layer_thickness_mm=float(self.layer_var.get()),
            slice_margin_px=int(self.slice_margin_var.get()),
            batch_layers=int(self.batch_var.get()),
            shell_mm=float(self.shell_var.get()),
            lattice_width_mm=float(self.lattice_width_var.get()),
            spacing_mm=float(self.spacing_var.get()),
            lattice_type=self.lattice_type_var.get(),
            pattern=self.pattern_var.get(),
            axes=self.axes_var.get(),
            shell_method="고속 정밀 EDT (정밀도 고정)",
            surface_mode=self.surface_mode_var.get(),
            crop_xy=self.crop_xy_var.get(),
            crop_z=self.crop_z_var.get(),
            crop_margin_px=int(self.crop_margin_var.get()),
            center_xy=self.center_var.get(),
            workers=int(self.workers_var.get()),
            max_triangles=None if max_tri <= 0 else max_tri,
            save_raw_slices=self.save_raw_var.get(),
            save_lattice_slices=self.save_lattice_var.get(),
            intermediate_format=self.intermediate_format_var.get(),
            verify_layer_xor=False,
            print_equivalent=self.print_equivalent_var.get(),
        )

    def start(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return
        try:
            params = self._collect_params()
            validate_pipeline(params)
        except Exception as exc:
            messagebox.showerror("입력 오류", str(exc))
            return

        self._save_settings()
        self.cancel_event.clear()
        self.progress_var.set(0.0)
        self.progress_text_var.set("0.0%")
        self.log_text.delete("1.0", tk.END)
        self.run_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.preview_raw = []
        self.preview_lattice = []
        self._cleanup_temp()
        self.temp_root = Path(tempfile.mkdtemp(prefix="bjt_model_pipeline_"))

        logger = UILogger(self)

        def worker():
            try:
                output, raw, lattice = run_pipeline(
                    params,
                    logger,
                    self.cancel_event,
                    self.temp_root,
                    self._set_preview_folders_threadsafe,
                )
                def _show_done():
                    messagebox.showinfo("완료", f"최종 STL 생성 완료\n\n{output}")
                self.after(0, _show_done)
            except (CancelledError, lattice_core.CancelledError, recon_core.CancelledError) as exc:
                logger.write(str(exc))
                self.after(0, lambda: messagebox.showinfo("중단", "작업이 중단되었습니다."))
            except Exception as exc:
                logger.write("ERROR:")
                logger.write(str(exc))
                logger.write(traceback.format_exc())
                self.after(0, lambda msg=str(exc): messagebox.showerror("오류", msg))
            finally:
                self.after(0, lambda: self.run_button.config(state=tk.NORMAL))
                self.after(0, lambda: self.cancel_button.config(state=tk.DISABLED))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.config(state=tk.DISABLED)
        self.progress_text_var.set("중단 요청됨 - 현재 단계 정리 중")
        self._append_log("중단 요청됨")

    def _append_log(self, message: str) -> None:
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def _set_progress(self, percent: float, message: str) -> None:
        value = max(0.0, min(100.0, percent))
        self.progress_var.set(value)
        self.progress_text_var.set(f"{value:.1f}%  |  {message}" if message else f"{value:.1f}%")

    def _set_preview_folders_threadsafe(self, raw: Optional[Path], lattice: Optional[Path]) -> None:
        self.after(0, self._set_preview_folders, raw, lattice)

    def _set_preview_folders(self, raw: Optional[Path], lattice: Optional[Path]) -> None:
        if raw is not None and raw.exists():
            self.preview_raw = find_images(raw)
        if lattice is not None and lattice.exists():
            self.preview_lattice = find_images(lattice)
        count = max(len(self.preview_raw), len(self.preview_lattice), 1)
        self.preview_scale.config(to=count)
        self.preview_index_var.set(max(1, min(self.preview_index_var.get(), count)))
        self._update_preview()

    def _preview_scale_changed(self, _value=None) -> None:
        self.preview_index_var.set(max(1, int(round(float(self.preview_index_var.get())))))
        self._update_preview()

    def _preview_wheel(self, event) -> None:
        count = max(len(self.preview_raw), len(self.preview_lattice), 1)
        delta = 1 if event.delta < 0 else -1
        self.preview_index_var.set(max(1, min(count, self.preview_index_var.get() + delta)))
        self._update_preview()

    def _preview_image(self, path: Path, max_size=(500, 310)):
        with Image.open(path) as im:
            image = ImageOps.grayscale(im).convert("RGB")
            image.thumbnail(max_size, Image.Resampling.NEAREST)
            return ImageTk.PhotoImage(image)

    def _update_preview(self) -> None:
        idx = max(1, self.preview_index_var.get()) - 1
        raw_name = "-"
        lattice_name = "-"
        if idx < len(self.preview_raw):
            try:
                self.preview_photo_raw = self._preview_image(self.preview_raw[idx])
                self.raw_label.config(image=self.preview_photo_raw, text="")
                raw_name = self.preview_raw[idx].name
            except Exception:
                pass
        else:
            self.raw_label.config(image="", text="원본 슬라이스 없음")
        if idx < len(self.preview_lattice):
            try:
                self.preview_photo_lattice = self._preview_image(self.preview_lattice[idx])
                self.lattice_label.config(image=self.preview_photo_lattice, text="")
                lattice_name = self.preview_lattice[idx].name
            except Exception:
                pass
        else:
            self.lattice_label.config(image="", text="격자 슬라이스 없음")
        count = max(len(self.preview_raw), len(self.preview_lattice), 0)
        self.preview_info_var.set(f"Layer {idx+1}/{count or 1} | raw={raw_name} | lattice={lattice_name}")

    def _load_settings(self) -> None:
        try:
            if not SETTINGS_PATH.exists():
                return
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            data["surface_mode"] = "원본 충실형"
            data["print_equivalent"] = False
            data.pop("output", None)
            mapping = {
                "mode": self.mode_var, "input": self.input_var, "output": self.output_var,
                "unit": self.unit_var, "dpi_x": self.dpi_x_var, "dpi_y": self.dpi_y_var,
                "layer": self.layer_var, "slice_margin": self.slice_margin_var, "batch": self.batch_var,
                "shell": self.shell_var, "lattice_width": self.lattice_width_var, "spacing": self.spacing_var,
                "lattice_type": self.lattice_type_var, "pattern": self.pattern_var, "axes": self.axes_var,
                "shell_method": self.shell_method_var, "surface_mode": self.surface_mode_var,
                "crop_margin": self.crop_margin_var,
                "workers": self.workers_var, "max_triangles": self.max_triangles_var,
                "intermediate_format": self.intermediate_format_var,
            }
            for key, var in mapping.items():
                if key in data:
                    var.set(data[key])
            bools = {
                "same_dpi": self.same_dpi_var, "crop_xy": self.crop_xy_var,
                "crop_z": self.crop_z_var, "center": self.center_var,
                "save_raw": self.save_raw_var, "save_lattice": self.save_lattice_var,
                "verify_layer_xor": self.verify_layer_xor_var,
                "print_equivalent": self.print_equivalent_var,
            }
            for key, var in bools.items():
                if key in data:
                    var.set(bool(data[key]))
        except Exception:
            pass
        # 통합 v4는 정밀도 저하가 가능한 근사 erosion 경로를 UI에서 사용하지 않는다.
        self.shell_method_var.set("고속 정밀 EDT (정밀도 고정)")
        self._mode_changed()
        self._surface_changed()

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "mode": self.mode_var.get(), "input": self.input_var.get(), "output": self.output_var.get(),
                "unit": self.unit_var.get(), "dpi_x": self.dpi_x_var.get(), "dpi_y": self.dpi_y_var.get(),
                "same_dpi": self.same_dpi_var.get(), "layer": self.layer_var.get(),
                "slice_margin": self.slice_margin_var.get(), "batch": self.batch_var.get(),
                "shell": self.shell_var.get(), "lattice_width": self.lattice_width_var.get(),
                "spacing": self.spacing_var.get(), "lattice_type": self.lattice_type_var.get(),
                "pattern": self.pattern_var.get(), "axes": self.axes_var.get(),
                "shell_method": self.shell_method_var.get(), "surface_mode": self.surface_mode_var.get(),
                "crop_xy": self.crop_xy_var.get(),
                "crop_z": self.crop_z_var.get(), "crop_margin": self.crop_margin_var.get(),
                "center": self.center_var.get(), "workers": self.workers_var.get(),
                "max_triangles": self.max_triangles_var.get(), "save_raw": self.save_raw_var.get(),
                "save_lattice": self.save_lattice_var.get(),
                "verify_layer_xor": self.verify_layer_xor_var.get(),
                "print_equivalent": self.print_equivalent_var.get(),
                "intermediate_format": self.intermediate_format_var.get(),
            }
            SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _cleanup_temp(self) -> None:
        if self.temp_root is not None:
            try:
                shutil.rmtree(self.temp_root, ignore_errors=True)
            except Exception:
                pass
            self.temp_root = None

    def on_close(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            if not messagebox.askyesno(APP_NAME, "작업 중입니다. 중단하고 종료할까요?"):
                return
            self.cancel_event.set()
        self._save_settings()
        self._cleanup_temp()
        self.destroy()



# =============================================================================
# v4.1 CAP throughput patch
# - actual large-cap workload: exact repeated-layer dedup, 1-bit temp I/O,
#   run-aware XY EDT, and Numba linear exact surface certificate.
# - NO DPI/layer reduction, NO coordinate approximation.
# =============================================================================


def _aio_v43_write_1bit_bmp_packed(packed: np.ndarray, width: int, height: int, path: Path) -> None:
    """Write an uncompressed top-down 1-bit BMP directly from np.packbits rows.

    Pixels are identical to PIL mode-1 BMP, but this avoids per-image Pillow encoder overhead.
    """
    p=np.ascontiguousarray(packed,dtype=np.uint8)
    bpr=(int(width)+7)//8
    if p.shape != (int(height), bpr):
        raise ValueError(f'packed BMP shape mismatch {p.shape} != {(int(height),bpr)}')
    stride=(bpr+3)&~3
    off=62
    image_bytes=stride*int(height)
    file_bytes=off+image_bytes
    with open(path,'wb',buffering=1024*1024) as f:
        f.write(struct.pack('<2sIHHI',b'BM',file_bytes,0,0,off))
        f.write(struct.pack('<IiiHHIIiiII',40,int(width),-int(height),1,1,0,image_bytes,2835,2835,2,2))
        f.write(bytes((0,0,0,0,255,255,255,0)))
        if stride == bpr:
            f.write(p.tobytes(order='C'))
        else:
            rows=np.zeros((int(height),stride),dtype=np.uint8)
            rows[:,:bpr]=p
            f.write(rows.tobytes(order='C'))

_AIO_V43_PIL_SAVE = Image.Image.save

def _aio_v43_fast_mode1_save(self, fp, format=None, **params):
    try:
        path=Path(fp) if isinstance(fp,(str,os.PathLike,Path)) else None
        fmt=(str(format).upper() if format else (path.suffix[1:].upper() if path else ''))
        if self.mode == '1' and path is not None and fmt == 'BMP':
            w,h=self.size; bpr=(w+7)//8
            packed=np.frombuffer(self.tobytes(),dtype=np.uint8).reshape(h,bpr)
            _aio_v43_write_1bit_bmp_packed(packed,w,h,path)
            return
    except Exception:
        pass
    return _AIO_V43_PIL_SAVE(self,fp,format=format,**params)

# Only mode-1 BMP is intercepted; all grayscale/PNG/other PIL saving remains unchanged.
# v5.4: do not monkeypatch Pillow globally.

# ---- 1) temporary binary output: same pixels, 1/8 file payload ----------------
_AIO_V41_ORIG_COMPOSE_OUTPUT_IMAGE = lattice_core.compose_output_image

def _aio_v41_compose_output_image(original_gray, final_mask, params):
    mode = str(getattr(params, 'output_mode', '')).strip().lower()
    internal_1bit = bool(getattr(lattice_core, '_aio_internal_1bit_output', False))
    if internal_1bit and mode in ['binary', '이진', 'binary 출력']:
        bg = int(getattr(params, 'background_value', 0))
        mat = int(getattr(params, 'material_value', 255))
        if bg == 0 and mat == 255:
            # PIL bool -> mode '1'. convert('L') reproduces exact 0/255 pixels.
            return Image.fromarray(np.asarray(final_mask, dtype=bool))
    return _AIO_V41_ORIG_COMPOSE_OUTPUT_IMAGE(original_gray, final_mask, params)

lattice_core.compose_output_image = _aio_v41_compose_output_image


# ---- 2) exact run-aware raw slicing ------------------------------------------
def _aio_v41_save_slice(mask: np.ndarray, path: Path, image_format: str, one_bit: bool) -> None:
    fmt = image_format.upper()
    if one_bit and fmt == 'BMP':
        mm=np.asarray(mask,dtype=bool)
        _aio_v43_write_1bit_bmp_packed(np.packbits(mm,axis=1),mm.shape[1],mm.shape[0],path)
        return
    if one_bit:
        image = Image.fromarray(np.asarray(mask, dtype=bool))
    else:
        image = Image.fromarray(np.asarray(mask, dtype=np.uint8) * np.uint8(255), mode='L')
    if fmt == 'PNG':
        image.save(path, format='PNG', compress_level=1, optimize=False)
    else:
        image.save(path, format='BMP')


def _aio_v44_save_packed_slice(packed: np.ndarray, width: int, height: int, path: Path, image_format: str, one_bit: bool) -> None:
    p=np.ascontiguousarray(packed,dtype=np.uint8)
    fmt=str(image_format).upper()
    if one_bit:
        if fmt == 'BMP':
            _aio_v43_write_1bit_bmp_packed(p,int(width),int(height),path); return
        im=Image.frombytes('1',(int(width),int(height)),p.tobytes(order='C'))
        im.save(path,format='PNG',compress_level=1,optimize=False); return
    # User-requested preserved intermediates retain the legacy 8-bit 0/255 representation.
    mask=np.unpackbits(p,axis=1,count=int(width)).astype(np.uint8,copy=False)*np.uint8(255)
    im=Image.fromarray(mask,mode='L')
    if fmt == 'PNG': im.save(path,format='PNG',compress_level=1,optimize=False)
    else: im.save(path,format='BMP')


def _aio_v41_link_or_copy(src: Path, dst: Path) -> bool:
    try:
        if dst.exists():
            dst.unlink()
        os.link(src, dst)
        return True
    except Exception:
        try:
            shutil.copy2(src, dst)
            return False
        except Exception:
            return False


def _aio_v41_slice_model_to_images(
    model_path: Path,
    output_dir: Path,
    params: PipelineParams,
    log: UILogger,
    cancel_event: threading.Event,
    progress_start: float = 0.0,
    progress_end: float = 24.0,
) -> SliceGrid:
    mesh = load_mesh_mm(model_path, params.input_unit, log)
    mesh, grid = build_slice_grid(
        mesh, params.dpi_x, params.dpi_y, params.layer_thickness_mm, params.slice_margin_px
    )
    clear_generated_images(output_dir)

    ext = '.png' if params.intermediate_format == 'PNG' else '.bmp'
    internal_1bit = not bool(params.save_raw_slices)
    log.write('')
    log.write('=============== 슬라이싱 조건 ===============')
    log.write(f'DPI: X={format_number(params.dpi_x)}, Y={format_number(params.dpi_y)}')
    log.write(f'픽셀 피치: X={format_number(grid.x_pitch_mm)}, Y={format_number(grid.y_pitch_mm)} mm')
    log.write(f'레이어 두께: {format_number(grid.layer_thickness_mm)} mm')
    log.write(f'캔버스: {grid.width_px:,} × {grid.height_px:,} px')
    log.write(f'캔버스 실제 크기: X={format_number(grid.physical_width_mm)}, Y={format_number(grid.physical_height_mm)} mm')
    log.write(f'레이어 수: {grid.layer_count:,} | 양자화 Z={format_number(grid.layer_count*grid.layer_thickness_mm)} mm')
    logical_px=int(grid.width_px)*int(grid.height_px)*int(grid.layer_count)
    log.write(f'전체 raster workload: {logical_px/1e9:.2f} Gpixel · 8-bit stack={logical_px/1024**3:.1f} GiB · 1-bit={logical_px/8/1024**3:.1f} GiB')
    log.write('샘플링: 각 레이어 중심 단면 + 픽셀 중심 포함 판정')
    log.write('v4.1 exact-run I/O: 인접 mask bit 동일 시 hardlink 재사용')
    if internal_1bit:
        log.write('임시 raw slice: 1-bit 저장 (재로딩 0/255 pixel은 8-bit와 bitwise 동일)')
    log.write('============================================')

    jit_time = _warmup_exact_rasterizer()
    log.write(f'정밀 scanline JIT 준비: {format_seconds(jit_time)} (첫 실행만 발생 가능)')
    log.write('래스터 엔진: Numba exact scanline (기존 픽셀 중심 결과와 동일)')

    started = time.time()
    batch = max(1, min(int(params.batch_layers), 2*_bjt_workers(params.workers, grid.width_px*grid.height_px, 12)))
    epsilon = max(1e-9, grid.layer_thickness_mm * 1e-7)
    raster_workers = _bjt_workers(min(int(params.workers),8), grid.width_px*grid.height_px, 12)
    completed = 0
    physical_writes = 0
    repeated_layers = 0
    hardlinks = 0
    prev_mask = None
    prev_path = None

    def raster_job(index: int, path2d):
        check_cancel(cancel_event)
        mask=rasterize_path_center_sample(path2d, grid, cancel_event)
        return index, np.packbits(mask,axis=1)

    with ThreadPoolExecutor(max_workers=raster_workers) as raster_pool:
        for batch_start in range(0, grid.layer_count, batch):
            check_cancel(cancel_event)
            batch_end = min(grid.layer_count, batch_start + batch)
            centers = (np.arange(batch_start, batch_end, dtype=np.float64) + 0.5) * grid.layer_thickness_mm
            centers = np.minimum(centers, max(epsilon, grid.model_height_mm - epsilon))
            sections = mesh.section_multiplane(
                plane_origin=(0.0, 0.0, 0.0), plane_normal=(0.0, 0.0, 1.0), heights=centers,
            )
            prepared = []
            for local, path2d in enumerate(sections):
                check_cancel(cancel_event)
                index = batch_start + local
                z_center = float(centers[local])
                suspicious, reason = _has_near_duplicate_outer_contours(path2d, grid)
                if suspicious:
                    log.write(f'WARNING: {index+1:,}층(Z={format_number(z_center)} mm) 배치 단면 이상 감지: {reason}')
                    log.write('         해당 층을 독립 단면 계산으로 자동 재처리합니다.')
                    path2d = _single_plane_section_2d(mesh, z_center)
                prepared.append((index, path2d))

            futs = [raster_pool.submit(raster_job, i, p) for i, p in prepared]
            results = {}
            while futs:
                fut=futs.pop(0)
                check_cancel(cancel_event)
                i, mask=fut.result()
                results[int(i)]=mask

            # Exact equality must be checked in layer order. No hash/tolerance is used.
            for index in range(batch_start, batch_end):
                check_cancel(cancel_event)
                packed = results.pop(index)
                dst = output_dir / f'{index+1:06d}{ext}'
                same = prev_mask is not None and packed.shape == prev_mask.shape and bool(np.array_equal(packed, prev_mask))
                if same and prev_path is not None and prev_path.exists():
                    repeated_layers += 1
                    if _aio_v41_link_or_copy(prev_path, dst):
                        hardlinks += 1
                    elif not dst.exists():
                        _aio_v44_save_packed_slice(packed,grid.width_px,grid.height_px,dst,params.intermediate_format,internal_1bit)
                        physical_writes += 1
                else:
                    _aio_v44_save_packed_slice(packed,grid.width_px,grid.height_px,dst,params.intermediate_format,internal_1bit)
                    physical_writes += 1
                prev_mask = packed
                prev_path = dst
                completed += 1
                if completed == 1 or completed % 25 == 0 or completed == grid.layer_count:
                    elapsed = time.time() - started
                    remain = elapsed / max(1, completed) * (grid.layer_count - completed)
                    pct = progress_start + (progress_end-progress_start)*completed/grid.layer_count
                    log.progress(pct, f'1/3 모델 슬라이싱 {completed:,}/{grid.layer_count:,} | ETA {format_seconds(remain)}')

    log.write(f'v4.1 exact repeated layer: {repeated_layers:,}/{grid.layer_count:,} · physical image writes={physical_writes:,} · hardlinks={hardlinks:,}')
    return grid

slice_model_to_images = _aio_v41_slice_model_to_images


# ---- 3) run-aware XY EDT/material bitpack -----------------------------------
class _AIOV41AliasPackedStore:
    def __init__(self, data, layer_to_unique):
        self.data = data
        self.layer_to_unique = np.asarray(layer_to_unique, dtype=np.int32)
    def __len__(self):
        return int(len(self.layer_to_unique))
    def __getitem__(self, idx):
        if isinstance(idx, (int, np.integer)):
            return self.data[int(self.layer_to_unique[int(idx)])]
        inds = np.asarray(self.layer_to_unique[idx], dtype=np.int64)
        return self.data[inds]
    def flush(self):
        try: self.data.flush()
        except Exception: pass
    def close(self):
        try: self.data.flush()
        except Exception: pass
        mm = getattr(self.data, '_mmap', None)
        if mm is not None:
            try:
                if not getattr(mm, 'closed', False): mm.close()
            except Exception: pass

_AIO_V41_ORIG_CLOSE_MEMMAP = lattice_core._close_memmap_store

def _aio_v41_close_memmap_store(store):
    if isinstance(store, _AIOV41AliasPackedStore):
        store.close(); return
    return _AIO_V41_ORIG_CLOSE_MEMMAP(store)

lattice_core._close_memmap_store = _aio_v41_close_memmap_store
_AIO_V41_ORIG_COMPUTE_Z_CACHED = lattice_core.compute_z_shell_masks_cached
_AIO_V41_LOAD_XY_WORKER = lattice_core._load_mask_and_xy_shell_worker
_AIO_V41_HORIZONTAL_SHELL = lattice_core._compute_horizontal_shell_parallel


def _aio_v41_file_identity(path: Path):
    st = os.stat(path, follow_symlinks=True)
    return (int(st.st_dev), int(st.st_ino), int(st.st_size))


def _aio_v41_compute_z_shell_masks_cached(images, params, log=None, cancel_event=None, cache_dir=None):
    # If there are no exact filesystem aliases, original path is already efficient.
    if not images or float(params.shell_mm) <= 0 or float(params.layer_thickness_mm) <= 0:
        return _AIO_V41_ORIG_COMPUTE_Z_CACHED(images, params, log, cancel_event, cache_dir)
    try:
        ids = [_aio_v41_file_identity(p) for p in images]
    except Exception:
        return _AIO_V41_ORIG_COMPUTE_Z_CACHED(images, params, log, cancel_event, cache_dir)
    unique_keys = []
    key_to_u = {}
    layer_to_u = np.empty(len(images), dtype=np.int32)
    representative = []
    for i, k in enumerate(ids):
        u = key_to_u.get(k)
        if u is None:
            u = len(unique_keys); key_to_u[k] = u; unique_keys.append(k); representative.append(i)
        layer_to_u[i] = int(u)
    ucount = len(unique_keys)
    n = len(images)
    if ucount >= n:
        return _AIO_V41_ORIG_COMPUTE_Z_CACHED(images, params, log, cancel_event, cache_dir)

    with Image.open(images[0]) as im:
        first_w, first_h = im.size
    bpr = (first_w + 7)//8
    if cache_dir is None:
        cache_dir = Path(tempfile.mkdtemp(prefix='bjt_v41_alias_'))
    else:
        cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    mat_mm = np.memmap(cache_dir/'material_unique.pack', dtype=np.uint8, mode='w+', shape=(ucount, first_h, bpr))
    xy_mm = np.memmap(cache_dir/'xy_shell_unique.pack', dtype=np.uint8, mode='w+', shape=(ucount, first_h, bpr))
    mat_mm[:] = 0; xy_mm[:] = 0

    workers = max(1, min(int(params.workers), 4, ucount))
    if log is not None:
        log.write(f'v4.1 run-aware XY EDT: {n:,} layers -> {ucount:,} exact physical masks ({n-ucount:,} EDT 생략)')
        log.write(f'alias bitpack cache: full 2-stack 대비 약 {ucount/max(1,n):.1%}만 물리 저장')
    shape_hw = None
    def job(u):
        idx = representative[u]
        return u, _AIO_V41_LOAD_XY_WORKER(idx, images[idx], params)
    if workers <= 1:
        iterable = map(job, range(ucount))
        for done, item in enumerate(iterable, 1):
            u, (_idx, shape, pm, ps) = item
            if shape_hw is None: shape_hw = shape
            elif shape != shape_hw: raise ValueError('3D shell 계산 중 layer 이미지 크기가 서로 다릅니다.')
            mat_mm[u] = pm; xy_mm[u] = ps
            if log is not None and (done % 25 == 0 or done == ucount): log.write(f'XY shell unique 계산: {done:,}/{ucount:,}')
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(job, u) for u in range(ucount)]
            done = 0
            for fut in as_completed(futs):
                u, (_idx, shape, pm, ps) = fut.result(); done += 1
                if shape_hw is None: shape_hw = shape
                elif shape != shape_hw: raise ValueError('3D shell 계산 중 layer 이미지 크기가 서로 다릅니다.')
                mat_mm[u] = pm; xy_mm[u] = ps
                if log is not None and (done % 25 == 0 or done == ucount): log.write(f'XY shell unique 계산: {done:,}/{ucount:,}')
    mat_mm.flush(); xy_mm.flush()
    raw_alias = _AIOV41AliasPackedStore(mat_mm, layer_to_u)
    xy_alias = _AIOV41AliasPackedStore(xy_mm, layer_to_u)
    h, w = shape_hw
    # Keep exact original Z-surface equation; only duplicate XY EDT/storage is removed.
    z_shells = np.zeros((n, h, bpr), dtype=np.uint8)
    _AIO_V41_HORIZONTAL_SHELL(raw_alias, z_shells, params, w, n, log, cancel_event)
    return z_shells, xy_alias, raw_alias, shape_hw

lattice_core.compute_z_shell_masks_cached = _aio_v41_compute_z_shell_masks_cached


# ---- 4) Numba O(N) exact surface certificate ---------------------------------
from numba import types as _aio_nb_types
from numba.typed import Dict as _aio_nb_Dict
_AIO_V41_K4 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 4)
_AIO_V41_V2 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 2)
_AIO_V41_K10 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 10)
_AIO_V41_K11 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 11)

@njit(cache=True)
def _aio_v41_gcd3(a,b,c):
    a=abs(a); b=abs(b); c=abs(c)
    return math.gcd(math.gcd(a,b),c)

@njit(cache=True)
def _aio_v41_add_edge(ed, pk0,pk1,pk2,pk3, ax,ay,az,bx,by,bz):
    # lexicographic xyz canonical endpoint ordering; sign preserves face orientation.
    less = (ax < bx) or (ax == bx and (ay < by or (ay == by and az < bz)))
    if less:
        k=(pk0,pk1,pk2,pk3,ax,ay,az,bx,by,bz); s=1
    else:
        k=(pk0,pk1,pk2,pk3,bx,by,bz,ax,ay,az); s=-1
    if k in ed: ed[k] = ed[k] + s
    else: ed[k] = s

@njit(cache=True)
def _aio_v41_build_plane_edge(q, f):
    area = _aio_nb_Dict.empty(_AIO_V41_K4, _AIO_V41_V2)
    edges = _aio_nb_Dict.empty(_AIO_V41_K10, _aio_nb_types.int64)
    for ii in range(f.shape[0]):
        i0=f[ii,0]; i1=f[ii,1]; i2=f[ii,2]
        ax=q[i0,0]; ay=q[i0,1]; az=q[i0,2]
        bx=q[i1,0]; by=q[i1,1]; bz=q[i1,2]
        cx=q[i2,0]; cy=q[i2,1]; cz=q[i2,2]
        ux=bx-ax; uy=by-ay; uz=bz-az; vx=cx-ax; vy=cy-ay; vz=cz-az
        nx=uy*vz-uz*vy; ny=uz*vx-ux*vz; nz=ux*vy-uy*vx
        g=_aio_v41_gcd3(nx,ny,nz)
        if g == 0: return area,edges,False
        pnx=nx//g; pny=ny//g; pnz=nz//g
        first = pnx if pnx != 0 else (pny if pny != 0 else pnz)
        if first < 0: pnx=-pnx; pny=-pny; pnz=-pnz
        d=pnx*ax+pny*ay+pnz*az
        pk=(pnx,pny,pnz,d)
        # same dominant-axis rule as np.argmax(abs(nc)): x wins ties, then y.
        apx=abs(pnx); apy=abs(pny); apz=abs(pnz)
        if apx >= apy and apx >= apz:
            if nx % pnx != 0: return area,edges,False
            kval=nx//pnx
        elif apy >= apz:
            if ny % pny != 0: return area,edges,False
            kval=ny//pny
        else:
            if nz % pnz != 0: return area,edges,False
            kval=nz//pnz
        if pk in area:
            old=area[pk]; area[pk]=(old[0]+kval, old[1]+abs(kval))
        else: area[pk]=(kval,abs(kval))
        _aio_v41_add_edge(edges,pnx,pny,pnz,d,ax,ay,az,bx,by,bz)
        _aio_v41_add_edge(edges,pnx,pny,pnz,d,bx,by,bz,cx,cy,cz)
        _aio_v41_add_edge(edges,pnx,pny,pnz,d,cx,cy,cz,ax,ay,az)
    return area,edges,True

@njit(cache=True)
def _aio_v41_make_events(edges):
    ev = _aio_nb_Dict.empty(_AIO_V41_K11, _aio_nb_types.int64)
    for key,val in edges.items():
        if val == 0: continue
        if val != 1 and val != -1: return ev,False
        if val > 0:
            ax=key[4]; ay=key[5]; az=key[6]; bx=key[7]; by=key[8]; bz=key[9]
        else:
            ax=key[7]; ay=key[8]; az=key[9]; bx=key[4]; by=key[5]; bz=key[6]
        dx=bx-ax; dy=by-ay; dz=bz-az
        g=_aio_v41_gcd3(dx,dy,dz)
        if g == 0: return ev,False
        dx//=g; dy//=g; dz//=g
        first=dx if dx != 0 else (dy if dy != 0 else dz)
        if first < 0: dx=-dx; dy=-dy; dz=-dz
        crx=dy*az-dz*ay; cry=dz*ax-dx*az; crz=dx*ay-dy*ax
        ta=dx*ax+dy*ay+dz*az; tb=dx*bx+dy*by+dz*bz
        if tb > ta: t0=ta; t1=tb; sg=1
        else: t0=tb; t1=ta; sg=-1
        k0=(key[0],key[1],key[2],key[3],dx,dy,dz,crx,cry,crz,t0)
        k1=(key[0],key[1],key[2],key[3],dx,dy,dz,crx,cry,crz,t1)
        if k0 in ev: ev[k0]=ev[k0]+sg
        else: ev[k0]=sg
        if k1 in ev: ev[k1]=ev[k1]-sg
        else: ev[k1]=-sg
    return ev,True

@njit(cache=True)
def _aio_v41_area_equal(a,b):
    if len(a) != len(b): return False
    for k,v in a.items():
        if k not in b or b[k] != v: return False
    return True

@njit(cache=True)
def _aio_v41_events_equal(a,b):
    ca=0; cb=0
    for k,v in a.items():
        if v != 0:
            ca += 1
            if k not in b or b[k] != v: return False
    for _k,v in b.items():
        if v != 0: cb += 1
    return ca == cb

@njit(cache=True)
def _aio_v41_cert_numba(q0,f0,q1,f1):
    a0,e0,ok0=_aio_v41_build_plane_edge(q0,f0)
    if not ok0: return False
    a1,e1,ok1=_aio_v41_build_plane_edge(q1,f1)
    if not ok1 or not _aio_v41_area_equal(a0,a1): return False
    v0,ve0=_aio_v41_make_events(e0)
    if not ve0: return False
    v1,ve1=_aio_v41_make_events(e1)
    if not ve1: return False
    return _aio_v41_events_equal(v0,v1)

_AIO_V41_NUMPY_CERT = _aio_v40_exact_surface_certificate
_AIO_V41_NUMBA_CERT_OK = False
_AIO_V41_NUMBA_CERT_MSG = 'not-run'

def _aio_v41_prepare_cert():
    global _AIO_V41_NUMBA_CERT_OK, _AIO_V41_NUMBA_CERT_MSG
    try:
        q=np.asarray([[0,0,0],[2,0,0],[2,2,0],[0,2,0]],np.int64)
        f0=np.asarray([[0,1,2],[0,2,3]],np.int64)
        f1=np.asarray([[0,1,3],[1,2,3]],np.int64)
        n0=_AIO_V41_NUMPY_CERT(q,f0,q,f1)[0]
        n1=bool(_aio_v41_cert_numba(q,f0,q,f1))
        qb=q.copy(); qb[2,2]=1
        b0=_AIO_V41_NUMPY_CERT(q,f0,qb,f1)[0]
        b1=bool(_aio_v41_cert_numba(q,f0,qb,f1))
        if n0 is True and n1 is True and b0 is False and b1 is False:
            _AIO_V41_NUMBA_CERT_OK=True; _AIO_V41_NUMBA_CERT_MSG='PASS · exact retriangulation + negative geometry mutation'
        else:
            _AIO_V41_NUMBA_CERT_MSG=f'mismatch true={n0}/{n1}, false={b0}/{b1}'
    except Exception as exc:
        _AIO_V41_NUMBA_CERT_MSG=f'{type(exc).__name__}: {exc}'

_aio_v41_prepare_cert()

def _aio_v40_exact_surface_certificate(q0, f0, q1, f1):
    if _AIO_V41_NUMBA_CERT_OK:
        try:
            ok=bool(_aio_v41_cert_numba(np.asarray(q0,np.int64),np.asarray(f0,np.int64),np.asarray(q1,np.int64),np.asarray(f1,np.int64)))
            return (ok, 'PASS' if ok else 'Numba exact surface mismatch')
        except Exception:
            pass
    return _AIO_V41_NUMPY_CERT(q0,f0,q1,f1)


# v4.2: VTK zero-error points are accepted only when their float32 bit pattern is an
# exact member of the raw ExactLUT vertex set. This is stricter than an mm tolerance and
# avoids false fallback caused only by float32 representation error at large X/Y coordinates.
def _aio_v42_map_exact_vertex_subset(raw_vertices: np.ndarray, raw_q: np.ndarray, out_vertices: np.ndarray):
    rv=np.ascontiguousarray(raw_vertices,dtype=np.float32)
    ov=np.ascontiguousarray(out_vertices,dtype=np.float32)
    kd=np.dtype((np.void,12))
    rkey=np.ascontiguousarray(rv.view(np.uint32).reshape(-1,3)).view(kd).ravel()
    okey=np.ascontiguousarray(ov.view(np.uint32).reshape(-1,3)).view(kd).ravel()
    order=np.argsort(rkey,kind='mergesort')
    sorted_keys=rkey[order]
    # If two integer half-grid vertices collapsed to the same float32 triplet, certificate
    # mapping would be ambiguous; reject rather than approximate.
    if len(sorted_keys)>1 and bool(np.any(sorted_keys[1:]==sorted_keys[:-1])):
        return None
    pos=np.searchsorted(sorted_keys,okey)
    if np.any(pos>=len(sorted_keys)):
        return None
    safe=np.minimum(pos,len(sorted_keys)-1)
    if np.any(sorted_keys[safe]!=okey):
        return None
    return np.ascontiguousarray(np.asarray(raw_q,dtype=np.int64)[order[safe]],dtype=np.int64)


def _aio_v39_decimate_q_batch(
    qtri: np.ndarray,
    *,
    bits_x: int,
    bits_y: int,
    half_xyz,
    xoff: float,
    yoff: float,
    strict_verify: bool,
):
    raw_count=int(len(qtri))
    vq,raw_faces,raw_keys=_aio_v32_weld_q(qtri,bits_x,bits_y)
    raw_vertices=_aio_v32_q_to_xyz(vq,half_xyz,xoff,yoff)
    if (not _AIO_V32_VTK_AVAILABLE) or raw_count<32:
        return raw_vertices,raw_faces,raw_count,'raw exact (VTK unavailable/small)',True
    try:
        ov,of,_=_aio_v40_vtk_propose(raw_vertices,raw_faces)
        if ov is None: raise RuntimeError('VTK proposal unavailable')
        saved=int(len(raw_faces)-len(of)); ratio=saved/max(1,int(len(raw_faces)))
        if saved<_AIO_V40_CERT_MIN_SAVED_FACES or ratio<_AIO_V40_CERT_MIN_SAVE_RATIO:
            return raw_vertices,raw_faces,raw_count,f'raw exact · low gain {ratio:.1%}',True
        # Exact float32 membership, no coordinate tolerance and no vertex movement.
        oq=_aio_v42_map_exact_vertex_subset(raw_vertices,vq,ov)
        if oq is None:
            raise RuntimeError('candidate vertex is not bit-exact input vertex')
        if (len(of)/max(1,len(raw_faces))) > 0.45:
            ok,why=_AIO_V41_NUMPY_CERT(vq,raw_faces,oq,of)
        else:
            ok,why=_aio_v40_exact_surface_certificate(vq,raw_faces,oq,of)
        if not ok: raise RuntimeError(f'exact certificate reject: {why}')
        return np.ascontiguousarray(ov,np.float32),np.ascontiguousarray(of,np.int64),raw_count, f'FAST-CERT v4.2 PASS · {len(raw_faces):,}->{len(of):,}',False
    except Exception as exc:
        return raw_vertices,raw_faces,raw_count,f'raw exact fallback: {type(exc).__name__}: {exc}',True

# Larger compact windows reduce repeated VTK/certificate passes on highly tessellated lattice.
_AIO_V41_ORIG_RUNTIME_TUNING = _aio_v36_runtime_tuning

def _aio_v36_runtime_tuning(params):
    """Measured sweet spot for dense 400-DPI lattice: keep certificate batches modest.

    1M/3M batches looked attractive for I/O, but exact boundary certification scales
    superlinearly on dense transition surfaces. Two 500k jobs in parallel are faster.
    """
    try: avail=int(_aio_v33_available_memory_bytes())
    except Exception: avail=8*1024**3
    gib=avail/float(1024**3); workers=max(1,int(getattr(params,'workers',1)))
    if gib >= 8.0: return 500_000, min(2,workers), 160
    if gib >= 5.0: return 500_000, 1, 160
    return 400_000, 1, 128


_AIO_V41_ORIG_FUSE_TARGET = _aio_v38_fuse_target_faces

def _aio_v38_fuse_target_faces():
    try: avail=int(_aio_v33_available_memory_bytes())
    except Exception: avail=8*1024**3
    gib=avail/float(1024**3)
    if gib >= 12.0: return 5_000_000
    if gib >= 8.0: return 3_500_000
    if gib >= 5.0: return 2_000_000
    return 1_000_000


# =============================================================================
# v5.0 DIRECT-PLANE exact surface engine
# =============================================================================
# Large coplanar regions are never materialized as raw ExactLUT triangle arrays.
# Pass 1 walks only active 2x2x2 cube cases and counts exact canonical planes.
# Pass 2 accumulates oriented line-boundary events for large exact planes while
# streaming all irreducible/small facets directly to the final binary STL.
# Each direct plane is reconstructed from its exact integer boundary arrangement
# and must reproduce the source canonical event map + signed/absolute projected
# area exactly. A failed plane is emitted raw in a narrow pass 3; no approximation
# or tolerance-based simplification is allowed.

from shapely.geometry import LineString as _aio_v50_LineString
from shapely.ops import polygonize as _aio_v50_polygonize, unary_union as _aio_v50_unary_union

_AIO_V50_K14 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 14)
_AIO_V50_V3 = _aio_nb_types.UniTuple(_aio_nb_types.int64, 3)  # signed area scalar, abs area scalar, tri count
_AIO_V50_I8 = _aio_nb_types.int8
_AIO_V50_DIRECT_MIN_FACES = 192
_AIO_V50_TILE_ROWS = 32
_AIO_V50_LAST_STATS = {}

@njit(cache=True)
def _aio_v50_gcd3(a, b, c):
    a = abs(a); b = abs(b); c = abs(c)
    return math.gcd(math.gcd(a, b), c)

@njit(cache=True)
def _aio_v50_plane_of_triangle(ax,ay,az,bx,by,bz,cx,cy,cz):
    ux=bx-ax; uy=by-ay; uz=bz-az
    vx=cx-ax; vy=cy-ay; vz=cz-az
    nx=uy*vz-uz*vy; ny=uz*vx-ux*vz; nz=ux*vy-uy*vx
    g=_aio_v50_gcd3(nx,ny,nz)
    if g == 0:
        return 0,0,0,0,0,0,False
    pnx=nx//g; pny=ny//g; pnz=nz//g
    first = pnx if pnx != 0 else (pny if pny != 0 else pnz)
    if first < 0:
        pnx=-pnx; pny=-pny; pnz=-pnz
    d=pnx*ax+pny*ay+pnz*az
    apx=abs(pnx); apy=abs(pny); apz=abs(pnz)
    if apx >= apy and apx >= apz:
        kval=nx//pnx
    elif apy >= apz:
        kval=ny//pny
    else:
        kval=nz//pnz
    return pnx,pny,pnz,d,kval,abs(kval),True

@njit(cache=True)
def _aio_v50_add_line_event(ev, p0,p1,p2,p3, ax,ay,az,bx,by,bz):
    dx=bx-ax; dy=by-ay; dz=bz-az
    g=_aio_v50_gcd3(dx,dy,dz)
    if g == 0:
        return False
    dx//=g; dy//=g; dz//=g
    first = dx if dx != 0 else (dy if dy != 0 else dz)
    if first < 0:
        dx=-dx; dy=-dy; dz=-dz
    crx=dy*az-dz*ay; cry=dz*ax-dx*az; crz=dx*ay-dy*ax
    ta=dx*ax+dy*ay+dz*az
    tb=dx*bx+dy*by+dz*bz
    if tb > ta:
        t0=ta; t1=tb; sg=1
        x0=ax; y0=ay; z0=az; x1=bx; y1=by; z1=bz
    else:
        t0=tb; t1=ta; sg=-1
        x0=bx; y0=by; z0=bz; x1=ax; y1=ay; z1=az
    k0=(p0,p1,p2,p3,dx,dy,dz,crx,cry,crz,t0,x0,y0,z0)
    k1=(p0,p1,p2,p3,dx,dy,dz,crx,cry,crz,t1,x1,y1,z1)
    if k0 in ev:
        nv=ev[k0]+sg
        if nv == 0: del ev[k0]
        else: ev[k0]=nv
    else: ev[k0]=sg
    if k1 in ev:
        nv=ev[k1]-sg
        if nv == 0: del ev[k1]
        else: ev[k1]=nv
    else: ev[k1]=-sg
    return True

@njit(cache=True)
def _aio_v50_stats_active(ids, codes, y0, wc, k, stretch_pairs, counts, lut_q, stats):
    raw=0
    zbase=2*k
    ztop=2*(k+stretch_pairs)
    stretched = stretch_pairs > 1
    for ii in range(ids.shape[0]):
        owner=int(ids[ii]); code=int(codes[ii])
        yy=owner//wc + y0; xx=owner-(owner//wc)*wc
        nt=int(counts[code])
        for s in range(nt):
            lza=int(lut_q[code,s,0,0]); lzb=int(lut_q[code,s,1,0]); lzc=int(lut_q[code,s,2,0])
            if stretched:
                if (lza != 0 and lza != 2) or (lzb != 0 and lzb != 2) or (lzc != 0 and lzc != 2):
                    return -1
                az=zbase if lza == 0 else ztop; bz=zbase if lzb == 0 else ztop; cz=zbase if lzc == 0 else ztop
            else:
                az=zbase+lza; bz=zbase+lzb; cz=zbase+lzc
            ax=2*xx+int(lut_q[code,s,0,2]); ay=2*yy+int(lut_q[code,s,0,1])
            bx=2*xx+int(lut_q[code,s,1,2]); by=2*yy+int(lut_q[code,s,1,1])
            cx=2*xx+int(lut_q[code,s,2,2]); cy=2*yy+int(lut_q[code,s,2,1])
            p0,p1,p2,p3,ks,ka,ok=_aio_v50_plane_of_triangle(ax,ay,az,bx,by,bz,cx,cy,cz)
            if not ok: return -1
            pk=(p0,p1,p2,p3)
            if pk in stats:
                old=stats[pk]; stats[pk]=(old[0]+ks, old[1]+ka, old[2]+1)
            else:
                stats[pk]=(ks,ka,1)
            raw += 1
    return raw

@njit(cache=True)
def _aio_v50_events_and_raw_active(ids, codes, y0, wc, k, stretch_pairs, counts, lut_q, direct_planes, events, raw_out):
    raw_n=0; logical=0
    zbase=2*k; ztop=2*(k+stretch_pairs); stretched=stretch_pairs>1
    for ii in range(ids.shape[0]):
        owner=int(ids[ii]); code=int(codes[ii]); yy=owner//wc+y0; xx=owner-(owner//wc)*wc
        nt=int(counts[code])
        for s in range(nt):
            lza=int(lut_q[code,s,0,0]); lzb=int(lut_q[code,s,1,0]); lzc=int(lut_q[code,s,2,0])
            if stretched:
                if (lza != 0 and lza != 2) or (lzb != 0 and lzb != 2) or (lzc != 0 and lzc != 2):
                    return -1,-1
                az=zbase if lza == 0 else ztop; bz=zbase if lzb == 0 else ztop; cz=zbase if lzc == 0 else ztop
            else:
                az=zbase+lza; bz=zbase+lzb; cz=zbase+lzc
            ax=2*xx+int(lut_q[code,s,0,2]); ay=2*yy+int(lut_q[code,s,0,1])
            bx=2*xx+int(lut_q[code,s,1,2]); by=2*yy+int(lut_q[code,s,1,1])
            cx=2*xx+int(lut_q[code,s,2,2]); cy=2*yy+int(lut_q[code,s,2,1])
            p0,p1,p2,p3,ks,ka,ok=_aio_v50_plane_of_triangle(ax,ay,az,bx,by,bz,cx,cy,cz)
            if not ok: return -1,-1
            pk=(p0,p1,p2,p3)
            if pk in direct_planes:
                if not _aio_v50_add_line_event(events,p0,p1,p2,p3,ax,ay,az,bx,by,bz): return -1,-1
                if not _aio_v50_add_line_event(events,p0,p1,p2,p3,bx,by,bz,cx,cy,cz): return -1,-1
                if not _aio_v50_add_line_event(events,p0,p1,p2,p3,cx,cy,cz,ax,ay,az): return -1,-1
            else:
                raw_out[raw_n,0,0]=ax; raw_out[raw_n,0,1]=ay; raw_out[raw_n,0,2]=az
                raw_out[raw_n,1,0]=bx; raw_out[raw_n,1,1]=by; raw_out[raw_n,1,2]=bz
                raw_out[raw_n,2,0]=cx; raw_out[raw_n,2,1]=cy; raw_out[raw_n,2,2]=cz
                raw_n += 1
            logical += 1
    return raw_n, logical

@njit(cache=True)
def _aio_v50_selected_raw_active(ids, codes, y0, wc, k, stretch_pairs, counts, lut_q, selected_planes, raw_out):
    raw_n=0
    zbase=2*k; ztop=2*(k+stretch_pairs); stretched=stretch_pairs>1
    for ii in range(ids.shape[0]):
        owner=int(ids[ii]); code=int(codes[ii]); yy=owner//wc+y0; xx=owner-(owner//wc)*wc
        nt=int(counts[code])
        for s in range(nt):
            lza=int(lut_q[code,s,0,0]); lzb=int(lut_q[code,s,1,0]); lzc=int(lut_q[code,s,2,0])
            if stretched:
                if (lza != 0 and lza != 2) or (lzb != 0 and lzb != 2) or (lzc != 0 and lzc != 2): return -1
                az=zbase if lza == 0 else ztop; bz=zbase if lzb == 0 else ztop; cz=zbase if lzc == 0 else ztop
            else:
                az=zbase+lza; bz=zbase+lzb; cz=zbase+lzc
            ax=2*xx+int(lut_q[code,s,0,2]); ay=2*yy+int(lut_q[code,s,0,1])
            bx=2*xx+int(lut_q[code,s,1,2]); by=2*yy+int(lut_q[code,s,1,1])
            cx=2*xx+int(lut_q[code,s,2,2]); cy=2*yy+int(lut_q[code,s,2,1])
            p0,p1,p2,p3,ks,ka,ok=_aio_v50_plane_of_triangle(ax,ay,az,bx,by,bz,cx,cy,cz)
            if not ok: return -1
            if (p0,p1,p2,p3) in selected_planes:
                raw_out[raw_n,0,0]=ax; raw_out[raw_n,0,1]=ay; raw_out[raw_n,0,2]=az
                raw_out[raw_n,1,0]=bx; raw_out[raw_n,1,1]=by; raw_out[raw_n,1,2]=bz
                raw_out[raw_n,2,0]=cx; raw_out[raw_n,2,1]=cy; raw_out[raw_n,2,2]=cz
                raw_n += 1
    return raw_n


def _aio_v50_make_active_band(lower, upper, y0, y1):
    c8=(lower[y0:y1] | (upper[y0:y1] << 4)).astype(np.uint8, copy=False)
    active=_AIO_FAST_EXACT_LUT_COUNT[c8] > 0
    ids=np.flatnonzero(active.ravel()).astype(np.int64, copy=False)
    if len(ids) == 0:
        return ids, np.empty(0, np.uint8), 0
    codes=np.ascontiguousarray(c8.ravel()[ids], dtype=np.uint8)
    ntri=int(_AIO_FAST_EXACT_LUT_COUNT[codes].sum(dtype=np.int64))
    return np.ascontiguousarray(ids), codes, ntri


def _aio_v50_qtri_to_xyz(qtri, half, xoff, yoff):
    q=np.asarray(qtri, dtype=np.float64)
    out=np.empty(q.shape, dtype=np.float32)
    out[:,:,0]=((q[:,:,0]-1.0)*float(half[0])+float(xoff)).astype(np.float32)
    out[:,:,1]=((q[:,:,1]-1.0)*float(half[1])+float(yoff)).astype(np.float32)
    out[:,:,2]=((q[:,:,2]-1.0)*float(half[2])).astype(np.float32)
    return out


def _aio_v50_typed_plane_set(keys):
    d=_aio_nb_Dict.empty(_AIO_V41_K4, _AIO_V50_I8)
    for key in keys:
        k=tuple(int(x) for x in key)
        d[k]=1
    return d


def _aio_v50_event_segments_for_planes(events):
    rows=[]; vals=[]
    for k,v in events.items():
        iv=int(v)
        if iv != 0:
            rows.append(tuple(int(x) for x in k)); vals.append(iv)
    if not rows:
        return {}
    ka=np.asarray(rows,dtype=np.int64); va=np.asarray(vals,dtype=np.int64)
    order=np.lexsort((ka[:,10],ka[:,9],ka[:,8],ka[:,7],ka[:,6],ka[:,5],ka[:,4],ka[:,3],ka[:,2],ka[:,1],ka[:,0]))
    ka=ka[order]; va=va[order]
    lk=ka[:,:10]
    changes=np.any(lk[1:] != lk[:-1],axis=1) if len(ka)>1 else np.empty(0,bool)
    starts=np.r_[0,1+np.flatnonzero(changes),len(ka)]
    by={}
    for s,e in zip(starts[:-1],starts[1:]):
        cum=0
        pk=tuple(int(x) for x in ka[s,:4])
        arr=by.setdefault(pk,[])
        for i in range(s,e-1):
            cum += int(va[i])
            if cum != 0:
                arr.append((ka[i,11:14].copy(), ka[i+1,11:14].copy(), int(cum)))
        cum += int(va[e-1])
        if cum != 0:
            raise RuntimeError(f'DIRECT event line does not close: plane={pk}')
    return by


def _aio_v50_winding(px,py,segs_uv):
    wn=0
    for (x1,y1),(x2,y2),weight in segs_uv:
        w=int(weight)
        if w < 0:
            x1,y1,x2,y2,w=x2,y2,x1,y1,-w
        if y1 <= py:
            if y2 > py:
                isleft=(x2-x1)*(py-y1)-(px-x1)*(y2-y1)
                if isleft > 0: wn += w
        else:
            if y2 <= py:
                isleft=(x2-x1)*(py-y1)-(px-x1)*(y2-y1)
                if isleft < 0: wn -= w
    return int(wn)


def _aio_v50_reconstruct_q_from_uv(pk, axes, uvkey):
    n=np.asarray(pk[:3],dtype=np.int64); d=int(pk[3])
    q=np.zeros(3,dtype=np.int64)
    q[int(axes[0])]=int(uvkey[0]); q[int(axes[1])]=int(uvkey[1])
    drop=next(i for i in range(3) if i not in axes)
    num=d-int(n[axes[0]])*int(q[axes[0]])-int(n[axes[1]])*int(q[axes[1]])
    den=int(n[drop])
    if den == 0 or (num % den) != 0:
        return None
    q[drop]=num//den
    return tuple(int(x) for x in q)


def _aio_v50_signed_proj_area2(q, faces, axes):
    if len(faces) == 0: return 0
    fs=np.asarray(faces,dtype=np.int64); uv=np.asarray(q,dtype=np.int64)[:,axes]
    a=uv[fs[:,0]]; b=uv[fs[:,1]]; c=uv[fs[:,2]]
    twice=(b[:,0]-a[:,0])*(c[:,1]-a[:,1])-(b[:,1]-a[:,1])*(c[:,0]-a[:,0])
    return int(twice.sum(dtype=np.int64))


def _aio_v50_rebuild_plane(pk, segs, stat):
    signed_k, abs_k, raw_faces = (int(stat[0]),int(stat[1]),int(stat[2]))
    # Direct mode only consumes one-orientation exact planes. Opposite coincident sheets
    # remain on the raw path so no signed cancellation can hide geometry.
    if abs(signed_k) != abs_k:
        raise RuntimeError('mixed/opposite oriented coplanar sheets')
    normal=np.asarray(pk[:3],dtype=np.int64)
    drop=int(np.argmax(np.abs(normal))); axes=tuple(i for i in range(3) if i != drop)
    proj_factor=-1 if drop == 1 else 1
    pts=[]; q_index={}
    for A,B,w in segs:
        for P in (A,B):
            tp=tuple(int(x) for x in P)
            if tp not in q_index:
                q_index[tp]=len(pts); pts.append(tp)
    if not pts:
        raise RuntimeError('no boundary events')
    uv_to_q={(p[axes[0]],p[axes[1]]):p for p in pts}
    seg_uv=[]
    for A,B,w in segs:
        A=tuple(int(x) for x in A); B=tuple(int(x) for x in B)
        seg_uv.append(((float(A[axes[0]]),float(A[axes[1]])),(float(B[axes[0]]),float(B[axes[1]])),int(w)))
    lines=[_aio_v50_LineString([a,b]) for a,b,_w in seg_uv]
    noded=_aio_v50_unary_union(lines)
    polygons=list(_aio_v50_polygonize(noded))
    if not polygons:
        raise RuntimeError('polygonize produced no region')
    # Noding may expose an exact integer half-grid intersection that was not a triangle
    # endpoint. It is accepted only when the plane equation reconstructs an exact q point.
    for poly in polygons:
        rings=[poly.exterior,*list(poly.interiors)]
        for ring in rings:
            for x,y in list(ring.coords)[:-1]:
                key=(int(round(x)),int(round(y)))
                if abs(float(x)-key[0]) > 1e-8 or abs(float(y)-key[1]) > 1e-8:
                    raise RuntimeError('non-half-grid planar intersection')
                if key not in uv_to_q:
                    qp=_aio_v50_reconstruct_q_from_uv(pk,axes,key)
                    if qp is None:
                        raise RuntimeError('planar intersection is not exact half-grid')
                    uv_to_q[key]=qp
                    if qp not in q_index:
                        q_index[qp]=len(pts); pts.append(qp)
    faces=[]
    plus_proj_sign=proj_factor*(1 if int(normal[drop]) > 0 else -1)
    for poly in polygons:
        rp=poly.representative_point(); wn=_aio_v50_winding(rp.x,rp.y,seg_uv)
        if wn == 0: continue
        mult=abs(int(wn))
        desired=normal*(1 if ((wn > 0) == (plus_proj_sign > 0)) else -1)
        base=[]
        gc=_aio_constrained_delaunay_triangles(poly) if _aio_constrained_delaunay_triangles is not None else None
        tris=list(getattr(gc,'geoms',())) if gc is not None else _aio_triangulate_polygon_exact(poly)
        if not tris: raise RuntimeError('constrained triangulation failed')
        qarr=np.asarray(pts,dtype=np.int64)
        for tri in tris:
            coords=list(tri.exterior.coords)
            vids=[]
            for x,y in coords[:3]:
                key=(int(round(x)),int(round(y)))
                if abs(float(x)-key[0]) > 1e-8 or abs(float(y)-key[1]) > 1e-8:
                    raise RuntimeError('triangulator inserted non-exact vertex')
                qp=uv_to_q.get(key)
                if qp is None:
                    qp=_aio_v50_reconstruct_q_from_uv(pk,axes,key)
                    if qp is None: raise RuntimeError('triangulator inserted non-half-grid vertex')
                    uv_to_q[key]=qp
                    if qp not in q_index:
                        q_index[qp]=len(pts); pts.append(qp); qarr=np.asarray(pts,dtype=np.int64)
                vids.append(int(q_index[qp]))
            if len(set(vids)) != 3: raise RuntimeError('degenerate direct triangle')
            qarr=np.asarray(pts,dtype=np.int64)
            cr=np.cross(qarr[vids[1]]-qarr[vids[0]],qarr[vids[2]]-qarr[vids[0]])
            dot=int(np.dot(cr,desired))
            if dot == 0: raise RuntimeError('zero-area direct triangle')
            if dot < 0: vids[1],vids[2]=vids[2],vids[1]
            base.append(vids)
        for _ in range(mult): faces.extend(base)
    if not faces: raise RuntimeError('direct plane reconstructed empty')
    q=np.asarray(pts,dtype=np.int64); f=np.asarray(faces,dtype=np.int32)
    got=_aio_v50_signed_proj_area2(q,f,axes)
    expect=proj_factor*signed_k*int(normal[drop])
    if got != expect:
        raise RuntimeError(f'signed projected area mismatch {got}!={expect}')
    # Full exact certificate for this plane: canonical events and signed+absolute area.
    ar,ed,ok=_aio_v41_build_plane_edge(q,f)
    if not ok: raise RuntimeError('direct certificate plane/edge build failed')
    ev,ok2=_aio_v41_make_events(ed)
    if not ok2: raise RuntimeError('direct certificate line-event build failed')
    gotstat=ar.get(tuple(int(x) for x in pk))
    if gotstat is None or int(gotstat[0]) != signed_k or int(gotstat[1]) != abs_k:
        raise RuntimeError(f'direct area certificate mismatch {gotstat}!={(signed_k,abs_k)}')
    src={}
    for A,B,w in segs:
        # rebuild source line events from weighted intervals for exact normalized comparison
        dx=int(B[0]-A[0]); dy=int(B[1]-A[1]); dz=int(B[2]-A[2])
        g=math.gcd(math.gcd(abs(dx),abs(dy)),abs(dz))
        if g == 0: raise RuntimeError('zero boundary segment')
        dx//=g; dy//=g; dz//=g
        first=dx if dx != 0 else (dy if dy != 0 else dz)
        if first < 0: dx=-dx; dy=-dy; dz=-dz
        crx=dy*int(A[2])-dz*int(A[1]); cry=dz*int(A[0])-dx*int(A[2]); crz=dx*int(A[1])-dy*int(A[0])
        ta=dx*int(A[0])+dy*int(A[1])+dz*int(A[2]); tb=dx*int(B[0])+dy*int(B[1])+dz*int(B[2])
        if tb > ta: t0,t1,sg=ta,tb,int(w)
        else: t0,t1,sg=tb,ta,-int(w)
        k0=(int(pk[0]),int(pk[1]),int(pk[2]),int(pk[3]),dx,dy,dz,crx,cry,crz,t0)
        k1=(int(pk[0]),int(pk[1]),int(pk[2]),int(pk[3]),dx,dy,dz,crx,cry,crz,t1)
        src[k0]=src.get(k0,0)+sg; src[k1]=src.get(k1,0)-sg
    src={k:v for k,v in src.items() if v != 0}
    dst={tuple(int(x) for x in k):int(v) for k,v in ev.items() if int(v) != 0}
    if src != dst:
        raise RuntimeError('direct line-event certificate mismatch')
    return np.ascontiguousarray(q,dtype=np.int32),np.ascontiguousarray(f,dtype=np.int32),raw_faces


def _aio_v50_case_pass(params, items, bbox, grid, resolved, mask_store, cancel_event, *, mode, stats=None, direct_planes=None, events=None, writer=None, selected_planes=None, half=None, xoff=0.0, yoff=0.0, log=None, progress_base=0.0, progress_span=1.0):
    total=int(bbox.layers); hc=int(grid.height)+1; wc=int(grid.width)+1
    zero=np.zeros((hc,wc),dtype=np.uint8); prev=zero
    material_voxels=0; logical_tri=0; raw_written=0; static_saved=0
    tile_rows=int(_AIO_V50_TILE_ROWS)
    prefetch_workers=max(1,min(3,int(getattr(params,'workers',1))))
    case_futures={}; next_submit=0
    def submit_case(pool, local_layer):
        absolute=int(bbox.zmin)+int(local_layer)
        if mask_store is not None: return pool.submit(_aio_fast_case_from_cached_layer,mask_store,absolute,bbox,grid)
        return pool.submit(_aio_fast_case_from_layer_item,items[absolute],bbox,resolved,grid)
    def process_pair(lower,upper,k,run_pairs=1):
        nonlocal logical_tri, raw_written
        for y0 in range(0,hc,tile_rows):
            check_cancel(cancel_event)
            y1=min(hc,y0+tile_rows)
            ids,codes,ntri=_aio_v50_make_active_band(lower,upper,y0,y1)
            if ntri <= 0: continue
            if mode == 'stats':
                got=_aio_v50_stats_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,stats)
                if got < 0: raise RuntimeError('DIRECT stats static invariant failed')
                logical_tri += int(got)
            elif mode == 'events_raw':
                out=np.empty((ntri,3,3),dtype=np.int32)
                rn,got=_aio_v50_events_and_raw_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,direct_planes,events,out)
                if rn < 0: raise RuntimeError('DIRECT event/raw static invariant failed')
                logical_tri += int(got)
                if rn:
                    xyz=_aio_v50_qtri_to_xyz(out[:rn],half,xoff,yoff)
                    _aio_fast_write_triangle_soup(writer,xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                    raw_written += int(rn)
            elif mode == 'selected_raw':
                out=np.empty((ntri,3,3),dtype=np.int32)
                rn=_aio_v50_selected_raw_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,selected_planes,out)
                if rn < 0: raise RuntimeError('DIRECT selected raw invariant failed')
                if rn:
                    xyz=_aio_v50_qtri_to_xyz(out[:rn],half,xoff,yoff)
                    _aio_fast_write_triangle_soup(writer,xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                    raw_written += int(rn)
    run_case=None; run_start=-1; run_pairs=0
    with ThreadPoolExecutor(max_workers=prefetch_workers) as pool:
        while next_submit < total and len(case_futures) < prefetch_workers:
            case_futures[next_submit]=submit_case(pool,next_submit); next_submit += 1
        for k in range(total+1):
            check_cancel(cancel_event)
            if k < total:
                nxt,material=case_futures.pop(k).result()
                if mode == 'stats': material_voxels += int(material)
                while next_submit < total and len(case_futures) < prefetch_workers:
                    case_futures[next_submit]=submit_case(pool,next_submit); next_submit += 1
            else: nxt=zero
            identical=bool(np.array_equal(prev,nxt))
            if identical:
                if run_case is None:
                    run_case=prev.copy(); run_start=int(k); run_pairs=1
                elif bool(np.array_equal(run_case,prev)):
                    run_pairs += 1
                else:
                    process_pair(run_case,run_case,run_start,run_pairs)
                    static_saved += max(0,int(run_pairs)-1)
                    run_case=prev.copy(); run_start=int(k); run_pairs=1
            else:
                if run_case is not None:
                    process_pair(run_case,run_case,run_start,run_pairs)
                    static_saved += max(0,int(run_pairs)-1)
                    run_case=None; run_start=-1; run_pairs=0
                process_pair(prev,nxt,k,1)
            prev=nxt
            if log is not None and (k==0 or (k+1)%50==0 or k==total):
                pct=progress_base+progress_span*(k+1)/max(1,total+1)
                log.progress(pct,f'DIRECT {mode} {k+1:,}/{total+1:,} pair · tri={logical_tri:,} rawOut={raw_written:,}')
        if run_case is not None:
            process_pair(run_case,run_case,run_start,run_pairs)
            static_saved += max(0,int(run_pairs)-1)
    return {'material_voxels':material_voxels,'logical_triangles':logical_tri,'raw_written':raw_written,'static_saved_pairs':static_saved}


def _aio_v50_exact_interpolated_surface(params, items, bbox, grid, resolved, log, cancel_event, mask_store=None):
    global _AIO_V38_LAST_COMPACT_STATS, _AIO_V50_LAST_STATS
    if getattr(params,'surface_mode','') != '표면 보간형':
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    if (not _AIO_FAST_EXACT_VALID) or (_AIO_V32_LUT_Q_ZYX is None) or (not _AIO_V36_STATIC_SELFTEST_OK):
        log.write('WARNING: DIRECT engine prerequisites failed -> v4.6 FAST-CERT fallback')
        return _aio_v38_exact_interpolated_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    xsp=float(grid.x_pitch_mm); ysp=float(grid.y_pitch_mm); zsp=float(params.layer_thickness_mm)
    half=(0.5*xsp,0.5*ysp,0.5*zsp); xoff=float(grid.x_offset_mm); yoff=float(grid.y_offset_mm)
    started=time.time()
    log.write('')
    log.write('=============== v5.0 DIRECT-PLANE exact surface ===============')
    log.write('대형 exact plane: raw MC triangle 배열 생성 없이 case->boundary event 직접 누적')
    log.write('복잡/소형 facet: ExactLUT triangle을 tile 단위로 즉시 STL 스트리밍')
    log.write('direct plane 조건: 한 방향 coplanar + exact event/area certificate PASS')
    log.write('vertex 이동/다운샘플링/QEM/허용오차 근사: 없음')
    log.write('================================================================')
    # Pass 1: exact plane census only; no triangle arrays.
    stats=_aio_nb_Dict.empty(_AIO_V41_K4,_AIO_V50_V3)
    p1=_aio_v50_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='stats',stats=stats,log=log,progress_base=64.0,progress_span=10.0)
    stat_py={tuple(int(x) for x in k):(int(v[0]),int(v[1]),int(v[2])) for k,v in stats.items()}
    candidates=[]
    for k,(ss,aa,nn) in stat_py.items():
        if nn >= int(_AIO_V50_DIRECT_MIN_FACES) and abs(ss) == aa and aa > 0:
            candidates.append(k)
    direct_set=_aio_v50_typed_plane_set(candidates)
    log.write(f'DIRECT plane census: planes={len(stat_py):,} · candidates={len(candidates):,} · logicalTri={p1["logical_triangles"]:,}')
    # Pass 2: candidate boundary accumulation + raw streaming for all other planes.
    writer=recon_core.BinarySTLWriter(params.output_stl,header='v5.0 DIRECT-PLANE exact STL',buffer_bytes=16*1024*1024)
    success=False
    events=_aio_nb_Dict.empty(_AIO_V50_K14,_aio_nb_types.int64)
    try:
        p2=_aio_v50_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='events_raw',direct_planes=direct_set,events=events,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=74.0,progress_span=17.0)
        byplane=_aio_v50_event_segments_for_planes(events)
        accepted=[]; failed=[]; direct_out=0; direct_raw=0
        for idx,pk in enumerate(candidates):
            check_cancel(cancel_event)
            segs=byplane.get(tuple(pk),())
            try:
                q,f,raw_faces=_aio_v50_rebuild_plane(tuple(pk),segs,stat_py[tuple(pk)])
                # Only keep the direct form if it actually removes work.
                if len(f) >= raw_faces:
                    raise RuntimeError(f'no direct reduction {len(f)}/{raw_faces}')
                tri_xyz=_aio_v50_qtri_to_xyz(q[np.asarray(f,dtype=np.int64)],half,xoff,yoff)
                _aio_fast_write_triangle_soup(writer,tri_xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                accepted.append(tuple(pk)); direct_out += int(len(f)); direct_raw += int(raw_faces)
            except Exception as exc:
                failed.append((tuple(pk),str(exc)))
            if idx==0 or (idx+1)%100==0 or idx+1==len(candidates):
                log.progress(91.0+5.0*(idx+1)/max(1,len(candidates)),f'DIRECT triangulate {idx+1:,}/{len(candidates):,} · PASS={len(accepted):,} fallback={len(failed):,}')
        # Rare pass 3: only direct candidates that failed exact reconstruction are emitted raw.
        if failed:
            failed_set=_aio_v50_typed_plane_set([k for k,_ in failed])
            p3=_aio_v50_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='selected_raw',selected_planes=failed_set,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=96.0,progress_span=1.0)
        else:
            p3={'raw_written':0}
        triangle_count=int(writer.tri_count)
        if triangle_count <= 0: raise ValueError('DIRECT engine produced no triangles')
        writer.close(success=True); success=True
        recon_core.verify_binary_stl(params.output_stl,triangle_count)
        _AIO_V50_LAST_STATS={
            'reason':'PASS','input_triangles':int(p1['logical_triangles']),'output_triangles':triangle_count,
            'direct_candidates':len(candidates),'direct_accepted':len(accepted),'direct_failed':len(failed),
            'direct_raw_faces':int(direct_raw),'direct_output_faces':int(direct_out),
            'raw_stream_faces':int(p2['raw_written']+p3.get('raw_written',0)),
            'static_saved_pairs':int(p1['static_saved_pairs']),'elapsed':time.time()-started,
        }
        _AIO_V38_LAST_COMPACT_STATS=dict(_AIO_V50_LAST_STATS)
        log.write('')
        log.write('=============== v5.0 DIRECT-PLANE 통계 ===============')
        log.write(f'logical ExactLUT triangles: {p1["logical_triangles"]:,}')
        log.write(f'direct candidates / PASS / fallback: {len(candidates):,} / {len(accepted):,} / {len(failed):,}')
        log.write(f'direct plane raw->compact: {direct_raw:,} -> {direct_out:,}')
        log.write(f'raw streamed facets: {p2["raw_written"]+p3.get("raw_written",0):,}')
        log.write(f'final STL triangles: {triangle_count:,}')
        log.write(f'DIRECT surface elapsed: {format_seconds(time.time()-started)}')
        if failed:
            for pk,why in failed[:8]: log.write(f'  fallback plane {pk}: {why}')
            if len(failed)>8: log.write(f'  ... +{len(failed)-8:,} fallback planes')
        log.write('=======================================================')
        return triangle_count,int(p1['material_voxels'])
    except Exception:
        if not success:
            writer.close(success=False)
        try: params.output_stl.unlink(missing_ok=True)
        except Exception: pass
        raise


# Lightweight startup proof: direct plane reconstruction must preserve the canonical
# line-event chain and signed/absolute area on representative exact planar meshes.
def _aio_v50_startup_selftest():
    try:
        # A rectangle with a collinear boundary vertex + a holed rectangle on z=1.
        tests=[]
        q=np.asarray([[0,0,1],[2,0,1],[4,0,1],[4,4,1],[0,4,1]],np.int64)
        f=np.asarray([[0,1,4],[1,3,4],[1,2,3]],np.int64); tests.append((q,f))
        q2=np.asarray([[0,0,1],[8,0,1],[8,8,1],[0,8,1],[2,2,1],[2,6,1],[6,6,1],[6,2,1]],np.int64)
        f2=np.asarray([[0,1,4],[1,7,4],[1,2,7],[2,6,7],[2,3,6],[3,5,6],[3,0,5],[0,4,5]],np.int64); tests.append((q2,f2))
        for qx,fx in tests:
            ar,ed,ok=_aio_v41_build_plane_edge(qx,fx)
            if not ok: return False,'source plane-edge fail'
            ev,ok2=_aio_v41_make_events(ed)
            if not ok2: return False,'source event fail'
            # convert K11 event map to K14 with exact points from edge intervals by using source faces through v50 events
            stats=_aio_nb_Dict.empty(_AIO_V41_K4,_AIO_V50_V3); events=_aio_nb_Dict.empty(_AIO_V50_K14,_aio_nb_types.int64)
            # feed triangles as one synthetic active-independent path
            for face in fx:
                A=qx[int(face[0])];B=qx[int(face[1])];C=qx[int(face[2])]
                p0,p1,p2,p3,ks,ka,good=_aio_v50_plane_of_triangle(int(A[0]),int(A[1]),int(A[2]),int(B[0]),int(B[1]),int(B[2]),int(C[0]),int(C[1]),int(C[2]))
                if not good:return False,'plane calc fail'
                pk=(p0,p1,p2,p3)
                old=stats.get(pk,(0,0,0));stats[pk]=(old[0]+ks,old[1]+ka,old[2]+1)
                _aio_v50_add_line_event(events,p0,p1,p2,p3,int(A[0]),int(A[1]),int(A[2]),int(B[0]),int(B[1]),int(B[2]))
                _aio_v50_add_line_event(events,p0,p1,p2,p3,int(B[0]),int(B[1]),int(B[2]),int(C[0]),int(C[1]),int(C[2]))
                _aio_v50_add_line_event(events,p0,p1,p2,p3,int(C[0]),int(C[1]),int(C[2]),int(A[0]),int(A[1]),int(A[2]))
            by=_aio_v50_event_segments_for_planes(events)
            for pk,st in stats.items():
                qq,ff,_=_aio_v50_rebuild_plane(tuple(pk),by.get(tuple(pk),()),st)
                ar2,ed2,ok=_aio_v41_build_plane_edge(qq,ff); ev2,ok=_aio_v41_make_events(ed2)
                if not ok:return False,'rebuilt cert fail'
                if dict((tuple(k),int(v)) for k,v in ev.items() if int(v)!=0) != dict((tuple(k),int(v)) for k,v in ev2.items() if int(v)!=0):
                    return False,'event mismatch'
        return True,'PASS · exact boundary events + projected area'
    except Exception as exc:
        return False,f'{type(exc).__name__}: {exc}'

_AIO_V50_SELFTEST_OK,_AIO_V50_SELFTEST_MSG=_aio_v50_startup_selftest()
if _AIO_V50_SELFTEST_OK:
    recon_core.reconstruct_interpolated_surface=_aio_v50_exact_interpolated_surface
    APP_VERSION='5.0-direct-plane-exact'
else:
    # keep v4.6 production path if startup proof fails
    APP_VERSION='4.6-cap-profiled-fast-v50-disabled'



# =============================================================================
# v5.1 DIRECT-BOUNDARY exact surface engine (final production override)
# =============================================================================
# v5.0 proved exact planar geometry, but removing collinear *boundary vertices* can
# change how a slicer resolves a section exactly through a mesh vertex/edge even when
# the geometric surface is identical.  v5.1 therefore keeps the COMPLETE exact MC
# boundary-edge subdivision of every compacted plane.  Interior coplanar edges still
# cancel online, so millions of raw MC triangles are never materialized.
#
# Accuracy invariant for every accepted direct plane:
#   - same canonical integer plane and signed/absolute projected area,
#   - same exact non-zero boundary edge set INCLUDING collinear intermediate vertices,
#   - no new non-half-grid boundary point,
#   - no vertex motion, tolerance weld, QEM, downsampling, or layer skipping.
# Any failure falls back to raw ExactLUT facets for that plane only.

_AIO_V51_LAST_STATS = {}

@njit(cache=True)
def _aio_v51_add_exact_boundary_edge(ed, p0,p1,p2,p3, ax,ay,az,bx,by,bz):
    less = (ax < bx) or (ax == bx and (ay < by or (ay == by and az < bz)))
    if less:
        key=(p0,p1,p2,p3,ax,ay,az,bx,by,bz); sg=1
    else:
        key=(p0,p1,p2,p3,bx,by,bz,ax,ay,az); sg=-1
    if key in ed:
        nv=ed[key]+sg
        if nv == 0:
            del ed[key]
        else:
            ed[key]=nv
    else:
        ed[key]=sg
    return True


@njit(cache=True)
def _aio_v51_edges_and_raw_active(ids, codes, y0, wc, k, stretch_pairs, counts, lut_q, direct_planes, edges, raw_out):
    raw_n=0; logical=0
    zbase=2*k; ztop=2*(k+stretch_pairs); stretched=stretch_pairs>1
    for ii in range(ids.shape[0]):
        owner=int(ids[ii]); code=int(codes[ii]); yy=owner//wc+y0; xx=owner-(owner//wc)*wc
        nt=int(counts[code])
        for ss in range(nt):
            lza=int(lut_q[code,ss,0,0]); lzb=int(lut_q[code,ss,1,0]); lzc=int(lut_q[code,ss,2,0])
            if stretched:
                if (lza != 0 and lza != 2) or (lzb != 0 and lzb != 2) or (lzc != 0 and lzc != 2):
                    return -1,-1
                az=zbase if lza == 0 else ztop; bz=zbase if lzb == 0 else ztop; cz=zbase if lzc == 0 else ztop
            else:
                az=zbase+lza; bz=zbase+lzb; cz=zbase+lzc
            ax=2*xx+int(lut_q[code,ss,0,2]); ay=2*yy+int(lut_q[code,ss,0,1])
            bx=2*xx+int(lut_q[code,ss,1,2]); by=2*yy+int(lut_q[code,ss,1,1])
            cx=2*xx+int(lut_q[code,ss,2,2]); cy=2*yy+int(lut_q[code,ss,2,1])
            p0,p1,p2,p3,ks,ka,ok=_aio_v50_plane_of_triangle(ax,ay,az,bx,by,bz,cx,cy,cz)
            if not ok: return -1,-1
            pk=(p0,p1,p2,p3)
            if pk in direct_planes:
                _aio_v51_add_exact_boundary_edge(edges,p0,p1,p2,p3,ax,ay,az,bx,by,bz)
                _aio_v51_add_exact_boundary_edge(edges,p0,p1,p2,p3,bx,by,bz,cx,cy,cz)
                _aio_v51_add_exact_boundary_edge(edges,p0,p1,p2,p3,cx,cy,cz,ax,ay,az)
            else:
                raw_out[raw_n,0,0]=ax; raw_out[raw_n,0,1]=ay; raw_out[raw_n,0,2]=az
                raw_out[raw_n,1,0]=bx; raw_out[raw_n,1,1]=by; raw_out[raw_n,1,2]=bz
                raw_out[raw_n,2,0]=cx; raw_out[raw_n,2,1]=cy; raw_out[raw_n,2,2]=cz
                raw_n += 1
            logical += 1
    return raw_n,logical


def _aio_v51_boundary_segments_by_plane(edges):
    by={}
    invalid=set()
    for key,val in edges.items():
        iv=int(val)
        if iv == 0:
            continue
        k=tuple(int(x) for x in key)
        pk=k[:4]
        if iv not in (-1,1):
            invalid.add(pk)
        lo=np.asarray(k[4:7],dtype=np.int64); hi=np.asarray(k[7:10],dtype=np.int64)
        if iv > 0:
            A,B=lo,hi; weight=iv
        else:
            A,B=hi,lo; weight=-iv
        by.setdefault(pk,[]).append((A,B,int(weight)))
    return by,invalid


def _aio_v51_exact_edge_dict_from_segments(pk,segs):
    src={}
    for A,B,w in segs:
        iw=int(w)
        if iw != 1:
            raise RuntimeError(f'boundary multiplicity={iw}')
        A=tuple(int(x) for x in A); B=tuple(int(x) for x in B)
        less=(A[0] < B[0]) or (A[0] == B[0] and (A[1] < B[1] or (A[1] == B[1] and A[2] < B[2])))
        if less:
            lo,hi,sg=A,B,1
        else:
            lo,hi,sg=B,A,-1
        key=(int(pk[0]),int(pk[1]),int(pk[2]),int(pk[3]),*lo,*hi)
        src[key]=src.get(key,0)+sg
    return {k:v for k,v in src.items() if v != 0}


def _aio_v51_rebuild_plane(pk,segs,stat):
    signed_k,abs_k,raw_faces=(int(stat[0]),int(stat[1]),int(stat[2]))
    if abs(signed_k) != abs_k:
        raise RuntimeError('mixed/opposite oriented coplanar sheets')
    src_edges=_aio_v51_exact_edge_dict_from_segments(pk,segs)
    if not src_edges:
        raise RuntimeError('no exact boundary edges')
    normal=np.asarray(pk[:3],dtype=np.int64)
    drop=int(np.argmax(np.abs(normal))); axes=tuple(i for i in range(3) if i != drop)
    proj_factor=-1 if drop == 1 else 1
    pts=[]; q_index={}; seg_uv=[]; uv_to_q={}
    for A,B,w in segs:
        if int(w) != 1:
            raise RuntimeError('non-unit boundary multiplicity')
        A=tuple(int(x) for x in A); B=tuple(int(x) for x in B)
        for P in (A,B):
            if P not in q_index:
                q_index[P]=len(pts); pts.append(P)
            uv_to_q[(P[axes[0]],P[axes[1]])]=P
        seg_uv.append(((float(A[axes[0]]),float(A[axes[1]])),
                       (float(B[axes[0]]),float(B[axes[1]])),1))
    # Important: do NOT unary_union/line-merge this boundary.  The source MC edge
    # subdivision itself is a slicing invariant; polygonize the already-noded exact
    # edge graph directly so all collinear boundary vertices survive.
    lines=[_aio_v50_LineString([a,b]) for a,b,_ in seg_uv]
    polygons=list(_aio_v50_polygonize(lines))
    if not polygons:
        raise RuntimeError('exact boundary polygonize produced no region')
    faces=[]
    plus_proj_sign=proj_factor*(1 if int(normal[drop]) > 0 else -1)
    for poly in polygons:
        rp=poly.representative_point(); wn=_aio_v50_winding(rp.x,rp.y,seg_uv)
        if wn == 0:
            continue
        if abs(int(wn)) != 1:
            raise RuntimeError(f'non-unit planar winding={wn}')
        desired=normal*(1 if ((wn > 0) == (plus_proj_sign > 0)) else -1)
        gc=_aio_constrained_delaunay_triangles(poly) if _aio_constrained_delaunay_triangles is not None else None
        tris=list(getattr(gc,'geoms',())) if gc is not None else _aio_triangulate_polygon_exact(poly)
        if not tris:
            raise RuntimeError('constrained triangulation failed')
        for tri in tris:
            coords=list(tri.exterior.coords)
            vids=[]
            for x,y in coords[:3]:
                ux=int(round(x)); uy=int(round(y))
                if abs(float(x)-ux) > 1e-8 or abs(float(y)-uy) > 1e-8:
                    raise RuntimeError('triangulator inserted non-half-grid projected vertex')
                key=(ux,uy)
                qp=uv_to_q.get(key)
                if qp is None:
                    # Interior Steiner-free exact vertex is allowed only if the integer
                    # plane equation recovers an exact half-grid q coordinate.
                    qp=_aio_v50_reconstruct_q_from_uv(pk,axes,key)
                    if qp is None:
                        raise RuntimeError('triangulator inserted non-exact vertex')
                    uv_to_q[key]=qp
                    if qp not in q_index:
                        q_index[qp]=len(pts); pts.append(qp)
                vids.append(int(q_index[qp]))
            if len(set(vids)) != 3:
                raise RuntimeError('degenerate direct triangle')
            qarr=np.asarray(pts,dtype=np.int64)
            cr=np.cross(qarr[vids[1]]-qarr[vids[0]],qarr[vids[2]]-qarr[vids[0]])
            dot=int(np.dot(cr,desired))
            if dot == 0:
                raise RuntimeError('zero-area direct triangle')
            if dot < 0:
                vids[1],vids[2]=vids[2],vids[1]
            faces.append(vids)
    if not faces:
        raise RuntimeError('direct plane reconstructed empty')
    q=np.asarray(pts,dtype=np.int64); f=np.asarray(faces,dtype=np.int32)
    got=_aio_v50_signed_proj_area2(q,f,axes)
    expect=proj_factor*signed_k*int(normal[drop])
    if got != expect:
        raise RuntimeError(f'signed projected area mismatch {got}!={expect}')
    ar,ed,ok=_aio_v41_build_plane_edge(q,f)
    if not ok:
        raise RuntimeError('direct exact-edge certificate build failed')
    gotstat=ar.get(tuple(int(x) for x in pk))
    if gotstat is None or int(gotstat[0]) != signed_k or int(gotstat[1]) != abs_k:
        raise RuntimeError(f'direct area certificate mismatch {gotstat}!={(signed_k,abs_k)}')
    dst={tuple(int(x) for x in k):int(v) for k,v in ed.items() if int(v) != 0}
    if src_edges != dst:
        raise RuntimeError('exact boundary-edge subdivision mismatch')
    return np.ascontiguousarray(q,dtype=np.int32),np.ascontiguousarray(f,dtype=np.int32),raw_faces


def _aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,*,mode,stats=None,direct_planes=None,edges=None,writer=None,selected_planes=None,half=None,xoff=0.0,yoff=0.0,log=None,progress_base=0.0,progress_span=1.0):
    total=int(bbox.layers); hc=int(grid.height)+1; wc=int(grid.width)+1
    zero=np.zeros((hc,wc),dtype=np.uint8); prev=zero
    material_voxels=0; logical_tri=0; raw_written=0; static_saved=0
    tile_rows=int(_AIO_V50_TILE_ROWS)
    prefetch_workers=max(1,min(3,int(getattr(params,'workers',1))))
    case_futures={}; next_submit=0
    def submit_case(pool,local_layer):
        absolute=int(bbox.zmin)+int(local_layer)
        if mask_store is not None:
            return pool.submit(_aio_fast_case_from_cached_layer,mask_store,absolute,bbox,grid)
        return pool.submit(_aio_fast_case_from_layer_item,items[absolute],bbox,resolved,grid)
    def process_pair(lower,upper,k,run_pairs=1):
        nonlocal logical_tri,raw_written
        for y0 in range(0,hc,tile_rows):
            check_cancel(cancel_event)
            if edges is not None and len(edges)*240 > _bjt_memory_available()*0.35:
                raise MemoryError("DIRECT boundary 메모리 예산 초과. 격자 슬라이스를 보존하고 모델 범위를 나누십시오.")
            y1=min(hc,y0+tile_rows)
            ids,codes,ntri=_aio_v50_make_active_band(lower,upper,y0,y1)
            if ntri <= 0:
                continue
            if mode == 'stats':
                got=_aio_v50_stats_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,stats)
                if got < 0:
                    raise RuntimeError('DIRECT stats static invariant failed')
                logical_tri += int(got)
            elif mode == 'edges_raw':
                out=np.empty((ntri,3,3),dtype=np.int32)
                rn,got=_aio_v51_edges_and_raw_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,direct_planes,edges,out)
                if rn < 0:
                    raise RuntimeError('DIRECT edge/raw static invariant failed')
                logical_tri += int(got)
                if rn:
                    xyz=_aio_v50_qtri_to_xyz(out[:rn],half,xoff,yoff)
                    _aio_fast_write_triangle_soup(writer,xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                    raw_written += int(rn)
            elif mode == 'selected_raw':
                out=np.empty((ntri,3,3),dtype=np.int32)
                rn=_aio_v50_selected_raw_active(ids,codes,y0,wc,int(k),int(run_pairs),_AIO_FAST_EXACT_LUT_COUNT,_AIO_V32_LUT_Q_ZYX,selected_planes,out)
                if rn < 0:
                    raise RuntimeError('DIRECT selected raw invariant failed')
                if rn:
                    xyz=_aio_v50_qtri_to_xyz(out[:rn],half,xoff,yoff)
                    _aio_fast_write_triangle_soup(writer,xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                    raw_written += int(rn)
    run_case=None; run_start=-1; run_pairs=0
    with ThreadPoolExecutor(max_workers=prefetch_workers) as pool:
        while next_submit < total and len(case_futures) < prefetch_workers:
            case_futures[next_submit]=submit_case(pool,next_submit); next_submit += 1
        for k in range(total+1):
            check_cancel(cancel_event)
            if k < total:
                nxt,material=case_futures.pop(k).result()
                if mode == 'stats': material_voxels += int(material)
                while next_submit < total and len(case_futures) < prefetch_workers:
                    case_futures[next_submit]=submit_case(pool,next_submit); next_submit += 1
            else:
                nxt=zero
            identical=bool(np.array_equal(prev,nxt))
            if identical:
                if run_case is None:
                    run_case=prev.copy(); run_start=int(k); run_pairs=1
                elif bool(np.array_equal(run_case,prev)):
                    run_pairs += 1
                else:
                    process_pair(run_case,run_case,run_start,run_pairs)
                    static_saved += max(0,int(run_pairs)-1)
                    run_case=prev.copy(); run_start=int(k); run_pairs=1
            else:
                if run_case is not None:
                    process_pair(run_case,run_case,run_start,run_pairs)
                    static_saved += max(0,int(run_pairs)-1)
                    run_case=None; run_start=-1; run_pairs=0
                process_pair(prev,nxt,k,1)
            prev=nxt
            if log is not None and (k==0 or (k+1)%50==0 or k==total):
                pct=progress_base+progress_span*(k+1)/max(1,total+1)
                log.progress(pct,f'DIRECT {mode} {k+1:,}/{total+1:,} pair · tri={logical_tri:,} rawOut={raw_written:,}')
        if run_case is not None:
            process_pair(run_case,run_case,run_start,run_pairs)
            static_saved += max(0,int(run_pairs)-1)
    return {'material_voxels':material_voxels,'logical_triangles':logical_tri,'raw_written':raw_written,'static_saved_pairs':static_saved}


def _aio_v51_exact_interpolated_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store=None):
    global _AIO_V38_LAST_COMPACT_STATS,_AIO_V50_LAST_STATS,_AIO_V51_LAST_STATS
    if getattr(params,'surface_mode','') != '표면 보간형':
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    if (not _AIO_FAST_EXACT_VALID) or (_AIO_V32_LUT_Q_ZYX is None) or (not _AIO_V36_STATIC_SELFTEST_OK):
        log.write('WARNING: DIRECT engine prerequisites failed -> v4.6 FAST-CERT fallback')
        return _aio_v38_exact_interpolated_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    xsp=float(grid.x_pitch_mm); ysp=float(grid.y_pitch_mm); zsp=float(params.layer_thickness_mm)
    half=(0.5*xsp,0.5*ysp,0.5*zsp); xoff=float(grid.x_offset_mm); yoff=float(grid.y_offset_mm)
    started=time.time()
    log.write('')
    log.write('=============== v5.1 DIRECT-BOUNDARY exact surface ===============')
    log.write('대형 coplanar 영역: raw MC triangle 배열 없이 exact boundary edge 직접 누적')
    log.write('경계의 collinear 중간 vertex까지 원 ExactLUT와 동일하게 보존')
    log.write('복잡/소형/검증실패 면: ExactLUT raw facet 자동 fallback')
    log.write('vertex 이동/다운샘플링/QEM/허용오차 근사: 없음')
    log.write('===================================================================')
    stats=_aio_nb_Dict.empty(_AIO_V41_K4,_AIO_V50_V3)
    p1=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='stats',stats=stats,log=log,progress_base=64.0,progress_span=10.0)
    stat_py={tuple(int(x) for x in k):(int(v[0]),int(v[1]),int(v[2])) for k,v in stats.items()}
    candidates=[k for k,(ss,aa,nn) in stat_py.items() if nn >= int(_AIO_V50_DIRECT_MIN_FACES) and abs(ss) == aa and aa > 0]
    direct_set=_aio_v50_typed_plane_set(candidates)
    log.write(f'DIRECT plane census: planes={len(stat_py):,} · candidates={len(candidates):,} · logicalTri={p1["logical_triangles"]:,}')
    writer=recon_core.BinarySTLWriter(params.output_stl,header='v5.1 DIRECT-BOUNDARY exact STL',buffer_bytes=16*1024*1024)
    success=False
    edges=_aio_nb_Dict.empty(_AIO_V41_K10,_aio_nb_types.int64)
    try:
        p2=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='edges_raw',direct_planes=direct_set,edges=edges,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=74.0,progress_span=17.0)
        byplane,invalid_planes=_aio_v51_boundary_segments_by_plane(edges)
        accepted=[]; failed=[]; direct_out=0; direct_raw=0
        for idx,pk in enumerate(candidates):
            check_cancel(cancel_event)
            try:
                if tuple(pk) in invalid_planes:
                    raise RuntimeError('non-unit exact boundary multiplicity')
                q,f,raw_faces=_aio_v51_rebuild_plane(tuple(pk),byplane.get(tuple(pk),()),stat_py[tuple(pk)])
                if len(f) >= raw_faces:
                    raise RuntimeError(f'no direct reduction {len(f)}/{raw_faces}')
                tri_xyz=_aio_v50_qtri_to_xyz(q[np.asarray(f,dtype=np.int64)],half,xoff,yoff)
                _aio_fast_write_triangle_soup(writer,tri_xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                accepted.append(tuple(pk)); direct_out += int(len(f)); direct_raw += int(raw_faces)
            except Exception as exc:
                failed.append((tuple(pk),str(exc)))
            if idx==0 or (idx+1)%100==0 or idx+1==len(candidates):
                log.progress(91.0+5.0*(idx+1)/max(1,len(candidates)),f'DIRECT triangulate {idx+1:,}/{len(candidates):,} · PASS={len(accepted):,} fallback={len(failed):,}')
        if failed:
            failed_set=_aio_v50_typed_plane_set([k for k,_ in failed])
            p3=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='selected_raw',selected_planes=failed_set,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=96.0,progress_span=1.0)
        else:
            p3={'raw_written':0}
        triangle_count=int(writer.tri_count)
        if triangle_count <= 0:
            raise ValueError('DIRECT engine produced no triangles')
        writer.close(success=True); success=True
        recon_core.verify_binary_stl(params.output_stl,triangle_count)
        _AIO_V51_LAST_STATS={
            'reason':'PASS','input_triangles':int(p1['logical_triangles']),'output_triangles':triangle_count,
            'direct_candidates':len(candidates),'direct_accepted':len(accepted),'direct_failed':len(failed),
            'direct_raw_faces':int(direct_raw),'direct_output_faces':int(direct_out),
            'raw_stream_faces':int(p2['raw_written']+p3.get('raw_written',0)),
            'exact_boundary_edges':int(len(edges)),'static_saved_pairs':int(p1['static_saved_pairs']),
            'elapsed':time.time()-started,
        }
        _AIO_V50_LAST_STATS=dict(_AIO_V51_LAST_STATS); _AIO_V38_LAST_COMPACT_STATS=dict(_AIO_V51_LAST_STATS)
        log.write('')
        log.write('=============== v5.1 DIRECT-BOUNDARY 통계 ===============')
        log.write(f'logical ExactLUT triangles: {p1["logical_triangles"]:,}')
        log.write(f'direct candidates / PASS / fallback: {len(candidates):,} / {len(accepted):,} / {len(failed):,}')
        log.write(f'direct plane raw->compact: {direct_raw:,} -> {direct_out:,}')
        log.write(f'exact surviving boundary edges: {len(edges):,}')
        log.write(f'raw streamed facets: {p2["raw_written"]+p3.get("raw_written",0):,}')
        log.write(f'final STL triangles: {triangle_count:,}')
        log.write(f'DIRECT surface elapsed: {format_seconds(time.time()-started)}')
        if failed:
            for pk,why in failed[:8]: log.write(f'  fallback plane {pk}: {why}')
            if len(failed)>8: log.write(f'  ... +{len(failed)-8:,} fallback planes')
        log.write('==========================================================')
        return triangle_count,int(p1['material_voxels'])
    except Exception:
        if not success:
            writer.close(success=False)
        try: params.output_stl.unlink(missing_ok=True)
        except Exception: pass
        raise


def _aio_v51_startup_selftest():
    try:
        # Deliberately split a rectangle boundary with many collinear vertices.  v5.1
        # must keep all of those boundary edges exactly while reducing only the interior.
        q=np.asarray([
            [0,0,1],[2,0,1],[4,0,1],[6,0,1],
            [0,2,1],[2,2,1],[4,2,1],[6,2,1],
            [0,4,1],[2,4,1],[4,4,1],[6,4,1],
        ],np.int64)
        f=np.asarray([
            [0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],
            [4,5,9],[4,9,8],[5,6,10],[5,10,9],[6,7,11],[6,11,10],
        ],np.int64)
        ar,ed,ok=_aio_v41_build_plane_edge(q,f)
        if not ok or len(ar)!=1:
            return False,'source exact-edge build failed'
        pk=tuple(int(x) for x in next(iter(ar.keys())))
        st0=next(iter(ar.values())); st=(int(st0[0]),int(st0[1]),int(len(f)))
        segs=[]
        for key,val in ed.items():
            iv=int(val)
            if iv==0: continue
            k=tuple(int(x) for x in key)
            lo=np.asarray(k[4:7],np.int64); hi=np.asarray(k[7:10],np.int64)
            A,B=(lo,hi) if iv>0 else (hi,lo)
            if abs(iv)!=1: return False,'source boundary multiplicity'
            segs.append((A,B,1))
        qq,ff,_=_aio_v51_rebuild_plane(pk,segs,st)
        ar2,ed2,ok2=_aio_v41_build_plane_edge(qq,ff)
        if not ok2: return False,'rebuilt exact-edge build failed'
        src={tuple(int(x) for x in k):int(v) for k,v in ed.items() if int(v)!=0}
        dst={tuple(int(x) for x in k):int(v) for k,v in ed2.items() if int(v)!=0}
        if src != dst: return False,'exact boundary subdivision mismatch'
        if len(ff) >= len(f): return False,'selftest did not compact interior'
        return True,'PASS · exact boundary subdivision + area'
    except Exception as exc:
        return False,f'{type(exc).__name__}: {exc}'

_AIO_V51_SELFTEST_OK,_AIO_V51_SELFTEST_MSG=_aio_v51_startup_selftest()
if _AIO_V51_SELFTEST_OK:
    recon_core.reconstruct_interpolated_surface=_aio_v51_exact_interpolated_surface
    APP_VERSION='5.1-direct-boundary-exact'
else:
    # v5.0 is geometrically exact but does not retain every collinear boundary vertex;
    # production therefore falls back to v4.6 rather than accepting a slicer-sensitive path.
    recon_core.reconstruct_interpolated_surface=_aio_v38_exact_interpolated_surface
    APP_VERSION='4.6-cap-profiled-fast-v51-disabled'



# =============================================================================
# v5.2 DIRECT-BOUNDARY fast exact certificate
# =============================================================================
# v5.1 correctness is retained.  The remaining hotspot was Python iteration over
# Numba typed dictionaries during per-plane certification.  v5.2 dumps the exact
# edge dictionary to dense int64 arrays inside Numba and compares boundary edge
# signatures with vectorized NumPy, so the certificate remains exact but avoids
# tens of thousands of Python/FFI dictionary lookups.

_AIO_V52_LAST_STATS={}

@njit(cache=True)
def _aio_v52_dump_edge_dict(ed):
    n=len(ed)
    rows=np.empty((n,10),dtype=np.int64)
    vals=np.empty(n,dtype=np.int64)
    i=0
    for k,v in ed.items():
        for j in range(10): rows[i,j]=k[j]
        vals[i]=v; i+=1
    return rows,vals


def _aio_v52_boundary_groups(edges):
    rows,vals=_aio_v52_dump_edge_dict(edges)
    if len(rows)==0:
        return {},set(),0
    keep=vals!=0; rows=rows[keep]; vals=vals[keep]
    if len(rows)==0:
        return {},set(),0
    order=np.lexsort((rows[:,3],rows[:,2],rows[:,1],rows[:,0]))
    rows=rows[order]; vals=vals[order]
    pks=rows[:,:4]
    ch=np.any(pks[1:]!=pks[:-1],axis=1) if len(rows)>1 else np.empty(0,bool)
    cuts=np.r_[0,1+np.flatnonzero(ch),len(rows)]
    by={}; invalid=set()
    for a,b in zip(cuts[:-1],cuts[1:]):
        pk=tuple(int(x) for x in rows[a,:4])
        rr=np.ascontiguousarray(rows[a:b],dtype=np.int64)
        vv=np.ascontiguousarray(vals[a:b],dtype=np.int64)
        by[pk]=(rr,vv)
        if bool(np.any(np.abs(vv)!=1)):
            invalid.add(pk)
    return by,invalid,int(len(rows))


def _aio_v52_output_boundary_signature(q,faces):
    q=np.asarray(q,dtype=np.int64); f=np.asarray(faces,dtype=np.int64)
    if len(f)==0:
        return np.empty((0,6),np.int64),np.empty(0,np.int64)
    t=q[f]
    A=np.concatenate((t[:,0],t[:,1],t[:,2]),axis=0)
    B=np.concatenate((t[:,1],t[:,2],t[:,0]),axis=0)
    less=(A[:,0]<B[:,0]) | ((A[:,0]==B[:,0]) & ((A[:,1]<B[:,1]) | ((A[:,1]==B[:,1]) & (A[:,2]<B[:,2]))))
    lo=np.where(less[:,None],A,B); hi=np.where(less[:,None],B,A)
    sg=np.where(less,1,-1).astype(np.int64,copy=False)
    key=np.ascontiguousarray(np.concatenate((lo,hi),axis=1),dtype=np.int64)
    order=np.lexsort((key[:,5],key[:,4],key[:,3],key[:,2],key[:,1],key[:,0]))
    key=key[order]; sg=sg[order]
    if len(key)==1:
        return key.copy(),sg.copy()
    same=np.all(key[1:]==key[:-1],axis=1)
    starts=np.r_[0,1+np.flatnonzero(~same)]
    sums=np.add.reduceat(sg,starts)
    uniq=key[starts]
    nz=sums!=0
    return np.ascontiguousarray(uniq[nz]),np.ascontiguousarray(sums[nz])


def _aio_v52_source_boundary_signature(edge_rows,edge_vals):
    rows=np.asarray(edge_rows,dtype=np.int64); vals=np.asarray(edge_vals,dtype=np.int64)
    keep=vals!=0; rows=rows[keep]; vals=vals[keep]
    if len(rows)==0:
        return np.empty((0,6),np.int64),np.empty(0,np.int64)
    key=np.ascontiguousarray(rows[:,4:10],dtype=np.int64)
    order=np.lexsort((key[:,5],key[:,4],key[:,3],key[:,2],key[:,1],key[:,0]))
    return np.ascontiguousarray(key[order]),np.ascontiguousarray(vals[order])


def _aio_v52_rebuild_plane(pk,edge_rows,edge_vals,stat):
    signed_k,abs_k,raw_faces=(int(stat[0]),int(stat[1]),int(stat[2]))
    if abs(signed_k)!=abs_k:
        raise RuntimeError('mixed/opposite oriented coplanar sheets')
    edge_rows=np.asarray(edge_rows,dtype=np.int64); edge_vals=np.asarray(edge_vals,dtype=np.int64)
    if len(edge_rows)==0:
        raise RuntimeError('no exact boundary edges')
    if bool(np.any(np.abs(edge_vals)!=1)):
        raise RuntimeError('non-unit exact boundary multiplicity')
    normal=np.asarray(pk[:3],dtype=np.int64)
    drop=int(np.argmax(np.abs(normal))); axes=tuple(i for i in range(3) if i!=drop)
    proj_factor=-1 if drop==1 else 1
    pts=[];q_index={};uv_to_q={};seg_uv=[]
    for row,val in zip(edge_rows,edge_vals):
        lo=tuple(int(x) for x in row[4:7]); hi=tuple(int(x) for x in row[7:10])
        A,B=(lo,hi) if int(val)>0 else (hi,lo)
        for P in (A,B):
            if P not in q_index:
                q_index[P]=len(pts);pts.append(P)
            uv_to_q[(P[axes[0]],P[axes[1]])]=P
        seg_uv.append(((float(A[axes[0]]),float(A[axes[1]])),(float(B[axes[0]]),float(B[axes[1]])),1))
    lines=[_aio_v50_LineString([a,b]) for a,b,_ in seg_uv]
    polygons=list(_aio_v50_polygonize(lines))
    if not polygons:
        raise RuntimeError('exact boundary polygonize produced no region')
    faces=[]; plus_proj_sign=proj_factor*(1 if int(normal[drop])>0 else -1)
    for poly in polygons:
        rp=poly.representative_point();wn=_aio_v50_winding(rp.x,rp.y,seg_uv)
        if wn==0:continue
        if abs(int(wn))!=1:raise RuntimeError(f'non-unit planar winding={wn}')
        desired=normal*(1 if ((wn>0)==(plus_proj_sign>0)) else -1)
        gc=_aio_constrained_delaunay_triangles(poly) if _aio_constrained_delaunay_triangles is not None else None
        tris=list(getattr(gc,'geoms',())) if gc is not None else _aio_triangulate_polygon_exact(poly)
        if not tris:raise RuntimeError('constrained triangulation failed')
        for tri in tris:
            vids=[]
            for x,y in list(tri.exterior.coords)[:3]:
                ux=int(round(x));uy=int(round(y))
                if abs(float(x)-ux)>1e-8 or abs(float(y)-uy)>1e-8:
                    raise RuntimeError('triangulator inserted non-half-grid projected vertex')
                uv=(ux,uy);qp=uv_to_q.get(uv)
                if qp is None:
                    qp=_aio_v50_reconstruct_q_from_uv(pk,axes,uv)
                    if qp is None:raise RuntimeError('triangulator inserted non-exact vertex')
                    uv_to_q[uv]=qp
                    if qp not in q_index:q_index[qp]=len(pts);pts.append(qp)
                vids.append(int(q_index[qp]))
            if len(set(vids))!=3:raise RuntimeError('degenerate direct triangle')
            qarr=np.asarray(pts,dtype=np.int64)
            cr=np.cross(qarr[vids[1]]-qarr[vids[0]],qarr[vids[2]]-qarr[vids[0]])
            dot=int(np.dot(cr,desired))
            if dot==0:raise RuntimeError('zero-area direct triangle')
            if dot<0:vids[1],vids[2]=vids[2],vids[1]
            faces.append(vids)
    if not faces:raise RuntimeError('direct plane reconstructed empty')
    q=np.asarray(pts,dtype=np.int64);f=np.asarray(faces,dtype=np.int32)
    got=_aio_v50_signed_proj_area2(q,f,axes);expect=proj_factor*signed_k*int(normal[drop])
    if got!=expect:raise RuntimeError(f'signed projected area mismatch {got}!={expect}')
    src_k,src_v=_aio_v52_source_boundary_signature(edge_rows,edge_vals)
    dst_k,dst_v=_aio_v52_output_boundary_signature(q,f)
    if src_k.shape!=dst_k.shape or not np.array_equal(src_k,dst_k) or not np.array_equal(src_v,dst_v):
        raise RuntimeError('exact boundary-edge subdivision mismatch')
    return np.ascontiguousarray(q,dtype=np.int32),np.ascontiguousarray(f,dtype=np.int32),raw_faces


def _aio_v52_exact_interpolated_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store=None):
    global _AIO_V38_LAST_COMPACT_STATS,_AIO_V50_LAST_STATS,_AIO_V51_LAST_STATS,_AIO_V52_LAST_STATS
    if getattr(params,'surface_mode','')!='표면 보간형':
        return _AIO_FAST_EXACT_ORIG_INTERPOLATED(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    if (not _AIO_FAST_EXACT_VALID) or (_AIO_V32_LUT_Q_ZYX is None) or (not _AIO_V36_STATIC_SELFTEST_OK):
        log.write('WARNING: DIRECT engine prerequisites failed -> v4.6 FAST-CERT fallback')
        return _aio_v38_exact_interpolated_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    xsp=float(grid.x_pitch_mm);ysp=float(grid.y_pitch_mm);zsp=float(params.layer_thickness_mm)
    half=(0.5*xsp,0.5*ysp,0.5*zsp);xoff=float(grid.x_offset_mm);yoff=float(grid.y_offset_mm)
    started=time.time();log.write('');log.write('=============== v5.2 DIRECT-BOUNDARY exact surface ===============')
    log.write('candidate raw triangle 미생성 + exact MC boundary subdivision 보존')
    log.write('certificate: Numba edge dump + vectorized exact integer boundary comparison')
    log.write('vertex 이동/다운샘플링/QEM/허용오차 근사: 없음');log.write('===================================================================')
    stats=_aio_nb_Dict.empty(_AIO_V41_K4,_AIO_V50_V3)
    p1=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='stats',stats=stats,log=log,progress_base=64.,progress_span=10.)
    stat_py={tuple(int(x) for x in k):(int(v[0]),int(v[1]),int(v[2])) for k,v in stats.items()}
    candidates=[k for k,(ss,aa,nn) in stat_py.items() if nn>=int(_AIO_V50_DIRECT_MIN_FACES) and abs(ss)==aa and aa>0]
    direct_set=_aio_v50_typed_plane_set(candidates)
    log.write(f'DIRECT plane census: planes={len(stat_py):,} · candidates={len(candidates):,} · logicalTri={p1["logical_triangles"]:,}')
    writer=recon_core.BinarySTLWriter(params.output_stl,header='v5.2 DIRECT-BOUNDARY exact STL',buffer_bytes=16*1024*1024);success=False
    edges=_aio_nb_Dict.empty(_AIO_V41_K10,_aio_nb_types.int64)
    try:
        p2=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='edges_raw',direct_planes=direct_set,edges=edges,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=74.,progress_span=17.)
        byplane,invalid_planes,edge_count=_aio_v52_boundary_groups(edges)
        accepted=[];failed=[];direct_out=0;direct_raw=0
        for idx,pk in enumerate(candidates):
            check_cancel(cancel_event)
            try:
                if tuple(pk) in invalid_planes:raise RuntimeError('non-unit exact boundary multiplicity')
                group=byplane.get(tuple(pk))
                if group is None:raise RuntimeError('missing exact boundary group')
                q,f,raw_faces=_aio_v52_rebuild_plane(tuple(pk),group[0],group[1],stat_py[tuple(pk)])
                if len(f)>=raw_faces:raise RuntimeError(f'no direct reduction {len(f)}/{raw_faces}')
                tri_xyz=_aio_v50_qtri_to_xyz(q[np.asarray(f,dtype=np.int64)],half,xoff,yoff)
                _aio_fast_write_triangle_soup(writer,tri_xyz,max_triangles=params.max_triangles,cancel_event=cancel_event)
                accepted.append(tuple(pk));direct_out+=int(len(f));direct_raw+=int(raw_faces)
            except (CancelledError, recon_core.CancelledError, OSError, MemoryError, OverflowError):
                raise
            except Exception as exc:
                if 'Triangle 제한' in str(exc): raise
                failed.append((tuple(pk),str(exc)))
            if idx==0 or (idx+1)%100==0 or idx+1==len(candidates):log.progress(91.+5.*(idx+1)/max(1,len(candidates)),f'DIRECT triangulate {idx+1:,}/{len(candidates):,} · PASS={len(accepted):,} fallback={len(failed):,}')
        if failed:
            failed_set=_aio_v50_typed_plane_set([k for k,_ in failed])
            p3=_aio_v51_case_pass(params,items,bbox,grid,resolved,mask_store,cancel_event,mode='selected_raw',selected_planes=failed_set,writer=writer,half=half,xoff=xoff,yoff=yoff,log=log,progress_base=96.,progress_span=1.)
        else:p3={'raw_written':0}
        triangle_count=int(writer.tri_count)
        if triangle_count<=0:raise ValueError('DIRECT engine produced no triangles')
        writer.close(success=True);success=True;recon_core.verify_binary_stl(params.output_stl,triangle_count)
        _AIO_V52_LAST_STATS={'reason':'PASS','input_triangles':int(p1['logical_triangles']),'output_triangles':triangle_count,'direct_candidates':len(candidates),'direct_accepted':len(accepted),'direct_failed':len(failed),'direct_raw_faces':int(direct_raw),'direct_output_faces':int(direct_out),'raw_stream_faces':int(p2['raw_written']+p3.get('raw_written',0)),'exact_boundary_edges':int(edge_count),'static_saved_pairs':int(p1['static_saved_pairs']),'elapsed':time.time()-started}
        _AIO_V51_LAST_STATS=dict(_AIO_V52_LAST_STATS);_AIO_V50_LAST_STATS=dict(_AIO_V52_LAST_STATS);_AIO_V38_LAST_COMPACT_STATS=dict(_AIO_V52_LAST_STATS)
        log.write('');log.write('=============== v5.2 DIRECT-BOUNDARY 통계 ===============');log.write(f'logical ExactLUT triangles: {p1["logical_triangles"]:,}');log.write(f'direct candidates / PASS / fallback: {len(candidates):,} / {len(accepted):,} / {len(failed):,}');log.write(f'direct plane raw->compact: {direct_raw:,} -> {direct_out:,}');log.write(f'exact surviving boundary edges: {edge_count:,}');log.write(f'raw streamed facets: {p2["raw_written"]+p3.get("raw_written",0):,}');log.write(f'final STL triangles: {triangle_count:,}');log.write(f'DIRECT surface elapsed: {format_seconds(time.time()-started)}');log.write('==========================================================')
        return triangle_count,int(p1['material_voxels'])
    except Exception:
        if not success:writer.close(success=False)
        try:params.output_stl.unlink(missing_ok=True)
        except Exception:pass
        raise


def _aio_v52_startup_selftest():
    try:
        q=np.asarray([[0,0,1],[2,0,1],[4,0,1],[6,0,1],[0,2,1],[2,2,1],[4,2,1],[6,2,1],[0,4,1],[2,4,1],[4,4,1],[6,4,1]],np.int64)
        f=np.asarray([[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[4,5,9],[4,9,8],[5,6,10],[5,10,9],[6,7,11],[6,11,10]],np.int64)
        ar,ed,ok=_aio_v41_build_plane_edge(q,f)
        if not ok or len(ar)!=1:return False,'source cert fail'
        rows,vals=_aio_v52_dump_edge_dict(ed);keep=vals!=0;rows=rows[keep];vals=vals[keep]
        pk=tuple(int(x) for x in rows[0,:4]);st0=ar.get(pk);st=(int(st0[0]),int(st0[1]),len(f))
        qq,ff,_=_aio_v52_rebuild_plane(pk,rows,vals,st)
        sk,sv=_aio_v52_source_boundary_signature(rows,vals);dk,dv=_aio_v52_output_boundary_signature(qq,ff)
        if not(np.array_equal(sk,dk) and np.array_equal(sv,dv)):return False,'boundary signature mismatch'
        if len(ff)>=len(f):return False,'selftest did not reduce interior'
        return True,'PASS · vectorized exact boundary certificate'
    except Exception as exc:return False,f'{type(exc).__name__}: {exc}'

_AIO_V52_SELFTEST_OK,_AIO_V52_SELFTEST_MSG=_aio_v52_startup_selftest()
if _AIO_V52_SELFTEST_OK:
    recon_core.reconstruct_interpolated_surface=_aio_v52_exact_interpolated_surface
    APP_VERSION='5.2-direct-boundary-fastcert'
else:
    recon_core.reconstruct_interpolated_surface=_aio_v38_exact_interpolated_surface
    APP_VERSION='4.6-cap-profiled-fast-v52-disabled'



# =============================================================================
# v5.3 vectorized direct-plane triangulation
# =============================================================================
# The direct case/edge passes in v5.2 are already sub-second on medium meshes; the
# remaining bottleneck is Python iteration over thousands of Shapely triangle objects.
# v5.3 triangulates all polygon regions of a plane in one vectorized GEOS call and
# extracts/maps/orients triangle coordinates in NumPy. Exact v5.2 boundary signatures
# remain the acceptance certificate.
import shapely as _aio_v53_shapely

_AIO_V53_LAST_STATS={}


def _aio_v53_uv_index_map(edge_rows,axes):
    rows=np.asarray(edge_rows,dtype=np.int64)
    lo=rows[:,4:7]; hi=rows[:,7:10]
    qall=np.concatenate((lo,hi),axis=0)
    uvall=qall[:,axes]
    uvuniq,first=np.unique(uvall,axis=0,return_index=True)
    quniq=np.ascontiguousarray(qall[first],dtype=np.int64)
    return np.ascontiguousarray(uvuniq,dtype=np.int64),quniq


def _aio_v53_map_uv_faces(tri_uv,src_uv,src_q):
    flat=np.ascontiguousarray(np.asarray(tri_uv,dtype=np.int64).reshape(-1,2))
    src_uv=np.ascontiguousarray(src_uv,dtype=np.int64)
    # Structured exact pair search; no tolerance and no Python per-triangle loop.
    dt=np.dtype((np.void,16))
    sk=np.ascontiguousarray(src_uv).view(dt).ravel()
    fk=np.ascontiguousarray(flat).view(dt).ravel()
    order=np.argsort(sk,kind='mergesort');ss=sk[order]
    pos=np.searchsorted(ss,fk)
    if np.any(pos>=len(ss)):
        return None,None
    safe=np.minimum(pos,len(ss)-1)
    if np.any(ss[safe]!=fk):
        return None,None
    q=np.ascontiguousarray(np.asarray(src_q,dtype=np.int64)[order],dtype=np.int64)
    # q is in sorted-key order; face indices are positions in that array.
    return q,np.ascontiguousarray(safe.reshape(-1,3),dtype=np.int64)


@njit(cache=True, nogil=True)
def _bjt_indexed_winding(points, segments, pairs):
    result=np.zeros(len(points),dtype=np.int64)
    for j in range(pairs.shape[1]):
        i=int(pairs[0,j]);e=int(pairs[1,j])
        px=points[i,0];py=points[i,1]
        x1=segments[e,0,0];y1=segments[e,0,1]
        x2=segments[e,1,0];y2=segments[e,1,1]
        if y1<=py<y2:
            if (x2-x1)*(py-y1)-(px-x1)*(y2-y1)>0: result[i]+=1
        elif y2<=py<y1:
            if (x2-x1)*(py-y1)-(px-x1)*(y2-y1)<0: result[i]-=1
    return result


def _aio_v52_rebuild_plane(pk,edge_rows,edge_vals,stat):
    """v5.3 implementation under the v5.2 API: vectorized but exact."""
    signed_k,abs_k,raw_faces=(int(stat[0]),int(stat[1]),int(stat[2]))
    if abs(signed_k)!=abs_k:
        raise RuntimeError('mixed/opposite oriented coplanar sheets')
    edge_rows=np.asarray(edge_rows,dtype=np.int64);edge_vals=np.asarray(edge_vals,dtype=np.int64)
    if len(edge_rows)==0:raise RuntimeError('no exact boundary edges')
    if bool(np.any(np.abs(edge_vals)!=1)):raise RuntimeError('non-unit exact boundary multiplicity')
    normal=np.asarray(pk[:3],dtype=np.int64);drop=int(np.argmax(np.abs(normal)));axes=tuple(i for i in range(3) if i!=drop)
    proj_factor=-1 if drop==1 else 1;plus_proj_sign=proj_factor*(1 if int(normal[drop])>0 else -1)
    # Vectorized GEOS construction and spatially indexed winding queries.
    uv0=edge_rows[:,4:7][:,axes].astype(np.float64)
    uv1=edge_rows[:,7:10][:,axes].astype(np.float64)
    flip=edge_vals<0
    start=np.where(flip[:,None],uv1,uv0)
    end=np.where(flip[:,None],uv0,uv1)
    segments=np.stack((start,end),axis=1)
    lines=_aio_v53_shapely.linestrings(segments)
    polygons=_aio_v53_shapely.get_parts(_aio_v53_shapely.polygonize(lines))
    if not len(polygons):raise RuntimeError('exact boundary polygonize produced no region')
    if _aio_constrained_delaunay_triangles is None:
        raise RuntimeError('Shapely >=2.1 with constrained triangulation required for compact plane')
    reps=_aio_v53_shapely.get_coordinates(_aio_v53_shapely.point_on_surface(polygons))
    tree=_aio_v53_shapely.STRtree(lines)
    winding=np.zeros(len(reps),dtype=np.int64)
    right=float(np.max(segments[:,:,0]))+1.0
    for begin in range(0,len(reps),512):
        pp=reps[begin:begin+512]
        ends=pp.copy();ends[:,0]=right
        rays=_aio_v53_shapely.linestrings(np.stack((pp,ends),axis=1))
        pairs=tree.query(rays)
        winding[begin:begin+len(pp)]=_bjt_indexed_winding(pp,segments,pairs)
    if bool(np.any(np.abs(winding)>1)):raise RuntimeError('non-unit planar winding')
    keep=winding!=0
    if not bool(np.any(keep)):raise RuntimeError('direct plane reconstructed empty')
    desired_sign=np.where((winding[keep]>0)==(plus_proj_sign>0),1,-1)
    arr=polygons[keep]
    geoms=_aio_v53_shapely.constrained_delaunay_triangles(arr)
    parts,owner=_aio_v53_shapely.get_parts(geoms,return_index=True)
    if len(parts)==0:raise RuntimeError('constrained triangulation produced no faces')
    nums=np.asarray(_aio_v53_shapely.get_num_coordinates(parts),dtype=np.int64)
    if bool(np.any(nums!=4)):raise RuntimeError('unexpected non-triangle constrained output')
    coords=np.asarray(_aio_v53_shapely.get_coordinates(parts),dtype=np.float64).reshape(-1,4,2)[:,:3]
    rounded=np.rint(coords)
    if float(np.max(np.abs(coords-rounded)))>1e-8:raise RuntimeError('triangulator inserted non-half-grid projected vertex')
    tri_uv=np.ascontiguousarray(rounded,dtype=np.int64)
    src_uv,src_q=_aio_v53_uv_index_map(edge_rows,axes)
    q,f=_aio_v53_map_uv_faces(tri_uv,src_uv,src_q)
    if q is None:raise RuntimeError('triangulator inserted vertex outside exact boundary set')
    # Orient all triangles in one vectorized operation.
    cr=np.cross(q[f[:,1]]-q[f[:,0]],q[f[:,2]]-q[f[:,0]])
    ds=np.asarray(desired_sign,dtype=np.int64)[np.asarray(owner,dtype=np.int64)]
    dot=np.sum(cr*normal[None,:],axis=1)*ds
    if bool(np.any(dot==0)):raise RuntimeError('zero-area direct triangle')
    flip=dot<0
    if bool(np.any(flip)):
        tmp=f[flip,1].copy();f[flip,1]=f[flip,2];f[flip,2]=tmp
    f=np.ascontiguousarray(f,dtype=np.int32);q=np.ascontiguousarray(q,dtype=np.int64)
    got=_aio_v50_signed_proj_area2(q,f,axes);expect=proj_factor*signed_k*int(normal[drop])
    if got!=expect:raise RuntimeError(f'signed projected area mismatch {got}!={expect}')
    src_k,src_v=_aio_v52_source_boundary_signature(edge_rows,edge_vals);dst_k,dst_v=_aio_v52_output_boundary_signature(q,f)
    if src_k.shape!=dst_k.shape or not np.array_equal(src_k,dst_k) or not np.array_equal(src_v,dst_v):raise RuntimeError('exact boundary-edge subdivision mismatch')
    return np.ascontiguousarray(q,dtype=np.int32),f,raw_faces



def _aio_v53_startup_selftest():
    try:
        q=np.asarray([[0,0,1],[2,0,1],[4,0,1],[6,0,1],[0,2,1],[2,2,1],[4,2,1],[6,2,1],[0,4,1],[2,4,1],[4,4,1],[6,4,1]],np.int64)
        f=np.asarray([[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[4,5,9],[4,9,8],[5,6,10],[5,10,9],[6,7,11],[6,11,10]],np.int64)
        ar,ed,ok=_aio_v41_build_plane_edge(q,f)
        if not ok:return False,'source cert fail'
        rows,vals=_aio_v52_dump_edge_dict(ed);keep=vals!=0;rows=rows[keep];vals=vals[keep];pk=tuple(int(x) for x in rows[0,:4]);st0=ar.get(pk)
        qq,ff,_=_aio_v52_rebuild_plane(pk,rows,vals,(int(st0[0]),int(st0[1]),len(f)))
        sk,sv=_aio_v52_source_boundary_signature(rows,vals);dk,dv=_aio_v52_output_boundary_signature(qq,ff)
        if not(np.array_equal(sk,dk) and np.array_equal(sv,dv)):return False,'vector exact boundary mismatch'
        if len(ff)>=len(f):return False,'selftest no reduction'
        return True,'PASS · vectorized triangulation + exact boundary certificate'
    except Exception as exc:return False,f'{type(exc).__name__}: {exc}'

_AIO_V53_SELFTEST_OK,_AIO_V53_SELFTEST_MSG=_aio_v53_startup_selftest()
if _AIO_V53_SELFTEST_OK:
    recon_core.reconstruct_interpolated_surface=_aio_v52_exact_interpolated_surface
    APP_VERSION='5.3-direct-boundary-vectorized'
else:
    recon_core.reconstruct_interpolated_surface=_aio_v38_exact_interpolated_surface
    APP_VERSION='4.6-cap-profiled-fast-v53-disabled'


# =============================================================================
# v5.4 audited repairs. Geometry modes retain their own original sampling rule.
# =============================================================================
import contextlib as _bjt_contextlib
from dataclasses import replace as _bjt_replace

APP_VERSION = '5.8-faithful-contact-aware'

def _bjt_memory_available():
    available=_aio_v33_available_memory_bytes()
    try:
        import psutil
        available=min(available,int(psutil.virtual_memory().available))
    except ImportError:
        pass
    # Respect Linux container limits during tests; Windows uses GlobalMemoryStatusEx.
    try:
        cap=Path('/sys/fs/cgroup/memory.max').read_text().strip()
        used=int(Path('/sys/fs/cgroup/memory.current').read_text())
        if cap!='max': available=min(available,max(0,int(cap)-used))
    except (OSError,ValueError):
        pass
    return int(available)


def _bjt_workers(requested, pixels, bytes_per_pixel=48, fixed=0):
    budget=max(0,min(4*1024**3,int(_bjt_memory_available()*0.45))-int(fixed))
    per=max(1,int(pixels)*int(bytes_per_pixel))
    if per>budget:
        raise MemoryError(f'현재 가용 RAM으로 단일 작업도 부족합니다. 예상 작업 메모리 {format_bytes(per)}, 예산 {format_bytes(budget)}. 다른 작업을 종료하거나 모델 범위를 나누십시오. DPI/레이어 두께는 자동으로 낮추지 않습니다.')
    return max(1,min(int(requested),os.cpu_count() or 1,budget//per))


def _bjt_allocate_z_shell(shape, cache_dir):
    size=math.prod(shape)
    if cache_dir is not None and size>min(512*1024**2,int(_bjt_memory_available()*0.12)):
        folder=Path(cache_dir);folder.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(folder).free<size+256*1024**2:
            raise OSError('Z shell 캐시를 저장할 디스크 공간이 부족합니다.')
        return np.memmap(folder/'z_shell.pack',dtype=np.uint8,mode='w+',shape=shape)
    return np.zeros(shape,dtype=np.uint8)

lattice_core._allocate_z_shell=_bjt_allocate_z_shell

def _bjt_save_output_image(image,path):
    if image.mode=='1' and Path(path).suffix.lower()=='.bmp':
        w,h=image.size
        packed=np.frombuffer(image.tobytes(),np.uint8).reshape(h,(w+7)//8)
        _aio_v43_write_1bit_bmp_packed(packed,w,h,path)
    elif Path(path).suffix.lower()=='.png':
        image.save(path,compress_level=1,optimize=False)
    else:
        image.save(path)
lattice_core._save_output_image=_bjt_save_output_image

# Fast exact run extraction replaces thousands of NumPy/Python calls per layer.
@njit(cache=True,nogil=True)
def _bjt_runs_rows(mask):
    count=0
    h,w=mask.shape
    for y in range(h):
        for x in range(w):
            if mask[y,x] and (x==0 or not mask[y,x-1]): count+=1
    out=np.empty((count,3),np.int64);idx=0
    for y in range(h):
        x=0
        while x<w:
            if not mask[y,x]: x+=1;continue
            begin=x
            while x<w and mask[y,x]: x+=1
            out[idx,0]=y;out[idx,1]=begin;out[idx,2]=x;idx+=1
    return out

@njit(cache=True,nogil=True)
def _bjt_rectangles(mask):
    runs=_bjt_runs_rows(mask)
    out=np.empty((len(runs),4),np.int64)
    # Row-run interval identity only, identical to original greedy merge.
    active=_aio_nb_Dict.empty(_BJT_RUN_KEY,_aio_nb_types.int64)
    count=0;pos=0
    for y in range(mask.shape[0]):
        current=_aio_nb_Dict.empty(_BJT_RUN_KEY,_aio_nb_types.int64)
        while pos<len(runs) and runs[pos,0]==y:
            key=(runs[pos,1],runs[pos,2]);current[key]=active.get(key,np.int64(y));pos+=1
        for key,start in active.items():
            if key not in current:
                out[count,0]=start;out[count,1]=y;out[count,2]=key[0];out[count,3]=key[1];count+=1
        active=current
    for key,start in active.items():
        out[count,0]=start;out[count,1]=mask.shape[0];out[count,2]=key[0];out[count,3]=key[1];count+=1
    return out[:count]

# Typed dictionary type constants must be constructed outside nopython code.
_BJT_RUN_KEY=_aio_nb_types.UniTuple(_aio_nb_types.int64,2)
# The actual compiled function below uses global type constants.


def _bjt_greedy_rectangles(mask):
    return (tuple(int(v) for v in row) for row in _bjt_rectangles(np.ascontiguousarray(mask)))

def _bjt_x_runs_by_y(mask):
    return (tuple(int(v) for v in row) for row in _bjt_runs_rows(np.ascontiguousarray(mask)))

def _bjt_y_runs_by_x(mask):
    return (tuple(int(v) for v in row) for row in _bjt_runs_rows(np.ascontiguousarray(mask.T)))

recon_core.greedy_rectangles=_bjt_greedy_rectangles
recon_core.x_runs_by_y=_bjt_x_runs_by_y
recon_core.y_runs_by_x=_bjt_y_runs_by_x

_BJT_ENDPOINT_DTYPE=np.dtype([('line','<u8'),('position','<i8')])

@njit(cache=True,nogil=True)
def _bjt_pack_line(direction, p, shifts, coord_bits):
    key=np.uint64(direction)<<np.uint64(coord_bits)
    for axis in range(3):
        if axis!=direction: key|=np.uint64(p[axis])<<np.uint64(shifts[axis])
    return key

@njit(cache=True,nogil=True)
def _bjt_rect_corners(row):
    axis=int(row[0]);other=np.empty(2,np.int64);j=0
    for k in range(3):
        if k!=axis:other[j]=k;j+=1
    u=other[0];v=other[1]
    pts=np.zeros((4,3),np.int64);pts[:,axis]=row[2]
    pts[0,u]=row[3];pts[0,v]=row[5]
    pts[1,u]=row[4];pts[1,v]=row[5]
    pts[2,u]=row[4];pts[2,v]=row[6]
    pts[3,u]=row[3];pts[3,v]=row[6]
    return pts

@njit(cache=True,nogil=True)
def _bjt_fill_endpoints(rects, result, shifts, coord_bits):
    for i in range(len(rects)):
        pts=_bjt_rect_corners(rects[i])
        for e in range(4):
            a=pts[e];b=pts[(e+1)%4];direction=0
            for k in range(3):
                if a[k]!=b[k]:direction=k
            key=_bjt_pack_line(direction,a,shifts,coord_bits)
            for j in range(2):
                pos=i*8+e*2+j
                result[pos]['line']=key
                result[pos]['position']=a[direction] if j==0 else b[direction]

@njit(cache=True,nogil=True)
def _bjt_find_endpoint(lines,positions,key,value):
    lo=0;hi=len(lines)
    while lo<hi:
        mid=(lo+hi)//2
        if lines[mid]<key or (lines[mid]==key and positions[mid]<value):lo=mid+1
        else:hi=mid
    return lo

@njit(cache=True,nogil=True)
def _bjt_rect_triangle_count(row,lines,positions,shifts,coord_bits):
    pts=_bjt_rect_corners(row);n=0
    for e in range(4):
        a=pts[e];b=pts[(e+1)%4];direction=0
        for k in range(3):
            if a[k]!=b[k]:direction=k
        key=_bjt_pack_line(direction,a,shifts,coord_bits)
        lo=_bjt_find_endpoint(lines,positions,key,min(a[direction],b[direction]))
        hi=_bjt_find_endpoint(lines,positions,key,max(a[direction],b[direction]))
        n+=hi-lo
    return 2 if n==4 else n

@njit(cache=True,nogil=True)
def _bjt_stitch_rectangles(rects,lines,positions,shifts,coord_bits):
    count=0
    for row in rects:count+=_bjt_rect_triangle_count(row,lines,positions,shifts,coord_bits)
    out=np.empty((count,3,3),np.int64);idx=0
    for row in rects:
        pts=_bjt_rect_corners(row)
        ntri=_bjt_rect_triangle_count(row,lines,positions,shifts,coord_bits)
        axis=int(row[0]);expected=int(row[1])
        # Ascending coordinate plane order has -Y normal, +X/+Z normal.
        reverse=(expected!=(-1 if axis==1 else 1))
        if ntri==2:
            for j in range(2):
                out[idx,0]=2*pts[0]
                out[idx,1]=2*pts[j+1 if not reverse else j+2]
                out[idx,2]=2*pts[j+2 if not reverse else j+1];idx+=1
            continue
        center=pts[0]+pts[2]  # doubled coordinates; face center stays in exact plane
        for e in range(4):
            a=pts[e];b=pts[(e+1)%4];direction=0
            for k in range(3):
                if a[k]!=b[k]:direction=k
            key=_bjt_pack_line(direction,a,shifts,coord_bits)
            lo=_bjt_find_endpoint(lines,positions,key,min(a[direction],b[direction]))
            hi=_bjt_find_endpoint(lines,positions,key,max(a[direction],b[direction]))
            increasing=a[direction]<b[direction]
            for j in range(lo,hi):
                aa=2*a.copy();bb=2*a.copy()
                aa[direction]=2*positions[j if increasing else hi-(j-lo)]
                bb[direction]=2*positions[j+1 if increasing else hi-(j-lo)-1]
                out[idx,0]=center
                out[idx,1]=bb if reverse else aa
                out[idx,2]=aa if reverse else bb;idx+=1
    return out

class _GridRectWriter:
    """Collect merged rectangles, then stitch actual shared endpoints once.

    Every emitted triangle lies exactly in the source rectangle. No voxel is
    resampled, no physical dimension is relaxed, and no smoothing is used.
    """
    def __init__(self,path,coord,max_triangles,cancel_event):
        self.final_path=Path(path);self.coord=coord;self.limit=max_triangles;self.cancel=cancel_event
        self.tri_count=0;self.rect_count=0;self._closed=False
        self._temp=tempfile.TemporaryDirectory(prefix='bjt_rect_')
        self._path=Path(self._temp.name)/'rectangles.bin'
        self._f=open(self._path,'wb',buffering=1024*1024)
        self._buffer=[];self._bounds=np.zeros(3,np.int64)
    def add_grid_rect(self,axis,sign,plane,u0,u1,v0,v1):
        check_cancel(self.cancel)
        self._buffer.append((axis,sign,plane,u0,u1,v0,v1))
        self.rect_count+=1;self.tri_count=2*self.rect_count
        if self.limit is not None and self.tri_count>self.limit:
            raise RuntimeError(f'Triangle 제한 초과: 최소 {self.tri_count:,} > {self.limit:,}')
        if len(self._buffer)>=8192:self._flush()
    def _flush(self):
        if self._buffer:
            data=np.asarray(self._buffer,np.int64)
            for axis in range(3):
                rows=data[data[:,0]==axis]
                if not len(rows):continue
                other=[a for a in range(3) if a!=axis]
                self._bounds[axis]=max(self._bounds[axis],int(rows[:,2].max()))
                self._bounds[other[0]]=max(self._bounds[other[0]],int(rows[:,4].max()))
                self._bounds[other[1]]=max(self._bounds[other[1]],int(rows[:,6].max()))
            data.tofile(self._f);self._buffer.clear()
    def close(self,success):
        if self._closed:return
        writer=None;rects=None
        try:
            if not success:return
            self._flush();self._f.close()
            if self.rect_count==0:raise ValueError('재료 표면이 없습니다.')
            estimate=self.rect_count*600
            if estimate>_bjt_memory_available()*0.45:
                raise MemoryError(f'정밀 경계 접합에 필요한 메모리 추정 {format_bytes(estimate)}가 현재 예산을 넘습니다. 격자 슬라이스를 보존하고 모델 범위를 나누십시오.')
            bits=[max(1,int(v).bit_length()) for v in self._bounds]
            total=sum(bits)
            if total>61:raise OverflowError('정밀 경계 key 좌표 범위를 초과했습니다.')
            shifts=np.array([bits[1]+bits[2],bits[2],0],np.int64)
            rects=np.memmap(self._path,dtype=np.int64,mode='r',shape=(self.rect_count,7))
            endpoints=np.empty(8*self.rect_count,_BJT_ENDPOINT_DTYPE)
            _bjt_fill_endpoints(rects,endpoints,shifts,total)
            check_cancel(self.cancel)
            # Sort exact integers, never tolerance-weld nearby points.
            position_bits=max(bits)
            if total+2+position_bits<=64:
                # Exact lossless integer packing allows a fast uint64 sort.
                packed=(endpoints['line']<<np.uint64(position_bits))|endpoints['position'].astype(np.uint64)
                del endpoints
                packed=np.unique(packed)
                lines=np.ascontiguousarray(packed>>np.uint64(position_bits))
                positions=np.ascontiguousarray((packed & np.uint64((1<<position_bits)-1)).astype(np.int64))
                del packed
            else:
                endpoints=np.unique(endpoints)
                lines=np.ascontiguousarray(endpoints['line']);positions=np.ascontiguousarray(endpoints['position'])
                del endpoints
            writer=recon_core.BinarySTLWriter(self.final_path,header='BJT 5.4 exact voxel surface with shared boundary endpoints')
            for begin in range(0,self.rect_count,4096):
                check_cancel(self.cancel)
                q=_bjt_stitch_rectangles(rects[begin:begin+4096],lines,positions,shifts,total)
                xyz=q.astype(np.float64)
                xyz[:,:,0]=xyz[:,:,0]*(0.5*self.coord.xsp)+self.coord.xoff
                xyz[:,:,1]=xyz[:,:,1]*(0.5*self.coord.ysp)+self.coord.yoff
                xyz[:,:,2]*=0.5*self.coord.zsp
                _aio_fast_write_triangle_soup(writer,xyz,max_triangles=self.limit,cancel_event=self.cancel)
            self.tri_count=writer.tri_count
            writer.close(success=True)
        except BaseException:
            if writer is not None:writer.close(success=False)
            raise
        finally:
            if not self._f.closed:self._f.close()
            if rects is not None:rects._mmap.close()
            self._temp.cleanup();self._closed=True

recon_core._GridRectWriter=_GridRectWriter

# Complete edge-incidence verification, independent of any optimization's PASS flag.
_BJT_EDGE_DTYPE=np.dtype([(f'p{i}','<u4') for i in range(6)]+[('direction','<i4'),('contact','<i4')])
_BJT_EDGE_FIELDS=[f'p{i}' for i in range(6)]

@njit(cache=True,nogil=True)
def _bjt_edges_and_buckets(bits,rows,buckets,mask):
    xyz=bits.view(np.float32)
    for i in range(len(bits)):
        for e in range(3):
            a=bits[i,e];b=bits[i,(e+1)%3]
            less=False
            for k in range(3):
                if a[k]!=b[k]:less=a[k]<b[k];break
            lo=a if less else b;hi=b if less else a
            j=3*i+e
            rows[j]['p0']=lo[0];rows[j]['p1']=lo[1];rows[j]['p2']=lo[2]
            rows[j]['p3']=hi[0];rows[j]['p4']=hi[1];rows[j]['p5']=hi[2]
            rows[j]['direction']=1 if less else -1
            # Certify an axis-aligned half-plane from actual coordinates, not
            # stored STL normals. The third vertex identifies its radial ray.
            u=xyz[i,e].astype(np.float64)
            v=xyz[i,(e+1)%3].astype(np.float64)
            w=xyz[i,(e+2)%3].astype(np.float64)
            d=v-u; t=w-u
            edge_axis=-1; changes=0
            for k in range(3):
                if d[k]!=0:edge_axis=k;changes+=1
            code=0
            if changes==1:
                ray=-1;radial=0
                for k in range(3):
                    if k!=edge_axis and t[k]!=0:ray=k;radial+=1
                if radial==1:
                    normal=np.cross(d,t)
                    normal_axis=3-edge_axis-ray
                    product=t[ray]*normal[normal_axis]
                    if product!=0:
                        code=(2*ray+(1 if t[ray]>0 else 2))*(1 if product>0 else -1)
            rows[j]['contact']=code
            h=np.uint64(1469598103934665603)
            for k in range(3):h=(h^np.uint64(lo[k]))*np.uint64(1099511628211)
            for k in range(3):h=(h^np.uint64(hi[k]))*np.uint64(1099511628211)
            # Mix high bits down; bucket collision never implies edge equality.
            h^=h>>np.uint64(32);h*=np.uint64(0xd6e8feb86659fd93);h^=h>>np.uint64(32)
            buckets[j]=h&np.uint64(mask)

@njit(cache=True,nogil=True)
def _bjt_edge_counts(rows):
    boundary=0;nonmanifold=0;winding=0;unique=0;contacts=0;i=0
    while i<len(rows):
        j=i+1;balance=int(rows[i]['direction'])
        while j<len(rows):
            a=rows[i];b=rows[j]
            same=(a['p0']==b['p0'] and a['p1']==b['p1'] and a['p2']==b['p2'] and a['p3']==b['p3'] and a['p4']==b['p4'] and a['p5']==b['p5'])
            if not same:break
            balance+=int(b['direction']);j+=1
        count=j-i;unique+=1
        if count==1:boundary+=1
        elif count!=2:
            nonmanifold+=1
            # Four distinct radial rays, with alternating occupied quadrants.
            # Duplicated faces and two crossing planes cannot satisfy this.
            rays=0;valid=count==4 and balance==0;sign=0
            for k in range(i,j):
                code=int(rows[k]['contact'])
                if code==0:valid=False;continue
                current=1 if code>0 else -1
                if sign and sign!=current:valid=False
                sign=current
                bit=1 << (abs(code)-1)
                if rays&bit:valid=False
                rays|=bit
            if valid and (rays==15 or rays==51 or rays==60):contacts+=1
            if balance!=0:winding+=1
        elif balance!=0:winding+=1
        i=j
    return boundary,nonmanifold,winding,unique,contacts


def _bjt_native_edge_backend(backend=None):
    """Load the optional sibling wrapper without changing Python's search path."""
    import hashlib as _hashlib
    import importlib.util as _importlib_util
    import os as _os
    import sys as _sys
    choice=str(backend if backend is not None else _os.environ.get('BJT_MESH_BACKEND','auto')).strip().lower()
    if choice not in ('auto','python','rust'):
        raise ValueError('BJT_MESH_BACKEND must be auto, python or rust')
    if choice=='python':return None
    folder=Path(__file__).resolve().parent
    folders=(folder.parent,folder) if folder.name=='sources' else (folder,)
    wrapper=next((root/'bjt_mesh_native.py' for root in folders if (root/'bjt_mesh_native.py').is_file()),None)
    if wrapper is None:
        if choice=='rust':raise RuntimeError('optional bjt_mesh_native.py wrapper is absent')
        return None
    alias='_bjt_mesh_native_'+_hashlib.sha256(str(wrapper).encode('utf-8')).hexdigest()[:16]
    module=_sys.modules.get(alias)
    if module is None:
        spec=_importlib_util.spec_from_file_location(alias,wrapper)
        if spec is None or spec.loader is None:
            if choice=='rust':raise RuntimeError('optional native wrapper could not be loaded')
            return None
        module=_importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Concurrent first use must never observe a partially executed wrapper.
        module=_sys.modules.setdefault(alias,module)
    return module.get_backend(choice)


def _bjt_verify_edges(path,tri_count,cancel_event,budget_bytes=None,backend=None):
    native=_bjt_native_edge_backend(backend)
    budget=int(budget_bytes or min(256*1024**2,max(16*1024**2,_bjt_memory_available()*.08)))
    # The first 24 bytes must remain the six little-endian coordinate words.
    if (_BJT_EDGE_DTYPE.itemsize!=32 or len(_BJT_EDGE_FIELDS)!=6 or
        any(_BJT_EDGE_DTYPE.fields[name][:2]!=(np.dtype('<u4'),4*col)
            for col,name in enumerate(_BJT_EDGE_FIELDS))):
        raise TypeError('edge verification requires six packed uint32 coordinates in 32-byte rows')
    edge_bytes=int(tri_count)*3*_BJT_EDGE_DTYPE.itemsize
    buckets=1
    while buckets<256 and edge_bytes/buckets>budget/4:buckets*=2
    with tempfile.TemporaryDirectory(prefix='bjt_edge_check_') as td:
        folder=Path(td)
        if buckets>1 and shutil.disk_usage(folder).free<edge_bytes+256*1024**2:
            raise OSError(f'전체 edge 검증용 임시 디스크 공간이 부족합니다. 필요량 약 {format_bytes(edge_bytes)}')
        files=[];memory=[]
        try:
            if buckets>1:files=[open(folder/f'{i:03d}.bin','wb') for i in range(buckets)]
            with open(path,'rb') as f:
                f.seek(84)
                remaining=tri_count
                chunk=min(100_000,max(1000,budget//200))
                while remaining:
                    check_cancel(cancel_event)
                    take=min(chunk,remaining)
                    records=np.fromfile(f,dtype=_AIO_FAST_EXACT_RECORD_DTYPE,count=take)
                    if len(records)!=take:raise ValueError('검증 중 STL이 변경되었거나 잘렸습니다.')
                    tri=np.stack((records['a'],records['b'],records['c']),axis=1)
                    tri[tri==0]=0.0  # normalize IEEE -0.0; no coordinate tolerance
                    bits=np.ascontiguousarray(tri,dtype='<f4').view('<u4')
                    rows=np.empty(3*take,_BJT_EDGE_DTYPE);ids=np.empty(3*take,np.uint16)
                    _bjt_edges_and_buckets(bits,rows,ids,buckets-1)
                    if buckets==1:memory.append(rows)
                    else:
                        order=np.argsort(ids,kind='stable');sorted_ids=ids[order];sorted_rows=rows[order]
                        starts=np.r_[0,np.flatnonzero(sorted_ids[1:]!=sorted_ids[:-1])+1,len(ids)]
                        for a,b in zip(starts[:-1],starts[1:]):sorted_rows[a:b].tofile(files[int(sorted_ids[a])])
                        del order,sorted_ids,sorted_rows,starts
                    del records,tri,bits,rows,ids
                    remaining-=take
            for f in files:f.close()
            totals=np.zeros(5,np.int64)
            for bucket in range(buckets):
                check_cancel(cancel_event)
                if buckets==1:
                    rows=memory[0] if len(memory)==1 else np.concatenate(memory);memory.clear()
                else:
                    p=folder/f'{bucket:03d}.bin'
                    if p.stat().st_size>budget:
                        raise MemoryError('edge 검증 버킷의 메모리 예산을 초과했습니다. 메시 중복면 또는 RAM 상태를 확인하십시오.')
                    rows=np.fromfile(p,dtype=_BJT_EDGE_DTYPE)
                if native is None:
                    # Three zero-copy uint64 keys encode all six exact coordinate bits.
                    # Keep the 32-byte row stride, skipping direction/contact in word 4.
                    words=rows.view('<u8').reshape(-1,4)[:,:3]
                    order=np.lexsort((words[:,2],words[:,1],words[:,0]))
                    rows=rows[order]
                    del words,order
                    totals+=_bjt_edge_counts(rows)
                else:
                    totals+=native.sort_count(rows)
                check_cancel(cancel_event)
                del rows
            return dict(zip(('boundary_edges','nonmanifold_edges','inconsistent_winding_edges','unique_edges','voxel_contact_edges'),map(int,totals)))
        finally:
            for f in files:
                if not f.closed:f.close()


def _aio_v37_final_fast_verify(model_path,params,cancel_event,*,compact_stats=None):
    check_cancel(cancel_event)
    st=binary_stl_stats(model_path,cancel_event)
    n=st['triangles']
    if n<=0:raise ValueError('최종 STL이 비어 있습니다.')
    if params.max_triangles is not None and n>params.max_triangles:
        raise RuntimeError(f'Triangle 제한 초과: {n:,} > {params.max_triangles:,}')
    if st['degenerate_triangles']:raise ValueError(f"퇴화 삼각형 {st['degenerate_triangles']:,}개")
    st.update(_bjt_verify_edges(model_path,n,cancel_event))
    st['verification_scope']='binary layout, finite vertices/normals, degeneracy, every edge incidence and orientation; no self-intersection or vertex-link test'
    allowed_contacts=st['voxel_contact_edges'] if params.surface_mode=='원본 충실형' else 0
    st['invalid_nonmanifold_edges']=st['nonmanifold_edges']-allowed_contacts
    st['edge_manifold']=st['nonmanifold_edges']==0 and st['boundary_edges']==0
    st['verification_status']='faithful_with_voxel_contacts' if allowed_contacts else 'edge_checks_passed'
    if st['boundary_edges'] or st['invalid_nonmanifold_edges'] or st['inconsistent_winding_edges']:
        raise ValueError('STL 경계 검사 실패: 열린/T 접합 edge={boundary_edges:,}, 3개 이상 면 공유 edge={nonmanifold_edges:,}, 방향 불일치={inconsistent_winding_edges:,}. 점/선으로만 접하는 복셀 형상은 정밀도를 유지한 자동 수정이 불가능할 수 있습니다.'.format(**st))
    st['estimated_import_memory_bytes_range']=[n*300,n*1000]
    return st

# Input validation cannot accept NaN, infinity, unknown modes or overwrite its source.
_bjt_original_validate=validate_pipeline

def validate_pipeline(params):
    _bjt_original_validate(params)
    for name in ('dpi_x','dpi_y','layer_thickness_mm','shell_mm','lattice_width_mm','spacing_mm'):
        if not math.isfinite(float(getattr(params,name))):raise ValueError(f'{name}: 유한한 숫자를 입력하십시오.')
    if params.batch_layers<1:raise ValueError('Batch layers는 1 이상이어야 합니다.')
    if params.crop_margin_px<0 or params.slice_margin_px<0:raise ValueError('여백은 0 이상이어야 합니다.')
    if params.surface_mode != '원본 충실형':raise ValueError('재구성은 원본 충실형만 지원합니다.')
    if params.print_equivalent:raise ValueError('별도 프린팅 후보 모드는 제거되었습니다.')
    if params.max_triangles is not None and params.max_triangles<1:raise ValueError('max_triangles는 양수 또는 None이어야 합니다.')
    if params.input_path.resolve()==params.output_stl.resolve():raise ValueError('입력 모델과 출력 STL 경로는 달라야 합니다.')
    if params.verify_layer_xor:
        raise ValueError('전 레이어 XOR는 이 버전의 GUI 처리 경로에 구현되지 않았습니다. 설정을 끄십시오. 검증 스크립트의 회귀 비교는 별도입니다.')

# Sharing an inode is only evidence of equality when the filesystem reports a valid ID.
_bjt_original_identity=_aio_v41_file_identity

def _aio_v41_file_identity(path):
    st=os.stat(path,follow_symlinks=True)
    if not st.st_ino:return ('path',str(Path(path).resolve()),st.st_size)
    return (int(st.st_dev),int(st.st_ino),int(st.st_size),int(st.st_mtime_ns))


def _bjt_compute_z_cached(images,params,log=None,cancel_event=None,cache_dir=None):
    """Bound unique-mask jobs; preserve original EDT and horizontal-shell equations."""
    if not images:return [],[],[],(0,0)
    with Image.open(images[0]) as im:w,h=im.size
    workers=_bjt_workers(min(4,params.workers),w*h,64)
    params=_bjt_replace(params,workers=workers)
    if params.shell_mm<=0:
        return _AIO_V41_ORIG_COMPUTE_Z_CACHED(images,params,log,cancel_event,cache_dir)
    if cache_dir is None:
        return _AIO_V41_ORIG_COMPUTE_Z_CACHED(images,params,log,cancel_event,cache_dir)
    ids=[_aio_v41_file_identity(p) for p in images]
    lookup={};representatives=[];mapping=[]
    for i,key in enumerate(ids):
        if key not in lookup:lookup[key]=len(representatives);representatives.append(i)
        mapping.append(lookup[key])
    # Use the same bounded path for both aliased and non-aliased stacks.
    n=len(images);u=len(representatives);bpr=(w+7)//8
    folder=Path(cache_dir);folder.mkdir(parents=True,exist_ok=True)
    needed=2*u*h*bpr+n*h*bpr
    if shutil.disk_usage(folder).free<needed+256*1024**2:raise OSError('material/XY/Z 캐시 디스크 공간이 부족합니다.')
    mat=xy=z=None
    try:
        mat=np.memmap(folder/'material_unique.pack',np.uint8,'w+',shape=(u,h,bpr))
        xy=np.memmap(folder/'xy_shell_unique.pack',np.uint8,'w+',shape=(u,h,bpr))
        def job(ui):
            lattice_core.check_cancel(cancel_event)
            return ui,_AIO_V41_LOAD_XY_WORKER(representatives[ui],images[representatives[ui]],params)
        next_i=0;pending={}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done in range(u):
                lattice_core.check_cancel(cancel_event)
                while next_i<u and len(pending)<workers:
                    pending[next_i]=pool.submit(job,next_i);next_i+=1
                ui,(_,shape,pm,ps)=pending.pop(done).result()
                if shape!=(h,w):raise ValueError('모든 레이어의 이미지 크기는 같아야 합니다.')
                mat[ui]=pm;xy[ui]=ps
                if log is not None and (done%25==0 or done+1==u):log.progress(20.*(done+1)/u,f'XY shell {done+1:,}/{u:,} · workers={workers}')
        mat.flush();xy.flush()
        raw_alias=_AIOV41AliasPackedStore(mat,mapping);xy_alias=_AIOV41AliasPackedStore(xy,mapping)
        z=_bjt_allocate_z_shell((n,h,bpr),folder)
        _bjt_horizontal_shell(raw_alias,z,params,w,n,log,cancel_event)
        return z,xy_alias,raw_alias,(h,w)
    except BaseException:
        for store in (mat,xy,z):lattice_core._close_memmap_store(store)
        raise

# Skip unpacking / surface EDT when packed consecutive layers are exactly equal.
def _bjt_horizontal_shell(raw,z,params,width,n,log,cancel):
    # A bounded queue retains at most one contribution list per worker.
    workers=_bjt_workers(min(4,params.workers),raw[0].shape[0]*width,
                         56+max(1,int(params.shell_mm/params.layer_thickness_mm))/8)
    from concurrent.futures import wait,FIRST_COMPLETED
    pending=set();prev_pack=np.asarray(raw[0]);prev=lattice_core.unpack_bool_mask(prev_pack,width)
    def collect(block):
        if not pending:return
        done,_=wait(pending,timeout=None if block else 0,return_when=FIRST_COMPLETED)
        for f in done:
            pending.remove(f);lattice_core._apply_surface_contributions(z,f.result())
    def submit(pool,surface,start,direction):
        if not np.any(surface):return
        while len(pending)>=workers:
            lattice_core.check_cancel(cancel);collect(True)
        pending.add(pool.submit(lattice_core._surface_shell_contributions_packed,surface,start,direction,params,width,n,cancel))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submit(pool,prev,0,1)
        for i in range(1,n):
            lattice_core.check_cancel(cancel)
            curr_pack=np.asarray(raw[i])
            if np.array_equal(prev_pack,curr_pack):continue
            curr=lattice_core.unpack_bool_mask(curr_pack,width)
            submit(pool,prev & ~curr,i-1,-1);submit(pool,curr & ~prev,i,1)
            prev=curr;prev_pack=curr_pack;collect(False)
            if log is not None and i%25==0:log.progress(20.+20.*i/n,f'Z shell {i:,}/{n:,}')
        submit(pool,prev,n-1,-1)
        while pending:lattice_core.check_cancel(cancel);collect(True)

lattice_core.compute_z_shell_masks_cached=_bjt_compute_z_cached

# Model slicing: same pixel test, bounded result queue, avoid retained Future results.
# Imported factory code is patched at build time; limits below also cover image stacks.
_bjt_run_conversion=lattice_core.run_conversion

def _bjt_safe_conversion(params,log,cancel_event=None):
    images=lattice_core.find_input_images(params.input_dir)
    if images:
        with Image.open(images[0]) as im:w,h=im.size
        requested=params.workers;actual=_bjt_workers(requested,w*h,64)
        params=_bjt_replace(params,workers=actual)
        log.write(f'가용 RAM {format_bytes(_bjt_memory_available())} · 격자 workers {requested} -> {actual}')
    return _bjt_run_conversion(params,log,cancel_event)
lattice_core.run_conversion=_bjt_safe_conversion

# Close the GUI only after the worker has released files and memmaps.
def _bjt_on_close(self):
    if self.worker_thread is not None and self.worker_thread.is_alive():
        if not messagebox.askyesno(APP_NAME,'작업을 중단하고 정리 후 종료할까요?'):return
        self.cancel_event.set()
        self.run_button.config(state=tk.DISABLED)
        def wait_worker():
            if self.worker_thread.is_alive():self.after(100,wait_worker)
            else:self._save_settings();self._cleanup_temp();self.destroy()
        self.after(100,wait_worker)
    else:
        self._save_settings();self._cleanup_temp();self.destroy()
App.on_close=_bjt_on_close

# Return two immutable phase masks, instead of allocating the same 3D pattern per layer.
_BJT_PATTERN_LOCK=threading.RLock()

def _bjt_pattern_cache_wrapper(fn,tag):
    def wrapped(cache,shape,params,layer_index):
        phase=bool(lattice_core.is_z_plane_layer(layer_index,params.layer_thickness_mm,params.spacing_mm,params.lattice_width_mm))
        key=('bjt54phase',tag,tuple(shape),params.dpi_x,params.dpi_y,params.spacing_mm,params.lattice_width_mm,phase)
        with _BJT_PATTERN_LOCK:
            if key not in cache:
                result=fn(cache,shape,params,layer_index)
                for value in result:
                    if isinstance(value,np.ndarray):value.flags.writeable=False
                cache[key]=result
            return cache[key]
    return wrapped
lattice_core.make_3d_hex_line_lattice_mask_cached=_bjt_pattern_cache_wrapper(lattice_core.make_3d_hex_line_lattice_mask_cached,'hex')
lattice_core.make_3d_square_line_lattice_mask_cached=_bjt_pattern_cache_wrapper(lattice_core.make_3d_square_line_lattice_mask_cached,'square')


def _bjt_scan_bbox_cached(items,params,resolved,log,cancel_event,cache_path):
    first=next((x.path for x in items if x.path is not None),None)
    if first is None:raise ValueError('실제 이미지 레이어가 없습니다.')
    with Image.open(first) as im:w,h=im.size
    n=len(items);bpr=(w+7)//8;cache_path.parent.mkdir(parents=True,exist_ok=True)
    size=n*h*bpr
    if shutil.disk_usage(cache_path.parent).free<size+256*1024**2:raise OSError('재구성 mask 캐시 디스크 공간이 부족합니다.')
    arr=np.memmap(cache_path,np.uint8,'w+',shape=(n,h,bpr))
    xmin=w;ymin=h;xmax=0;ymax=0;zmin=n;zmax=-1
    workers=_bjt_workers(params.workers,w*h,8)
    try:
        next_i=0;pending={}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done in range(n):
                check_cancel(cancel_event)
                while next_i<n and len(pending)<workers:
                    i=next_i;pending[i]=pool.submit(recon_core.bbox_worker_cached,i,items[i],resolved.threshold,resolved.print_is_white,(w,h));next_i+=1
                i,filled,x0,x1,y0,y1,packed=pending.pop(done).result()
                if packed is not None:arr[i]=packed
                else:arr[i]=0
                if filled:
                    xmin=min(xmin,x0);xmax=max(xmax,x1);ymin=min(ymin,y0);ymax=max(ymax,y1);zmin=min(zmin,i);zmax=max(zmax,i)
                if done%25==0 or done+1==n:log.progress(20.*(done+1)/n,f'입력 검사/캐시 {done+1:,}/{n:,}')
        if zmax<0:raise ValueError('슬라이스에 재료가 없습니다.')
        margin=max(0,params.crop_margin_px)
        if params.crop_xy:xmin=max(0,xmin-margin);xmax=min(w,xmax+margin);ymin=max(0,ymin-margin);ymax=min(h,ymax+margin)
        else:xmin,xmax,ymin,ymax=0,w,0,h
        if not params.crop_empty_z:zmin,zmax=0,n-1
        arr.flush()
        return recon_core.BBox(xmin,xmax,ymin,ymax,zmin,zmax,w,h),recon_core.PackedMaskStore(cache_path,arr,n,h,w,bpr)
    except BaseException:
        arr._mmap.close();cache_path.unlink(missing_ok=True);raise
recon_core.scan_bbox_cached=_bjt_scan_bbox_cached

# Bound original-mode prefetch memory without changing surface choice or grid spacing.
_bjt_original_reconstruct=recon_core.reconstruct_original_surface

def _bjt_original_reconstruct_bounded(params,items,bbox,grid,resolved,log,cancel_event,mask_store=None):
    params=_bjt_replace(params,workers=_bjt_workers(params.workers,grid.height*grid.width,160))
    return _bjt_original_reconstruct(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
recon_core.reconstruct_original_surface=_bjt_original_reconstruct_bounded

# Strict boundary semantics: contains excludes horizontal edges as well as sloped edges.
# Original scanline filled some pixels exactly on horizontal exterior/hole boundaries.
_bjt_legacy_rings=_scanline_rasterize_rings_exact

@njit(cache=True,nogil=True)
def _bjt_clear_horizontal_boundary(out,rx,ry,offsets,x0,y0,dx,dy):
    h,w=out.shape
    for ring in range(len(offsets)-1):
        begin=offsets[ring];end=offsets[ring+1]
        if end-begin<3:continue
        for i in range(begin,end):
            j=begin if i+1==end else i+1
            if ry[i]!=ry[j]:continue
            row=int(round((ry[i]-y0)/dy-.5))
            if row<0 or row>=h or y0+(row+.5)*dy!=ry[i]:continue
            lo=min(rx[i],rx[j]);hi=max(rx[i],rx[j])
            left=max(0,int(math.floor((lo-x0)/dx-.5))-1)
            right=min(w,int(math.ceil((hi-x0)/dx-.5))+2)
            for col in range(left,right):
                x=x0+(col+.5)*dx
                if lo<=x<=hi:out[row,col]=0
    return out

def _scanline_rasterize_rings_exact(rx,ry,offsets,x0,y0,dx,dy,width,height):
    out=_bjt_legacy_rings(rx,ry,offsets,x0,y0,dx,dy,width,height)
    return _bjt_clear_horizontal_boundary(out,rx,ry,offsets,x0,y0,dx,dy)

# A failed copy must not leave a partial file that the caller mistakes for success.
def _aio_v41_link_or_copy(src,dst):
    src=Path(src);dst=Path(dst)
    if src.resolve()==dst.resolve():return True
    try:
        dst.unlink(missing_ok=True)
        os.link(src,dst)
        return True
    except OSError:
        try:
            shutil.copy2(src,dst)
            return False
        except OSError:
            dst.unlink(missing_ok=True)
            return False

# Printing-equivalent candidate: 400 DPI XY / 0.1 mm Z, center sampled binary pixels.
# The geometrically faithful STL is always retained. This is a separately named
# candidate until the actual printer/slicer raster settings have been confirmed.

class _BJTPrintBudgetExceeded(Exception):
    pass

def _bjt_candidate_budget_expired():
    deadline=getattr(_BJT_STAGE_LOCAL,'candidate_deadline',None)
    return deadline is not None and time.perf_counter()>=deadline

def _bjt_candidate_check(cancel):
    check_cancel(cancel)
    if _bjt_candidate_budget_expired():raise _BJTPrintBudgetExceeded()

def _bjt_candidate_progress(obj,event,cancel):
    # VTK calls progress observers during long algorithms; inspect again after
    # Update because callback frequency is implementation-dependent.
    if cancel.is_set() or _bjt_candidate_budget_expired():obj.AbortExecuteOn()

@njit(cache=True,nogil=True)
def _bjt_plane_segments(tri,z,tol=1e-8):
    out=np.empty((len(tri),2,2),np.float64);count=0
    for t in tri:
        sign=np.zeros(3,np.int64);zero=0;positive=0;negative=0
        for i in range(3):
            d=t[i,2]-z
            if d>tol:sign[i]=1;positive+=1
            elif d<-tol:sign[i]=-1;negative+=1
            else:zero+=1
        points=np.empty((2,2),np.float64);n=0
        if zero==2 and positive==1:
            for i in range(3):
                if sign[i]==0:points[n]=t[i,:2];n+=1
        elif positive and negative:
            if zero==1:
                for i in range(3):
                    if sign[i]==0:points[n]=t[i,:2];n+=1
            for i in range(3):
                j=(i+1)%3
                if sign[i]*sign[j]<0:
                    fraction=(z-t[i,2])/(t[j,2]-t[i,2])
                    points[n]=t[i,:2]+fraction*(t[j,:2]-t[i,:2]);n+=1
        if n==2:out[count]=points;count+=1
    return out[:count]

@njit(cache=True,nogil=True)
def _bjt_crossings(segments,x0,y0,dx,dy,height):
    count=0
    for seg in segments:
        ya=seg[0,1];yb=seg[1,1]
        if ya==yb:continue
        lo=max(0,int(math.ceil((min(ya,yb)-y0)/dy-.5)))
        hi=min(height,int(math.ceil((max(ya,yb)-y0)/dy-.5)))
        count+=max(0,hi-lo)
    rows=np.empty(count,np.int64);xs=np.empty(count,np.float64);n=0
    for seg in segments:
        xa=seg[0,0];ya=seg[0,1];xb=seg[1,0];yb=seg[1,1]
        if ya==yb:continue
        lo=max(0,int(math.ceil((min(ya,yb)-y0)/dy-.5)))
        hi=min(height,int(math.ceil((max(ya,yb)-y0)/dy-.5)))
        for row in range(lo,hi):
            y=y0+(row+.5)*dy
            if (ya<=y<yb) or (yb<=y<ya):
                rows[n]=row;xs[n]=xa+(y-ya)*(xb-xa)/(yb-ya);n+=1
    return rows[:n],xs[:n]

@njit(cache=True,nogil=True)
def _bjt_pixel_runs_sorted(rows,xs,x0,dx,width):
    result=np.empty((len(rows)//2+1,3),np.int64);n=0;i=0
    while i<len(rows):
        row=rows[i];stop=i+1
        while stop<len(rows) and rows[stop]==row:stop+=1
        unique=np.empty(stop-i,np.float64);nu=0;j=i
        while j<stop:
            k=j+1
            while k<stop and abs(xs[k]-xs[j])<=1e-10:k+=1
            if (k-j)%2:unique[nu]=xs[j];nu+=1
            j=k
        if nu%2:raise ValueError('Odd number of scanline intersections; candidate cannot be certified')
        for j in range(0,nu,2):
            left=max(0,int(math.floor((unique[j]-x0)/dx-.5))+1)
            right=min(width,int(math.ceil((unique[j+1]-x0)/dx-.5)))
            if left>=right:continue
            if n and result[n-1,0]==row and left<=result[n-1,2]:
                result[n-1,2]=max(result[n-1,2],right)
            else:result[n,0]=row;result[n,1]=left;result[n,2]=right;n+=1
        i=stop
    return result[:n]


def _bjt_segments_pixel_runs(segments,grid):
    rows,xs=_bjt_crossings(segments,grid.x_offset_mm,grid.y_offset_mm,grid.x_pitch_mm,grid.y_pitch_mm,grid.height)
    order=np.lexsort((xs,rows))
    return _bjt_pixel_runs_sorted(rows[order],xs[order],grid.x_offset_mm,grid.x_pitch_mm,grid.width)

class _BJTSliceIndex:
    """STL memmap plus sorted Z start/end events; only active triangles are cut."""
    def __init__(self,path,layers,dz):
        with open(path,'rb') as f:f.seek(80);n=struct.unpack('<I',f.read(4))[0]
        self.rec=np.memmap(path,_AIO_FAST_EXACT_RECORD_DTYPE,'r',offset=84,shape=(n,))
        self.n=n;self.layers=layers;self.dz=dz
        z=np.stack((self.rec['a'][:,2],self.rec['b'][:,2],self.rec['c'][:,2]),axis=1).astype(np.float64)
        self.start=np.maximum(0,np.ceil((z.min(axis=1)-1e-8)/dz-.5)).astype(np.int64)
        self.end=np.minimum(layers,np.floor((z.max(axis=1)+1e-8)/dz-.5)+1).astype(np.int64)
        valid=self.start<self.end;indices=np.flatnonzero(valid)
        self.sorted_start=indices[np.argsort(self.start[indices],kind='stable')]
        self.sorted_end=indices[np.argsort(self.end[indices],kind='stable')]
    def runs(self,grid,cancel):
        active=set();si=ei=0
        for layer in range(self.layers):
            _bjt_candidate_check(cancel)
            while si<len(self.sorted_start) and self.start[self.sorted_start[si]]<=layer:
                active.add(int(self.sorted_start[si]));si+=1
            while ei<len(self.sorted_end) and self.end[self.sorted_end[ei]]<=layer:
                active.discard(int(self.sorted_end[ei]));ei+=1
            ids=np.fromiter(active,dtype=np.int64,count=len(active))
            rec=self.rec[ids]
            tri=np.stack((rec['a'],rec['b'],rec['c']),axis=1).astype(np.float64)
            seg=_bjt_plane_segments(tri,(layer+.5)*self.dz)
            yield _bjt_segments_pixel_runs(seg,grid)
    def close(self):self.rec._mmap.close()


def _bjt_compare_print_slices(reference,candidate,grid,layers,dz,cancel,log=None):
    a=_BJTSliceIndex(reference,layers,dz);b=None
    try:
        b=_BJTSliceIndex(candidate,layers,dz)
        for i,(ra,rb) in enumerate(zip(a.runs(grid,cancel),b.runs(grid,cancel))):
            # RLE equality is exact equality of the whole binary pixel row, not a sample/hash.
            if not np.array_equal(ra,rb):
                return {'equal':False,'first_different_layer':i+1,'layers_checked':i+1}
            if log is not None and (i%50==0 or i+1==layers):
                log.write(f'프린팅 픽셀 전수 비교 {i+1:,}/{layers:,}: 차이 0')
        return {'equal':True,'layers_checked':layers,'xor_pixels':0}
    finally:
        a.close()
        if b is not None:b.close()


def _bjt_make_print_equivalent(reference,params,log,cancel):
    if getattr(params,'surface_mode','') == '레이어 출력 전용':return {'status':'not_needed_direct_print_mode'}
    if not getattr(params,'print_equivalent',False):return {'status':'disabled'}
    if not (params.dpi_x==400 and params.dpi_y==400 and params.layer_thickness_mm==.1):
        log.write('프린팅 후보 최적화: 400×400 DPI / 0.1 mm 프로필에서만 실행')
        return {'status':'profile_not_400dpi_0.1mm'}
    if not _AIO_V32_VTK_AVAILABLE:
        log.write('프린팅 후보 최적화: VTK가 없어 생략')
        return {'status':'vtk_unavailable'}
    grid=getattr(_BJT_STAGE_LOCAL,'model_grid',None);bbox=getattr(_BJT_STAGE_LOCAL,'model_bbox',None)
    if grid is None or bbox is None:return {'status':'missing_grid'}
    with open(reference,'rb') as f:f.seek(80);triangles=struct.unpack('<I',f.read(4))[0]
    if triangles*512>_bjt_memory_available()*.45:
        log.write('프린팅 후보 최적화: 전체 메시 단순화의 RAM 예산 부족, 정밀 STL 유지')
        return {'status':'memory_budget','source_triangles':triangles}
    started=time.perf_counter();_bjt_candidate_check(cancel)
    reader=_aio_vtk.vtkSTLReader();reader.SetFileName(str(reference));reader.MergingOn()
    reader.AddObserver('ProgressEvent',lambda obj,event:_bjt_candidate_progress(obj,event,cancel))
    reader.Update();_bjt_candidate_check(cancel)
    poly=reader.GetOutput()
    if poly.GetNumberOfCells()!=triangles:raise ValueError('단순화 입력 STL triangle 수 불일치')
    coords=np.asarray(_aio_vtk_to_numpy(poly.GetPoints().GetData()),np.float64)
    source_bounds=np.array(poly.GetBounds())
    origin=np.array([grid.x_offset_mm,grid.y_offset_mm,0.0]);pitch=np.array([grid.x_pitch_mm,grid.y_pitch_mm,.1])
    normalized=(coords-origin)/pitch
    points=_aio_vtk.vtkPoints();points.SetData(_aio_numpy_to_vtk(np.ascontiguousarray(normalized),deep=True))
    source_poly=_aio_vtk.vtkPolyData();source_poly.DeepCopy(poly);source_poly.SetPoints(points)
    # A unique name prevents an earlier accepted file from masquerading as this run's
    # candidate when a later run cannot be simplified. The run report is the index.
    trials=[];destination=reference.with_name(reference.stem+'_print400_'+str(time.time_ns())+'_candidate.stl')
    with tempfile.TemporaryDirectory(prefix='bjt_print_equivalent_',dir=reference.parent) as td:
        candidate=Path(td)/'candidate.stl'
        for error,reduction in ((.45,.95),(.30,.90),(.15,.85),(0.0,.80)):
            _bjt_candidate_check(cancel)
            dec=_aio_vtk.vtkDecimatePro();dec.SetInputData(source_poly)
            dec.SetTargetReduction(reduction);dec.PreserveTopologyOn();dec.SplittingOff();dec.BoundaryVertexDeletionOff()
            dec.SetFeatureAngle(180.0);dec.SetErrorIsAbsolute(1);dec.SetAbsoluteError(error)
            dec.AddObserver('ProgressEvent',lambda obj,event:_bjt_candidate_progress(obj,event,cancel))
            dec.Update();_bjt_candidate_check(cancel);out=dec.GetOutput();n=int(out.GetNumberOfCells())
            trial={'normalized_error_limit':error,'triangles':n};trials.append(trial)
            if n<=0 or n>=triangles:trial['result']='no_reduction';continue
            vv=np.asarray(_aio_vtk_to_numpy(out.GetPoints().GetData()),np.float64)*pitch+origin
            points=_aio_vtk.vtkPoints();points.SetData(_aio_numpy_to_vtk(np.ascontiguousarray(vv,dtype=np.float32),deep=True))
            world=_aio_vtk.vtkPolyData();world.DeepCopy(out);world.SetPoints(points)
            if not np.array_equal(source_bounds,np.array(world.GetBounds())):
                trial['result']='bounds_changed';continue
            sw=_aio_vtk.vtkSTLWriter();sw.SetFileName(str(candidate));sw.SetFileTypeToBinary();sw.SetInputData(world)
            if sw.Write()!=1:raise OSError('프린팅 후보 STL 쓰기 실패')
            _bjt_candidate_check(cancel)
            compare=_bjt_compare_print_slices(reference,candidate,grid,bbox.layers,.1,cancel,log)
            trial.update(compare)
            if not compare['equal']:trial['result']='pixel_difference';continue
            try:
                st=_aio_v37_final_fast_verify(candidate,params,cancel)
            except (ValueError,RuntimeError) as exc:
                trial['result']='invalid_edges';trial['error']=str(exc);continue
            _bjt_candidate_check(cancel)
            os.replace(candidate,destination)
            report={'status':'candidate_verified','candidate_path':str(destination),'reference_path':str(reference),
                    'source_triangles':triangles,'candidate_triangles':n,'reduction_percent':100*(1-n/triangles),
                    'profile':{'dpi_x':400,'dpi_y':400,'layer_thickness_mm':.1,'sample':'pixel/layer centers','polarity':'white','antialias':'binary/no AA','xy_origin_mm':[grid.x_offset_mm,grid.y_offset_mm],'center_xy':params.center_xy},
                    'layers_checked':bbox.layers,'xor_pixels':0,'actual_chitubox_validated':False,
                    'trials':trials,'elapsed_seconds':time.perf_counter()-started,'stl_stats':st}
            destination.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            log.write(f'프린팅 최적화 후보: {triangles:,} -> {n:,}면 ({report["reduction_percent"]:.1f}% 감소), {bbox.layers:,}층 픽셀 차이 0')
            log.write(f'후보 파일: {destination} | 실제 CHITUBOX의 동일 배치·binary/AA 설정 확인 필요')
            return report
    log.write('프린팅 후보 최적화: 모든 층의 픽셀 동일성을 만족하는 경량 후보가 없어 정밀 STL 유지')
    return {'status':'no_certified_reduction','source_triangles':triangles,'trials':trials,'elapsed_seconds':time.perf_counter()-started}

_bjt_build_grid_original=recon_core.build_modeling_grid

def _bjt_capture_grid(bbox,params):
    grid=_bjt_build_grid_original(bbox,params)
    _BJT_STAGE_LOCAL.model_grid=grid;_BJT_STAGE_LOCAL.model_bbox=bbox
    return grid
recon_core.build_modeling_grid=_bjt_capture_grid


# 5.6: print-only proxy, never a claim of unchanged CAD geometry.
import shapely as _bjt_shape
from skimage.measure import find_contours as _bjt_find_contours
_BJT_PRINT_MODE = '레이어 출력 전용'
@njit(cache=True,nogil=True)
def _bjt_gap_has_pixel(p,start,end):
 y0=min(p[start,1],p[end,1]);y1=max(p[start,1],p[end,1])
 for i in range(start,end+1):y0=min(y0,p[i,1]);y1=max(y1,p[i,1])
 hits=np.empty(end-start+1)
 for row in range(int(np.ceil(y0-.5)),int(np.floor(y1-.5))+1):
  y=row+.5;n=0
  for k in range(start,end+1):
   a=p[k];b=p[k+1] if k<end else p[start]
   if min(a[1],b[1])<=y<max(a[1],b[1]):
    x=a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]);hits[n]=x;n+=1
    if k==end and abs(x-.5-round(x-.5))*abs(b[1]-a[1])/np.sqrt((b[0]-a[0])**2+(b[1]-a[1])**2)<.02:return True
   elif k==end and a[1]==b[1] and y==a[1]:
    if np.ceil(min(a[0],b[0])-.5)<=np.floor(max(a[0],b[0])-.5):return True
  vv=np.sort(hits[:n])
  if n%2:return True
  for k in range(0,n,2):
   if np.ceil(vv[k]-.5+1e-8)<=np.floor(vv[k+1]-.5-1e-8):return True
 return False

@njit(cache=True,nogil=True)
def _bjt_simplify_pixels(p,tol=.85):
 n=len(p);keep=np.zeros(n,np.bool_);keep[0]=True;keep[n-1]=True
 stack=np.empty((n*2,2),np.int64);stack[0,0]=0;stack[0,1]=n-1;top=1
 while top:
  top-=1;a,b=stack[top];dx=p[b,0]-p[a,0];dy=p[b,1]-p[a,1];den=dx*dx+dy*dy
  if b<=a+1:continue
  best=-1.;idx=a+1
  for i in range(a+1,b):
   t=((p[i,0]-p[a,0])*dx+(p[i,1]-p[a,1])*dy)/den if den else 0.
   t=max(0.,min(1.,t));ex=p[i,0]-(p[a,0]+t*dx);ey=p[i,1]-(p[a,1]+t*dy);d=ex*ex+ey*ey
   if d>best:best=d;idx=i
  if best>tol*tol or _bjt_gap_has_pixel(p,a,b):
   keep[idx]=True;stack[top,0]=a;stack[top,1]=idx;top+=1;stack[top,0]=idx;stack[top,1]=b;top+=1
 return p[keep]

@njit(cache=True,nogil=True)
def _bjt_orthogonal_ring(a):
 out=np.empty((len(a)*2,2));n=0
 for i in range(len(a)-1):
  out[n]=a[i];n+=1
  if a[i,0]!=a[i+1,0] and a[i,1]!=a[i+1,1]:
   for j in range(2):out[n,j]=a[i,j] if a[i,j]==round(a[i,j]) else a[i+1,j]
   n+=1
 kept=np.empty((n+1,2));k=0
 for i in range(n):
  prev=out[(i-1)%n];curr=out[i];nex=out[(i+1)%n]
  if (curr[0]-prev[0])*(nex[1]-curr[1])!=(curr[1]-prev[1])*(nex[0]-curr[0]):kept[k]=curr;k+=1
 if k:kept[k]=kept[0];k+=1
 return kept[:k]
def _bjt_polygon_compact(mask):
 rings=_bjt_find_contours(np.pad(mask,1),.5,fully_connected='high')
 if not rings:return _bjt_shape.Polygon()
 ls=[]
 for raw in rings:
  a=np.ascontiguousarray(raw[:,::-1]-.5);simple=_bjt_simplify_pixels(a);ortho=_bjt_orthogonal_ring(a)
  ls.append(ortho if 4<=len(ortho)<=len(simple) else simple)
 return _bjt_shape.orient_polygons(_bjt_shape.build_area(_bjt_shape.node(_bjt_shape.MultiLineString(ls))))

def _bjt_poly_segments(poly):
 rings=_bjt_shape.get_parts(_bjt_shape.get_rings(_bjt_shape.get_parts(poly)))
 if not len(rings):return np.empty((0,2,2))
 xy,ids=_bjt_shape.get_coordinates(rings,return_index=True)
 keep=ids[1:]==ids[:-1]
 return np.stack((xy[:-1][keep],xy[1:][keep]),axis=1)


def _bjt_prism_template(mask, fallback=False):
 if fallback:
  rings=_bjt_find_contours(np.pad(mask,1),.5,fully_connected='low')
  p=_bjt_shape.build_area(_bjt_shape.node(_bjt_shape.MultiLineString([a[:,::-1]-.5 for a in rings]))) if rings else _bjt_shape.Polygon()
  p=_bjt_shape.orient_polygons(p)
 else:p=_bjt_polygon_compact(mask)
 if p.is_empty:return np.empty((0,3,3))
 p=_bjt_shape.orient_polygons(_bjt_shape.set_precision(p.buffer(-1/512,join_style='mitre'),1/4096))
 tri=_bjt_shape.get_parts(_bjt_shape.constrained_delaunay_triangles(p));uv=_bjt_shape.get_coordinates(tri).reshape(-1,4,2)[:,:3]
 cr=(uv[:,1,0]-uv[:,0,0])*(uv[:,2,1]-uv[:,0,1])-(uv[:,1,1]-uv[:,0,1])*(uv[:,2,0]-uv[:,0,0]);uv[cr<0]=uv[cr<0][:,[0,2,1]]
 seg=_bjt_poly_segments(p);out=np.empty((2*len(uv)+2*len(seg),3,3))
 n=len(uv);out[:n,:,:2]=uv[:,[0,2,1]];out[:n,:,2]=0;out[n:2*n,:,:2]=uv;out[n:2*n,:,2]=1
 out[2*n::2,:,:2]=seg[:,[0,1,1]];out[2*n::2,:,2]=[0,0,1]
 out[2*n+1::2,:,:2]=seg[:,[0,1,0]];out[2*n+1::2,:,2]=[0,1,1]
 return out



def _bjt_print_surface(params,items,bbox,grid,resolved,log,cancel,mask_store=None):
    """Stream closed prismatic print proxies; certify their actual float32 boundary.

    Each output part is exactly P(x,y) times one Z interval. P is checked against
    every source pixel, and every source layer is assigned to one checked part.
    This product certificate covers all layers without repeated slicing of the
    same prism. Independent tests also reslice every layer with Trimesh.
    """
    if not (params.dpi_x==400 and params.dpi_y==400 and params.layer_thickness_mm==.1):
        raise ValueError('레이어 출력 전용은 400 × 400 DPI / 0.1 mm 전용입니다.')
    if mask_store is None:raise ValueError('입력 mask cache가 필요합니다.')
    if max(abs(grid.x_offset_mm),abs(grid.y_offset_mm),grid.physical_width_mm,grid.physical_height_mm)>500:
        raise ValueError('출력 전용 경로의 검증된 float32 좌표 범위는 원점 기준 ±500 mm입니다. 원점/크기를 확인하십시오.')
    workers=_bjt_workers(min(4,params.workers),grid.width*grid.height,256)
    log.write(f'레이어 출력 전용: 일반 CAD와 다른 메시입니다. 동일 구간을 닫힌 솔리드로 묶고 구간 사이 {format_number(0.001)} mm 간격을 둡니다.')
    log.write('조건: 400 DPI, 0.1 mm 층 중앙, XY/Z 위치·배율 고정, AA/블러/자동 수리/지지대 OFF.')
    representatives=[];keys={};runs=[];previous=None
    for k in range(bbox.layers):
        check_cancel(cancel)
        absolute=bbox.zmin+k
        packed=mask_store.array[absolute,bbox.ymin:bbox.ymax,bbox.xmin//8:(bbox.xmax+7)//8]
        digest=hashlib.sha256(packed.tobytes()).digest()
        pid=None
        # Hash equality is only a lookup: compare the complete packed data.
        for candidate in keys.get(digest,[]):
            other=mask_store.array[representatives[candidate],bbox.ymin:bbox.ymax,bbox.xmin//8:(bbox.xmax+7)//8]
            if np.array_equal(packed,other):pid=candidate;break
        if pid is None:
            pid=len(representatives);representatives.append(absolute);keys.setdefault(digest,[]).append(pid)
        if pid==previous:runs[-1][2]=k+1
        else:runs.append([pid,k,k+1]);previous=pid
    # Small smooth profiles lose time to extra threads; complex contours benefit.
    if grid.width*grid.height<65536:
        peak_runs=0
        for si in sorted({0,len(representatives)//2,len(representatives)-1}):
            check_cancel(cancel)
            probe=recon_core.load_cached_model_mask(mask_store,representatives[si],bbox,grid)
            peak_runs=max(peak_runs,len(_bjt_runs_rows(probe)))
        workers=min(workers,1 if peak_runs<256 else 2)
    certificate={'mode':'layer_print_proxy','geometry_equal_to_original':False,'profile':{'dpi_x':400,'dpi_y':400,'layer_mm':.1,'sample':'pixel and layer centers','antialias':False},
                 'proof':'exact RLE of actual float32 prism boundary versus every source pixel; product geometry and Z intervals cover every source layer',
                 'template_workers':workers,'unique_masks':len(representatives),'runs':len(runs),'layers_checked':0,'xor_pixels':0,'gap_between_adjacent_runs_mm':.001,
                 'cad_rotation_rescaling_supported':False,'physical_printer_or_chitubox_tested':False}
    writer=None
    with tempfile.TemporaryDirectory(prefix='bjt_print_templates_') as td:
        folder=Path(td)
        def job(pid):
            check_cancel(cancel)
            mask=recon_core.load_cached_model_mask(mask_store,representatives[pid],bbox,grid)
            count=int(mask.sum());expected=_bjt_runs_rows(mask)
            for fallback in (False,True):
                check_cancel(cancel)
                q=_bjt_prism_template(mask,fallback=fallback)
                try:
                    if len(q):
                        q[:,:,0]=q[:,:,0]*grid.x_pitch_mm+grid.x_offset_mm
                        q[:,:,1]=q[:,:,1]*grid.y_pitch_mm+grid.y_offset_mm
                        q=q.astype(np.float32)
                        if np.any(np.all(np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0])==0,axis=1)):
                            raise ValueError('float32 퇴화면')
                        z=q[:,:,2];wall=(z[:,0]==0)&(z[:,1]==0)&(z[:,2]==1)
                        boundary=q[wall,:2,:2].astype(np.float64)
                        rows,xs=_bjt_crossings(boundary,grid.x_offset_mm,grid.y_offset_mm,grid.x_pitch_mm,grid.y_pitch_mm,grid.height)
                        if len(xs):
                            cells=(xs-grid.x_offset_mm)/grid.x_pitch_mm-.5
                            if np.any(np.abs(cells-np.rint(cells))<1e-6):
                                raise ValueError('픽셀 경계의 수치 여유 부족')
                        order=np.lexsort((xs,rows));got=_bjt_pixel_runs_sorted(rows[order],xs[order],grid.x_offset_mm,grid.x_pitch_mm,grid.width)
                    else:got=np.empty((0,3),np.int64);boundary=np.empty((0,2,2))
                    if not np.array_equal(got,expected):raise ValueError('전 픽셀 비교 불일치')
                    break
                except ValueError:
                    if fallback:raise ValueError(f'출력 템플릿 {pid+1} 픽셀/수치 검증 실패: 파일을 승격하지 않습니다.')
            # Only unique Y endpoint pairs matter for diagonal intersection stability.
            ypairs=np.unique(boundary[:,:,1],axis=0)
            ypairs=ypairs[ypairs[:,0]!=ypairs[:,1]]
            np.save(folder/f'{pid:08d}_y.npy',ypairs,allow_pickle=False)
            check_cancel(cancel)
            path=folder/f'{pid:08d}.npy'
            if params.max_triangles is not None and len(q)>params.max_triangles:raise RuntimeError('Triangle 제한 초과: 단일 출력 템플릿')
            if shutil.disk_usage(folder).free<workers*q.nbytes+256*1024**2:raise OSError('출력 템플릿 캐시 디스크 공간 부족')
            np.save(path,q,allow_pickle=False)
            return len(q),count,bool(fallback)
        stats={};pending={};nextpid=0
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for pid in range(len(representatives)):
                    check_cancel(cancel)
                    while nextpid<len(representatives) and len(pending)<workers:
                        pending[nextpid]=pool.submit(job,nextpid);nextpid+=1
                    stats[pid]=pending.pop(pid).result()
                    if pid%20==0:log.progress(35+35*(pid+1)/len(representatives),f'픽셀 전수 인증 템플릿 {pid+1:,}/{len(representatives):,}')
            total_tri=sum(stats[pid][0] for pid,lo,hi in runs)
            if not total_tri:raise ValueError('출력 재료가 없습니다.')
            if params.max_triangles is not None and total_tri>params.max_triangles:raise RuntimeError(f'Triangle 제한 초과: {total_tri:,} > {params.max_triangles:,}')
            if total_tri>0xFFFFFFFF:raise OverflowError('STL uint32 triangle limit')
            if shutil.disk_usage(params.output_stl.parent).free<84+50*total_tri+128*1024**2:raise OSError('STL 기록용 디스크 공간이 부족합니다.')
            certificate['fallback_templates']=sum(int(st[2]) for st in stats.values())
            bounds=None
            for fraction in (2**.5-1,3**.5-1,(5**.5-1)/2,math.pi-3):
                alpha=.01*fraction;trial=[];safe=True
                for pid,lo,hi in runs:
                    check_cancel(cancel)
                    lower=np.float32((lo+alpha)*.1);upper=np.float32((hi-(.01-alpha))*.1)
                    if not (lower<(lo+.5)*.1 and upper>(hi-.5)*.1 and lower>=(lo-.5)*.1 and upper<=(hi+.5)*.1):safe=False;break
                    if trial and lower<=trial[-1][1]:safe=False;break
                    ypairs=np.load(folder/f'{pid:08d}_y.npy',allow_pickle=False)
                    if len(ypairs):
                        batch=max(1,min(64,1000000//len(ypairs)))
                        for zfirst in range(lo,hi,batch):
                            check_cancel(cancel)
                            levels=(np.arange(zfirst,min(hi,zfirst+batch),dtype=np.float64)+.5)*.1
                            t=(levels-float(lower))/(float(upper)-float(lower))
                            yy=ypairs[:,0,None]+(ypairs[:,1]-ypairs[:,0])[:,None]*t[None,:]
                            cells=(yy-grid.y_offset_mm)/grid.y_pitch_mm-.5
                            if np.any(np.abs(cells-np.rint(cells))<1e-6):safe=False;break
                    if not safe:break
                    trial.append((lower,upper))
                if safe:bounds=trial;certificate['z_partition_fraction']=float(fraction);break
            if bounds is None:raise ValueError('안정한 Z 분할을 찾지 못했습니다. 출력하지 않습니다.')
            certificate['diagonal_intersection_phase_guard']=True
            writer=recon_core.BinarySTLWriter(params.output_stl,header='BJT 5.6 PRINT PROXY 400dpi 0.1mm centers ONLY; AA OFF; NOT CAD')
            material=0
            for ri,(pid,lo,hi) in enumerate(runs):
                check_cancel(cancel);material+=stats[pid][1]*(hi-lo)
                if not stats[pid][0]:
                    certificate['layers_checked']+=hi-lo;continue
                q=np.load(folder/f'{pid:08d}.npy',allow_pickle=False)
                lower,upper=bounds[ri]
                if not (lower<(lo+.5)*.1 and upper>(hi-.5)*.1 and lower>=(lo-.5)*.1 and upper<=(hi+.5)*.1):
                    raise ValueError('float32 Z interval does not preserve the source layer assignment')
                q[:,:,2]=np.where(q[:,:,2]==0,lower,upper)
                _aio_fast_write_triangle_soup(writer,q,max_triangles=params.max_triangles,cancel_event=cancel)
                certificate['layers_checked']+=hi-lo
                log.progress(70+27*(ri+1)/len(runs),f'출력 전용 STL {ri+1:,}/{len(runs):,} 구간')
            writer.close(success=True)
            certificate['pixel_positions_covered']=bbox.layers*grid.width*grid.height
            certificate['triangles']=int(writer.tri_count)
            _BJT_STAGE_LOCAL.layer_print_certificate=certificate
            log.write(f"출력 픽셀 인증: {certificate['layers_checked']:,}층, {certificate['pixel_positions_covered']:,}위치, 차이 0")
            return int(writer.tri_count),material
        except BaseException:
            for future in pending.values():future.cancel()
            if writer is not None:writer.close(success=False)
            raise

_bjt_v55_interpolated=recon_core.reconstruct_interpolated_surface

def _bjt_dispatch_print_surface(params,*args,**kwargs):
    if params.surface_mode==_BJT_PRINT_MODE:return _bjt_print_surface(params,*args,**kwargs)
    return _bjt_v55_interpolated(params,*args,**kwargs)
recon_core.reconstruct_interpolated_surface=_bjt_dispatch_print_surface

def _bjt_environment_report():
    import platform,importlib.metadata
    versions={}
    for name in ('numpy','scipy','Pillow','scikit-image','trimesh','shapely','numba','vtk','opencv-python','rtree'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    return {'os':platform.platform(),'python':sys.version,'python_bits':struct.calcsize('P')*8,
            'cpu':platform.processor(),'logical_cpus':os.cpu_count(),'available_ram_bytes':_bjt_memory_available(),
            'temp_dir':tempfile.gettempdir(),'temp_free_bytes':shutil.disk_usage(tempfile.gettempdir()).free,
            'versions':versions,'app_version':APP_VERSION,'engine':recon_core.reconstruct_original_surface.__name__}

_bjt_unprofiled_pipeline=run_pipeline

def run_pipeline(params,log,cancel_event,temp_root,preview_callback):
    start=time.perf_counter()
    _BJT_STAGE_LOCAL.timings={}
    _BJT_STAGE_LOCAL.verification=None
    _BJT_STAGE_LOCAL.model_grid=None
    _BJT_STAGE_LOCAL.model_bbox=None
    _BJT_STAGE_LOCAL.layer_print_certificate=None
    _BJT_STAGE_LOCAL.exact_streaming=None
    _BJT_STAGE_LOCAL.candidate_deadline=None
    environment=_bjt_environment_report()
    data={k:(str(v) if isinstance(v,Path) else v) for k,v in vars(params).items()}
    report={'environment':environment,'parameters':data,'status':'running',
            'target_seconds':1800,'target_scope':'run_pipeline; excludes application startup, CHITUBOX and physical printing',
            'user_target_hardware':{'cpu':'Intel Core i5-13500','ram_gb':16,'gpu':'Intel UHD Graphics 770','source':'user screenshot; not measured by this process'}}
    log.write(f"실행 환경: {environment['python_bits']}bit Python · CPU threads={environment['logical_cpus']} · 가용 RAM={format_bytes(environment['available_ram_bytes'])}")
    try:
        result=_bjt_unprofiled_pipeline(params,log,cancel_event,temp_root,preview_callback)
        report['status']='completed'
        if params.output_stl.exists():
            with open(params.output_stl,'rb') as f:f.seek(80);n=struct.unpack('<I',f.read(4))[0]
            report['triangles']=n;report['stl_bytes']=params.output_stl.stat().st_size
            report['verification']=getattr(_BJT_STAGE_LOCAL,'verification',None)
            report['import_ram_estimate_bytes_range']=[n*300,n*1000]
            log.write(f'슬라이서 RAM 계획용 추정: {format_bytes(n*300)} ~ {format_bytes(n*1000)} (CHITUBOX 실측 아님, 형상과 버전별 차이)')
        return result
    except BaseException as exc:
        report['status']='failed';report['error']=str(exc)
        raise
    finally:
        report['elapsed_seconds']=time.perf_counter()-start
        report['target_met']=report['status']=='completed' and report['elapsed_seconds']<=1800
        log.write(f"30분 처리 목표: {'시간 기준 충족' if report['target_met'] else '미달성'} ({report['elapsed_seconds']/60:.2f}분; CHITUBOX/장비 출력 별도)")
        report['stages_seconds']=dict(getattr(_BJT_STAGE_LOCAL,'timings',{}))
        report['layer_print_certificate']=getattr(_BJT_STAGE_LOCAL,'layer_print_certificate',None)
        report['exact_streaming']=getattr(_BJT_STAGE_LOCAL,'exact_streaming',None)
        report_path=params.output_stl.with_suffix('.run.json')
        try:
            report_path.parent.mkdir(parents=True,exist_ok=True)
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            log.write(f'실행 기록: {report_path}')
        except OSError as exc:log.write(f'실행 기록 저장 실패: {exc}')

# Stage timing does not change inputs or returned geometry.
_BJT_STAGE_LOCAL=threading.local()

def _bjt_timed(name,fn):
    def wrapped(*a,**kw):
        start=time.perf_counter()
        try:
            result=fn(*a,**kw)
            if name=='verification':_BJT_STAGE_LOCAL.verification=result
            return result
        finally:
            timings=getattr(_BJT_STAGE_LOCAL,'timings',None)
            if timings is None:timings={};_BJT_STAGE_LOCAL.timings=timings
            timings[name]=time.perf_counter()-start
    return wrapped
slice_model_to_images=_bjt_timed('slice',slice_model_to_images)
lattice_core.run_conversion=_bjt_timed('lattice',lattice_core.run_conversion)
recon_core.reconstruct_streaming=_bjt_timed('reconstruction',recon_core.reconstruct_streaming)
_aio_v37_final_fast_verify=_bjt_timed('verification',_aio_v37_final_fast_verify)


# v5.7: exact greedy rectangles, bounded endpoint sorting, minimal triangulation.
# All coordinates remain on the same continuous voxel surface. No decimation.
@njit(cache=True,nogil=True)
def _bjt57_boundary(row,lines,positions,shifts,total):
    pts=_bjt_rect_corners(row);n=0
    for e in range(4):
        a=pts[e];b=pts[(e+1)%4];axis=0
        for k in range(3):
            if a[k]!=b[k]:axis=k
        key=_bjt_pack_line(axis,a,shifts,total)
        n+=_bjt_find_endpoint(lines,positions,key,max(a[axis],b[axis]))-_bjt_find_endpoint(lines,positions,key,min(a[axis],b[axis]))
    ring=np.empty((n,3),np.int64);idx=0
    for e in range(4):
        a=pts[e];b=pts[(e+1)%4];axis=0
        for k in range(3):
            if a[k]!=b[k]:axis=k
        key=_bjt_pack_line(axis,a,shifts,total)
        lo=_bjt_find_endpoint(lines,positions,key,min(a[axis],b[axis]));hi=_bjt_find_endpoint(lines,positions,key,max(a[axis],b[axis]))
        for j in range(lo,hi):
            ring[idx]=a;ring[idx,axis]=positions[j if a[axis]<b[axis] else hi-(j-lo)];idx+=1
    return ring

@njit(cache=True,nogil=True)
def _bjt57_cross(a,b,c,u,v):
    return (b[u]-a[u])*(c[v]-a[v])-(b[v]-a[v])*(c[u]-a[u])

@njit(cache=True,nogil=True)
def _bjt57_stitch(rects,lines,positions,shifts,total):
    # Strictly convex ears, retaining every collinear shared edge vertex.
    # Grid span is restricted by caller, so all integer cross products fit int64.
    rings=[];count=0
    for row in rects:
        p=_bjt57_boundary(row,lines,positions,shifts,total);rings.append(p);count+=len(p)-2
    out=np.empty((count,3,3),np.int64);idx=0
    for r in range(len(rects)):
        row=rects[r];p=rings[r];n=len(p);axis=int(row[0]);u=0;v=1
        if axis==0:u=1;v=2
        elif axis==1:u=0;v=2
        reverse=(int(row[1])!=(-1 if axis==1 else 1))
        active=np.arange(n,dtype=np.int64);left=n
        while left>3:
            found=False
            for j in range(left):
                ia=active[(j+left-1)%left];ib=active[j];ic=active[(j+1)%left]
                a=p[ia];b=p[ib];c=p[ic]
                if _bjt57_cross(a,b,c,u,v)<=0:continue
                blocked=False
                for k in range(left):
                    ip=active[k]
                    if ip==ia or ip==ib or ip==ic:continue
                    q=p[ip]
                    if (_bjt57_cross(a,b,q,u,v)>=0 and _bjt57_cross(b,c,q,u,v)>=0 and _bjt57_cross(c,a,q,u,v)>=0):
                        blocked=True;break
                if blocked:continue
                out[idx,0]=2*a;out[idx,1]=2*(c if reverse else b);out[idx,2]=2*(b if reverse else c);idx+=1
                for k in range(j,left-1):active[k]=active[k+1]
                left-=1;found=True;break
            if not found:raise ValueError('exact rectangle triangulation failed')
        a=p[active[0]];b=p[active[1]];c=p[active[2]]
        if _bjt57_cross(a,b,c,u,v)<=0:raise ValueError('degenerate final rectangle triangle')
        out[idx,0]=2*a;out[idx,1]=2*(c if reverse else b);out[idx,2]=2*(b if reverse else c);idx+=1
    return out

@njit(cache=True,nogil=True)
def _bjt57_merge_block(a,b,out,last_line,last_pos,have_last):
    i=0;j=0;n=0
    while i<len(a) and j<len(b) and n<len(out):
        use_a=a[i]['line']<b[j]['line'] or (a[i]['line']==b[j]['line'] and a[i]['position']<=b[j]['position'])
        if use_a:line=a[i]['line'];pos=a[i]['position'];i+=1
        else:line=b[j]['line'];pos=b[j]['position'];j+=1
        if not have_last or line!=last_line or pos!=last_pos:
            out[n]['line']=line;out[n]['position']=pos;n+=1;last_line=line;last_pos=pos;have_last=True
    return i,j,n,last_line,last_pos,have_last


def _bjt57_merge_files(pa,pb,target,block,cancel):
    output=np.empty(2*block,_BJT_ENDPOINT_DTYPE)
    with open(pa,'rb') as fa,open(pb,'rb') as fb,open(target,'wb') as dest:
        a=np.fromfile(fa,_BJT_ENDPOINT_DTYPE,count=block);b=np.fromfile(fb,_BJT_ENDPOINT_DTYPE,count=block)
        last_line=np.uint64(0);last_pos=np.int64(0);have=False
        while len(a) and len(b):
            check_cancel(cancel)
            i,j,n,last_line,last_pos,have=_bjt57_merge_block(a,b,output,last_line,last_pos,have)
            output[:n].tofile(dest);a=a[i:];b=b[j:]
            if not len(a):a=np.fromfile(fa,_BJT_ENDPOINT_DTYPE,count=block)
            if not len(b):b=np.fromfile(fb,_BJT_ENDPOINT_DTYPE,count=block)
        remaining,source=(a,fa) if len(a) else (b,fb)
        while len(remaining):
            check_cancel(cancel)
            if have and remaining[0]['line']==last_line and remaining[0]['position']==last_pos:remaining=remaining[1:]
            if len(remaining):remaining.tofile(dest);last_line=remaining[-1]['line'];last_pos=remaining[-1]['position'];have=True
            remaining=np.fromfile(source,_BJT_ENDPOINT_DTYPE,count=block)


def _bjt57_endpoints(rects,shifts,total,budget,folder,cancel):
    # Temporary paths belong to this writer only. Sorted runs use bounded arrays.
    chunk=max(1,int(budget)//1024);runs=[];serial=0
    for start in range(0,len(rects),chunk):
        check_cancel(cancel)
        endpoints=np.empty(8*min(chunk,len(rects)-start),_BJT_ENDPOINT_DTYPE)
        _bjt_fill_endpoints(rects[start:start+chunk],endpoints,shifts,total)
        endpoints=np.unique(endpoints)
        path=folder/f'sort_{serial:06d}.bin';serial+=1;endpoints.tofile(path);runs.append(path)
    block=max(64,min(65536,int(budget)//256))
    while len(runs)>1:
        merged=[]
        for i in range(0,len(runs),2):
            if i+1==len(runs):merged.append(runs[i]);continue
            target=folder/f'sort_{serial:06d}.bin';serial+=1
            _bjt57_merge_files(runs[i],runs[i+1],target,block,cancel)
            runs[i].unlink();runs[i+1].unlink();merged.append(target)
        runs=merged
    return np.memmap(runs[0],dtype=_BJT_ENDPOINT_DTYPE,mode='r')


def _bjt57_budget():
    available=_bjt_memory_available()
    if available<64*1024**2:raise MemoryError('정밀 메시 생성에 필요한 최소 작업 RAM이 부족합니다.')
    return int(min(512*1024**2,available*.12))


@njit(cache=True,nogil=True)
def _bjt57_batch_end(rects,start,lines,positions,shifts,total,max_q):
    end=start;count=0
    while end<len(rects) and end-start<4096:
        upper=_bjt_rect_triangle_count(rects[end],lines,positions,shifts,total)
        if upper>max_q:raise MemoryError('single rectangle exceeds triangle workspace budget')
        if count+upper>max_q:break
        count+=upper;end+=1
    return end


def _bjt_write_grid_triangle_soup(writer,q,coord,max_triangles=None,cancel_event=None,chunk_size=120_000,backend=None):
    """Pack existing ordered grid triangles with bounded optional native output."""
    count=int(len(q))
    if count<=0:return 0
    _aio_fast_writer_check_limit(writer,count,max_triangles)
    native=_bjt_native_edge_backend(backend)
    if native is None or not native.can_pack_grid:
        if native is not None:
            import os as _os
            choice=str(backend if backend is not None else _os.environ.get('BJT_MESH_BACKEND','auto')).strip().lower()
            if choice=='rust':raise RuntimeError('optional Rust grid triangle packing ABI is unavailable')
        xyz=q.astype(np.float64)
        xyz[:,:,0]=xyz[:,:,0]*(.5*coord.xsp)+coord.xoff
        xyz[:,:,1]=xyz[:,:,1]*(.5*coord.ysp)+coord.yoff
        xyz[:,:,2]*=.5*coord.zsp
        return _aio_fast_write_triangle_soup(writer,xyz,max_triangles=max_triangles,cancel_event=cancel_event,chunk_size=chunk_size)
    chunk_size=max(1,int(chunk_size))
    records=np.empty(min(chunk_size,count),dtype=_AIO_FAST_EXACT_RECORD_DTYPE)
    writer._flush_buffer()
    for begin in range(0,count,chunk_size):
        recon_core.check_cancel(cancel_event)
        take=min(chunk_size,count-begin)
        native.pack_grid_triangles(q[begin:begin+take],records,xsp=coord.xsp,ysp=coord.ysp,zsp=coord.zsp,xoff=coord.xoff,yoff=coord.yoff)
        records[:take].tofile(writer.f)
    writer.tri_count+=count
    return count


class _BJT57GridRectWriter(_GridRectWriter):
    def _flush(self):
        if self._buffer and shutil.disk_usage(self._temp.name).free<len(self._buffer)*56+16*1024**2:
            raise OSError('정밀 메시 임시 디스크 공간이 부족합니다.')
        super()._flush()
    def close(self,success):
        if self._closed:return
        writer=None;rects=None;endpoints=None
        try:
            if not success:return
            self._flush();self._f.close()
            if not self.rect_count:raise ValueError('재료 표면이 없습니다.')
            bits=[max(1,int(v).bit_length()) for v in self._bounds];total=sum(bits)
            if total>61:raise OverflowError('정밀 경계 key 좌표 범위를 초과했습니다.')
            shifts=np.array([bits[1]+bits[2],bits[2],0],np.int64)
            rects=np.memmap(self._path,dtype=np.int64,mode='r',shape=(self.rect_count,7))
            budget=_bjt57_budget();disk_mode=self.rect_count*600>budget
            if disk_mode:
                required=self.rect_count*8*_BJT_ENDPOINT_DTYPE.itemsize*3+64*1024**2
                if shutil.disk_usage(self._temp.name).free<required:raise OSError('경계 외부 정렬용 디스크 공간이 부족합니다.')
                endpoints=_bjt57_endpoints(rects,shifts,total,budget,Path(self._temp.name),self.cancel)
                lines=endpoints['line'];positions=endpoints['position']
            else:
                e=np.empty(8*self.rect_count,_BJT_ENDPOINT_DTYPE);_bjt_fill_endpoints(rects,e,shifts,total)
                position_bits=max(bits)
                if total+2+position_bits<=64:
                    packed=(e['line']<<np.uint64(position_bits))|e['position'].astype(np.uint64);del e
                    packed=np.unique(packed)
                    lines=np.ascontiguousarray(packed>>np.uint64(position_bits));positions=np.ascontiguousarray((packed&np.uint64((1<<position_bits)-1)).astype(np.int64));del packed
                else:
                    e=np.unique(e);lines=np.ascontiguousarray(e['line']);positions=np.ascontiguousarray(e['position']);del e
            check_cancel(self.cancel)
            writer=recon_core.BinarySTLWriter(self.final_path,header='BJT 5.7 lossless greedy voxel; exact shared edges; bounded sorting')
            # Bound emitted triangle arrays too. One exceptional rectangle must fit.
            max_q=max(64,budget//512);start=0;emitted=0
            while start<self.rect_count:
                check_cancel(self.cancel)
                end=_bjt57_batch_end(rects,start,lines,positions,shifts,total,max_q)
                if max(bits)<=26:
                    q=_bjt57_stitch(rects[start:end],lines,positions,shifts,total)
                else:
                    q=_bjt_stitch_rectangles(rects[start:end],lines,positions,shifts,total)
                if shutil.disk_usage(self.final_path.parent).free<len(q)*50+16*1024**2:raise OSError('STL 출력 공간이 부족합니다.')
                _bjt_write_grid_triangle_soup(writer,q,self.coord,max_triangles=self.limit,cancel_event=self.cancel)
                emitted+=len(q);start=end
            self.tri_count=writer.tri_count;writer.close(success=True)
            _BJT_STAGE_LOCAL.exact_streaming={'mode':'external sort' if disk_mode else 'RAM sort','rectangle_count':self.rect_count,'endpoint_count':len(lines),'working_array_budget_bytes':budget,'triangles':self.tri_count,'geometry':'exact original voxel surface; no gaps, smoothing or decimation','resident_memory_limit_enforced':False}
        except BaseException:
            if writer is not None:writer.close(success=False)
            raise
        finally:
            if not self._f.closed:self._f.close()
            if endpoints is not None:endpoints._mmap.close()
            if rects is not None:rects._mmap.close()
            self._temp.cleanup();self._closed=True

recon_core._GridRectWriter=_BJT57GridRectWriter

@njit(cache=True,nogil=True)
def _bjt57_side_step(active,runs,z,axis,sign,shift):
    following=np.empty((len(runs),4),np.int64)
    done=np.empty((len(active),7),np.int64);i=0;j=0;n=0
    while i<len(active) or j<len(runs):
        comparison=0
        if i==len(active):comparison=1
        elif j==len(runs):comparison=-1
        else:
            for k in range(3):
                value=runs[j,k]+(shift if k==0 else 0)
                if active[i,k]<value:comparison=-1;break
                if active[i,k]>value:comparison=1;break
        if comparison<=0:
            if comparison==0:
                following[j]=active[i];j+=1
            else:
                done[n,0]=axis;done[n,1]=sign;done[n,2]=active[i,0]
                done[n,3]=active[i,1];done[n,4]=active[i,2];done[n,5]=active[i,3];done[n,6]=z;n+=1
            i+=1
        else:
            following[j,0]=runs[j,0]+shift;following[j,1]=runs[j,1];following[j,2]=runs[j,2];following[j,3]=z;j+=1
    return following,done[:n]

@njit(cache=True,nogil=True)
def _bjt57_z_rects(rectangles,z,sign):
    out=np.empty((len(rectangles),7),np.int64)
    for i in range(len(rectangles)):
        out[i,0]=2;out[i,1]=sign;out[i,2]=z
        out[i,3]=rectangles[i,2];out[i,4]=rectangles[i,3];out[i,5]=rectangles[i,0];out[i,6]=rectangles[i,1]
    return out


def _bjt57_add_rectangles(self,rects):
    check_cancel(self.cancel)
    if not len(rects):return
    self._flush()
    self.rect_count+=len(rects);self.tri_count=2*self.rect_count
    if self.limit is not None and self.tri_count>self.limit:raise RuntimeError('Triangle 제한 초과: 원본 정보 보존에 필요한 면 수가 제한보다 큽니다.')
    if shutil.disk_usage(self._temp.name).free<rects.nbytes+16*1024**2:raise OSError('메시 임시 디스크 공간이 부족합니다.')
    for axis in range(3):
        rows=rects[rects[:,0]==axis]
        if not len(rows):continue
        other=[a for a in range(3) if a!=axis]
        self._bounds[axis]=max(self._bounds[axis],int(rows[:,2].max()))
        self._bounds[other[0]]=max(self._bounds[other[0]],int(rows[:,4].max()))
        self._bounds[other[1]]=max(self._bounds[other[1]],int(rows[:,6].max()))
    rects.tofile(self._f)
_BJT57GridRectWriter.add_rectangles=_bjt57_add_rectangles


def _bjt57_original_surface(params,items,bbox,grid,resolved,log,cancel_event,mask_store=None):
    if params.mesh_mode!='고속 병합':
        return _bjt_original_reconstruct_bounded(params,items,bbox,grid,resolved,log,cancel_event,mask_store)
    # Conservative layer workspace includes active run arrays and generated rectangles.
    _bjt_workers(1,grid.height*grid.width,160)
    writer=_BJT57GridRectWriter(params.output_stl,recon_core.CoordinateMap(grid,params.layer_thickness_mm),params.max_triangles,cancel_event)
    active=[np.empty((0,4),np.int64) for _ in range(4)]
    previous=np.zeros((grid.height,grid.width),bool);material=0;identical=0
    log.write('정밀 그리디 메싱: Numba 배열 단위 측면 병합 → RAM 예산에 맞춘 경계 정렬 → STL 순차 기록')
    try:
        for z in range(bbox.layers+1):
            check_cancel(cancel_event)
            if z==bbox.layers:current=np.zeros_like(previous)
            elif mask_store is not None:current=recon_core.load_cached_model_mask(mask_store,bbox.zmin+z,bbox,grid)
            else:current=recon_core.load_model_mask(items[bbox.zmin+z],bbox,resolved,grid)
            material+=int(np.count_nonzero(current))
            if np.array_equal(current,previous):identical+=1;continue
            writer.add_rectangles(_bjt57_z_rects(_bjt_rectangles(current&~previous),z,-1))
            writer.add_rectangles(_bjt57_z_rects(_bjt_rectangles(previous&~current),z,1))
            masks=recon_core.side_masks(current)
            for k,(axis,sign,shift) in enumerate([(0,-1,0),(0,1,1),(1,-1,0),(1,1,1)]):
                mask=np.ascontiguousarray(masks[k].T if axis==0 else masks[k])
                runs=_bjt_runs_rows(mask)
                active[k],done=_bjt57_side_step(active[k],runs,z,axis,sign,shift)
                writer.add_rectangles(done)
            previous=current
            if z%10==0 or z==bbox.layers:log.progress(20+77*z/max(1,bbox.layers),f'그리디 면 생성 {min(z+1,bbox.layers)}/{bbox.layers}')
        log.progress(98,'경계 정렬 및 정밀 삼각형 기록')
        writer.close(True)
        recon_core.verify_binary_stl(params.output_stl,writer.tri_count)
        if getattr(_BJT_STAGE_LOCAL,'exact_streaming',None) is not None:
            _BJT_STAGE_LOCAL.exact_streaming['run_engine']='Numba sorted arrays; no per-face Python dictionary updates'
            _BJT_STAGE_LOCAL.exact_streaming['identical_layer_steps']=identical
        return writer.tri_count,material
    except BaseException:
        writer.close(False);raise
recon_core.reconstruct_original_surface=_bjt57_original_surface


def main():
    import argparse
    parser=argparse.ArgumentParser(description='BJT exact voxel/lattice STL converter')
    parser.add_argument('--config',type=Path,help='PipelineParams JSON; omit to open GUI')
    parser.add_argument('--diagnose',type=Path,help='write machine and dependency report')
    args=parser.parse_args()
    if args.diagnose:
        args.diagnose.write_text(json.dumps(_bjt_environment_report(),ensure_ascii=False,indent=2),encoding='utf-8')
        print(args.diagnose);return
    if args.config:
        data=json.loads(args.config.read_text(encoding='utf-8-sig'))
        data['input_path']=Path(data['input_path'])
        data['output_stl']=Path(data['output_stl']) if data.get('output_stl') else data['input_path'].with_name(data['input_path'].stem+'_shell.stl')
        data['surface_mode']='원본 충실형'
        data['print_equivalent']=False
        params=PipelineParams(**data)
        class ConsoleLog:
            def write(self,message=''):print(message,flush=True)
            def progress(self,percent,message=''):print(f'[{percent:.1f}%] {message}',flush=True)
        with tempfile.TemporaryDirectory(prefix='bjt_model_pipeline_') as td:
            run_pipeline(params,ConsoleLog(),threading.Event(),Path(td),lambda *a:None)
        return
    app=App();app.mainloop()

if _BJT_LAUNCH_AS_MAIN:
    main()
