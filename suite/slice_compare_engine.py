"""Lossless inspection and comparison of two image-file bundles.

PNG samples use native libpng (imagecodecs), with PyPNG as a safe fallback.
This includes original 16-bit RGB/RGBA samples. TIFF samples are
read by tifffile, without converting them to Pillow's 8-bit RGB mode.
Input archives are never extracted to the filesystem.
"""
from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
import os
import re
import struct
import threading
import time
import warnings
import zipfile
import zlib
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = False

_FAST_VALIDATION_WARNING = "빠른 비교: 바이트가 동일한 파일과 한쪽에만 있는 파일은 헤더/PNG CRC를 검사했습니다. 동일 바이트는 파일 간 일치를 증명하지만, 해당 파일의 전체 픽셀 디코딩 무결성은 검사하지 않았습니다."


class ComparisonCancelled(Exception):
    pass


class UnsupportedImage(Exception):
    pass


@dataclass(frozen=True)
class SourceSpec:
    paths: tuple[str, ...]

    @classmethod
    def from_paths(cls, paths: Iterable[os.PathLike | str] | os.PathLike | str):
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        return cls(tuple(str(Path(p).expanduser().absolute()) for p in paths))

    @property
    def label(self):
        return ", ".join(self.paths)


@dataclass
class CompareOptions:
    match_mode: str = "path"
    max_file_bytes: int = 512 * 1024 * 1024
    max_archive_bytes: int = 8 * 1024 * 1024 * 1024
    max_archive_entries: int = 100_000
    max_compression_ratio: int = 2000
    max_pixels: int = 100_000_000
    max_frames: int = 10_000
    png_decoder: str = "auto"  # 'python' selects the precision-reference fallback.
    verify_identical: bool = True  # False: equal bytes need header/CRC validation only.
    decode_cache_size: int = 256


@dataclass
class ComparisonRow:
    left_name: str | None = None
    right_name: str | None = None
    status: str = "error"
    differences: list[str] = field(default_factory=list)
    left: dict | None = None
    right: dict | None = None
    error: str = ""
    pixels_equal: bool | None = None
    pixel_comparison: str = ""

    @property
    def name(self):
        return self.left_name or self.right_name or "입력 오류"

    def to_dict(self):
        return asdict(self)


@dataclass
class Report:
    rows: list[ComparisonRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    left_source: str = ""
    right_source: str = ""
    match_mode: str = "path"
    elapsed_seconds: float = 0.0
    performance: dict = field(default_factory=dict)

    @property
    def overall_identical(self):
        return self.summary["overall_identical"]

    @property
    def summary(self):
        counts = {key: sum(row.status == key for row in self.rows) for key in
                  ("identical", "different", "left_only", "right_only", "error", "unsupported")}
        return {
            "total": len(self.rows), **counts,
            "left_files": sum(row.left_name is not None and row.left_name != "입력 오류" for row in self.rows),
            "right_files": sum(row.right_name is not None and row.right_name != "입력 오류" for row in self.rows),
            "pixels_equal": sum(row.pixels_equal is True for row in self.rows),
            "left_layers": sum((row.left or {}).get("frame_count", 0) for row in self.rows),
            "right_layers": sum((row.right or {}).get("frame_count", 0) for row in self.rows),
            "overall_identical": bool(self.rows) and not self.cancelled and counts["identical"] == len(self.rows),
        }

    def to_dict(self):
        return {"schema_version": 1, "summary": self.summary, "cancelled": self.cancelled,
                "left_source": self.left_source, "right_source": self.right_source,
                "match_mode": self.match_mode, "warnings": self.warnings,
                "elapsed_seconds": self.elapsed_seconds, "performance": self.performance,
                "rows": [row.to_dict() for row in self.rows]}

    def save_json(self, path):
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False)

    def save_csv(self, path):
        # UTF-8 BOM allows the Korean column content to open correctly in Excel.
        columns = ["left_name", "right_name", "status", "pixels_equal", "pixel_comparison", "differences", "error", "left", "right"]
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in self.rows:
                item = row.to_dict()
                for key in ("differences", "left", "right"):
                    item[key] = json.dumps(item[key], ensure_ascii=False, allow_nan=False)
                # Do not allow a source filename to become an Excel formula.
                for key in ("left_name", "right_name", "error"):
                    value = item.get(key)
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
                        item[key] = "'" + value
                writer.writerow(item)

    # Alternate names are useful to command-line and notebook callers.
    export_json = save_json
    export_csv = save_csv


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ComparisonCancelled()


def _stamp(path):
    info = os.stat(path)
    return (info.st_size, info.st_mtime_ns, info.st_ino)


class _ArchivePool:
    """One central-directory parse per archive per comparison, no global state."""
    def __init__(self):
        self.archives = {}

    def get(self, path):
        if path not in self.archives:
            self.archives[path] = zipfile.ZipFile(path)
        return self.archives[path]

    def close(self):
        for archive in self.archives.values():
            archive.close()
        self.archives.clear()


@contextmanager
def _archive(path, pool=None):
    if pool is not None:
        yield pool.get(path)
    else:
        with zipfile.ZipFile(path) as archive:
            yield archive


