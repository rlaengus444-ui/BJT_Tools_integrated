"""Independent C# GDI+ golden parity and safe M2 batch publishing tests.

Golden hashes were obtained by a separate headless .NET Framework reference
using the statically decompiled DrawImage/Clone/palette-invert/TIFF-LZW calls.
Neither supplied ImageConverter EXE was executed. Full-size goldens use exact
8504x6614/400x400 and 8504x13228/400x800 settings; small 7x5 fixtures isolate
resampling, RGBA handling and atomic batch behavior without repeated large work.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import warnings

from PIL import Image

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import m2_image_engine as engine

OPAQUE_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAF0lEQVR4nGNgYGD4////fwYQDcLVus4AR9wG5r+dsuMAAAAASUVORK5CYII=")
ALPHA_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAI0lEQVR4nGP8z8DAwPifwYGB8b8D4//////zKluezOfmtgQAdswJbqfCUywAAAAASUVORK5CYII=")
PALETTE = [(255,255,255),(255,255,0),(255,0,255),(255,0,0),
           (0,255,255),(0,255,0),(0,0,255),(0,0,0),(63,63,63),(127,127,127),
           (127,255,255),(255,127,255),(255,255,127),(127,127,255),(127,255,127),(255,127,127)]
GOLDEN = {
    "single": ("639f56973564e43d0fb3007e4b7ddd732cc8015a1e289e00bea13b599c461462",
               "753797732b56515fc940f86c2ee9b575e5eb907ffeb8f4cfd6dda1f11642a84a"),
    "multi": ("a78cbf595fa2395d3f13d7cab8f5a42cb6497f595332ba752898d648bf6f62e1",
              "eb6c834fd122bfa539a3d67d25d953b93058ea473c8a5728881d21db1e5191c6"),
    "opaque_small": ("85497e6e131c52302625ea385a48c7f7d9bd6c455b0faa1251cd30b86f9733df",
                     "adc832fb358d30dda7beb945074493a2a52f1082d29aa764fd030474a1d24803"),
    "alpha_small": ("b511aee735d396c881cd0f8d2107ac7465f01e6b80115b2138f967b3ed3f9c33",
                    "a9f960e452c5641226c7363758b8b3e7600f560150fb520645e95ffa29dff795"),
}


def image_fingerprint(path):
    # Only these known synthetic fixtures exceed Pillow's warning threshold.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            tags = dict(image.tag_v2)
            image.load()
            indices, rgb = hashlib.sha256(), hashlib.sha256()
            for y in range(0, image.height, 128):
                tile = image.crop((0, y, image.width, min(y+128, image.height)))
                indices.update(tile.tobytes())
                rgb.update(tile.convert("RGB").tobytes())
            colors = image.getpalette()[:48]
            palette = list(zip(colors[::3], colors[1::3], colors[2::3]))
            return {"format": image.format, "mode": image.mode, "size": image.size,
                    "frames": image.n_frames, "tags": tags, "palette": palette,
                    "hashes": (indices.hexdigest(), rgb.hexdigest())}


@unittest.skipUnless(os.name == "nt", "Original M2 conversion requires Windows GDI+")
class M2FullReferenceTests(unittest.TestCase):
    def check_mode(self, mode, size, dpi):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(OPAQUE_PNG)
            output = root / "result"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                report = engine.convert_images(engine.ConversionJob((source,), output, mode=mode))
            self.assertTrue(report.completed, report.to_dict())
            self.assertFalse(report.cancelled)
            self.assertEqual((report.total, report.converted, report.skipped, report.failed), (1,1,0,0))
            fingerprint = image_fingerprint(output / "001.tif")
            self.assertEqual(fingerprint["hashes"], GOLDEN[mode])
            self.assertEqual(fingerprint["size"], size)
            self.assertEqual(fingerprint["palette"], PALETTE)
            self.assertEqual((fingerprint["format"], fingerprint["mode"], fingerprint["frames"]), ("TIFF","P",1))
            tags = fingerprint["tags"]
            self.assertEqual(tuple(tags[258]), (4,))
            self.assertEqual((tags[259], tags[262], tags[277], tags[296]), (5,3,1,2))
            self.assertEqual((float(tags[282]), float(tags[283])), dpi)
            self.assertNotIn(338, tags)  # Indexed palette has no alpha channel.
            self.assertEqual(source.read_bytes(), OPAQUE_PNG)
            self.assertEqual([p.name for p in output.iterdir()], ["001.tif"])

    def test_full_single_gdi_reference_all_pixels_palette_and_tags(self):
        self.check_mode("single", (8504,6614), (400,400))

    def test_full_multi_gdi_reference_all_pixels_palette_and_tags(self):
        self.check_mode("multi", (8504,13228), (400,800))


@unittest.skipUnless(os.name == "nt", "Original M2 conversion requires Windows GDI+")
class M2BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(OPAQUE_PNG)
        self.alpha = self.root / "alpha.png"
        self.alpha.write_bytes(ALPHA_PNG)
        self.output = self.root / "result"
        self.small = engine.ModeSpec(7,5,400,400,"small test")
        self.multi = engine.ModeSpec(7,5,400,800,"small multi test")
        self.modes = patch.object(engine, "MODE_SPECS", {"single":self.small,"multi":self.multi})
        self.modes.start()
        self.addCleanup(self.modes.stop)

    def job(self, sources=None, **kwargs):
        return engine.ConversionJob(tuple(sources or [self.source]), self.output, **kwargs)

    def assert_no_temporaries(self):
        if self.output.exists():
            self.assertFalse(list(self.output.glob(".bjt_m2_*")))

    def test_small_opaque_and_rgba_match_independent_reference(self):
        report = engine.convert_images(self.job([self.source,self.alpha], mode="multi"))
        self.assertTrue(report.completed, report.to_dict())
        self.assertEqual(report.converted, 2)
        self.assertEqual(image_fingerprint(self.output/"001.tif")["hashes"], GOLDEN["opaque_small"])
        alpha = image_fingerprint(self.output/"002.tif")
        self.assertEqual(alpha["hashes"], GOLDEN["alpha_small"])
        self.assertEqual(alpha["palette"], PALETTE)
        self.assertEqual(float(alpha["tags"][283]), 800)
        self.assert_no_temporaries()

    def test_selection_order_and_minimum_padding_not_filename_sort(self):
        report = engine.convert_images(self.job([self.alpha,self.source],start_number=999,zero_pad=3))
        self.assertTrue(report.completed)
        self.assertEqual(image_fingerprint(self.output/"999.tif")["hashes"], GOLDEN["alpha_small"])
        self.assertEqual(image_fingerprint(self.output/"1000.tif")["hashes"], GOLDEN["opaque_small"])
        self.assertEqual([Path(item["source"]).name for item in report.items], ["alpha.png","source.png"])

    def test_skip_does_not_decode_or_modify_existing_output(self):
        self.output.mkdir()
        existing = self.output/"001.tif"
        existing.write_bytes(b"original destination")
        with patch.object(engine,"_GDIPlus") as native:
            report = engine.convert_images(self.job())
        native.assert_not_called()
        self.assertTrue(report.completed)
        self.assertEqual((report.converted,report.skipped,report.failed),(0,1,0))
        self.assertEqual(existing.read_bytes(),b"original destination")
        self.assert_no_temporaries()

    def test_overwrite_replaces_destination_after_valid_conversion(self):
        self.output.mkdir()
        existing = self.output/"001.tif"
        existing.write_bytes(b"old")
        report = engine.convert_images(self.job(conflict="overwrite"))
        self.assertTrue(report.completed,report.to_dict())
        self.assertEqual(image_fingerprint(existing)["hashes"],GOLDEN["opaque_small"])
        self.assertEqual(self.source.read_bytes(),OPAQUE_PNG)
        self.assert_no_temporaries()

    def test_source_equal_output_overwrite_rejected_before_any_work(self):
        self.output.mkdir()
        source = self.output/"001.tif"
        Image.new("RGB",(2,2),(1,2,3)).save(source)
        before = source.read_bytes()
        with patch.object(engine,"_GDIPlus") as native, self.assertRaises(ValueError):
            engine.convert_images(self.job([source],conflict="overwrite"))
        native.assert_not_called()
        self.assertEqual(source.read_bytes(),before)

    def test_source_equal_output_skip_keeps_source_and_does_not_decode(self):
        self.output.mkdir()
        source=self.output/"001.tif"
        Image.new("RGB",(2,2),(1,2,3)).save(source)
        before=source.read_bytes()
        with patch.object(engine,"_GDIPlus") as native:
            report=engine.convert_images(self.job([source]))
        native.assert_not_called()
        self.assertTrue(report.completed)
        self.assertEqual((report.converted,report.skipped),(0,1))
        self.assertEqual(source.read_bytes(),before)

    def test_earlier_output_cannot_overwrite_later_source(self):
        self.output.mkdir()
        later = self.output/"001.tif"
        Image.new("RGB",(2,2),(1,2,3)).save(later)
        before = later.read_bytes()
        with patch.object(engine,"_GDIPlus") as native, self.assertRaises(ValueError):
            engine.convert_images(self.job([self.source,later],conflict="overwrite"))
        native.assert_not_called()
        self.assertEqual(later.read_bytes(),before)
        self.assertEqual(list(self.output.iterdir()),[later])

    def test_hard_link_source_alias_is_protected(self):
        self.output.mkdir()
        target=self.output/"001.tif"
        os.link(self.source,target)
        with self.assertRaises(ValueError):
            engine.convert_images(self.job(conflict="overwrite"))
        self.assertEqual(self.source.read_bytes(),OPAQUE_PNG)
        self.assertEqual(target.read_bytes(),OPAQUE_PNG)

    def test_skip_race_preserves_file_created_by_other_process(self):
        def race(source,target):
            Path(target).write_bytes(b"other creator")
            raise FileExistsError("created concurrently")
        with patch.object(engine.os,"rename",side_effect=race) as rename:
            report=engine.convert_images(self.job())
        rename.assert_called_once()
        self.assertTrue(report.completed)
        self.assertEqual((report.converted,report.skipped,report.failed),(0,1,0))
        self.assertEqual((self.output/"001.tif").read_bytes(),b"other creator")
        self.assert_no_temporaries()

    def test_overwrite_rechecks_source_alias_created_during_conversion(self):
        def progress(event):
            if event["stage"]=="validate":
                os.link(self.source,self.output/"001.tif")
        report=engine.convert_images(self.job(conflict="overwrite"),progress=progress)
        self.assertFalse(report.completed)
        self.assertEqual((report.converted,report.failed),(0,1))
        self.assertIn("원본",report.error)
        self.assertEqual(self.source.read_bytes(),OPAQUE_PNG)
        self.assertEqual((self.output/"001.tif").read_bytes(),OPAQUE_PNG)
        self.assert_no_temporaries()

    def test_precancel_creates_no_output_and_initializes_no_native_state(self):
        event=threading.Event();event.set()
        with patch.object(engine,"_GDIPlus") as native:
            report=engine.convert_images(self.job(),cancel_event=event)
        native.assert_not_called()
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertEqual((report.converted,report.failed),(0,0))
        self.assertFalse(self.output.exists())

    def test_cancel_after_first_output_keeps_only_completed_prefix(self):
        event=threading.Event()
        def progress(update):
            if update["stage"]=="converted":event.set()
        report=engine.convert_images(self.job([self.source,self.alpha]),event,progress)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertEqual((report.converted,report.failed),(1,0))
        self.assertTrue((self.output/"001.tif").is_file())
        self.assertFalse((self.output/"002.tif").exists())
        self.assert_no_temporaries()

    def test_cancel_during_native_save_preserves_old_destination(self):
        self.output.mkdir();target=self.output/"001.tif";target.write_bytes(b"old")
        event=threading.Event()
        def progress(update):
            if update["stage"]=="save":event.set()
        report=engine.convert_images(self.job(conflict="overwrite"),event,progress)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertEqual((report.converted,report.failed),(0,0))
        self.assertEqual(target.read_bytes(),b"old")
        self.assert_no_temporaries()
        # Native resources were released; a following conversion succeeds.
        self.assertTrue(engine.convert_images(self.job(conflict="overwrite")).completed)

    def test_cancel_at_validation_prevents_publish(self):
        event=threading.Event()
        def progress(update):
            if update["stage"]=="validate":event.set()
        report=engine.convert_images(self.job(),event,progress)
        self.assertTrue(report.cancelled)
        self.assertEqual(report.converted,0)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_corrupt_second_input_stops_batch_and_keeps_first_output(self):
        corrupt=self.root/"corrupt.png";corrupt.write_bytes(b"broken png")
        report=engine.convert_images(self.job([self.source,corrupt,self.alpha]))
        self.assertFalse(report.completed)
        self.assertFalse(report.cancelled)
        self.assertEqual((report.total,report.converted,report.failed),(3,1,1))
        self.assertTrue(report.error)
        self.assertEqual([item["status"] for item in report.items],["converted","failed"])
        self.assertEqual([p.name for p in self.output.iterdir()],["001.tif"])
        self.assert_no_temporaries()

    def test_publish_failure_preserves_old_destination_and_cleans_temp(self):
        self.output.mkdir();target=self.output/"001.tif";target.write_bytes(b"old")
        with patch.object(engine.os,"replace",side_effect=OSError("simulated commit failure")) as replace:
            report=engine.convert_images(self.job(conflict="overwrite"))
        replace.assert_called_once()
        self.assertFalse(report.completed)
        self.assertEqual((report.converted,report.failed),(0,1))
        self.assertIn("simulated commit failure",report.error)
        self.assertEqual(target.read_bytes(),b"old")
        self.assert_no_temporaries()

    def test_invalid_native_output_is_not_published(self):
        def invalid(native,source,destination,spec,cancel_event=None,progress=None):
            Image.new("RGB",(7,5)).save(destination,format="TIFF")
        with patch.object(engine._GDIPlus,"convert",invalid):
            report=engine.convert_images(self.job())
        self.assertFalse(report.completed)
        self.assertEqual(report.failed,1)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_partial_native_failure_cleans_temp_and_shuts_down(self):
        native=engine._GDIPlus()
        original_close=native.close
        native.close=Mock(wraps=original_close)
        def failure(source,destination,spec,cancel_event=None,progress=None):
            Path(destination).write_bytes(b"partial")
            raise OSError("simulated native failure")
        native.convert=Mock(side_effect=failure)
        with patch.object(engine,"_GDIPlus",return_value=native):
            report=engine.convert_images(self.job())
        native.close.assert_called_once()
        self.assertEqual(native.token.value,0)
        self.assertEqual((report.converted,report.failed),(0,1))
        self.assertIn("simulated native failure",report.error)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_invalid_job_parameters_never_create_output(self):
        invalid=[{"mode":"unknown"},{"start_number":0},{"start_number":True},
                 {"zero_pad":-1},{"zero_pad":13},{"zero_pad":True},{"conflict":"rename"}]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                engine.convert_images(self.job(**kwargs))
        for job in (engine.ConversionJob((),self.output),engine.ConversionJob(str(self.source),self.output),
                    engine.ConversionJob((self.source,),""),
                    engine.ConversionJob((self.root/"missing.png",),self.output)):
            with self.assertRaises(ValueError):engine.convert_images(job)
        self.assertFalse(self.output.exists())

    def test_unsupported_extension_and_output_file_are_rejected(self):
        unsupported=self.root/"not-image.txt";unsupported.write_bytes(OPAQUE_PNG)
        with self.assertRaises(ValueError):
            engine.convert_images(self.job([unsupported]))
        self.output.write_bytes(b"existing non-directory")
        with self.assertRaises(ValueError):
            engine.convert_images(self.job())
        self.assertEqual(self.output.read_bytes(),b"existing non-directory")

    def test_report_serializes_paths_counts_progress_and_metadata(self):
        events=[]
        report=engine.convert_images(self.job(),progress=events.append)
        payload=report.to_dict()
        json.dumps(payload,ensure_ascii=False)
        self.assertTrue(payload["completed"])
        self.assertGreaterEqual(payload["elapsed_seconds"],0)
        self.assertEqual(payload["items"][0]["metadata"]["bits_per_sample"],[4])
        self.assertEqual(events[-1]["stage"],"converted")
        self.assertEqual({event["stage"] for event in events},{"decode","resize","palette","save","validate","converted"})
        self.assertEqual(self.source.read_bytes(),OPAQUE_PNG)


if __name__=="__main__":
    unittest.main()
