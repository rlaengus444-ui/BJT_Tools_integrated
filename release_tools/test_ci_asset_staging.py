"""Native CI publication names preserve verified bytes without collisions."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'suite' / '배포_빌드' / 'ci_windows_release.py'
spec = importlib.util.spec_from_file_location('native_ci_asset_staging', PATH)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class NativeAssetStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'raw'
        self.raw.mkdir()

    def seed(self, version):
        names = ['BJT_통합프로그램.pyw', f'BJT_통합프로그램_{version}_EXE.zip',
                 f'BJT_통합프로그램_{version}_전체소스.zip',
                 f'BJT_통합프로그램_{version}_사용설명서.zip']
        for index, name in enumerate(names):
            (self.raw / name).write_bytes(('unchanged verified bytes ' + str(index)).encode())

    def test_hotfix_uses_four_distinct_ascii_names_and_matching_sums(self):
        self.seed('1.7.1')
        output = self.root / 'upload'
        names = ci.stage_ascii_assets(self.raw, output, '1.7.1')
        self.assertEqual(len(set(names.values())), 4)
        for original, renamed in names.items():
            renamed.encode('ascii')
            self.assertEqual((self.raw / original).read_bytes(), (output / renamed).read_bytes())
        lines = (output / 'SHA256SUMS.txt').read_text().splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            checksum, name = line.split('  ', 1)
            self.assertEqual(checksum, ci.digest(output / name))

    def test_names_are_derived_from_each_future_manifest_version(self):
        self.seed('9.8.7')
        names = ci.stage_ascii_assets(self.raw, self.root / 'upload', '9.8.7')
        self.assertTrue(all('9.8.7' in name for name in names.values()))

    def test_unverified_extra_file_fails_before_staging(self):
        self.seed('1.7.1')
        (self.raw / 'extra.exe').write_bytes(b'unverified')
        output = self.root / 'upload'
        with self.assertRaises(RuntimeError):
            ci.stage_ascii_assets(self.raw, output, '1.7.1')
        self.assertFalse(output.exists())

    def test_copy_corruption_cannot_produce_publishable_assets(self):
        self.seed('1.7.1')
        def corrupt(source, target):
            target.write_bytes(b'corrupted during staging')
        with mock.patch.object(ci.shutil, 'copyfile', side_effect=corrupt):
            with self.assertRaises(RuntimeError):
                ci.stage_ascii_assets(self.raw, self.root / 'upload', '1.7.1')


if __name__ == '__main__':
    unittest.main()