@dataclass
class _Entry:
    name: str
    source_path: str
    byte_size: int
    stamp: tuple
    archive_member: str | None = None
    archive_pool: _ArchivePool | None = field(default=None, repr=False)

    def peek(self, cancel_event):
        _check_cancel(cancel_event)
        if _stamp(self.source_path) != self.stamp:
            raise ValueError("입력 파일이 검사 도중 변경되었습니다.")
        if self.archive_member is None:
            with open(self.source_path, "rb") as stream:
                return stream.read(16)
        with _archive(self.source_path, self.archive_pool) as archive:
            with archive.open(self.archive_member) as stream:
                return stream.read(16)

    @contextmanager
    def open(self, options, cancel_event):
        _check_cancel(cancel_event)
        if _stamp(self.source_path) != self.stamp:
            raise ValueError("입력 파일이 검사 도중 변경되었습니다.")
        if self.byte_size > options.max_file_bytes:
            raise ValueError(f"파일 크기 제한 초과: {self.byte_size:,} bytes")
        if self.archive_member is None:
            with open(self.source_path, "rb") as stream:
                yield stream
        else:
            # BytesIO, not extraction: archive entry paths cannot write anywhere.
            buffer = io.BytesIO()
            with _archive(self.source_path, self.archive_pool) as archive:
                with archive.open(self.archive_member) as source:
                    total = 0
                    while True:
                        _check_cancel(cancel_event)
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > options.max_file_bytes or total > self.byte_size:
                            raise ValueError("ZIP 항목의 실제 크기가 제한 또는 헤더 크기를 초과합니다.")
                        buffer.write(chunk)
            if total != self.byte_size:
                raise ValueError("ZIP 항목 크기 불일치")
            buffer.seek(0)
            try:
                yield buffer
            finally:
                buffer.close()
        if _stamp(self.source_path) != self.stamp:
            raise ValueError("입력 파일이 검사 도중 변경되었습니다.")


def natural_key(value):
    """Natural filename order: layer2 precedes layer10."""
    return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold())
                 for part in re.split(r"(\d+)", value))


