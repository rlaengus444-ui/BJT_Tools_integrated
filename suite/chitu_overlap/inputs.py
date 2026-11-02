"""Native project loading, with explicit per-object STL export fallback."""

from pathlib import Path

import numpy as np
import trimesh

from .chitubox import ChituboxCancelled, ChituboxFormatError, is_chitubox_header, read_chitubox
from .geometry import MeshObject

SURFACE_INDEX_WORKSPACE_BYTES = 256 * 1024 * 1024


def _surface_wrapper(vertices, faces, indexing):
    # Independent metadata/cache over borrowed immutable views, not a stale
    # copy of native provenance or derived source properties.
    vertices = vertices.view()
    faces = faces.view()
    vertices.flags.writeable = faces.flags.writeable = False
    result = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    result.metadata["surface_indexing"] = indexing
    return result


def _exact_surface_index(mesh, cancel_event):
    """Compact identical float64 positions, retaining every original triangle.

    This is an equivalent representation, not a tolerance weld or repair.
    Signed zeros may share an index; all other coordinate bits stay distinct.
    Reconstruction is checked in chunks without expanding the whole soup.
    """
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise ChituboxCancelled("STL 표면 좌표 인덱싱을 중단했습니다.")
    cancelled()
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or faces.ndim != 2 or faces.shape[1] != 3
            or not len(vertices) or not len(faces) or not np.issubdtype(faces.dtype, np.integer)):
        raise ValueError("STL에 비어 있지 않은 삼각형 좌표와 인덱스가 필요합니다.")
    negative_zero = False
    for start in range(0, len(vertices), 262_144):
        cancelled()
        chunk = vertices[start:start+262_144]
        if not np.isfinite(chunk).all():
            raise ValueError("STL 삼각형 좌표에 NaN/무한대가 있습니다.")
        negative_zero |= bool(np.any((chunk == 0) & np.signbit(chunk)))
    for start in range(0, len(faces), 65_536):
        cancelled()
        chunk = faces[start:start+65_536]
        if np.any(chunk < 0) or np.any(chunk >= len(vertices)):
            raise ValueError("STL 삼각형 인덱스가 유효하지 않습니다.")
    # np.unique needs linear sorting/inverse buffers. Refuse that optional
    # optimization when its conservative workspace estimate exceeds the cap;
    # original triangle-soup distance queries can still proceed unchanged.
    workspace = len(vertices) * (24 * 3 + 8 * 3 + 1) + faces.size * 8
    if negative_zero:
        workspace += len(vertices) * 24
    if workspace > SURFACE_INDEX_WORKSPACE_BYTES:
        cancelled()
        return _surface_wrapper(vertices, faces, {"method": "original-soup-workspace-fallback",
            "reason": "exact indexing exceeds the workspace budget", "estimated_workspace_bytes": workspace,
            "workspace_budget_bytes": SURFACE_INDEX_WORKSPACE_BYTES, "raw_vertex_count": len(vertices),
            "indexed_vertex_count": len(vertices), "triangle_count": len(faces),
            "triangle_order_preserved": True, "triangle_coordinates_preserved": True,
            "signed_zero_canonicalized": False})
    positions = np.ascontiguousarray(vertices, dtype=np.float64)
    if negative_zero:
        positions = positions.copy()
        for start in range(0, len(positions), 262_144):
            cancelled()
            chunk = positions[start:start+262_144]
            chunk[chunk == 0] = 0.0
    cancelled()
    unique, inverse = np.unique(positions.view("V24").reshape(-1), return_inverse=True)
    cancelled()
    indexed_vertices = unique.view(np.float64).reshape((-1, 3))
    indexed_faces = np.ascontiguousarray(inverse.reshape(-1)[faces], dtype=np.int64)
    for start in range(0, len(faces), 65_536):
        cancelled()
        if not np.array_equal(indexed_vertices[indexed_faces[start:start+65_536]],
                              vertices[faces[start:start+65_536]]):
            raise ValueError("STL exact 인덱싱에서 원본 삼각형 좌표 동등성을 확인하지 못했습니다.")
    cancelled()
    # A new mesh receives no stale caches or native CHITUBOX provenance token.
    result = _surface_wrapper(indexed_vertices, indexed_faces, {"method": "exact-float64-position-indexing",
        "raw_vertex_count": len(vertices), "indexed_vertex_count": len(indexed_vertices),
        "triangle_count": len(faces), "triangle_order_preserved": True,
        "triangle_coordinates_preserved": True, "signed_zero_canonicalized": negative_zero,
        "estimated_workspace_bytes": workspace, "workspace_budget_bytes": SURFACE_INDEX_WORKSPACE_BYTES})
    return result


def load_objects(paths: list[Path], *, cancel_event=None, progress=None, preserve_surface=False) -> tuple[list[MeshObject], str]:
    if cancel_event is not None and cancel_event.is_set():
        raise ChituboxCancelled("파일 읽기를 중단했습니다.")
    if len(paths) == 1 and paths[0].is_dir():
        paths = sorted(
            (p for p in paths[0].iterdir() if p.is_file() and p.suffix.lower() == ".stl"),
            key=lambda p: p.name,
        )
        if not paths:
            raise ValueError("폴더 안에 개별 오브젝트 STL 파일이 없습니다.")
    if len(paths) == 1 and paths[0].suffix.lower() != ".stl":
        with paths[0].open("rb") as stream:
            header = stream.read(4)
        if is_chitubox_header(header):
            return read_chitubox(paths[0], cancel_event=cancel_event, progress=progress), "chitubox-basic"
        raise ChituboxFormatError(
            "지원하는 CHITUBOX Basic 바이너리(매직 0xAB231243)가 아닙니다. "
            "확장자만으로 형식을 추정하지 않습니다. .ctb/.ctp는 지원하지 않습니다."
        )
    if not paths or any(p.suffix.lower() != ".stl" for p in paths):
        raise ValueError("프로젝트 한 개, STL 파일 여러 개 또는 STL 폴더를 입력하세요.")
    objects = []
    for index, path in enumerate(paths):
        if cancel_event is not None and cancel_event.is_set():
            raise ChituboxCancelled("STL 파일 읽기를 중단했습니다.")
        if progress:
            progress(100 * index / len(paths), f"STL {index + 1}/{len(paths)} 읽는 중: {path.name}")
        mesh = trimesh.load_mesh(str(path), file_type="stl", process=not preserve_surface)
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(f"{path}: 단일 STL 메쉬로 읽을 수 없습니다.")
        if preserve_surface:
            mesh = _exact_surface_index(mesh, cancel_event)
        # One file is one source object, even with disconnected components.
        # Never center or align exports: their shared plate frame is essential.
        objects.append(MeshObject(name=path.name, mesh=mesh))
    if progress:
        progress(100, f"STL 오브젝트 {len(objects)}개 읽기 완료")
    return objects, "stl-per-object"
