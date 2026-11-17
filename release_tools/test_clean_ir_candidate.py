"""Owned compressed ZIP fixtures; no production execution or remote actions."""
from __future__ import annotations

import io
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
import zipfile

import clean_ir_candidate as cleaner


class CleanupProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ir-clean-zip-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original, self.cleaned = self.root / 'original.zip', self.root / 'cleaned.zip'
        self.log = 'BJT_통합프로그램_1.10.0_EXE/_internal/logs/errors.log'
        self.log_bytes = b'owned synthetic fixture log\n'
        self.entries = [('BJT_통합프로그램_1.10.0_EXE/첫 입력.json', b'{"owned":true}'),
                        (self.log, self.log_bytes), ('BJT_통합프로그램_1.10.0_EXE/BJT_통합프로그램.exe', b'MZ owned non-executable fixture')]
        self.make(self.original, self.entries)
        self.original_pin = cleaner.facts(self.original)
        import hashlib
        self.log_pin = {'bytes': len(self.log_bytes), 'sha256': hashlib.sha256(self.log_bytes).hexdigest()}

    def make(self, path, entries):
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b'owned archive comment'
            for name, value in entries:
                info = zipfile.ZipInfo(name, (2026, 11, 17, 12, 34, 56))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.extra = struct.pack('<HH4s', 0xCAFE, 4, b'test')
                info.comment = b'owned member comment'
                archive.writestr(info, value)

    def delete(self):
        tool = shutil.which('zip')
        if tool is None: self.skipTest('Installed zip executable unavailable')
        shutil.copyfile(self.original, self.cleaned)
        result = subprocess.run([tool, '-d', str(self.cleaned), self.log],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def proof(self, **kwargs):
        return cleaner.verify_transform(self.original, self.cleaned,
            kwargs.get('original_pin', self.original_pin), self.log,
            kwargs.get('log_pin', self.log_pin), expected_remaining=2)

    def test_valid_unicode_deletion_preserves_all_raw_compressed_records_and_metadata(self):
        self.delete()
        before_bytes = self.original.read_bytes()
        result = self.proof()
        self.assertEqual(result['original_members'], 3)
        self.assertEqual(result['cleaned_members'], 2)
        self.assertTrue(result['all_retained_raw_local_records_identical'])
        self.assertTrue(result['all_retained_normalized_central_records_identical'])
        self.assertTrue(result['all_retained_metadata_identical'])
        self.assertEqual(len(result['retained_inventory']), 2)
        self.assertEqual(self.original.read_bytes(), before_bytes)

    def test_wrong_original_hash_is_blocked(self):
        self.delete()
        pin = dict(self.original_pin, sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'Pinned file differs'): self.proof(original_pin=pin)

    def test_wrong_log_hash_or_length_is_blocked(self):
        self.delete()
        for pin in (dict(self.log_pin, sha256='0' * 64), dict(self.log_pin, bytes=len(self.log_bytes) + 1)):
            with self.subTest(pin=pin), self.assertRaisesRegex(ValueError, 'Pinned log bytes differ'):
                self.proof(log_pin=pin)

    def test_missing_deletion_and_additional_deletion_are_blocked(self):
        shutil.copyfile(self.original, self.cleaned)
        with self.assertRaisesRegex(ValueError, 'more than the pinned log'): self.proof()
        self.make(self.cleaned, [self.entries[0]])
        with self.assertRaisesRegex(ValueError, 'more than the pinned log'): self.proof()

    def test_changed_retained_member_is_blocked(self):
        self.make(self.cleaned, [(self.entries[0][0], b'changed actual resource'), self.entries[2]])
        with self.assertRaisesRegex(ValueError, 'Retained member bytes/metadata/raw record changed'): self.proof()

    def test_changed_retained_metadata_is_blocked(self):
        self.delete()
        with zipfile.ZipFile(self.cleaned) as archive:
            infos = archive.infolist()
            values = {info.filename: archive.read(info) for info in infos}
        with zipfile.ZipFile(self.cleaned, 'w') as archive:
            archive.comment = b'owned archive comment'
            for index, info in enumerate(infos):
                if index == 0: info.comment = b'changed metadata'
                archive.writestr(info, values[info.filename])
        with self.assertRaisesRegex(ValueError, 'Retained member bytes/metadata/raw record changed'): self.proof()

    def test_unsafe_and_duplicate_source_archives_are_blocked(self):
        for entries in ([('../unsafe', b'x')], [('A', b'x'), ('a', b'y')], [('a', b'x'), ('a/b', b'y')]):
            with self.subTest(entries=entries):
                self.make(self.cleaned, entries)
                with self.assertRaises(ValueError): cleaner.archive_inventory(self.cleaned)

    def test_symlink_source_member_is_blocked(self):
        with zipfile.ZipFile(self.cleaned, 'w') as archive:
            info = zipfile.ZipInfo('alias')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'target')
        with self.assertRaisesRegex(ValueError, 'Special/encrypted'): cleaner.archive_inventory(self.cleaned)

    def test_stored_metadata_and_normalized_central_bytes_both_cover_unknown_extra_fields(self):
        self.delete()
        result = self.proof()
        for row in result['retained_inventory'].values():
            self.assertEqual(row['metadata']['extra'], 'feca040074657374')
            self.assertEqual(row['metadata']['comment'], b'owned member comment'.hex())
            self.assertEqual(len(row['normalized_central_record_sha256']), 64)
            self.assertGreater(row['raw_local_record_bytes'], row['metadata']['compress_size'])


if __name__ == '__main__':
    unittest.main()
