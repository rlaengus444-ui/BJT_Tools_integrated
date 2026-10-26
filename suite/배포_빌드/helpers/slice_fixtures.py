"""Behavior regression suite for the four slice applications.

The public run_suite(modules, destination) can also be called with the unified
program's loaded modules.  Tests write only below destination, never user input.
No GUI or installer is executed by this suite.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import queue
import sys
import threading
import traceback
import uuid
import zipfile

import numpy as np
from PIL import Image


ORIGINALS = {
    "exchanger": Path(r"C:\Users\Kain\PycharmProjects\shellprinting\slice exchanger.py"),
    "overlay": Path(r"C:\Users\Kain\PycharmProjects\shellprinting\slice_folder_overlay.py"),
    "arrange": Path(r"C:\Users\Kain\PycharmProjects\shellprinting\slicing arrange.py"),
    "alignment": Path(r"C:\Users\Kain\PycharmProjects\slicing checker\slice_alignment_checker.py"),
}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location("regression_" + name + "_" + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _image(path, array):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(array, dtype=np.uint8)).save(path)
    return path


def _same(actual, expected):
    assert np.array_equal(np.asarray(actual), np.asarray(expected)), "Image pixels differ"


def _raises(kind, callback):
    try:
        callback()
    except kind:
        return
    raise AssertionError("Expected " + kind.__name__)


class MemoryLog:
    def __init__(self):
        self.lines, self.steps = [], []

    def write(self, value):
        self.lines.append(str(value))

    def progress(self, value, status):
        self.steps.append((value, status))


def run_suite(modules, destination, case_prefix=None):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    ex, ov, ar, al = (modules[name] for name in ORIGINALS)
    results = []

    def case(name, callback):
        if case_prefix is not None and not name.startswith(case_prefix):
            return
        folder = destination / name
        folder.mkdir()
        try:
            callback(folder)
        except Exception:
            results.append({"name": name, "passed": False, "error": traceback.format_exc()})
        else:
            results.append({"name": name, "passed": True})

    def exchange_success(root):
        aa, bb = root / "target", root / "source"
        for n in range(1, 4):
            _image(aa / f"{n:03}.png", np.full((8, 9), n, np.uint8))
            _image(bb / f"{n + 9}.png", np.full((8, 9), n + 50, np.uint8))
        before = {p.name: p.read_bytes() for p in aa.iterdir()}
        ex.safe_replace_images(aa, bb, 2, 3, 10, 11, lambda _: None)
        assert (aa / "001.png").read_bytes() == before["001.png"]
        assert (aa / "002.png").read_bytes() == (bb / "10.png").read_bytes()
        assert (aa / "003.png").read_bytes() == (bb / "11.png").read_bytes()
        backups = list(aa.glob("_backup_before_replace_*"))
        assert len(backups) == 1
        assert {p.name: p.read_bytes() for p in backups[0].iterdir()} == {n: before[n] for n in ("002.png", "003.png")}

    def exchange_mismatch(root):
        aa, bb = root / "target", root / "source"
        target = _image(aa / "1.png", np.zeros((5, 5), np.uint8))
        _image(bb / "1.png", np.ones((6, 5), np.uint8))
        original = target.read_bytes()
        _raises(RuntimeError, lambda: ex.safe_replace_images(aa, bb, 1, 1, 1, 1, lambda _: None))
        assert target.read_bytes() == original
        assert not list(aa.glob("_backup*"))

    def exchange_rollback(root):
        aa, bb = root / "target", root / "source"
        target = _image(aa / "1.png", np.zeros((5, 5), np.uint8))
        _image(aa / "3.png", np.zeros((5, 5), np.uint8))  # Gap detected after replacement.
        _image(bb / "1.png", np.full((5, 5), 100, np.uint8))
        original = target.read_bytes()
        logs = []
        _raises(RuntimeError, lambda: ex.safe_replace_images(aa, bb, 1, 1, 1, 1, logs.append))
        assert target.read_bytes() == original
        assert any("자동복구 완료" in line for line in logs)

    def exchange_numbering(root):
        _image(root / "01.png", np.zeros((3, 3), np.uint8))
        _image(root / "1.bmp", np.zeros((3, 3), np.uint8))
        _raises(RuntimeError, lambda: ex.build_number_file_map(root))
        _raises(RuntimeError, lambda: ex.check_missing_numbers({1: None, 3: None}, 1, 3, "test"))
        _raises(RuntimeError, lambda: ex.check_folder_continuity_all({}, "test"))
        assert ex.parse_drop_path(r"{C:\two words\input} {C:\next}") == r"C:\two words\input"

    case("exchange_success_and_backup", exchange_success)
    case("exchange_format_rejection", exchange_mismatch)
    case("exchange_automatic_rollback", exchange_rollback)
    case("exchange_number_validation", exchange_numbering)

    a = np.array([[0, 90, 170], [250, 129, 40]], np.uint8)
    b = np.array([[40, 120, 160], [20, 0, 240]], np.uint8)
    for mode, expected in [
        ("grayscale max", [[40, 120, 170], [250, 129, 240]]),
        ("grayscale min", [[0, 90, 160], [20, 0, 40]]),
        ("add clamp", [[40, 210, 255], [255, 129, 255]]),
        ("binary union", [[7, 7, 201], [201, 201, 201]]),
    ]:
        case("overlay_" + mode.replace(" ", "_"), lambda root, mode=mode, expected=expected: _same(
            ov.overlay_arrays([a, b], mode, 128, True, 7, 201), expected))
    case("overlay_black_material", lambda root: _same(
        ov.overlay_arrays([a, b], "binary union", 10, False, 17, 222), [[222, 17, 17], [17, 222, 17]]))

    def overlay_sizes(root):
        small = np.array([[1, 2]], np.uint8)
        large = np.array([[3, 4, 5], [6, 7, 8]], np.uint8)
        _raises(ValueError, lambda: ov.align_arrays([small, large], "오류", 0))
        padded = ov.align_arrays([small, large], "작은 이미지 패딩", 10)
        _same(padded[0], [[1, 2, 10], [10, 10, 10]])
        _same(padded[1], large)
        cropped = ov.align_arrays([small, large], "큰 이미지 크롭", 0)
        _same(cropped[0], small)
        _same(cropped[1], [[3, 4]])

    def overlay_match(root):
        files = [[Path("a/2.png"), Path("a/10.png")], [Path("b/10.png"), Path("b/2.png")]]
        jobs = ov.build_jobs(files, "파일명 기준", "첫 번째 폴더 파일명")
        assert [name for _, name in jobs] == ["2.png", "10.png"]
        assert [p.name for p in jobs[0][0]] == ["2.png", "2.png"]
        jobs = ov.build_jobs(files, "정렬 순서 기준", "순번")
        assert [name for _, name in jobs] == ["1.png", "2.png"]
        assert [p.name for p in jobs[0][0]] == ["2.png", "10.png"]
        _raises(ValueError, lambda: ov.build_jobs([[Path("a.png")], [Path("b.png")]], "파일명 기준", "순번"))

    def overlay_files(root):
        folders = [root / "first", root / "second"]
        for n in (10, 2):
            _image(folders[0] / f"{n}.png", a)
            _image(folders[1] / f"{n}.png", b)
        params = ov.Params(folders, root / "out", "파일명 기준", "grayscale max", "오류", 128, True, 0, 255, "순번", False)
        log = MemoryLog()
        ov.run_overlay(params, log, threading.Event())
        assert sorted(p.name for p in params.output_dir.iterdir()) == ["1.png", "2.png"]
        _same(Image.open(params.output_dir / "1.png"), np.maximum(a, b))
        assert log.steps[-1] == (100, "complete")
        _raises(FileExistsError, lambda: ov.run_overlay(params, MemoryLog(), threading.Event()))
        event = threading.Event()
        event.set()
        _raises(ov.CancelledError, lambda: ov.run_overlay(params, MemoryLog(), event))

    case("overlay_sizes_and_clipping", overlay_sizes)
    case("overlay_file_matching", overlay_match)
    case("overlay_file_output_cancel_overwrite", overlay_files)

    def compose(root):
        first = _image(root / "source" / "1.png", [[1, 2, 3], [4, 5, 6]])
        second = _image(root / "other" / "1.png", [[200, 201], [202, 203]])
        sources = {"a": ar.SourceSet("a", first.parent, [first]), "b": ar.SourceSet("b", second.parent, [second])}
        placements = [ar.Placement("p1", "a", "a", -1, 1), ar.Placement("p2", "b", "b", 1, 2),
                      ar.Placement("p3", "a", "hidden", 0, 0, False), ar.Placement("p4", "a", "rotated", 3, 0, True, 90)]
        expected = np.array([[0, 0, 0, 4, 1, 0], [2, 3, 0, 5, 2, 0], [5, 200, 201, 6, 3, 0], [0, 202, 203, 0, 0, 0]], np.uint8)
        _same(ar.compose_layer((6, 4), sources, placements, 0, "max"), expected)
        native = ar.np
        try:
            ar.np = None
            _same(ar.compose_layer((6, 4), sources, placements, 0, "max"), expected)
        finally:
            ar.np = native
        _same(ar.compose_layer((6, 4), sources, placements, 1, "max"), np.zeros((4, 6), np.uint8))
        p = [ar.Placement("a", "a", "a"), ar.Placement("b", "b", "b")]
        _same(ar.compose_layer((3, 2), sources, p, 0, "overwrite"), [[200, 201, 3], [202, 203, 6]])
        assert ar.output_names_from_sources(sources, p, 3) == ["1.png", "2.png", "3.png"]

    def alpha(root):
        p = _image(root / "alpha.png", [[[255, 255, 255, 255], [255, 255, 255, 0], [255, 0, 0, 128]]])
        _same(ar.load_grayscale(p), [[255, 0, 38]])
        _same(ar.load_grayscale_array(p), [[255, 0, 38]])

    def arbitrary_rotations(root):
        image = Image.fromarray(a)
        for angle in (-90, 0, 45, 90, 180, 270, 360, 725):
            _same(ar.rotate_grayscale_array(a, angle), ar.rotate_grayscale(image, angle))
        assert ar.clipped_box(10, 10, 4, 4, 11, 11) is None
        assert ar.ceil_with_tolerance(10.0000001) == 10
        assert ar.common_folder_title(["sample_001", "sample_002"]) == "sample"

    def save_formats(root):
        image = Image.fromarray(np.pad(np.full((10, 10), 255, np.uint8), 5))
        for suffix in ("png", "bmp", "tif", "jpg", "webp"):
            p = root / ("out." + suffix)
            ar.save_merged_image(image, p, (400, 500))
            with Image.open(p) as loaded:
                assert loaded.size == image.size
                if suffix != "jpg":
                    _same(loaded.convert("L"), image)
        ar.save_merged_image(image, root / "fallback.unknown", (400, 400))
        assert (root / "fallback.png").is_file()

    def stl_cache(root):
        assert ar.trimesh is not None, "trimesh dependency required for STL regression"
        path = root / "box.stl"
        ar.trimesh.creation.box(extents=[2.54, 1.27, 0.3]).export(path)
        native = ar.get_stl_cache_root
        try:
            ar.get_stl_cache_root = lambda: root / "cache"
            result = ar.slice_stl_to_cache(path, 100, 100, 0.1)
            assert result["layer_count"] == 3
            assert (result["image_width"], result["image_height"]) == (10, 5)
            assert not result["cached"]
            pixels = [np.asarray(Image.open(p)).copy() for p in result["images"]]
            assert all(np.count_nonzero(v) > 0 for v in pixels)
            again = ar.slice_stl_to_cache(path, 100, 100, 0.1)
            assert again["cached"]
            for i, p in enumerate(again["images"]):
                _same(Image.open(p), pixels[i])
            _raises(ValueError, lambda: ar.slice_stl_to_cache(path, 0, 100, 0.1))
        finally:
            ar.get_stl_cache_root = native

    def export_layers(root):
        first = [_image(root / "a" / f"{n}.png", np.full((4, 6), n, np.uint8)) for n in (1, 2, 3)]
        second = [_image(root / "b" / "1.png", np.full((4, 6), 170, np.uint8))]
        sources = {"a": ar.SourceSet("a", first[0].parent, first), "b": ar.SourceSet("b", second[0].parent, second)}
        placements = [ar.Placement("a", "a", "a"), ar.Placement("b", "b", "b", 3, 0)]
        for mode, count in (("min", 1), ("max", 3)):
            app = ar.SliceLayoutApp.__new__(ar.SliceLayoutApp)
            app.export_cancel = threading.Event()
            app.export_queue = queue.Queue()
            target = root / mode
            app._export_worker((12, 8), (400, 400), sources, placements, target, mode, "max")
            events = []
            while not app.export_queue.empty():
                events.append(app.export_queue.get_nowait())
            assert events[-1][0] == "finished", events
            assert len(list(target.glob("*.png"))) == count
            for index in range(count):
                _same(Image.open(target / f"{index + 1}.png"), ar.compose_layer((12, 8), sources, placements, index, "max"))
        app = ar.SliceLayoutApp.__new__(ar.SliceLayoutApp)
        app.export_cancel = threading.Event()
        app.export_cancel.set()
        app.export_queue = queue.Queue()
        app._export_worker((12, 8), (400, 400), sources, placements, root / "cancelled", "max", "max")
        assert not list((root / "cancelled").glob("*.png"))
        events = []
        while not app.export_queue.empty():
            events.append(app.export_queue.get_nowait())
        assert events[-1][0] == "cancelled", events

    case("arrange_compose_clip_hide_rotate_fallback", compose)
    case("arrange_alpha_compositing", alpha)
    case("arrange_rotations_geometry_naming", arbitrary_rotations)
    case("arrange_output_formats", save_formats)
    case("arrange_stl_slicing_and_cache", stl_cache)
    case("arrange_parallel_export_length_modes_cancel", export_layers)

    def masks(root):
        old = np.zeros((64, 80), np.uint8)
        old[10:22, 10:25] = 255
        old[32:39, 41:60] = 255
        old[30:33, 41:46] = 255
        same = al.compare_slices(old, old.copy(), max_shift_pixels=8)
        assert same["status"] == "unchanged", same
        shifted = np.roll(old, (3, -2), axis=(0, 1))
        result = al.compare_slices(old, shifted, max_shift_pixels=8)
        assert result["status"] == "shifted", result
        additions = old.copy()
        additions[45:55, 5:11] = 255
        assert al.compare_slices(old, additions, max_shift_pixels=8)["status"] == "unchanged"
        mismatched = al.compare_slices(old, old[:, :-1], max_shift_pixels=8)
        assert mismatched["status"] == "inconclusive"
        assert mismatched["reason"] == "image_dimensions_differ"

    def source_forms(root):
        files = []
        for n in range(3):
            files.append(_image(root / "images" / f"{n}.png", np.full((9, 10), n * 40, np.uint8)))
        archive = root / "layers.zip"
        if globals().get("PREPARED_INPUT_ZIP"):
            archive = Path(PREPARED_INPUT_ZIP)
        else:
            with zipfile.ZipFile(archive, "w") as output:
                for file in reversed(files):
                    output.write(file, "nested/" + file.name)
        for source in (files, root / "images", archive):
            layers = al.LayerSource(source, numbering="auto")
            try:
                assert sorted(layers.entries) == [1, 2, 3]
                _same(layers.load(2), np.full((9, 10), 40, np.uint8))
                read = layers.load(2)
                read[0, 0] = 123
                assert layers.load(2)[0, 0] == 40  # Cached arrays must be independent.
                _same(layers.load(2, "black"), np.full((9, 10), 215, np.uint8))
            finally:
                layers.close()
        _raises(ValueError, lambda: al.LayerSource([files[0], files[0]], numbering="auto"))
        _raises(ValueError, lambda: al.LayerSource([files[0], files[2]], numbering="auto"))

    def scan_reports(root):
        files = []
        shape = np.zeros((64, 80), np.uint8)
        shape[10:20, 20:35] = 255
        shape[30:45, 50:55] = 255
        shape[40:45, 43:55] = 255
        for i in range(7):
            pixels = shape if i < 3 else np.roll(shape, 4, axis=1)
            files.append(_image(root / "images" / f"{i + 1}.png", pixels))
        hashes = [p.read_bytes() for p in files]
        for mode in ("boundary", "full"):
            options = {"output": root / mode}
            if mode == "boundary":
                report = al.run_boundary_check(files, 400, 4, **options)
            else:
                report = al.run_full_check(files, 400, **options)
            assert report["report_html"] and Path(report["report_html"]).is_file()
            assert Path(report["result_dir"]).is_dir()
            assert list(Path(report["result_dir"]).glob("*.csv")), "CSV export missing"
            assert list(Path(report["result_dir"]).glob("*.json")), "JSON export missing"
        assert [p.read_bytes() for p in files] == hashes
        cancel = threading.Event()
        cancel.set()
        _raises(al.CheckCancelled, lambda: al.run_full_check(files, 400, output=root / "cancel", cancel_event=cancel))

    case("alignment_compare_masks_additions_shift", masks)
    case("alignment_folder_zip_list_cache_numbering", source_forms)
    case("alignment_boundary_full_reports_cancel", scan_reports)
    report = {"total": len(results), "passed": sum(r["passed"] for r in results), "cases": results}
    (destination / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    report = run_suite({key: load_module(key, value) for key, value in ORIGINALS.items()}, args.destination)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["total"] == report["passed"] else 1)
