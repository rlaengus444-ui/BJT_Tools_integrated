"""Behavioral checks for the real STL writer and exact edge verifier."""

import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from bjt_kernel_support import load_definitions, load_embedded_definitions


SOURCE = Path(__file__).resolve().parents[1] / "sources" / "260914_BJT_model_to_lattice_STL_all_in_one.py"
NAMES = [
    "CancelledError", "check_cancel", "_AIO_FAST_EXACT_RECORD_DTYPE",
    "_aio_fast_writer_check_limit", "_aio_fast_write_triangle_soup",
    "_BJT_EDGE_DTYPE", "_BJT_EDGE_FIELDS", "_bjt_edges_and_buckets",
    "_bjt_edge_counts", "_bjt_native_edge_backend", "_bjt_verify_edges", "_bjt_write_grid_triangle_soup",
]
KERNEL = load_definitions(SOURCE, NAMES, {
    "np": np, "Path": Path, "tempfile": tempfile, "shutil": shutil, "__file__": str(SOURCE),
    "format_bytes": str,
})
CORE = load_embedded_definitions(SOURCE, "_RECON_CORE_B64", ["CancelledError", "check_cancel", "BinarySTLWriter"], {
    "np": np, "Path": Path, "struct": struct, "os": os,
})
KERNEL["recon_core"] = SimpleNamespace(check_cancel=CORE["check_cancel"])
WRITER = CORE["BinarySTLWriter"]
if os.environ.get("BJT_TEST_NATIVE_NUMBA") == "1":
    from numba import njit
    for name in ("_bjt_edges_and_buckets", "_bjt_edge_counts"):
        KERNEL[name] = njit(cache=False, nogil=True)(KERNEL[name])
RECORD = struct.Struct("<12fH")
METRICS = ("boundary_edges", "nonmanifold_edges", "inconsistent_winding_edges", "unique_edges", "voxel_contact_edges")


def expected_metrics(boundary=0, nonmanifold=0, winding=0, unique=18, contacts=0):
    return dict(zip(METRICS, (boundary, nonmanifold, winding, unique, contacts)))


