"""Real optional Rust ABI/count parity, skipped when the binary is absent."""
import ctypes
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bjt_mesh_native as native
import test_bjt_mesh_io as fixtures
from bjt_kernel_support import load_definitions


class BackendDiscoveryConcurrencyTests(unittest.TestCase):
    def test_concurrent_first_use_only_publishes_complete_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / "bjt_mesh_native.py"
            wrapper.write_text("# Controlled test loader replaces execution.\n")
            alias = "_bjt_mesh_native_" + hashlib.sha256(str(wrapper).encode()).hexdigest()[:16]
            helper = load_definitions(fixtures.SOURCE, ["_bjt_native_edge_backend"],
                                      {"Path": Path, "__file__": str(root / "source.py")})["_bjt_native_edge_backend"]
            started, release, lock = threading.Event(), threading.Event(), threading.Lock()
            calls = []

            class Loader:
                def create_module(self, spec):
                    return None

                def exec_module(self, module):
                    with lock:
                        calls.append(True)
                        first = len(calls) == 1
                    if first:
                        started.set()
                        if not release.wait(5):
                            raise TimeoutError("test loader release")
                    module.get_backend = lambda choice: "ready"

            make_spec = importlib.util.spec_from_file_location

            def controlled_spec(name, path):
                return make_spec(name, path, loader=Loader())

            try:
                with patch.object(importlib.util, "spec_from_file_location", controlled_spec):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        first = pool.submit(helper, "rust")
                        try:
                            self.assertTrue(started.wait(5))
                            second = pool.submit(helper, "rust")
                            self.assertEqual(second.result(timeout=5), "ready")
                        finally:
                            release.set()
                        self.assertEqual(first.result(timeout=5), "ready")
                self.assertEqual(len(calls), 2)
                self.assertEqual(sys.modules[alias].get_backend("rust"), "ready")
            finally:
                sys.modules.pop(alias, None)


@unittest.skipUnless(native.backend_status()["available"], "optional Rust library is absent")
class RustEdgeKernelTests(unittest.TestCase):
    def setUp(self):
        self.backend = native.get_backend("rust")

    def reference(self, rows):
        words = rows.view("<u8").reshape(-1, 4)[:, :3]
        order = np.lexsort((words[:, 2], words[:, 1], words[:, 0]))
        return tuple(map(int, fixtures.KERNEL["_bjt_edge_counts"](rows[order])))

    def test_random_groups_keys_and_all_rows_preserved(self):
        rng = np.random.default_rng(182)
        rows = np.empty(10_000, native.EDGE_DTYPE)
        for name in fixtures.KERNEL["_BJT_EDGE_FIELDS"]:
            rows[name] = rng.integers(0, 2**32, len(rows), dtype=np.uint32)
            rows[1::3][name] = rows[::3][name][:len(rows[1::3])]
        rows["direction"] = rng.choice([-1, 1], len(rows))
        rows["contact"] = rng.integers(-6, 7, len(rows), dtype=np.int32)
        original = rows.copy()
        pointer = rows.ctypes.data
        self.assertEqual(self.backend.sort_count(rows), self.reference(original))
        self.assertEqual(rows.ctypes.data, pointer)
        np.testing.assert_array_equal(np.sort(rows.view("V32")), np.sort(original.view("V32")))
        keys = rows.view("<u8").reshape(-1, 4)[:, :3]
        order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
        np.testing.assert_array_equal(keys, keys[order])

    def test_contact_masks_signs_duplicates_and_balances(self):
        cases = [(1, 2, 3, 4), (1, 2, 5, 6), (3, 4, 5, 6),
                 (-1, -2, -3, -4), (1, 2, 3, -4), (1, 2, 3, 3), (0, 2, 3, 4)]
        for codes in cases:
            with self.subTest(codes=codes):
                rows = np.zeros(4, native.EDGE_DTYPE)
                rows["direction"] = (1, -1, 1, -1)
                rows["contact"] = codes
                expected = self.reference(rows.copy())
                self.assertEqual(self.backend.sort_count(rows), expected)

    def test_invalid_metadata_fails_before_mutation(self):
        for name, value in (("direction", 0), ("direction", 2), ("contact", 7), ("contact", -2**31)):
            with self.subTest(name=name, value=value):
                rows = np.zeros(2, native.EDGE_DTYPE)
                rows["p0"] = (9, 1)
                rows["direction"] = (1, -1)
                rows[1][name] = value
                before = rows.copy()
                with self.assertRaises(native.NativeExecutionError):
                    self.backend.sort_count(rows)
                np.testing.assert_array_equal(rows, before)

    def test_ffi_rejects_invalid_ranges_without_dereferencing(self):
        rows = np.zeros(2, native.EDGE_DTYPE)
        rows["direction"] = 1
        output = native._Counts()
        invoke = self.backend._sort_count
        self.assertEqual(invoke(None, 1, ctypes.byref(output)), 1)
        self.assertEqual(invoke(ctypes.c_void_p(rows.ctypes.data + 1), 1, ctypes.byref(output)), 2)
        self.assertEqual(invoke(ctypes.c_void_p(rows.ctypes.data), sys.maxsize // 32 + 1, ctypes.byref(output)), 3)
        self.assertEqual(invoke(ctypes.c_void_p(rows.ctypes.data), 2,
                                ctypes.cast(rows.ctypes.data, ctypes.POINTER(native._Counts))), 7)
        self.assertEqual(self.backend.sort_count(np.empty(0, native.EDGE_DTYPE)), (0, 0, 0, 0, 0))

    def test_complete_verifier_topologies_and_disk_paths(self):
        case = fixtures.ExactEdgeVerifierTests("test_topology_metrics_in_memory_and_on_disk")
        case.setUp()
        self.addCleanup(case.doCleanups)
        closed = fixtures.cube()
        reverse = closed.copy()
        reverse[0] = reverse[0, [0, 2, 1]]
        signed = closed.copy()
        signed[signed == 0] = -0.0
        variants = (closed, closed[:-1], reverse, np.concatenate((closed, closed[:1])),
                    np.concatenate((closed, fixtures.cube((1, 1, 0)))), signed,
                    fixtures.cube((-3, -2, -1)),
                    np.concatenate((closed, fixtures.cube((np.nextafter(np.float32(1), np.float32(2)), 1, 0)))))
        for index, triangles in enumerate(variants):
            for budget in (1_000_000, 256):
                with self.subTest(index=index, budget=budget):
                    with np.errstate(over="ignore"):
                        self.assertEqual(case.verify(triangles, budget, backend="rust"),
                                         case.verify(triangles, budget, backend="python"))


if __name__ == "__main__":
    unittest.main()
