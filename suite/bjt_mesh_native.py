"""Optional, versioned zero-copy Rust edge-verification backend.

Only the sibling native directory is searched. Missing/unsupported binaries are
optional in auto mode; a failure after invoking native code always raises.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys

import numpy as np

ABI_VERSION = 1
EDGE_DTYPE = np.dtype([(f"p{i}", "<u4") for i in range(6)] + [("direction", "<i4"), ("contact", "<i4")])
STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("a", "<f4", (3,)), ("b", "<f4", (3,)),
                      ("c", "<f4", (3,)), ("attr", "<u2")])
COUNT_FIELDS = ("boundary_edges", "nonmanifold_edges", "inconsistent_winding_edges", "unique_edges", "voxel_contact_edges")
STATUS_REASONS = {1: "null pointer", 2: "pointer alignment", 3: "row length overflow",
                  4: "invalid edge direction", 5: "invalid contact code", 6: "unsupported platform",
                  7: "overlapping buffers", 8: "output capacity", 9: "nonfinite grid transform", 255: "native panic caught"}


class NativeUnavailableError(RuntimeError):
    """The optional binary could not be discovered or its ABI is unsupported."""


class NativeExecutionError(RuntimeError):
    """Native execution failed; callers must not silently accept a fallback."""


class _Counts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in COUNT_FIELDS]


class _GridTransform(ctypes.Structure):
    _fields_ = [(name, ctypes.c_double) for name in ("xsp", "ysp", "zsp", "xoff", "yoff")]


class NativeBackend:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._library = ctypes.CDLL(str(self.path))
        checks = (("bjt_mesh_abi_version", ctypes.c_uint32, ABI_VERSION),
                  ("bjt_mesh_edge_row_size", ctypes.c_size_t, 32),
                  ("bjt_mesh_edge_row_align", ctypes.c_size_t, 4),
                  ("bjt_mesh_counts_size", ctypes.c_size_t, ctypes.sizeof(_Counts)),
                  ("bjt_mesh_platform_supported", ctypes.c_uint32, 1))
        for name, result_type, expected in checks:
            function = getattr(self._library, name)
            function.argtypes = []
            function.restype = result_type
            actual = int(function())
            if actual != expected:
                raise NativeUnavailableError(f"{name}: {actual} != {expected}")
        self._sort_count = self._library.bjt_mesh_sort_count_edges
        self._sort_count.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(_Counts)]
        self._sort_count.restype = ctypes.c_int32
        self._pack_grid = None
        self._pack_unavailable_reason = "optional triangle packing ABI is absent"
        try:
            version = self._library.bjt_mesh_triangle_pack_abi_version
            version.argtypes = []
            version.restype = ctypes.c_uint32
            if int(version()) != 1:
                self._pack_unavailable_reason = "optional triangle packing ABI is unsupported"
            else:
                pack = self._library.bjt_mesh_pack_grid_triangles
                pack.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(_GridTransform), ctypes.c_void_p, ctypes.c_size_t]
                pack.restype = ctypes.c_int32
                self._pack_grid = pack
        except AttributeError:
            pass

    @property
    def can_pack_grid(self):
        return self._pack_grid is not None

    def sort_count(self, rows):
        """Mutate private verification rows and return exact incidence counts.

        The caller must prevent concurrent reads, writes or resizing of rows and
        every alias while ctypes releases the GIL. OWNDATA alone cannot prove
        exclusive access; production passes unpublished, private work arrays.
        """
        if not isinstance(rows, np.ndarray) or rows.ndim != 1 or rows.dtype != EDGE_DTYPE:
            raise TypeError("native edge rows require the exact one-dimensional 32-byte edge dtype")
        if not (rows.flags.owndata and rows.flags.c_contiguous and rows.flags.writeable):
            raise ValueError("native edge rows must be private owned, writable and contiguous")
        count = len(rows)
        if count > (sys.maxsize // EDGE_DTYPE.itemsize):
            raise OverflowError("native edge row length exceeds isize range")
        address = int(rows.ctypes.data)
        if address % 4:
            raise ValueError("native edge row pointer must have 4-byte alignment")
        output = _Counts()
        # The array and this backend (which owns the CDLL) remain alive across
        # the GIL-releasing ctypes call. Native code neither retains nor frees it.
        status = int(self._sort_count(ctypes.c_void_p(address), count, ctypes.byref(output)))
        if status:
            raise NativeExecutionError(f"Rust edge verification failed ({status}: {STATUS_REASONS.get(status, 'unknown status')})")
        return tuple(int(getattr(output, name)) for name in COUNT_FIELDS)

    def pack_grid_triangles(self, q, records, *, xsp, ysp, zsp, xoff, yoff):
        """Read ordered q and fill bounded records, with exclusive borrowed access.

        No thread may read/write/resize q, records or any aliases during the
        GIL-releasing call. Input q is never modified or retained by Rust.
        """
        if not self.can_pack_grid:
            raise NativeUnavailableError(self._pack_unavailable_reason)
        if (not isinstance(q, np.ndarray) or q.dtype != np.dtype("<i8") or q.ndim != 3
                or q.shape[1:] != (3, 3) or not q.flags.c_contiguous):
            raise TypeError("grid triangles require contiguous int64 (N,3,3) q")
        if (not isinstance(records, np.ndarray) or records.dtype != STL_DTYPE or records.ndim != 1
                or not (records.flags.owndata and records.flags.c_contiguous and records.flags.writeable)):
            raise TypeError("output requires an owned writable contiguous 50-byte STL record array")
        count = len(q)
        if count > sys.maxsize // 72:
            raise OverflowError("grid triangle input length exceeds isize range")
        if len(records) < count:
            raise ValueError("STL record output capacity is too small")
        if q.ctypes.data % 8:
            raise ValueError("grid triangle input must have 8-byte alignment")
        transform = _GridTransform(xsp, ysp, zsp, xoff, yoff)
        status = int(self._pack_grid(ctypes.c_void_p(q.ctypes.data), count, ctypes.byref(transform),
                                     ctypes.c_void_p(records.ctypes.data), records.nbytes))
        if status:
            raise NativeExecutionError(f"Rust grid triangle packing failed ({status}: {STATUS_REASONS.get(status, 'unknown status')})")
        return count


def _library_path():
    names = {"win32": "bjt_mesh_native.dll", "darwin": "libbjt_mesh_native.dylib"}
    name = names.get(sys.platform, "libbjt_mesh_native.so" if sys.platform.startswith("linux") else None)
    return Path(__file__).resolve().parent / "native" / name if name else None


@lru_cache(maxsize=1)
def _load_backend():
    path = _library_path()
    if sys.byteorder != "little" or ctypes.sizeof(ctypes.c_void_p) != 8 or path is None:
        return None, "requires a supported little-endian 64-bit platform", path
    if not path.is_file():
        return None, "optional native library is absent", path
    try:
        return NativeBackend(path), "available", path
    except (OSError, AttributeError, NativeUnavailableError) as exc:
        return None, f"optional native library unavailable: {exc}", path


def get_backend(backend=None):
    choice = str(backend if backend is not None else os.environ.get("BJT_MESH_BACKEND", "auto")).strip().lower()
    if choice not in ("auto", "python", "rust"):
        raise ValueError("BJT_MESH_BACKEND must be auto, python or rust")
    if choice == "python":
        return None
    native, reason, _ = _load_backend()
    if native is None and choice == "rust":
        raise NativeUnavailableError(reason)
    return native


def backend_status():
    native, reason, path = _load_backend()
    return {"available": native is not None, "reason": reason,
            "path": str(path) if path is not None else None, "abi_version": ABI_VERSION if native else None,
            "can_pack_grid": bool(native and native.can_pack_grid)}