def cube(offset=(0, 0, 0)):
    vertices = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float32) + np.asarray(offset, dtype=np.float32)
    faces = np.array([
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
        [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
    ])
    return vertices[faces]


class TriangleSoupWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "메시.stl"
        self.triangles = np.array([
            [[-0., 0., 0.], [1.25, 0., 0.], [0., 2.5, 0.]],
            [[-2., 0., 0.], [-2., 1., 0.], [-2., 0., 1.]],
            [[0., 0., 0.], [0., 0., 1.], [1., 0., 0.]],
            [[0., 0., 0.], [0., 1., 0.], [1., 0., 0.]],
            [[1., 2., 3.], [1., 2., 3.], [1., 2., 3.]],
        ], dtype=np.float64)

    def soup_bytes(self):
        normals = [(0., 0., 1.), (1., 0., 0.), (0., 1., 0.), (0., 0., -1.), (0., 0., 0.)]
        return b"".join(RECORD.pack(*normal, *tri.astype(np.float32).reshape(-1), 0)
                        for normal, tri in zip(normals, self.triangles))

    def test_exact_bytes_and_scalar_buffer_order(self):
        scalar = RECORD.pack(0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0)
        strided = np.repeat(self.triangles, 2, axis=0)[::2]
        mapped = np.memmap(Path(self.temp.name) / "input.dat", mode="w+", dtype=np.float64, shape=self.triangles.shape)
        mapped[:] = self.triangles
        self.addCleanup(mapped._mmap.close)
        for data in (self.triangles, self.triangles.astype(np.float32), strided,
                     mapped, self.triangles.tolist(), tuple(self.triangles.tolist())):
            for chunk in (1, 2, 120_000, 0, -3, 2.9, "2"):
                with self.subTest(dtype=type(data), chunk=chunk):
                    writer = WRITER(self.path, header="fixture")
                    try:
                        writer.write_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
                        count = KERNEL["_aio_fast_write_triangle_soup"](writer, data, chunk_size=chunk)
                        writer.write_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
                        self.assertEqual(count, 5)
                        self.assertEqual(writer.tri_count, 7)
                        writer.close(True)
                    finally:
                        writer.close(False)
                    content = self.path.read_bytes()
                    self.assertEqual(struct.unpack_from("<I", content, 80)[0], 7)
                    self.assertEqual(content[84:], scalar + self.soup_bytes() + scalar)
                    self.assertFalse(writer.temp_path.exists())

    def test_default_chunk_boundary(self):
        triangles = np.repeat(self.triangles[:1], 120_001, axis=0)
        triangles[-1] = self.triangles[1]
        writer = WRITER(self.path)
        try:
            self.assertEqual(KERNEL["_aio_fast_write_triangle_soup"](writer, triangles), 120_001)
            writer.close(True)
        finally:
            writer.close(False)
        content = self.path.read_bytes()
        expected = self.soup_bytes()
        self.assertEqual(struct.unpack_from("<I", content, 80)[0], 120_001)
        self.assertEqual(content[84:], expected[:50] * 120_000 + expected[50:100])

    def test_sequence_is_only_converted_after_slicing(self):
        triangles = self.triangles

        class SliceOnly:
            def __init__(self):
                self.sizes = []

            def __len__(self):
                return len(triangles)

            def __array__(self, *args, **kwargs):
                raise AssertionError("whole input conversion")

            def __getitem__(self, key):
                if not isinstance(key, slice):
                    raise AssertionError("expected a bounded slice")
                result = triangles[key]
                self.sizes.append(len(result))
                return result

        data = SliceOnly()
        writer = WRITER(self.path)
        try:
            self.assertEqual(KERNEL["_aio_fast_write_triangle_soup"](writer, data, chunk_size=2), 5)
            writer.close(True)
        finally:
            writer.close(False)
        self.assertEqual(data.sizes, [2, 2, 1])
        self.assertEqual(self.path.read_bytes()[84:], self.soup_bytes())

    def test_empty_preserves_buffer_and_skips_cancellation(self):
        writer = WRITER(self.path)
        event = threading.Event()
        event.set()
        try:
            writer.write_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
            before = bytes(writer._buffer)
            self.assertEqual(KERNEL["_aio_fast_write_triangle_soup"](writer, [], cancel_event=event), 0)
            self.assertEqual(bytes(writer._buffer), before)
            self.assertEqual(writer.tri_count, 1)
        finally:
            writer.close(False)

    def test_limit_and_uint32_checks_precede_writes(self):
        for count, maximum, error in ((1, 5, RuntimeError), (0xFFFFFFFF, None, OverflowError)):
            with self.subTest(count=count):
                writer = WRITER(self.path)
                writer.tri_count = count
                try:
                    position = writer.f.tell()
                    with self.assertRaises(error):
                        KERNEL["_aio_fast_write_triangle_soup"](writer, self.triangles, max_triangles=maximum)
                    self.assertEqual(writer.f.tell(), position)
                    self.assertEqual(writer.tri_count, count)
                finally:
                    writer.close(False)

    def test_cancellation_removes_part_and_preserves_existing_file(self):
        self.path.write_bytes(b"existing")
        writer = WRITER(self.path)
        event = threading.Event()
        checks = []
        original = KERNEL["recon_core"]

        def cancel_after_one_chunk(cancel_event):
            checks.append(True)
            if len(checks) == 2:
                cancel_event.set()
            CORE["check_cancel"](cancel_event)

        KERNEL["recon_core"] = SimpleNamespace(check_cancel=cancel_after_one_chunk)
        try:
            with self.assertRaises(CORE["CancelledError"]):
                KERNEL["_aio_fast_write_triangle_soup"](writer, self.triangles, cancel_event=event, chunk_size=2)
            self.assertEqual(writer.tri_count, 0)
        finally:
            KERNEL["recon_core"] = original
            writer.close(False)
        self.assertEqual(len(checks), 2)
        self.assertFalse(writer.temp_path.exists())
        self.assertEqual(self.path.read_bytes(), b"existing")


class ExactEdgeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.stl"

    def verify(self, triangles, budget=1_000_000, backend="python"):
        writer = WRITER(self.path)
        try:
            KERNEL["_aio_fast_write_triangle_soup"](writer, triangles)
            writer.close(True)
        finally:
            writer.close(False)
        with np.errstate(over="ignore"):
            return KERNEL["_bjt_verify_edges"](self.path, len(triangles), threading.Event(), budget, backend=backend)

    def test_topology_metrics_in_memory_and_on_disk(self):
        closed = cube()
        reversed_face = closed.copy()
        reversed_face[0] = reversed_face[0, [0, 2, 1]]
        triple = np.array([
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            [[1, 0, 0], [0, 0, 0], [0, 0, 1]],
            [[0, 0, 0], [1, 0, 0], [0, -1, 0]],
        ], dtype=np.float32)
        touching = np.concatenate((closed, cube((1, 1, 0))))
        nearby = np.concatenate((closed, cube((np.nextafter(np.float32(1), np.float32(2)), 1, 0))))
        signed = closed.copy()
        signed[signed == 0] = -0.0
        cases = [
            (closed, expected_metrics()),
            (closed[:-1], expected_metrics(boundary=3)),
            (reversed_face, expected_metrics(winding=3)),
            (np.concatenate((closed, closed[:1])), expected_metrics(nonmanifold=3, winding=3)),
            (triple, expected_metrics(boundary=6, nonmanifold=1, winding=1, unique=7)),
            (touching, expected_metrics(nonmanifold=1, unique=35, contacts=1)),
            (nearby, expected_metrics(unique=36)),
            (signed, expected_metrics()),
            (cube((-3, -2, -1)), expected_metrics()),
        ]
        for index, (triangles, expected) in enumerate(cases):
            for budget in (1_000_000, 256):
                with self.subTest(case=index, budget=budget):
                    self.assertEqual(self.verify(triangles, budget), expected)

    def test_multiple_input_chunks(self):
        triangles = np.zeros((1001, 3, 3), dtype=np.float32)
        triangles[:, :, 0] = np.arange(1001, dtype=np.float32)[:, None] * 3
        triangles[:, 1, 0] += 1
        triangles[:, 2, 1] = 1
        self.assertEqual(self.verify(triangles, budget=80_000), expected_metrics(boundary=3003, unique=3003))
        # The last triangle belongs to the second input chunk and closes the
        # first triangle's three edges with the opposite winding.
        triangles[-1] = triangles[0, [0, 2, 1]]
        self.assertEqual(self.verify(triangles, budget=80_000), expected_metrics(boundary=2997, unique=3000))

    def test_single_input_chunk_reuses_rows(self):
        with patch.object(np, "concatenate", side_effect=AssertionError("unnecessary row copy")):
            self.assertEqual(self.verify(cube()), expected_metrics())

    def test_sort_keys_are_zero_copy_and_keep_exact_order(self):
        original_lexsort = np.lexsort
        original_edges = KERNEL["_bjt_edges_and_buckets"]
        observed = []

        def lexsort(keys):
            base = keys[0].base
            self.assertEqual(base.dtype, KERNEL["_BJT_EDGE_DTYPE"])
            for key in keys:
                self.assertEqual(key.strides, (32,))
                self.assertTrue(np.shares_memory(base, key))
            old_words = np.column_stack([base[name] for name in KERNEL["_BJT_EDGE_FIELDS"]]).view(np.uint64).reshape(-1, 3)
            expected = original_lexsort((old_words[:, 2], old_words[:, 1], old_words[:, 0]))
            actual = original_lexsort(keys)
            np.testing.assert_array_equal(actual, expected)
            observed.append(True)
            return actual

        with patch.object(np, "lexsort", lexsort):
            triangles = np.concatenate((cube((-3, 2, -1)), cube((-3, 2, -1)), cube((1, 1, 0))))
            self.verify(triangles)

            def random_edges(bits, rows, ids, mask):
                rng = np.random.default_rng(7)
                for name in KERNEL["_BJT_EDGE_FIELDS"]:
                    rows[name] = rng.integers(0, 2**32, len(rows), dtype=np.uint32)
                rows["direction"] = 1
                rows["contact"] = 0
                rows[1::3] = rows[::3][:len(rows[1::3])]
                ids[:] = 0

            try:
                KERNEL["_bjt_edges_and_buckets"] = random_edges
                self.verify(cube())
            finally:
                KERNEL["_bjt_edges_and_buckets"] = original_edges
        self.assertEqual(observed, [True, True])

    def test_cancel_truncated_and_oversized_bucket(self):
        self.verify(cube())
        event = threading.Event()
        event.set()
        with self.assertRaises(KERNEL["CancelledError"]):
            KERNEL["_bjt_verify_edges"](self.path, 12, event, 256)
        with np.errstate(over="ignore"), self.assertRaises(MemoryError):
            KERNEL["_bjt_verify_edges"](self.path, 12, threading.Event(), 1)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            KERNEL["_bjt_verify_edges"](self.path, 12, threading.Event(), 256)

    def test_layout_guard(self):
        original = KERNEL["_BJT_EDGE_DTYPE"]
        KERNEL["_BJT_EDGE_DTYPE"] = np.dtype([(f"p{i}", "<u4") for i in range(6)] + [("direction", "<i4")])
        try:
            with self.assertRaises(TypeError):
                KERNEL["_bjt_verify_edges"](self.path, 1, None, 1000)
        finally:
            KERNEL["_BJT_EDGE_DTYPE"] = original

    def test_insufficient_temporary_disk(self):
        original = KERNEL["shutil"]
        KERNEL["shutil"] = SimpleNamespace(disk_usage=lambda path: SimpleNamespace(free=0))
        try:
            with self.assertRaises(OSError):
                KERNEL["_bjt_verify_edges"](self.path, 12, threading.Event(), 256)
        finally:
            KERNEL["shutil"] = original


if __name__ == "__main__":
    unittest.main()
