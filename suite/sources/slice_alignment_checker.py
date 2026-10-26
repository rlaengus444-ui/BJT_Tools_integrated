# -*- coding: utf-8 -*-
"""슬라이싱 위치 검사 — 단일 파일 간편판 v3.3.

실행: python slice_alignment_checker.py
화면 입력은 전체 이미지(ZIP 또는 여러 이미지 선택), DPI, 검사 모드입니다.
치환 경계 검사에서는 치환 시작 층을 입력하고, 전체 스캔에서는 생략합니다.
예: 1~300층 중 61~300층을 바꿨다면 전체 300층과 61을 입력합니다.
파일 번호 0..N-1 / 1..N을 자동 인식하고 경계 전후 이동 추이를 검사합니다.
다른 층의 단면 변화를 포함하므로 모든 판정은 참고용입니다.
CLI: python slice_alignment_checker.py --zip mixed.zip --dpi 400 --switch-layer 61
전체 스캔: python slice_alignment_checker.py --zip mixed.zip --dpi 400 --full-scan

NumPy/Pillow/OpenCV가 없으면 이 파일 옆 .slice_alignment_dependencies에 설치합니다.
첫 설치에는 인터넷이 필요합니다. --no-install로 자동 설치를 끌 수 있습니다.
Python 3.10~3.13 64비트를 권장합니다. PNG/BMP/TIF/JPG 지원, CTB 직접 해석 미지원.
원본 이미지는 변경하지 않고 입력 파일 옆 slice_alignment_reports에 보고서를 저장합니다.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import traceback

_SINGLE_DEPS = Path(__file__).resolve().parent / ".slice_alignment_dependencies"
if _SINGLE_DEPS.is_dir():
    sys.path.insert(0, str(_SINGLE_DEPS))


def _dependencies_available():
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import numpy, PIL, cv2; from PIL import Image; "
             "assert tuple(int(v) for v in numpy.__version__.split('.')[:2]) >= (1, 24); "
             "assert hasattr(Image, 'Resampling'); "
             "assert tuple(int(v) for v in cv2.__version__.split('.')[:2]) >= (4, 10)")
    completed = subprocess.run([sys.executable, "-c", probe, str(_SINGLE_DEPS)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return completed.returncode == 0


def _ensure_dependencies():
    if _dependencies_available():
        return
    packages = ["numpy==2.2.6", "Pillow==11.3.0", "opencv-python-headless==4.12.0.88"]
    if "--no-install" in sys.argv:
        raise RuntimeError("NumPy/Pillow/OpenCV를 불러오지 못했습니다. --no-install을 빼고 실행하거나 필요한 패키지를 설치하세요.")
    if not (3, 10) <= sys.version_info[:2] <= (3, 13) or sys.maxsize <= 2**32:
        raise RuntimeError("자동 설치에는 Python 3.10~3.13 64비트가 필요합니다. Python 3.11 64비트를 권장합니다.")
    _SINGLE_DEPS.mkdir(exist_ok=True)
    print("필요한 영상처리 패키지를 설치합니다. 첫 설치에는 인터넷이 필요합니다.", flush=True)
    completed = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                "--upgrade", "--only-binary", ":all:", "--target", str(_SINGLE_DEPS), *packages], check=False)
    if completed.returncode != 0 or not _dependencies_available():
        raise RuntimeError("패키지 설치 또는 불러오기에 실패했습니다. 위 오류, 인터넷 연결, 폴더 쓰기 권한을 확인하세요.")
    sys.path.insert(0, str(_SINGLE_DEPS))


def _startup_failure():
    details = traceback.format_exc()
    print(details, file=sys.stderr)
    try:
        log_path = Path(__file__).resolve().with_suffix(".error.log")
        log_path.write_text(details, encoding="utf-8")
        print("오류 기록: " + str(log_path), file=sys.stderr)
    except OSError:
        log_path = None
    if not sys.argv[1:] or "--gui" in sys.argv:
        try:
            import tkinter as error_tk
            from tkinter import messagebox as error_messagebox
            error_root = error_tk.Tk()
            error_root.withdraw()
            error_messagebox.showerror("실행 오류", details[-2500:] + ("\n\n오류 기록: " + str(log_path) if log_path else ""))
            error_root.destroy()
        except Exception:
            pass


import argparse


def build_parser():
    parser = argparse.ArgumentParser(description="슬라이싱의 치환 경계 또는 전체 인접층에서 위치 이동 신호를 검사합니다.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--zip", dest="zip_path", help="전체 슬라이싱 이미지 ZIP")
    inputs.add_argument("--images", nargs="+", help="전체 슬라이싱 이미지 파일 목록")
    parser.add_argument("--dpi", type=float, default=400, help="실제 XY 해상도 DPI (기본 400)")
    parser.add_argument("--switch-layer", type=int, default=61, help="치환한 첫 층 (기본 61, 전체 스캔에서는 사용하지 않음)")
    parser.add_argument("--full-scan", action="store_true", help="치환 층 지정 없이 전체 인접층을 검사")
    parser.add_argument("--gui", action="store_true", help="화면으로 실행")
    parser.add_argument("--no-install", action="store_true", help="의존 패키지 자동 설치를 하지 않음")
    return parser




if __name__ == "__main__" and any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    # Show help without loading or installing scientific packages.
    build_parser().parse_args()
    raise SystemExit(0)


if __name__ == "__main__":
    try:
        _ensure_dependencies()
    except Exception:
        _startup_failure()
        raise SystemExit(3)


# ── 같은 층 형상 위치 비교 ──
"""Conservative, addition-tolerant registration of SAME-LAYER slice images.

The reference contains the parts already being printed; the candidate may also
contain newly added parts.  Components of the reference are matched separately
to candidate components.  A foreground-pixel count over the entire bed is not
used as proof of alignment: a newly added part must not hide an existing shift.

Public API
----------
compare_slices(reference, candidate, *, max_shift_pixels, threshold=127,
               min_component_area=8, min_match_score=0.85, max_components=160)

Inputs are 2-D grayscale/bool numpy arrays or RGB/RGBA arrays, with white/True
meaning printable foreground.  Both images must use the same pixel grid and
the SAME layer number.  Outputs are JSON-serializable and use integer pixels;
positive dx is right and positive dy is down. uint8 uses [0,255], uint16
uses [0,65535]; only float inputs may use normalized [0,1]. Connected regions are image
anchors, not guaranteed to be one physical product each.

Status: unchanged / shifted / inconclusive.  Detection status:
unchanged / shifted / ambiguous / unmatched.  Only reliable, unique matches
are counted as confirmed.  Repeated identical shapes, clipped shapes, newly
touching parts and substantial shape changes can require manual review.
This module does not estimate rotation, scale, or fractional-pixel shifts.

