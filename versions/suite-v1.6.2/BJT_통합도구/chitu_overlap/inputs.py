"""Native project loading, with explicit per-object STL export fallback."""

from pathlib import Path

import trimesh

from .chitubox import ChituboxCancelled, ChituboxFormatError, is_chitubox_header, read_chitubox
from .geometry import MeshObject


def load_objects(paths: list[Path], *, cancel_event=None, progress=None) -> tuple[list[MeshObject], str]:
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
        mesh = trimesh.load_mesh(str(path), file_type="stl", process=True)
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(f"{path}: 단일 STL 메쉬로 읽을 수 없습니다.")
        # One file is one source object, even with disconnected components.
        # Never center or align exports: their shared plate frame is essential.
        objects.append(MeshObject(name=path.name, mesh=mesh))
    if progress:
        progress(100, f"STL 오브젝트 {len(objects)}개 읽기 완료")
    return objects, "stl-per-object"
