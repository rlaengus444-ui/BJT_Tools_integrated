"""Native builds publish without changing the inode of a loaded binary."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

path = Path(__file__).resolve().parents[1] / 'native' / 'build_bjt_mesh_native.py'
spec = importlib.util.spec_from_file_location('_bjt_build_test', path)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class AtomicPublishTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "open-file replacement semantics vary by platform")
    def test_open_old_file_survives_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'library.so'
            target.write_bytes(b'old binary')
            with target.open('rb') as old:
                build.atomic_publish(target, b'new binary')
                self.assertEqual(old.read(), b'old binary')
            self.assertEqual(target.read_bytes(), b'new binary')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_failure_before_replace_keeps_old_file(self):
        for failed in ('fsync', 'replace'):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'library.so'
                target.write_bytes(b'old binary')
                with mock.patch.object(build.os, failed, side_effect=OSError('simulated failure')):
                    with self.assertRaises(OSError):
                        build.atomic_publish(target, b'partial replacement')
                self.assertEqual(target.read_bytes(), b'old binary')
                self.assertEqual(list(Path(directory).iterdir()), [target])


if __name__ == '__main__':
    unittest.main()
