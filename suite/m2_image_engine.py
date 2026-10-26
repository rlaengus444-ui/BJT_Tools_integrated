"""M2 ImageConverter's Windows GDI+ pipeline, with safe batch publishing.

The source utilities use GDI+ DrawImage -> 4bpp indexed Clone -> palette
inversion -> LZW TIFF.  Using those same native operations preserves their
palette and resampling, including transparent inputs.  No .NET runtime or
external converter executable is required.
"""
from __future__ import annotations

import ctypes as C
from dataclasses import asdict, dataclass, field
import math
import numbers
import os
from pathlib import Path
import tempfile
import time
from types import MappingProxyType
import uuid


@dataclass(frozen=True)
class ModeSpec:
    width: int
    height: int
    dpi_x: float
    dpi_y: float
    label: str


MODE_SPECS = MappingProxyType({
    "single": ModeSpec(8504, 6614, 400, 400, "싱글"),
    "multi": ModeSpec(8504, 13228, 400, 800, "멀티"),
})
SUPPORTED_EXTENSIONS = frozenset((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"))


@dataclass(frozen=True)
class ConversionJob:
    input_paths: tuple
    output_dir: str
    mode: str = "single"
    start_number: int = 1
    zero_pad: int = 3
    conflict: str = "skip"
    width: int | None = None
    height: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None


@dataclass
class ConversionReport:
    mode: str
    total: int
    output_dir: str
    completed: bool = False
    cancelled: bool = False
    converted: int = 0
    skipped: int = 0
    failed: int = 0
    error: str | None = None
    items: list = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self):
        return asdict(self)


class ConversionCancelled(Exception):
    pass


def _check_cancel(event):
    if event is not None and event.is_set():
        raise ConversionCancelled()


def _identity(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _same_file(left, right):
    if _identity(left) == _identity(right):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def resolve_spec(job):
    """Resolve optional pixel/DPI overrides without changing preset defaults.

    GDI+ receives signed 32-bit pixel sizes and single-precision DPI values.
    Reject values that would wrap, overflow or become zero at that boundary;
    use the representable DPI for the subsequent TIFF-directory check.
    """
    if not isinstance(job, ConversionJob):
        raise ValueError("ConversionJob 설정이 필요합니다.")
    if job.mode not in MODE_SPECS:
        raise ValueError("변환 모드는 single 또는 multi여야 합니다.")
    base = MODE_SPECS[job.mode]
    dimensions = []
    for key, label in (("width", "가로 픽셀"), ("height", "세로 픽셀")):
        value = getattr(job, key)
        if value is None:
            value = getattr(base, key)
        if (isinstance(value, bool) or not isinstance(value, numbers.Integral)
                or not 1 <= value <= 2_147_483_647):
            raise ValueError(label + "은 1~2,147,483,647의 정수여야 합니다.")
        dimensions.append(int(value))
    resolutions = []
    for key, label in (("dpi_x", "X DPI"), ("dpi_y", "Y DPI")):
        value = getattr(job, key)
        if value is None:
            value = getattr(base, key)
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError(label + "는 0보다 큰 유한한 숫자여야 합니다.")
        try:
            value = float(value)
            native_value = C.c_float(value).value
        except (OverflowError, TypeError, ValueError):
            raise ValueError(label + "를 Windows 이미지 형식으로 표현할 수 없습니다.") from None
        if not math.isfinite(value) or not math.isfinite(native_value) or native_value <= 0:
            raise ValueError(label + "는 Windows에서 표현할 수 있는 0보다 큰 유한한 숫자여야 합니다.")
        resolutions.append(native_value)
    return ModeSpec(*dimensions, *resolutions, base.label)


def _preflight(job):
    if not isinstance(job, ConversionJob):
        raise ValueError("ConversionJob 설정이 필요합니다.")
    if job.mode not in MODE_SPECS:
        raise ValueError("변환 모드는 single 또는 multi여야 합니다.")
    resolve_spec(job)
    if (isinstance(job.start_number, bool) or not isinstance(job.start_number, numbers.Integral)
            or job.start_number < 1):
        raise ValueError("시작 번호는 1 이상의 정수여야 합니다.")
    if (isinstance(job.zero_pad, bool) or not isinstance(job.zero_pad, numbers.Integral)
            or not 0 <= job.zero_pad <= 12):
        raise ValueError("번호 자릿수는 0~12의 정수여야 합니다.")
    if job.conflict not in ("skip", "overwrite"):
        raise ValueError("파일 충돌 처리는 skip 또는 overwrite여야 합니다.")
    if isinstance(job.input_paths, (str, bytes, os.PathLike)):
        raise ValueError("입력 이미지를 목록으로 지정하세요.")
    paths = tuple(Path(p).expanduser().absolute() for p in job.input_paths)
    if not paths:
        raise ValueError("변환할 이미지를 추가하세요.")
    for path in paths:
        if not path.is_file():
            raise ValueError("입력 파일을 찾을 수 없습니다: " + str(path))
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("지원하지 않는 이미지 형식입니다: " + str(path))
    if not str(job.output_dir).strip():
        raise ValueError("출력 폴더를 지정하세요.")
    output = Path(job.output_dir).expanduser().absolute()
    if output.exists() and not output.is_dir():
        raise ValueError("출력 경로가 폴더가 아닙니다: " + str(output))
    targets = tuple(output / (str(int(job.start_number) + i).zfill(int(job.zero_pad)) + ".tif")
                    for i in range(len(paths)))
    # Check *every* source before doing any work; an early output may otherwise
    # overwrite a later input. samefile also detects hard links and junctions.
    if job.conflict == "overwrite":
        source_names = {_identity(path) for path in paths}
        source_inodes = set()
        for path in paths:
            st = path.stat()
            source_inodes.add((st.st_dev, st.st_ino))
        for target in targets:
            collision = _identity(target) in source_names
            if not collision and target.exists():
                st = target.stat()
                collision = (st.st_dev, st.st_ino) in source_inodes
            if collision:
                raise ValueError("출력이 원본 입력과 겹칩니다. 다른 출력 폴더를 선택하세요: " + str(target))
    return paths, output, targets


class _GUID(C.Structure):
    _fields_ = [("data", C.c_ubyte * 16)]

    @classmethod
    def from_text(cls, value):
        return cls((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


class _StartupInput(C.Structure):
    _fields_ = [("version", C.c_uint32), ("debug_callback", C.c_void_p),
                ("suppress_background", C.c_int32), ("suppress_external", C.c_int32)]


class _EncoderParameter(C.Structure):
    _fields_ = [("guid", _GUID), ("number", C.c_uint32),
                ("type", C.c_uint32), ("value", C.c_void_p)]


class _EncoderParameters(C.Structure):
    _fields_ = [("count", C.c_uint32), ("parameter", _EncoderParameter * 1)]


class _GDIPlus:
    PF32_ARGB = 0x26200A
    PF4_INDEXED = 0x30402

    def __init__(self):
        if os.name != "nt":
            raise OSError("이미지 변환에는 Windows GDI+가 필요합니다.")
        self.dll = C.WinDLL("gdiplus.dll")
        ptr = C.c_void_p
        uint = C.c_uint32
        integer = C.c_int32
        signatures = {
            "GdiplusStartup": ([C.POINTER(C.c_size_t), C.POINTER(_StartupInput), ptr], integer),
            "GdiplusShutdown": ([C.c_size_t], None),
            "GdipCreateBitmapFromFile": ([C.c_wchar_p, C.POINTER(ptr)], integer),
            "GdipCreateBitmapFromScan0": ([integer, integer, integer, integer, ptr, C.POINTER(ptr)], integer),
            "GdipBitmapSetResolution": ([ptr, C.c_float, C.c_float], integer),
            "GdipGetImageGraphicsContext": ([ptr, C.POINTER(ptr)], integer),
            "GdipDrawImageRectI": ([ptr, ptr, integer, integer, integer, integer], integer),
            "GdipDeleteGraphics": ([ptr], integer),
            "GdipCloneBitmapAreaI": ([integer, integer, integer, integer, integer, ptr, C.POINTER(ptr)], integer),
            "GdipDisposeImage": ([ptr], integer),
            "GdipGetImagePixelFormat": ([ptr, C.POINTER(integer)], integer),
            "GdipGetImagePaletteSize": ([ptr, C.POINTER(integer)], integer),
            "GdipGetImagePalette": ([ptr, ptr, integer], integer),
            "GdipSetImagePalette": ([ptr, ptr], integer),
            "GdipSaveImageToFile": ([ptr, C.c_wchar_p, C.POINTER(_GUID), C.POINTER(_EncoderParameters)], integer),
        }
        for name, (args, restype) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, restype
        self.token = C.c_size_t()
        self._call("GdiplusStartup", C.byref(self.token), C.byref(_StartupInput(1, None, 0, 0)), None)

    def _call(self, name, *args):
        status = getattr(self.dll, name)(*args)
        if status:
            labels = {1: "일반 오류", 2: "잘못된 인수", 3: "메모리 부족 또는 지원하지 않는 이미지",
                      4: "객체 사용 중", 5: "버퍼 부족", 7: "Windows 오류", 10: "파일을 찾을 수 없음",
                      13: "지원하지 않는 이미지 형식", 18: "GDI+ 초기화 오류", 20: "속성을 찾을 수 없음"}
            raise OSError(f"GDI+ {name}: {labels.get(status, '변환 오류')} (코드 {status})")

    def close(self):
        if self.token.value:
            self.dll.GdiplusShutdown(self.token)
            self.token.value = 0

    def convert(self, source, destination, spec, cancel_event=None, progress=None):
        original, resized, indexed, graphics = (C.c_void_p() for _ in range(4))

        def stage(name, message):
            _check_cancel(cancel_event)
            if progress:
                progress(name, message)

        try:
            stage("decode", "이미지 읽는 중")
            self._call("GdipCreateBitmapFromFile", str(source), C.byref(original))
            self._call("GdipCreateBitmapFromScan0", spec.width, spec.height, 0,
                       self.PF32_ARGB, None, C.byref(resized))
            self._call("GdipBitmapSetResolution", resized, spec.dpi_x, spec.dpi_y)
            self._call("GdipGetImageGraphicsContext", resized, C.byref(graphics))
            stage("resize", "원본 방식으로 해상도 변환 중")
            self._call("GdipDrawImageRectI", graphics, original, 0, 0, spec.width, spec.height)
            self._call("GdipDeleteGraphics", graphics)
            graphics.value = None
            stage("palette", "4비트 팔레트 변환 및 색상 반전 중")
            self._call("GdipCloneBitmapAreaI", 0, 0, spec.width, spec.height, self.PF4_INDEXED,
                       resized, C.byref(indexed))
            # The originals dispose the intermediate bitmap before inversion.
            self._call("GdipDisposeImage", resized)
            resized.value = None
            self._call("GdipDisposeImage", original)
            original.value = None
            self._call("GdipBitmapSetResolution", indexed, spec.dpi_x, spec.dpi_y)
            pixel_format = C.c_int32()
            self._call("GdipGetImagePixelFormat", indexed, C.byref(pixel_format))
            if pixel_format.value != self.PF4_INDEXED:
                raise OSError("Windows가 4비트 팔레트 변환을 지원하지 않아 저장을 중단했습니다.")
            palette_size = C.c_int32()
            self._call("GdipGetImagePaletteSize", indexed, C.byref(palette_size))
            if not 8 <= palette_size.value <= 4096:
                raise OSError("GDI+ 팔레트 크기가 올바르지 않습니다.")
            palette = C.create_string_buffer(palette_size.value)
            self._call("GdipGetImagePalette", indexed, palette, palette_size.value)
            count = C.c_uint32.from_buffer(palette, 4).value
            if count != 16 or 8 + count * 4 > palette_size.value:
                raise OSError("4비트 16색 팔레트를 확인하지 못했습니다.")
            entries = (C.c_uint32 * count).from_buffer(palette, 8)
            for i in range(count):
                entries[i] = 0xFF000000 | (~entries[i] & 0x00FFFFFF)
            self._call("GdipSetImagePalette", indexed, palette)
            stage("save", "LZW TIFF 저장 중")
            lzw_value = C.c_uint32(2)
            compression = _GUID.from_text("e09d739d-ccd4-44ee-8eba-3fbf8be4fc58")
            parameter = _EncoderParameter(compression, 1, 4, C.cast(C.byref(lzw_value), C.c_void_p))
            parameters = _EncoderParameters(1, (_EncoderParameter * 1)(parameter))
            tiff_encoder = _GUID.from_text("557cf405-1a04-11d3-9a73-0000f81ef32e")
            self._call("GdipSaveImageToFile", indexed, str(destination), C.byref(tiff_encoder), C.byref(parameters))
            _check_cancel(cancel_event)
        finally:
            if graphics.value:
                self.dll.GdipDeleteGraphics(graphics)
            for bitmap in (indexed, resized, original):
                if bitmap.value:
                    self.dll.GdipDisposeImage(bitmap)


def inspect_output(path, spec):
    """Read TIFF directory tags without decoding the large pixel array."""
    from PIL import Image
    with Image.open(path) as image:
        tags = image.tag_v2
        bits = tags.get(258)
        bits = tuple(bits) if isinstance(bits, (tuple, list)) else (bits,)
        metadata = {
            "format": image.format, "width": image.width, "height": image.height,
            "bits_per_sample": list(bits), "bits_per_pixel": sum(bits),
            "samples_per_pixel": int(tags.get(277, 1)), "compression": int(tags.get(259, 1)),
            "photometric": int(tags.get(262, -1)),
            "dpi_x": float(tags.get(282, 0)), "dpi_y": float(tags.get(283, 0)),
            "resolution_unit": int(tags.get(296, 0)), "frames": image.n_frames,
        }
    if (metadata["format"] != "TIFF" or (metadata["width"], metadata["height"]) != (spec.width, spec.height)
            or bits != (4,) or metadata["samples_per_pixel"] != 1 or metadata["compression"] != 5
            or metadata["photometric"] != 3 or metadata["resolution_unit"] != 2
            or metadata["frames"] != 1 or metadata["dpi_x"] <= 0 or metadata["dpi_y"] <= 0
            or not math.isclose(metadata["dpi_x"], spec.dpi_x, rel_tol=1e-6)
            or not math.isclose(metadata["dpi_y"], spec.dpi_y, rel_tol=1e-6)):
        raise OSError("출력 TIFF의 해상도·DPI·4비트 팔레트·LZW 형식 검사에 실패했습니다.")
    return metadata


def convert_images(job, cancel_event=None, progress=None):
    """Convert sequentially; publish only completely saved, verified images.

    A first file failure stops the batch, matching the source utility. Already
    completed outputs remain. Cancellation discards only the active temp file.
    """
    started = time.perf_counter()
    paths, output, targets = _preflight(job)
    report = ConversionReport(job.mode, len(paths), str(output))
    native = None
    spec = resolve_spec(job)
    try:
        _check_cancel(cancel_event)
        output.mkdir(parents=True, exist_ok=True)
        for index, (source, target) in enumerate(zip(paths, targets), 1):
            temp_path = None
            item = {"index": index, "source": str(source), "target": str(target)}

            def emit(stage, message):
                if progress:
                    progress({**item, "stage": stage, "total": len(paths), "message": message})

            try:
                _check_cancel(cancel_event)
                if job.conflict == "skip" and target.exists():
                    report.skipped += 1
                    report.items.append({**item, "status": "skipped", "message": "같은 이름의 파일이 있어 건너뛰었습니다."})
                    emit("skipped", "기존 파일 건너뜀")
                    continue
                if native is None:
                    native = _GDIPlus()
                fd, temp_name = tempfile.mkstemp(prefix=".bjt_m2_", suffix=".tif", dir=output)
                os.close(fd)
                temp_path = Path(temp_name)
                native.convert(source, temp_path, spec, cancel_event, emit)
                emit("validate", "출력 해상도·DPI·비트 수·파일 형식 확인 중")
                metadata = inspect_output(temp_path, spec)
                with temp_path.open("rb+") as saved:
                    os.fsync(saved.fileno())
                _check_cancel(cancel_event)
                if job.conflict == "overwrite":
                    # Recheck against source aliases created while conversion ran.
                    if any(_same_file(target, path) for path in paths):
                        raise ValueError("출력이 원본 입력과 겹쳐 저장하지 않았습니다: " + str(target))
                    os.replace(temp_path, target)
                else:
                    # Windows rename never replaces an existing target, including
                    # a file created between the initial check and publication.
                    try:
                        os.rename(temp_path, target)
                    except FileExistsError:
                        report.skipped += 1
                        report.items.append({**item, "status": "skipped", "message": "처리 중 같은 이름의 파일이 생성되어 건너뛰었습니다."})
                        emit("skipped", "기존 파일 건너뜀")
                        continue
                temp_path = None
                report.converted += 1
                report.items.append({**item, "status": "converted", "message": "변환 및 형식 검사 완료", "metadata": metadata})
                emit("converted", "변환 완료")
            except ConversionCancelled:
                raise
            except Exception as exc:
                report.failed += 1
                report.error = str(exc)
                report.items.append({**item, "status": "failed", "message": str(exc)})
                emit("failed", str(exc))
                break
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        report.completed = report.failed == 0 and report.converted + report.skipped == report.total
    except ConversionCancelled:
        report.cancelled = True
    finally:
        if native is not None:
            native.close()
        report.elapsed_seconds = time.perf_counter() - started
    return report
