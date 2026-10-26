"""Opt-in package verification; synthetic input and output live in a temp dir."""
from __future__ import annotations
import importlib.util
import gc
import io
from pathlib import Path
import sys
import tempfile
import time
import traceback
import unittest
import threading
from unittest import mock


def _load(path):
    name = '_bjt_frozen_fixture_' + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _existing_zip_checks(base):
    import slice_compare_engine as engine
    seed = Path(base) / 'frozen_validation/zip_inputs'
    records, opened = [], []
    original = engine.zipfile.ZipFile
    def open_archive(*args, **kwargs):
        assert kwargs.get('mode', args[1] if len(args) > 1 else 'r') == 'r'
        archive = original(*args, **kwargs)
        opened.append(archive)
        return archive
    def compare(left, right, cancel_event=None, progress=None):
        return engine.compare_sources(engine.SourceSpec.from_paths([left]), engine.SourceSpec.from_paths([right]),
                                      engine.CompareOptions(verify_identical=True), cancel_event=cancel_event, progress=progress)
    with mock.patch.object(engine.zipfile, 'ZipFile', side_effect=open_archive):
        for index in range(4):
            report = compare(seed / 'images', seed / 'layers.zip')
            assert report.overall_identical and report.summary['identical'] == 3
            assert all(archive.fp is None for archive in opened)
            records.append({'case': 'repeated_directory_zip_read_' + str(index + 1), 'passed': True})
        assert compare(seed / 'layers.zip', seed / 'layers.zip').overall_identical
        records.append({'case': 'zip_zip_read', 'passed': True})
        report = compare(seed / 'images', seed / 'different.zip')
        assert report.summary['different'] == 1 and not report.overall_identical
        records.append({'case': 'different_native_pixels', 'passed': True})
        report = compare(seed / 'images', seed / 'corrupt.zip')
        assert report.summary['error'] >= 1 and not report.overall_identical
        records.append({'case': 'corrupt_image_inside_existing_zip', 'passed': True})
        report = compare(seed / 'images', seed / 'duplicate.zip')
        assert report.summary['error'] >= 1 and not report.overall_identical
        records.append({'case': 'duplicate_existing_zip_members', 'passed': True})
        cancel = threading.Event()
        report = compare(seed / 'layers.zip', seed / 'layers.zip', cancel_event=cancel,
                         progress=lambda percent, message: cancel.set() if message.startswith('B:') else None)
        assert report.cancelled and not report.overall_identical
        assert all(archive.fp is None for archive in opened)
        records.append({'case': 'cancel_and_close_existing_archives', 'passed': True})
    return {'passed': True, 'cases': records, 'opened_archives': len(opened), 'all_closed': True,
            'scope': 'After STL/thermal/M2 calculations: existing ZIPs are read, never created, appended, renamed or deleted'}


def run(base, *, input_zip_only=False):
    from suite_loader import ToolLoader, tool_specs
    started = time.monotonic()
    fixture_dir = Path(base) / 'frozen_validation'
    result = {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)), 'checks': {}}
    checks = result['checks']
    result['scope'] = 'product operations with prepared ZIP inputs' if input_zip_only else 'full developer fixtures including ZIP writing'
    if input_zip_only:
        result['environment_limitation'] = ('This PC denies .zip creation/append/delete with Win32 error 5. '
            'ZIP-writing developer cases are explicitly skipped; product ZIP reading is tested separately. '
            'Security settings and product file operations are unchanged.')
    loader = ToolLoader()
    modules = {key: loader.load(key) for key in tool_specs()}
    temporary = tempfile.TemporaryDirectory(prefix='BJT_EXE_fixture_')
    try:
        directory = temporary.name
        destination = Path(directory)
        slice_module = _load(fixture_dir / 'slice_fixtures.py')
        if input_zip_only:
            slice_module.PREPARED_INPUT_ZIP = fixture_dir / 'zip_inputs/layers.zip'
        for name, callback in (
            ('slice_tools', lambda: slice_module.run_suite(modules, destination / 'slices')),
            ('bjt_pipeline', lambda: _load(fixture_dir / 'bjt_fixtures.py').run(modules['bjt'], destination / 'bjt')),
            ('thermal_physics', lambda: _load(fixture_dir / 'thermal_fixtures.py').run(modules['thermal'], modules['thermal'])),
        ):
            try:
                value = callback()
                if name == 'slice_tools':
                    passed = value['passed'] == value['total'] and value['total'] > 0
                elif name == 'bjt_pipeline':
                    passed = bool(value['cases']) and all(case['status'] == 'passed' for case in value['cases'])
                else:
                    passed = True  # Helper asserts every scalar/array and expected physical invariants.
                    value = {'assertions': value.count, 'cases': value.cases,
                             'scope': 'Bundled solver smoke, deterministic repeat and physical invariants; no external baseline loaded'}
                checks[name] = {'passed': passed, 'result': value}
            except Exception:
                checks[name] = {'passed': False, 'traceback': traceback.format_exc()}
        for name in ('test_slice_compare', 'test_replace_validation', 'test_number_tools',
                     'test_sequence_z', 'test_m2_image_engine', 'test_image_conversion_settings',
                     'test_suite_state', 'test_oven_i18n'):
            source = fixture_dir / (name + '.py')
            if not source.is_file():
                checks[name] = {'passed': False, 'error': 'Required validation fixture is absent'}
                continue
            try:
                module = _load(source)
                if input_zip_only and name == 'test_slice_compare':
                    for test_name in unittest.defaultTestLoader.getTestCaseNames(module.ComparisonTests):
                        method = getattr(module.ComparisonTests, test_name)
                        if {'zipfile', 'zip_pair'}.intersection(method.__code__.co_names):
                            setattr(module.ComparisonTests, test_name, unittest.skip('OS denies ZIP-writing developer fixture; existing ZIP product reads covered separately')(method))
                stream = io.StringIO()
                test_result = unittest.TextTestRunner(stream=stream, verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(module))
                checks[name] = {'passed': test_result.wasSuccessful() and test_result.testsRun > 0,
                                'tests': test_result.testsRun,
                                'skipped': [{'test': str(test), 'reason': reason} for test, reason in test_result.skipped],
                                'output': stream.getvalue()}
            except Exception:
                checks[name] = {'passed': False, 'traceback': traceback.format_exc()}
        if input_zip_only:
            try:
                checks['existing_zip_after_all_calculations'] = _existing_zip_checks(base)
            except Exception:
                checks['existing_zip_after_all_calculations'] = {'passed': False, 'traceback': traceback.format_exc()}
    finally:
        gc.collect()
        try:
            temporary.cleanup()
        except Exception:
            checks['temporary_cleanup'] = {'passed': False, 'traceback': traceback.format_exc()}
    result['passed'] = bool(checks) and all(value['passed'] for value in checks.values())
    result['seconds'] = round(time.monotonic() - started, 3)
    return result
