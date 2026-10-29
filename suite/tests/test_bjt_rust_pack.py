"""Byte-exact optional Rust packing of the unchanged ordered grid triangles."""
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bjt_mesh_native as native
import test_bjt_mesh_io as fixtures


@unittest.skipUnless(native.backend_status()["can_pack_grid"], "optional Rust packing feature is absent")
class RustGridPackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = native.get_backend("rust")

    def write(self, q, coord, backend, chunk=120_000, scalar=False):
        path = self.root / f"{backend}.stl"
        writer = fixtures.WRITER(path, header="exact grid packing fixture")
        try:
            if scalar:
                writer.write_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
            count = fixtures.KERNEL["_bjt_write_grid_triangle_soup"](writer, q, coord, chunk_size=chunk, backend=backend)
            if scalar:
                writer.write_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
            self.assertEqual(count, len(q))
            self.assertEqual(writer.tri_count, len(q) + 2 * scalar)
            writer.close(True)
        finally:
            writer.close(False)
        self.assertFalse(writer.temp_path.exists())
        return path.read_bytes()

    def test_random_extreme_scaling_and_signed_zero_bytes(self):
        rng = np.random.default_rng(2926)
        q = rng.integers(-2**25, 2**25, (5003, 3, 3), dtype=np.int64)
        q[:3] = 0
        q[3] = [[0, 0, 0], [1, 1, 1], [2, 2, 2]]
        q[4] = [[2**24 - 1, 2**24, 2**24 + 1], [-2**24 - 1, -2**24, -2**24 + 1], [1, -1, 0]]
        transforms = [(.1, .2, .03, -11.25, 2.75), (2**-24, 2**12, 2**-9, 2**20, -2**20),
                      (-.1, -.2, -.3, 0., -0.), (-0., 0., -0., -0., -0.),
                      (1e-40, 2e-40, 3e-40, 0., 0.), (1e30, 2e30, 3e30, -1e20, 1e20),
                      (1e300, 2e300, 3e300, 1e300, -1e300)]
        for values in transforms:
            coord = SimpleNamespace(**dict(zip(("xsp", "ysp", "zsp", "xoff", "yoff"), values)))
            with self.subTest(transform=values), np.errstate(all="ignore"):
                self.assertEqual(self.write(q, coord, "rust", chunk=997), self.write(q, coord, "python", chunk=997))
        large = rng.integers(-2**62, 2**62, (1001, 3, 3), dtype=np.int64)
        coord = SimpleNamespace(xsp=2**-41, ysp=.3, zsp=2**-9, xoff=-.25, yoff=2**24)
        with np.errstate(all="ignore"):
            self.assertEqual(self.write(large, coord, "rust"), self.write(large, coord, "python"))

    def test_chunk_boundaries_scalar_buffers_and_readonly_input(self):
        q = np.repeat(np.array([[[0, 0, 0], [2, 0, 0], [0, 2, 0]]], np.int64), 120_001, axis=0)
        q[-1] = [[0, 0, 0], [0, 0, 2], [2, 0, 0]]
        q.flags.writeable = False
        coord = SimpleNamespace(xsp=.1, ysp=.2, zsp=.3, xoff=-1., yoff=2.)
        self.assertEqual(self.write(q, coord, "rust", scalar=True), self.write(q, coord, "python", scalar=True))
        for chunk in (0, -2, 1, 3, "2"):
            with self.subTest(chunk=chunk):
                self.assertEqual(self.write(q[:5], coord, "rust", chunk, scalar=True),
                                 self.write(q[:5], coord, "python", chunk, scalar=True))

    def test_invalid_transform_and_capacity_do_not_mutate_output(self):
        q = np.zeros((2, 3, 3), np.int64)
        records = np.empty(2, native.STL_DTYPE)
        records.view("u1")[:] = 0xA5
        before = records.tobytes()
        with self.assertRaises(native.NativeExecutionError):
            self.backend.pack_grid_triangles(q, records, xsp=float("nan"), ysp=1., zsp=1., xoff=0., yoff=0.)
        self.assertEqual(records.tobytes(), before)
        with self.assertRaises(ValueError):
            self.backend.pack_grid_triangles(q, np.empty(1, native.STL_DTYPE), xsp=1., ysp=1., zsp=1., xoff=0., yoff=0.)

    def test_limits_and_cancellation_keep_atomic_writer_contract(self):
        q = np.zeros((5, 3, 3), np.int64)
        coord = SimpleNamespace(xsp=1., ysp=1., zsp=1., xoff=0., yoff=0.)
        path = self.root / "existing.stl"
        path.write_bytes(b"existing")
        for count, maximum, exception in ((1, 5, RuntimeError), (0xFFFFFFFF, None, OverflowError)):
            writer = fixtures.WRITER(path)
            writer.tri_count = count
            try:
                position = writer.f.tell()
                with self.assertRaises(exception):
                    fixtures.KERNEL["_bjt_write_grid_triangle_soup"](writer, q, coord, max_triangles=maximum, backend="rust")
                self.assertEqual(writer.f.tell(), position)
            finally:
                writer.close(False)
        writer = fixtures.WRITER(path)
        event = threading.Event()
        original = fixtures.KERNEL["recon_core"]
        checks = []

        def check(cancel_event):
            checks.append(True)
            if len(checks) == 2:
                cancel_event.set()
            fixtures.CORE["check_cancel"](cancel_event)

        fixtures.KERNEL["recon_core"] = SimpleNamespace(check_cancel=check)
        try:
            with self.assertRaises(fixtures.CORE["CancelledError"]):
                fixtures.KERNEL["_bjt_write_grid_triangle_soup"](writer, q, coord, cancel_event=event, chunk_size=2, backend="rust")
            self.assertEqual(writer.tri_count, 0)
        finally:
            fixtures.KERNEL["recon_core"] = original
            writer.close(False)
        self.assertFalse(writer.temp_path.exists())
        self.assertEqual(path.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
