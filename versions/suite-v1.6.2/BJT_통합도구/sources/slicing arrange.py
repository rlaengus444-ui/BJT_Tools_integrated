#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
슬라이싱 이미지·STL 배치·합성 프로그램 (Tkinter STL 슬라이싱 v11)

주요 기능
- 최종 캔버스 해상도와 DPI 설정 및 다음 실행 시 자동 복원
- 이미지 세트 폴더 선택 및 Tk 이벤트 기반 폴더 드래그앤드롭
- 각 폴더 이미지의 자연 정렬 후 동일 순번 레이어끼리 고속 합성
- 복사본은 동일 원본을 레이어당 한 번만 읽는 공유 캐시 사용
- 메모리 한도 기반 다중 레이어 병렬 처리
- STL 파일을 DPI/레이어 두께 기준으로 내부 슬라이싱해 이미지 세트처럼 배치
- 최종 출력 파일명은 1.png, 2.png, 3.png ... 형식으로 저장
- 캔버스에서 배치 이미지 드래그 이동
- X/Y 좌표 직접 입력 및 방향키 미세 이동
- 선택 배치 Ctrl+C/Ctrl+V 또는 Ctrl+D 내부 복제
- Ctrl+Z로 직전 배치 상태 복구
- Alt+마우스 휠로 선택 이미지 5° 단위 고속 회전 미리보기
- 미리보기 패널의 휠/스크럽 바로 레이어 연속 탐색
- 캔버스 밖 자동 클리핑, 빈 영역 검정
- MAX/OR 또는 덮어쓰기 합성
- 짧은 세트 기준 또는 긴 세트 기준 출력
- 입력 폴더명 공통부를 이용한 기본 출력 폴더 자동 생성

PySide6/Qt와 windnd를 사용하지 않습니다. 드래그앤드롭은 Tk 이벤트 루프 안에서 처리합니다.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import importlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple


APP_NAME = "슬라이싱 이미지 배치·합성"
SETTINGS_DIR_NAME = "SlicingArrange"
SETTINGS_FILE_NAME = "settings.json"
IMAGE_EXTENSIONS = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".webp"}
STL_EXTENSIONS = {".stl"}
STL_CACHE_VERSION = 1


def get_settings_path() -> Path:
    """사용자별 설정 파일 경로를 반환한다."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / SETTINGS_DIR_NAME / SETTINGS_FILE_NAME


def _clean_folder_title(text: str) -> str:
    """출력 폴더명으로 사용할 수 있도록 공통 제목을 정리한다."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = text.strip().strip(" ._-–—()[]{}")
    text = re.sub(r"[\s._-]+$", "", text)
    return text.strip().strip(" ._-–—()[]{}")


def common_folder_title(names: Sequence[str]) -> str:
    """폴더명들의 가장 자연스러운 공통부를 구한다."""
    names = [str(name).strip() for name in names if str(name).strip()]
    if not names:
        return "슬라이싱이미지"
    if len(names) == 1:
        return _clean_folder_title(names[0]) or "슬라이싱이미지"

    folded = [name.casefold() for name in names]
    prefix_folded = os.path.commonprefix(folded)
    raw_prefix = names[0][: len(prefix_folded)]
    # 숫자로 된 가변 접미사의 일부(예: 001/002에서 공통인 00)는 제목에서 제외한다.
    if (
        prefix_folded
        and prefix_folded[-1].isdigit()
        and all(len(value) > len(prefix_folded) and value[len(prefix_folded)].isdigit() for value in folded)
    ):
        raw_prefix = re.sub(r"\d+$", "", raw_prefix)
    prefix = _clean_folder_title(raw_prefix)
    if len(prefix) >= 2:
        return prefix

    # 시작 부분이 서로 다르면 가장 긴 공통 부분 문자열을 찾는다.
    shortest_index = min(range(len(names)), key=lambda i: len(names[i]))
    shortest = names[shortest_index]
    shortest_folded = folded[shortest_index]
    for size in range(len(shortest), 1, -1):
        for start in range(0, len(shortest) - size + 1):
            candidate_folded = shortest_folded[start : start + size]
            if all(candidate_folded in value for value in folded):
                candidate = _clean_folder_title(shortest[start : start + size])
                # 서로 다른 번호 앞뒤에서 우연히 겹친 숫자 조각은 제거한다.
                candidate = re.sub(r"^\d+[\s._-]+(?=.*[A-Za-z가-힣])", "", candidate)
                candidate = re.sub(r"(?<=[A-Za-z가-힣])[\s._-]+\d+$", "", candidate)
                candidate = _clean_folder_title(candidate)
                if len(candidate) >= 2 and any(ch.isalnum() for ch in candidate):
                    return candidate
    return "이미지세트"


def _ensure_import(import_name: str, pip_name: str, required: bool = True):
    """필요한 순수 Python/일반 패키지만 현재 인터프리터에 설치한다."""
    try:
        return importlib.import_module(import_name)
    except Exception as first_error:
        try:
            print(f"[{APP_NAME}] {pip_name} 패키지를 설치합니다...")
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", pip_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
            if output.strip():
                print(output[-6000:])
            if completed.returncode != 0:
                raise RuntimeError(f"pip 종료 코드: {completed.returncode}")
            importlib.invalidate_caches()
            return importlib.import_module(import_name)
        except Exception as install_error:
            if required:
                raise RuntimeError(
                    f"{pip_name} 설치 또는 불러오기에 실패했습니다.\n"
                    f'명령 프롬프트에서 다음 명령을 실행해 주세요:\n\n'
                    f'  "{sys.executable}" -m pip install --upgrade {pip_name}\n\n'
                    f"최초 오류: {first_error}\n설치 오류: {install_error}"
                ) from install_error
            return None


try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:
    raise RuntimeError(
        "Tkinter를 불러올 수 없습니다. Anaconda 환경에서는 보통 기본 포함됩니다.\n"
        "Anaconda Prompt에서 `conda install tk`를 실행한 뒤 다시 시도하세요.\n\n"
        f"원래 오류: {exc}"
    ) from exc


_ensure_import("PIL", "Pillow", required=True)
from PIL import Image, ImageChops, ImageDraw, ImageTk

# NumPy가 있으면 대형 이미지와 다수 복사본 합성을 배열 슬라이스에서 직접 처리한다.
# 설치 실패 시에도 Pillow 캐시 경로로 정상 동작한다.
_np_module = _ensure_import("numpy", "numpy", required=False)
np = _np_module

# STL 슬라이싱은 trimesh의 다중 평면 단면과 Path2D 래스터화를 사용한다.
# STL 기능만 사용하지 않는 경우에는 설치 실패가 이미지 폴더 기능을 막지 않는다.
_trimesh_module = _ensure_import("trimesh", "trimesh", required=False)
trimesh = _trimesh_module


# 폴더 드래그앤드롭은 tkinterdnd2/tkdnd를 사용한다.
# windnd처럼 별도 Python 스레드나 ctypes 콜백에서 Tk를 호출하지 않고,
# Tk 이벤트 루프 안에서 <<Drop>> 이벤트를 전달하므로 GIL/Tk 충돌을 피한다.
_tkdnd_module = _ensure_import("tkinterdnd2", "tkinterdnd2", required=False)
if _tkdnd_module is not None:
    DND_FILES = _tkdnd_module.DND_FILES
    TkinterDnD = _tkdnd_module.TkinterDnD
else:
    DND_FILES = None
    TkinterDnD = None


try:
    RESAMPLE_NEAREST = Image.Resampling.NEAREST
except AttributeError:  # Pillow < 9.1
    RESAMPLE_NEAREST = Image.NEAREST


@dataclass
class SourceSet:
    source_id: str
    folder: Path
    images: List[Path]
    kind: str = "folder"
    original_path: Optional[Path] = None
    display_name: str = ""
    dpi_x: int = 0
    dpi_y: int = 0
    layer_height_mm: float = 0.0
    model_size_mm: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def name(self) -> str:
        if self.display_name:
            return self.display_name
        if self.kind == "stl" and self.original_path is not None:
            return self.original_path.stem
        return self.folder.name

    @property
    def origin_path(self) -> Path:
        return self.original_path if self.original_path is not None else self.folder


def clone_source_set(source: SourceSet) -> SourceSet:
    return SourceSet(
        source_id=source.source_id,
        folder=source.folder,
        images=list(source.images),
        kind=source.kind,
        original_path=source.original_path,
        display_name=source.display_name,
        dpi_x=source.dpi_x,
        dpi_y=source.dpi_y,
        layer_height_mm=source.layer_height_mm,
        model_size_mm=tuple(source.model_size_mm),
    )


@dataclass
class Placement:
    placement_id: str
    source_id: str
    label: str
    x: int = 0
    y: int = 0
    visible: bool = True
    # 사용자 표시 기준 시계방향 각도. 최종 합성과 미리보기에 동일하게 적용한다.
    rotation: int = 0


@dataclass
class LayoutState:
    """Ctrl+Z로 복구할 수 있는 가벼운 배치 상태 스냅샷."""
    sources: Dict[str, SourceSet]
    source_by_folder: Dict[str, str]
    placements: Dict[str, Placement]
    placement_order: List[str]
    selected_id: Optional[str]
    copied_id: Optional[str]
    description: str = "배치 변경"


