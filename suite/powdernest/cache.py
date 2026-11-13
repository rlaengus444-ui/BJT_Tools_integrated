"""Bounded, content-addressed caches of completed solid checks.

No pickle, optimizer feasibility claim or public diagnostics flag is trusted.
Memory records contain immutable bytes. Disk records are authenticated with a
local cache key, and bind source content, units, meshing settings and checker
version. Every restored caller gets private arrays, never the stored buffers.
"""
from __future__ import annotations

from collections import OrderedDict, Counter
from dataclasses import dataclass
from hashlib import sha256
import hmac
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import zipfile

import numpy as np


SCHEMA = 'powdernest.geometry-cache.v1'
MEMORY_LIMIT = 128 * 1024 ** 2
DISK_LIMIT = 512 * 1024 ** 2
ENTRY_LIMIT = 128 * 1024 ** 2
PAIR_LIMIT = 65536
_lock = threading.RLock()
_geometry = OrderedDict()
_pairs = OrderedDict()
_memory_bytes = 0
_stats = Counter()
_receipt_key = os.urandom(32)


def enabled():
    return os.environ.get('POWDERNEST_DISABLE_CACHE', '').lower() not in ('1', 'true', 'yes')


def statistics():
    with _lock:
        return dict(_stats, memory_bytes=_memory_bytes, geometry_records=len(_geometry), pair_records=len(_pairs))


def mesh_digest(mesh):
    """Hash actual numeric input, including topology; never rely on mtime."""
    try:
        v, f = np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces)
        if (v.ndim != 2 or v.shape[1:] != (3,) or not len(v) or not np.isfinite(v).all()
                or f.ndim != 2 or f.shape[1:] != (3,) or not len(f)
                or not np.issubdtype(f.dtype, np.integer) or f.min() < 0 or f.max() >= len(v)):
            return None
        digest = sha256(struct.pack('<QQ', len(v), len(f)))
        digest.update(np.ascontiguousarray(v, dtype='<f8').tobytes())
        digest.update(np.ascontiguousarray(f, dtype='<i8').tobytes())
        return digest.digest()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def attach_receipt(model, stamp, information):
    """A small process-local proof keeps a live model fast after LRU eviction."""
    if not enabled(): return
    digest = mesh_digest(model.mesh)
    if digest is None: return
    encoded = information if isinstance(information, str) else json.dumps(information, allow_nan=False)
    body = stamp.encode() + digest + encoded.encode()
    model._geometry_receipt = (stamp, digest, encoded, hmac.digest(_receipt_key, body, 'sha256'))


def receipt_information(model, stamp, snapshot):
    if not enabled(): return None
    value = getattr(model, '_geometry_receipt', None)
    if not isinstance(value, tuple) or len(value) != 4: return None
    revision, digest, encoded, signature = value
    try:
        body = revision.encode() + digest + encoded.encode()
        if (revision != stamp or mesh_digest(snapshot) != digest
                or not hmac.compare_digest(signature, hmac.digest(_receipt_key, body, 'sha256'))):
            return None
    except (TypeError, AttributeError):
        return None
    with _lock:
        _stats['geometry_hits'] += 1; _stats['receipt_hits'] += 1
    return encoded


@dataclass(frozen=True)
class GeometryRecord:
    vertices: bytes
    faces: bytes
    information: str

    def arrays(self):
        return (np.frombuffer(self.vertices, dtype='<f8').reshape((-1, 3)).copy(),
                np.frombuffer(self.faces, dtype='<i8').reshape((-1, 3)).copy())

    @property
    def size(self):
        return len(self.vertices) + len(self.faces) + len(self.information.encode('utf-8'))


def remember_geometry(stamp, mesh, information, *, alias=None):
    if not enabled(): return
    digest = mesh_digest(mesh)
    if digest is None: return
    try:
        record = GeometryRecord(np.ascontiguousarray(mesh.vertices, dtype='<f8').tobytes(),
                                np.ascontiguousarray(mesh.faces, dtype='<i8').tobytes(),
                                json.dumps(information, allow_nan=False))
        put_geometry((stamp, digest), record)
        if alias is not None and alias != digest:
            put_geometry((stamp, alias), record)
    except (TypeError, ValueError, MemoryError):
        pass


def put_geometry(key, record):
    global _memory_bytes
    if record.size > MEMORY_LIMIT: return
    with _lock:
        old = _geometry.pop(key, None)
        if old is not None: _memory_bytes -= old.size
        _geometry[key] = record; _memory_bytes += record.size
        while _memory_bytes > MEMORY_LIMIT and _geometry:
            _, removed = _geometry.popitem(last=False); _memory_bytes -= removed.size


def get_geometry(stamp, mesh):
    if not enabled(): return None
    digest = mesh_digest(mesh)
    if digest is None: return None
    with _lock:
        record = _geometry.pop((stamp, digest), None)
        if record is not None:
            _geometry[(stamp, digest)] = record; _stats['geometry_hits'] += 1
        else:
            _stats['geometry_misses'] += 1
        return record


