"""Numbered-copy and Missing No. behavior; fixtures never touch user files."""
from __future__ import annotations
import hashlib
import importlib.util
import os
from pathlib import Path
import queue
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "sources"))
import number_tools as nt

REFERENCE = PACKAGE.parents[1] / "work" / "bj-tools" / "FileNumberDuplicatorPlus.py"


class NumberToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bjt_numbers_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.PnG"
        self.source.write_bytes(b"opaque file bytes, preserved without image re-encoding\x00\xFF")
        os.utime(self.source, ns=(1_650_000_000_123_456_700, 1_650_000_000_123_456_700))
        self.output = self.root / "output"
        self.output.mkdir()

    def job(self, **overrides):
        values = {"source_path": str(self.source), "output_dir": str(self.output), "min_number": 1, "max_number": 3, "conflict": "skip"}
        values.update(overrides)
        return nt.DuplicateJob(**values)

    def assertSameCopy(self, path):
        self.assertEqual(path.read_bytes(), self.source.read_bytes())
        self.assertEqual(path.stat().st_mtime_ns, self.source.stat().st_mtime_ns)

    def test_inclusive_range_preserves_extension_bytes_and_modified_time(self):
        report = nt.duplicate_files(self.job(min_number=7, max_number=9))
        self.assertTrue(report.completed)
        self.assertFalse(report.cancelled)
        self.assertEqual(report.requested_count, 3)
        self.assertEqual(report.copied_count, 3)
        self.assertEqual(report.error_count, 0)
        self.assertEqual({path.name for path in self.output.iterdir()}, {"7.PnG", "8.PnG", "9.PnG"})
        for path in self.output.iterdir():
            self.assertSameCopy(path)

    def test_zero_padding_is_explicit(self):
        report = nt.duplicate_files(self.job(min_number=2, max_number=2, zero_pad=4))
        self.assertEqual(report.copied_count, 1)
        self.assertSameCopy(self.output / "0002.PnG")

    def test_negative_duplicate_range_preserves_original_numbering(self):
        report = nt.duplicate_files(self.job(min_number=-1, max_number=1))
        self.assertEqual(report.copied_count, 3)
        self.assertEqual({path.name for path in self.output.iterdir()}, {"-1.PnG", "0.PnG", "1.PnG"})
        missing = nt.scan_missing_numbers(self.output, min_number=0, max_number=1)
        self.assertEqual(missing.missing_count, 0)
        self.assertIn("-1.PnG", {Path(name).name for name in missing.unnumbered})

    def test_skip_keeps_existing_file(self):
        destination = self.output / "2.PnG"
        destination.write_bytes(b"existing")
        report = nt.duplicate_files(self.job(conflict="skip"))
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertEqual(report.copied_count, 2)
        self.assertEqual(report.skipped_count, 1)

    def test_overwrite_replaces_existing_file(self):
        (self.output / "2.PnG").write_bytes(b"existing")
        report = nt.duplicate_files(self.job(conflict="overwrite"))
        self.assertEqual(report.copied_count, 3)
        self.assertEqual(report.error_count, 0)
        self.assertSameCopy(self.output / "2.PnG")

    def test_rename_selects_next_available_suffix(self):
        (self.output / "2.PnG").write_bytes(b"existing")
        (self.output / "2_1.PnG").write_bytes(b"first collision")
        report = nt.duplicate_files(self.job(min_number=2, max_number=2, conflict="rename"))
        self.assertEqual(report.copied_count, 1)
        self.assertEqual((self.output / "2.PnG").read_bytes(), b"existing")
        self.assertEqual((self.output / "2_1.PnG").read_bytes(), b"first collision")
        self.assertSameCopy(self.output / "2_2.PnG")

    def test_same_source_target_is_protected_for_overwrite(self):
        source = self.output / "2.PnG"
        source.write_bytes(self.source.read_bytes())
        before = source.read_bytes(), source.stat().st_mtime_ns
        report = nt.duplicate_files(self.job(source_path=str(source), min_number=2, max_number=3, conflict="overwrite"))
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
        self.assertEqual(report.copied_count, 1)
        self.assertEqual(report.error_count, 0)
        self.assertTrue(any(item.status == "skipped_source" for item in report.items))
        self.assertEqual((self.output / "3.PnG").read_bytes(), source.read_bytes())

    def test_same_source_rename_makes_a_separate_copy(self):
        source = self.output / "2.PnG"
        source.write_bytes(self.source.read_bytes())
        report = nt.duplicate_files(self.job(source_path=str(source), min_number=2, max_number=2, conflict="rename"))
        self.assertEqual(report.copied_count, 1)
        self.assertEqual((self.output / "2_1.PnG").read_bytes(), source.read_bytes())

    def test_blank_output_uses_source_parent(self):
        report = nt.duplicate_files(self.job(output_dir=None, min_number=4, max_number=4))
        self.assertEqual(report.copied_count, 1)
        self.assertSameCopy(self.root / "4.PnG")

    def test_invalid_copy_inputs_do_not_write_files(self):
        cases = [{"min_number": 3, "max_number": 2}, {"conflict": "unknown"}, {"zero_pad": -1}, {"source_path": str(self.root / "missing.png")}]
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    nt.duplicate_files(self.job(**values))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_partial_copy_failure_keeps_old_file_and_cleans_temp(self):
        target = self.output / "1.PnG"
        target.write_bytes(b"existing bytes")

        def fail_copy(source, destination, *args, **kwargs):
            destination.write(b"partial output")
            raise OSError("test disk write failure")

        with mock.patch.object(nt.shutil, "copyfileobj", side_effect=fail_copy):
            report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="overwrite"))
        self.assertEqual(report.error_count, 1)
        self.assertEqual(target.read_bytes(), b"existing bytes")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"1.PnG"})

    def test_atomic_commit_failure_keeps_old_file_and_cleans_temp(self):
        target = self.output / "1.PnG"
        target.write_bytes(b"existing bytes")
        with mock.patch.object(nt.os, "replace", side_effect=OSError("test commit failure")):
            report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="overwrite"))
        self.assertEqual(report.error_count, 1)
        self.assertEqual(target.read_bytes(), b"existing bytes")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"1.PnG"})

    def test_readonly_source_preserves_metadata_and_cleans_failed_temp(self):
        def restore_writable():
            for path in self.root.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)

        self.addCleanup(restore_writable)
        self.source.chmod(stat.S_IREAD)
        report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="skip"))
        self.assertEqual(report.copied_count, 1)
        self.assertSameCopy(self.output / "1.PnG")
        self.assertFalse((self.output / "1.PnG").stat().st_mode & stat.S_IWRITE)
        report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="rename"))
        self.assertEqual(report.copied_count, 1)
        self.assertSameCopy(self.output / "1_1.PnG")
        with mock.patch.object(nt.os, "replace", side_effect=OSError("test commit failure")):
            report = nt.duplicate_files(self.job(min_number=2, max_number=2, conflict="overwrite"))
        self.assertEqual(report.error_count, 1)
        self.assertEqual({path.name for path in self.output.iterdir()}, {"1.PnG", "1_1.PnG"})

    @unittest.skipUnless(os.name == "nt", "Windows rename publishes exclusively without filesystem hard links")
    def test_windows_copy_does_not_require_hardlink_support(self):
        with mock.patch.object(nt.os, "link", side_effect=OSError("hard links unsupported on this filesystem")):
            report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="skip"))
        self.assertEqual(report.copied_count, 1)
        self.assertSameCopy(self.output / "1.PnG")

    @unittest.skipUnless(os.name == "nt", "Windows rename no-overwrite race")
    def test_skip_race_keeps_concurrent_creator_bytes(self):
        original_rename = nt.os.rename
        raced = False

        def race(source, destination, *args, **kwargs):
            nonlocal raced
            if not raced:
                Path(destination).write_bytes(b"concurrent creator")
                raced = True
                raise FileExistsError("created just before publish")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(nt.os, "rename", side_effect=race):
            report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="skip"))
        self.assertTrue(raced)
        self.assertEqual(report.copied_count, 0)
        self.assertEqual(report.skipped_count, 1)
        self.assertEqual((self.output / "1.PnG").read_bytes(), b"concurrent creator")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"1.PnG"})

    @unittest.skipUnless(os.name == "nt", "Windows rename no-overwrite race")
    def test_rename_race_tries_next_name_without_overwriting_creator(self):
        original_rename = nt.os.rename
        raced = False

        def race(source, destination, *args, **kwargs):
            nonlocal raced
            if not raced:
                Path(destination).write_bytes(b"concurrent creator")
                raced = True
                raise FileExistsError("created just before publish")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(nt.os, "rename", side_effect=race):
            report = nt.duplicate_files(self.job(min_number=1, max_number=1, conflict="rename"))
        self.assertTrue(raced)
        self.assertEqual(report.copied_count, 1)
        self.assertEqual((self.output / "1.PnG").read_bytes(), b"concurrent creator")
        self.assertSameCopy(self.output / "1_1.PnG")
        self.assertEqual({path.name for path in self.output.iterdir()}, {"1.PnG", "1_1.PnG"})

    def test_copy_cancellation_returns_partial_incomplete_report(self):
        cancelled = threading.Event()

        def progress(percent, message):
            if 0 < percent < 100:
                cancelled.set()

        report = nt.duplicate_files(self.job(min_number=1, max_number=100), cancel_event=cancelled, progress=progress)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertGreater(report.copied_count, 0)
        self.assertLess(report.copied_count, 100)
        self.assertEqual(len(list(self.output.glob("*.PnG"))), report.copied_count)

    def test_pre_cancelled_copy_writes_nothing(self):
        cancelled = threading.Event()
        cancelled.set()
        report = nt.duplicate_files(self.job(), cancel_event=cancelled)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertEqual(report.copied_count, 0)
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(REFERENCE.is_file(), "Original supplied tool is only present in the integration workspace")
    def test_original_valid_conflict_policies_match_file_bytes_and_metadata(self):
        spec = importlib.util.spec_from_file_location("number_reference", REFERENCE)
        original = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = original
        spec.loader.exec_module(original)

        def snapshot(folder):
            return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns) for path in folder.iterdir()}

        for policy in ("skip", "overwrite", "rename"):
            with self.subTest(policy=policy):
                baseline, candidate = self.root / (policy + "_old"), self.root / (policy + "_new")
                baseline.mkdir()
                candidate.mkdir()
                for folder in (baseline, candidate):
                    for name in ("1.PnG", "2.PnG", "2_1.PnG"):
                        path = folder / name
                        path.write_bytes(("existing " + name).encode())
                        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
                fake = SimpleNamespace(stop_flag=threading.Event(), queue=queue.Queue())
                old_job = original.DuplicateJob(str(self.source), str(baseline), 1, 3, policy)
                original.DuplicatorFrame._worker(fake, old_job)
                messages = list(fake.queue.queue)
                self.assertTrue(any(message["kind"] == "done" for message in messages))
                new_report = nt.duplicate_files(self.job(output_dir=str(candidate), conflict=policy))
                self.assertEqual(new_report.error_count, 0)
                self.assertEqual(snapshot(baseline), snapshot(candidate))

    def make_number_inventory(self):
        for name in ("1.png", "03.PNG", "15_1.png", "15_2.png", "22.png", "-1.png", "abc.png", "20.txt"):
            (self.output / name).write_bytes(b"not an image; filename inventory only")
        nested = self.output / "nested"
        nested.mkdir()
        (nested / "2.png").write_bytes(b"ignored nonrecursive image")

    def test_missing_prefix_duplicates_filter_range_and_nonrecursive(self):
        self.make_number_inventory()
        report = nt.scan_missing_numbers(self.output, extensions=[".png"], min_number=1, max_number=16)
        self.assertTrue(report.completed)
        self.assertTrue(report.missing_checked)
        self.assertEqual(report.scanned_count, 7)
        self.assertEqual(report.numbered_count, 5)
        self.assertEqual(report.unique_count, 4)
        self.assertEqual(report.missing_count, 13)
        self.assertEqual([tuple(pair) for pair in report.missing_ranges], [(2, 2), (4, 14), (16, 16)])
        self.assertEqual(len(report.duplicates[15]), 2)
        self.assertIn(22, report.out_of_range)
        self.assertEqual({Path(name).name for name in report.unnumbered}, {"-1.png", "abc.png"})

    def test_missing_scan_does_not_open_or_decode_image_contents(self):
        self.make_number_inventory()
        with mock.patch("PIL.Image.open", side_effect=AssertionError("Image decoding is forbidden for Missing No.")):
            report = nt.scan_missing_numbers(self.output, extensions=["png"], min_number=1, max_number=3)
        self.assertEqual(report.missing_count, 1)
        self.assertEqual([tuple(pair) for pair in report.missing_ranges], [(2, 2)])

    def test_huge_missing_range_stays_compressed(self):
        maximum = 10**12
        for number in (1, maximum):
            (self.output / f"{number}.png").write_bytes(b"opaque")
        report = nt.scan_missing_numbers(self.output, min_number=1, max_number=maximum)
        self.assertTrue(report.completed)
        self.assertEqual(report.missing_count, maximum - 2)
        self.assertEqual([tuple(pair) for pair in report.missing_ranges], [(2, maximum - 1)])

    def test_no_bounds_is_inventory_without_inferred_missing_range(self):
        self.make_number_inventory()
        report = nt.scan_missing_numbers(self.output)
        self.assertTrue(report.completed)
        self.assertFalse(report.missing_checked)
        self.assertIsNone(report.missing_count)
        self.assertEqual(report.missing_ranges, [])

    def test_empty_folder_reports_entire_expected_range_missing(self):
        report = nt.scan_missing_numbers(self.output, min_number=1, max_number=5)
        self.assertTrue(report.completed)
        self.assertEqual(report.missing_count, 5)
        self.assertEqual([tuple(pair) for pair in report.missing_ranges], [(1, 5)])

    def test_invalid_missing_ranges_are_rejected(self):
        for arguments in ({"min_number": 1}, {"max_number": 2}, {"min_number": 5, "max_number": 3}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    nt.scan_missing_numbers(self.output, **arguments)

    def test_blank_scan_folder_does_not_silently_scan_working_directory(self):
        with self.assertRaises(ValueError):
            nt.scan_missing_numbers("")

    def test_cancelled_scan_never_claims_a_complete_missing_result(self):
        self.make_number_inventory()
        cancelled = threading.Event()
        cancelled.set()
        report = nt.scan_missing_numbers(self.output, min_number=1, max_number=30, cancel_event=cancelled)
        self.assertTrue(report.cancelled)
        self.assertFalse(report.completed)
        self.assertFalse(report.missing_checked)
        self.assertIsNone(report.missing_count)


if __name__ == "__main__":
    unittest.main()