OpenCV connectedComponentsWithStats identifies anchors; integer overlaps
are computed at 25 local offsets. A full-component Dice score includes
both missing and extra foreground, so a large filled patch cannot count as a
perfect match to a smaller original shape.
"""


import math
from typing import Any

import cv2
import numpy as np


_LOCAL_RADIUS = 2
_AMBIGUITY_MARGIN = 0.015
_STRONG_GEOMETRY_SCORE = 0.97
VERSION = "3.3.0"


def _mask(image: np.ndarray, threshold: float) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim not in (2, 3):
        raise ValueError("Images must be 2-D grayscale or 3-D RGB/RGBA arrays.")
    if array.size == 0 or any(v == 0 for v in array.shape[:2]):
        raise ValueError("Image is empty.")
    # Unsigned bytes are necessarily finite and in [0,255]. LayerSource uses
    # this format, so avoid three redundant full-image validation scans.
    if array.dtype == np.uint8:
        if array.ndim == 2:
            # Produce the 0/1 uint8 mask directly, avoiding a full-size bool
            # temporary followed by another full-image conversion.
            # Keep NumPy comparisons for unusual numeric types (e.g. Decimal)
            # whose exact boundary could change when converted to float64.
            ordinary = isinstance(threshold, (int, float, np.integer, np.floating))
            extended = isinstance(threshold, np.floating) and threshold.dtype.itemsize > 8
            if ordinary and not extended and 0 <= threshold <= 255:
                return cv2.threshold(array, float(threshold), 1, cv2.THRESH_BINARY)[1]
            return np.ascontiguousarray(array > threshold, dtype=np.uint8)
        scale = 1.0
    else:
        if array.dtype.kind not in "buif" or not np.all(np.isfinite(array)):
            raise ValueError("Image must contain finite, real numeric pixels.")
        if float(array.min()) < 0:
            raise ValueError("Image pixel values must be non-negative.")
        # Pixel encoding, not the brightest pixel in each slice, defines scale.
        # In particular uint8 intensity 1 is dark; it is not normalized white.
        if array.dtype == np.bool_:
            scale = 255.0
        elif array.dtype.kind == "u" and array.dtype.itemsize == 2:
            scale = 255.0 / 65535.0
        elif array.dtype.kind == "f" and float(array.max()) <= 1.0:
            scale = 255.0
        else:
            if float(array.max()) > 255.0:
                raise ValueError("Use uint8 [0,255], uint16 [0,65535], bool, or float [0,1]/[0,255].")
            scale = 1.0
        if array.ndim == 2 and scale == 1.0:
            return np.ascontiguousarray(array > threshold, dtype=np.uint8)
    array = array.astype(np.float32) * scale
    if array.ndim == 3:
        if array.shape[2] not in (3, 4):
            raise ValueError("Color images must have 3 or 4 channels.")
        rgb = array[..., :3]
        # This is RGB (as returned by Pillow), not OpenCV's default BGR.
        gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        if array.shape[2] == 4:
            gray = gray * (array[..., 3] / 255.0)
        array = gray
    return np.ascontiguousarray(array > threshold, dtype=np.uint8)


def _intersection(reference: np.ndarray, other: np.ndarray,
                  other_x: int, other_y: int) -> int:
    """Intersection when ``other`` is at this offset in reference coordinates."""
    rh, rw = reference.shape
    oh, ow = other.shape
    left, top = max(0, other_x), max(0, other_y)
    right, bottom = min(rw, other_x + ow), min(rh, other_y + oh)
    if right <= left or bottom <= top:
        return 0
    a = reference[top:bottom, left:right]
    b = other[top - other_y:bottom - other_y, left - other_x:right - other_x]
    # OpenCV accepts row-strided ROIs and counts bytes without NumPy's extra
    # reduction overhead. Preserve the general helper's dtype/large-array path.
    if a.dtype == np.uint8 and b.dtype == np.uint8 and a.size <= 2_147_483_647:
        return int(cv2.countNonZero(cv2.bitwise_and(a, b)))
    return int(np.count_nonzero(a & b))


_BYTE_POPCOUNTS = np.array([value.bit_count() for value in range(256)], dtype=np.uint8)


class _PackedLocalOverlap:
    """Exact binary overlaps at the five local X offsets, in 1/8 the space.

    Labels produce 0/1 masks. Packing changes storage only: the LUT counts all
    intersecting bits, including partial last bytes and clipped translations.
    Keep the general _intersection path for arbitrary byte values/offsets.
    """
    def __init__(self, reference, candidate):
        self.reference = np.packbits(reference, axis=1, bitorder="little")
        packed = np.packbits(candidate, axis=1, bitorder="little")
        self.candidate = np.zeros((candidate.shape[0],
                                   max(self.reference.shape[1], packed.shape[1]) + 2), dtype=np.uint8)
        self.candidate[:, 1:packed.shape[1] + 1] = packed
        self.reference_height = reference.shape[0]
        self.candidate_height = candidate.shape[0]
        self.shifts = {}

    def intersection(self, x, y):
        columns = self.reference.shape[1]
        if x not in self.shifts:
            center = self.candidate[:, 1:columns + 1]
            if x == 0:
                shifted = center
            elif x > 0:
                shifted = (center << x) | (self.candidate[:, :columns] >> (8 - x))
            else:
                shift = -x
                shifted = (center >> shift) | (self.candidate[:, 2:columns + 2] << (8 - shift))
            self.shifts[x] = shifted
        top, bottom = max(0, y), min(self.reference_height, y + self.candidate_height)
        if bottom <= top:
            return 0
        a = self.reference[top:bottom]
        b = self.shifts[x][top - y:bottom - y]
        return int(cv2.sumElems(cv2.LUT(cv2.bitwise_and(a, b), _BYTE_POPCOUNTS))[0])


def _residual_geometry(reference, candidate, candidate_x, candidate_y):
    """Count shape differences beyond one pixel from the aligned contour.

    Symmetric contour containment includes holes as well as outside edges.
    It prevents a large body from hiding a moved thin support or small hole.
    One pixel of contour slack allows raster/AA variation; this is a geometry
    guard, not a subpixel motion estimate.
    """
    rh, rw = reference.shape
    ch, cw = candidate.shape
    left, top = min(0, candidate_x), min(0, candidate_y)
    right, bottom = max(rw, candidate_x + cw), max(rh, candidate_y + ch)
    a = np.zeros((bottom - top, right - left), dtype=np.uint8)
    b = np.zeros_like(a)
    a[-top:rh - top, -left:rw - left] = reference
    x, y = candidate_x - left, candidate_y - top
    b[y:y + ch, x:x + cw] = candidate
    kernel = np.ones((3, 3), dtype=np.uint8)
    # Foreground-only dilation would fill tiny holes and hide their movement.
    cv2.subtract(a, cv2.erode(a, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0), dst=a)
    cv2.subtract(b, cv2.erode(b, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0), dst=b)
    # Reuse one work buffer for both directions. uint8 subtraction retains
    # the original wraparound semantics even for direct nonbinary helper use.
    expanded = cv2.dilate(a, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    np.subtract(np.uint8(1), expanded, out=expanded)
    cv2.bitwise_and(b, expanded, dst=expanded)
    count = cv2.countNonZero if expanded.size <= 2_147_483_647 else np.count_nonzero
    extra = int(count(expanded))
    cv2.dilate(b, kernel, dst=expanded, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    np.subtract(np.uint8(1), expanded, out=expanded)
    cv2.bitwise_and(a, expanded, dst=expanded)
    missing = int(count(expanded))
    return missing, extra


def _pair(reference_mask: np.ndarray, reference_bbox: tuple[int, int, int, int],
          reference_area: int, candidate_labels: np.ndarray,
          candidate_stats: np.ndarray, candidate_id: int,
          max_shift: int, max_shift_y: int) -> dict[str, Any] | None:
    rx, ry, rw, rh = reference_bbox
    cx, cy, cw, ch, candidate_area = map(int, candidate_stats[candidate_id])
    candidate_mask = np.ascontiguousarray(
        candidate_labels[cy:cy + ch, cx:cx + cw] == candidate_id,
        dtype=np.uint8)

    # Local refinement is only a few pixels: the component bounding boxes give
    # the large displacement directly, without a full-bed exhaustive search.
    # Only 25 integer overlaps are needed. Avoid a full correlation map when
    # bounding boxes differ greatly; that could consume gigabytes needlessly.
    # Equal finite nonempty masks have Dice 1 only at local offset (0, 0).
    # Any nonzero translation loses at least one foreground pixel. The area
    # guard keeps that loss safely outside the original 1e-10 plateau slack.
    # A bbox displacement outside the search window still needs refinement.
    exact = (0 < reference_area <= 1_000_000_000
             and reference_area == candidate_area
             and (rw, rh) == (cw, ch)
             and abs(cx - rx) <= max_shift and abs(cy - ry) <= max_shift_y
             and np.array_equal(reference_mask, candidate_mask))
    if exact:
        best_score = 1.0
        dx, dy = cx - rx, cy - ry
        plateau_pixels = 1
    else:
        scores = []
        # Large, slowly changing components otherwise scan the same multi-MP
        # masks 25 times. Bit packing retains exact integer intersection counts.
        packed_overlap = (_PackedLocalOverlap(reference_mask, candidate_mask)
                          if reference_mask.size + candidate_mask.size >= 512 * 1024
                          and _LOCAL_RADIUS < 8 and reference_mask.dtype == np.uint8
                          and int(reference_mask.max()) <= 1 else None)
        for oy in range(-_LOCAL_RADIUS, _LOCAL_RADIUS + 1):
            for ox in range(-_LOCAL_RADIUS, _LOCAL_RADIUS + 1):
                dx, dy = cx - rx + ox, cy - ry + oy
                if abs(dx) <= max_shift and abs(dy) <= max_shift_y:
                    overlap = (packed_overlap.intersection(-ox, -oy) if packed_overlap is not None else
                               _intersection(reference_mask, candidate_mask, -ox, -oy))
                    scores.append((2.0 * overlap / (reference_area + candidate_area), ox, oy, dx, dy))
        if not scores:
            return None
        best_score = max(score[0] for score in scores)
        locations = [score for score in scores if score[0] >= best_score - 1e-10]
        # Equal plateaus inside one candidate are not proof of a precise offset.
        # Keep the closest-to-bbox location deterministically for display.
        _, _, _, dx, dy = min(locations, key=lambda score: score[1]**2 + score[2]**2)
        plateau_pixels = len(locations)
    zero_overlap = (reference_area if exact and dx == dy == 0 else
                    _intersection(reference_mask, candidate_mask, cx - rx, cy - ry))
    zero_score = 2.0 * zero_overlap / float(reference_area + candidate_area)
    area_ratio = candidate_area / float(reference_area)
    shape_stable = (
        best_score >= _STRONG_GEOMETRY_SCORE and
        0.97 <= area_ratio <= 1.03 and
        abs(cw - rw) <= max(2, 0.025 * rw) and
        abs(ch - rh) <= max(2, 0.025 * rh))
    missing_far = extra_far = None
    if shape_stable:
        if exact:
            missing_far = extra_far = 0
        else:
            missing_far, extra_far = _residual_geometry(
                reference_mask, candidate_mask, cx - rx - dx, cy - ry - dy)
        shape_stable = missing_far == 0 and extra_far == 0
    return {
        "candidate_id": int(candidate_id),
        "candidate_bbox": [cx, cy, cw, ch],
        "candidate_area_px": candidate_area,
        "dx_pixels": dx, "dy_pixels": dy,
        "match_score": round(best_score, 8),
        "zero_score": round(float(zero_score), 8),
        "score_improvement": round(best_score - zero_score, 8),
        "area_ratio": round(area_ratio, 8),
        "shape_stable": bool(shape_stable),
        "missing_beyond_contour_slack_px": missing_far,
        "extra_beyond_contour_slack_px": extra_far,
        "contour_slack_pixels": 1,
        "peak_plateau_pixels": int(plateau_pixels),
        "search_boundary": bool((max_shift > 0 and abs(dx) == max_shift) or
                                (max_shift_y > 0 and abs(dy) == max_shift_y)),
    }


def _base_result(shape: tuple[int, ...], max_shift: int,
                 min_area: int, min_score: float) -> dict[str, Any]:
    return {
        "status": "inconclusive",
        "reason": "not_evaluated",
        "motion_pattern": "unresolved",
        "global_translation": None,
        "detections": [],
        "diagnostics": {
            "image_shape": list(map(int, shape)),
            "reference_blank": False, "candidate_blank": False,
            "shape_mismatch": False,
            "total_components": 0, "checked_components": 0,
            "ignored_components": 0, "ignored_area_px": 0,
            "coverage_fraction": 0.0,
            "reference_foreground_px": 0, "candidate_foreground_px": 0,
            "search_boundary_components": 0,
            "ambiguous_components": 0, "unmatched_components": 0,
            "reliable_components": 0,
            "max_shift_pixels": max_shift,
            "min_component_area": min_area,
            "min_match_score": min_score,
            "pixel_resolution": "integer",
            "limitations": [
                "same_layer_comparison_required",
                "translation_only_rotation_and_scale_not_estimated",
                "connected_regions_are_anchors_not_physical_part_ids",
                "identical_repeated_shapes_can_be_unidentifiable",
                "touching_or_clipped_shapes_may_require_manual_review",
            ],
        },
    }


class _PreparedSlice:
    """Run-local binary image and lazily computed, read-only component labels."""
    __slots__ = ("mask", "area", "_components")

    def __init__(self, mask):
        self.mask = mask
        self.area = int(np.count_nonzero(mask))
        self._components = None

    def components(self):
        if self._components is None:
            self._components = cv2.connectedComponentsWithStats(self.mask, 8)
        return self._components


def _prepare_slice(image, threshold, previous=None):
    mask = _mask(image, threshold)
    # Reuse only after exact, full-resolution equality, never a sampled hash.
    if previous is not None and np.array_equal(mask, previous.mask):
        return previous
    return _PreparedSlice(mask)


def compare_slices(reference: np.ndarray, candidate: np.ndarray, *,
                   max_shift_pixels: int,
                   max_shift_y_pixels: int | None = None,
                   threshold: float = 127,
                   min_component_area: int = 8,
                   min_match_score: float = 0.85,
                   max_components: int = 160) -> dict[str, Any]:
    """Find unique per-anchor XY translations while allowing new components."""
    return _compare_slices(reference, candidate,
                           max_shift_pixels=max_shift_pixels,
                           max_shift_y_pixels=max_shift_y_pixels,
                           threshold=threshold, min_component_area=min_component_area,
                           min_match_score=min_match_score, max_components=max_components)


def _compare_slices(reference: np.ndarray, candidate: np.ndarray, *,
                   max_shift_pixels: int,
                   max_shift_y_pixels: int | None = None,
                   threshold: float = 127,
                   min_component_area: int = 8,
                   min_match_score: float = 0.85,
                   max_components: int = 160,
                   _prepared=None) -> dict[str, Any]:
    """Find unique per-anchor XY translations while allowing new components.

    ``max_shift_pixels`` bounds X and (by default) Y, not Euclidean distance.
    ``max_shift_y_pixels`` optionally sets an independent Y bound.  A best match
    on the limit is ambiguous, so an offset outside the configured window must
    not produce an unchanged result.  Increasing the window can resolve it.

    ``min_match_score`` is a candidate screening threshold.  Confirming a shift
    additionally requires strong geometry preservation (Dice >= .97, area
    ratio within 3%, similar bounding dimensions).  Lower-quality matches are
    reported for review, not silently accepted.  Near-equal matches to another
    component are ambiguous even if zero displacement is one of them.

    All existing anchors must be verified to report unchanged.  A reliable
    shifted anchor is sufficient to report shifted; unresolved anchors remain
    visible in diagnostics. Any excluded reference anchor forces inconclusive
    when no movement has been confirmed; even a small support can matter.
    """
    if max_shift_y_pixels is None:
        max_shift_y_pixels = max_shift_pixels
    for name, value, minimum in (("max_shift_pixels", max_shift_pixels, 0),
                                 ("max_shift_y_pixels", max_shift_y_pixels, 0),
                                 ("min_component_area", min_component_area, 1),
                                 ("max_components", max_components, 1)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}.")
    if not math.isfinite(float(threshold)) or not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255.")
    if not math.isfinite(float(min_match_score)) or not 0 < min_match_score <= 1:
        raise ValueError("min_match_score must be in (0, 1].")
    max_shift = int(max_shift_pixels)
    max_shift_y = int(max_shift_y_pixels)
    min_area, max_count = int(min_component_area), int(max_components)
    if _prepared is None:
        prepared_ref = _prepare_slice(reference, threshold)
        prepared_new = _prepare_slice(candidate, threshold)
    else:
        prepared_ref, prepared_new = _prepared
    ref, new = prepared_ref.mask, prepared_new.mask
    result = _base_result(ref.shape, max_shift, min_area, float(min_match_score))
    diagnostic = result["diagnostics"]
    diagnostic["max_shift_y_pixels"] = max_shift_y
    if ref.shape != new.shape:
        diagnostic["shape_mismatch"] = True
        diagnostic["candidate_image_shape"] = list(map(int, new.shape))
        result["reason"] = "image_dimensions_differ"
        return result
    ref_area, new_area = prepared_ref.area, prepared_new.area
    diagnostic["reference_foreground_px"] = ref_area
    diagnostic["candidate_foreground_px"] = new_area
    diagnostic["reference_blank"] = ref_area == 0
    diagnostic["candidate_blank"] = new_area == 0
    if ref_area == 0 or new_area == 0:
        result["reason"] = "reference_blank" if ref_area == 0 else "candidate_blank"
        return result

    ref_count, ref_labels, ref_stats, _ = prepared_ref.components()
    new_count, new_labels, new_stats, _ = prepared_new.components()
    diagnostic["total_components"] = int(ref_count - 1)
    eligible = [i for i in range(1, ref_count) if ref_stats[i, 4] >= min_area]
    eligible.sort(key=lambda i: int(ref_stats[i, 4]), reverse=True)
    selected = eligible[:max_count]
    checked_area = int(sum(int(ref_stats[i, 4]) for i in selected))
    diagnostic["checked_components"] = len(selected)
    diagnostic["ignored_components"] = int(ref_count - 1 - len(selected))
    diagnostic["ignored_area_px"] = ref_area - checked_area
    diagnostic["coverage_fraction"] = round(checked_area / ref_area, 8)
    diagnostic["component_limit_reached"] = len(eligible) > max_count
    if not selected:
        result["reason"] = "no_reference_component_above_minimum_area"
        return result

    candidate_ids = np.arange(1, new_count, dtype=np.int32)
    cstats = new_stats[1:]
    detections: list[dict[str, Any]] = []
    for component_id in selected:
        rx, ry, rw, rh, area = map(int, ref_stats[component_id])
        bbox = (rx, ry, rw, rh)
        reference_mask = np.ascontiguousarray(
            ref_labels[ry:ry + rh, rx:rx + rw] == component_id, dtype=np.uint8)
        # Whole candidate components, not arbitrary filled local patches, are
        # compared.  The generous filter only avoids unnecessary evaluations.
        near = (
            (np.abs(cstats[:, 0] - rx) <= max_shift + _LOCAL_RADIUS) &
            (np.abs(cstats[:, 1] - ry) <= max_shift_y + _LOCAL_RADIUS) &
            (cstats[:, 4] >= 0.70 * area) &
            (cstats[:, 4] <= 1.40 * area) &
            (cstats[:, 2] >= 0.70 * rw - 2) &
            (cstats[:, 2] <= 1.40 * rw + 2) &
            (cstats[:, 3] >= 0.70 * rh - 2) &
            (cstats[:, 3] <= 1.40 * rh + 2))
        pairs = []
        for candidate_id in candidate_ids[near]:
            pair = _pair(reference_mask, bbox, area, new_labels, new_stats,
                         int(candidate_id), max_shift, max_shift_y)
            if pair is not None:
                pairs.append(pair)
        pairs.sort(key=lambda p: (-p["match_score"],
                                  abs(p["dx_pixels"]) + abs(p["dy_pixels"]),
                                  p["candidate_id"]))
        item: dict[str, Any] = {
            "component_id": int(component_id), "area_px": area, "area": area,
            "bbox": list(bbox), "status": "unmatched",
            "reason": "no_matching_geometry_in_search_range",
            "dx_pixels": None, "dy_pixels": None,
            "dx_px": None, "dy_px": None,
            "match_score": 0.0, "zero_score": 0.0,
            "confidence": 0.0, "candidate_id": None,
            "alternatives": [],
            "plausible_candidate_ids": [p["candidate_id"] for p in pairs
                                        if p["match_score"] >= min_match_score],
        }
        if pairs:
            best = pairs[0]
            item.update(best)
            item["dx_px"], item["dy_px"] = best["dx_pixels"], best["dy_pixels"]
            alternatives = [p for p in pairs[1:]
                            if p["match_score"] >= best["match_score"] - _AMBIGUITY_MARGIN
                            and p["match_score"] >= min_match_score]
            item["alternatives"] = [
                {k: p[k] for k in ("candidate_id", "dx_pixels", "dy_pixels", "match_score")}
                for p in alternatives[:12]]
            item["alternative_count"] = len(alternatives)
            if best["match_score"] < min_match_score:
                item["reason"] = "low_shape_match"
            elif alternatives:
                item["status"], item["reason"] = "ambiguous", "multiple_similar_shapes"
            elif not best["shape_stable"]:
                item["status"], item["reason"] = "ambiguous", "geometry_changed_or_clipped"
            elif best["peak_plateau_pixels"] > 1:
                item["status"], item["reason"] = "ambiguous", "offset_not_unique"
            elif best["search_boundary"]:
                item["status"], item["reason"] = "ambiguous", "search_limit_reached"
            else:
                moved = best["dx_pixels"] != 0 or best["dy_pixels"] != 0
                item["status"] = "shifted" if moved else "unchanged"
                item["reason"] = "unique_geometry_match"
                item["confidence"] = round(best["match_score"], 8)
        detections.append(item)

    # A single candidate region cannot independently verify two old anchors.
    # This also protects against disappeared parts being mistaken for a neighbor.
    candidate_users: dict[int, list[dict[str, Any]]] = {}
    for item in detections:
        # Ambiguous anchors still own plausible candidates. Otherwise a
        # changed anchor can steal a neighbor whose own match is ambiguous.
        for candidate_id in item["plausible_candidate_ids"]:
            candidate_users.setdefault(candidate_id, []).append(item)
    for candidate_id, users in candidate_users.items():
        if len(users) > 1:
            for item in users:
                if item["status"] in {"unchanged", "shifted"} and item["candidate_id"] == candidate_id:
                    item["status"] = "ambiguous"
                    item["reason"] = "candidate_shared_by_multiple_reference_anchors"
                    item["confidence"] = 0.0

    result["detections"] = detections
    reliable = [d for d in detections if d["status"] in ("unchanged", "shifted")]
    shifted = [d for d in reliable if d["status"] == "shifted"]
    diagnostic["reliable_components"] = len(reliable)
    diagnostic["ambiguous_components"] = sum(d["status"] == "ambiguous" for d in detections)
    diagnostic["unmatched_components"] = sum(d["status"] == "unmatched" for d in detections)
    diagnostic["search_boundary_components"] = sum(bool(d.get("search_boundary")) for d in detections)
    diagnostic["verified_area_fraction"] = round(
        sum(d["area_px"] for d in reliable) / ref_area, 8)
    if reliable:
        groups: dict[tuple[int, int], int] = {}
        for item in reliable:
            key = (item["dx_pixels"], item["dy_pixels"])
            groups[key] = groups.get(key, 0) + item["area_px"]
        offset, group_area = max(groups.items(), key=lambda p: p[1])
        reliable_area = sum(d["area_px"] for d in reliable)
        agreement = group_area / reliable_area
        all_verified = len(reliable) == len(detections)
        common = len(groups) == 1 and all_verified
        result["global_translation"] = {
            "dx_pixels": offset[0], "dy_pixels": offset[1],
            "dx_px": offset[0], "dy_px": offset[1],
            "agreement_fraction": round(agreement, 8),
            "verified_area_fraction": diagnostic["verified_area_fraction"],
            "reliable": bool(common and diagnostic["ignored_components"] == 0
                             and not diagnostic["component_limit_reached"]),
        }
        if common:
            result["motion_pattern"] = "global_translation" if offset != (0, 0) else "no_translation"
        elif len(groups) > 1:
            result["motion_pattern"] = "partial_or_mixed"
        else:
            result["motion_pattern"] = "partially_verified"
    if shifted:
        result["status"] = "shifted"
        result["reason"] = ("some_existing_anchors_shifted" if
                            len(shifted) != len(detections) else
                            "existing_anchors_shifted")
    elif (len(reliable) == len(detections) and
          diagnostic["ignored_components"] == 0 and
          not diagnostic["component_limit_reached"]):
        result["status"] = "unchanged"
        result["reason"] = "all_checked_existing_anchors_unchanged"
    else:
        result["reason"] = "existing_geometry_could_not_be_fully_verified"
    return result


__all__ = ["compare_slices"]

# ── 입력과 검사 보고서 ──
"""Read-only checks of exported slice images, with physical-unit reports.

