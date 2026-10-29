"""Optional native backend discovery, ABI negotiation, and buffer safety."""
import ctypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bjt_mesh_native as native


class Function:
    def __init__(self, result):
        self.result = result
    def __call__(self, *args):
        return self.result


def fake_library():
    library = SimpleNamespace()
    for name, value in {
        'bjt_mesh_abi_version': 1, 'bjt_mesh_edge_row_size': 32,
        'bjt_mesh_edge_row_align': 4, 'bjt_mesh_counts_size': 40,
        'bjt_mesh_platform_supported': 1, 'bjt_mesh_sort_count_edges': 0,
    }.items():
        setattr(library, name, Function(value))
    return library


class NativeDiscoveryTests(unittest.TestCase):
    def tearDown(self):
        native._load_backend.cache_clear()

    def test_missing_library_auto_falls_back_and_rust_reports_error(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(native, '_library_path', return_value=Path(directory) / 'missing'):
            native._load_backend.cache_clear()
            self.assertIsNone(native.get_backend('auto'))
            with self.assertRaises(native.NativeUnavailableError):
                native.get_backend('rust')
            self.assertFalse(native.backend_status()['available'])
            self.assertIsNone(native.get_backend('python'))

    def test_invalid_choice_and_environment(self):
        with mock.patch.dict('os.environ', {'BJT_MESH_BACKEND': 'python'}):
            self.assertIsNone(native.get_backend())
        with self.assertRaises(ValueError):
            native.get_backend('invalid')

    def test_local_discovery_never_uses_path_search(self):
        with mock.patch.object(native.sys, 'platform', 'win32'):
            self.assertEqual(native._library_path(), Path(native.__file__).resolve().parent / 'native' / 'bjt_mesh_native.dll')
        with mock.patch.object(native.sys, 'platform', 'linux'):
            self.assertEqual(native._library_path(), Path(native.__file__).resolve().parent / 'native' / 'libbjt_mesh_native.so')

    def test_all_abi_mismatches_rejected(self):
        for name in ('bjt_mesh_abi_version', 'bjt_mesh_edge_row_size', 'bjt_mesh_edge_row_align',
                     'bjt_mesh_counts_size', 'bjt_mesh_platform_supported'):
            library = fake_library()
            getattr(library, name).result = 999
            with self.subTest(name=name), mock.patch.object(native.ctypes, 'CDLL', return_value=library):
                with self.assertRaises(native.NativeUnavailableError):
                    native.NativeBackend('example')

    def test_optional_packing_abi_does_not_disable_edge_backend(self):
        for version in (None, 0, 2):
            library = fake_library()
            if version is not None:
                library.bjt_mesh_triangle_pack_abi_version = Function(version)
            with self.subTest(version=version), mock.patch.object(native.ctypes, 'CDLL', return_value=library):
                backend = native.NativeBackend('example')
                self.assertFalse(backend.can_pack_grid)
                self.assertEqual(backend.sort_count(np.empty(0, dtype=native.EDGE_DTYPE)), (0, 0, 0, 0, 0))
                with self.assertRaises(native.NativeUnavailableError):
                    backend.pack_grid_triangles(None, None, xsp=1, ysp=1, zsp=1, xoff=0, yoff=0)

    def test_incompatible_binary_falls_back_in_auto(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.so'
            path.write_bytes(b'not a library')
            with mock.patch.object(native, '_library_path', return_value=path):
                native._load_backend.cache_clear()
                self.assertIsNone(native.get_backend('auto'))
                with self.assertRaises(native.NativeUnavailableError):
                    native.get_backend('rust')

    def test_unsafe_buffers_rejected_before_native_call(self):
        library = fake_library()
        with mock.patch.object(native.ctypes, 'CDLL', return_value=library):
            backend = native.NativeBackend('example')
            owned = np.zeros(4, dtype=native.EDGE_DTYPE)
            readonly = owned.copy()
            readonly.flags.writeable = False
            for bad in (owned[::2], readonly):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    backend.sort_count(bad)
            for bad in ([], np.zeros(4, dtype='u1'), owned.reshape(2, 2)):
                with self.subTest(bad=type(bad)), self.assertRaises(TypeError):
                    backend.sort_count(bad)
            self.assertEqual(backend.sort_count(owned), (0, 0, 0, 0, 0))
            library.bjt_mesh_sort_count_edges.result = 4
            with self.assertRaises(native.NativeExecutionError):
                backend.sort_count(owned)

    def test_real_library_metadata_if_built(self):
        path = native._library_path()
        if path is None or not path.is_file():
            self.skipTest('optional host native library not built')
        backend = native.NativeBackend(path)
        self.assertEqual(ctypes.sizeof(native._Counts), 40)
        self.assertEqual(backend.sort_count(np.empty(0, dtype=native.EDGE_DTYPE)), (0, 0, 0, 0, 0))


if __name__ == '__main__':
    unittest.main()
