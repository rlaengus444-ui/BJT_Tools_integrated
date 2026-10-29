"""Mandatory opt-in native proof using the production BJT backend instance."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile

from release_native import digest, verify_native

EXPECTED_CASES = ('reconstruct_crop_center', 'hex_line_xyz_bmp', 'square_line_xy_png',
                  'hex_face_xyz', 'shell_only', 'model_full')


def compare_fixtures(rust, python):
    left, right = rust.get('cases', []), python.get('cases', [])
    if len(left) != 6 or len(right) != 6:
        raise RuntimeError('Expected all six BJT fixtures under each backend')
    if tuple(case.get('case') for case in left) != EXPECTED_CASES:
        raise RuntimeError('Required six production BJT fixture cases changed')
    fields = ('case', 'status', 'stats', 'images', 'previews_count',
              'progress_ends_at_100', 'report_status')
    for actual, expected in zip(left, right):
        if (actual.get('status') != 'passed' or expected.get('status') != 'passed'
                or actual.get('report_status') != 'completed'
                or not actual.get('progress_ends_at_100')
                or actual.get('stats', {}).get('triangles', 0) <= 0
                or not actual.get('stats', {}).get('sha256')
                or {key: actual.get(key) for key in fields} !=
                   {key: expected.get(key) for key in fields}):
            raise RuntimeError('Rust/Python BJT fixture parity failed: ' + actual.get('case', '?'))
    return [case['case'] for case in left]


def reject_missing_dll(module, backend, base):
    # Use the exact module alias loaded by _bjt_native_edge_backend; importing
    # bjt_mesh_native again would exercise a different cache and instance.
    wrapper = sys.modules[type(backend).__module__]
    original = wrapper._library_path
    wrapper._library_path = lambda: Path(base) / 'native/__missing_rust_release_probe__.dll'
    wrapper._load_backend.cache_clear()
    try:
        try:
            module._bjt_native_edge_backend('rust')
        except wrapper.NativeUnavailableError:
            return True
        raise RuntimeError('Missing Rust DLL silently fell back during release verification')
    finally:
        wrapper._library_path = original
        wrapper._load_backend.cache_clear()


def run(base):
    from suite_loader import ToolLoader
    base = Path(base).resolve()
    if not getattr(sys, 'frozen', False) or base.name != '_internal':
        raise RuntimeError('Rust release proof must run inside the frozen Windows bundle')
    native = verify_native(base / 'native')
    module = ToolLoader().load('bjt')
    backend = module._bjt_native_edge_backend('rust')
    expected_path = (base / 'native/bjt_mesh_native.dll').resolve()
    if backend.path != expected_path or not backend.can_pack_grid:
        raise RuntimeError('Production BJT must load _internal/native/bjt_mesh_native.dll with packing')
    rejected = reject_missing_dll(module, backend, base)
    backend = module._bjt_native_edge_backend('rust')
    if backend.path != expected_path or not backend.can_pack_grid:
        raise RuntimeError('Production Rust backend changed after the absence test')
    helper = base / 'frozen_validation/bjt_fixtures.py'
    spec = importlib.util.spec_from_file_location('_bjt_frozen_rust_fixtures', helper)
    fixtures = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fixtures
    spec.loader.exec_module(fixtures)
    usage = {name: {'success_calls': 0, 'rows': 0} for name in ('sort_count', 'pack_grid_triangles')}
    originals = {name: getattr(backend, name) for name in usage}
    def instrument(name):
        def execute(rows, *args, **kwargs):
            value = originals[name](rows, *args, **kwargs)
            if len(rows):
                usage[name]['success_calls'] += 1
                usage[name]['rows'] += len(rows)
            return value
        return execute
    previous = os.environ.get('BJT_MESH_BACKEND')
    try:
        for name in usage:
            setattr(backend, name, instrument(name))
        with tempfile.TemporaryDirectory(prefix='BJT_frozen_Rust_') as directory:
            os.environ['BJT_MESH_BACKEND'] = 'rust'
            if module._bjt_native_edge_backend() is not backend:
                raise RuntimeError('Instrumented instance is not the production Rust backend')
            rust = fixtures.run(module, Path(directory) / 'rust')
            if any(value['success_calls'] <= 0 or value['rows'] <= 0 for value in usage.values()):
                raise RuntimeError('BJT fixtures did not execute both nonempty Rust kernels')
            rust_usage = {name: dict(value) for name, value in usage.items()}
            os.environ['BJT_MESH_BACKEND'] = 'python'
            if module._bjt_native_edge_backend() is not None:
                raise RuntimeError('Python parity fixtures must use the Python backend')
            python = fixtures.run(module, Path(directory) / 'python')
            if usage != rust_usage:
                raise RuntimeError('Forced Python fixtures unexpectedly called Rust')
            cases = compare_fixtures(rust, python)
    finally:
        for name, original in originals.items():
            setattr(backend, name, original)
        if previous is None:
            os.environ.pop('BJT_MESH_BACKEND', None)
        else:
            os.environ['BJT_MESH_BACKEND'] = previous
    return {'passed': True, 'frozen': True, 'commit': os.environ.get('BJT_RELEASE_COMMIT'),
            'native': native, 'backend_path': str(backend.path), 'can_pack_grid': True,
            'missing_dll_rejected': rejected, 'native_calls': rust_usage,
            'parity': True, 'fixture_count': len(cases), 'cases': cases,
            'rust_fixtures': rust, 'python_fixtures': python,
            'source_manifest_sha256': digest(base / 'source_manifest.json'),
            'executable_sha256': digest(sys.executable)}