Use old/new images from the SAME layer for an alignment decision.
Adjacent-layer checks are exploratory and always return REVIEW.
"""

import argparse
import copy
import csv
from collections import OrderedDict
from datetime import datetime
import html
import io
import json
import math
from pathlib import Path
import re
import sys
from threading import Event
from typing import Callable
import zipfile
import weakref


import numpy as np
from PIL import Image, ImageDraw


IMAGE_EXTENSIONS = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg"}
STATUS_LABELS = {
    "MISALIGNED": "위치 어긋남 감지",
    "WITHIN_TOLERANCE": "검사한 기존 형상은 허용 범위 이내",
    "REVIEW": "판단 보류 / 확인 필요",
    "REVIEW_SUSPECTED": "어긋남 의심 — 인접층 참고 검사",
}
REASON_LABELS = {
    "image_dimensions_differ": "이미지 픽셀 크기가 다름",
    "reference_blank": "기존 이미지에 비교할 형상이 없음",
    "candidate_blank": "새 이미지에 기존 형상을 찾을 수 없음",
    "no_reference_component_above_minimum_area": "최소 픽셀 기준을 넘는 기존 형상이 없음",
    "multiple_similar_shapes": "같은 모양이 여러 위치에 있어 대응이 불명확함",
    "geometry_changed_or_clipped": "모양이 달라졌거나 형상이 잘림",
    "offset_not_unique": "여러 이동값이 비슷하게 맞음",
    "search_limit_reached": "검색 범위 끝에 걸림 — 검색 폭을 늘려 확인",
    "physical_search_limit_exceeded": "이동 후보가 해당 축의 물리적 검색 범위를 넘음",
    "candidate_shared_by_multiple_reference_anchors": "여러 기존 형상이 하나의 새 형상 후보에 대응함",
    "no_matching_geometry_in_search_range": "검색 범위 안에서 같은 형상을 찾지 못함",
    "low_shape_match": "기존 형상과 충분히 일치하지 않음",
    "unique_geometry_match": "같은 형상의 위치를 하나로 확인함",
}


class CheckCancelled(Exception):
    pass


def natural_key(value: str):
    return tuple(int(v) if v.isdigit() else v.casefold() for v in re.split(r"(\d+)", value))


class LayerSource:
    """A folder or ZIP, read in place without extracting or changing it.

    filename: last integer group in the file stem + offset = real layer.
    order: natural-sorted image index, starting at 1 + offset = real layer.
    Duplicate layer assignments are errors rather than silent overwrites.
    """
    def __init__(self, path, numbering="filename", offset=0):
        self.archive = None
        self.entries = {}
        # Exact encoded bytes, rather than filenames/mtime or sampled hashes,
        # identify repeats. Every layer is still read, and callers get their
        # own writable array. Keep at most two decoded images / 32 MiB.
        self._decoded_cache = OrderedDict()
        self._decoded_cache_bytes = 0
        self.numbering = numbering
        self.offset = int(offset)
        if isinstance(path, (list, tuple)):
            if not path:
                raise ValueError("전체 슬라이싱 이미지를 선택하세요.")
            selected = [Path(p).expanduser().resolve() for p in path]
            if len({os.path.normcase(str(p)) for p in selected}) != len(selected):
                raise ValueError("같은 이미지를 중복 선택했습니다.")
            for p in selected:
                if not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise ValueError(f"지원하는 이미지 파일을 선택하세요: {p}")
            self.path = Path(os.path.commonpath([str(p.parent) for p in selected]))
            self.kind = "files"
            names = [str(p) for p in selected]
        else:
            self.path = Path(path).expanduser().resolve()
            self.kind = "directory" if self.path.is_dir() else "zip"
        if self.kind == "files":
            pass
        elif self.path.is_dir():
            names = [str(p.relative_to(self.path)) for p in self.path.rglob("*")
                     if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
                     and not any(v.startswith(".") for v in p.relative_to(self.path).parts)]
        elif self.path.is_file() and zipfile.is_zipfile(self.path):
            self.archive = zipfile.ZipFile(self.path)
            names = [v.filename for v in self.archive.infolist()
                     if not v.is_dir() and Path(v.filename).suffix.lower() in IMAGE_EXTENSIONS
                     and not v.filename.startswith("__MACOSX/")
                     and not any(p.startswith(".") for p in Path(v.filename).parts)]
            if len(names) != len(set(names)):
                self.close()
                raise ValueError("ZIP 내부에 동일한 이미지 이름이 중복되어 있습니다.")
        else:
            raise ValueError(f"이미지 폴더 또는 이미지 ZIP을 선택하세요: {self.path}\n"
                             "CTB/CBDDLP 같은 전용 슬라이스 파일은 직접 읽지 않습니다.")
        names = sorted(names, key=natural_key)
        if not names:
            self.close()
            raise ValueError("지원하는 슬라이싱 이미지가 없습니다. PNG/BMP/TIF/JPG 폴더 또는 ZIP이 필요합니다.")
        try:
            if numbering == "auto":
                numbered = []
                for name in names:
                    groups = re.findall(r"\d+", Path(name).stem)
                    if not groups:
                        raise ValueError(f"층 번호가 없는 이미지: {Path(name).name}. 0 또는 1부터 연속 번호인 전체 슬라이스만 넣으세요.")
                    numbered.append((int(groups[-1]), name))
                numbered.sort(key=lambda item: item[0])
                numbers = [item[0] for item in numbered]
                if len(set(numbers)) != len(numbers):
                    raise ValueError("중복 층 번호가 있습니다. 미리보기나 다른 작업의 이미지를 제외하세요.")
                if numbers[0] not in (0, 1):
                    raise ValueError("첫 층부터 마지막 층까지 전체 이미지를 넣으세요. 파일 번호는 0 또는 1부터 시작해야 합니다.")
                if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
                    raise ValueError("중간 층 번호가 빠져 있습니다. 전체 슬라이싱 이미지를 다시 선택하세요.")
                self.offset = 1 if numbers[0] == 0 else 0
                self.entries = {number + self.offset: name for number, name in numbered}
                return
            for index, name in enumerate(names, 1):
                if numbering == "filename":
                    groups = re.findall(r"\d+", Path(name).stem)
                    if not groups:
                        raise ValueError(f"층 번호를 찾을 수 없는 이미지: {name}. 정렬순서 모드를 사용하거나 이름을 확인하세요.")
                    layer = int(groups[-1]) + self.offset
                elif numbering == "order":
                    layer = index + self.offset
                else:
                    raise ValueError("numbering은 filename 또는 order여야 합니다.")
                if layer < 1:
                    raise ValueError(f"{name}의 실제 층 번호가 {layer}입니다. 0.png가 1층이면 번호 보정을 +1로 설정하세요.")
                if layer in self.entries:
                    raise ValueError(f"{layer}층에 두 이미지가 대응합니다: {self.entries[layer]}, {name}. "
                                     "슬라이스 이미지만 담긴 폴더를 선택하고 번호 규칙을 확인하세요.")
                self.entries[layer] = name
        except Exception:
            self.close()
            raise

    def close(self):
        self._decoded_cache.clear()
        self._decoded_cache_bytes = 0
        if self.archive is not None:
            self.archive.close()
            self.archive = None

    def description(self):
        layers = sorted(self.entries)
        return {"source": str(self.path), "input_kind": self.kind, "numbering": self.numbering, "offset": self.offset,
                "image_count": len(layers), "first_layer": layers[0], "last_layer": layers[-1],
                "mapping_preview": [{"layer": n, "filename": self.entries[n]} for n in layers[:8]]}

    def load(self, layer, polarity="white"):
        name = self.entries[layer]
        encoded = None
        if self.archive is not None:
            info = self.archive.getinfo(name)
            if info.file_size > 512 * 1024 * 1024:
                raise ValueError(f"압축을 푼 이미지가 512MB를 초과합니다: {name}")
            encoded = self.archive.read(name)
            stream = io.BytesIO(encoded)
        else:
            stream = self.path / name
            # Avoid an extra large-file allocation for uncompressed inputs.
            if stream.stat().st_size <= 4 * 1024 * 1024:
                encoded = stream.read_bytes()
                stream = io.BytesIO(encoded)
        cache_key = (encoded, polarity) if encoded is not None and len(encoded) <= 4 * 1024 * 1024 else None
        if cache_key is not None and cache_key in self._decoded_cache:
            self._decoded_cache.move_to_end(cache_key)
            return self._decoded_cache[cache_key].copy()
        with Image.open(stream) as im:
            if getattr(im, "n_frames", 1) > 1:
                raise ValueError(f"다중 페이지 이미지는 한 층씩 내보내야 합니다: {name}")
            # Do not rotate by EXIF or crop/rescale: fixed bed coordinates matter.
            if im.mode in {"I", "I;16", "I;16L", "I;16B", "I;16N"}:
                raw = np.asarray(im).astype(np.float64)
                if raw.min() < 0 or raw.max() > 65535:
                    raise ValueError(f"지원하지 않는 정수 이미지 범위: {name}")
                # Older Pillow exposes 16-bit PNG as mode I. Never infer bit
                # depth from per-layer maximum brightness (255 vs 256).
                if im.mode == "I" and im.format != "PNG":
                    raise ValueError(f"32비트 정수 이미지는 8/16비트로 변환하세요: {name}")
                if "transparency" in im.info:
                    raw[raw == im.info["transparency"]] = 0 if polarity == "white" else 65535
                image = np.clip(np.rint(raw / 257.0), 0, 255).astype(np.uint8)
            elif im.mode == "F":
                raise ValueError(f"부동소수점 이미지는 8비트 슬라이스로 변환하세요: {name}")
            else:
                # RGBA/LA exports often have an entirely opaque alpha plane.
                # In that case conversion to L already gives the same pixels.
                opaque_alpha = (im.mode in {"RGBA", "LA"} and
                                im.getchannel("A").getextrema() == (255, 255))
                if ("A" in im.getbands() or "transparency" in im.info) and not opaque_alpha:
                    background = Image.new("RGBA", im.size, (0, 0, 0, 255) if polarity == "white" else (255, 255, 255, 255))
                    background.alpha_composite(im.convert("RGBA"))
                    im = background
                # convert("L") copies even an existing L image in Pillow.
                image = np.array(im if im.mode == "L" else im.convert("L"), dtype=np.uint8)
        if polarity == "black":
            image = 255 - image
        elif polarity != "white":
            raise ValueError("polarity는 white 또는 black이어야 합니다.")
        image = np.ascontiguousarray(image)
        if cache_key is not None:
            cost = len(encoded) + image.nbytes
            if cost <= 32 * 1024 * 1024:
                while self._decoded_cache and (len(self._decoded_cache) >= 2 or
                        self._decoded_cache_bytes + cost > 32 * 1024 * 1024):
                    (old_encoded, _), old_image = self._decoded_cache.popitem(last=False)
                    self._decoded_cache_bytes -= len(old_encoded) + old_image.nbytes
                self._decoded_cache[cache_key] = image.copy()
                self._decoded_cache_bytes += cost
        return image


def _safe_json(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_json(v) for v in value]
    if isinstance(value, np.generic):
        return _safe_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _motion(detection, pitch_x, pitch_y):
    dx = detection.get("dx_pixels", detection.get("dx_px"))
    dy = detection.get("dy_pixels", detection.get("dy_px"))
    if dx is None or dy is None or not math.isfinite(float(dx)) or not math.isfinite(float(dy)):
        return None
    return {"dx_pixels": float(dx), "dy_pixels": float(dy), "dx_mm": float(dx) * pitch_x,
            "dy_mm": float(dy) * pitch_y,
            "distance_mm": math.hypot(float(dx) * pitch_x, float(dy) * pitch_y)}


def _overlay(old, new, core, destination, threshold, *, _prepared=None):
    if old.shape != new.shape:
        return None
    # All output colors fit in five palette entries. Keeping indexed PNGs
    # avoids a three-channel full-bed canvas; decoded RGB pixels stay identical.
    if _prepared is None:
        indices = np.asarray(old > threshold, dtype=np.uint8)
        indices |= np.asarray(new > threshold, dtype=np.uint8) << 1
    else:
        indices = _prepared[0].mask.copy()
        indices |= _prepared[1].mask << 1
    image = Image.fromarray(indices, mode="P")
    image.putpalette([0, 0, 0, 255, 70, 70, 0, 220, 235, 245, 245, 245,
                      255, 210, 0] + [0] * 753)
    del indices
    # Retain every slice pixel so wheel zoom can reveal one-pixel boundaries.
    # The viewer fits the full-resolution image to the available screen space.
    drawing = ImageDraw.Draw(image)
    for detection in core.get("detections", [])[:30]:
        if detection.get("status") not in {"shifted", "unchanged"}:
            continue
        box = detection.get("bbox")
        movement = _motion(detection, 1, 1)
        if box is None or movement is None:
            continue
        x, y, w, h = box
        start = (x + w / 2, y + h / 2)
        end = (start[0] + movement["dx_pixels"], start[1] + movement["dy_pixels"])
        if math.hypot(end[0] - start[0], end[1] - start[1]) > 0:
            drawing.line([start, end], fill=4, width=2)
            drawing.ellipse((end[0] - 3, end[1] - 3, end[0] + 3, end[1] + 3), fill=4)
    # RLE compression is lossless and well suited to long blank slice regions.
    image.save(destination, compress_level=1, compress_type=3, bits=4)
    return "overlays/" + destination.name


def _analyse(old, new, options, output_file, *, _prepared=None, _cache=None):
    # Ordered binary-mask pairs have identical geometry and overlay pixels.
    # Prepared identities are reused only after full pixel equality. Weakrefs
    # keep this cache from retaining full-size label maps, and deep copies keep
    # layer-specific trend analysis independent. Settings are fixed per run.
    cache_key = None
    if _cache is not None and _prepared is not None:
        cache_key = (id(_prepared[0].mask), id(_prepared[1].mask))
        for key, entry in list(_cache.items()):
            if any(ref() is None for ref in entry["masks"]):
                del _cache[key]
        entry = _cache.get(cache_key)
        if entry is not None and all(ref() is prepared.mask for ref, prepared in
                                     zip(entry["masks"], _prepared)):
            output_file.write_bytes(entry["png"])
            cached = copy.deepcopy(entry["analysis"])
            cached["overlay"] = "overlays/" + output_file.name
            # Insertion order is the LRU order (plain dicts work as well).
            _cache[cache_key] = _cache.pop(cache_key)
            return cached
    px = 25.4 / options["dpi_x"]
    py = 25.4 / options["dpi_y"]
    # Apply physical bounds during matching, independently for each axis.
    search_pixels = int(math.floor(options["search_mm"] / px + 1e-12))
    search_y_pixels = int(math.floor(options["search_mm"] / py + 1e-12))
    core = _compare_slices(old, new, max_shift_pixels=search_pixels,
                          max_shift_y_pixels=search_y_pixels,
                          threshold=options["threshold"], min_component_area=options["min_area"],
                          max_components=options["max_components"], _prepared=_prepared)
    core = _safe_json(core)
    motions = []
    physical_search_excluded = 0
    for detection in core.get("detections", []):
        movement = _motion(detection, px, py)
        if movement is not None:
            detection.update(movement)
            if (abs(movement["dx_mm"]) > options["search_mm"] + 1e-12 or
                    abs(movement["dy_mm"]) > options["search_mm"] + 1e-12):
                detection.update(status="ambiguous", reason="physical_search_limit_exceeded", confidence=0.0)
                physical_search_excluded += 1
            if detection.get("status") in {"shifted", "unchanged"}:
                motions.append(movement)
    reliable = core.get("global_translation")
    global_motion = _motion(reliable, px, py) if reliable and reliable.get("reliable") else None
    diagnostic = core.get("diagnostics", {})
    if physical_search_excluded:
        global_motion = None
        if reliable is not None:
            reliable["reliable"] = False
        diagnostic["physical_search_excluded"] = physical_search_excluded
        diagnostic["ambiguous_components"] = sum(v.get("status") == "ambiguous" for v in core["detections"])
        diagnostic["reliable_components"] = len(motions)
        reference_area = diagnostic.get("reference_foreground_px", 0)
        verified_area = sum(v.get("area_px", 0) for v in core["detections"] if v.get("status") in {"shifted", "unchanged"})
        diagnostic["verified_area_fraction"] = verified_area / reference_area if reference_area else 0.0
        core["status"] = "shifted" if any(v.get("status") == "shifted" for v in core["detections"]) else "inconclusive"
        core["reason"] = "physical_search_limit_excluded_some_candidates"
        core["motion_pattern"] = "partially_verified" if motions else "unresolved"
    unresolved = (physical_search_excluded > 0 or diagnostic.get("ambiguous_components", 0) > 0 or
                  diagnostic.get("unmatched_components", 0) > 0 or
                  diagnostic.get("component_limit_reached", False) or
                  diagnostic.get("ignored_components", 0) > 0)
    maximum = max((v["distance_mm"] for v in motions), default=None)
    if any(v["distance_mm"] > options["tolerance_mm"] + 1e-12 for v in motions):
        status = "MISALIGNED"
        note = "기존 형상 중 허용 이동량을 넘긴 형상이 있습니다."
    elif core.get("status") == "inconclusive" or unresolved or not motions:
        status = "REVIEW"
        note = "비교할 형상이 부족하거나, 여러 위치가 비슷하거나, 모양 변화가 커서 판정을 보류합니다."
    else:
        status = "WITHIN_TOLERANCE"
        note = "검사 가능한 기존 형상의 이동이 설정한 허용 범위 이내입니다."
    reasons = sorted({REASON_LABELS.get(v.get("reason"), v.get("reason", ""))
                      for v in core.get("detections", [])
                      if v.get("status") in {"ambiguous", "unmatched"}})
    if core.get("reason") in REASON_LABELS:
        reasons.append(REASON_LABELS[core["reason"]])
    if reasons:
        note += " " + "; ".join(reasons) + "."
    if diagnostic.get("ignored_components", 0):
        note += f' 검사에서 제외한 연결 형상 {diagnostic["ignored_components"]}개. 최소 형상 픽셀을 낮추거나 최대 형상 수를 늘려 다시 검사하세요.'
    if unresolved and status == "MISALIGNED":
        note += " 이동을 확인한 형상 외에 판단을 보류한 부분도 있습니다."
    preview = _overlay(old, new, core, output_file, options["threshold"], _prepared=_prepared)
    analysis = {"status": status, "label": STATUS_LABELS[status], "note": note,
            "maximum_component_movement_mm": maximum,
            "global_movement": global_motion, "accepted_component_count": len(motions),
            "partial_verification": unresolved, "physical_search_excluded": physical_search_excluded,
            "overlay": preview, "registration": core}
    if cache_key is not None and preview:
        # Bound retained compressed data to four entries / 16 MiB in total.
        # An optional cache read failure must not invalidate a completed PNG.
        try:
            size = output_file.stat().st_size
            if size <= 16 * 1024 * 1024:
                _cache.pop(cache_key, None)
                while _cache and (len(_cache) >= 4 or
                        sum(len(entry["png"]) for entry in _cache.values()) + size > 16 * 1024 * 1024):
                    del _cache[next(iter(_cache))]
                _cache[cache_key] = {"masks": tuple(weakref.ref(p.mask) for p in _prepared),
                                     "png": output_file.read_bytes(),
                                     "analysis": copy.deepcopy(analysis)}
        except OSError:
            _cache.clear()
    return analysis


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _write_overlay_viewer(row, directory, *, include_inline=True):
    """Write a separate numbered overlay viewer and return its report preview.

    Boxes refer to components of the preceding layer.  PNGs may have been
    downsampled by _overlay, so transform the X and Y axes independently.
    """
    from urllib.parse import quote

    overlay = row.get("overlay")
    if not overlay:
        return None, ""
    overlay_path = Path(directory) / overlay
    if not overlay_path.is_file():
        return None, ""
    with Image.open(overlay_path) as image:
        width, height = image.size
    registration = row.get("registration") or {}
    image_shape = (registration.get("diagnostics") or {}).get("image_shape")
    try:
        original_h, original_w = map(float, image_shape)
        if not (math.isfinite(original_w) and math.isfinite(original_h)
                and original_w > 0 and original_h > 0):
            raise ValueError("invalid dimensions")
    except (TypeError, ValueError, OverflowError):
        # Older saved results may not contain the original image dimensions.
        # In that case a numbered map would risk pointing at the wrong shape.
        original_w = original_h = None
    shapes = []
    used_ids = set()
    for detection in registration.get("detections", []):
        if original_w is None:
            break
        try:
            component_id = int(detection["component_id"])
            x, y, box_w, box_h = map(float, detection["bbox"])
            if (component_id <= 0 or component_id in used_ids
                    or not all(math.isfinite(v) for v in (x, y, box_w, box_h))
                    or box_w <= 0 or box_h <= 0):
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        x0 = max(0.0, min(float(width), x * width / original_w))
        y0 = max(0.0, min(float(height), y * height / original_h))
        x1 = max(0.0, min(float(width), (x + box_w) * width / original_w))
        y1 = max(0.0, min(float(height), (y + box_h) * height / original_h))
        if x1 <= x0 or y1 <= y0:
            continue
        used_ids.add(component_id)
        shapes.append({"id": component_id, "x": x0, "y": y0,
                       "w": x1 - x0, "h": y1 - y0})

    # Keep tags apart where space allows, while a leader line preserves which
    # box a displaced tag belongs to.  Focusing one shape handles dense areas.
    font_size = max(18.0, min(30.0, max(width, height) / 50.0))
    font_size = min(font_size, width / 4.0, height / 3.0)
    tag_height = font_size * 1.45
    placed_tags = []
    for shape in shapes:
        tag_width = min(float(width), font_size * (0.68 * len(str(shape["id"])) + 0.9))
        target_x, target_y = shape["x"], shape["y"] - tag_height - 3
        choices = [(target_x, target_y), (target_x, shape["y"] + 3),
                   (shape["x"] + shape["w"] + 4, shape["y"]),
                   (shape["x"] - tag_width - 4, shape["y"])]
        for level in range(1, 5):
            choices.extend([(target_x, target_y - level * (tag_height + 3)),
                            (target_x + level * (tag_width + 4), target_y),
                            (target_x - level * (tag_width + 4), target_y)])
        best = None
        for candidate_x, candidate_y in choices:
            tx = max(0.0, min(width - tag_width, candidate_x))
            ty = max(0.0, min(height - tag_height, candidate_y))
            collisions = sum(not (tx + tag_width + 2 < px or px + pw + 2 < tx
                                  or ty + tag_height + 2 < py or py + ph + 2 < ty)
                             for px, py, pw, ph in placed_tags)
            candidate = (collisions, abs(tx - target_x) + abs(ty - target_y), tx, ty)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
            if collisions == 0:
                break
        shape.update(tx=best[2], ty=best[3], tw=tag_width, th=tag_height)
        placed_tags.append((best[2], best[3], tag_width, tag_height))

    viewer_relative = Path(overlay).with_suffix(".html").as_posix()
    viewer_path = Path(directory) / viewer_relative
    viewer_url = quote(viewer_relative, safe="/")
    report_image_url = quote(Path(overlay).as_posix(), safe="/")
    viewer_image_url = quote(overlay_path.name, safe="")
    previous_layer = html.escape(str(row.get("old_layer", "?")))
    current_layer = html.escape(str(row.get("layer", "?")))
    pair_title = f"{previous_layer}층 → {current_layer}층 겹쳐 보기"

    def make_svg(image_url, prefix, report=False):
        svg = [f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'class="numbered-overlay" viewBox="0 0 {width} {height}" '
               f'role="img" aria-label="{pair_title}: 형상 번호 지도" '
               'style="display:block;width:100%;height:auto;background:#090d14">',
               f'<image href="{html.escape(image_url, quote=True)}" '
               f'width="{width}" height="{height}" image-rendering="pixelated"/>']
        for shape in shapes:
            sid = shape["id"]
            x, y, w, h = (shape[key] for key in ("x", "y", "w", "h"))
            tx, ty, tw, th = (shape[key] for key in ("tx", "ty", "tw", "th"))
            if report:
                href = html.escape(f"{viewer_url}#shape-{sid}", quote=True)
                svg.append(f'<a href="{href}" target="_blank" rel="noopener noreferrer" '
                           f'class="overlay-link" data-overlay-viewer="true" aria-label="형상 {sid} 확대 보기">')
            svg.append(f'<g id="{prefix}{sid}" data-component-id="{sid}" '
                       'class="shape-marker" style="cursor:pointer" '
                       + (f'tabindex="0" role="button" aria-label="형상 {sid} 확대 보기"'
                          if not report else "") + '>')
            svg.extend([
                f'<title>형상 {sid} — 이전 층의 연결된 단면</title>',
                f'<rect class="shape-box" x="{x:.3f}" y="{y:.3f}" '
                f'width="{w:.3f}" height="{h:.3f}" fill="none" '
                'stroke="#67aaff" stroke-width="2" vector-effect="non-scaling-stroke"/>',
                f'<line x1="{tx + tw / 2:.3f}" y1="{ty + th / 2:.3f}" '
                f'x2="{x:.3f}" y2="{y:.3f}" stroke="#67aaff" '
                'stroke-width="1" vector-effect="non-scaling-stroke"/>',
                f'<rect class="shape-tag" x="{tx:.3f}" y="{ty:.3f}" '
                f'width="{tw:.3f}" height="{th:.3f}" rx="3" '
                'fill="#165fce" stroke="#d8e9ff" stroke-width="1" '
                'vector-effect="non-scaling-stroke"/>',
                f'<text x="{tx + tw / 2:.3f}" y="{ty + th / 2:.3f}" '
                'text-anchor="middle" dominant-baseline="central" '
                f'font-family="Arial,sans-serif" font-size="{font_size:.3f}" '
                f'font-weight="700" fill="white">{sid}</text>',
                '</g>'])
            if report:
                svg.append('</a>')
        svg.append('</svg>')
        return "".join(svg)

    caption = (f"번호는 {previous_layer}층의 연결된 2D 단면을 가리킵니다. "
               "제품 번호가 아니며, 층이 달라지면 번호도 달라질 수 있습니다.")
    if not shapes:
        caption += " 이 비교에서는 표시할 형상 번호가 없습니다."
    viewer_html = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
*{box-sizing:border-box}body{margin:0;background:#0c1220;color:#e9f0fb;font-family:Arial,"Malgun Gothic",sans-serif}
header{padding:18px 22px;border-bottom:1px solid #314058;background:#141e30}
h1{font-size:21px;margin:0 0 12px}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
button{background:#e3edff;color:#11233e;border:0;border-radius:6px;padding:9px 16px;font:inherit;cursor:pointer}
button:disabled{opacity:.4;cursor:default}#zoom-level{min-width:4.5em;text-align:center;font-variant-numeric:tabular-nums}
button:focus-visible,.shape-marker:focus{outline:3px solid #facc15;outline-offset:3px}
p{margin:12px 0 0;line-height:1.65;font-size:14px;color:#b9c9df}
main{padding:14px 20px}.stage{border:1px solid #334561;border-radius:6px;overflow:hidden;background:#090d14}
.stage .numbered-overlay{width:100%;height:calc(100vh - 250px)!important;min-height:280px;touch-action:none;user-select:none;cursor:grab}
.stage .numbered-overlay.is-dragging,.stage .numbered-overlay.is-dragging *{cursor:grabbing!important}
.shape-marker.selected .shape-box{stroke:#ffd966;stroke-width:3}
.shape-marker.selected .shape-tag{fill:#795400;stroke:#fff0b0}
.shape-marker.selected text{fill:white}.legend{font-size:14px;display:flex;gap:18px;flex-wrap:wrap;margin:12px 0}
.old{color:#ff7575}.new{color:#44e5f0}.same{color:#fff}.number{color:#9dc6ff}
@media(max-width:600px){header{padding:14px}main{padding:10px}h1{font-size:18px}.stage .numbered-overlay{height:65vh!important}}
</style></head><body>
<header><h1>__TITLE__</h1><div class="toolbar"><button type="button" id="zoom-out" aria-label="축소">−</button><output id="zoom-level" aria-label="확대 배율">100%</output><button type="button" id="zoom-in" aria-label="확대">+</button><button type="button" id="reset-view">전체보기</button><strong id="view-label" aria-live="polite">전체 형상</strong></div>
<p>휠 확대·축소 · 드래그 이동 · 번호 클릭: 형상 확대<br>__CAPTION__</p></header>
<main><div class="legend"><span class="old">빨강: 이전 층</span><span class="new">청록: 다음 층</span><span class="same">흰색: 겹침</span><span class="number">파랑 번호: 형상 위치</span></div>
<div class="stage">__SVG__</div></main>
<script>
(function(){
  'use strict';
  const width=__WIDTH__, height=__HEIGHT__, shapes=__SHAPES__;
  const svg=document.querySelector('.numbered-overlay');
  const label=document.getElementById('view-label');
  const zoomLevel=document.getElementById('zoom-level');
  const zoomIn=document.getElementById('zoom-in'),zoomOut=document.getElementById('zoom-out');
  const MIN_ZOOM=1,MAX_ZOOM=32;
  let view={x:0,y:0,w:width,h:height},drag=null;
  const clamp=(value,low,high)=>Math.max(low,Math.min(high,value));
  function setView(x,y,zoom){
    zoom=clamp(zoom,MIN_ZOOM,MAX_ZOOM);
    const w=width/zoom,h=height/zoom;
    view={x:clamp(x,0,width-w),y:clamp(y,0,height-h),w,h};
    svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);
    zoomLevel.textContent=Math.round(zoom*100)+'%';
    zoomIn.disabled=zoom>=MAX_ZOOM-1e-9;
    zoomOut.disabled=zoom<=MIN_ZOOM+1e-9;
  }
  function clientPoint(event,inverse){
    // The SVG transform includes the blank margins from preserveAspectRatio.
    // Mapping screen pixels through it keeps the wheel centered at the cursor.
    return new DOMPoint(event.clientX,event.clientY).matrixTransform(inverse);
  }
  function finishDrag(){
    if(!drag)return;
    const pointerId=drag.id;
    drag=null;
    svg.classList.remove('is-dragging');
    if(svg.hasPointerCapture(pointerId))svg.releasePointerCapture(pointerId);
  }
  function zoomAt(factor,anchor){
    finishDrag();
    anchor=anchor||{x:view.x+view.w/2,y:view.y+view.h/2};
    const zoom=clamp(width/view.w*factor,MIN_ZOOM,MAX_ZOOM);
    const fractionX=clamp((anchor.x-view.x)/view.w,0,1);
    const fractionY=clamp((anchor.y-view.y)/view.h,0,1);
    // Keep the same image point under the cursor, except when an image edge
    // reaches the viewport and clamping is necessary to keep the image in view.
    setView(anchor.x-fractionX*width/zoom,anchor.y-fractionY*height/zoom,zoom);
  }
  function showHash(){
    finishDrag();
    const match=/^#shape-(\d+)$/.exec(location.hash);
    const shape=match ? shapes.find(item=>item.id===Number(match[1])) : null;
    svg.querySelectorAll('.selected').forEach(item=>item.classList.remove('selected'));
    if(!shape){setView(0,0,1);label.textContent='전체 형상';return;}
    // Include the tag as well as its box so displaced labels remain visible.
    const left=Math.min(shape.x,shape.tx),top=Math.min(shape.y,shape.ty);
    const right=Math.max(shape.x+shape.w,shape.tx+shape.tw),bottom=Math.max(shape.y+shape.h,shape.ty+shape.th);
    const focusWidth=Math.min(width,Math.max(Math.min(240,width), (right-left)*1.8));
    const focusHeight=Math.min(height,Math.max(Math.min(180,height), (bottom-top)*1.8));
    // Preserve the full image aspect ratio so the scale is stable when the
    // user continues from a numbered shape into free wheel zoom and panning.
    const zoom=clamp(Math.min(width/focusWidth,height/focusHeight),MIN_ZOOM,MAX_ZOOM);
    setView((left+right-width/zoom)/2,(top+bottom-height/zoom)/2,zoom);
    document.getElementById('shape-'+shape.id).classList.add('selected');
    label.textContent='형상 '+shape.id;
  }
  function selectShape(target){
    const marker=target.closest('[data-component-id]');
    if(!marker)return;
    location.hash='shape-'+marker.dataset.componentId;
    showHash();
  }
  svg.addEventListener('wheel',event=>{
    event.preventDefault();
    const matrix=svg.getScreenCTM();
    if(!matrix)return;
    const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?svg.clientHeight:1);
    if(!delta)return;
    zoomAt(Math.exp(-clamp(delta,-600,600)*0.0015),clientPoint(event,matrix.inverse()));
  },{passive:false});
  svg.addEventListener('pointerdown',event=>{
    if(!event.isPrimary||event.button!==0||drag)return;
    const matrix=svg.getScreenCTM();
    if(!matrix)return;
    const inverse=matrix.inverse();
    drag={id:event.pointerId,target:event.target,startX:event.clientX,startY:event.clientY,
          point:clientPoint(event,inverse),inverse,view:{...view},moved:false};
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove',event=>{
    if(!drag||event.pointerId!==drag.id)return;
    if(event.pointerType==='mouse'&&!(event.buttons&1)){finishDrag();return;}
    if(Math.hypot(event.clientX-drag.startX,event.clientY-drag.startY)>4)drag.moved=true;
    if(!drag.moved)return;
    event.preventDefault();
    svg.classList.add('is-dragging');
    // Use the transform from pointerdown; a changing transform would feed the
    // movement back into itself and cause the image to jump during a drag.
    const point=clientPoint(event,drag.inverse);
    setView(drag.view.x-(point.x-drag.point.x),drag.view.y-(point.y-drag.point.y),width/drag.view.w);
  });
  svg.addEventListener('pointerup',event=>{
    if(!drag||event.pointerId!==drag.id)return;
    const target=drag.target;
    const clicked=!drag.moved&&Math.hypot(event.clientX-drag.startX,event.clientY-drag.startY)<=4;
    finishDrag();
    if(clicked)selectShape(target);
  });
  svg.addEventListener('pointercancel',finishDrag);
  svg.addEventListener('lostpointercapture',finishDrag);
  // Pointer clicks are handled above so a drag beginning on a shape does not
  // unexpectedly refocus it. Assistive-technology clicks still work here.
  svg.addEventListener('click',event=>{if(event.detail===0)selectShape(event.target);});
  svg.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectShape(event.target);}});
  zoomIn.addEventListener('click',()=>zoomAt(1.5));
  zoomOut.addEventListener('click',()=>zoomAt(1/1.5));
  document.getElementById('reset-view').addEventListener('click',()=>{
    // Avoid file:// History API restrictions while preserving normal hash links.
    location.hash='';showHash();
  });
  window.addEventListener('hashchange',showHash);showHash();
})();
</script></body></html>'''
    viewer_html = (viewer_html.replace("__TITLE__", pair_title)
                   .replace("__CAPTION__", caption)
                   .replace("__SVG__", make_svg(viewer_image_url, "shape-"))
                   .replace("__WIDTH__", str(width)).replace("__HEIGHT__", str(height))
                   .replace("__SHAPES__", json.dumps(shapes, ensure_ascii=True, separators=(",", ":"))))
    viewer_path.write_text(viewer_html, encoding="utf-8")
    return viewer_relative, (make_svg(report_image_url, "boundary-shape-", report=True)
                             if include_inline else "")


def _write_reports(result, directory, *, cancel_event=None, progress_callback=None):
    """Keep full diagnostic data; present concise decisions and numbered figures."""
    e = html.escape
    full_scan = result.get("scan_mode") == "full"
    boundary_layer = result["settings"].get("switch_layer") if not full_scan and "boundary_status" in result else None
    rows, component_sections = [], []
    row_html_by_layer = {}
    boundary_row, boundary_map = None, ""
    classification_labels = {"suspected": "어긋남 의심", "no_signal": "신호 없음", "inconclusive": "판단 보류"}
    reason_labels = {
        "boundary_match_unresolved": "형상 대응 불명확",
        "before_after_drift_disagree": "전후 흐름이 다름",
        "insufficient_or_unstable_history": "주변 추이 부족",
        "boundary_exceeds_usual_drift": "주변 흐름과 다른 변화",
        "within_usual_drift": "주변 흐름과 비슷함",
    }

    def overlay_link(viewer, label="겹쳐 보기 ↗", component=None):
        if not viewer:
            return "—"
        href = viewer + (f"#shape-{int(component)}" if component is not None else "")
        return (f'<a class="overlay-link" href="{e(href, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{e(label)}</a>')

    def brief_note(row):
        classification = row.get("boundary_status")
        if classification == "suspected":
            return "주변 흐름과 다른 위치 변화가 보입니다."
        if classification == "no_signal":
            return "뚜렷한 경계 변화가 보이지 않습니다."
        if classification == "inconclusive":
            return "형상 대응이나 주변 추이가 부족합니다."
        core = row.get("registration", {})
        if not core:
            return "이미지를 읽거나 비교하지 못했습니다."
        if row.get("partial_verification") or core.get("status") == "inconclusive":
            return "일부 형상의 위치를 확인하지 못했습니다."
        if core.get("status") == "shifted":
            return "인접층에서 형상 위치 변화가 보입니다."
        return "형상 위치가 이어집니다."

    for index, row in enumerate(result["results"], 1):
        if cancel_event is not None and cancel_event.is_set():
            raise CheckCancelled("보고서 생성을 중지했습니다. 완료된 결과로 사용하지 마세요.")
        if progress_callback is not None and (index == 1 or index % 20 == 0 or index == len(result["results"])):
            progress_callback(f"보고서 작성 [{index}/{len(result['results'])}]")
        viewer, inline_svg = _write_overlay_viewer(row, directory,
                                                  include_inline=row["layer"] == boundary_layer)
        if viewer:
            row["overlay_viewer"] = viewer
        is_boundary = row["layer"] == boundary_layer
        if is_boundary:
            boundary_row, boundary_map = row, inline_svg
        if full_scan:
            label = classification_labels.get(row.get("boundary_status"), "판단 보류")
        elif is_boundary or result["mode"] != "adjacent":
            label = row["label"]
        else:
            core = row.get("registration", {})
            label = "확인 필요" if row.get("partial_verification") or core.get("status", "inconclusive") == "inconclusive" else "참고 비교"
        row_html = (f'<tr class="{e(row["status"])}"><td>{row["old_layer"]} → {row["layer"]}</td>'
                    f'<td>{e(label)}</td><td>{e(brief_note(row))}</td>'
                    f'<td>{overlay_link(viewer, "번호·겹쳐 보기 ↗" if full_scan else "겹쳐 보기 ↗")}</td></tr>')
        rows.append(row_html)
        row_html_by_layer[row["layer"]] = row_html
        if is_boundary:
            continue
        details = []
        tracked_signals = {signal.get("component_id"): signal
                           for signal in row.get("boundary_track_signals", [])}
        for detection in row.get("registration", {}).get("detections", []):
            reason = REASON_LABELS.get(detection.get("reason"), "형상 대응 확인 필요")
            label = {"shifted": "이동 후보", "unchanged": "같은 위치", "ambiguous": "판단 보류", "unmatched": "대응 없음"}.get(detection.get("status"), "판단 보류")
            if full_scan:
                signal = tracked_signals.get(detection.get("component_id"), {})
                label = classification_labels.get(signal.get("status"), "판단 보류")
                reason = reason_labels.get(signal.get("reason"), "형상 대응 확인 필요")
                if signal.get("caveat"):
                    reason += " · 한쪽 추이만 사용"
            number = int(detection["component_id"])
            details.append(f'<tr><td>{overlay_link(viewer, f"#{number}", number)}</td>'
                           f'<td>{e(label)}</td><td>{e(reason)}</td></tr>')
        if details:
            component_sections.append(f'<details><summary>{row["old_layer"]} → {row["layer"]}층 형상 목록</summary>'
                                      f'<p>{overlay_link(viewer)} · 번호는 {row["old_layer"]}층의 형상을 가리킵니다.</p>'
                                      '<table><tr><th>형상 번호</th><th>상태</th><th>설명</th></tr>'
                                      + ''.join(details) + '</table></details>')

    boundary_section = ""
    if boundary_layer is not None:
        track_rows = []
        viewer = boundary_row.get("overlay_viewer") if boundary_row else None
        for signal in result.get("boundary_track_signals", []):
            status_text = {"suspected": "어긋남 의심", "no_signal": "뚜렷한 신호 없음", "inconclusive": "판단 보류"}.get(signal["status"], "판단 보류")
            reason = reason_labels.get(signal["reason"], "형상 대응 확인 필요")
            if signal.get("caveat"):
                reason += " · 한쪽 추이만 사용"
            number = int(signal["component_id"])
            track_rows.append(f'<tr><td>{overlay_link(viewer, f"#{number}", number)}</td>'
                              f'<td>{e(status_text)}</td><td>{e(reason)}</td></tr>')
        figure = (f'<figure class="numbered-map">{boundary_map}'
                  f'<figcaption>파란 번호는 {boundary_layer-1}층의 형상입니다. 표나 그림의 번호를 누르면 해당 형상을 새 창에서 확대합니다.</figcaption></figure>'
                  if boundary_map else '<p>이 경계의 형상 번호 그림을 만들 수 없습니다.</p>')
        boundary_section = (f'<h2>치환 경계 {boundary_layer-1} → {boundary_layer}층</h2>'
            f'<p>{overlay_link(viewer, "번호가 표시된 겹쳐 보기 ↗")}</p><div class="boundary-layout">' + figure +
            '<table class="boundary-table"><tr><th>형상 번호</th><th>판정</th><th>설명</th></tr>'
            + ''.join(track_rows) + '</table></div>')

    mappings = []
    for item in result["sources"]:
        mapping = " / ".join(f'{v["layer"]}층: {v["filename"]}' for v in item["mapping_preview"])
        mappings.append(f'<p><b>{e(item["source"])}</b><br>{e(mapping)}</p>')
    table_header = '<tr><th>층</th><th>판정</th><th>설명</th><th>그림</th></tr>'
    comparison_table = '<table>' + table_header + ''.join(rows) + '</table>'
    scan_section = ""
    mode_text = ""
    if full_scan:
        scan_counts = result.get("full_scan_counts", {})
        suspected_count = int(scan_counts.get("suspected", 0))
        no_signal_count = int(scan_counts.get("no_signal", 0))
        inconclusive_count = int(scan_counts.get("inconclusive", 0))
        summary = {"suspected": "전체 스캔에서 어긋남이 의심되는 구간을 찾았습니다.",
                   "no_signal": "전체 스캔에서 뚜렷한 이동 신호를 찾지 못했습니다.",
                   "inconclusive": "일부 구간은 위치 변화를 판단하기 어렵습니다."}.get(
                       result.get("full_scan_status"), "전체 스캔 결과를 확인하세요.")
        summary += (f" 어긋남 의심 {suspected_count}개 · 신호 없음 {no_signal_count}개"
                    f" · 판단 보류 {inconclusive_count}개")
        mode_text = f'<br>{e(result.get("mode_label", "전체 인접층 참고 검사"))} · 인접층 {len(rows)}개 비교'
        suspicious_layers = sorted(set(result.get("suspicious_layers", [])))
        suspicious_rows = ''.join(row_html_by_layer[layer] for layer in suspicious_layers
                                  if layer in row_html_by_layer)
        if not suspicious_rows:
            suspicious_rows = '<tr><td colspan="4">어긋남 의심으로 분류된 경계가 없습니다.</td></tr>'
        scan_section = ('<section id="suspected-boundaries"><h2>의심 구간</h2>'
                        '<p class="muted">표의 층은 위치 변화가 관측된 앞층과 뒤층입니다. 그림을 열어 형상 번호를 확인하세요.</p>'
                        '<table>' + table_header + suspicious_rows + '</table></section>')
        comparison_section = (f'<details id="all-comparisons"><summary>전체 인접층 비교 {len(rows)}개 펼치기</summary>'
                              + comparison_table + '</details>')
        component_heading = "각 비교의 형상 번호"
    elif boundary_layer is not None:
        classification = result["boundary_status"]
        summary = {"suspected": "치환 경계에서 어긋남이 의심됩니다.",
                   "no_signal": "치환 경계에서 뚜렷한 이동 신호를 찾지 못했습니다.",
                   "inconclusive": "치환 경계의 위치를 판단하기 어렵습니다."}.get(classification, "판단 보류")
        if not result.get("boundary_all_tracks_verified", False):
            summary += " 일부 형상은 확인이 필요합니다."
    else:
        summary = {"MISALIGNED": "기존 형상의 위치 변화가 확인됐습니다.",
                   "WITHIN_TOLERANCE": "확인한 형상은 설정 기준 이내입니다."}.get(result["status"], "일부 형상은 확인이 필요합니다.")
    if not full_scan:
        comparison_section = '<h2>경계 주변 인접층 비교</h2>' + comparison_table
        component_heading = "다른 비교의 형상 번호"
    image_count = result["sources"][0]["image_count"] if result["sources"] else 0
    text = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>슬라이싱 위치 검사</title>
<style>body{{font:15px/1.65 "Malgun Gothic",sans-serif;color:#203348;max-width:1100px;margin:32px auto;padding:0 20px}}h1{{font-size:26px}}h2{{font-size:21px;margin-top:30px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d3dce5;padding:10px;text-align:left}}th{{background:#173252;color:white}}.REVIEW_SUSPECTED,.MISALIGNED{{background:#ffede6}}.box{{background:#f0f5fa;padding:16px;border-radius:8px}}a{{color:#176696}}.numbered-map{{margin:16px 0}}.numbered-map svg{{display:block;width:100%;height:auto;background:#000;border-radius:6px}}figcaption,.muted{{color:#526578;font-size:13px}}details{{margin:12px 0}}summary{{cursor:pointer;font-weight:bold}}</style>
<style>.boundary-layout{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:18px;align-items:start}}.boundary-layout .numbered-map{{margin:0}}@media(max-width:900px){{.boundary-layout{{grid-template-columns:1fr}}}}</style>
<h1>{e(result["label"])}</h1>
<div class="box"><b>{e(summary)}</b>{mode_text}<br>전체 {image_count}층 · DPI {result["settings"]["dpi_x"]:g} · 검사기 v{e(result["software_version"])}</div>
{scan_section}
{boundary_section}
<p class="muted">빨강: 앞층에만 있음 · 청록: 뒤층에만 있음 · 흰색: 겹침 · 노랑: 이동 방향 · 파란 번호: 검사 형상</p>
{comparison_section}
<details><summary>{component_heading}</summary>{''.join(component_sections)}</details>
<p class="muted">번호는 각 비교의 앞층에서 연결된 형상을 가리킵니다. 같은 생산품도 여러 번호로 나뉠 수 있으며, 비교 층이 달라지면 번호도 바뀔 수 있습니다. 새로 나타난 형상은 번호가 없을 수 있습니다.</p>
<details><summary>입력 파일 정보</summary>{''.join(mappings)}</details>
<p class="muted">인접층 비교는 참고 판정입니다. 정상적인 단면 변화와 배치 이동을 확정적으로 구별하지 못할 수 있습니다.</p>
<script>
document.addEventListener('click', function(event) {{
  const link = event.target.closest('a.overlay-link');
  if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const href = link.getAttribute('href');
  if (!href) return;
  const popup = window.open(new URL(href, document.baseURI).href, '_blank', 'popup=yes,width=1200,height=860,resizable=yes,scrollbars=yes');
  if (popup) {{ popup.opener = null; event.preventDefault(); }}
}});
</script></html>'''
    (directory / "report.html").write_text(text, encoding="utf-8")
    (directory / "result.json").write_text(json.dumps(_safe_json(result), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    fields = ["layer", "old_layer", "old_filename", "new_filename", "status", "label",
              "dx_mm", "dy_mm", "distance_mm", "maximum_component_movement_mm", "boundary_status", "boundary_signal_mm", "accepted_component_count", "note"]
    with (directory / "result.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        for row in result["results"]:
            movement = row.get("global_movement") or {}
            record = {v: row.get(v) for v in fields}
            record.update({v: movement.get(v) for v in ("dx_mm", "dy_mm", "distance_mm")})
            writer.writerow({k: _csv_value(v) for k, v in record.items()})



def run_check(options, progress_callback: Callable[[str], None] | None = None, cancel_event: Event | None = None):
    defaults = dict(mode="reference", old="", new="", start_layer=61, count=10, switch_layer=61, context=4,
                    dpi_x=400., dpi_y=400., tolerance_mm=.2, search_mm=3., threshold=127,
                    pixel_size_x_um=None, pixel_size_y_um=None,
                    polarity="white", numbering="filename", old_offset=0, new_offset=0,
                    min_area=8, max_components=160, output=str(Path(__file__).parent / "reports"))
    options = {**defaults, **dict(options)}
    log = progress_callback or (lambda message: None)
    sizes = [options["pixel_size_x_um"], options["pixel_size_y_um"]]
    if any(value is not None for value in sizes):
        if any(value is None for value in sizes):
            raise ValueError("픽셀 크기는 X, Y를 모두 입력하세요. 단위는 um/픽셀입니다.")
        for axis in ("x", "y"):
            size = float(options[f"pixel_size_{axis}_um"])
            if not math.isfinite(size) or size <= 0:
                raise ValueError("픽셀 크기는 0보다 큰 유한한 숫자여야 합니다.")
            options[f"pixel_size_{axis}_um"] = size
            options[f"dpi_{axis}"] = 25400.0 / size
        options["calibration_input"] = "pixel_size_um"
    else:
        options["calibration_input"] = "dpi"
    for key in ("dpi_x", "dpi_y", "tolerance_mm", "search_mm"):
        options[key] = float(options[key])
        if not math.isfinite(options[key]) or options[key] <= 0:
            raise ValueError(f"{key}는 0보다 큰 유한한 숫자여야 합니다.")
    if options["search_mm"] <= options["tolerance_mm"]:
        raise ValueError("검색 범위는 허용 이동량보다 크게 설정하세요.")
    for key in ("start_layer", "count", "switch_layer", "context", "threshold", "old_offset", "new_offset", "min_area", "max_components"):
        value = float(options[key])
        if not math.isfinite(value) or value != int(value):
            raise ValueError(f"{key}는 정수여야 합니다.")
        options[key] = int(value)
    if not 0 <= options["threshold"] <= 254 or not 1 <= options["count"] <= 1000 or not 1 <= options["context"] <= 50:
        raise ValueError("이진화 기준은 0~254, 검사 층수는 1~1000, 주변 층수는 1~50이어야 합니다.")
    if options["start_layer"] < 1 or options["switch_layer"] < 2 or options["min_area"] < 1 or options["max_components"] < 1:
        raise ValueError("시작층≥1, 교체층≥2, 최소 형상 픽셀≥1, 최대 형상 수≥1이 필요합니다.")
    if options["mode"] not in {"reference", "adjacent"}:
        raise ValueError("mode는 reference 또는 adjacent여야 합니다.")
    if not str(options["old"]).strip() or (options["mode"] == "reference" and not str(options["new"]).strip()):
        raise ValueError("같은 층 검사는 기존·신규 폴더를 모두, 인접층 검사는 혼합 슬라이스 폴더를 선택하세요.")
    px, py = 25.4 / options["dpi_x"], 25.4 / options["dpi_y"]
    if math.ceil(options["search_mm"] / min(px, py)) > 512:
        raise ValueError("검색 범위가 ±512픽셀을 초과합니다. DPI 또는 검색 범위(mm)를 확인하세요.")
    sources = []
    result = None
    try:
        old_source = LayerSource(options["old"], options["numbering"], options["old_offset"])
        sources.append(old_source)
        new_source = LayerSource(options["new"], options["numbering"], options["new_offset"]) if options["mode"] == "reference" else old_source
        if new_source is not old_source:
            sources.append(new_source)
        for source in sources:
            desc = source.description()
            log(f'{desc["source"]}: {desc["image_count"]}개, {desc["first_layer"]}~{desc["last_layer"]}층')
            log("층 번호 확인: " + " / ".join(f'{v["layer"]}={v["filename"]}' for v in desc["mapping_preview"]))
        if options.get("simple_boundary") and not 2 <= options["switch_layer"] <= len(old_source.entries):
            raise ValueError(f"치환 시작 층은 2~{len(old_source.entries)} 사이여야 합니다.")
        if options.get("full_scan") and (options["mode"] != "adjacent" or len(old_source.entries) < 2):
            raise ValueError("전체 스캔에는 연속된 슬라이싱 이미지가 2개 이상 필요합니다.")
        output_base = Path(options["output"]).expanduser().resolve()
        for source in sources:
            if source.kind == "directory" and (output_base == source.path or source.path in output_base.parents):
                raise ValueError("보고서는 입력 슬라이스 폴더 밖에 저장하세요. 다음 검사에 보고서 그림이 섞이는 것을 방지합니다.")
        output_base.mkdir(parents=True, exist_ok=True)
        directory = output_base / datetime.now().strftime("check_%Y%m%d_%H%M%S_%f")
        directory.mkdir(exist_ok=False)
        (directory / "overlays").mkdir()
        incomplete = directory / "INCOMPLETE.txt"
        incomplete.write_text("검사가 아직 완료되지 않았습니다. 이 폴더를 최종 판정으로 사용하지 마세요.", encoding="utf-8")
        pairs = [(layer, layer) for layer in range(options["start_layer"], options["start_layer"] + options["count"])] if options["mode"] == "reference" else [
            (layer-1, layer) for layer in range(max(2, options["switch_layer"]-options["context"]), options["switch_layer"]+options["context"]+1)]
        if options.get("simple_boundary"):
            pairs = [(a, b) for a, b in pairs if a in old_source.entries and b in old_source.entries]
        if options.get("full_scan"):
            layers = sorted(old_source.entries)
            if layers != list(range(1, len(layers) + 1)):
                raise ValueError("전체 스캔은 첫 층부터 연속된 전체 이미지가 필요합니다.")
            pairs = [(layer - 1, layer) for layer in range(2, len(layers) + 1)]
        rows = []
        adjacent_cache = {}
        analysis_cache = {}
        mask_cache = OrderedDict()

        def read_adjacent(layer):
            if layer not in adjacent_cache:
                try:
                    image = old_source.load(layer, options["polarity"])
                    previous = adjacent_cache.get(layer - 1)
                    prepared = _prepare_slice(image, options["threshold"],
                                              previous[2] if previous is not None else None)
                    # A/B/A runs can reuse the other currently held mask and
                    # labels too, without retaining any extra prepared images.
                    for _, _, recent in adjacent_cache.values():
                        if (recent is not None and recent is not prepared and
                                np.array_equal(prepared.mask, recent.mask)):
                            prepared = recent
                            break
                    # Keep two canonical binary masks across AA/BB runs as
                    # well. Only the masks are retained here, never labels.
                    # Rebinding to equal pixels preserves any existing labels.
                    for recent_mask in mask_cache.values():
                        if prepared.mask is recent_mask:
                            break
                        if np.array_equal(prepared.mask, recent_mask):
                            prepared.mask = recent_mask
                            break
                    if prepared.mask.nbytes <= 16 * 1024 * 1024:
                        key = id(prepared.mask)
                        mask_cache.pop(key, None)
                        while mask_cache and (len(mask_cache) >= 2 or
                                sum(mask.nbytes for mask in mask_cache.values()) + prepared.mask.nbytes > 16 * 1024 * 1024):
                            mask_cache.popitem(last=False)
                        mask_cache[key] = prepared.mask
                    adjacent_cache[layer] = (image, None, prepared)
                except (OSError, ValueError, KeyError, zipfile.BadZipFile, cv2.error, Image.DecompressionBombError) as exc:
                    # Cache failures too so a corrupt shared layer is read only once.
                    adjacent_cache[layer] = (None, str(exc), None)
            return adjacent_cache[layer]

        for index, (old_layer, layer) in enumerate(pairs, 1):
            if cancel_event is not None and cancel_event.is_set():
                raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
            log(f"[{index}/{len(pairs)}] {old_layer}층 → {layer}층 검사")
            if cancel_event is not None and cancel_event.is_set():
                raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
            row = {"layer": layer, "old_layer": old_layer,
                   "old_filename": old_source.entries.get(old_layer, ""),
                   "new_filename": new_source.entries.get(layer, "")}
            if old_layer not in old_source.entries or layer not in new_source.entries:
                row.update(status="REVIEW", label=STATUS_LABELS["REVIEW"], accepted_component_count=0,
                           note="해당 층의 기준 또는 신규 이미지가 없습니다. 층 번호 대응을 확인하세요.")
            else:
                try:
                    if options["mode"] == "adjacent":
                        before = read_adjacent(old_layer)
                        after = read_adjacent(layer)
                        adjacent_cache = {old_layer: before, layer: after}
                        if before[1] is not None or after[1] is not None:
                            raise ValueError(before[1] or after[1])
                        a, b = before[0], after[0]
                    else:
                        a = old_source.load(old_layer, options["polarity"])
                        b = new_source.load(layer, options["polarity"])
                    row.update(_analyse(a, b, options, directory / "overlays" / f"layer_{layer:06d}.png",
                                        _prepared=(before[2], after[2]) if options["mode"] == "adjacent" else None,
                                        _cache=analysis_cache if options["mode"] == "adjacent" else None))
                except (OSError, ValueError, KeyError, zipfile.BadZipFile, cv2.error, Image.DecompressionBombError) as exc:
                    row.update(status="REVIEW", label=STATUS_LABELS["REVIEW"], accepted_component_count=0, note=str(exc))
                if options["mode"] == "adjacent":
                    row["same_layer_status_not_applicable"] = row.get("status")
                    row["status"] = "REVIEW"
                    row["label"] = STATUS_LABELS["REVIEW"]
                    row["note"] = "인접층의 단면 변화가 섞여 있으므로 참고용 이동 신호입니다. " + row["note"]
            if cancel_event is not None and cancel_event.is_set():
                raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
            rows.append(row)
        jump = None
        if options.get("full_scan"):
            status = "REVIEW"
            summary = "전체 인접층의 이동 추이를 정리합니다."
        elif options["mode"] == "adjacent":
            previous = [v["global_movement"] for v in rows if v["layer"] < options["switch_layer"] and v.get("global_movement")]
            boundary = next(v for v in rows if v["layer"] == options["switch_layer"])
            if boundary.get("global_movement"):
                observed = boundary["global_movement"]
                drift_x = float(np.median([v["dx_pixels"] for v in previous])) if len(previous) >= 2 else None
                drift_y = float(np.median([v["dy_pixels"] for v in previous])) if len(previous) >= 2 else None
                if drift_x is not None:
                    jump = _motion({"dx_pixels": observed["dx_pixels"]-drift_x, "dy_pixels": observed["dy_pixels"]-drift_y}, px, py)
                    boundary["jump_after_previous_layer_drift"] = jump
                    boundary["note"] += f' 교체 전 이동 추이를 뺀 경계 이동 신호: {jump["distance_mm"]:.3f}mm.'
                else:
                    boundary["note"] += " 이전 이동 추이를 계산할 기준이 부족합니다."
                signal = jump or observed
                if signal["distance_mm"] > options["tolerance_mm"]:
                    boundary.update(status="REVIEW_SUSPECTED", label=STATUS_LABELS["REVIEW_SUSPECTED"])
            elif (boundary.get("maximum_component_movement_mm") or 0) > options["tolerance_mm"]:
                boundary.update(status="REVIEW_SUSPECTED", label=STATUS_LABELS["REVIEW_SUSPECTED"])
                boundary["note"] += " 일부 형상에서 허용량을 넘는 이동 신호가 있습니다. 공통 이동 추이 보정은 불가능하며 정상 단면 변화일 수도 있습니다."
            status = "REVIEW"
            summary = "인접층 검사는 참고용입니다. 기존 형상의 이동과 정상 단면 변화를 확정적으로 구별할 수 없습니다. 같은 층의 기존·신규 원본으로 다시 확인하세요."
        else:
            status = "MISALIGNED" if any(v["status"] == "MISALIGNED" for v in rows) else "REVIEW" if any(v["status"] == "REVIEW" for v in rows) else "WITHIN_TOLERANCE"
            summary = f'{len(rows)}개 같은 층을 비교했습니다. 어긋남 {sum(v["status"]=="MISALIGNED" for v in rows)}층, 판단 보류 {sum(v["status"]=="REVIEW" for v in rows)}층. 신규 형상 추가 자체는 위치 어긋남으로 판정하지 않습니다.'
        result = {"schema_version": 2, "software_version": VERSION, "mode": options["mode"], "mode_label": "같은 층의 교체 전·후 비교" if options["mode"] == "reference" else "인접층 참고 검사",
                  "status": status, "label": STATUS_LABELS[status], "summary": summary,
                  "settings": options, "pixel_pitch_mm": {"x": px, "y": py},
                  "sources": [s.description() for s in sources], "results": rows,
                  "boundary_jump_signal": jump, "count": len(rows),
                  "counts": {name: sum(row["status"] == name for row in rows) for name in STATUS_LABELS},
                  "result_dir": str(directory), "report_html": str(directory / "report.html"),
                  "limitations": ["같은 층·같은 원점·같은 픽셀 크기가 기준", "연결된 2D 형상 기준, 모델 ID 아님", "정수 픽셀 이동 탐색", "회전·배율 변경 정량화 미지원", "실제 생산 이미지로 별도 검증 필요"]}
        if options.get("simple_boundary"):
            _summarize_boundary(result)
        if options.get("full_scan"):
            _summarize_full_scan(result, cancel_event=cancel_event, progress_callback=log)
        if cancel_event is not None and cancel_event.is_set():
            raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
        _write_reports(result, directory, cancel_event=cancel_event, progress_callback=log)
        if cancel_event is not None and cancel_event.is_set():
            for filename in ("report.html", "result.json", "result.csv"):
                (directory / filename).unlink(missing_ok=True)
            raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
        incomplete.unlink()
        log(result["label"] + ": " + result["summary"])
        log("보고서: " + result["report_html"])
        return result
    finally:
        for source in sources:
            source.close()




def _summarize_boundary(result):
    """Infer an advisory discontinuity from tracked adjacent-layer anchors.

    Mutates and returns a run_check(adjacent) result. No I/O or third-party
    imports. Connected-component IDs join only immediately adjacent rows:
    a row's candidate_id is the next row's component_id for the same mask.
    """
    import math
    from statistics import median

    settings = result.get("settings", {})
    switch = int(settings["switch_layer"])
    tolerance = float(settings.get("tolerance_mm", 0.2))
    pitch = result["pixel_pitch_mm"]
    px, py = float(pitch["x"]), float(pitch["y"])
    rows = result.get("results", [])
    indexed = {row.get("layer"): row for row in rows}
    boundary = indexed.get(switch)
    sources = result.get("sources", [])
    image_count = result.get("input_image_count")
    if image_count is None and sources:
        image_count = sources[0].get("image_count")
    count_text = f"전체 {image_count}개 슬라이싱 이미지" if image_count is not None else "전체 슬라이싱 이미지"
    lead = f"{count_text}에서 교체 시작 {switch}층({switch - 1}→{switch}층)을 검사했습니다. "
    advisory = "인접층 단면 변화와 실제 배치 이동을 확정적으로 구분하는 판정은 아닙니다."

    def vector(detection):
        if detection.get("status") not in {"unchanged", "shifted"}:
            return None
        x = detection.get("dx_pixels", detection.get("dx_px"))
        y = detection.get("dy_pixels", detection.get("dy_px"))
        if x is None or y is None:
            return None
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            return None
        return (x, y) if math.isfinite(x) and math.isfinite(y) else None

    def movement(x, y):
        return {"dx_pixels": x, "dy_pixels": y,
                "dx_mm": x * px, "dy_mm": y * py,
                "distance_mm": math.hypot(x * px, y * py)}

    def detections(row):
        return row.get("registration", {}).get("detections", []) if row else []

    joins = {}

    def joined(layer, join_key, current_id):
        # Build each local index once, retaining lists so duplicate candidates
        # still break tracks exactly as the original exhaustive search did.
        key = (layer, join_key)
        if key not in joins:
            matches_by_id = {}
            for detection in detections(indexed[layer]):
                if vector(detection) is not None:
                    matches_by_id.setdefault(detection.get(join_key), []).append(detection)
            joins[key] = matches_by_id
        return joins[key].get(current_id, [])

    def trace(anchor, direction):
        # A missing, ambiguous, split, or merged match breaks this track.
        # Do not leap across a missing layer to a reused component ID.
        current_id = anchor.get("component_id" if direction < 0 else "candidate_id")
        samples = []
        if current_id is None:
            return samples
        for step in range(1, 5):
            layer = switch + direction * step
            row = indexed.get(layer)
            if row is None or row.get("old_layer") != layer - 1:
                break
            join_key = "candidate_id" if direction < 0 else "component_id"
            matches = joined(layer, join_key, current_id)
            if len(matches) != 1:
                break
            matched = matches[0]
            x, y = vector(matched)
            samples.append({"layer": layer, "dx_pixels": x, "dy_pixels": y})
            current_id = matched.get("component_id" if direction < 0 else "candidate_id")
            if current_id is None:
                break
        return samples

    def baseline(samples):
        info = {"samples": samples, "sample_count": len(samples), "stable": False,
                "dx_pixels": None, "dy_pixels": None, "max_axis_deviation_pixels": None}
        if not samples:
            return info
        x = float(median(s["dx_pixels"] for s in samples))
        y = float(median(s["dy_pixels"] for s in samples))
        deviation = max(max(abs(s["dx_pixels"] - x), abs(s["dy_pixels"] - y)) for s in samples)
        info.update(dx_pixels=x, dy_pixels=y, max_axis_deviation_pixels=deviation,
                    stable=len(samples) >= 2 and deviation <= 1.0 + 1e-12)
        return info

    signals = []
    result["boundary_jump_signal"] = None
    if boundary is not None:
        # Remove the old global-only inference before evaluating individual tracks.
        boundary.pop("jump_after_previous_layer_drift", None)
        boundary.update(status="REVIEW", label="판단 보류 / 확인 필요")
        for anchor in detections(boundary):
            observed = vector(anchor)
            signal = {"component_id": anchor.get("component_id"),
                      "candidate_id": anchor.get("candidate_id"),
                      "bbox": anchor.get("bbox"),
                      "status": "inconclusive", "observed_movement": None,
                      "baseline_source": None, "baseline_movement": None,
                      "residual_movement": None, "reason": "boundary_match_unresolved",
                      "caveat": None}
            if observed is None or boundary.get("old_layer") != switch - 1:
                signals.append(signal)
                continue
            signal["observed_movement"] = movement(*observed)
            before = baseline(trace(anchor, -1))
            after = baseline(trace(anchor, 1))
            signal.update(before=before, after=after)
            if before["stable"] and after["stable"]:
                disagreement = max(abs(before["dx_pixels"] - after["dx_pixels"]),
                                   abs(before["dy_pixels"] - after["dy_pixels"]))
                signal["baseline_disagreement_pixels"] = disagreement
                if disagreement > 2.0 + 1e-12:
                    signal["reason"] = "before_after_drift_disagree"
                    signals.append(signal)
                    continue
                # Give each side equal weight even when one has fewer images.
                drift_x = (before["dx_pixels"] + after["dx_pixels"]) / 2.0
                drift_y = (before["dy_pixels"] + after["dy_pixels"]) / 2.0
                signal["baseline_source"] = "both"
            elif before["stable"] or after["stable"]:
                chosen = before if before["stable"] else after
                drift_x, drift_y = chosen["dx_pixels"], chosen["dy_pixels"]
                signal["baseline_source"] = "before" if before["stable"] else "after"
                signal["caveat"] = "교체 경계의 한쪽에서만 안정적인 이동 추이를 확보했습니다."
            else:
                signal["reason"] = "insufficient_or_unstable_history"
                signals.append(signal)
                continue
            residual = movement(observed[0] - drift_x, observed[1] - drift_y)
            signal.update(baseline_movement=movement(drift_x, drift_y),
                          residual_movement=residual,
                          status="suspected" if residual["distance_mm"] > tolerance + 1e-12 else "no_signal",
                          reason="boundary_exceeds_usual_drift" if residual["distance_mm"] > tolerance + 1e-12 else "within_usual_drift")
            signals.append(signal)

    usable = [s for s in signals if s["residual_movement"] is not None]
    suspected = [s for s in usable if s["status"] == "suspected"]
    representative = max(usable, key=lambda s: s["residual_movement"]["distance_mm"], default=None)
    maximum = representative["residual_movement"]["distance_mm"] if representative else None
    raw_maximum = max((s["observed_movement"]["distance_mm"] for s in signals
                       if s["observed_movement"] is not None), default=None)
    diagnostic = boundary.get("registration", {}).get("diagnostics", {}) if boundary else {}
    excluded = (diagnostic.get("ignored_components", 0) or diagnostic.get("component_limit_reached", False)
                or diagnostic.get("ambiguous_components", 0) or diagnostic.get("unmatched_components", 0)
                or (boundary and boundary.get("partial_verification", False)))
    all_verified = bool(signals) and len(usable) == len(signals) and not excluded
    one_side = sum(s["baseline_source"] in {"before", "after"} for s in usable)
    if suspected:
        status, classification = "REVIEW_SUSPECTED", "suspected"
        label = "어긋남 의심 신호 · 참고 검사"
        detail = (f"주변 층의 통상 이동을 뺀 경계 이동 신호가 최대 {maximum:.3f} mm입니다. "
                  f"{len(suspected)}개 연결 형상에서 기준 {tolerance:.3f} mm를 넘었습니다. ")
        if not all_verified:
            detail += "그 외 일부 형상은 판단 보류입니다. "
    elif all_verified:
        status, classification = "REVIEW", "no_signal"
        label = "뚜렷한 경계 이동 신호 없음 · 참고 검사"
        detail = (f"검사 가능한 기존 연결 형상 {len(usable)}개에서 주변 이동 추이를 벗어난 "
                  f"{tolerance:.3f} mm 초과 신호를 찾지 못했습니다. ")
    else:
        status, classification = "REVIEW", "inconclusive"
        label = "판단 보류 / 확인 필요"
        detail = "형상 대응 또는 안정적인 주변 이동 추이가 부족해 경계 이동 여부를 판단하기 어렵습니다. "
        if any(s["reason"] == "before_after_drift_disagree" for s in signals):
            detail += "교체 전후의 통상 이동 방향·크기도 일치하지 않습니다. "
        if maximum is not None:
            detail += f"추이를 계산할 수 있는 일부 형상의 경계 이동 신호는 최대 {maximum:.3f} mm입니다. "
        elif raw_maximum is not None:
            detail += f"추이 보정 전 인접층 이동 후보는 최대 {raw_maximum:.3f} mm이며 어긋남 확정값이 아닙니다. "
    if one_side:
        detail += f"{one_side}개 형상은 경계 한쪽의 안정적인 추이만 사용했습니다. "
    detail += advisory
    result.update(status=status, label=label, boundary_status=classification,
                  boundary_track_signals=signals, boundary_signal_mm=maximum,
                  boundary_raw_movement_mm=raw_maximum, boundary_verified_track_count=len(usable),
                  boundary_all_tracks_verified=all_verified, summary=lead + detail)
    if representative is not None:
        result["boundary_jump_signal"] = representative["residual_movement"].copy()
    if boundary is not None:
        boundary.update(status=status, label=label, note=detail, boundary_status=classification,
                        boundary_track_signals=signals, boundary_signal_mm=maximum)
    names = set(result.get("counts", {})) | {"REVIEW", "REVIEW_SUSPECTED", "MISALIGNED", "WITHIN_TOLERANCE"}
    result["counts"] = {name: sum(row.get("status") == name for row in rows) for name in names}
    return result



def _summarize_full_scan(result, cancel_event=None, progress_callback=None):
    """Assess every adjacent pair with its own bounded component-track context.

    Row registration data are read-only. Each local boundary copy is classified
    using at most four pairs on either side, keeping the stack walk linear in
    layer count. A normal outcome remains advisory, never a print-safe verdict.
    """
    rows = result.get("results", [])
    indexed = {row["layer"]: row for row in rows}
    settings = result.get("settings", {})
    log = progress_callback or (lambda message: None)
    for index, row in enumerate(rows, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")
        layer = row["layer"]
        local_rows = [indexed[key].copy() for key in range(layer - 4, layer + 5)
                      if key in indexed]
        local = {"settings": {**settings, "switch_layer": layer},
                 "pixel_pitch_mm": result["pixel_pitch_mm"],
                 "sources": result.get("sources", []),
                 "input_image_count": result.get("input_image_count"),
                 "results": local_rows}
        _summarize_boundary(local)
        updated = next(item for item in local_rows if item["layer"] == layer)
        row.pop("jump_after_previous_layer_drift", None)
        row.update(updated)
        # Keep all numeric evidence in JSON/CSV. The report controls display.
        row["boundary_verified_track_count"] = local["boundary_verified_track_count"]
        row["boundary_all_tracks_verified"] = local["boundary_all_tracks_verified"]
        if index == 1 or index % 25 == 0 or index == len(rows):
            log(f"전체 스캔 추이 분석 [{index}/{len(rows)}]")
    if cancel_event is not None and cancel_event.is_set():
        raise CheckCancelled("검사를 중지했습니다. 완료된 보고서로 판정하지 마세요.")

    scan_counts = {key: sum(row.get("boundary_status") == key for row in rows)
                   for key in ("suspected", "no_signal", "inconclusive")}
    suspicious = sorted(row["layer"] for row in rows
                        if row.get("boundary_status") == "suspected")
    ranges = []
    for layer in suspicious:
        if ranges and ranges[-1]["end_layer"] + 1 == layer:
            ranges[-1]["end_layer"] = layer
        else:
            ranges.append({"start_layer": layer, "end_layer": layer})
    if suspicious:
        classification, status = "suspected", "REVIEW_SUSPECTED"
        label = "어긋남 의심 신호 · 전체 스캔 참고 검사"
    elif rows and scan_counts["no_signal"] == len(rows):
        classification, status = "no_signal", "REVIEW"
        label = "뚜렷한 이동 신호 없음 · 전체 스캔 참고 검사"
    else:
        classification, status = "inconclusive", "REVIEW"
        label = "판단 보류 / 확인 필요 · 전체 스캔"
    summary = (f"전체 인접층 {len(rows)}개를 비교했습니다. "
               f"어긋남 의심 {scan_counts['suspected']}개, "
               f"신호 없음 {scan_counts['no_signal']}개, "
               f"판단 보류 {scan_counts['inconclusive']}개입니다. "
               "인접층 단면 변화와 실제 배치 이동을 확정적으로 구분하는 판정은 아닙니다.")
    # Top-level boundary metadata describes a single selected layer only.
    # Do not let stale single-boundary results steer the full-scan report.
    for key in ("boundary_status", "boundary_track_signals", "boundary_signal_mm",
                "boundary_raw_movement_mm", "boundary_verified_track_count",
                "boundary_all_tracks_verified"):
        result.pop(key, None)
    result.update(scan_mode="full", mode_label="전체 인접층 참고 검사",
                  status=status, label=label, summary=summary,
                  full_scan_status=classification, full_scan_counts=scan_counts,
                  suspicious_layers=suspicious, suspicious_ranges=ranges,
                  boundary_jump_signal=None, count=len(rows))
    status_names = set(result.get("counts", {})) | {
        "REVIEW", "REVIEW_SUSPECTED", "MISALIGNED", "WITHIN_TOLERANCE"}
    result["counts"] = {name: sum(row.get("status") == name for row in rows)
                        for name in status_names}
    return result


def _run_stack_check(source, dpi, switch_layer=None, *, full_scan=False,
                     progress_callback=None, cancel_event=None, output=None):
    """Inspect a complete mixed stack, optionally limited to a known boundary.

    source is a ZIP pathname or a list/tuple of selected image paths. Filename
    trailing numbers must form 0..N-1 or 1..N. The exact input bytes are read in
    place; each adjacent input image is decoded once. All outcomes are advisory.
    output is an optional API/testing destination; the UI chooses it itself.
    """
    if isinstance(dpi, (bool, np.bool_)):
        raise ValueError("DPI는 0보다 큰 숫자여야 합니다.")
    try:
        dpi = float(dpi)
    except (TypeError, ValueError) as exc:
        raise ValueError("DPI는 0보다 큰 숫자여야 합니다.") from exc
    if not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("DPI는 0보다 큰 유한한 숫자여야 합니다.")
    if not full_scan and (isinstance(switch_layer, (bool, np.bool_)) or not isinstance(switch_layer, (int, np.integer)) or switch_layer < 2):
        raise ValueError("치환 시작 층은 2 이상의 정수여야 합니다.")
    if isinstance(source, (list, tuple)):
        if not source:
            raise ValueError("첫 층부터 마지막 층까지 전체 이미지를 선택하세요.")
        source = [str(Path(p).expanduser().resolve()) for p in source]
        output_parent = Path(source[0]).parent
    else:
        if not source:
            raise ValueError("ZIP 또는 전체 슬라이싱 이미지를 선택하세요.")
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".zip":
            raise ValueError("ZIP 파일을 선택하거나 이미지 전체 선택을 사용하세요.")
        output_parent = source.parent
        source = str(source)
    destination = str(Path(output).expanduser().resolve()) if output else str(output_parent / "slice_alignment_reports")
    options = dict(mode="adjacent", old=source, switch_layer=2 if full_scan else int(switch_layer),
                   context=4, dpi_x=dpi, dpi_y=dpi, tolerance_mm=.2, search_mm=3.,
                   threshold=127, polarity="white", numbering="auto", min_area=1,
                   max_components=512, output=destination, simple_boundary=not full_scan, full_scan=full_scan)
    return run_check(options, progress_callback=progress_callback, cancel_event=cancel_event)


def run_boundary_check(source, dpi, switch_layer, *, progress_callback=None,
                       cancel_event=None, output=None):
    """Check one specified replacement boundary and its surrounding layers."""
    return _run_stack_check(source, dpi, switch_layer, progress_callback=progress_callback,
                            cancel_event=cancel_event, output=output)


def run_full_check(source, dpi, *, progress_callback=None, cancel_event=None, output=None):
    """Check every adjacent pair, reading each image only once, without a boundary input."""
    return _run_stack_check(source, dpi, full_scan=True, progress_callback=progress_callback,
                            cancel_event=cancel_event, output=output)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parsed = build_parser().parse_args(args)
    if not args or parsed.gui or args == ["--no-install"]:
        gui_main()
        return 0
    try:
        source = parsed.images if parsed.images is not None else parsed.zip_path
        result = (run_full_check(source, parsed.dpi, progress_callback=print) if parsed.full_scan else
                  run_boundary_check(source, parsed.dpi, parsed.switch_layer, progress_callback=print))
        return 1 if result["status"] == "REVIEW_SUSPECTED" else 2
    except CheckCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 130
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print("검사 실패: " + str(exc), file=sys.stderr)
        return 3




# ── 한국어 화면 ──
"""Korean Tkinter front end; registration and all image I/O run off the UI thread."""

import os
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:
    tk = None
    filedialog = messagebox = scrolledtext = ttk = None


ROOT = Path(__file__).resolve().parent


class SimpleAlignmentApp:
    """One mixed stack and DPI, with an optional known replacement boundary."""
    def __init__(self, root):
        self.root = root
        root.title("슬라이싱 위치 검사 v" + VERSION)
        width = min(780, max(640, root.winfo_screenwidth() - 80))
        height = min(710, max(600, root.winfo_screenheight() - 80))
        root.geometry(f"{width}x{height}")
        root.minsize(640, 600)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self.source = None
        self.vars = {"dpi": tk.StringVar(value="400"), "switch_layer": tk.StringVar(value="61")}
        self.scan_mode = tk.StringVar(value="boundary")
        self.mode_hint = tk.StringVar()
        self.input_summary = tk.StringVar(value="ZIP 파일 또는 슬라이싱 이미지 전체를 선택하세요.")
        self.status = tk.StringVar(value="이미지와 DPI, 치환 시작 층을 입력하면 검사할 수 있습니다.")
        self.summary = None
        self.worker = None
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self._build()
        self._apply_scan_mode()
        root.after(100, self._poll)

    def _build(self):
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(7, weight=1)
        def rewrap(event):
            def visit(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0):
                        child.configure(wraplength=max(500, event.width - 65))
                    visit(child)
            visit(main)
        main.bind("<Configure>", rewrap)
        ttk.Label(main, text="슬라이싱 위치 검사", font=("맑은 고딕", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(main, text="전체 이미지를 넣고, 치환한 경계 또는 전체 층을 검사하세요.", wraplength=700).grid(row=1, column=0, sticky="ew", pady=(8,14))
        inputs = ttk.LabelFrame(main, text="1. 전체 슬라이싱 이미지", padding=12)
        inputs.grid(row=2, column=0, sticky="ew")
        buttons = ttk.Frame(inputs)
        buttons.pack(anchor="w")
        self.zip_button = ttk.Button(buttons, text="ZIP 파일 선택", command=self._select_zip)
        self.zip_button.pack(side="left")
        self.images_button = ttk.Button(buttons, text="이미지 전체 선택", command=self._select_images)
        self.images_button.pack(side="left", padx=10)
        ttk.Label(inputs, textvariable=self.input_summary, wraplength=680).pack(anchor="w", pady=(10,0))
        settings = ttk.LabelFrame(main, text="2. 검사 기준", padding=12)
        settings.grid(row=3, column=0, sticky="ew", pady=12)
        modes = ttk.Frame(settings)
        modes.grid(row=0,column=0,columnspan=4,sticky="w",pady=(0,12))
        self.mode_buttons = []
        for label, value in (("치환 경계 검사", "boundary"), ("전체 스캔", "full")):
            button = ttk.Radiobutton(modes,text=label,value=value,variable=self.scan_mode,command=self._apply_scan_mode)
            button.pack(side="left",padx=(0,22))
            self.mode_buttons.append(button)
        ttk.Label(settings, text="DPI").grid(row=1,column=0,sticky="w")
        self.dpi_entry = ttk.Entry(settings,textvariable=self.vars["dpi"],width=12)
        self.dpi_entry.grid(row=1,column=1,sticky="w",padx=(10,35))
        ttk.Label(settings, text="치환 시작 층").grid(row=1,column=2,sticky="w")
        self.switch_entry = ttk.Entry(settings,textvariable=self.vars["switch_layer"],width=12)
        self.switch_entry.grid(row=1,column=3,sticky="w",padx=10)
        ttk.Label(settings, text="DPI는 실제 XY 해상도입니다. 400은 예시값이며, 가로·세로 DPI가 같은 이미지가 기준입니다.", wraplength=680).grid(row=2,column=0,columnspan=4,sticky="w",pady=(10,0))
        ttk.Label(main, textvariable=self.mode_hint, wraplength=700).grid(row=4,column=0,sticky="ew",pady=(0,12))
        controls = ttk.Frame(main)
        controls.grid(row=5,column=0,sticky="ew")
        self.run_button=ttk.Button(controls,text="검사 시작",command=self._start)
        self.run_button.pack(side="left")
        self.stop_button=ttk.Button(controls,text="검사 중지",command=self._cancel,state="disabled")
        self.stop_button.pack(side="left",padx=8)
        self.report_button=ttk.Button(controls,text="보고서 보기",command=self._open_report,state="disabled")
        self.report_button.pack(side="left",padx=(12,8))
        self.folder_button=ttk.Button(controls,text="결과 폴더 열기",command=self._open_folder,state="disabled")
        self.folder_button.pack(side="left")
        progress_area=ttk.Frame(main)
        progress_area.grid(row=6,column=0,sticky="ew",pady=10)
        self.progress=ttk.Progressbar(progress_area,mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(progress_area,textvariable=self.status,wraplength=700).pack(anchor="w",pady=(8,0))
        self.log=scrolledtext.ScrolledText(main,height=7,wrap="word",state="disabled",font=("맑은 고딕",10))
        self.log.grid(row=7,column=0,sticky="nsew")

    def _apply_scan_mode(self):
        full = self.scan_mode.get() == "full"
        self.switch_entry.configure(state="disabled" if full else "normal")
        self.mode_hint.set(
            "첫 층부터 마지막 층까지 모든 인접층을 검사합니다. 치환 시작 층은 입력하지 않아도 됩니다. 정상 단면 변화와 구별되지 않을 수 있는 참고 판정입니다."
            if full else
            "예: 61~300층을 교체했다면 치환 시작 층에 61을 입력하세요. 경계 전후 형상의 이동 추이를 비교하는 참고 판정입니다.")

    def _select_zip(self):
        path=filedialog.askopenfilename(title="전체 슬라이싱 이미지 ZIP",filetypes=[("ZIP 파일","*.zip")])
        if path:
            self.source=path
            self.input_summary.set("ZIP: " + path)

    def _select_images(self):
        paths=filedialog.askopenfilenames(title="첫 층부터 마지막 층까지 전체 선택 (Ctrl+A)",
                filetypes=[("슬라이싱 이미지","*.png *.bmp *.tif *.tiff *.jpg *.jpeg")])
        if paths:
            self.source=list(paths)
            self.input_summary.set(f"이미지 {len(paths)}개 선택 · {Path(paths[0]).parent}")

    def _options(self):
        if self.source is None:
            raise ValueError("ZIP 또는 이미지 전체를 선택하세요.")
        try:
            dpi=float(self.vars["dpi"].get().strip())
        except ValueError as exc:
            raise ValueError("DPI는 숫자로 입력하세요.") from exc
        if not math.isfinite(dpi) or dpi<=0:
            raise ValueError("DPI는 0보다 큰 숫자여야 합니다.")
        if self.scan_mode.get() == "full":
            return dict(source=self.source,dpi=dpi,full_scan=True)
        try:
            layer=int(self.vars["switch_layer"].get().strip())
        except ValueError as exc:
            raise ValueError("치환 시작 층은 정수로 입력하세요.") from exc
        if layer<2:
            raise ValueError("치환 시작 층은 2 이상이어야 합니다.")
        return dict(source=self.source,dpi=dpi,switch_layer=layer,full_scan=False)

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            options=self._options()
        except Exception as exc:
            messagebox.showerror("입력 확인",str(exc))
            return
        self.summary=None
        self.cancel_event.clear()
        for widget in (self.run_button,self.zip_button,self.images_button,self.report_button,self.folder_button,
                       self.dpi_entry,self.switch_entry,*self.mode_buttons):
            widget.configure(state="disabled")
        self.stop_button.configure(state="normal")
        full_scan = options.pop("full_scan")
        self.status.set("전체 인접층을 검사하고 있습니다." if full_scan else "전체 파일의 층 번호와 치환 경계 주변을 검사하고 있습니다.")
        self.log.configure(state="normal")
        self.log.delete("1.0","end")
        self.log.configure(state="disabled")
        self.progress.start(12)
        def work():
            try:
                check = run_full_check if full_scan else run_boundary_check
                result=check(**options,progress_callback=lambda msg:self.events.put(("log",msg)),cancel_event=self.cancel_event)
                self.events.put(("done",result))
            except Exception as exc:
                self.events.put(("error",(str(exc),traceback.format_exc())))
        self.worker=threading.Thread(target=work,name="slice-alignment-check",daemon=True)
        self.worker.start()

    def _finish(self):
        self.progress.stop()
        for widget in (self.run_button,self.zip_button,self.images_button,self.dpi_entry,*self.mode_buttons):
            widget.configure(state="normal")
        self._apply_scan_mode()
        self.stop_button.configure(state="disabled")
    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status.set("중지를 요청했습니다. 현재 이미지 처리가 끝나면 멈춥니다.")
        self.stop_button.configure(state="disabled")

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "done":
                    self._finish()
                    self.summary = payload if isinstance(payload, dict) else {}
                    label = self.summary.get("label", self.summary.get("status", "완료"))
                    count = self.summary.get("count", "")
                    self.status.set(f"검사 완료 · {label} · 비교 {count}쌍")
                    self._append(self.summary.get("summary", ""))
                    self._append(f"결과 폴더: {self.summary.get('result_dir', '')}")
                    self._append("보고서에서 판정 근거와 겹쳐 보기 이미지를 확인하세요.")
                    if self.summary.get("report_html"):
                        self.report_button.configure(state="normal")
                    if self.summary.get("result_dir"):
                        self.folder_button.configure(state="normal")
                elif kind == "error":
                    self._finish()
                    msg, trace = payload
                    if self.cancel_event.is_set():
                        self.status.set("검사를 중지했습니다. 완료된 보고서가 생성되지 않았습니다.")
                        self._append(str(msg))
                    else:
                        self.status.set("검사를 완료하지 못했습니다.")
                        self._append(str(trace))
                        messagebox.showerror("검사 오류", str(msg))
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _open_report(self) -> None:
        if not self.summary or not self.summary.get("report_html"):
            return
        path = Path(self.summary["report_html"]).resolve()
        if not path.is_file():
            messagebox.showerror("보고서", "보고서 파일이 없습니다.")
            return
        webbrowser.open(path.as_uri())

    def _open_folder(self) -> None:
        if not self.summary or not self.summary.get("result_dir"):
            return
        path = str(Path(self.summary["result_dir"]).resolve())
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path], shell=False)
            else:
                subprocess.Popen(["xdg-open", path], shell=False)
        except OSError as exc:
            messagebox.showerror("결과 폴더", str(exc))

    def _close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("검사 진행 중", "검사를 중지하고 프로그램을 종료할까요?"):
                return
            self.cancel_event.set()
        self.root.destroy()



AlignmentApp = SimpleAlignmentApp



def gui_main() -> None:
    if tk is None:
        raise RuntimeError("Tkinter가 없는 Python입니다. 공식 Python 설치에 Tcl/Tk를 포함하거나 명령줄 검사 모드를 사용하세요.")
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("clam")
    except tk.TclError:
        pass
    AlignmentApp(root)
    root.mainloop()



__all__ = ["compare_slices", "run_check", "run_boundary_check", "run_full_check", "LayerSource", "main", "gui_main"]

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        _startup_failure()
        raise SystemExit(3)
