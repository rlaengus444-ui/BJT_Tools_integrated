"""Optional, bundled Rust kernels behind a checked, Python-independent C ABI.

The reference algorithms, RNG and FCL final predicates remain in Python/C++.
No compiler, pip extension or changed scientific dependencies are needed on a
user machine. Set POWDERNEST_NATIVE=python to compare with the reference;
POWDERNEST_NATIVE=rust requires the library instead of silently falling back.
"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
import base64
import ctypes as ct
import hashlib
import os
from pathlib import Path
import platform
import tempfile
import threading
import zlib

import numpy as np

_choice = ContextVar('powdernest_native_backend', default=None)
_lock = threading.Lock()
_library = None
_failure = None
_digest = None
_counts = Counter()
_bridge_token = _bridge_digest = None
_d = ct.POINTER(ct.c_double)
_i = ct.POINTER(ct.c_int64)
_b = ct.POINTER(ct.c_uint8)
_z = ct.c_size_t


def mode():
    value = _choice.get() or os.environ.get('POWDERNEST_NATIVE', 'auto').lower()
    if value not in ('auto', 'python', 'rust'):
        raise ValueError('POWDERNEST_NATIVE must be auto, python or rust')
    return value


@contextmanager
def backend(value):
    if value not in ('auto', 'python', 'rust'): raise ValueError('Unknown native backend')
    token = _choice.set(value)
    try: yield
    finally: _choice.reset(token)


def _configure(lib):
    signatures = {
        'pn_triangle_pairs': [_d, _d, _i, _z, _i, _z, ct.c_double, _b],
        'pn_unique_rows': [_i, _z, _i],
        'pn_mesh_contacts': [_d, _z, _d, _z, ct.c_double, _d, ct.c_double, _d],
        'pn_solid_filter': [_d, _z, _d, _d, _d, _d, _z, ct.c_double, _b],
        'pn_neighbors': [_d, _d, _d, _d, _d, _z, ct.c_double, _d, _b],
        'pn_overlap_cost': [_d, _d, _z, _d, _d, _z, _d, _d],
        'pn_compaction_contacts': [_d, _d, _d, _z, _z, ct.c_double, ct.c_double, ct.c_double, ct.c_double, _d],
    }
    lib.pn_abi_version.argtypes = []; lib.pn_abi_version.restype = ct.c_uint32
    if lib.pn_abi_version() != 1: raise RuntimeError('Unsupported Rust kernel ABI')
    for name, arguments in signatures.items():
        function = getattr(lib, name); function.argtypes = arguments; function.restype = ct.c_int64


def library():
    global _library, _failure, _digest
    selected = mode()
    if selected == 'python': return None
    if _library is not None: return _library
    if _failure is None:
        with _lock:
            if _library is None and _failure is None:
                try:
                    from ._native_payloads import PAYLOADS
                    system = platform.system().lower()
                    machine = platform.machine().lower()
                    if machine not in ('x86_64', 'amd64'): raise RuntimeError('Rust binary requires x86-64')
                    item = PAYLOADS[system + '-x86_64']
                    data = zlib.decompress(base64.b85decode(item['data']))
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != item['sha256'] or len(data) != item['bytes']:
                        raise RuntimeError('Bundled Rust binary checksum failed')
                    base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else os.environ.get('XDG_CACHE_HOME')
                    candidates = [(Path(base) if base else Path.home() / '.cache') / 'PowderNest' / 'native-v1',
                                  Path(tempfile.gettempdir()) / 'powdernest-native-v1']
                    error = None
                    for root in candidates:
                        temporary = None
                        try:
                            folder = root / digest; folder.mkdir(parents=True, exist_ok=True)
                            path = folder / item['filename']
                            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                                with tempfile.NamedTemporaryFile(dir=folder, suffix='.tmp', delete=False) as stream:
                                    temporary = Path(stream.name); stream.write(data)
                                os.replace(temporary, path)
                            lib = ct.CDLL(str(path.resolve())); _configure(lib)
                            _library, _digest = lib, digest
                            break
                        except (OSError, RuntimeError) as exc: error = exc
                        finally:
                            if temporary is not None: temporary.unlink(missing_ok=True)
                    if _library is None: raise RuntimeError(str(error))
                except (ImportError, KeyError, OSError, ValueError, RuntimeError, zlib.error) as exc:
                    _failure = str(exc)
    if _library is None and selected == 'rust':
        raise RuntimeError('Rust 연산 모듈을 실행하지 못했습니다: ' + str(_failure))
    return _library


def fingerprint():
    global _bridge_token, _bridge_digest
    if library() is None: return 'python-reference'
    functions = (TriangleBatch.__init__, TriangleBatch.classify, _array, _ptr, _call)
    token = tuple(id(function) for function in functions)
    if token != _bridge_token:
        from .geometry import _code_signature
        digest = hashlib.sha256()
        for function in functions:
            digest.update(_code_signature(function.__code__).encode())
            if function.__module__ != __name__ or function.__closure__:
                digest.update(str(id(function)).encode())
        _bridge_token, _bridge_digest = token, digest.hexdigest()
    return 'rust-abi1:' + _digest + ':' + _bridge_digest


def status():
    active = library() is not None
    return {'backend': 'rust' if active else 'python', 'abi': 1 if active else None,
            'binary_sha256': _digest if active else None, 'fallback_reason': _failure if not active else None,
            'kernels': dict(_counts)}


def _array(value, dimensions, dtype=np.float64):
    raw = np.asarray(value)
    if np.issubdtype(dtype, np.integer) and not np.issubdtype(raw.dtype, np.integer):
        raise ValueError('Native integer input must contain integers')
    array = np.ascontiguousarray(raw, dtype=dtype)
    if array.ndim != len(dimensions) or any(n is not None and array.shape[a] != n for a, n in enumerate(dimensions)):
        raise ValueError('Invalid native array shape')
    if not np.isfinite(array).all(): raise ValueError('Native arrays must be finite')
    return array


def _ptr(array):
    return array.ctypes.data_as({np.dtype('float64'): _d, np.dtype('int64'): _i, np.dtype('uint8'): _b}[array.dtype])


def _call(lib, name, *arguments):
    result = getattr(lib, 'pn_' + name)(*arguments)
    if result < 0: raise ValueError('Rust kernel rejected invalid input: ' + name)
    _counts[name] += 1
    return result


class TriangleBatch:
    def __init__(self, triangles, normals, faces):
        self.lib = library()
        self.triangles = _array(triangles, (None, 3, 3))
        self.normals = _array(normals, (len(self.triangles), 3))
        self.faces = _array(faces, (len(self.triangles), 3), np.int64)
        if np.any(self.faces < 0): raise ValueError('Negative vertex index')

    def classify(self, pairs, epsilon):
        if self.lib is None: return None
        pairs = _array(pairs, (None, 2), np.int64)
        if pairs.size and (pairs.min() < 0 or pairs.max() >= len(self.triangles)):
            raise ValueError('Triangle pair index out of bounds')
        if not np.isfinite(epsilon) or epsilon <= 0: raise ValueError('Invalid geometry tolerance')
        result = np.empty(len(pairs), dtype=np.uint8)
        _call(self.lib, 'triangle_pairs', _ptr(self.triangles), _ptr(self.normals), _ptr(self.faces),
              len(self.triangles), _ptr(pairs), len(pairs), epsilon, _ptr(result))
        _counts['triangle_pairs_tested'] += len(pairs)
        _counts['triangle_python_fallbacks'] += int(np.count_nonzero(result == 2))
        return result


def unique_rows(quantized):
    lib = library()
    if lib is None: return None
    rows = _array(quantized, (None, 3), np.int64); output = np.empty(len(rows), dtype=np.int64)
    count = _call(lib, 'unique_rows', _ptr(rows), len(rows), _ptr(output))
    return output[:count]


def mesh_contacts(world, own, gap, extent, wall):
    lib = library()
    if lib is None: return None
    world = _array(world, (None, 3)); own = _array(own, (None, 3)); extent = _array(extent, (3,))
    if not np.isfinite([gap, wall]).all(): raise ValueError('Nonfinite contact condition')
    result = np.empty((len(world) * (8 + 5 * len(own)), 3))
    _call(lib, 'mesh_contacts', _ptr(world), len(world), _ptr(own), len(own), gap, _ptr(extent), wall, _ptr(result))
    return result


def solid_filter(points, extent, lows, highs, required, tolerance):
    lib = library()
    if lib is None: return None
    points = _array(points, (None, 3)); extent = _array(extent, (3,))
    lows = _array(lows, (None, 3)); highs = _array(highs, (len(lows), 3)); required = _array(required, (len(lows),))
    if not np.isfinite(tolerance) or tolerance < 0: raise ValueError('Invalid numerical tolerance')
    result = np.empty(len(points), dtype=np.uint8)
    _call(lib, 'solid_filter', _ptr(points), len(points), _ptr(extent), _ptr(lows), _ptr(highs),
          _ptr(required), len(lows), tolerance, _ptr(result))
    return result


def overlap_cost(low, extent, other_low, other_extent, gap):
    lib = library()
    if lib is None: return None
    low = _array(low, (None, 3)); extent = _array(extent, (len(low), 3))
    other_low = _array(other_low, (None, 3)); other_extent = _array(other_extent, (len(other_low), 3))
    gap = _array(gap, (len(low), len(other_low))); result = np.empty(gap.shape)
    _call(lib, 'overlap_cost', _ptr(low), _ptr(extent), len(low), _ptr(other_low), _ptr(other_extent),
          len(other_low), _ptr(gap), _ptr(result))
    return result


def neighbors(low, high, lows, highs, required, tolerance):
    lib = library()
    if lib is None: return None
    low = _array(low, (3,)); high = _array(high, (3,)); lows = _array(lows, (None, 3))
    highs = _array(highs, (len(lows), 3)); required = _array(required, (len(lows),))
    if not np.isfinite(tolerance) or tolerance < 0: raise ValueError('Invalid numerical tolerance')
    distance = np.empty(len(lows)); flags = np.empty(len(lows), dtype=np.uint8)
    _call(lib, 'neighbors', _ptr(low), _ptr(high), _ptr(lows), _ptr(highs), _ptr(required),
          len(lows), tolerance, _ptr(distance), _ptr(flags))
    if np.any(flags == 2): return None
    return np.flatnonzero(flags), distance


def compaction_contacts(low, extent, gaps, axis, own, wall, upper, minimum):
    lib = library()
    if lib is None: return None
    low = _array(low, (None, 3)); extent = _array(extent, (len(low), 3)); gaps = _array(gaps, (len(low),))
    if axis not in (0, 1, 2) or not np.isfinite([own, wall, upper, minimum]).all():
        raise ValueError('Invalid compaction bounds')
    result = np.empty(2 * len(low) + 1)
    n = _call(lib, 'compaction_contacts', _ptr(low), _ptr(extent), _ptr(gaps), len(low), axis,
              own, wall, upper, minimum, _ptr(result))
    return result[:n].tolist()