def _safe_member(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or re.match(r"^[A-Za-z]:", normalized) or ".." in path.parts:
        raise ValueError(f"유효하지 않은 ZIP 경로: {name}")
    return str(path)


def _scan_source(spec, options, cancel_event, archive_pool=None):
    entries: list[_Entry] = []
    errors: list[str] = []
    notices: list[str] = []
    if not spec.paths:
        return [], ["입력 파일 또는 폴더가 없습니다."], []
    paths = [Path(p) for p in spec.paths]
    only_one = len(paths) == 1
    files_only = all(p.is_file() for p in paths)
    common_parent = None
    if files_only and len(paths) > 1:
        try:
            common_parent = Path(os.path.commonpath([str(p.parent) for p in paths]))
        except ValueError:
            pass

    def add_file(path, name):
        try:
            info = _stamp(path)
            entries.append(_Entry(name.replace("\\", "/"), str(path), info[0], info))
        except OSError as exc:
            errors.append(f"{name}: {exc}")

    for path in paths:
        _check_cancel(cancel_event)
        if not path.exists():
            errors.append(f"경로를 찾을 수 없습니다: {path}")
            continue
        if path.is_dir():
            prefix = "" if only_one else path.name + "/"
            def walk_error(exc):
                errors.append(f"폴더 읽기 오류: {exc}")
            for folder, dirs, names in os.walk(path, followlinks=False, onerror=walk_error):
                _check_cancel(cancel_event)
                symlink_dirs = [name for name in dirs if Path(folder, name).is_symlink()]
                for name in symlink_dirs:
                    errors.append(f"폴더 링크는 검사할 수 없습니다: {Path(folder, name)}")
                    dirs.remove(name)
                for name in names:
                    child = Path(folder, name)
                    add_file(child, prefix + child.relative_to(path).as_posix())
            continue
        if not path.is_file():
            errors.append(f"일반 파일이 아닙니다: {path}")
            continue
        # Extension alone does not establish whether this is a ZIP file.
        try:
            is_zip = zipfile.is_zipfile(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if is_zip or path.suffix.lower() == ".zip":
            archive_entries = []
            try:
                stamp = _stamp(path)
                with _archive(str(path), archive_pool) as archive:
                    infos = archive.infolist()
                    if len(infos) > options.max_archive_entries:
                        raise ValueError("ZIP 항목 수 제한을 초과했습니다.")
                    if sum(info.file_size for info in infos) > options.max_archive_bytes:
                        raise ValueError("ZIP 전체 압축 해제 크기 제한을 초과했습니다.")
                    for info in infos:
                        _check_cancel(cancel_event)
                        if info.is_dir():
                            continue
                        name = _safe_member(info.filename)
                        if info.flag_bits & 1:
                            errors.append(f"암호화된 ZIP 항목: {name}")
                            continue
                        if (info.external_attr >> 16) & 0o170000 == 0o120000:
                            errors.append(f"ZIP 심볼릭 링크는 지원하지 않습니다: {name}")
                            continue
                        if info.file_size > options.max_file_bytes:
                            errors.append(f"ZIP 항목 크기 제한 초과: {name}")
                            continue
                        if info.file_size > max(info.compress_size, 1) * options.max_compression_ratio:
                            errors.append(f"ZIP 압축률 제한 초과: {name}")
                            continue
                        archive_entries.append(_Entry(name, str(path), info.file_size, stamp, info.filename, archive_pool))
                # An enclosing export folder is normally packaging, not a layer path.
                if only_one and archive_entries:
                    parts = [entry.name.split("/") for entry in archive_entries]
                    if all(len(part) > 1 for part in parts) and len({part[0] for part in parts}) == 1:
                        for entry in archive_entries:
                            entry.name = entry.name.split("/", 1)[1]
                        notices.append(f"ZIP 최상위 공통 폴더를 제외하여 비교합니다: {parts[0][0]}")
                if not only_one:
                    for entry in archive_entries:
                        entry.name = path.stem + "/" + entry.name
                entries.extend(archive_entries)
            except ComparisonCancelled:
                raise
            except Exception as exc:
                errors.append(f"ZIP 읽기 오류 ({path.name}): {exc}")
        else:
            name = path.name
            if common_parent is not None:
                name = path.relative_to(common_parent).as_posix()
            add_file(path, name)

    # Never silently choose one of duplicate archive names or case collisions.
    by_name: dict[str, list[_Entry]] = {}
    for entry in entries:
        by_name.setdefault(entry.name.casefold(), []).append(entry)
    rejected = set()
    for key, group in by_name.items():
        if len(group) > 1:
            rejected.add(key)
            errors.append("중복 또는 대소문자 충돌 경로: " + ", ".join(e.name for e in group))
    entries = [entry for entry in entries if entry.name.casefold() not in rejected]
    entries.sort(key=lambda entry: (natural_key(entry.name), entry.name))
    if not entries and not errors:
        errors.append("입력 묶음에 검사할 파일이 없습니다.")
    return entries, errors, notices


def _detect_format(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head[:4] in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"):
        return "TIFF"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if head.startswith(b"BM"):
        return "BMP"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "WEBP"
    if head.startswith(b"PK\x03\x04"):
        return "ZIP"
    return "UNKNOWN"


def _hash_stream(stream, cancel_event):
    digest = hashlib.sha256()
    while True:
        _check_cancel(cancel_event)
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _canonical_array(array):
    array = np.asarray(array)
    if array.dtype.kind == "b":
        array = array.astype(np.uint8)
    if array.dtype.byteorder == ">" or (array.dtype.byteorder == "=" and not np.little_endian):
        array = array.astype(array.dtype.newbyteorder("<"))
    return np.ascontiguousarray(array)


def _array_hash(array, cancel_event):
    array = _canonical_array(array)
    digest = hashlib.sha256()
    digest.update(json.dumps({"shape": array.shape, "dtype": array.dtype.name}).encode())
    view = memoryview(array).cast("B")
    for index in range(0, len(view), 1024 * 1024):
        _check_cancel(cancel_event)
        digest.update(view[index:index + 1024 * 1024])
    return digest.hexdigest()


def _check_size(width, height, channels, options):
    if width <= 0 or height <= 0 or width * height > options.max_pixels:
        raise ValueError(f"해상도 제한 초과 또는 잘못된 해상도: {width} × {height}")
    if width * height * max(channels, 1) * 8 > options.max_file_bytes * 4:
        raise ValueError("디코딩 메모리 제한을 초과합니다.")


def _png_structure(stream, cancel_event):
    stream.seek(8)
    header = None
    color_chunks = {}
    dpi = None
    seen_data = False
    seen_header = False
    auxiliary = {}
    while True:
        _check_cancel(cancel_event)
        length_raw = stream.read(4)
        if len(length_raw) != 4:
            raise ValueError("PNG IEND가 없거나 파일이 잘렸습니다.")
        length = struct.unpack(">I", length_raw)[0]
        chunk_type = stream.read(4)
        if len(chunk_type) != 4:
            raise ValueError("잘린 PNG 청크")
        if not seen_header and chunk_type != b"IHDR":
            raise ValueError("PNG 첫 청크가 IHDR가 아닙니다.")
        crc = zlib.crc32(chunk_type)
        saved = bytearray()
        keep = chunk_type in (b"IHDR", b"pHYs", b"eXIf", b"iCCP", b"tRNS")
        if keep and length > 16 * 1024 * 1024:
            raise ValueError("PNG 메타데이터 크기 제한 초과")
        hasher = hashlib.sha256() if chunk_type in (b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"sBIT") else None
        remaining = length
        while remaining:
            _check_cancel(cancel_event)
            data = stream.read(min(remaining, 1024 * 1024))
            if not data:
                raise ValueError("잘린 PNG 청크 데이터")
            remaining -= len(data)
            crc = zlib.crc32(data, crc)
            if hasher is not None:
                hasher.update(data)
            if keep:
                saved.extend(data)
        checksum = stream.read(4)
        if len(checksum) != 4 or struct.unpack(">I", checksum)[0] != crc & 0xffffffff:
            raise ValueError(f"PNG CRC 오류: {chunk_type!r}")
        if chunk_type == b"IHDR":
            if seen_header or length != 13:
                raise ValueError("잘못된 PNG IHDR")
            seen_header = True
            header = struct.unpack(">IIBBBBB", saved)
        if chunk_type == b"IDAT":
            seen_data = True
        if chunk_type == b"acTL":
            raise UnsupportedImage("애니메이션 PNG(APNG)는 지원하지 않습니다.")
        if chunk_type in (b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"sBIT"):
            color_chunks[chunk_type.decode()] = hasher.hexdigest()
        if chunk_type in (b"eXIf", b"iCCP", b"tRNS"):
            auxiliary[chunk_type.decode()] = bytes(saved)
        if chunk_type == b"pHYs" and length == 9:
            x, y, unit = struct.unpack(">IIB", saved)
            if unit == 1:
                dpi = [round(x * .0254, 5), round(y * .0254, 5)]
        if chunk_type == b"IEND":
            if length != 0 or not seen_data:
                raise ValueError("잘못된 PNG IEND/IDAT")
            break
    stream.seek(0)
    return header, color_chunks, dpi, auxiliary


def _png_metadata(stream, options, cancel_event):
    header, color_info, dpi, auxiliary = _png_structure(stream, cancel_event)
    width, height, bitdepth, color_type, *_ = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise UnsupportedImage(f"지원하지 않는 PNG 색상 형식: {color_type}")
    _check_size(width, height, channels, options)
    valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if bitdepth not in valid_depths[color_type] or header[4] != 0 or header[5] != 0 or header[6] not in (0, 1):
        raise ValueError("PNG 비트수/압축/필터/인터레이스 헤더가 올바르지 않습니다.")
    orientation = 1
    if "eXIf" in auxiliary:
        exif = Image.Exif()
        exif.load(auxiliary["eXIf"])
        orientation = int(exif.get(274, 1))
    if "iCCP" in auxiliary:
        raw = auxiliary["iCCP"]
        separator = raw.find(b"\0")
        if separator < 1 or separator + 2 > len(raw) or raw[separator + 1] != 0:
            raise ValueError("잘못된 PNG ICC 색상 프로필")
        inflater = zlib.decompressobj()
        icc = inflater.decompress(raw[separator + 2:], 16 * 1024 * 1024 + 1)
        if len(icc) > 16 * 1024 * 1024 or inflater.unconsumed_tail or not inflater.eof or not icc:
            raise ValueError("PNG ICC 색상 프로필 크기 초과 또는 손상")
        color_info.pop("iCCP", None)
        color_info["ICCProfile"] = hashlib.sha256(icc).hexdigest()
    transparency = None
    if "tRNS" in auxiliary and color_type in (0, 2):
        raw = auxiliary["tRNS"]
        expected_length = 2 if color_type == 0 else 6
        if len(raw) != expected_length:
            raise ValueError("잘못된 PNG 투명 색상 데이터")
        transparency = struct.unpack(">" + "H" * channels, raw)
    dtype = np.dtype("<u2" if bitdepth == 16 else "u1")
    frame = {"width": width, "height": height, "bit_depth": bitdepth,
             "channels": channels, "bits_per_pixel": bitdepth * channels,
             "dtype": dtype.name, "mode": {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}[color_type],
             "palette_sha256": None, "transparency": transparency,
             "color_info": color_info, "orientation": orientation}
    return frame, dpi, header


def _decode_png(stream, options, cancel_event):
    frame, dpi, header = _png_metadata(stream, options, cancel_event)
    width, height, bitdepth, color_type, *_ = header
    channels = frame["channels"]
    dtype = np.dtype(frame["dtype"])
    # Native libpng handles common slicing PNGs orders of magnitude faster than
    # Python's per-sample filters. Keep indexed/sub-byte samples on PyPNG so
    # their stored indices and unscaled 1/2/4-bit values remain exact.
    if options.png_decoder != "python" and bitdepth in (8, 16) and color_type != 3:
        try:
            import imagecodecs
            native_decode = imagecodecs.png_decode
        except (ImportError, AttributeError):
            native_decode = None
        if native_decode is not None:
            _check_cancel(cancel_event)
            stream.seek(0)
            array = native_decode(stream.read())
            _check_cancel(cancel_event)
            array = np.asarray(array)
            # libpng expands a tRNS key to an alpha channel; preserve the
            # original channels and retain that key separately in metadata.
            if frame["transparency"] is not None and array.ndim == 3 and array.shape[-1] == channels + 1:
                array = array[..., :channels]
            if array.dtype.name != dtype.name or array.size != width * height * channels:
                raise ValueError("PNG 네이티브 디코더가 원본 비트수/채널을 보존하지 않았습니다.")
            array = array.reshape(height, width, channels)
            digest = _array_hash(array, cancel_event)
            frame.update(sample_sha256=digest, visual_sha256=digest)
            return [frame], dpi, []
    try:
        import png
    except ImportError as exc:
        raise UnsupportedImage("이 PNG의 원본 샘플 검사를 위해 pypng를 설치해야 합니다.") from exc
    stream.seek(0)
    reader = png.Reader(file=stream)
    read_width, read_height, rows, info = reader.read()
    if (read_width, read_height) != (width, height):
        raise ValueError("PNG 헤더와 디코더 크기 불일치")
    descriptor = {"shape": [height, width, channels], "dtype": dtype.name}
    digest = hashlib.sha256(json.dumps(descriptor).encode())
    visual = hashlib.sha256()
    palette = info.get("palette")
    palette_digest = None
    if palette is not None:
        pal = np.array([tuple(color) + ((255,) if len(color) == 3 else ()) for color in palette], dtype=np.uint8)
        palette_digest = hashlib.sha256(pal.tobytes()).hexdigest()
        visual.update(json.dumps({"shape": [height, width, 4], "dtype": "uint8"}).encode())
    row_count = 0
    for samples in rows:
        _check_cancel(cancel_event)
        row = np.asarray(samples, dtype=dtype)
        if row.size != width * channels:
            raise ValueError("PNG 행의 샘플 수가 올바르지 않습니다.")
        digest.update(row.tobytes())
        if palette is not None:
            if row.size and int(row.max()) >= len(pal):
                raise ValueError("PNG 팔레트 인덱스가 범위를 벗어났습니다.")
            visual.update(pal[row].tobytes())
        row_count += 1
        if row_count > height:
            raise ValueError("PNG 행 수가 해상도를 초과했습니다.")
    if row_count != height:
        raise ValueError("PNG 픽셀 데이터가 잘렸습니다.")
    frame.update(sample_sha256=digest.hexdigest(),
                 visual_sha256=visual.hexdigest() if palette is not None else digest.hexdigest(),
                 palette_sha256=palette_digest, transparency=info.get("transparent"))
    return [frame], dpi, []


def _rational(value):
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else None
        return float(value)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _decode_tiff(stream, options, cancel_event, header_only=False):
    try:
        import tifffile
    except ImportError as exc:
        raise UnsupportedImage("TIFF 원본 비트수 검사를 위해 tifffile을 설치해야 합니다.") from exc
    frames = []
    notices = []
    dpi = None
    stream.seek(0, 2)
    stream_size = stream.tell()
    stream.seek(0)
    with tifffile.TiffFile(stream) as document:
        def inspect_pages(pages, prefix=""):
            nonlocal dpi
            for index, page in enumerate(pages):
                _check_cancel(cancel_event)
                if len(frames) >= options.max_frames:
                    raise ValueError("TIFF 프레임 수 제한 초과")
                width, height = int(page.imagewidth), int(page.imagelength)
                channels = int(page.samplesperpixel)
                _check_size(width, height, channels, options)
                depth = page.bitspersample
                depth = [int(x) for x in depth] if isinstance(depth, tuple) else int(depth)
                for offset, count in zip(page.dataoffsets, page.databytecounts):
                    if offset < 0 or count < 0 or offset + count > stream_size:
                        raise ValueError(f"잘린 TIFF 스트립/타일 데이터 (프레임 {prefix}{index + 1})")
                array = None
                dtype_name = np.dtype(page.dtype).name
                if not header_only:
                    try:
                        array = page.asarray()
                    except Exception as exc:
                        # No Pillow fallback: it can silently reduce RGB16 to RGB8.
                        raise ValueError(f"TIFF 디코딩 실패 (프레임 {prefix}{index + 1}): {exc}. imagecodecs 설치를 확인하세요.") from exc
                    axes = page.axes
                    if "Y" in axes and "X" in axes:
                        order = [axes.index("Y"), axes.index("X")] + [n for n, axis in enumerate(axes) if axis not in "YX"]
                        if len(order) == array.ndim:
                            array = np.transpose(array, order)
                    array = _canonical_array(array)
                    if array.size != width * height * channels:
                        raise UnsupportedImage(f"일반 2D 슬라이스가 아닌 TIFF 페이지: {page.shape}")
                    array = array.reshape(height, width, channels)
                    dtype_name = array.dtype.name
                photometric = getattr(page.photometric, "name", str(page.photometric))
                mode = {"MINISBLACK": "L", "MINISWHITE": "L (white=0)", "PALETTE": "P", "RGB": "RGB"}.get(photometric, photometric)
                if photometric == "RGB" and channels == 4:
                    mode = "RGBA"
                if photometric == "MINISBLACK" and channels == 2:
                    mode = "LA"
                digest = None if header_only else _array_hash(array, cancel_event)
                visual_digest = digest
                palette_digest = None
                if page.colormap is not None:
                    palette = _canonical_array(page.colormap).T
                    palette_digest = _array_hash(palette, cancel_event)
                    if not header_only:
                        visual_hash = hashlib.sha256()
                        visual_hash.update(json.dumps({"shape": [height, width, palette.shape[1]], "dtype": palette.dtype.name}).encode())
                        for row in array[:, :, 0]:
                            _check_cancel(cancel_event)
                            if row.size and int(row.max()) >= len(palette):
                                raise ValueError("TIFF 팔레트 인덱스 범위 초과")
                            visual_hash.update(palette[row].tobytes())
                        visual_digest = visual_hash.hexdigest()
                orientation = int(page.tags["Orientation"].value) if "Orientation" in page.tags else 1
                extras = [int(x) for x in page.extrasamples]
                color_info = {}
                for tag_name in ("ICCProfile", "InterColorProfile", "TransferFunction", "WhitePoint", "PrimaryChromaticities"):
                    if tag_name in page.tags:
                        value = page.tags[tag_name].value
                        raw = value if isinstance(value, bytes) else repr(value).encode()
                        color_info["ICCProfile" if tag_name == "InterColorProfile" else tag_name] = hashlib.sha256(raw).hexdigest()
                frame = {"width": width, "height": height, "bit_depth": depth,
                         "channels": channels, "bits_per_pixel": sum(depth) if isinstance(depth, list) else depth * channels,
                         "dtype": dtype_name, "mode": mode, "sample_sha256": digest,
                         "visual_sha256": visual_digest, "palette_sha256": palette_digest,
                         "photometric": photometric, "orientation": orientation,
                         "alpha_type": extras, "color_info": color_info, "page": f"{prefix}{index + 1}"}
                frames.append(frame)
                if len(frames) == 1 and "XResolution" in page.tags and "YResolution" in page.tags:
                    unit = int(page.tags["ResolutionUnit"].value) if "ResolutionUnit" in page.tags else 1
                    x = _rational(page.tags["XResolution"].value)
                    y = _rational(page.tags["YResolution"].value)
                    if x is not None and y is not None and unit in (2, 3):
                        dpi = [x * (2.54 if unit == 3 else 1), y * (2.54 if unit == 3 else 1)]
                del array
                # SubIFDs are image data too (e.g. pyramidal TIFF files).
                if page.subifds:
                    inspect_pages(page.pages, f"{prefix}{index + 1}.")
        inspect_pages(document.pages)
    if not frames:
        raise ValueError("TIFF에 이미지 페이지가 없습니다.")
    return frames, dpi, notices


def _jpeg_precision(stream):
    stream.seek(2)
    sof = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
    while True:
        marker = stream.read(1)
        if not marker:
            raise ValueError("JPEG SOF 헤더가 없습니다.")
        if marker != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker:
            raise ValueError("잘린 JPEG 마커")
        code = marker[0]
        if code in (0xd8, 0x01) or 0xd0 <= code <= 0xd7:
            continue
        if code in (0xda, 0xd9):
            raise ValueError("JPEG 픽셀 정밀도 헤더가 없습니다.")
        raw = stream.read(2)
        if len(raw) != 2:
            raise ValueError("잘린 JPEG 길이")
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            raise ValueError("잘못된 JPEG 세그먼트 길이")
        if code in sof:
            raw = stream.read(6)
            if len(raw) != 6:
                raise ValueError("잘린 JPEG SOF")
            precision, height, width, channels = struct.unpack(">BHHB", raw)
            stream.seek(0)
            return precision, width, height, channels
        stream.seek(length - 2, 1)


def _decode_jpeg(stream, options, cancel_event, header_only=False):
    precision, width, height, channels = _jpeg_precision(stream)
    if precision != 8:
        raise UnsupportedImage(f"{precision}비트 JPEG는 원본 샘플을 보존하는 디코더가 없어 지원하지 않습니다.")
    _check_size(width, height, channels, options)
    _check_cancel(cancel_event)
    with Image.open(stream) as image:
        if image.format != "JPEG":
            raise ValueError("JPEG 시그니처와 디코더 형식 불일치")
        mode = image.mode
        if image.size != (width, height) or len(image.getbands()) != channels:
            raise UnsupportedImage("JPEG 디코딩 결과의 원본 비트수/채널을 확인할 수 없습니다.")
        digest = None
        if header_only:
            current = stream.tell()
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            if b"\xff\xd9" not in stream.read():
                raise ValueError("JPEG 끝 마커(EOI)가 없어 파일이 잘렸을 수 있습니다.")
            stream.seek(current)
        else:
            image.load()
            _check_cancel(cancel_event)
            array = _canonical_array(np.asarray(image))
            if array.dtype != np.uint8:
                raise UnsupportedImage("JPEG 디코더가 원본 8비트 샘플을 보존하지 않았습니다.")
            array = array.reshape(height, width, channels)
            digest = _array_hash(array, cancel_event)
        icc = image.info.get("icc_profile")
        frame = {"width": width, "height": height, "bit_depth": precision,
                 "channels": channels, "bits_per_pixel": precision * channels,
                 "dtype": "uint8", "mode": mode, "sample_sha256": digest,
                 "visual_sha256": digest, "palette_sha256": None,
                 "orientation": int(image.getexif().get(274, 1)),
                 "color_info": {"ICCProfile": hashlib.sha256(icc).hexdigest()} if icc else {}}
        dpi = image.info.get("dpi")
        dpi = [float(value) for value in dpi] if dpi else None
    return [frame], dpi, []


class _DecodeCache:
    """Bounded metadata-only cache. It never retains decoded image arrays."""
    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.items = OrderedDict()
        self.hits = 0
        self.decodes = 0

    def get(self, digest):
        value = self.items.get(digest)
        if value is not None:
            self.items.move_to_end(digest)
            self.hits += 1
            return copy.deepcopy(value)
        return None

    def put(self, digest, metadata):
        if not self.limit:
            return
        local_keys = {"name", "source_path", "archive_member", "byte_size", "sha256", "extension", "extension_matches", "decode_reused"}
        self.items[digest] = copy.deepcopy({key: value for key, value in metadata.items() if key not in local_keys})
        self.items.move_to_end(digest)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)


def _inspect_entry(entry, options, cancel_event, decode_cache=None, header_only=False):
    metadata = {"name": entry.name, "source_path": entry.source_path,
                "archive_member": entry.archive_member, "byte_size": entry.byte_size}
    try:
        with entry.open(options, cancel_event) as stream:
            head = stream.read(16)
            stream.seek(0)
            actual_format = _detect_format(head)
            metadata["format"] = actual_format
            metadata["sha256"] = _hash_stream(stream, cancel_event)
            stream.seek(0)
            expected = {".png": "PNG", ".tif": "TIFF", ".tiff": "TIFF", ".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG"}
            suffix = Path(entry.name).suffix.lower()
            metadata["extension"] = suffix
            metadata["extension_matches"] = expected.get(suffix) == actual_format
            if actual_format not in ("PNG", "TIFF", "JPEG"):
                raise UnsupportedImage(f"지원하지 않는 실제 파일 형식: {actual_format} ({suffix or '확장자 없음'}). PNG/TIFF/JPEG만 지원합니다.")
            cached = decode_cache.get(metadata["sha256"]) if decode_cache is not None and not header_only else None
            if cached is not None:
                metadata.update(cached)
                metadata["decode_reused"] = True
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    if header_only and actual_format == "PNG":
                        frame, dpi, _ = _png_metadata(stream, options, cancel_event)
                        frames, notices = [frame], []
                    elif actual_format == "TIFF":
                        frames, dpi, notices = _decode_tiff(stream, options, cancel_event, header_only=header_only)
                    elif actual_format == "JPEG":
                        frames, dpi, notices = _decode_jpeg(stream, options, cancel_event, header_only=header_only)
                    else:
                        frames, dpi, notices = _decode_png(stream, options, cancel_event)
                first = frames[0]
                for key in ("width", "height", "bit_depth", "channels", "bits_per_pixel", "mode", "dtype"):
                    metadata[key] = first[key]
                metadata.update(frame_count=len(frames), frames=frames, dpi=dpi, warnings=notices,
                                decode_validation="header_only" if header_only else "full",
                                uniform_frames=all(all(frame[key] == first[key] for key in
                                                   ("width", "height", "bit_depth", "channels", "mode", "dtype")) for frame in frames))
        # Entering/exiting entry.open also checks stamps for reused byte content.
        # A failed or incomplete decode is never inserted into this cache.
        if decode_cache is not None and not header_only and not metadata.get("decode_reused"):
            decode_cache.decodes += 1
            decode_cache.put(metadata["sha256"], metadata)
        return metadata, None, ""
    except ComparisonCancelled:
        raise
    except UnsupportedImage as exc:
        return metadata, "unsupported", str(exc)
    except Exception as exc:
        return metadata, "error", f"{type(exc).__name__}: {exc}"


def _compare_metadata(left, right):
    differences = []
    if left["format"] != right["format"]:
        differences.append("format")
    if left["frame_count"] != right["frame_count"]:
        differences.append("frame_count")
    lf, rf = left["frames"], right["frames"]
    pixels_equal = len(lf) == len(rf)
    same_validated_bytes = left.get("sha256") == right.get("sha256") and left.get("decode_validation") == right.get("decode_validation") == "header_only"
    keys = ("width", "height", "bit_depth", "channels", "bits_per_pixel", "dtype", "mode", "palette_sha256",
            "orientation", "color_info", "transparency")
    for index, (a, b) in enumerate(zip(lf, rf)):
        for key in keys:
            # Decoder-specific keys use defaults when they have no semantic meaning.
            if a.get(key) != b.get(key):
                code = key if len(lf) == len(rf) == 1 else f"frame_{index + 1}:{key}"
                if code not in differences:
                    differences.append(code)
        same_samples = same_validated_bytes or (a.get("sample_sha256") is not None and a.get("sample_sha256") == b.get("sample_sha256"))
        same_visual = same_validated_bytes or (a.get("visual_sha256") is not None and a.get("visual_sha256") == b.get("visual_sha256"))
        def interpretation(frame):
            # PNG's RGBA/LA alpha is unassociated; TIFF writes that meaning as
            # ExtraSamples=2. Absent container-specific tags do not change RGB/L.
            alpha = tuple(frame.get("alpha_type") or ())
            if not alpha and frame.get("mode") in ("RGBA", "LA"):
                alpha = (2,)
            return (frame.get("mode"), frame.get("bit_depth"), frame.get("orientation", 1),
                    frame.get("color_info") or {}, frame.get("transparency"), alpha)
        same_interpretation = interpretation(a) == interpretation(b)
        if not same_interpretation:
            differences.append("color_interpretation" if len(lf) == len(rf) == 1 else f"frame_{index + 1}:color_interpretation")
        if not (same_samples and same_visual and same_interpretation):
            pixels_equal = False
            differences.append("pixels" if len(lf) == len(rf) == 1 else f"frame_{index + 1}:pixels")
    if left["sha256"] != right["sha256"]:
        differences.append("file_bytes")
    if left.get("extension") != right.get("extension"):
        differences.append("extension")
    # Wrong extensions are exposed in metadata. Equal bundles can share the
    # same wrong label; the actual image format is compared independently.
    return differences, pixels_equal


def compare_sources(left_spec, right_spec, options=None, progress=None, cancel_event=None):
    """Compare all files, continuing after corrupt and unsupported entries.

    ``progress(percent, message)`` is called from the caller's thread. GUI callers
    should invoke this function in a worker and marshal the callback to the UI.
    A cancellation returns an explicitly incomplete report; it never passes.
    """
    options = options or CompareOptions()
    started = time.perf_counter()
    if options.match_mode not in ("path", "sequence"):
        raise ValueError("match_mode는 'path' 또는 'sequence'여야 합니다.")
    if options.png_decoder not in ("auto", "python"):
        raise ValueError("png_decoder는 'auto' 또는 'python'이어야 합니다.")
    if not isinstance(left_spec, SourceSpec):
        left_spec = SourceSpec.from_paths(left_spec)
    if not isinstance(right_spec, SourceSpec):
        right_spec = SourceSpec.from_paths(right_spec)
    report = Report(left_source=left_spec.label, right_source=right_spec.label, match_mode=options.match_mode)
    archive_pool = _ArchivePool()
    decode_cache = _DecodeCache(options.decode_cache_size)
    def notify(percent, message):
        if progress:
            progress(int(percent), message)
    try:
        notify(0, "입력 묶음을 확인하는 중입니다.")
        left_entries, left_errors, left_notices = _scan_source(left_spec, options, cancel_event, archive_pool)
        right_entries, right_errors, right_notices = _scan_source(right_spec, options, cancel_event, archive_pool)
        report.warnings.extend(["A: " + note for note in left_notices] + ["B: " + note for note in right_notices])
        for message in left_errors:
            report.rows.append(ComparisonRow(left_name="입력 오류", error="A: " + message))
        for message in right_errors:
            report.rows.append(ComparisonRow(right_name="입력 오류", error="B: " + message))
        if options.match_mode == "path":
            left_map = {entry.name: entry for entry in left_entries}
            right_map = {entry.name: entry for entry in right_entries}
            pairs = [(left_map.get(name), right_map.get(name)) for name in
                     sorted(left_map.keys() | right_map.keys(), key=lambda name: (natural_key(name), name))]
        else:
            from itertools import zip_longest
            # Auxiliary files are deliberately visible as unsupported rows and
            # should not shift the natural image layer sequence.
            image_suffixes = {".png", ".tif", ".tiff", ".jpg", ".jpeg", ".jpe"}
            def sequence_groups(entries):
                images, other = [], []
                for entry in entries:
                    _check_cancel(cancel_event)
                    try:
                        is_image = _detect_format(entry.peek(cancel_event)) in ("PNG", "TIFF", "JPEG")
                    except ComparisonCancelled:
                        raise
                    except Exception:
                        # Keep an unreadable image in its expected sequence
                        # position; its actual error is retained during inspection.
                        is_image = False
                    is_image = is_image or Path(entry.name).suffix.lower() in image_suffixes
                    (images if is_image else other).append(entry)
                return images, other
            left_images, left_other = sequence_groups(left_entries)
            right_images, right_other = sequence_groups(right_entries)
            pairs = list(zip_longest(left_images, right_images)) + list(zip_longest(left_other, right_other))
        total_files = sum(a is not None for a, b in pairs) + sum(b is not None for a, b in pairs)
        done = 0
        for left_entry, right_entry in pairs:
            _check_cancel(cancel_event)
            row = ComparisonRow(left_name=left_entry.name if left_entry else None,
                                right_name=right_entry.name if right_entry else None)
            states = []
            errors = []
            for side, entry in (("left", left_entry), ("right", right_entry)):
                if entry is None:
                    continue
                notify(5 + int(90 * done / max(total_files, 1)), f"{'A' if side == 'left' else 'B'}: {entry.name}")
                metadata, state, error = _inspect_entry(entry, options, cancel_event, decode_cache,
                                                        header_only=not options.verify_identical)
                setattr(row, side, metadata)
                done += 1
                if state:
                    states.append(state)
                    errors.append(f"{'A' if side == 'left' else 'B'}: {error}")
            # Equal bytes prove pair consistency without expanding an image.
            # With the explicit fast option, decode only genuinely different
            # paired files. Unmatched files still receive complete header/CRC
            # validation and their actual metadata is retained in the report.
            if (not options.verify_identical and not states and left_entry is not None and right_entry is not None
                    and row.left["sha256"] != row.right["sha256"]):
                for side, entry in (("left", left_entry), ("right", right_entry)):
                    _check_cancel(cancel_event)
                    notify(5 + int(90 * done / max(total_files, 1)), f"정밀 픽셀 비교 {'A' if side == 'left' else 'B'}: {entry.name}")
                    metadata, state, error = _inspect_entry(entry, options, cancel_event, decode_cache)
                    setattr(row, side, metadata)
                    if state:
                        states.append(state)
                        errors.append(f"{'A' if side == 'left' else 'B'}: {error}")
            if states:
                row.status = "error" if "error" in states else "unsupported"
                row.error = " | ".join(errors)
            elif left_entry is None:
                row.status = "right_only"
                row.differences = ["missing_left"]
            elif right_entry is None:
                row.status = "left_only"
                row.differences = ["missing_right"]
            else:
                row.differences, row.pixels_equal = _compare_metadata(row.left, row.right)
                row.pixel_comparison = "identical_file_bytes" if row.left.get("decode_validation") == row.right.get("decode_validation") == "header_only" else "decoded_samples"
                row.status = "different" if row.differences else "identical"
            report.rows.append(row)
        # Recheck every source so a mutation after a previously compared pair is
        # detected as well; previously captured metadata alone is not a pass.
        checked = set()
        for entry in left_entries + right_entries:
            _check_cancel(cancel_event)
            if entry.source_path in checked:
                continue
            checked.add(entry.source_path)
            try:
                if _stamp(entry.source_path) != entry.stamp:
                    raise ValueError("검사 도중 입력 파일이 변경되었습니다.")
            except (OSError, ValueError) as exc:
                report.rows.append(ComparisonRow(left_name=entry.name, error=f"{entry.source_path}: {exc}"))
        header_only_files = sum((metadata or {}).get("decode_validation") == "header_only"
                                for row in report.rows for metadata in (row.left, row.right))
        if header_only_files:
            report.warnings.append(_FAST_VALIDATION_WARNING)
        notify(100, "검사가 완료되었습니다.")
    except ComparisonCancelled:
        report.cancelled = True
        report.warnings.append("검사가 취소되었습니다. 결과는 완료된 파일에 한정됩니다.")
        notify(0, "검사가 취소되었습니다.")
    finally:
        archive_pool.close()
        report.elapsed_seconds = round(time.perf_counter() - started, 6)
        report.performance = {"full_decodes": decode_cache.decodes, "decoded_cache_hits": decode_cache.hits,
                              "header_only_files": sum((metadata or {}).get("decode_validation") == "header_only"
                                                       for row in report.rows for metadata in (row.left, row.right))}
        if report.performance["header_only_files"] and _FAST_VALIDATION_WARNING not in report.warnings:
            report.warnings.append(_FAST_VALIDATION_WARNING)
    return report
