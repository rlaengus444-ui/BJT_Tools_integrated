"""Strict reader for the observed, unsliced CHITUBOX Basic binary layout.

This is deliberately a narrow interoperability reader. The on-disk structure is
documented by Open Resin Alliance's MIT-licensed parser and layout assertions:
https://github.com/Open-Resin-Alliance/df-plugin-chitubox/blob/main/CbxParser.ts
https://github.com/Open-Resin-Alliance/df-plugin-chitubox/blob/main/CbxLayout.test.ts

The format is not a ZIP file. A matching extension alone is never sufficient.
Unmapped native support/raft placement is rejected instead of reporting a
possibly false "no overlap" result. Geometry already contains authored rotation
and scale; each table entry still needs its own plate translation.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import mmap
from pathlib import Path
import struct

import numpy as np
import trimesh

from .geometry import MeshObject


MAGIC = 0xAB231243
MAGIC_BYTES = struct.pack("<I", MAGIC)
INSTANCE_STRIDE = 680
INSTANCE_TAIL = 256
NO_SUPPORT = 0xFFFFFFFF
_VERTEX_CHUNK = 262_144


class ChituboxFormatError(ValueError):
    """An unsupported or inconsistent native project, never a clear result."""


class ChituboxCancelled(ChituboxFormatError):
    """The caller cancelled loading; no complete inspection is implied."""


class _NativeGeometryProvenance:
    """Identity-bound proof that immutable arrays came from this loader.

    A digest in public metadata is insufficient: callers can replace mesh
    arrays while preserving metadata. The geometry validator may reuse a
    certificate only while these exact, read-only buffers remain attached.
    """

    __slots__ = ("digest", "vertices", "faces", "vertex_shape", "face_shape")

    def __init__(self, digest, vertices, faces):
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "faces", faces)
        object.__setattr__(self, "vertex_shape", vertices.shape)
        object.__setattr__(self, "face_shape", faces.shape)

    def __setattr__(self, name, value):
        raise AttributeError("Native geometry provenance is immutable")

    def __deepcopy__(self, memo):
        # Copied mesh arrays will have different identities and fail matches;
        # copying this token must not duplicate another large pair of arrays.
        return self

    def matches(self, mesh):
        return (
            mesh.vertices is self.vertices
            and mesh.faces is self.faces
            and mesh.vertices.shape == self.vertex_shape
            and mesh.faces.shape == self.face_shape
            and not mesh.vertices.flags.writeable
            and not mesh.faces.flags.writeable
        )


def native_geometry_key(mesh):
    """Return verified original geometry identity, or None after replacement."""
    token = mesh.metadata.get("_native_geometry_provenance")
    if isinstance(token, _NativeGeometryProvenance) and token.matches(mesh):
        return token.digest
    return None


def _freeze_array(array):
    # Freeze underlying owners too. A read-only view over a writable owner can
    # otherwise be made writable again with ndarray.setflags(write=True).
    current = array
    while isinstance(current, np.ndarray):
        current.flags.writeable = False
        current = current.base


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ChituboxCancelled("치투 파일 읽기를 중단했습니다.")


def _mix_vertex_hash64(values):
    values ^= values >> np.uint64(30)
    values *= np.uint64(0xBF58476D1CE4E5B9)
    values ^= values >> np.uint64(27)
    values *= np.uint64(0x94D049BB133111EB)
    values ^= values >> np.uint64(31)


def _vertex_hash64(vertices, cancel_event):
    """Create grouping keys; hashes alone are never accepted as equality."""
    bits = chunk = None
    try:
        hashes = np.empty(len(vertices), dtype=np.uint64)
        bits = vertices.view("<u4")
        for start in range(0, len(vertices), _VERTEX_CHUNK):
            _check_cancel(cancel_event)
            chunk = bits[start:start + _VERTEX_CHUNK]
            values = chunk[:, 0].astype(np.uint64)
            values += np.uint64(0x9E3779B97F4A7C15)
            _mix_vertex_hash64(values)
            values ^= chunk[:, 1].astype(np.uint64) + np.uint64(0xD1B54A32D192ED03)
            _mix_vertex_hash64(values)
            values ^= chunk[:, 2].astype(np.uint64) + np.uint64(0x94D049BB133111EB)
            _mix_vertex_hash64(values)
            hashes[start:start + len(chunk)] = values
        return hashes
    finally:
        vertices = bits = chunk = None


def _exact_vertex_index(vertices):
    unique, inverse = np.unique(vertices.view("V12").reshape(-1), return_inverse=True)
    return unique.view("<f4").reshape((-1, 3)), inverse.reshape((-1, 3))


def _verified_vertex_index(vertices, cancel_event):
    """Index by numeric hash, then prove every 96-bit position matches.

    A representative is selected for every hash bucket. Every original point
    is compared byte-for-byte to its representative in bounded chunks. A
    collision therefore cannot merge positions: any mismatch discards this
    result and falls back to exact 12-byte-key indexing.
    """
    original_keys = actual = representative = None
    try:
        hashes = _vertex_hash64(vertices, cancel_event)
        unique_hashes, inverse = np.unique(hashes, return_inverse=True)
        del hashes
        _check_cancel(cancel_event)
        # return_index would force NumPy's slower stable mergesort. Any member
        # of a bucket is valid after the full equality proof below, so fill a
        # representative index in a bounded scatter pass instead.
        first = np.empty(len(unique_hashes), dtype=np.intp)
        del unique_hashes
        for start in range(0, len(vertices), _VERTEX_CHUNK):
            _check_cancel(cancel_event)
            stop = min(start + _VERTEX_CHUNK, len(vertices))
            first[inverse[start:stop]] = np.arange(start, stop, dtype=np.intp)
        unique_vertices = vertices[first]
        del first
        unique_keys = unique_vertices.view("V12").reshape(-1)
        original_keys = vertices.view("V12").reshape(-1)
        collision = False
        for start in range(0, len(vertices), _VERTEX_CHUNK):
            _check_cancel(cancel_event)
            actual = original_keys[start:start + _VERTEX_CHUNK]
            representative = unique_keys[inverse[start:start + _VERTEX_CHUNK]]
            if not np.array_equal(actual, representative):
                collision = True
                break
        if collision:
            del unique_vertices, unique_keys, inverse
            original_keys = actual = representative = None
            _check_cancel(cancel_event)
            return _exact_vertex_index(vertices)
        return unique_vertices, inverse.reshape((-1, 3))
    finally:
        vertices = original_keys = actual = representative = None


def _index_geometry(data, start, byte_count, name, cancel_event):
    """Exact float32 indexing without a float64 triangle-soup allocation.

    Numeric hashing accelerates grouping, but a complete 96-bit position
    equality proof is required before its result is accepted. Signed zero is
    canonicalized first because numeric +0/-0 are the same vertex despite
    their different bit patterns. Every other float32 position remains
    distinct; no tolerance-based weld or geometry repair is performed.
    """
    raw = chunk = None
    try:
        raw = np.frombuffer(data, dtype="<f4", count=byte_count // 4, offset=start).reshape((-1, 3))
        negative_zero = False
        for offset in range(0, len(raw), _VERTEX_CHUNK):
            _check_cancel(cancel_event)
            chunk = raw[offset:offset + _VERTEX_CHUNK]
            if not np.isfinite(chunk).all():
                raise ChituboxFormatError(f"{name}: 삼각형 좌표에 NaN/무한대가 있습니다.")
            if np.any(chunk.view("<u4") == np.uint32(0x80000000)):
                negative_zero = True
        if negative_zero:
            raw = raw.copy()
            for offset in range(0, len(raw), _VERTEX_CHUNK):
                _check_cancel(cancel_event)
                chunk = raw[offset:offset + _VERTEX_CHUNK]
                bits = chunk.view("<u4")
                bits[bits == np.uint32(0x80000000)] = 0
            bits = None
        _check_cancel(cancel_event)
        vertices, faces = _verified_vertex_index(raw, cancel_event)
        _check_cancel(cancel_event)
        _freeze_array(vertices)
        _freeze_array(faces)
        return vertices, faces
    finally:
        # Explicit format/cancel exceptions may keep this frame alive in a
        # traceback. Do not let its views keep a Windows mapping/file locked.
        raw = chunk = None


def is_chitubox_header(header: bytes) -> bool:
    """Recognize the verified container independently of its filename suffix."""
    return header[:4] == MAGIC_BYTES


def read_chitubox(path: str | Path, *, cancel_event=None, progress=None) -> list[MeshObject]:
    """Read supported Basic projects with no native support/raft block.

    The 680-byte instance table is addressed by header field 8. Meshes are
    triangle soup (nine little-endian float32 values per triangle). Clones are
    kept as separate instances even if they reference the same mesh bytes.

    This reader has synthetic format tests, but the calling application's own
    representative projects must still validate interoperability across writer
    versions. In particular, a native-support project is rejected because its
    supported-model Z frame cannot safely be recovered from nominal lift alone.
    """
    project = Path(path)
    _check_cancel(cancel_event)
    # Mapping avoids retaining a second, whole-project bytes allocation. All
    # returned vertex/face buffers are detached from the mapping before close.
    with project.open("rb") as stream:
        if project.stat().st_size == 0:
            return parse_chitubox(b"", source_name=project.name)
        mapped = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return parse_chitubox(mapped, source_name=project.name,
                                  cancel_event=cancel_event, progress=progress)
        finally:
            try:
                mapped.close()
            except BufferError:
                # An exception traceback inside NumPy can temporarily retain
                # an exported view. Let its mapping be collected with that
                # traceback rather than masking the actual format/cancel error.
                pass


def parse_chitubox(data: bytes, *, source_name: str = "project.chitubox",
                   cancel_event=None, progress=None) -> list[MeshObject]:
    """Decode an in-memory project using declared pointers, without scans."""
    size = len(data)
    if size < 16 or not is_chitubox_header(data):
        raise ChituboxFormatError(
            "지원하는 CHITUBOX Basic 프로젝트 바이너리가 아닙니다 "
            "(필요한 매직: 0xAB231243). .ctb 슬라이스와 .ctp Pro는 지원하지 않습니다."
        )

    def u32(offset: int) -> int:
        if offset < 0 or offset + 4 > size:
            raise ChituboxFormatError(f"프로젝트 포인터가 파일 범위를 벗어납니다: {offset}")
        return struct.unpack_from("<I", data, offset)[0]

    count = u32(4)
    table = u32(8)
    pointer_block = u32(12)
    if count == 0:
        return []
    # Bound count before multiplication/allocation. Check the declared complete
    # table, not just a plausible first entry or guessed trailing geometry.
    if table < 16 or count > size // INSTANCE_STRIDE or table + count * INSTANCE_STRIDE > size:
        raise ChituboxFormatError("오브젝트 테이블이 잘렸거나 지원하지 않는 레이아웃입니다.")

    if pointer_block:
        if pointer_block < 16 or pointer_block + 16 > size:
            raise ChituboxFormatError("프로젝트의 포인터 블록이 유효하지 않습니다.")
        delta = u32(pointer_block + 8)
        mesh_section = u32(pointer_block + 12)
        if mesh_section + delta != table:
            raise ChituboxFormatError(
                "프로젝트의 테이블 포인터가 서로 일치하지 않습니다. "
                "이 CHITUBOX 버전의 레이아웃은 지원하지 않습니다."
            )

    _check_cancel(cancel_event)
    headers = []
    table_end = table + count * INSTANCE_STRIDE
    for index in range(count):
        _check_cancel(cancel_event)
        entry = table + index * INSTANCE_STRIDE
        tail = entry + INSTANCE_TAIL
        name_bytes = bytes(data[entry:tail]).split(b"\0", 1)[0]
        name = name_bytes.decode("utf-8", errors="replace") or f"object_{index + 1}"
        plate_x, plate_y, lift_z = struct.unpack_from("<3f", data, tail)
        if not np.isfinite((plate_x, plate_y, lift_z)).all():
            raise ChituboxFormatError(f"{name}: 오브젝트 위치에 NaN/무한대가 있습니다.")

        support_pointer = u32(tail + 12)
        if support_pointer not in (0, NO_SUPPORT):
            raise ChituboxFormatError(
                f"{name}: 별도 서포트/래프트 블록이 있는 프로젝트는 현재 지원하지 않습니다. "
                "서포트와 모델의 실제 Z 배치를 확인할 수 없어 검사를 중단했습니다. "
                "CHITUBOX에서 배치 좌표가 유지된 개별 STL 또는 3MF로 내보내 주세요."
            )

        geometry_start = u32(tail + 16)
        geometry_bytes = u32(tail + 20)
        geometry_end = geometry_start + geometry_bytes
        if (
            geometry_bytes == 0
            or geometry_bytes % 36
            or geometry_start < 16
            or geometry_end > size
            or (geometry_start < table_end and geometry_end > table)
            or (pointer_block and geometry_start < pointer_block + 16 and geometry_end > pointer_block)
        ):
            raise ChituboxFormatError(f"{name}: 삼각형 데이터의 크기/포인터가 유효하지 않습니다.")

        headers.append((index, name, plate_x, plate_y, lift_z, geometry_start, geometry_bytes))

    # Hash directly over mapped spans. Distinct instance records often embed
    # byte-identical meshes at different offsets; index each such mesh once.
    digests = []
    span_hashes = {}
    for index, name, plate_x, plate_y, lift_z, geometry_start, geometry_bytes in headers:
        _check_cancel(cancel_event)
        span = (geometry_start, geometry_bytes)
        if span not in span_hashes:
            view = memoryview(data)[geometry_start:geometry_start + geometry_bytes]
            try:
                span_hashes[span] = hashlib.sha256(view).hexdigest()
            finally:
                view.release()
        digests.append(span_hashes[span])
    remaining = Counter(digests)
    cache = {}
    total_bytes = sum(header[-1] for header in headers)
    finished_bytes = 0
    objects: list[MeshObject] = []
    for header, digest in zip(headers, digests):
        index, name, plate_x, plate_y, lift_z, geometry_start, geometry_bytes = header
        _check_cancel(cancel_event)
        if progress:
            progress(int(100 * finished_bytes / total_bytes),
                     f"오브젝트 {index + 1}/{count} 정점 연결 중: {name}")
        if digest in cache:
            base, faces = cache[digest]
        else:
            base, faces = _index_geometry(data, geometry_start, geometry_bytes, name, cancel_event)
            if remaining[digest] > 1:
                cache[digest] = (base, faces)
        vertices = base.astype(np.float64)
        # The raw soup already contains rotation and scaling. Unsupported
        # objects sit on the plate at their authored nominal Z lift. Keeping
        # entries independent is essential when clones share geometry bytes.
        vertices[:, 0] += plate_x
        vertices[:, 1] += plate_y
        vertices[:, 2] += lift_z - vertices[:, 2].min()
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        _freeze_array(mesh.vertices)
        _freeze_array(mesh.faces)
        mesh.metadata.update(
            source_format="chitubox-basic",
            source_name=source_name,
            source_instance=index,
            source_geometry_offset=geometry_start,
            source_geometry_bytes=geometry_bytes,
            source_plate_translation=[float(plate_x), float(plate_y), float(lift_z)],
            native_supports=False,
            geometry_digest=digest,
            loader_weld="exact-float32",
            loader_indexing="verified-hash64-with-exact-fallback",
        )
        mesh.metadata["_native_geometry_provenance"] = _NativeGeometryProvenance(
            digest, mesh.vertices, mesh.faces)
        objects.append(MeshObject(name=name, mesh=mesh))
        remaining[digest] -= 1
        if remaining[digest] == 0:
            cache.pop(digest, None)
        finished_bytes += geometry_bytes
        if progress:
            progress(int(100 * finished_bytes / total_bytes),
                     f"오브젝트 {index + 1}/{count} 읽기 완료: {name}")
    _check_cancel(cancel_event)
    return objects