def natural_key(path: Path) -> List[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def list_images(folder: Path) -> List[Path]:
    try:
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    except OSError:
        return []
    return sorted(files, key=natural_key)


def get_stl_cache_root() -> Path:
    """STL에서 생성한 내부 슬라이스 캐시 위치를 반환한다."""
    return get_settings_path().parent / "stl_cache"


def _stl_cache_identity(stl_path: Path, dpi_x: int, dpi_y: int, layer_height_mm: float) -> str:
    stat = stl_path.stat()
    raw = "|".join(
        (
            str(stl_path.resolve()),
            str(stat.st_size),
            str(stat.st_mtime_ns),
            str(int(dpi_x)),
            str(int(dpi_y)),
            f"{float(layer_height_mm):.9f}",
            str(STL_CACHE_VERSION),
        )
    )
    return hashlib.sha1(raw.encode("utf-8", errors="surrogatepass")).hexdigest()


def _load_stl_cache_manifest(cache_dir: Path) -> Optional[dict]:
    manifest_path = cache_dir / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        count = int(data.get("layer_count", 0))
        if count <= 0:
            return None
        images = [cache_dir / f"{index}.png" for index in range(1, count + 1)]
        if not all(path.is_file() for path in images):
            return None
        data["images"] = images
        return data
    except Exception:
        return None


def _rasterize_section_even_odd(
    section,
    width_px: int,
    height_px: int,
    pitch_x: float,
    pitch_y: float,
) -> Image.Image:
    """Path2D의 폐곡선을 even-odd 규칙으로 이진 래스터화한다.

    trimesh의 기본 fill 경로는 환경에 따라 rtree가 필요할 수 있다. 이 함수는
    Path2D.discrete의 폐곡선을 Pillow로 직접 그린 뒤 XOR하여 외곽/구멍/중첩을
    별도 공간 인덱스 패키지 없이 처리한다.
    """
    result = Image.new("1", (width_px, height_px), 0)
    for loop in section.discrete:
        points_array = np.asarray(loop, dtype=float)
        if points_array.ndim != 2 or points_array.shape[0] < 3:
            continue
        # 수치 오차로 마지막 점이 첫 점과 아주 조금 다를 수 있으므로 강제로 닫는다.
        if not np.allclose(points_array[0], points_array[-1], rtol=0.0, atol=1e-7):
            points_array = np.vstack((points_array, points_array[0]))
        x_pixels = points_array[:, 0] / pitch_x
        # 모델 +Y가 화면 위쪽이 되도록 max-Y 기준으로 뒤집는다.
        y_pixels = height_px - (points_array[:, 1] / pitch_y)
        points = [(int(round(x)), int(round(y))) for x, y in zip(x_pixels, y_pixels)]
        polygon_mask = Image.new("1", (width_px, height_px), 0)
        ImageDraw.Draw(polygon_mask).polygon(points, fill=1)
        result = ImageChops.logical_xor(result, polygon_mask)
    return result.convert("L").point(lambda value: 255 if value else 0)


def slice_stl_to_cache(
    stl_path: Path,
    dpi_x: int,
    dpi_y: int,
    layer_height_mm: float,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """STL을 수평 단면으로 슬라이싱하고 동일 XY 크기의 PNG 캐시를 만든다.

    STL 좌표 단위는 mm로 해석한다. 각 레이어는 층 중앙 높이에서 자르며, 모델의
    전체 XY 바운딩 박스를 모든 레이어에 공통 사용한다. 따라서 배치 회전 중심이
    층별 형상 변화와 무관하게 일정하다.
    """
    if trimesh is None:
        raise RuntimeError(
            "STL 슬라이싱에는 trimesh가 필요합니다.\n\n"
            f'  "{sys.executable}" -m pip install --upgrade trimesh'
        )
    if np is None:
        raise RuntimeError("STL 슬라이싱에는 NumPy가 필요합니다.")
    if dpi_x <= 0 or dpi_y <= 0 or layer_height_mm <= 0:
        raise ValueError("DPI와 STL 레이어 두께는 0보다 커야 합니다.")

    stl_path = stl_path.expanduser().resolve()
    cache_key = _stl_cache_identity(stl_path, dpi_x, dpi_y, layer_height_mm)
    cache_root = get_stl_cache_root()
    cache_dir = cache_root / cache_key
    cached = _load_stl_cache_manifest(cache_dir)
    if cached is not None:
        cached["cache_dir"] = cache_dir
        cached["cached"] = True
        if progress_callback:
            progress_callback(int(cached["layer_count"]), int(cached["layer_count"]), "캐시 재사용")
        return cached

    if progress_callback:
        progress_callback(0, 1, "STL 메시 읽는 중")

    loaded = trimesh.load_mesh(str(stl_path), file_type="stl", process=True)
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.to_mesh()
    if isinstance(loaded, (list, tuple)):
        loaded = trimesh.util.concatenate(tuple(loaded))
    if not isinstance(loaded, trimesh.Trimesh) or loaded.faces.size == 0:
        raise ValueError("STL에서 유효한 삼각형 메시를 찾지 못했습니다.")

    mesh = loaded.copy()
    mesh.remove_unreferenced_vertices()
    bounds = np.asarray(mesh.bounds, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError("STL 바운딩 박스를 계산할 수 없습니다.")
    extents = bounds[1] - bounds[0]
    if np.any(extents <= 0):
        raise ValueError(f"STL 크기가 유효하지 않습니다: {extents.tolist()}")

    # 모델의 최소점을 (0, 0, 0)으로 옮겨 모든 레이어가 같은 로컬 좌표를 사용한다.
    mesh.apply_translation(-bounds[0])
    width_mm, height_mm, model_height_mm = (float(v) for v in extents)
    pitch_x = 25.4 / float(dpi_x)
    pitch_y = 25.4 / float(dpi_y)
    width_px = max(1, ceil_with_tolerance(width_mm / pitch_x))
    height_px = max(1, ceil_with_tolerance(height_mm / pitch_y))
    layer_count = max(1, ceil_with_tolerance(model_height_mm / float(layer_height_mm)))

    # 비정상 설정으로 시스템 메모리가 고갈되는 것을 막는다.
    if width_px > 200000 or height_px > 200000 or width_px * height_px > 2_000_000_000:
        raise ValueError(
            f"STL 슬라이스 해상도가 너무 큽니다: {width_px} × {height_px} px. "
            "DPI 또는 모델 단위를 확인하세요."
        )

    temp_dir = cache_root / f".{cache_key}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        heights = (np.arange(layer_count, dtype=float) + 0.5) * float(layer_height_mm)
        # 마지막 층의 중앙이 모델 높이를 넘는 경우 상단 바로 아래에서 절단한다.
        epsilon = max(1e-9, model_height_mm * 1e-10)
        heights = np.minimum(heights, max(epsilon, model_height_mm - epsilon))

        batch_size = 96
        black = Image.new("L", (width_px, height_px), 0)
        for batch_start in range(0, layer_count, batch_size):
            batch_end = min(layer_count, batch_start + batch_size)
            batch_heights = heights[batch_start:batch_end]
            sections = mesh.section_multiplane(
                plane_origin=np.array([0.0, 0.0, 0.0]),
                plane_normal=np.array([0.0, 0.0, 1.0]),
                heights=batch_heights,
            )
            for local_index, section in enumerate(sections):
                layer_number = batch_start + local_index + 1
                output_path = temp_dir / f"{layer_number}.png"
                if section is None or len(section.entities) == 0:
                    image = black
                else:
                    image = _rasterize_section_even_odd(
                        section,
                        width_px=width_px,
                        height_px=height_px,
                        pitch_x=pitch_x,
                        pitch_y=pitch_y,
                    )
                image.save(output_path, format="PNG", optimize=False, compress_level=1)
                if progress_callback:
                    progress_callback(layer_number, layer_count, f"레이어 {layer_number}/{layer_count}")

        manifest = {
            "version": STL_CACHE_VERSION,
            "source_path": str(stl_path),
            "dpi_x": int(dpi_x),
            "dpi_y": int(dpi_y),
            "layer_height_mm": float(layer_height_mm),
            "layer_count": int(layer_count),
            "image_width": int(width_px),
            "image_height": int(height_px),
            "model_size_mm": [width_mm, height_mm, model_height_mm],
            "watertight": bool(mesh.is_watertight),
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.replace(cache_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    result = _load_stl_cache_manifest(cache_dir)
    if result is None:
        raise RuntimeError("STL 슬라이스 캐시 생성 후 검증에 실패했습니다.")
    result["cache_dir"] = cache_dir
    result["cached"] = False
    return result


def load_grayscale(path: Path) -> Image.Image:
    """투명 영역을 검정으로 합성한 8-bit grayscale 이미지를 반환한다."""
    with Image.open(path) as original:
        if "A" in original.getbands():
            rgba = original.convert("RGBA")
            black = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            composed = Image.alpha_composite(black, rgba)
            return composed.convert("L")
        return original.convert("L")


def load_grayscale_array(path: Path):
    """파일을 바로 연속 uint8 배열로 읽어 중간 Pillow 복사를 최소화한다."""
    if np is None:
        raise RuntimeError("NumPy를 사용할 수 없습니다.")
    with Image.open(path) as original:
        if "A" in original.getbands():
            rgba = original.convert("RGBA")
            black = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            gray = Image.alpha_composite(black, rgba).convert("L")
        else:
            gray = original.convert("L")
        return np.array(gray, dtype=np.uint8, copy=True)


def normalize_rotation(angle: int) -> int:
    """시계방향 각도를 0~359 범위 정수로 정규화한다."""
    return int(round(angle)) % 360


def rotate_grayscale(image: Image.Image, clockwise_degrees: int) -> Image.Image:
    """검정 배경, 최근접 보간으로 이미지를 중심 기준 회전한다."""
    angle = normalize_rotation(clockwise_degrees)
    if angle == 0:
        return image
    # Pillow 양의 각도는 반시계방향이므로 사용자 기준 시계방향 각도에 음수를 쓴다.
    return image.rotate(-angle, resample=RESAMPLE_NEAREST, expand=True, fillcolor=0)


def rotate_grayscale_array(image_array, clockwise_degrees: int):
    """uint8 배열 회전. 90° 배수는 NumPy 경로를 사용해 최대한 빠르게 처리한다."""
    angle = normalize_rotation(clockwise_degrees)
    if angle == 0:
        return image_array
    if np is not None and angle % 90 == 0:
        # np.rot90의 양수 k는 반시계방향이므로 시계방향은 음수 k이다.
        k = (-(angle // 90)) % 4
        return np.ascontiguousarray(np.rot90(image_array, k=k))
    rotated = rotate_grayscale(Image.fromarray(image_array, mode="L"), angle)
    return np.array(rotated, dtype=np.uint8, copy=True)


def rotated_size(width: int, height: int, clockwise_degrees: int) -> Tuple[int, int]:
    """회전 후 바운딩 박스 크기를 픽셀 이미지 생성 없이 즉시 계산한다.

    이전 버전은 Alt+휠 한 칸마다 원본 해상도 크기의 빈 Pillow 이미지를 만들고
    rotate(expand=True)를 호출했다. 수천 px 이미지에서는 이 크기 계산 자체가 큰 병목이므로
    삼각함수만 사용하는 O(1) 계산으로 바꾼다.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    angle = normalize_rotation(clockwise_degrees)
    if angle == 0 or angle == 180:
        return width, height
    if angle == 90 or angle == 270:
        return height, width
    radians = math.radians(angle)
    cosine = abs(math.cos(radians))
    sine = abs(math.sin(radians))
    return (
        max(1, int(math.ceil(width * cosine + height * sine))),
        max(1, int(math.ceil(width * sine + height * cosine))),
    )


def ceil_with_tolerance(value: float, tolerance: float = 1e-6) -> int:
    """STL float32 저장 오차로 정수 배수가 한 층/픽셀 늘어나는 것을 방지한다."""
    value = float(value)
    nearest = round(value)
    if abs(value - nearest) <= tolerance * max(1.0, abs(value)):
        return int(nearest)
    return int(math.ceil(value))


def clipped_box(
    canvas_w: int,
    canvas_h: int,
    image_w: int,
    image_h: int,
    x: int,
    y: int,
) -> Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int]]]:
    """소스 crop box와 목적지 좌표를 반환한다."""
    dst_x0 = max(0, x)
    dst_y0 = max(0, y)
    dst_x1 = min(canvas_w, x + image_w)
    dst_y1 = min(canvas_h, y + image_h)
    if dst_x0 >= dst_x1 or dst_y0 >= dst_y1:
        return None

    src_x0 = dst_x0 - x
    src_y0 = dst_y0 - y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    return (src_x0, src_y0, src_x1, src_y1), (dst_x0, dst_y0)


def compose_layer(
    canvas_size: Tuple[int, int],
    sources: Dict[str, SourceSet],
    placements: Sequence[Placement],
    layer_index: int,
    merge_mode: str,
) -> Image.Image:
    """한 레이어를 빠르게 합성한다.

    핵심 최적화
    - 동일 source_id의 복사본은 원본 파일을 레이어당 한 번만 디코딩한다.
    - NumPy 사용 가능 시 crop 이미지와 기존 영역을 매번 만들지 않고 배열 view에 직접 합성한다.
    - NumPy가 없더라도 Pillow 이미지 캐시로 중복 디코딩을 제거한다.
    """
    canvas_w, canvas_h = canvas_size

    if np is not None:
        output_array = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        # 원본 디코딩은 source_id별 한 번, 회전 결과는 (source_id, angle)별 한 번만 수행한다.
        array_cache: Dict[str, object] = {}
        rotated_array_cache: Dict[Tuple[str, int], object] = {}

        for placement in placements:
            if not placement.visible:
                continue
            source = sources.get(placement.source_id)
            if source is None or layer_index >= len(source.images):
                continue

            base_array = array_cache.get(placement.source_id)
            if base_array is None:
                # copy=True에 해당하는 연속 배열을 만들어 Image 파일 핸들과 완전히 분리한다.
                base_array = load_grayscale_array(source.images[layer_index])
                array_cache[placement.source_id] = base_array

            rotation = normalize_rotation(placement.rotation)
            rotation_key = (placement.source_id, rotation)
            image_array = rotated_array_cache.get(rotation_key)
            if image_array is None:
                image_array = rotate_grayscale_array(base_array, rotation)
                rotated_array_cache[rotation_key] = image_array

            image_h, image_w = image_array.shape
            region = clipped_box(
                canvas_w,
                canvas_h,
                image_w,
                image_h,
                placement.x,
                placement.y,
            )
            if region is None:
                continue

            (src_x0, src_y0, src_x1, src_y1), (dst_x0, dst_y0) = region
            src_view = image_array[src_y0:src_y1, src_x0:src_x1]
            dst_y1 = dst_y0 + src_view.shape[0]
            dst_x1 = dst_x0 + src_view.shape[1]
            dst_view = output_array[dst_y0:dst_y1, dst_x0:dst_x1]

            if merge_mode == "overwrite":
                dst_view[...] = src_view
            else:
                np.maximum(dst_view, src_view, out=dst_view)

        return Image.fromarray(output_array, mode="L")

    # NumPy를 사용할 수 없는 환경의 Pillow 최적화 경로.
    output = Image.new("L", (canvas_w, canvas_h), 0)
    image_cache: Dict[str, Image.Image] = {}
    rotated_image_cache: Dict[Tuple[str, int], Image.Image] = {}

    for placement in placements:
        if not placement.visible:
            continue
        source = sources.get(placement.source_id)
        if source is None or layer_index >= len(source.images):
            continue

        base_image = image_cache.get(placement.source_id)
        if base_image is None:
            base_image = load_grayscale(source.images[layer_index])
            image_cache[placement.source_id] = base_image

        rotation = normalize_rotation(placement.rotation)
        rotation_key = (placement.source_id, rotation)
        image = rotated_image_cache.get(rotation_key)
        if image is None:
            image = rotate_grayscale(base_image, rotation)
            rotated_image_cache[rotation_key] = image

        region = clipped_box(
            canvas_w,
            canvas_h,
            image.width,
            image.height,
            placement.x,
            placement.y,
        )
        if region is None:
            continue

        source_box, destination = region
        cropped = image.crop(source_box)
        if merge_mode == "overwrite":
            output.paste(cropped, destination)
        else:
            x0, y0 = destination
            x1, y1 = x0 + cropped.width, y0 + cropped.height
            existing = output.crop((x0, y0, x1, y1))
            output.paste(ImageChops.lighter(existing, cropped), destination)

    return output


def output_names_from_sources(
    sources: Dict[str, SourceSet],
    placements: Sequence[Placement],
    total: int,
) -> List[str]:
    """최종 합성 파일명을 1.png, 2.png, 3.png ... 형식으로 만든다."""
    return [f"{index + 1}.png" for index in range(max(0, int(total)))]


def save_merged_image(image: Image.Image, output_path: Path, dpi: Tuple[int, int]) -> None:
    """원본 파일 확장자를 유지하면서 속도 우선 옵션으로 저장한다."""
    suffix = output_path.suffix.lower()
    if suffix == ".png":
        image.save(output_path, format="PNG", dpi=dpi, optimize=False, compress_level=1)
    elif suffix == ".bmp":
        image.save(output_path, format="BMP", dpi=dpi)
    elif suffix in {".tif", ".tiff"}:
        image.save(output_path, format="TIFF", dpi=dpi, compression="raw")
    elif suffix in {".jpg", ".jpeg"}:
        image.save(output_path, format="JPEG", dpi=dpi, quality=95, subsampling=0, optimize=False)
    elif suffix == ".webp":
        image.save(output_path, format="WEBP", dpi=dpi, lossless=True, method=0)
    else:
        # 알 수 없는 확장자는 데이터 형식과 확장자가 어긋나지 않도록 PNG 확장자로 교체한다.
        output_path = output_path.with_suffix(".png")
        image.save(output_path, format="PNG", dpi=dpi, optimize=False, compress_level=1)


def recommended_export_workers(canvas_size: Tuple[int, int]) -> int:
    """캔버스 메모리 사용량을 고려한 안전한 자동 병렬 작업 수."""
    cpu_count = max(1, os.cpu_count() or 1)
    canvas_w, canvas_h = canvas_size
    # 출력 배열, 디코딩 배열 및 저장용 버퍼를 넉넉하게 4 byte/pixel로 추산한다.
    bytes_per_worker = max(1, canvas_w * canvas_h * 4)
    memory_budget = 384 * 1024 * 1024
    memory_limited = max(1, memory_budget // bytes_per_worker)
    return max(1, min(6, cpu_count, memory_limited))


class SliceLayoutApp:
    MIN_ZOOM = 0.02
    MAX_ZOOM = 8.0

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1500x900")
        self.root.minsize(1050, 680)

        self.sources: Dict[str, SourceSet] = {}
        self.source_by_folder: Dict[str, str] = {}
        self.placements: Dict[str, Placement] = {}
        self.placement_order: List[str] = []
        self.selected_id: Optional[str] = None
        self.copied_id: Optional[str] = None

        self.zoom = 0.2
        self.current_layer = 0
        self.canvas_item_ids: Dict[str, int] = {}
        self.canvas_photo_refs: Dict[str, ImageTk.PhotoImage] = {}
        self.drag_state: Optional[Tuple[str, float, float, int, int, LayoutState]] = None
        self.pan_active = False
        self.export_cancel = threading.Event()
        self.export_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.stl_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.stl_import_active = False
        self.stl_import_button = None
        self.drag_drop_enabled = False
        self.settings_path = get_settings_path()
        self._settings_save_after_id: Optional[str] = None
        self.output_folder_manual = False
        self.undo_stack: List[LayoutState] = []
        self.max_undo_steps = 100
        self.image_size_cache: Dict[str, Tuple[int, int]] = {}

        # Alt+휠 연속 입력 최적화 상태. 한 번의 휠 제스처는 Undo 한 단계로 기록하고,
        # 회전 중에는 원본 전체가 아니라 화면 축소본만 회전한다.
        self._rotation_undo_state: Optional[LayoutState] = None
        self._rotation_active_id: Optional[str] = None
        self._rotation_commit_after_id: Optional[str] = None
        self._fast_preview_base_cache: Dict[Tuple[str, float], Image.Image] = {}
        self._fast_preview_cache_limit = 24

        # 레이어 연속 미리보기 상태. 휠/슬라이더 입력이 빠르게 이어질 때는
        # 최종 요청 레이어만 그리도록 짧게 디바운스해 UI가 멈추지 않게 한다.
        self._layer_redraw_after_id: Optional[str] = None
        self._layer_scrub_updating = False
        self._layer_wheel_remainder = 0

        saved_settings = self._load_settings()
        self.canvas_width_var = tk.IntVar(value=saved_settings.get("canvas_width", 2000))
        self.canvas_height_var = tk.IntVar(value=saved_settings.get("canvas_height", 2000))
        self.dpi_x_var = tk.IntVar(value=saved_settings.get("dpi_x", 400))
        self.dpi_y_var = tk.IntVar(value=saved_settings.get("dpi_y", 400))
        self.stl_layer_height_var = tk.DoubleVar(value=saved_settings.get("stl_layer_height_mm", 0.1))
        self.x_var = tk.IntVar(value=0)
        self.y_var = tk.IntVar(value=0)
        self.visible_var = tk.BooleanVar(value=True)
        self.opacity_var = tk.IntVar(value=65)
        self.layer_var = tk.IntVar(value=1)
        self.layer_total_var = tk.StringVar(value="/ 0")
        self.layer_scrub_var = tk.DoubleVar(value=1.0)
        self.layer_scrub_info_var = tk.StringVar(value="이미지 세트 또는 STL을 추가하면 휠로 레이어를 연속 미리보기할 수 있습니다.")
        self.merge_mode_var = tk.StringVar(value="max")
        self.length_mode_var = tk.StringVar(value="max")
        self.output_folder_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="이미지 세트 폴더 또는 STL 파일을 추가하거나 중앙 캔버스로 드래그하세요.")
        self.selected_info_var = tk.StringVar(value="선택 없음")
        self.progress_var = tk.IntVar(value=0)

        self._build_ui()
        self._bind_shortcuts()
        self._setup_tk_drop()
        self._bind_setting_persistence()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self.fit_canvas)

    # ---------------------------- UI ----------------------------
    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 7))
        top.pack(fill="x")

        ttk.Label(top, text="최종 해상도").pack(side="left")
        ttk.Spinbox(top, from_=1, to=100000, width=8, textvariable=self.canvas_width_var).pack(side="left", padx=(5, 2))
        ttk.Label(top, text="×").pack(side="left")
        ttk.Spinbox(top, from_=1, to=100000, width=8, textvariable=self.canvas_height_var).pack(side="left", padx=(2, 8))
        ttk.Button(top, text="캔버스 적용", command=self.apply_canvas_size).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="DPI X/Y").pack(side="left")
        ttk.Spinbox(top, from_=1, to=10000, width=6, textvariable=self.dpi_x_var).pack(side="left", padx=(5, 2))
        ttk.Spinbox(top, from_=1, to=10000, width=6, textvariable=self.dpi_y_var).pack(side="left", padx=(2, 8))

        ttk.Label(top, text="STL 층두께(mm)").pack(side="left")
        ttk.Spinbox(
            top, from_=0.001, to=1000.0, increment=0.01, width=7,
            textvariable=self.stl_layer_height_var,
        ).pack(side="left", padx=(5, 8))

        ttk.Label(top, text="합성").pack(side="left")
        ttk.Combobox(
            top,
            width=12,
            state="readonly",
            textvariable=self.merge_mode_var,
            values=("max", "overwrite"),
        ).pack(side="left", padx=(5, 8))
        ttk.Label(top, text="길이").pack(side="left")
        ttk.Combobox(
            top,
            width=8,
            state="readonly",
            textvariable=self.length_mode_var,
            values=("max", "min"),
        ).pack(side="left", padx=(5, 8))

        ttk.Button(top, text="화면 맞춤", command=self.fit_canvas).pack(side="right", padx=(5, 0))
        ttk.Button(top, text="100%", command=lambda: self.set_zoom(1.0)).pack(side="right")

        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        left = ttk.Frame(main, padding=(0, 0, 7, 0), width=330)
        center = ttk.Frame(main)
        main.add(left, weight=0)
        main.add(center, weight=1)

        # 왼쪽 목록
        source_box = ttk.LabelFrame(left, text="이미지 세트 / STL 배치", padding=7)
        source_box.pack(fill="both", expand=True)
        ttk.Label(source_box, text="이미지 폴더 또는 STL 파일을 목록/캔버스로 드롭할 수 있습니다. STL은 mm 단위로 해석해 현재 DPI와 층두께로 슬라이싱합니다.", wraplength=290).pack(fill="x", pady=(0, 5))

        list_frame = ttk.Frame(source_box)
        list_frame.pack(fill="both", expand=True)
        self.placement_list = tk.Listbox(list_frame, exportselection=False, activestyle="dotbox")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.placement_list.yview)
        self.placement_list.configure(yscrollcommand=list_scroll.set)
        self.placement_list.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")
        self.placement_list.bind("<<ListboxSelect>>", self._on_list_select)

        buttons = ttk.Frame(source_box)
        buttons.pack(fill="x", pady=(7, 0))
        ttk.Button(buttons, text="폴더 추가", command=self.choose_folders).pack(side="left", fill="x", expand=True)
        self.stl_import_button = ttk.Button(buttons, text="STL 추가", command=self.choose_stl_files)
        self.stl_import_button.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(buttons, text="복제", command=self.duplicate_selected).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(buttons, text="삭제", command=self.remove_selected).pack(side="left", fill="x", expand=True)

        position_box = ttk.LabelFrame(left, text="선택 배치", padding=7)
        position_box.pack(fill="x", pady=(7, 0))
        coord = ttk.Frame(position_box)
        coord.pack(fill="x")
        ttk.Label(coord, text="X").pack(side="left")
        self.x_spin = ttk.Spinbox(coord, from_=-100000, to=100000, width=9, textvariable=self.x_var, command=self._apply_position_from_vars)
        self.x_spin.pack(side="left", padx=(4, 9))
        ttk.Label(coord, text="Y").pack(side="left")
        self.y_spin = ttk.Spinbox(coord, from_=-100000, to=100000, width=9, textvariable=self.y_var, command=self._apply_position_from_vars)
        self.y_spin.pack(side="left", padx=(4, 0))
        self.x_spin.bind("<Return>", self._apply_position_from_event)
        self.y_spin.bind("<Return>", self._apply_position_from_event)
        self.x_spin.bind("<FocusOut>", self._apply_position_from_event)
        self.y_spin.bind("<FocusOut>", self._apply_position_from_event)

        ttk.Checkbutton(position_box, text="출력에 포함", variable=self.visible_var, command=self._apply_visibility).pack(anchor="w", pady=(7, 0))
        ttk.Label(position_box, textvariable=self.selected_info_var, wraplength=290, justify="left").pack(fill="x", pady=(7, 0))

        preview_box = ttk.LabelFrame(left, text="미리보기", padding=7)
        preview_box.pack(fill="x", pady=(7, 0))
        layer_line = ttk.Frame(preview_box)
        layer_line.pack(fill="x")
        ttk.Button(layer_line, text="◀", width=3, command=lambda: self.change_layer(-1)).pack(side="left")
        self.layer_spin = ttk.Spinbox(layer_line, from_=1, to=1, width=8, textvariable=self.layer_var, command=self._on_layer_spin)
        self.layer_spin.pack(side="left", padx=4)
        self.layer_spin.bind("<Return>", self._on_layer_event)
        self.layer_spin.bind("<FocusOut>", self._on_layer_event)
        ttk.Label(layer_line, textvariable=self.layer_total_var).pack(side="left")
        ttk.Button(layer_line, text="▶", width=3, command=lambda: self.change_layer(1)).pack(side="right")

        scrub_box = ttk.Frame(preview_box)
        scrub_box.pack(fill="x", pady=(8, 0))
        ttk.Label(scrub_box, text="연속 레이어 미리보기 · 이 영역에서 휠").pack(anchor="w")
        self.layer_scrub_scale = ttk.Scale(
            scrub_box,
            from_=1,
            to=1,
            orient="horizontal",
            variable=self.layer_scrub_var,
            command=self._on_layer_scrub_scale,
        )
        self.layer_scrub_scale.pack(fill="x", pady=(3, 1))
        self.layer_scrub_scale.bind("<MouseWheel>", self._layer_preview_wheel)
        self.layer_scrub_scale.bind("<Button-4>", self._layer_preview_wheel)
        self.layer_scrub_scale.bind("<Button-5>", self._layer_preview_wheel)
        self.layer_scrub_scale.bind("<ButtonRelease-1>", self._on_layer_scrub_release)
        self.layer_scrub_info_label = ttk.Label(
            scrub_box,
            textvariable=self.layer_scrub_info_var,
            anchor="center",
            justify="center",
            wraplength=290,
        )
        self.layer_scrub_info_label.pack(fill="x")
        self.layer_scrub_info_label.bind("<MouseWheel>", self._layer_preview_wheel)
        self.layer_scrub_info_label.bind("<Button-4>", self._layer_preview_wheel)
        self.layer_scrub_info_label.bind("<Button-5>", self._layer_preview_wheel)

        opacity_line = ttk.Frame(preview_box)
        opacity_line.pack(fill="x", pady=(7, 0))
        ttk.Label(opacity_line, text="검정 투명도 미리보기").pack(anchor="w")
        ttk.Scale(
            opacity_line,
            from_=20,
            to=100,
            variable=self.opacity_var,
            command=lambda _value: self.redraw_canvas(),
        ).pack(fill="x")

        help_box = ttk.LabelFrame(left, text="조작", padding=7)
        help_box.pack(fill="x", pady=(7, 0))
        ttk.Label(
            help_box,
            text=(
                "드래그: 배치 이동\n"
                "방향키: 1 px, Shift+방향키: 10 px\n"
                "Ctrl+C / Ctrl+V: 복사·붙여넣기\n"
                "Ctrl+D: 즉시 복제, Delete: 삭제\n"
                "Ctrl+Z: 직전 배치 복구\n"
                "Alt+휠: 선택 이미지 5° 회전\n"
                "연속 미리보기 바에서 휠: 이전/다음 레이어\n"
                "Ctrl+휠: 확대/축소, 가운데 버튼: 화면 이동"
            ),
            justify="left",
        ).pack(anchor="w")

        # 중앙 캔버스
        canvas_frame = ttk.Frame(center)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, background="#303030", highlightthickness=0, cursor="crosshair")
        h_scroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        v_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._pan_end)
        self.canvas.bind("<Alt-MouseWheel>", self._rotate_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._zoom_wheel)
        self.canvas.bind("<MouseWheel>", self._vertical_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._horizontal_wheel)

        # 하단 출력
        output = ttk.Frame(self.root, padding=(8, 0, 8, 7))
        output.pack(fill="x")
        ttk.Label(output, text="출력 폴더").grid(row=0, column=0, sticky="w")
        self.output_entry = ttk.Entry(output, textvariable=self.output_folder_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=6)
        self.output_entry.bind("<KeyRelease>", self._mark_output_folder_manual)
        self.output_entry.bind("<<Paste>>", self._mark_output_folder_manual)
        ttk.Button(output, text="자동", command=self.use_auto_output_folder).grid(row=0, column=2, padx=(0, 3))
        ttk.Button(output, text="선택", command=self.choose_output_folder).grid(row=0, column=3)
        self.export_button = ttk.Button(output, text="전체 레이어 합성", command=self.start_export)
        self.export_button.grid(row=0, column=4, padx=(8, 3))
        self.cancel_button = ttk.Button(output, text="취소", command=self.cancel_export, state="disabled")
        self.cancel_button.grid(row=0, column=5)
        output.columnconfigure(1, weight=1)

        self.progress = ttk.Progressbar(output, maximum=100, variable=self.progress_var)
        self.progress.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 2))
        ttk.Label(output, textvariable=self.status_var).grid(row=2, column=0, columnspan=6, sticky="w")

        self.redraw_canvas()

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-c>", lambda _e: self.copy_selected())
        self.root.bind_all("<Control-v>", lambda _e: self.paste_copied())
        self.root.bind_all("<Control-d>", lambda _e: self.duplicate_selected())
        self.root.bind_all("<Control-z>", self.undo_last_action)
        self.root.bind_all("<Delete>", lambda _e: self.remove_selected())
        self.root.bind_all("<Left>", lambda e: self._nudge(-10 if e.state & 0x0001 else -1, 0, e))
        self.root.bind_all("<Right>", lambda e: self._nudge(10 if e.state & 0x0001 else 1, 0, e))
        self.root.bind_all("<Up>", lambda e: self._nudge(0, -10 if e.state & 0x0001 else -1, e))
        self.root.bind_all("<Down>", lambda e: self._nudge(0, 10 if e.state & 0x0001 else 1, e))

    # ----------------------- Undo history -----------------------
    def _capture_layout_state(self, description: str = "배치 변경") -> LayoutState:
        return LayoutState(
            sources={sid: clone_source_set(source) for sid, source in self.sources.items()},
            source_by_folder=dict(self.source_by_folder),
            placements={
                pid: Placement(
                    p.placement_id,
                    p.source_id,
                    p.label,
                    p.x,
                    p.y,
                    p.visible,
                    normalize_rotation(p.rotation),
                )
                for pid, p in self.placements.items()
            },
            placement_order=list(self.placement_order),
            selected_id=self.selected_id,
            copied_id=self.copied_id,
            description=description,
        )

    def _append_undo_state(self, state: LayoutState) -> None:
        self.undo_stack.append(state)
        if len(self.undo_stack) > self.max_undo_steps:
            del self.undo_stack[: len(self.undo_stack) - self.max_undo_steps]

    def _record_undo(self, description: str) -> None:
        self._append_undo_state(self._capture_layout_state(description))

    def undo_last_action(self, _event=None):
        # 텍스트 입력 중에는 Entry/Spinbox 자체의 Ctrl+Z 동작을 방해하지 않는다.
        if self._focus_is_text_input():
            return None
        if self._rotation_undo_state is not None:
            self._finish_rotation_interaction()
        if not self.undo_stack:
            self.status_var.set("되돌릴 배치 작업이 없습니다.")
            return "break"

        state = self.undo_stack.pop()
        self.sources = {sid: clone_source_set(source) for sid, source in state.sources.items()}
        self.source_by_folder = dict(state.source_by_folder)
        self.placements = {
            pid: Placement(
                p.placement_id,
                p.source_id,
                p.label,
                p.x,
                p.y,
                p.visible,
                normalize_rotation(p.rotation),
            )
            for pid, p in state.placements.items()
        }
        self.placement_order = list(state.placement_order)
        self.selected_id = state.selected_id if state.selected_id in self.placements else None
        self.copied_id = state.copied_id if state.copied_id in self.placements else None
        self.drag_state = None

        self.refresh_list()
        self.update_layer_controls()
        self._update_auto_output_folder()
        self.redraw_canvas()
        if self.selected_id:
            self.select_placement(self.selected_id)
        else:
            self.selected_info_var.set("선택 없음")
        self.status_var.set(f"되돌림: {state.description}")
        return "break"

    # ----------------------- Drag and drop -----------------------
    def _setup_tk_drop(self) -> None:
        """Tk 이벤트 루프 기반 드래그앤드롭을 등록한다."""
        if DND_FILES is None or not hasattr(self.root, "drop_target_register"):
            self.drag_drop_enabled = False
            self.status_var.set(
                "드래그앤드롭 모듈을 사용할 수 없습니다. 폴더 추가 버튼은 정상 사용 가능합니다."
            )
            return

        try:
            for widget, handler in (
                (self.placement_list, self._drop_on_list),
                (self.canvas, self._drop_on_canvas),
            ):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", handler)
            self.drag_drop_enabled = True
            self.status_var.set("이미지 폴더 또는 STL을 목록/중앙 캔버스로 드래그앤드롭할 수 있습니다.")
        except Exception as exc:
            self.drag_drop_enabled = False
            self.status_var.set(
                f"드래그앤드롭 초기화 실패: {exc}. 폴더 추가 버튼은 정상 사용 가능합니다."
            )

    def _parse_drop_paths(self, data: object) -> List[Path]:
        """tkdnd의 Tcl 목록 문자열을 Windows 경로 목록으로 변환한다."""
        text = str(data or "").strip()
        if not text:
            return []
        try:
            raw_items = list(self.root.tk.splitlist(text))
        except tk.TclError:
            raw_items = [text]

        paths: List[Path] = []
        for raw in raw_items:
            value = str(raw).strip()
            if value.startswith("{") and value.endswith("}"):
                value = value[1:-1]
            if value.lower().startswith("file:///"):
                value = value[8:]
            if os.name == "nt":
                value = value.replace("/", os.sep)
            try:
                paths.append(Path(value))
            except (TypeError, ValueError, OSError):
                continue
        return paths

    def _drop_on_list(self, event):
        """목록 드롭 이벤트. 이미지 폴더와 STL 파일을 함께 처리한다."""
        try:
            paths = self._parse_drop_paths(event.data)
            if not any(path.is_dir() or (path.is_file() and path.suffix.lower() in STL_EXTENSIONS) for path in paths):
                self.status_var.set("드롭된 항목 중 유효한 이미지 폴더 또는 STL 파일이 없습니다.")
                return "break"
            self.add_input_paths(paths, 0, 0)
        except Exception:
            self.status_var.set(f"드롭 처리 실패: {traceback.format_exc().splitlines()[-1]}")
        return "break"

    def _drop_on_canvas(self, event):
        """캔버스 드롭 이벤트. 드롭 위치를 실제 출력 픽셀 좌표로 변환한다."""
        try:
            paths = self._parse_drop_paths(event.data)
            valid = [
                path for path in paths
                if path.is_dir() or (path.is_file() and path.suffix.lower() in STL_EXTENSIONS)
            ]
            if not valid:
                self.status_var.set("드롭된 항목 중 유효한 이미지 폴더 또는 STL 파일이 없습니다.")
                return "break"

            local_x = int(event.x_root) - self.canvas.winfo_rootx()
            local_y = int(event.y_root) - self.canvas.winfo_rooty()
            canvas_x = self.canvas.canvasx(local_x)
            canvas_y = self.canvas.canvasy(local_y)
            x = round(canvas_x / self.zoom)
            y = round(canvas_y / self.zoom)
            self.add_input_paths(valid, x, y)
        except Exception:
            self.status_var.set(f"드롭 처리 실패: {traceback.format_exc().splitlines()[-1]}")
        return "break"

    # ----------------------- Source/placement -----------------------
    def choose_folders(self) -> None:
        folder = filedialog.askdirectory(title="이미지 세트 폴더 선택")
        if folder:
            self.add_folders([Path(folder)], 0, 0)

    def choose_stl_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="STL 파일 선택",
            filetypes=(("STL mesh", "*.stl"), ("모든 파일", "*.*")),
        )
        if files:
            self.add_stl_files([Path(value) for value in files], 0, 0)

    def add_input_paths(self, paths: Sequence[Path], x: int, y: int) -> None:
        folders = [path for path in paths if path.is_dir()]
        stl_files = [
            path for path in paths
            if path.is_file() and path.suffix.lower() in STL_EXTENSIONS
        ]
        if folders:
            self.add_folders(folders, x, y)
        if stl_files:
            offset = 20 * len(folders)
            self.add_stl_files(stl_files, x + offset, y + offset)

    def add_folders(self, folders: Sequence[Path], x: int, y: int) -> None:
        added = 0
        history_recorded = False
        for index, folder in enumerate(folders):
            try:
                folder = folder.expanduser().resolve()
            except OSError:
                folder = folder.expanduser()
            if not folder.is_dir():
                continue

            key = "folder|" + os.path.normcase(str(folder))
            source_id = self.source_by_folder.get(key)
            if source_id is None:
                images = list_images(folder)
                if not images:
                    messagebox.showwarning(APP_NAME, f"이미지 파일을 찾지 못했습니다.\n\n{folder}")
                    continue
                source_id = uuid.uuid4().hex
                self.sources[source_id] = SourceSet(
                    source_id=source_id,
                    folder=folder,
                    images=images,
                    kind="folder",
                    original_path=folder,
                    display_name=folder.name,
                )
                self.source_by_folder[key] = source_id

            if not history_recorded:
                self._record_undo("이미지 세트 배치")
                history_recorded = True
            self.create_placement(source_id, x + index * 20, y + index * 20, select=True)
            added += 1

        if added:
            self.update_layer_controls()
            self._update_auto_output_folder()
            self.redraw_canvas()
            self.status_var.set(f"이미지 세트 {added}개를 배치했습니다.")

    def _stl_source_lookup_key(
        self, stl_path: Path, dpi_x: int, dpi_y: int, layer_height_mm: float
    ) -> str:
        return (
            "stl|"
            + os.path.normcase(str(stl_path))
            + f"|{int(dpi_x)}|{int(dpi_y)}|{float(layer_height_mm):.9f}"
        )

    def add_stl_files(self, files: Sequence[Path], x: int, y: int) -> None:
        if self.stl_import_active:
            messagebox.showinfo(APP_NAME, "현재 STL 슬라이싱이 진행 중입니다. 완료 후 다시 추가하세요.")
            return
        try:
            dpi_x = int(self.dpi_x_var.get())
            dpi_y = int(self.dpi_y_var.get())
            layer_height_mm = float(self.stl_layer_height_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_NAME, "DPI와 STL 층두께 값을 확인하세요.")
            return
        if dpi_x <= 0 or dpi_y <= 0 or layer_height_mm <= 0:
            messagebox.showerror(APP_NAME, "DPI와 STL 층두께는 0보다 커야 합니다.")
            return
        if trimesh is None or np is None:
            messagebox.showerror(
                APP_NAME,
                "STL 기능 패키지를 불러오지 못했습니다.\n\n"
                f'"{sys.executable}" -m pip install --upgrade numpy trimesh',
            )
            return

        valid: List[Path] = []
        for path in files:
            try:
                resolved = path.expanduser().resolve()
            except OSError:
                resolved = path.expanduser()
            if resolved.is_file() and resolved.suffix.lower() in STL_EXTENSIONS:
                valid.append(resolved)
        if not valid:
            messagebox.showwarning(APP_NAME, "유효한 STL 파일이 없습니다.")
            return

        undo_state = self._capture_layout_state("STL 배치")
        self.stl_import_active = True
        if self.stl_import_button is not None:
            self.stl_import_button.configure(state="disabled")
        self.status_var.set(
            f"STL {len(valid)}개 슬라이싱 준비 · {dpi_x}×{dpi_y} DPI · {layer_height_mm:g} mm"
        )

        worker = threading.Thread(
            target=self._stl_import_worker,
            args=(valid, int(x), int(y), dpi_x, dpi_y, layer_height_mm, undo_state),
            daemon=True,
            name="stl-slicer",
        )
        worker.start()
        self.root.after(80, self._poll_stl_queue)

    def _stl_import_worker(
        self,
        files: Sequence[Path],
        x: int,
        y: int,
        dpi_x: int,
        dpi_y: int,
        layer_height_mm: float,
        undo_state: LayoutState,
    ) -> None:
        try:
            results = []
            total_files = len(files)
            for file_index, stl_path in enumerate(files, start=1):
                def on_progress(current: int, total: int, detail: str, *, _path=stl_path, _index=file_index):
                    self.stl_queue.put(
                        (
                            "progress",
                            {
                                "file_index": _index,
                                "file_total": total_files,
                                "name": _path.name,
                                "current": current,
                                "total": total,
                                "detail": detail,
                            },
                        )
                    )

                result = slice_stl_to_cache(
                    stl_path,
                    dpi_x=dpi_x,
                    dpi_y=dpi_y,
                    layer_height_mm=layer_height_mm,
                    progress_callback=on_progress,
                )
                results.append((stl_path, result, x + (file_index - 1) * 20, y + (file_index - 1) * 20))
            self.stl_queue.put(("finished", (results, dpi_x, dpi_y, layer_height_mm, undo_state)))
        except Exception:
            self.stl_queue.put(("error", traceback.format_exc()))

    def _poll_stl_queue(self) -> None:
        keep_polling = self.stl_import_active
        try:
            while True:
                event, payload = self.stl_queue.get_nowait()
                if event == "progress":
                    data = payload
                    total = max(1, int(data.get("total", 1)))
                    current = max(0, int(data.get("current", 0)))
                    percent = round(current * 100 / total)
                    self.progress_var.set(percent)
                    self.status_var.set(
                        f"STL {data['file_index']}/{data['file_total']} · {data['name']} · "
                        f"{data['detail']}"
                    )
                elif event == "finished":
                    results, dpi_x, dpi_y, layer_height_mm, undo_state = payload
                    added = 0
                    for stl_path, result, x, y in results:
                        key = self._stl_source_lookup_key(stl_path, dpi_x, dpi_y, layer_height_mm)
                        source_id = self.source_by_folder.get(key)
                        if source_id is None:
                            source_id = uuid.uuid4().hex
                            model_size = tuple(float(v) for v in result.get("model_size_mm", (0, 0, 0)))
                            self.sources[source_id] = SourceSet(
                                source_id=source_id,
                                folder=Path(result["cache_dir"]),
                                images=list(result["images"]),
                                kind="stl",
                                original_path=stl_path,
                                display_name=stl_path.stem,
                                dpi_x=int(dpi_x),
                                dpi_y=int(dpi_y),
                                layer_height_mm=float(layer_height_mm),
                                model_size_mm=model_size,
                            )
                            self.source_by_folder[key] = source_id
                        self.create_placement(source_id, x, y, select=True)
                        added += 1
                    if added:
                        self._append_undo_state(undo_state)
                    self.stl_import_active = False
                    if self.stl_import_button is not None:
                        self.stl_import_button.configure(state="normal")
                    self.progress_var.set(0)
                    self.update_layer_controls()
                    self._update_auto_output_folder()
                    self.redraw_canvas()
                    if self.selected_id:
                        self.select_placement(self.selected_id)
                    self.status_var.set(f"STL {added}개를 슬라이싱해 배치했습니다. Z축 회전만 적용됩니다.")
                    keep_polling = False
                elif event == "error":
                    self.stl_import_active = False
                    if self.stl_import_button is not None:
                        self.stl_import_button.configure(state="normal")
                    self.progress_var.set(0)
                    messagebox.showerror(APP_NAME, f"STL 슬라이싱 중 오류가 발생했습니다.\n\n{payload}")
                    self.status_var.set("STL 슬라이싱 실패")
                    keep_polling = False
        except queue.Empty:
            pass
        if keep_polling and self.stl_import_active:
            self.root.after(80, self._poll_stl_queue)

    def create_placement(self, source_id: str, x: int, y: int, select: bool = True) -> Optional[str]:
        source = self.sources.get(source_id)
        if source is None:
            return None
        instance_no = 1 + sum(1 for p in self.placements.values() if p.source_id == source_id)
        placement_id = uuid.uuid4().hex
        placement = Placement(
            placement_id=placement_id,
            source_id=source_id,
            label=f"{source.name} [배치 {instance_no}]",
            x=int(x),
            y=int(y),
        )
        self.placements[placement_id] = placement
        self.placement_order.append(placement_id)
        self.refresh_list()
        if select:
            self.select_placement(placement_id)
        return placement_id

    def refresh_list(self) -> None:
        current = self.selected_id
        self.placement_list.delete(0, "end")
        selected_index = None
        for index, placement_id in enumerate(self.placement_order):
            placement = self.placements.get(placement_id)
            if placement is None:
                continue
            source = self.sources.get(placement.source_id)
            count = len(source.images) if source else 0
            state = "표시" if placement.visible else "숨김"
            source_type = "STL" if source and source.kind == "stl" else "이미지"
            self.placement_list.insert(
                "end",
                f"[{source_type}] {placement.label} | {count}장 | X={placement.x}, Y={placement.y} | 회전={normalize_rotation(placement.rotation)}° | {state}",
            )
            if placement_id == current:
                selected_index = index
        if selected_index is not None:
            self.placement_list.selection_set(selected_index)
            self.placement_list.activate(selected_index)
            self.placement_list.see(selected_index)

    def _on_list_select(self, _event=None) -> None:
        selection = self.placement_list.curselection()
        if not selection:
            return
        index = selection[0]
        if 0 <= index < len(self.placement_order):
            self.select_placement(self.placement_order[index], from_list=True)

    def select_placement(self, placement_id: str, from_list: bool = False) -> None:
        placement = self.placements.get(placement_id)
        if placement is None:
            return
        self.selected_id = placement_id
        self.x_var.set(placement.x)
        self.y_var.set(placement.y)
        self.visible_var.set(placement.visible)
        source = self.sources.get(placement.source_id)
        if source:
            current_name = source.images[self.current_layer].name if self.current_layer < len(source.images) else "(해당 레이어 없음)"
            if source.kind == "stl":
                size_x, size_y, size_z = source.model_size_mm
                self.selected_info_var.set(
                    f"STL: {source.origin_path}\n"
                    f"모델 크기: {size_x:.3f} × {size_y:.3f} × {size_z:.3f} mm\n"
                    f"슬라이스: {len(source.images)}장 · {source.layer_height_mm:g} mm · "
                    f"{source.dpi_x}×{source.dpi_y} DPI\n"
                    f"현재 층: {self.current_layer + 1}\n회전: {normalize_rotation(placement.rotation)}° (Z축·시계방향)"
                )
            else:
                self.selected_info_var.set(
                    f"폴더: {source.folder}\n파일 수: {len(source.images)}\n현재 파일: {current_name}\n"
                    f"회전: {normalize_rotation(placement.rotation)}° (시계방향)"
                )
        if not from_list:
            try:
                index = self.placement_order.index(placement_id)
                self.placement_list.selection_clear(0, "end")
                self.placement_list.selection_set(index)
                self.placement_list.activate(index)
                self.placement_list.see(index)
            except ValueError:
                pass
        self._update_selection_outline()

    def copy_selected(self) -> None:
        if self.selected_id in self.placements:
            self.copied_id = self.selected_id
            self.status_var.set("선택 배치를 복사했습니다. Ctrl+V로 동일 이미지 세트 또는 STL 슬라이스를 내부 복제합니다.")

    def paste_copied(self) -> None:
        if self.copied_id not in self.placements:
            return
        original = self.placements[self.copied_id]
        self._record_undo("배치 복제")
        new_id = self.create_placement(original.source_id, original.x + 20, original.y + 20, select=True)
        if new_id:
            self.placements[new_id].visible = original.visible
            self.placements[new_id].rotation = normalize_rotation(original.rotation)
            self.refresh_list()
            self.redraw_canvas()
            self.status_var.set("원본 파일을 복사하지 않고 동일 이미지/STL 슬라이스를 독립 배치로 복제했습니다.")

    def duplicate_selected(self) -> None:
        if self.selected_id in self.placements:
            self.copied_id = self.selected_id
            self.paste_copied()

    def remove_selected(self) -> None:
        if self._focus_is_text_input() or self.selected_id not in self.placements:
            return
        self._record_undo("배치 삭제")
        removed = self.placements.pop(self.selected_id)
        try:
            self.placement_order.remove(self.selected_id)
        except ValueError:
            pass
        if self.copied_id == self.selected_id:
            self.copied_id = None
        self.selected_id = None

        if not any(p.source_id == removed.source_id for p in self.placements.values()):
            source = self.sources.pop(removed.source_id, None)
            if source:
                stale_keys = [key for key, value in self.source_by_folder.items() if value == removed.source_id]
                for key in stale_keys:
                    self.source_by_folder.pop(key, None)

        self.selected_info_var.set("선택 없음")
        self.refresh_list()
        self.update_layer_controls()
        self._update_auto_output_folder()
        self.redraw_canvas()
        self.status_var.set("배치를 삭제했습니다.")

    def _apply_position_from_event(self, _event=None) -> None:
        self._apply_position_from_vars()

    def _apply_position_from_vars(self) -> None:
        if self.selected_id not in self.placements:
            return
        try:
            x = int(self.x_var.get())
            y = int(self.y_var.get())
        except (ValueError, tk.TclError):
            return
        placement = self.placements[self.selected_id]
        if (placement.x, placement.y) == (x, y):
            return
        self._record_undo("좌표 변경")
        placement.x, placement.y = x, y
        self._move_canvas_item(self.selected_id)
        self.refresh_list()

    def _apply_visibility(self) -> None:
        if self.selected_id not in self.placements:
            return
        new_visible = bool(self.visible_var.get())
        if self.placements[self.selected_id].visible == new_visible:
            return
        self._record_undo("표시 상태 변경")
        self.placements[self.selected_id].visible = new_visible
        self.refresh_list()
        self.redraw_canvas()

    def _nudge(self, dx: int, dy: int, event=None):
        if self._focus_is_text_input() or self.selected_id not in self.placements:
            return None
        self._record_undo("배치 미세 이동")
        placement = self.placements[self.selected_id]
        placement.x += dx
        placement.y += dy
        self.x_var.set(placement.x)
        self.y_var.set(placement.y)
        self._move_canvas_item(self.selected_id)
        self.refresh_list()
        return "break"

    def _focus_is_text_input(self) -> bool:
        widget = self.root.focus_get()
        if widget is None:
            return False
        return widget.winfo_class() in {"Entry", "TEntry", "Spinbox", "TSpinbox", "Text"}

    # ---------------------------- Canvas ----------------------------
    def apply_canvas_size(self) -> None:
        try:
            width = int(self.canvas_width_var.get())
            height = int(self.canvas_height_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_NAME, "캔버스 해상도는 정수여야 합니다.")
            return
        if width <= 0 or height <= 0:
            messagebox.showerror(APP_NAME, "캔버스 해상도는 1 이상이어야 합니다.")
            return
        self.redraw_canvas()
        self.fit_canvas()
        self.status_var.set(f"캔버스를 {width} × {height} px로 적용했습니다.")

    def redraw_canvas(self) -> None:
        self.canvas.delete("all")
        self.canvas_item_ids.clear()
        self.canvas_photo_refs.clear()
        width = max(1, int(self.canvas_width_var.get()))
        height = max(1, int(self.canvas_height_var.get()))
        dw = width * self.zoom
        dh = height * self.zoom
        self.canvas.create_rectangle(0, 0, dw, dh, fill="black", outline="#cfcfcf", width=1, tags=("build_area",))

        # 원본은 source_id별 한 번 읽고 먼저 화면 크기로 축소한 뒤 회전한다.
        # 원본 전체를 회전하고 나서 축소하던 이전 방식보다 임의각 회전이 훨씬 빠르다.
        base_preview_cache: Dict[str, Image.Image] = {}
        preview_photo_cache: Dict[Tuple[str, int], ImageTk.PhotoImage] = {}
        failed_preview_keys: set[Tuple[str, int]] = set()
        for placement_id in self.placement_order:
            placement = self.placements.get(placement_id)
            if placement is None or not placement.visible:
                continue
            source = self.sources.get(placement.source_id)
            if source is None or self.current_layer >= len(source.images):
                continue
            rotation = normalize_rotation(placement.rotation)
            preview_key = (placement.source_id, rotation)
            if preview_key in failed_preview_keys:
                continue
            photo = preview_photo_cache.get(preview_key)
            if photo is None:
                try:
                    scaled_gray = base_preview_cache.get(placement.source_id)
                    if scaled_gray is None:
                        gray = load_grayscale(source.images[self.current_layer])
                        scaled_gray = self._scale_gray_for_preview(gray)
                        base_preview_cache[placement.source_id] = scaled_gray
                    rotated_preview = rotate_grayscale(scaled_gray, rotation)
                    preview = self._preview_rgba(rotated_preview)
                    photo = ImageTk.PhotoImage(preview)
                    preview_photo_cache[preview_key] = photo
                except Exception:
                    failed_preview_keys.add(preview_key)
                    continue
            item_id = self.canvas.create_image(
                placement.x * self.zoom,
                placement.y * self.zoom,
                anchor="nw",
                image=photo,
                tags=("placement", f"pid:{placement_id}"),
            )
            self.canvas_item_ids[placement_id] = item_id
            self.canvas_photo_refs[placement_id] = photo

        margin = max(150.0, min(1000.0, 250.0 * self.zoom))
        self.canvas.configure(scrollregion=(-margin, -margin, dw + margin, dh + margin))
        self._update_selection_outline()

    def _scale_gray_for_preview(self, gray: Image.Image) -> Image.Image:
        """원본을 현재 줌 크기로 먼저 축소한다. 임의각 회전은 이 작은 이미지에서 수행한다."""
        width = max(1, round(gray.width * self.zoom))
        height = max(1, round(gray.height * self.zoom))
        if (width, height) == gray.size:
            return gray.copy()
        return gray.resize((width, height), RESAMPLE_NEAREST)

    def _preview_rgba(self, resized_gray: Image.Image) -> Image.Image:
        """이미 축소/회전된 grayscale 미리보기에 표시용 알파를 적용한다."""
        rgba = Image.new("RGBA", resized_gray.size, (255, 255, 255, 0))
        rgba.paste(resized_gray.convert("RGB"), (0, 0))
        opacity = max(0.05, min(1.0, self.opacity_var.get() / 100.0))
        alpha = resized_gray.point(lambda p: int(p * opacity))
        rgba.putalpha(alpha)
        return rgba

    def _make_preview_image(self, gray: Image.Image) -> Image.Image:
        """호환용 헬퍼. 새 경로는 축소 후 회전 순서를 사용한다."""
        return self._preview_rgba(self._scale_gray_for_preview(gray))

    def _trim_fast_preview_cache(self) -> None:
        while len(self._fast_preview_base_cache) > self._fast_preview_cache_limit:
            try:
                oldest_key = next(iter(self._fast_preview_base_cache))
            except StopIteration:
                break
            self._fast_preview_base_cache.pop(oldest_key, None)

    def _fast_rotated_preview_photo(self, placement: Placement) -> Optional[ImageTk.PhotoImage]:
        """Alt+휠 반응용: 원본 전체가 아닌 축소본만 회전해 PhotoImage를 만든다."""
        source = self.sources.get(placement.source_id)
        if source is None or self.current_layer >= len(source.images):
            return None
        path = source.images[self.current_layer]
        zoom_key = round(float(self.zoom), 6)
        cache_key = (str(path), zoom_key)
        scaled_gray = self._fast_preview_base_cache.get(cache_key)
        if scaled_gray is None:
            gray = load_grayscale(path)
            scaled_gray = self._scale_gray_for_preview(gray)
            self._fast_preview_base_cache[cache_key] = scaled_gray
            self._trim_fast_preview_cache()
        rotated_preview = rotate_grayscale(scaled_gray, normalize_rotation(placement.rotation))
        return ImageTk.PhotoImage(self._preview_rgba(rotated_preview))

    def _update_selected_info_only(self, placement: Placement) -> None:
        source = self.sources.get(placement.source_id)
        if source:
            current_name = source.images[self.current_layer].name if self.current_layer < len(source.images) else "(해당 레이어 없음)"
            if source.kind == "stl":
                size_x, size_y, size_z = source.model_size_mm
                self.selected_info_var.set(
                    f"STL: {source.origin_path}\n"
                    f"모델 크기: {size_x:.3f} × {size_y:.3f} × {size_z:.3f} mm\n"
                    f"슬라이스: {len(source.images)}장 · {source.layer_height_mm:g} mm · "
                    f"{source.dpi_x}×{source.dpi_y} DPI\n"
                    f"현재 층: {self.current_layer + 1}\n회전: {normalize_rotation(placement.rotation)}° (Z축·시계방향)"
                )
            else:
                self.selected_info_var.set(
                    f"폴더: {source.folder}\n파일 수: {len(source.images)}\n현재 파일: {current_name}\n"
                    f"회전: {normalize_rotation(placement.rotation)}° (시계방향)"
                )

    def _fast_update_rotation_preview(self, placement_id: str) -> None:
        """선택 이미지 한 개의 캔버스 항목만 교체한다. 전체 캔버스 재합성을 하지 않는다."""
        placement = self.placements.get(placement_id)
        item_id = self.canvas_item_ids.get(placement_id)
        if placement is None or item_id is None:
            self.redraw_canvas()
            return
        try:
            photo = self._fast_rotated_preview_photo(placement)
        except Exception:
            photo = None
        if photo is None:
            self.redraw_canvas()
            return
        self.canvas.itemconfigure(item_id, image=photo)
        self.canvas.coords(item_id, placement.x * self.zoom, placement.y * self.zoom)
        self.canvas_photo_refs[placement_id] = photo
        self._update_selection_outline()

    def _finish_rotation_interaction(self) -> None:
        """연속 Alt+휠 입력을 한 작업으로 확정하고 Undo 스냅샷을 한 번만 저장한다."""
        if self._rotation_commit_after_id is not None:
            try:
                self.root.after_cancel(self._rotation_commit_after_id)
            except Exception:
                pass
            self._rotation_commit_after_id = None
        state = self._rotation_undo_state
        placement_id = self._rotation_active_id
        self._rotation_undo_state = None
        self._rotation_active_id = None
        if state is not None:
            self._append_undo_state(state)
            self.refresh_list()
        if placement_id in self.placements:
            self.select_placement(placement_id)

    def _placement_from_canvas_event(self, event) -> Optional[str]:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        overlapping = self.canvas.find_overlapping(x, y, x, y)
        for item_id in reversed(overlapping):
            for tag in self.canvas.gettags(item_id):
                if tag.startswith("pid:"):
                    placement_id = tag[4:]
                    if placement_id in self.placements:
                        return placement_id
        return None

    def _canvas_press(self, event) -> None:
        placement_id = self._placement_from_canvas_event(event)
        if placement_id is None:
            self.selected_id = None
            self.placement_list.selection_clear(0, "end")
            self.selected_info_var.set("선택 없음")
            self._update_selection_outline()
            return
        self.select_placement(placement_id)
        placement = self.placements[placement_id]
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        self.drag_state = (
            placement_id,
            cx,
            cy,
            placement.x,
            placement.y,
            self._capture_layout_state("배치 드래그 이동"),
        )
        self.canvas.configure(cursor="fleur")

    def _canvas_drag(self, event) -> None:
        if self.drag_state is None:
            return
        placement_id, start_x, start_y, original_x, original_y, _undo_state = self.drag_state
        if placement_id not in self.placements:
            return
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        dx = round((cx - start_x) / self.zoom)
        dy = round((cy - start_y) / self.zoom)
        placement = self.placements[placement_id]
        placement.x = original_x + dx
        placement.y = original_y + dy
        self.x_var.set(placement.x)
        self.y_var.set(placement.y)
        self._move_canvas_item(placement_id)

    def _canvas_release(self, _event) -> None:
        if self.drag_state is not None:
            placement_id, _sx, _sy, original_x, original_y, undo_state = self.drag_state
            placement = self.placements.get(placement_id)
            if placement is not None and (placement.x, placement.y) != (original_x, original_y):
                self._append_undo_state(undo_state)
                self.refresh_list()
        self.drag_state = None
        self.canvas.configure(cursor="crosshair")

    def _move_canvas_item(self, placement_id: str) -> None:
        placement = self.placements.get(placement_id)
        item_id = self.canvas_item_ids.get(placement_id)
        if placement is not None and item_id is not None:
            self.canvas.coords(item_id, placement.x * self.zoom, placement.y * self.zoom)
        self._update_selection_outline()

    def _update_selection_outline(self) -> None:
        self.canvas.delete("selection_outline")
        if self.selected_id not in self.placements:
            return
        item_id = self.canvas_item_ids.get(self.selected_id)
        if item_id is None:
            return
        bbox = self.canvas.bbox(item_id)
        if bbox:
            self.canvas.create_rectangle(
                bbox[0], bbox[1], bbox[2], bbox[3],
                outline="#00d8ff", width=2, dash=(5, 3),
                tags=("selection_outline",),
            )
            self.canvas.tag_raise("selection_outline")

    def _pan_start(self, event) -> None:
        self.pan_active = True
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="hand2")

    def _pan_move(self, event) -> None:
        if self.pan_active:
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _pan_end(self, _event) -> None:
        self.pan_active = False
        self.canvas.configure(cursor="crosshair")

    def _source_image_size(self, source_id: str) -> Optional[Tuple[int, int]]:
        source = self.sources.get(source_id)
        if source is None or not source.images:
            return None
        layer_index = min(self.current_layer, len(source.images) - 1)
        path = source.images[layer_index]
        cache_key = str(path)
        cached = self.image_size_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            with Image.open(path) as image:
                size = image.size
            self.image_size_cache[cache_key] = size
            return size
        except Exception:
            return None

    def _rotate_wheel(self, event):
        if self._focus_is_text_input():
            return None

        # 커서 아래 이미지가 있으면 그 이미지를 선택하고, 아니면 현재 선택 배치를 회전한다.
        placement_id = self._placement_from_canvas_event(event)
        if placement_id in self.placements:
            if placement_id != self.selected_id:
                self._finish_rotation_interaction()
                self.select_placement(placement_id)
        elif self.selected_id not in self.placements:
            self.status_var.set("회전할 이미지를 먼저 선택하세요.")
            return "break"

        placement = self.placements[self.selected_id]
        if self._rotation_active_id not in (None, placement.placement_id):
            self._finish_rotation_interaction()
        if self._rotation_undo_state is None:
            self._rotation_undo_state = self._capture_layout_state("이미지 회전")
            self._rotation_active_id = placement.placement_id

        wheel_steps = int(event.delta / 120) if event.delta else 0
        if wheel_steps == 0:
            wheel_steps = 1 if event.delta > 0 else -1
        delta_angle = 5 * wheel_steps
        old_angle = normalize_rotation(placement.rotation)
        new_angle = normalize_rotation(old_angle + delta_angle)
        if new_angle == old_angle:
            return "break"

        # 중심 유지 좌표 계산은 큰 Pillow 임시 이미지 없이 삼각함수만 사용한다.
        size = self._source_image_size(placement.source_id)
        if size is not None:
            old_w, old_h = rotated_size(size[0], size[1], old_angle)
            new_w, new_h = rotated_size(size[0], size[1], new_angle)
            center_x = placement.x + old_w / 2.0
            center_y = placement.y + old_h / 2.0
            placement.x = round(center_x - new_w / 2.0)
            placement.y = round(center_y - new_h / 2.0)

        placement.rotation = new_angle
        self.x_var.set(placement.x)
        self.y_var.set(placement.y)
        self._update_selected_info_only(placement)

        # 핵심: 전체 배치 redraw가 아니라 선택한 작은 화면용 축소본 한 개만 회전/교체한다.
        self._fast_update_rotation_preview(placement.placement_id)

        if self._rotation_commit_after_id is not None:
            try:
                self.root.after_cancel(self._rotation_commit_after_id)
            except Exception:
                pass
        self._rotation_commit_after_id = self.root.after(180, self._finish_rotation_interaction)

        direction = "시계방향" if delta_angle > 0 else "반시계방향"
        self.status_var.set(f"{direction} 회전: {new_angle}° (고속 미리보기, 입력 종료 후 확정)")
        return "break"

    def _zoom_wheel(self, event):
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self.set_zoom(self.zoom * factor)
        return "break"

    def _vertical_wheel(self, event):
        if getattr(event, "state", 0) & 0x0008:
            return self._rotate_wheel(event)
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _horizontal_wheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120), "units")
        return "break"

    def set_zoom(self, zoom: float) -> None:
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(zoom)))
        if abs(new_zoom - self.zoom) < 1e-9:
            return
        # 현재 화면 중심의 실제 캔버스 좌표를 보존한다.
        view_w = max(1, self.canvas.winfo_width())
        view_h = max(1, self.canvas.winfo_height())
        center_x_actual = self.canvas.canvasx(view_w / 2) / self.zoom
        center_y_actual = self.canvas.canvasy(view_h / 2) / self.zoom
        self.zoom = new_zoom
        self._fast_preview_base_cache.clear()
        self.redraw_canvas()
        self.root.update_idletasks()
        self._center_on_actual(center_x_actual, center_y_actual)
        self.status_var.set(f"확대율: {self.zoom * 100:.1f}%")

    def fit_canvas(self) -> None:
        self.root.update_idletasks()
        width = max(1, int(self.canvas_width_var.get()))
        height = max(1, int(self.canvas_height_var.get()))
        view_w = max(100, self.canvas.winfo_width() - 30)
        view_h = max(100, self.canvas.winfo_height() - 30)
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, min(view_w / width, view_h / height)))
        self.redraw_canvas()
        self._center_on_actual(width / 2, height / 2)
        self.status_var.set(f"캔버스를 화면에 맞췄습니다. 확대율: {self.zoom * 100:.1f}%")

    def _center_on_actual(self, x: float, y: float) -> None:
        self.root.update_idletasks()
        scrollregion = self.canvas.cget("scrollregion").split()
        if len(scrollregion) != 4:
            return
        x0, y0, x1, y1 = map(float, scrollregion)
        total_w = max(1.0, x1 - x0)
        total_h = max(1.0, y1 - y0)
        view_w = self.canvas.winfo_width()
        view_h = self.canvas.winfo_height()
        target_left = x * self.zoom - view_w / 2
        target_top = y * self.zoom - view_h / 2
        self.canvas.xview_moveto(max(0.0, min(1.0, (target_left - x0) / total_w)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (target_top - y0) / total_h)))

    # ---------------------------- Layer ----------------------------
    def _maximum_layer_count(self) -> int:
        return max((len(source.images) for source in self.sources.values()), default=0)

    def _reference_name_for_layer(self, layer_index: int) -> str:
        """현재 레이어에 실제 데이터가 있는 첫 번째 배치 원본의 표시명을 반환한다."""
        checked_sources: set[str] = set()
        for placement_id in self.placement_order:
            placement = self.placements.get(placement_id)
            if placement is None or placement.source_id in checked_sources:
                continue
            checked_sources.add(placement.source_id)
            source = self.sources.get(placement.source_id)
            if source is not None and 0 <= layer_index < len(source.images):
                if source.kind == "stl":
                    return f"[STL] {source.name} · 층 {layer_index + 1}"
                return source.images[layer_index].name
        for source in self.sources.values():
            if 0 <= layer_index < len(source.images):
                if source.kind == "stl":
                    return f"[STL] {source.name} · 층 {layer_index + 1}"
                return source.images[layer_index].name
        return "해당 레이어 데이터 없음"

    def _sync_layer_scrub_display(self) -> None:
        maximum = self._maximum_layer_count()
        self._layer_scrub_updating = True
        try:
            self.layer_scrub_var.set(float(self.current_layer + 1 if maximum else 1))
        finally:
            self._layer_scrub_updating = False

        if maximum <= 0:
            self.layer_scrub_info_var.set("이미지 세트 또는 STL을 추가하면 휠로 레이어를 연속 미리보기할 수 있습니다.")
            return

        current = self.current_layer + 1
        start = max(1, current - 2)
        end = min(maximum, current + 2)
        neighbors = []
        for number in range(start, end + 1):
            neighbors.append(f"[{number}]" if number == current else str(number))
        filename = self._reference_name_for_layer(self.current_layer)
        self.layer_scrub_info_var.set(f"{'  '.join(neighbors)}\n{filename}")

    def update_layer_controls(self) -> None:
        maximum = self._maximum_layer_count()
        self.layer_spin.configure(from_=1, to=max(1, maximum))
        self.layer_scrub_scale.configure(from_=1, to=max(1, maximum))
        if maximum == 0:
            self.current_layer = 0
            self.layer_var.set(1)
            self.layer_scrub_scale.state(["disabled"])
        else:
            self.current_layer = min(self.current_layer, maximum - 1)
            self.layer_var.set(self.current_layer + 1)
            self.layer_scrub_scale.state(["!disabled"])
        self.layer_total_var.set(f"/ {maximum}")
        self._sync_layer_scrub_display()

    def _cancel_pending_layer_redraw(self) -> None:
        if self._layer_redraw_after_id is not None:
            try:
                self.root.after_cancel(self._layer_redraw_after_id)
            except Exception:
                pass
            self._layer_redraw_after_id = None

    def _commit_layer_preview(self) -> None:
        self._layer_redraw_after_id = None
        self.redraw_canvas()
        if self.selected_id:
            self.select_placement(self.selected_id)

    def _set_current_layer(self, layer_index: int, redraw_delay_ms: int = 0) -> bool:
        maximum = self._maximum_layer_count()
        if maximum <= 0:
            return False
        layer_index = max(0, min(maximum - 1, int(layer_index)))
        changed = layer_index != self.current_layer
        self.current_layer = layer_index
        self.layer_var.set(layer_index + 1)
        self._sync_layer_scrub_display()
        if not changed:
            return False

        self._fast_preview_base_cache.clear()
        self._cancel_pending_layer_redraw()
        if redraw_delay_ms <= 0:
            self._commit_layer_preview()
        else:
            self._layer_redraw_after_id = self.root.after(
                max(1, int(redraw_delay_ms)), self._commit_layer_preview
            )
        return True

    def _on_layer_spin(self) -> None:
        self._set_layer_from_var()

    def _on_layer_event(self, _event=None) -> None:
        self._set_layer_from_var()

    def _set_layer_from_var(self) -> None:
        maximum = self._maximum_layer_count()
        if maximum <= 0:
            return
        try:
            value = int(self.layer_var.get())
        except (ValueError, tk.TclError):
            return
        value = max(1, min(maximum, value))
        self._set_current_layer(value - 1, redraw_delay_ms=0)

    def _on_layer_scrub_scale(self, value: object) -> None:
        if self._layer_scrub_updating:
            return
        try:
            layer_index = round(float(value)) - 1
        except (TypeError, ValueError, tk.TclError):
            return
        # 슬라이더를 끄는 동안 다수의 이벤트가 발생하므로 마지막 요청만 약간 지연해 그린다.
        self._set_current_layer(layer_index, redraw_delay_ms=24)

    def _on_layer_scrub_release(self, _event=None):
        if self._layer_redraw_after_id is not None:
            self._cancel_pending_layer_redraw()
            self._commit_layer_preview()
        return "break"

    def _layer_preview_wheel(self, event):
        """연속 미리보기 바에서 휠로 이전/다음 레이어를 빠르게 탐색한다."""
        maximum = self._maximum_layer_count()
        if maximum <= 0:
            return "break"

        event_num = getattr(event, "num", None)
        if event_num == 4:
            steps = 1
        elif event_num == 5:
            steps = -1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            self._layer_wheel_remainder += delta
            steps = int(self._layer_wheel_remainder / 120)
            if steps == 0:
                # 고해상도 터치패드처럼 120보다 작은 delta도 한 번의 탐색으로 반응시킨다.
                steps = 1 if delta > 0 else -1
                self._layer_wheel_remainder = 0
            else:
                self._layer_wheel_remainder -= steps * 120

        # 휠 위는 이전(낮은 층), 휠 아래는 다음(높은 층)으로 이동한다.
        jump = 10 if (getattr(event, "state", 0) & 0x0001) else 1
        target = self.current_layer - steps * jump
        self._set_current_layer(target, redraw_delay_ms=18)
        return "break"

    def change_layer(self, delta: int) -> None:
        self._set_current_layer(self.current_layer + delta, redraw_delay_ms=0)

    # ---------------------- Settings / output path ----------------------
    def _load_settings(self) -> Dict[str, object]:
        defaults: Dict[str, object] = {
            "canvas_width": 2000,
            "canvas_height": 2000,
            "dpi_x": 400,
            "dpi_y": 400,
            "stl_layer_height_mm": 0.1,
        }
        try:
            if not self.settings_path.is_file():
                return defaults
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            result = defaults.copy()
            for key in ("canvas_width", "canvas_height", "dpi_x", "dpi_y"):
                value = int(data.get(key, result[key]))
                if value > 0:
                    result[key] = value
            layer_height = float(data.get("stl_layer_height_mm", result["stl_layer_height_mm"]))
            if layer_height > 0:
                result["stl_layer_height_mm"] = layer_height
            return result
        except Exception:
            return defaults

    def _bind_setting_persistence(self) -> None:
        for variable in (
            self.canvas_width_var, self.canvas_height_var, self.dpi_x_var, self.dpi_y_var,
            self.stl_layer_height_var,
        ):
            variable.trace_add("write", self._schedule_settings_save)

    def _schedule_settings_save(self, *_args) -> None:
        if self._settings_save_after_id is not None:
            try:
                self.root.after_cancel(self._settings_save_after_id)
            except Exception:
                pass
        self._settings_save_after_id = self.root.after(500, self._save_settings)

    def _save_settings(self) -> None:
        self._settings_save_after_id = None
        try:
            values = {
                "canvas_width": int(self.canvas_width_var.get()),
                "canvas_height": int(self.canvas_height_var.get()),
                "dpi_x": int(self.dpi_x_var.get()),
                "dpi_y": int(self.dpi_y_var.get()),
                "stl_layer_height_mm": float(self.stl_layer_height_var.get()),
            }
            if min(float(value) for value in values.values()) <= 0:
                return
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.settings_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.settings_path)
        except (ValueError, tk.TclError, OSError):
            pass

    def _ordered_source_origins(self) -> List[Path]:
        result: List[Path] = []
        seen = set()
        for placement_id in self.placement_order:
            placement = self.placements.get(placement_id)
            source = self.sources.get(placement.source_id) if placement else None
            if source is None:
                continue
            origin = source.origin_path
            key = os.path.normcase(str(origin))
            if key not in seen:
                seen.add(key)
                result.append(origin)
        return result

    def _suggest_output_folder(self) -> Optional[Path]:
        origins = self._ordered_source_origins()
        if not origins:
            return None
        names = [path.name if path.is_dir() else path.stem for path in origins]
        title = common_folder_title(names)
        first_parent = origins[0] if origins[0].is_dir() else origins[0].parent
        if origins[0].is_dir():
            first_parent = origins[0].parent
        return first_parent / f"{title}_합성"

    def _update_auto_output_folder(self, force: bool = False) -> None:
        if self.output_folder_manual and not force:
            return
        suggested = self._suggest_output_folder()
        if suggested is None:
            if force:
                self.output_folder_var.set("")
            return
        self.output_folder_var.set(str(suggested))

    def _mark_output_folder_manual(self, _event=None):
        self.output_folder_manual = True

    def use_auto_output_folder(self) -> None:
        self.output_folder_manual = False
        self._update_auto_output_folder(force=True)
        if self.output_folder_var.get().strip():
            self.status_var.set(f"자동 출력 폴더: {self.output_folder_var.get()}")
        else:
            self.status_var.set("이미지 세트를 추가하면 출력 폴더가 자동 지정됩니다.")

    def _on_close(self) -> None:
        self._save_settings()
        self.export_cancel.set()
        self.root.destroy()

    # ---------------------------- Export ----------------------------
    def choose_output_folder(self) -> None:
        current_text = self.output_folder_var.get().strip()
        initial_dir = None
        if current_text:
            current = Path(current_text)
            initial_dir = current if current.is_dir() else current.parent
        dialog_options = {"title": "합성 이미지 출력 폴더"}
        if initial_dir and initial_dir.exists():
            dialog_options["initialdir"] = str(initial_dir)
        folder = filedialog.askdirectory(**dialog_options)
        if folder:
            self.output_folder_manual = True
            self.output_folder_var.set(folder)

    def start_export(self) -> None:
        if self.stl_import_active:
            messagebox.showinfo(APP_NAME, "STL 슬라이싱이 끝난 뒤 전체 합성을 실행하세요.")
            return
        active = [p for p in self.placements.values() if p.visible and p.source_id in self.sources]
        if not active:
            messagebox.showwarning(APP_NAME, "출력할 배치 항목이 없습니다.")
            return
        output_text = self.output_folder_var.get().strip()
        if not output_text:
            self._update_auto_output_folder(force=True)
            output_text = self.output_folder_var.get().strip()
        if not output_text:
            self.choose_output_folder()
            output_text = self.output_folder_var.get().strip()
            if not output_text:
                return

        self._save_settings()
        try:
            canvas_size = (int(self.canvas_width_var.get()), int(self.canvas_height_var.get()))
            dpi = (int(self.dpi_x_var.get()), int(self.dpi_y_var.get()))
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_NAME, "해상도와 DPI는 정수여야 합니다.")
            return
        if min(*canvas_size, *dpi) <= 0:
            messagebox.showerror(APP_NAME, "해상도와 DPI는 1 이상이어야 합니다.")
            return

        # STL은 추가 시점의 DPI로 래스터화된다. 이후 DPI가 바뀌었으면 재추가를 요구해
        # 물리 치수와 출력 픽셀 크기가 조용히 어긋나는 것을 방지한다.
        try:
            current_stl_layer_height = float(self.stl_layer_height_var.get())
        except (ValueError, tk.TclError):
            current_stl_layer_height = 0.0
        mismatched_stl = []
        for placement in active:
            source = self.sources.get(placement.source_id)
            if source and source.kind == "stl" and (
                (source.dpi_x, source.dpi_y) != dpi
                or abs(source.layer_height_mm - current_stl_layer_height) > 1e-9
            ):
                mismatched_stl.append(source.name)
        if mismatched_stl:
            names = ", ".join(dict.fromkeys(mismatched_stl))
            messagebox.showwarning(
                APP_NAME,
                "현재 DPI 또는 STL 층두께가 STL을 슬라이싱할 때의 설정과 다릅니다.\n"
                "해당 STL을 삭제한 뒤 현재 DPI로 다시 추가하세요.\n\n"
                f"대상: {names}",
            )
            return

        # 작업 중 UI 변경의 영향을 받지 않도록 스냅샷을 만든다.
        sources_snapshot = {sid: clone_source_set(source) for sid, source in self.sources.items()}
        placements_snapshot = [
            Placement(p.placement_id, p.source_id, p.label, p.x, p.y, p.visible, normalize_rotation(p.rotation))
            for pid in self.placement_order
            if (p := self.placements.get(pid)) is not None
        ]

        self.export_cancel.clear()
        self.export_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_var.set(0)
        self.status_var.set("전체 레이어 합성을 시작합니다.")

        worker = threading.Thread(
            target=self._export_worker,
            args=(
                canvas_size,
                dpi,
                sources_snapshot,
                placements_snapshot,
                Path(output_text),
                self.length_mode_var.get(),
                self.merge_mode_var.get(),
            ),
            daemon=True,
        )
        worker.start()
        self.root.after(100, self._poll_export_queue)

    def _export_worker(
        self,
        canvas_size: Tuple[int, int],
        dpi: Tuple[int, int],
        sources: Dict[str, SourceSet],
        placements: Sequence[Placement],
        output_folder: Path,
        length_mode: str,
        merge_mode: str,
    ) -> None:
        try:
            active = [p for p in placements if p.visible and p.source_id in sources]
            counts = [len(sources[p.source_id].images) for p in active]
            total = min(counts) if length_mode == "min" else max(counts)
            if total <= 0:
                raise ValueError("출력할 이미지가 없습니다.")

            output_folder.mkdir(parents=True, exist_ok=True)
            output_names = output_names_from_sources(sources, active, total)
            worker_count = recommended_export_workers(canvas_size)
            self.export_queue.put(("workers", worker_count))

            def process_layer(layer_index: int) -> Tuple[int, str]:
                if self.export_cancel.is_set():
                    raise concurrent.futures.CancelledError()
                merged = compose_layer(canvas_size, sources, active, layer_index, merge_mode)
                output_path = output_folder / output_names[layer_index]
                save_merged_image(merged, output_path, dpi)
                return layer_index, output_path.name

            completed_count = 0
            futures: Dict[concurrent.futures.Future, int] = {}
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="slice-merge",
            ) as executor:
                for layer_index in range(total):
                    if self.export_cancel.is_set():
                        break
                    future = executor.submit(process_layer, layer_index)
                    futures[future] = layer_index

                for future in concurrent.futures.as_completed(futures):
                    if self.export_cancel.is_set():
                        for pending in futures:
                            pending.cancel()
                        self.export_queue.put(("cancelled", completed_count))
                        return
                    try:
                        layer_index, output_name = future.result()
                    except concurrent.futures.CancelledError:
                        continue
                    completed_count += 1
                    percent = round(completed_count * 100 / total)
                    self.export_queue.put(
                        ("progress", (percent, completed_count, total, output_name, layer_index + 1))
                    )

            if self.export_cancel.is_set():
                self.export_queue.put(("cancelled", completed_count))
                return
            self.export_queue.put(("finished", (str(output_folder), total, worker_count)))
        except Exception:
            self.export_queue.put(("error", traceback.format_exc()))

    def _poll_export_queue(self) -> None:
        keep_polling = True
        try:
            while True:
                event, payload = self.export_queue.get_nowait()
                if event == "workers":
                    self.status_var.set(f"고속 합성 준비 완료 · 병렬 작업 {payload}개")
                elif event == "progress":
                    percent, current, total, name, source_layer = payload
                    self.progress_var.set(percent)
                    self.status_var.set(
                        f"고속 합성 중: {current}/{total} 완료 · 원본 레이어 {source_layer} · {name}"
                    )
                elif event == "finished":
                    folder, total, worker_count = payload
                    self._finish_export_ui()
                    messagebox.showinfo(
                        APP_NAME,
                        f"전체 레이어 합성을 완료했습니다.\n\n"
                        f"출력 수: {total}장\n병렬 작업: {worker_count}개\n폴더: {folder}",
                    )
                    self.status_var.set(f"합성 완료: {total}장 · {folder}")
                    keep_polling = False
                elif event == "cancelled":
                    self._finish_export_ui()
                    self.status_var.set(f"사용자가 합성을 취소했습니다. 완료된 레이어: {payload}장")
                    keep_polling = False
                elif event == "error":
                    self._finish_export_ui()
                    messagebox.showerror(APP_NAME, f"합성 중 오류가 발생했습니다.\n\n{payload}")
                    self.status_var.set("합성 실패")
                    keep_polling = False
        except queue.Empty:
            pass
        if keep_polling and str(self.export_button.cget("state")) == "disabled":
            self.root.after(100, self._poll_export_queue)

    def _finish_export_ui(self) -> None:
        self.export_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def cancel_export(self) -> None:
        self.export_cancel.set()
        self.status_var.set("현재 레이어 저장 후 취소합니다.")


def main() -> int:
    # tkinterdnd2가 준비되면 TkinterDnD.Tk를 우선 사용한다.
    # tkdnd 바이너리 로딩이 실패해도 일반 Tk로 즉시 폴백한다.
    root = None
    dnd_root_error = None
    if TkinterDnD is not None:
        try:
            root = TkinterDnD.Tk()
        except Exception as exc:
            dnd_root_error = exc
    if root is None:
        root = tk.Tk()
    try:
        # Windows 고해상도 화면에서 Tk가 흐릿해지는 것을 줄인다.
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        app = SliceLayoutApp(root)
        if dnd_root_error is not None:
            app.status_var.set(
                f"드래그앤드롭 모듈 로딩 실패: {dnd_root_error}. 폴더 추가 버튼은 정상 사용 가능합니다."
            )
        root.mainloop()
        return 0
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror(APP_NAME, traceback.format_exc())
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
