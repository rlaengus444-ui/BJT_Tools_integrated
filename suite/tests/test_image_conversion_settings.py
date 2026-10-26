"""Editable pixel dimensions and DPI, including real encoded TIFF directories.

The 7x5 fixture was written by the original GDI+ conversion pipeline. Metadata
checks below patch its TIFF RATIONAL values without changing compressed pixels.
Windows cases additionally execute the native converter with custom settings.
"""
import base64
import ctypes
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import m2_image_engine as engine

OPAQUE_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAF0lEQVR4nGNgYGD4////fwYQDcLVus4AR9wG5r+dsuMAAAAASUVORK5CYII=")
ORIGINAL_TIFF = base64.b64decode(
    "SUkqAB4AAACAAlEEQnrlEuQmi8IupQQp3OCHKCAgDwD+AAQAAQAAAAAAAAAAAQQAAQAAAAcAAAABAQQAAQAAAAUAAAACAQMAAQAAAAQAAAADAQMAAQAAAAUAAAAGAQMAAQAAAAMAAAARAQQAAQAAAAgAAAAVAQMAAQAAAAEAAAAWAQQAAQAAAAUAAAAXAQQAAQAAABYAAAAaAQUAAQAAANgAAAAbAQUAAQAAAOAAAAAoAQMAAQAAAAIAAAA9AQMAAQAAAAEAAABAAQMAMAAAAOgAAAAAAAAAgBoGAOgDAACAGgYA6AMAAP//////////AAAAAAAAAAA/P39/f3//////f39/f////////wAAAAD/////AAAAAD8/f3///39///9/f///f3///wAA//8AAP//AAD//wAAPz9/f/////9/f///f39/fw==")


def with_resolution(dpi_x, dpi_y):
    """Change only two TIFF RATIONAL fields in the original encoded fixture."""
    from fractions import Fraction
    data = bytearray(ORIGINAL_TIFF)
    offset = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from("<H", data, offset)[0]
    remaining = {282: dpi_x, 283: dpi_y}
    for index in range(count):
        tag, kind, length, location = struct.unpack_from("<HHII", data, offset + 2 + index * 12)
        if tag in remaining:
            assert kind == 5 and length == 1
            value = Fraction(remaining.pop(tag))
            struct.pack_into("<II", data, location, value.numerator, value.denominator)
    assert not remaining
    return bytes(data)


class OutputSpecValidationTests(unittest.TestCase):
    def job(self, **options):
        return engine.ConversionJob(("input.png",), "output", **options)

    def test_no_overrides_preserves_each_original_preset(self):
        for mode, original in engine.MODE_SPECS.items():
            with self.subTest(mode=mode):
                self.assertEqual(engine.resolve_spec(self.job(mode=mode)), original)

    def test_pixel_and_dpi_overrides_are_independent(self):
        pixels = engine.resolve_spec(self.job(width=13, height=9))
        self.assertEqual((pixels.width, pixels.height, pixels.dpi_x, pixels.dpi_y), (13, 9, 400, 400))
        dpi = engine.resolve_spec(self.job(mode="multi", dpi_x=203.25, dpi_y=612.5))
        self.assertEqual((dpi.width, dpi.height, dpi.dpi_x, dpi.dpi_y), (8504, 13228, 203.25, 612.5))

    def test_single_override_does_not_replace_other_axis(self):
        spec = engine.resolve_spec(self.job(mode="multi", width=17, dpi_y=300))
        self.assertEqual((spec.width, spec.height, spec.dpi_x, spec.dpi_y), (17, 13228, 400, 300))

    def test_decimal_dpi_uses_native_float32_representation(self):
        spec = engine.resolve_spec(self.job(dpi_x=203.2))
        self.assertEqual(spec.dpi_x, ctypes.c_float(203.2).value)
        self.assertTrue(math.isclose(spec.dpi_x, 203.2, rel_tol=1e-7))

    def test_invalid_overrides_reject_before_native_work_or_output_creation(self):
        invalid = []
        for key in ("width", "height"):
            invalid.extend({key: value} for value in (True, 0, -1, 7.0, "7", 2**31))
        for key in ("dpi_x", "dpi_y"):
            invalid.extend({key: value} for value in (True, "300", 0, -1, math.inf, -math.inf, math.nan, 1e-100, 1e100))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.png"
            source.write_bytes(OPAQUE_PNG)
            output = Path(directory) / "not-created"
            for options in invalid:
                with self.subTest(options=options), patch.object(engine, "_GDIPlus") as native:
                    with self.assertRaises(ValueError):
                        engine.convert_images(engine.ConversionJob((source,), output, **options))
                    native.assert_not_called()
                    self.assertFalse(output.exists())


