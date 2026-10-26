"""Small native-precision fixtures for post-replacement validation.

The replacement scope deliberately matches the original application: strictly
numeric image stems in the selected folder, without recursion or previews.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import zlib
from PIL import Image, PngImagePlugin

CASES = {
    'clean_gray8': {'original_succeeds': True, 'enhanced_succeeds': True},
    'clean_rgb16': {'original_succeeds': True, 'enhanced_succeeds': True},
    'mixed_rgb_depth': {'original_succeeds': True, 'enhanced_succeeds': False, 'issue': 'native RGB channel depth 8 versus 16'},
    'mixed_gray_resolution': {'original_succeeds': False, 'enhanced_succeeds': False, 'issue': 'resolution differs outside replaced range'},
    'mixed_actual_format': {'original_succeeds': False, 'enhanced_succeeds': False, 'issue': 'JPEG bytes named 4.png'},
    'mixed_tiff_later_frame': {'original_succeeds': True, 'enhanced_succeeds': False, 'issue': 'second TIFF page resolution differs'},
    'uniform_multipage_tiff': {'original_succeeds': True, 'enhanced_succeeds': True},
    'numeric_gap': {'original_succeeds': False, 'enhanced_succeeds': False, 'issue': 'number 3 absent outside replacement range'},
    'duplicate_numeric': {'original_succeeds': False, 'enhanced_succeeds': False, 'issue': '1.png and 001.png duplicate number'},
    'preview_and_backup_ignored': {'original_succeeds': True, 'enhanced_succeeds': True},
    'corrupt_numeric': {'original_succeeds': False, 'enhanced_succeeds': False, 'issue': 'truncated numbered PNG'},
    'clean_bmp': {'original_succeeds': True, 'enhanced_succeeds': True},
    'metadata_only_variation': {'original_succeeds': True, 'enhanced_succeeds': True},
}


def write_png(path, *, width=8, height=6, channels=1, depth=8, value=20):
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

    samples = [((value + channel * 0x101) & ((1 << depth) - 1)) for _ in range(width) for channel in range(channels)]
    row = struct.pack('>' + 'H' * len(samples), *samples) if depth == 16 else bytes(samples)
    header = struct.pack('>IIBBBBB', width, height, depth, color_type, 0, 0, 0)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress((b'\x00' + row) * height)) + chunk(b'IEND', b''))
    return path


def make_case(root, name):
    root = Path(root)
    a, b = root / 'A', root / 'B'
    a.mkdir(parents=True)
    b.mkdir()
    case = dict(CASES[name])
    tiff = name in ('mixed_tiff_later_frame', 'uniform_multipage_tiff')
    extension = '.tif' if tiff else ('.bmp' if name == 'clean_bmp' else '.png')
    for number, folder in [(n, a) for n in range(1, 5)] + [(10, b)]:
        value = 20 + number * 5
        path = folder / (str(number) + extension)
        if tiff:
            first = Image.new('L', (8, 6), value)
            second_size = (9, 6) if name == 'mixed_tiff_later_frame' and number == 4 else (8, 6)
            second = Image.new('L', second_size, value + 1)
            first.save(path, save_all=True, append_images=[second])
        elif name == 'clean_bmp':
            Image.new('L', (8, 6), value).save(path, format='BMP')
        else:
            depth = 16 if name == 'clean_rgb16' or (name == 'mixed_rgb_depth' and number == 4) else 8
            channels = 3 if name in ('clean_rgb16', 'mixed_rgb_depth') else 1
            width = 9 if name == 'mixed_gray_resolution' and number == 4 else 8
            write_png(path, width=width, channels=channels, depth=depth, value=value)
    if name == 'mixed_actual_format':
        Image.new('L', (8, 6), 40).save(a / '4.png', format='JPEG')
    elif name == 'numeric_gap':
        (a / '3.png').unlink()
    elif name == 'duplicate_numeric':
        (a / '001.png').write_bytes((a / '1.png').read_bytes())
    elif name == 'preview_and_backup_ignored':
        (a / 'preview.png').write_bytes(b'Corrupt preview must remain outside numeric replacement scope')
        (a / 'run.gcode').write_text('auxiliary text', encoding='utf-8')
        write_png(a / '_backup_before_replace_fixture' / '1.png', width=3, depth=16)
    elif name == 'corrupt_numeric':
        (a / '4.png').write_bytes(b'\x89PNG\r\n\x1a\ntruncated')
    elif name == 'metadata_only_variation':
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('Comment', 'Layer content and storage metadata may vary without changing geometry/depth/format')
        Image.new('L', (8, 6), 40).save(a / '4.png', pnginfo=metadata, dpi=(300, 300), compress_level=1)
    manifest = {'name': name, **case, 'a_folder': str(a), 'b_folder': str(b), 'a_start': 2, 'a_end': 2, 'b_start': 10, 'b_end': 10,
                'scope': 'strictly numeric image stems in A, nonrecursive; previews/backups/auxiliary files excluded', 'extension': extension}
    (root / 'fixture.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


import hashlib
import importlib.util
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / 'sources' / 'slice exchanger.py'
sys.path.insert(0, str(PACKAGE))

def load_original(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ExchangeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bjt_replace_validation_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = load_original('replace_validation_fixture_' + self.id().rsplit('.', 1)[-1])

    def enhanced(self):
        import slice_replace_validation
        slice_replace_validation.install(self.module)
        return slice_replace_validation

    def run_case(self, name, enhanced=True):
        manifest = make_case(self.root / name, name)
        a, b = Path(manifest['a_folder']), Path(manifest['b_folder'])
        before_a = {p.name: p.read_bytes() for p in a.iterdir() if p.is_file()}
        before_b = {p.name: p.read_bytes() for p in b.iterdir() if p.is_file()}
        if enhanced:
            self.enhanced()
        logs = []
        args = {key: manifest[key] for key in ('a_folder', 'b_folder', 'a_start', 'a_end', 'b_start', 'b_end')}
        success = manifest['enhanced_succeeds' if enhanced else 'original_succeeds']
        if success:
            self.module.safe_replace_images(**args, log=logs.append)
            target = '2' + manifest['extension']
            source = '10' + manifest['extension']
            self.assertEqual((a / target).read_bytes(), before_b[source])
            self.assertNotEqual((a / target).read_bytes(), before_a[target])
            for name, data in before_a.items():
                if name != target:
                    self.assertEqual((a / name).read_bytes(), data)
            backups = [p for p in a.glob('_backup_before_replace_*') if p.name != '_backup_before_replace_fixture']
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / target).read_bytes(), before_a[target])
        else:
            with self.assertRaises(RuntimeError):
                self.module.safe_replace_images(**args, log=logs.append)
            self.assertEqual({p.name: p.read_bytes() for p in a.iterdir() if p.is_file()}, before_a)
            if manifest['name'] != 'duplicate_numeric':
                self.assertTrue(any('자동복구 완료' in line for line in logs), logs)
        self.assertEqual({p.name: p.read_bytes() for p in b.iterdir() if p.is_file()}, before_b)
        return logs

    def test_all_enhanced_fixture_outcomes_and_original_backup_rollback_contract(self):
        for name in CASES:
            with self.subTest(name=name):
                self.run_case(name)

    def test_original_rgb16_precision_blind_spot_is_demonstrated(self):
        self.run_case('mixed_rgb_depth', enhanced=False)

    def test_original_later_tiff_page_blind_spot_is_demonstrated(self):
        self.run_case('mixed_tiff_later_frame', enhanced=False)

    def test_signature_reports_native_rgb16_channel_and_pixel_depth(self):
        path = write_png(self.root / 'native.png', channels=3, depth=16)
        validation = self.enhanced()
        signature = validation.get_image_signature(path)
        self.assertEqual(signature['format'], 'PNG')
        self.assertEqual(signature['size'], (8, 6))
        self.assertEqual(signature['bit_depth'], 16)
        self.assertEqual(signature['bits'], 48)
        self.assertEqual(signature['frame_count'], 1)
        self.assertIn('16', validation.signature_to_text(signature))

    def test_source_file_remains_byte_identical(self):
        before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self.enhanced()
        after = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_install_changes_only_three_helpers_and_is_idempotent(self):
        originals = {name: getattr(self.module, name) for name in ('safe_replace_images', 'compare_pair_signatures', 'check_folder_continuity_all', 'check_missing_numbers')}
        worker = self.module.ImageReplaceApp.worker
        signature = self.module.get_image_signature
        validation = self.enhanced()
        self.assertIs(self.module._suite_original_get_image_signature, signature)
        self.assertIs(self.module.get_image_signature, validation.get_image_signature)
        self.assertIs(self.module.ImageReplaceApp.worker, worker)
        self.assertTrue(all(getattr(self.module, name) is value for name, value in originals.items()))
        validation.install(self.module)
        self.assertIs(self.module._suite_original_get_image_signature, signature)

    def test_loader_installs_adapter_in_the_original_worker_namespace(self):
        from suite_loader import ToolLoader
        import slice_replace_validation as validation
        module = ToolLoader().load('exchanger')
        self.assertTrue(module._suite_native_validation)
        self.assertIs(module.get_image_signature, validation.get_image_signature)
        self.assertIs(module.safe_replace_images.__globals__['get_image_signature'], validation.get_image_signature)
        self.assertIs(module.ImageReplaceApp.worker.__globals__['safe_replace_images'], module.safe_replace_images)

    def test_native_depth_mismatch_in_selected_pair_fails_before_backup(self):
        manifest = make_case(self.root / 'selected_depth', 'clean_rgb16')
        a, b = Path(manifest['a_folder']), Path(manifest['b_folder'])
        write_png(b / '10.png', channels=3, depth=8)
        before = {path.name: path.read_bytes() for path in a.iterdir()}
        self.enhanced()
        with self.assertRaises(RuntimeError):
            self.module.safe_replace_images(a, b, 2, 2, 10, 10, lambda line: None)
        self.assertEqual({path.name: path.read_bytes() for path in a.iterdir()}, before)
        self.assertEqual(list(a.glob('_backup_before_replace_*')), [])

    def test_different_image_content_is_not_a_structural_difference(self):
        first = write_png(self.root / 'first.png', channels=3, depth=16, value=0x1201)
        second = write_png(self.root / 'second.png', channels=3, depth=16, value=0xABCD)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())
        validation = self.enhanced()
        self.assertEqual(validation.get_image_signature(first), validation.get_image_signature(second))

    def test_later_tiff_frame_error_identifies_the_frame(self):
        manifest = make_case(self.root / 'later_frame', 'mixed_tiff_later_frame')
        self.enhanced()
        mapping = self.module.build_number_file_map(Path(manifest['a_folder']))
        with self.assertRaisesRegex(RuntimeError, '프레임 2'):
            self.module.check_folder_format_consistency(mapping, 'A 폴더', lambda line: None)

    def test_full_decode_rejects_invalid_deflate_even_with_correct_crc(self):
        path = write_png(self.root / 'invalid.png')
        data = bytearray(path.read_bytes())
        offset = data.index(b'IDAT')
        length = struct.unpack('>I', data[offset - 4:offset])[0]
        start, end = offset + 4, offset + 4 + length
        data[start] ^= 0xFF
        data[end:end + 4] = struct.pack('>I', zlib.crc32(data[offset:end]) & 0xffffffff)
        path.write_bytes(data)
        validation = self.enhanced()
        with self.assertRaises(RuntimeError):
            validation.get_image_signature(path)

    def test_native_gray_la_rgb_rgba_channel_depths(self):
        validation = self.enhanced()
        for channels in (1, 2, 3, 4):
            for depth in (8, 16):
                with self.subTest(channels=channels, depth=depth):
                    path = write_png(self.root / f'{channels}_{depth}.png', channels=channels, depth=depth)
                    signature = validation.get_image_signature(path)
                    self.assertEqual(signature['bit_depth'], depth)
                    self.assertEqual(signature['bits'], channels * depth)
                    self.assertEqual(len(signature['bands']), channels)


if __name__ == '__main__':
    unittest.main()
