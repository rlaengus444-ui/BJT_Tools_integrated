"""Fail-closed checks for publication of an existing verified build."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('suite_publisher', Path(__file__).with_name('publish_verified_suite.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets, self.evidence = self.root / 'assets', self.root / 'evidence'
        self.assets.mkdir(); self.evidence.mkdir()
        self.expected = {}
        for index, name in enumerate(publisher.KNOWN_ASSETS):
            path = self.assets / name
            path.write_bytes(('verified fixture ' + str(index)).encode())
            self.expected[name] = {'bytes': path.stat().st_size, 'sha256': publisher.digest(path)}
        patch = mock.patch.object(publisher, 'KNOWN_ASSETS', self.expected)
        patch.start(); self.addCleanup(patch.stop)
        self.summary = {'passed': True, 'version': publisher.VERSION, 'commit': publisher.COMMIT,
                        'checks': {name: {'passed': True} for name in publisher.REQUIRED_CHECKS},
                        'assets': self.expected}
        self.save_summary()

    def save_summary(self):
        (self.evidence / 'native-summary.json').write_text(json.dumps(self.summary), encoding='utf-8')

    def test_all_bytes_verified_before_unique_ascii_names_and_new_sums(self):
        output = self.root / 'upload'
        report = publisher.prepare(self.evidence, self.assets, output)
        self.assertEqual(set(report['ascii_assets']), set(publisher.ASCII_NAMES.values()) | {'SHA256SUMS.txt'})
        self.assertEqual(len(set(report['ascii_assets'])), 5)
        for name, record in report['ascii_assets'].items():
            name.encode('ascii')
            self.assertEqual(publisher.digest(output / name), record['sha256'])
        lines = (output / 'SHA256SUMS.txt').read_text().splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            digest, name = line.split('  ', 1)
            self.assertEqual(digest, publisher.digest(output / name))
        self.assertFalse(report['rebuilt'])
        self.assertFalse(report['program_bytes_changed'])

    def test_changed_original_content_fails_before_staging(self):
        (self.assets / next(iter(self.expected))).write_bytes(b'not the verified file')
        output = self.root / 'upload'
        with self.assertRaises(ValueError):
            publisher.prepare(self.evidence, self.assets, output)
        self.assertFalse(output.exists())

    def test_wrong_commit_missing_gate_and_different_manifest_are_rejected(self):
        for change in ('commit', 'gate', 'manifest', 'failed'):
            summary = copy.deepcopy(self.summary)
            if change == 'commit': summary['commit'] = '0' * 40
            elif change == 'gate': summary['checks']['features-exe']['passed'] = False
            elif change == 'manifest': summary['assets'].pop('SHA256SUMS.txt')
            else: summary['passed'] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                publisher.verify_summary(summary)

    def test_unexpected_extra_file_cannot_join_publication(self):
        (self.assets / 'unverified.exe').write_bytes(b'unverified')
        with self.assertRaises(ValueError):
            publisher.prepare(self.evidence, self.assets, self.root / 'upload')

    def test_published_unrelated_commit_tag_or_release_are_never_modified(self):
        base = {'id': publisher.RELEASE_ID, 'draft': True, 'tag_name': 'v' + publisher.VERSION,
                'target_commitish': publisher.COMMIT}
        publisher.validate_release(base)
        for key, value in (('id', 1), ('draft', False), ('tag_name', 'v1.6.2'),
                           ('target_commitish', '0' * 40)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                publisher.validate_release({**base, key: value})

    def test_server_digest_and_size_must_both_match(self):
        expected = {'BJT_1.7.0_source.zip': {'bytes': 3, 'sha256': 'abc'}}
        good = {'name': 'BJT_1.7.0_source.zip', 'state': 'uploaded', 'size': 3, 'digest': 'sha256:abc'}
        publisher.verify_remote_assets([good], expected)
        for change in ({'digest': None}, {'digest': 'sha256:other'}, {'size': 2}, {'state': 'starter'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                publisher.verify_remote_assets([{**good, **change}], expected)


if __name__ == '__main__':
    unittest.main()
