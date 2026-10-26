"""Correctness tests for slice comparison, including image precision edge cases.

Run from the application directory with ``python -m unittest discover -s tests -v``.
All fixtures are generated in an isolated temporary directory.
"""

from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import warnings
import zipfile
import zlib

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slice_compare_engine import CompareOptions, SourceSpec, compare_sources
import slice_compare_engine as engine_module


def write_png(path, pixels, *, width=2, height=1, bit_depth=8, channels=1):
    """Write exact PNG samples without a library downconverting RGB16 fixtures."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    if len(pixels) != width * height * channels:
        raise ValueError("Fixture sample count does not match its dimensions")

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = []
    stride = width * channels
    for row in range(height):
        samples = pixels[row * stride : (row + 1) * stride]
        payload = (
            struct.pack(">" + "H" * len(samples), *samples)
            if bit_depth == 16
            else bytes(samples)
        )
        rows.append(b"\x00" + payload)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )
    return path


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="slice_compare_tests_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.left = self.root / "left"
        self.right = self.root / "right"
        self.left.mkdir()
        self.right.mkdir()

    def compare(self, *, left=None, right=None, mode="path", **kwargs):
        options = kwargs.pop("options", None) or CompareOptions(match_mode=mode)
        return compare_sources(
            SourceSpec.from_paths([str(left or self.left)]),
            SourceSpec.from_paths([str(right or self.right)]),
            options,
            **kwargs,
        )

    def pair(self, values=(1, 2), *, name="slice.png", **kwargs):
        write_png(self.left / name, list(values), **kwargs)
        write_png(self.right / name, list(values), **kwargs)

    def only_row(self, report):
        self.assertEqual(len(report.rows), 1, report.to_dict())
        return report.rows[0]

    def assertDifferentPixels(self, report):
        row = self.only_row(report)
        self.assertEqual(row.status, "different", row.to_dict())
        self.assertIs(row.pixels_equal, False, row.to_dict())
        self.assertFalse(report.overall_identical)
        return row

    def test_identical_image_and_bytes(self):
        self.pair()
        report = self.compare()
        row = self.only_row(report)
        self.assertEqual(row.status, "identical")
        self.assertIs(row.pixels_equal, True)
        self.assertTrue(report.overall_identical)
        self.assertEqual(report.summary["identical"], 1)
        self.assertEqual(row.left["format"], "PNG")
        self.assertEqual(row.left["width"], 2)
        self.assertEqual(row.left["height"], 1)
        self.assertEqual(row.left["bit_depth"], 8)

    def test_metadata_only_difference_preserves_pixel_equality(self):
        image = Image.new("RGB", (2, 2), (10, 20, 30))
        image.save(self.left / "slice.png")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "Identical pixels, additional text metadata")
        image.save(self.right / "slice.png", pnginfo=metadata)
        row = self.only_row(self.compare())
        self.assertEqual(row.status, "different")
        self.assertIs(row.pixels_equal, True)
        self.assertIn("file_bytes", row.differences)

    def test_pixel_difference(self):
        write_png(self.left / "slice.png", [1, 2])
        write_png(self.right / "slice.png", [1, 3])
        self.assertDifferentPixels(self.compare())

    def test_gray16_difference_in_low_byte(self):
        write_png(self.left / "slice.png", [0x1200, 0xABCD], bit_depth=16)
        write_png(self.right / "slice.png", [0x1201, 0xABCD], bit_depth=16)
        row = self.assertDifferentPixels(self.compare())
        self.assertEqual(row.left["bit_depth"], 16)
        self.assertEqual(row.right["bit_depth"], 16)

    def test_rgb16_difference_in_low_byte(self):
        values = [0x1200, 0x3400, 0x5600, 0x789A, 0xBCDE, 0xF012]
        changed = values.copy()
        changed[1] += 1
        write_png(self.left / "slice.png", values, bit_depth=16, channels=3)
        write_png(self.right / "slice.png", changed, bit_depth=16, channels=3)
        row = self.assertDifferentPixels(self.compare())
        self.assertEqual(row.left["bit_depth"], 16)
        self.assertEqual(row.left["channels"], 3)
        self.assertEqual(row.left["bits_per_pixel"], 48)

    def test_resolution_difference(self):
        write_png(self.left / "slice.png", [1, 2], width=2, height=1)
        write_png(self.right / "slice.png", [1, 2], width=1, height=2)
        row = self.only_row(self.compare())
        self.assertEqual(row.status, "different")
        self.assertNotEqual((row.left["width"], row.left["height"]), (row.right["width"], row.right["height"]))

    def test_bit_depth_difference(self):
        write_png(self.left / "slice.png", [1, 2], bit_depth=8)
        write_png(self.right / "slice.png", [1, 2], bit_depth=16)
        row = self.only_row(self.compare())
        self.assertEqual(row.status, "different")
        self.assertEqual(row.left["bit_depth"], 8)
        self.assertEqual(row.right["bit_depth"], 16)

    def test_subbyte_grayscale_depth_changes_pixel_interpretation(self):
        import png

        with (self.left / "slice.png").open("wb") as stream:
            png.Writer(width=2, height=1, greyscale=True, bitdepth=2).write(stream, [[0, 1]])
        write_png(self.right / "slice.png", [0, 1], bit_depth=8)
        row = self.assertDifferentPixels(self.compare())
        self.assertEqual(row.left["bit_depth"], 2)
        self.assertEqual(row.right["bit_depth"], 8)
        self.assertIn("bit_depth", row.differences)

    def test_actual_format_is_read_from_content(self):
        # Both filenames claim JPEG; the actual bytes are a valid PNG.
        self.pair(name="misnamed.jpg")
        row = self.only_row(self.compare())
        self.assertEqual(row.left["format"], "PNG")
        self.assertEqual(row.right["format"], "PNG")
        self.assertEqual(row.status, "identical")

    def test_unicode_filename_without_extension(self):
        # A Unicode digit is not necessarily accepted by int() during sorting.
        self.pair(name="layer2²")
        report = self.compare()
        self.assertEqual(self.only_row(report).status, "identical")
        self.assertTrue(report.overall_identical)

    def test_actual_format_difference_with_same_extension(self):
        image = Image.new("L", (4, 3), 0)
        image.save(self.left / "slice.png", format="PNG")
        image.save(self.right / "slice.png", format="JPEG", quality=100)
        row = self.only_row(self.compare())
        self.assertEqual(row.status, "different")
        self.assertEqual(row.left["format"], "PNG")
        self.assertEqual(row.right["format"], "JPEG")
        self.assertIs(row.pixels_equal, True)

    def test_png_tiff_grayscale8_same_samples_have_equal_pixels(self):
        image = Image.new("L", (2, 1))
        image.putdata([1, 200])
        image.save(self.left / "slice.png", format="PNG")
        image.save(self.right / "slice.tif", format="TIFF")
        row = self.only_row(self.compare(mode="sequence"))
        self.assertEqual(row.status, "different")
        self.assertEqual(row.left["format"], "PNG")
        self.assertEqual(row.right["format"], "TIFF")
        self.assertIn("format", row.differences)
        self.assertIs(row.pixels_equal, True, row.to_dict())

    def test_png_tiff_rgb8_same_samples_have_equal_pixels(self):
        image = Image.new("RGB", (2, 1))
        image.putdata([(12, 34, 56), (78, 90, 123)])
        image.save(self.left / "slice.png", format="PNG")
        image.save(self.right / "slice.tif", format="TIFF")
        row = self.only_row(self.compare(mode="sequence"))
        self.assertEqual(row.status, "different")
        self.assertEqual(row.left["format"], "PNG")
        self.assertEqual(row.right["format"], "TIFF")
        self.assertIn("format", row.differences)
        self.assertIs(row.pixels_equal, True, row.to_dict())

    def test_palette_color_difference_even_when_indices_match(self):
        image = Image.new("P", (2, 1), 0)
        image.putpalette([255, 0, 0] + [0] * 765)
        image.save(self.left / "slice.png")
        image.putpalette([0, 255, 0] + [0] * 765)
        image.save(self.right / "slice.png")
        self.assertDifferentPixels(self.compare())

    def test_palette_transparency_difference(self):
        image = Image.new("P", (2, 1), 0)
        image.putpalette([255, 0, 0] + [0] * 765)
        image.save(self.left / "slice.png", transparency=255)
        image.save(self.right / "slice.png", transparency=0)
        self.assertDifferentPixels(self.compare())

    def test_missing_file_is_reported(self):
        self.pair()
        write_png(self.left / "extra.png", [3, 4])
        report = self.compare()
        self.assertEqual(report.summary["identical"], 1)
        self.assertEqual(report.summary["left_only"], 1)
        self.assertFalse(report.overall_identical)
        self.assertTrue(any(row.left_name.endswith("extra.png") and row.status == "left_only" for row in report.rows))

    def test_corrupt_equal_bytes_are_an_error(self):
        for directory in (self.left, self.right):
            (directory / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        report = self.compare()
        row = self.only_row(report)
        self.assertEqual(row.status, "error", row.to_dict())
        self.assertFalse(report.overall_identical)
        self.assertTrue(row.error)

    def test_unsupported_equal_files_are_not_identical(self):
        for directory in (self.left, self.right):
            (directory / "settings.txt").write_text("a=1", encoding="utf-8")
        report = self.compare()
        self.assertEqual(self.only_row(report).status, "unsupported")
        self.assertFalse(report.overall_identical)

    def test_empty_collections_are_not_identical(self):
        report = self.compare()
        self.assertFalse(report.overall_identical)
        self.assertGreater(report.summary["error"], 0)

    def test_zip_common_wrapper_matches_directory(self):
        self.pair(name="layers/slice002.png")
        archive_path = self.root / "right.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(self.right / "layers/slice002.png", "export/layers/slice002.png")
        report = self.compare(right=archive_path)
        self.assertEqual(self.only_row(report).status, "identical", report.to_dict())
        self.assertTrue(report.overall_identical)

    def test_duplicate_zip_member_cannot_be_silently_overwritten(self):
        self.pair()
        archive_path = self.root / "duplicate.zip"
        data = (self.right / "slice.png").read_bytes()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("slice.png", data)
                archive.writestr("slice.png", data)
        report = self.compare(right=archive_path)
        self.assertFalse(report.overall_identical)
        self.assertTrue(any(row.status == "error" for row in report.rows), report.to_dict())

    def test_sequence_matching_uses_natural_numbers(self):
        write_png(self.left / "layer2.png", [2, 2])
        write_png(self.left / "layer10.png", [10, 10])
        write_png(self.right / "slice001.png", [2, 2])
        write_png(self.right / "slice002.png", [10, 10])
        report = self.compare(mode="sequence")
        self.assertEqual(report.summary["identical"], 2, report.to_dict())
        self.assertEqual(report.rows[0].left_name, "layer2.png")
        self.assertEqual(report.rows[0].right_name, "slice001.png")
        self.assertTrue(report.overall_identical)

    def test_sequence_matching_reports_extra_file(self):
        write_png(self.left / "layer2.png", [2, 2])
        write_png(self.left / "layer10.png", [10, 10])
        write_png(self.right / "slice001.png", [2, 2])
        report = self.compare(mode="sequence")
        self.assertEqual(report.summary["identical"], 1)
        self.assertEqual(report.summary["left_only"], 1)
        self.assertFalse(report.overall_identical)

    def test_sequence_uses_actual_format_for_misnamed_image(self):
        write_png(self.left / "layer1.png", [1, 1])
        write_png(self.left / "layer2.dat", [2, 2])
        write_png(self.right / "slice001.png", [1, 1])
        write_png(self.right / "slice002.png", [2, 2])
        for directory in (self.left, self.right):
            (directory / "notes.txt").write_text("Auxiliary file", encoding="utf-8")
        report = self.compare(mode="sequence")
        self.assertEqual(len(report.rows), 3, report.to_dict())
        renamed = next(row for row in report.rows if row.left_name == "layer2.dat")
        self.assertEqual(renamed.right_name, "slice002.png", renamed.to_dict())
        self.assertEqual(renamed.left["format"], "PNG")
        self.assertEqual(renamed.right["format"], "PNG")
        self.assertIs(renamed.pixels_equal, True)
        self.assertEqual(report.summary["unsupported"], 1)

    def test_all_tiff_frames_are_compared(self):
        first = Image.new("L", (2, 2), 10)
        second = Image.new("L", (2, 2), 20)
        changed = Image.new("L", (2, 2), 21)
        first.save(self.left / "slice.tif", save_all=True, append_images=[second])
        first.save(self.right / "slice.tif", save_all=True, append_images=[changed])
        row = self.assertDifferentPixels(self.compare())
        self.assertEqual(row.left["frame_count"], 2)
        self.assertEqual(row.right["frame_count"], 2)

    def test_tiff_native_16_bit_low_bit_difference(self):
        import numpy as np
        import tifffile

        tifffile.imwrite(self.left / "slice.tif", np.array([[0x1200, 0xABCD]], dtype=np.uint16))
        tifffile.imwrite(self.right / "slice.tif", np.array([[0x1201, 0xABCD]], dtype=np.uint16))
        row = self.assertDifferentPixels(self.compare())
        self.assertEqual(row.left["bit_depth"], 16)

    def test_pre_cancelled_comparison_has_no_positive_conclusion(self):
        self.pair()
        cancelled = threading.Event()
        cancelled.set()
        report = self.compare(cancel_event=cancelled)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.overall_identical)

    def test_cancellation_from_progress_callback(self):
        for index in range(8):
            self.pair(name=f"slice{index}.png")
        cancelled = threading.Event()
        progress_calls = []

        def progress(percent, message):
            progress_calls.append((percent, message))
            cancelled.set()

        report = self.compare(cancel_event=cancelled, progress=progress)
        self.assertTrue(progress_calls)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.overall_identical)
        self.assertLess(len(report.rows), 8)

    def test_native_png_backend_preserves_samples_like_python_backend(self):
        for bit_depth in (8, 16):
            for channels in (1, 2, 3, 4):
                with self.subTest(bit_depth=bit_depth, channels=channels):
                    limit = (1 << bit_depth) - 1
                    values = [(index * 113 + 0x34) & limit for index in range(6 * channels)]
                    changed = values.copy()
                    changed[channels - 1] ^= 1
                    for directory, samples in ((self.left, values), (self.right, changed)):
                        write_png(directory / "slice.png", samples, width=3, height=2, bit_depth=bit_depth, channels=channels)
                    native = self.assertDifferentPixels(self.compare(options=CompareOptions(png_decoder="auto")))
                    python = self.assertDifferentPixels(self.compare(options=CompareOptions(png_decoder="python")))
                    for side in ("left", "right"):
                        native_frame = getattr(native, side)["frames"][0]
                        python_frame = getattr(python, side)["frames"][0]
                        for key in ("sample_sha256", "visual_sha256", "width", "height", "bit_depth", "channels", "dtype"):
                            self.assertEqual(native_frame[key], python_frame[key], (bit_depth, channels, side, key))

    def test_fast_identical_mode_marks_decode_validation_scope(self):
        self.pair(values=[0x1201, 0x3402, 0x5603, 0x7804, 0x9A05, 0xBC06], bit_depth=16, channels=3)
        report = self.compare(options=CompareOptions(verify_identical=False))
        row = self.only_row(report)
        self.assertEqual(row.status, "identical")
        self.assertTrue(report.overall_identical)
        self.assertIs(row.pixels_equal, True)
        self.assertEqual(row.left["decode_validation"], "header_only")
        self.assertEqual(row.right["decode_validation"], "header_only")
        self.assertFalse(row.left["frames"][0].get("sample_sha256"))

    def test_fast_mode_still_decodes_changed_pixel_bytes(self):
        write_png(self.left / "slice.png", [0x1200, 0x3400, 0x5600, 0x7800, 0x9A00, 0xBC00], bit_depth=16, channels=3)
        write_png(self.right / "slice.png", [0x1201, 0x3400, 0x5600, 0x7800, 0x9A00, 0xBC00], bit_depth=16, channels=3)
        row = self.assertDifferentPixels(self.compare(options=CompareOptions(verify_identical=False)))
        self.assertNotEqual(row.left.get("decode_validation"), "header_only")
        self.assertNotEqual(row.right.get("decode_validation"), "header_only")

    def test_fast_mode_still_decodes_metadata_only_difference(self):
        image = Image.new("L", (2, 1), 30)
        image.save(self.left / "slice.png")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "Different bytes, equal samples")
        image.save(self.right / "slice.png", pnginfo=metadata)
        row = self.only_row(self.compare(options=CompareOptions(verify_identical=False)))
        self.assertEqual(row.status, "different")
        self.assertIs(row.pixels_equal, True)
        self.assertNotEqual(row.left.get("decode_validation"), "header_only")
        self.assertNotEqual(row.right.get("decode_validation"), "header_only")

    def test_fast_mode_rejects_structurally_corrupt_equal_files(self):
        for directory in (self.left, self.right):
            (directory / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        report = self.compare(options=CompareOptions(verify_identical=False))
        self.assertEqual(self.only_row(report).status, "error")
        self.assertFalse(report.overall_identical)

    def test_fast_header_only_scope_is_explicit_for_invalid_deflate(self):
        path = write_png(self.left / "slice.png", [1, 2])
        data = bytearray(path.read_bytes())
        type_offset = data.index(b"IDAT")
        length = struct.unpack(">I", data[type_offset - 4:type_offset])[0]
        payload_start = type_offset + 4
        payload_end = payload_start + length
        data[payload_start] ^= 0xFF
        data[payload_end:payload_end + 4] = struct.pack(">I", zlib.crc32(data[type_offset:payload_end]) & 0xFFFFFFFF)
        for directory in (self.left, self.right):
            (directory / "slice.png").write_bytes(data)
        precise = self.compare(options=CompareOptions(verify_identical=True))
        self.assertEqual(self.only_row(precise).status, "error")
        self.assertFalse(precise.overall_identical)
        fast = self.compare(options=CompareOptions(verify_identical=False))
        row = self.only_row(fast)
        self.assertEqual(row.status, "identical")
        self.assertEqual(row.left["decode_validation"], "header_only")
        self.assertEqual(row.right["decode_validation"], "header_only")
        self.assertFalse(row.left["frames"][0].get("sample_sha256"))
        self.assertTrue(fast.warnings)

    def test_fast_unmatched_images_use_headers_and_keep_missing_status(self):
        self.pair()
        write_png(self.left / "extra.png", [3, 4])
        report = self.compare(options=CompareOptions(verify_identical=False))
        self.assertEqual(report.summary["identical"], 1)
        self.assertEqual(report.summary["left_only"], 1)
        row = next(row for row in report.rows if row.left_name == "extra.png")
        self.assertEqual(row.status, "left_only")
        self.assertEqual(row.left["decode_validation"], "header_only")

    def test_decode_cache_does_not_turn_equal_corrupt_files_into_matches(self):
        for directory in (self.left, self.right):
            for index in range(3):
                (directory / f"broken{index}.png").write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        report = self.compare()
        self.assertEqual(report.summary["error"], 3)
        self.assertEqual(report.summary["identical"], 0)
        self.assertFalse(report.overall_identical)

    def zip_pair(self, count=4):
        for index in range(count):
            self.pair(values=[index, index + 1], name=f"slice{index}.png")
        archives = []
        for directory in (self.left, self.right):
            destination = self.root / (directory.name + ".zip")
            with zipfile.ZipFile(destination, "w") as archive:
                for path in directory.glob("*.png"):
                    archive.write(path, path.name)
            archives.append(destination)
        return archives

    def track_archives(self):
        opened = []
        original = zipfile.ZipFile

        def open_archive(*args, **kwargs):
            archive = original(*args, **kwargs)
            opened.append(archive)
            return archive

        return opened, mock.patch.object(engine_module.zipfile, "ZipFile", side_effect=open_archive)

    def test_zip_sessions_close_after_success(self):
        left, right = self.zip_pair()
        opened, patcher = self.track_archives()
        with patcher:
            report = self.compare(left=left, right=right, mode="sequence")
        self.assertTrue(report.overall_identical)
        self.assertTrue(opened)
        self.assertTrue(all(archive.fp is None for archive in opened))

    def test_zip_sessions_close_after_mid_comparison_cancellation(self):
        left, right = self.zip_pair()
        cancelled = threading.Event()

        def progress(percent, message):
            if message.startswith("B:"):
                cancelled.set()

        opened, patcher = self.track_archives()
        with patcher:
            report = self.compare(left=left, right=right, mode="sequence", cancel_event=cancelled, progress=progress)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.overall_identical)
        self.assertTrue(opened)
        self.assertTrue(all(archive.fp is None for archive in opened))

    def test_zip_changed_after_cached_read_is_reported_and_closed(self):
        left, right = self.zip_pair()
        changed = False

        def progress(percent, message):
            nonlocal changed
            if message.startswith("B:") and not changed:
                with left.open("ab") as stream:
                    stream.write(b"changed after first read")
                changed = True

        opened, patcher = self.track_archives()
        with patcher:
            report = self.compare(left=left, right=right, mode="sequence", progress=progress)
        self.assertTrue(changed)
        self.assertFalse(report.overall_identical)
        self.assertGreater(report.summary["error"], 0)
        self.assertTrue(all(archive.fp is None for archive in opened))

    def test_json_and_csv_exports(self):
        self.pair()
        report = self.compare()
        json_path = self.root / "report.json"
        csv_path = self.root / "report.csv"
        report.save_json(json_path)
        report.save_csv(csv_path)
        payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(payload["summary"]["identical"], 1)
        self.assertEqual(payload["rows"][0]["status"], "identical")
        self.assertIn("slice.png", csv_path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