class EncodedOutputMetadataTests(unittest.TestCase):
    def test_custom_anisotropic_decimal_dpi_in_actual_four_bit_lzw_tiff(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.tif"
            path.write_bytes(with_resolution(203.25, 612.5))
            info = engine.inspect_output(path, engine.ModeSpec(7, 5, 203.25, 612.5, "custom"))
            self.assertEqual((info["width"], info["height"], info["dpi_x"], info["dpi_y"]), (7, 5, 203.25, 612.5))
            self.assertEqual((info["bits_per_pixel"], info["compression"], info["resolution_unit"]), (4, 5, 2))
            # Metadata-only DPI changes preserve all decoded indexed pixels.
            with Image.open(path) as image:
                digest = hashlib.sha256(image.tobytes()).hexdigest()
            self.assertEqual(digest, "85497e6e131c52302625ea385a48c7f7d9bd6c455b0faa1251cd30b86f9733df")

    def test_mismatched_pixels_or_each_dpi_axis_reject_actual_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.tif"
            path.write_bytes(with_resolution(203.25, 612.5))
            for spec in (engine.ModeSpec(8, 5, 203.25, 612.5, "bad width"),
                         engine.ModeSpec(7, 6, 203.25, 612.5, "bad height"),
                         engine.ModeSpec(7, 5, 300, 612.5, "bad X DPI"),
                         engine.ModeSpec(7, 5, 203.25, 600, "bad Y DPI")):
                with self.subTest(spec=spec), self.assertRaises(OSError):
                    engine.inspect_output(path, spec)

    def test_zero_dpi_is_never_accepted_even_for_small_requested_dpi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.tif"
            path.write_bytes(with_resolution(0, 400))
            with self.assertRaises(OSError):
                engine.inspect_output(path, engine.ModeSpec(7, 5, .0001, 400, "small"))


class ImageConverterInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("image_converter_settings_ui", PACKAGE / "sources" / "m2_converter.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.interpreter = tk.Tcl()
        self.app = object.__new__(self.module.M2ConverterApp)
        for name, value in {"mode": "싱글", "width": "8504", "height": "6614", "dpi_x": "400", "dpi_y": "400",
                            "output": "output", "start": "1", "padding": "3", "conflict": "건너뛰기 (기본)", "spec": ""}.items():
            setattr(self.app, name + "_var", tk.StringVar(master=self.interpreter, value=value))
        self.app._invalidate = Mock()
        self.app._applying_preset = False

    def test_preset_switch_and_explicit_reset_restore_known_defaults(self):
        self.app.mode_var.set("멀티")
        self.app._mode_changed()
        self.assertEqual(self.app._output_spec_options(), {"width": 8504, "height": 13228, "dpi_x": 400, "dpi_y": 800})
        self.app.width_var.set("123")
        self.app.dpi_y_var.set("612.5")
        self.app.apply_preset()
        self.assertEqual((self.app.width_var.get(), self.app.dpi_y_var.get()), ("8504", "800"))

    def test_custom_fields_and_original_options_tuple_stay_independent(self):
        self.app.width_var.set("17")
        self.app.height_var.set("11")
        self.app.dpi_x_var.set("203.25")
        self.app.dpi_y_var.set("612.5")
        self.assertEqual(self.app._options(), ("single", 1, 3, "skip", "output"))
        self.assertEqual(self.app._output_spec_options(), {"width": 17, "height": 11, "dpi_x": 203.25, "dpi_y": 612.5})
        self.app._update_specs()
        self.assertIn("17 × 11 px", self.app.spec_var.get())
        self.assertIn("203.25 × 612.5", self.app.spec_var.get())

    def test_invalid_text_dimensions_and_nonfinite_dpi_disable_valid_options(self):
        for name, value in (("width", "2.5"), ("height", "0"), ("width", "2147483648"),
                            ("dpi_x", "nan"), ("dpi_y", "inf"), ("dpi_y", "-1")):
            variable = getattr(self.app, name + "_var")
            old = variable.get()
            variable.set(value)
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.app._options()
            variable.set(old)


@unittest.skipUnless(os.name == "nt", "Native GDI+ conversion requires Windows")
class NativeCustomOutputTests(unittest.TestCase):
    def test_custom_pixels_and_anisotropic_decimal_dpi_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.png"
            source.write_bytes(OPAQUE_PNG)
            output = Path(directory) / "output"
            report = engine.convert_images(engine.ConversionJob((source,), output,
                width=13, height=9, dpi_x=203.25, dpi_y=612.5))
            self.assertTrue(report.completed, report.to_dict())
            metadata = report.items[0]["metadata"]
            self.assertEqual((metadata["width"], metadata["height"], metadata["dpi_x"], metadata["dpi_y"]),
                             (13, 9, 203.25, 612.5))
            self.assertEqual((metadata["bits_per_pixel"], metadata["compression"], metadata["resolution_unit"]), (4, 5, 2))
            self.assertEqual(source.read_bytes(), OPAQUE_PNG)

    def test_changing_only_dpi_keeps_native_pixel_values_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.png"
            source.write_bytes(OPAQUE_PNG)
            hashes = []
            for index, dpi in enumerate(((400, 400), (203.2, 612.5))):
                output = Path(directory) / str(index)
                report = engine.convert_images(engine.ConversionJob((source,), output,
                    width=7, height=5, dpi_x=dpi[0], dpi_y=dpi[1]))
                self.assertTrue(report.completed, report.to_dict())
                with Image.open(output / "001.tif") as image:
                    hashes.append(hashlib.sha256(image.tobytes()).hexdigest())
            self.assertEqual(hashes, ["85497e6e131c52302625ea385a48c7f7d9bd6c455b0faa1251cd30b86f9733df"] * 2)


if __name__ == "__main__":
    unittest.main()
