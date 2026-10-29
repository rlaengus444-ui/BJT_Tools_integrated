"""Offline checks for fail-closed Windows release glue; no EXE is built here."""
import copy
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

BUILD = Path(__file__).resolve().parents[1] / '배포_빌드'


def load(name):
    spec = importlib.util.spec_from_file_location(name, BUILD / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {name: module}), mock.patch.object(sys, 'path', [str(BUILD), *sys.path]):
        spec.loader.exec_module(module)
    return module


native = load('release_native')
with mock.patch.dict(sys.modules, {'release_native': native}):
    probe = load('frozen_rust_probe')
    ci = load('ci_windows_release')


def fixture_result():
    return {'cases': [{'case': name, 'status': 'passed', 'report_status': 'completed',
        'stats': {'triangles': 12, 'sha256': 'a' * 64},
        'images': {'raw': [], 'lattice': [{'sha256_pixels': 'b' * 64, 'size': [32, 36]}]},
        'previews_count': 1, 'progress_ends_at_100': True} for name in probe.EXPECTED_CASES]}


class ReleaseGateTests(unittest.TestCase):
    def test_cargo_tests_start_with_pinned_toolchain_and_external_target_directory(self):
        environment = {'CARGO_ENCODED_RUSTFLAGS': 'untrusted', 'RUSTUP_TOOLCHAIN': 'wrong'}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            class StoppedAtCargo(Exception):
                pass
            with mock.patch.object(ci, 'run', side_effect=StoppedAtCargo) as execute:
                with self.assertRaises(StoppedAtCargo):
                    ci.build_native(output, output / 'reports', environment)
            self.assertEqual(execute.call_args.args[0][:4], ['cargo', 'test', '--offline', '--locked'])
            self.assertEqual(environment['RUSTUP_TOOLCHAIN'], native.TOOLCHAIN)
            self.assertEqual(Path(environment['CARGO_TARGET_DIR']), output / 'cargo-target')
            self.assertFalse(Path(environment['CARGO_TARGET_DIR']).is_relative_to(ci.SOURCE))
            self.assertNotIn('CARGO_ENCODED_RUSTFLAGS', environment)

    def test_rust_action_launches_frozen_cli_with_clean_dependency_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app-source').mkdir()
            (root / 'app-source/source_manifest.json').write_text('{"suite_version":"1.8.0"}')
            script = root / 'run_build.py'
            shutil.copy2(BUILD / 'run_build.py', script)
            seen = {}
            def execute(command, **kwargs):
                seen.update(command=command, environment=kwargs['env'])
                Path(command[-1]).write_text('{"passed":true,"frozen":true}')
                return SimpleNamespace(returncode=0)
            runtime = SimpleNamespace(process_environment=lambda python: dict(probe.os.environ))
            with mock.patch.dict(sys.modules, {'suite_runtime': runtime}), \
                    mock.patch.dict(probe.os.environ, {'PYTHONPATH': 'outside-package', 'NUMBA_CACHE_DIR': 'old',
                        'BJT_RELEASE_COMMIT': 'checked-commit'}), \
                    mock.patch.object(sys, 'argv', [str(script), 'rust-probe-exe']), \
                    mock.patch('subprocess.run', side_effect=execute), \
                    mock.patch.object(sys, 'path', list(sys.path)):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script), run_name='__main__')
            self.assertEqual(result.exception.code, 0)
            self.assertTrue(seen['command'][0].endswith('BJT_명령줄.exe'))
            self.assertEqual(seen['command'][1], '--frozen-rust-probe')
            self.assertNotIn('PYTHONPATH', seen['environment'])
            self.assertNotIn('NUMBA_CACHE_DIR', seen['environment'])
            self.assertNotIn('outside-package', seen['environment']['PATH'])
            self.assertEqual(seen['environment']['BJT_RELEASE_COMMIT'], 'checked-commit')

    def test_rust_absence_skip_is_release_failure_but_known_posix_skip_is_allowed(self):
        rust = ET.fromstring('<testcase classname="tests.test_bjt_rust_pack.RustGridPackingTests" '
            'name="test_random_extreme_scaling_and_signed_zero_bytes">'
            '<skipped message="unittest.case.SkipTest: optional Rust packing feature is absent"/></testcase>')
        self.assertFalse(ci.allowed_source_skip(rust))
        self.assertTrue(ci.allowed_source_skip(rust, rust_required=False))
        posix = ET.fromstring('<testcase classname="test_bjt_build_publish.AtomicPublishTests" '
            'name="test_open_old_file_survives_replacement">'
            '<skipped message="open-file replacement semantics vary by platform"/></testcase>')
        self.assertTrue(ci.allowed_source_skip(posix))

    def test_pixel_hash_stl_hash_and_report_status_must_match_for_all_six_cases(self):
        good = fixture_result()
        self.assertEqual(probe.compare_fixtures(good, copy.deepcopy(good)), list(probe.EXPECTED_CASES))
        for field in ('pixel', 'stl', 'status', 'missing'):
            bad = copy.deepcopy(good)
            if field == 'pixel':
                bad['cases'][3]['images']['lattice'][0]['sha256_pixels'] = 'c' * 64
            elif field == 'stl':
                bad['cases'][2]['stats']['sha256'] = 'd' * 64
            elif field == 'status':
                bad['cases'][0]['report_status'] = 'failed'
            else:
                bad['cases'].pop()
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                probe.compare_fixtures(good, bad)

    def test_dll_metadata_digest_target_and_exports_are_independent_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / native.BINARY
            binary.write_bytes(b'fake PE inspected by controlled mock')
            metadata = {'schema': 1, 'target': native.TARGET, 'profile': 'release',
                'binary': native.BINARY, 'binary_sha256': native.digest(binary),
                'binary_bytes': binary.stat().st_size, 'crate_source_sha256': 'crate',
                'rustflags': '-C panic=unwind -C target-cpu=generic',
                'rustc': f'rustc {native.TOOLCHAIN} (commit date)\nhost: {native.TARGET}\nrelease: {native.TOOLCHAIN}',
                'cargo': f'cargo {native.TOOLCHAIN} (commit date)'}
            def store(value):
                (root / 'bjt_mesh_native.build.json').write_text(json.dumps(value), encoding='utf-8')
            symbols = [SimpleNamespace(name=name.encode(), forwarder=None) for name in native.EXPORTS]
            pe = SimpleNamespace(FILE_HEADER=SimpleNamespace(Machine=0x8664),
                DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=symbols), close=mock.Mock())
            with mock.patch.dict(sys.modules, {'pefile': SimpleNamespace(PE=lambda *a, **k: pe)}):
                store(metadata)
                self.assertEqual(native.verify_native(root, expected_source='crate')['machine'], 'AMD64')
                for key, wrong in (('target', 'x86_64-unknown-linux-gnu'), ('binary_sha256', '0' * 64),
                                   ('crate_source_sha256', 'stale'), ('rustc', 'rustc wrong')):
                    store(dict(metadata, **{key: wrong}))
                    with self.subTest(key=key), self.assertRaises(RuntimeError):
                        native.verify_native(root, expected_source='crate')
                store(metadata)
                pe.DIRECTORY_ENTRY_EXPORT.symbols = symbols[:-1]
                with self.assertRaisesRegex(RuntimeError, 'eight ABI exports'):
                    native.verify_native(root)
                pe.DIRECTORY_ENTRY_EXPORT.symbols = symbols
                pe.FILE_HEADER.Machine = 0x14c
                with self.assertRaisesRegex(RuntimeError, 'AMD64'):
                    native.verify_native(root)

    def test_stale_commit_or_dll_and_zero_native_calls_block_packaging(self):
        proof = {'sha256': 'dll'}
        report = {'passed': True, 'frozen': True, 'commit': 'head', 'native': proof,
            'source_manifest_sha256': 'manifest', 'executable_sha256': 'exe',
            'missing_dll_rejected': True, 'can_pack_grid': True, 'fixture_count': 6, 'parity': True,
            'native_calls': {name: {'success_calls': 2, 'rows': 18}
                             for name in ('sort_count', 'pack_grid_triangles')}}
        def check(value):
            native.require_probe(value, proof, commit='head', manifest_sha256='manifest', executable_sha256='exe')
        check(report)
        for key, value in (('commit', 'old'), ('native', {'sha256': 'stale'}),
                           ('executable_sha256', 'old'), ('missing_dll_rejected', False), ('parity', False)):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                check(dict(report, **{key: value}))
        for name in report['native_calls']:
            bad = copy.deepcopy(report)
            bad['native_calls'][name]['success_calls'] = 0
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                check(bad)