def pair_get(key):
    if not enabled(): return None
    with _lock:
        value = _pairs.pop(key, None)
        if value is not None:
            _pairs[key] = value; _stats['pair_hits'] += 1
        return value


def pair_put(key, value):
    if not enabled(): return
    # Exceptions and invalid numerical distances are never memoized.
    if not np.isfinite(value[1]): return
    with _lock:
        _pairs.pop(key, None); _pairs[key] = tuple(value)
        while len(_pairs) > PAIR_LIMIT: _pairs.popitem(last=False)


def directory():
    override = os.environ.get('POWDERNEST_CACHE_DIR')
    if override:
        choices = [Path(override)]
    else:
        base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else os.environ.get('XDG_CACHE_HOME')
        choices = [(Path(base) if base else Path.home() / '.cache') / 'PowderNest' / 'geometry-v1',
                   Path(tempfile.gettempdir()) / ('powdernest-geometry-' + sha256(str(Path.home()).encode()).hexdigest()[:12])]
    for path in choices:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if path.is_dir(): return path
        except OSError:
            continue
    return None


def _secret(root):
    path = root / '.validation-key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        data = path.read_bytes()
    else:
        data = os.urandom(32)
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
    if len(data) != 32: raise ValueError('Invalid cache key')
    return data


def file_digest(path):
    from .cancellation import check_cancelled
    digest = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            check_cancelled(); digest.update(block)
    return digest.hexdigest()


def source_key(descriptor):
    return sha256(json.dumps(descriptor, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load_disk(descriptor):
    if not enabled(): return None
    root = directory()
    if root is None: return None
    key = source_key(descriptor); path = root / (key + '.pnc')
    try:
        if not 36 <= path.stat().st_size <= ENTRY_LIMIT: return None
        data = path.read_bytes()
        if data[:4] != b'PNC1' or not hmac.compare_digest(data[4:36], hmac.digest(_secret(root), data[36:], 'sha256')):
            raise ValueError('Cache authentication failed')
        payload = data[36:]
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if (set(archive.namelist()) != {'vertices.npy', 'faces.npy', 'metadata.npy'}
                    or sum(e.file_size for e in archive.infolist()) > ENTRY_LIMIT):
                raise ValueError('Invalid cache array archive')
        with np.load(io.BytesIO(payload), allow_pickle=False) as arrays:
            v, f = arrays['vertices'], arrays['faces']
            metadata = json.loads(arrays['metadata'].tobytes().decode('utf-8'))
        if metadata['schema'] != SCHEMA or metadata['descriptor'] != descriptor:
            raise ValueError('Cache version or input mismatch')
        if (v.dtype != np.dtype('<f8') or f.dtype != np.dtype('<i8')
                or v.ndim != 2 or v.shape[1:] != (3,) or not len(v)
                or f.ndim != 2 or f.shape[1:] != (3,) or not len(f)
                or not np.isfinite(v).all() or f.min() < 0 or f.max() >= len(v)):
            raise ValueError('Invalid cached geometry')
        record = GeometryRecord(v.tobytes(), f.tobytes(), json.dumps(metadata['trusted_information'], allow_nan=False))
        with _lock: _stats['disk_hits'] += 1
        try: os.utime(path, None)
        except OSError: pass
        return record, metadata
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, EOFError):
        with _lock: _stats['disk_misses'] += 1
        return None


def save_disk(descriptor, model, trusted_information):
    if not enabled(): return
    root = directory()
    if root is None: return
    temporary = None
    try:
        metadata = {'schema': SCHEMA, 'descriptor': descriptor, 'diagnostics': model.diagnostics,
                    'source_unit': model.source_unit, 'tessellation_error': model.tessellation_error,
                    'trusted_information': trusted_information}
        encoded = json.dumps(metadata, allow_nan=False).encode('utf-8')
        payload = io.BytesIO()
        np.savez(payload, vertices=np.asarray(model.mesh.vertices, dtype='<f8'),
                 faces=np.asarray(model.mesh.faces, dtype='<i8'), metadata=np.frombuffer(encoded, dtype=np.uint8))
        data = payload.getvalue()
        if len(data) + 36 > ENTRY_LIMIT: return
        signature = hmac.digest(_secret(root), data, 'sha256')
        with tempfile.NamedTemporaryFile(dir=root, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name); stream.write(b'PNC1' + signature + data)
        os.replace(temporary, root / (source_key(descriptor) + '.pnc'))
        files = sorted(root.glob('*.pnc'), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for file in files:
            total += file.stat().st_size
            if total > DISK_LIMIT: file.unlink(missing_ok=True)
        with _lock: _stats['disk_writes'] += 1
    except (OSError, ValueError, TypeError, MemoryError):
        pass  # Lack of cache space never prevents a successful model import.
    finally:
        if temporary is not None:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass


def clear(*, disk=False):
    global _memory_bytes, _receipt_key
    with _lock:
        _geometry.clear(); _pairs.clear(); _stats.clear(); _memory_bytes = 0
        _receipt_key = os.urandom(32)
    if disk:
        root = directory()
        if root is not None:
            for path in root.glob('*.pnc'):
                try: path.unlink()
                except OSError: pass
