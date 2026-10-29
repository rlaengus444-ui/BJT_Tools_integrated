import hashlib
import math
from pathlib import Path
import struct
import tempfile
import threading
import unittest

import numpy as np

from bjt_kernel_support import load_definitions

SOURCE = Path(__file__).resolve().parents[1] / "sources" / "260914_BJT_model_to_lattice_STL_all_in_one.py"
KERNEL = load_definitions(SOURCE, ["CancelledError", "check_cancel", "binary_stl_stats"], {
    "np": np, "hashlib": hashlib, "struct": struct,
})
stats = KERNEL["binary_stl_stats"]
RECORD = struct.Struct("<12fH")


def record(a, b, c, normal=(0., 0., 1.)):
    return RECORD.pack(*normal, *a, *b, *c, 0)


def stl(records, count=None, trailing=b""):
    return bytes(80) + struct.pack("<I", len(records) if count is None else count) + b"".join(records) + trailing


def oracle(data):
    count = struct.unpack_from("<I", data, 80)[0]
    mins, maxs = [math.inf] * 3, [-math.inf] * 3
    degenerate = 0
    for i in range(count):
        v = RECORD.unpack_from(data, 84 + i * 50)
        a, b, c = v[3:6], v[6:9], v[9:12]
        for p in (a, b, c):
            for j in range(3):
                mins[j] = min(mins[j], p[j])
                maxs[j] = max(maxs[j], p[j])
        ab = [b[j] - a[j] for j in range(3)]
        ac = [c[j] - a[j] for j in range(3)]
        cross = [ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]]
        degenerate += sum(x*x for x in cross) <= 1e-24
    return count, mins, maxs, degenerate


class BinaryStlStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "모델.stl"

    def write(self, data):
        self.path.write_bytes(data)
        return self.path

    def assert_matches_oracle(self, data):
        out = stats(self.write(data))
        count, mins, maxs, degenerate = oracle(data)
        self.assertEqual(out["path"], str(self.path))
        self.assertEqual(out["triangles"], count)
        self.assertEqual(out["bytes"], len(data))
        self.assertEqual(out["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(out["bounds_min"], mins)
        self.assertEqual(out["bounds_max"], maxs)
        self.assertEqual(out["extents"], [hi - lo for lo, hi in zip(mins, maxs)])
        self.assertEqual(out["degenerate_triangles"], degenerate)

    def test_geometry_and_threshold(self):
        records = [record((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                   record((-2, 3, 4), (-2, 3, 4), (5, -6, 7)),
                   record((0, 0, 0), (1, 0, 0), (0, 0.999e-12, 0)),
                   record((0, 0, 0), (1, 0, 0), (0, 1.001e-12, 0))]
        self.assert_matches_oracle(stl(records))
        self.assertEqual(stats(self.path)["degenerate_triangles"], 2)

    def test_threshold_adjacent_float32_values(self):
        center = np.float32(1e-12)
        values = [np.nextafter(center, np.float32(-np.inf)), center,
                  np.nextafter(center, np.float32(np.inf))]
        data = stl([record((0, 0, 0), (1, 0, 0), (0, value, 0)) for value in values])
        self.assert_matches_oracle(data)
        self.assertEqual(stats(self.path)["degenerate_triangles"],
                         sum(float(value) ** 2 <= 1e-24 for value in values))
        self.assertEqual(stats(self.path)["degenerate_triangles"], 2)

    def test_seeded_float32_matches_baseline_formula(self):
        rng = np.random.default_rng(20261028)
        vertices = rng.normal(size=(5000, 3, 3)).astype(np.float32)
        vertices[::79, 2] = vertices[::79, 0]
        data = stl([record(*triangle) for triangle in vertices])
        actual = stats(self.write(data))
        # Frozen baseline implementation's concat, promotion, and degeneracy formula.
        a, b, c = (vertices[:, i] for i in range(3))
        pts = np.concatenate((a, b, c), axis=0).astype(np.float64, copy=False)
        ab = b.astype(np.float64) - a.astype(np.float64)
        ac = c.astype(np.float64) - a.astype(np.float64)
        cross = np.cross(ab, ac)
        self.assertEqual(actual["bounds_min"], pts.min(axis=0).tolist())
        self.assertEqual(actual["bounds_max"], pts.max(axis=0).tolist())
        self.assertEqual(actual["degenerate_triangles"],
                         int(np.count_nonzero(np.einsum("ij,ij->i", cross, cross) <= 1e-24)))
        self.assertEqual(actual["sha256"], hashlib.sha256(data).hexdigest())

    def test_signed_zero_bounds_match_baseline(self):
        vertices = np.array([[[0., -0., 0.], [-0., 0., -0.],
                              [0., -0., 0.]]], dtype=np.float32)
        actual = stats(self.write(stl([record(*vertices[0])])))
        pts = np.concatenate(tuple(vertices[:, i] for i in range(3)), axis=0).astype(np.float64)
        for key, baseline in (("bounds_min", pts.min(axis=0)), ("bounds_max", pts.max(axis=0))):
            self.assertEqual(np.signbit(actual[key]).tolist(), np.signbit(baseline).tolist())

    def test_empty(self):
        self.assert_matches_oracle(stl([]))

    def test_multi_chunk(self):
        tri = record((0, 0, 0), (1, 0, 0), (0, 1, 0))
        last = record((-5, 0, 0), (-5, 0, 0), (0, 0, 0))
        data = bytes(80) + struct.pack("<I", 200001) + tri * 200000 + last
        out = stats(self.write(data))
        self.assertEqual(out["triangles"], 200001)
        self.assertEqual(out["bounds_min"], [-5., 0., 0.])
        self.assertEqual(out["degenerate_triangles"], 1)
        self.assertEqual(out["sha256"], hashlib.sha256(data).hexdigest())

    def test_nonfinite_normals_and_vertices(self):
        for x in (float("nan"), float("inf"), -float("inf")):
            for tri in (record((0, 0, 0), (1, 0, 0), (0, 1, 0), (x, 0, 0)),
                        record((0, 0, 0), (1, x, 0), (0, 1, 0))):
                with self.subTest(x=x, tri=tri), self.assertRaises(ValueError):
                    stats(self.write(stl([tri])))

    def test_malformed_truncated_and_trailing(self):
        tri = record((0, 0, 0), (1, 0, 0), (0, 1, 0))
        for data in (b"", bytes(83), stl([tri], count=2), stl([tri])[:-1], stl([tri], trailing=b"x"), stl([], trailing=b"x")):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                stats(self.write(data))

    def test_cancelled_and_empty_does_not_check_cancellation(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(KERNEL["CancelledError"]):
            stats(self.write(stl([record((0, 0, 0), (1, 0, 0), (0, 1, 0))])), event)
        self.assertEqual(stats(self.write(stl([])), event)["triangles"], 0)


class KernelSupportTests(unittest.TestCase):
    def test_preserves_python_decorators_and_uses_last_definition(self):
        source = """
def value():
    return 1

def value():
    return 2

class Example:
    @staticmethod
    @njit(cache=True)
    def result():
        return value()

    @property
    def answer(self):
        return self.result()
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(source, encoding="utf-8")
            loaded = load_definitions(path, ["value", "Example"])
            self.assertEqual(loaded["Example"]().answer, 2)


if __name__ == "__main__":
    unittest.main()