class ProductionInstanceTests(unittest.TestCase):
    def test_probe_instruments_alias_instance_and_rejects_missing_dll(self):
        """A second ordinary import would see a different, unused backend."""
        class Backend:
            can_pack_grid = True
            def sort_count(self, rows):
                return (0, 0, 0, len(rows), 0)
            def pack_grid_triangles(self, rows, records=None):
                return len(rows)
        alias = '_bjt_release_exact_production_alias'
        Backend.__module__ = alias
        wrapper = SimpleNamespace(NativeUnavailableError=type('NativeUnavailableError', (RuntimeError,), {}))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / '_internal'
            (base / 'native').mkdir(parents=True)
            binary = base / 'native/bjt_mesh_native.dll'
            binary.write_bytes(b'controlled native fixture')
            (base / 'source_manifest.json').write_text('{}')
            instance, other_instance = Backend(), Backend()
            instance.path = binary.resolve()
            wrapper._library_path = lambda: binary
            @lru_cache(maxsize=1)
            def cached():
                return instance if wrapper._library_path().is_file() else None
            wrapper._load_backend = cached
            def get_backend(choice=None):
                choice = choice or probe.os.environ.get('BJT_MESH_BACKEND', 'auto')
                if choice == 'python':
                    return None
                value = cached()
                if value is None and choice == 'rust':
                    raise wrapper.NativeUnavailableError('missing')
                return value
            module = SimpleNamespace(_bjt_native_edge_backend=get_backend)
            def run_fixtures(tool, root):
                used = tool._bjt_native_edge_backend()
                if used is not None:
                    used.sort_count([1, 2, 3])
                    used.pack_grid_triangles([1, 2])
                return fixture_result()
            helper = SimpleNamespace(run=run_fixtures)
            spec = SimpleNamespace(name='_bjt_frozen_rust_fixtures', loader=SimpleNamespace(exec_module=lambda m: None))
            with mock.patch.dict(sys.modules, {alias: wrapper,
                    'bjt_mesh_native': SimpleNamespace(get_backend=lambda *a: other_instance),
                    'suite_loader': SimpleNamespace(ToolLoader=lambda: SimpleNamespace(load=lambda key: module))}), \
                    mock.patch.object(sys, 'frozen', True, create=True), \
                    mock.patch.object(probe, 'verify_native', return_value={'sha256': 'dll'}), \
                    mock.patch.object(probe.importlib.util, 'spec_from_file_location', return_value=spec), \
                    mock.patch.object(probe.importlib.util, 'module_from_spec', return_value=helper):
                result = probe.run(base)
            self.assertTrue(result['passed'])
            self.assertTrue(result['missing_dll_rejected'])
            self.assertEqual(result['native_calls']['sort_count'], {'success_calls': 1, 'rows': 3})
            self.assertEqual(result['native_calls']['pack_grid_triangles'], {'success_calls': 1, 'rows': 2})
            self.assertEqual(result['fixture_count'], 6)

    def test_missing_dll_fallback_is_an_error_even_if_wrapper_returns_none(self):
        class Backend:
            pass
        Backend.__module__ = '_bjt_bad_probe_alias'
        wrapper = SimpleNamespace(_library_path=lambda: Path('dummy'),
            _load_backend=SimpleNamespace(cache_clear=lambda: None),
            NativeUnavailableError=type('NativeUnavailableError', (RuntimeError,), {}))
        module = SimpleNamespace(_bjt_native_edge_backend=lambda choice: None)
        with mock.patch.dict(sys.modules, {'_bjt_bad_probe_alias': wrapper}):
            with self.assertRaisesRegex(RuntimeError, 'silently fell back'):
                probe.reject_missing_dll(module, Backend(), Path('/unused'))


if __name__ == '__main__':
    unittest.main()
