"""Frozen suite IR integration checks with owned synthetic inputs only.

The test-mode controller is replaced by an explicit offline runner before
Connect. No Windows discovery, printer process, Excel COM or heater is used.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
from pathlib import Path
import sys
import threading
import time
import traceback
from unittest.mock import patch


WORKBOOK_SHA256 = 'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20'
BRIDGE_SHA256 = '939c65d211ff9d144e956eb7b2bae26c4cfab80437c2f0a6c60efa70f4c1b215'


def _exists(root):
    try:
        return bool(root.winfo_exists())
    except Exception:
        return False


def _until(root, predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for owned offline IR fixture')
        root.update()
        time.sleep(.01)


def run(base, report_path):
    import tkinter as tk
    from PIL import Image
    from suite_ui import SuiteApp, create_root
    from suite_loader import verify_sources
    from ir_feedback.adapter_contracts import MachineWindow, UiSnapshot
    from ir_feedback.excel_workbook import PreparedWorkbook
    from ir_feedback.layer_charts import _write_csv
    from ir_feedback.plot_archive import PlotArchive
    from ir_feedback.slice_data import SliceConfig, analyze

    started = time.monotonic()
    base = Path(base).resolve()
    work = Path(report_path).resolve().with_suffix('')
    work.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)),
              'checks': {}, 'callback_errors': [],
              'scope': 'Owned synthetic suite/IR session, real bundled resources/Pillow/CSV/PNG; no equipment or Excel COM',
              'excel_com_verified': False, 'equipment_verified': False}
    checks = report['checks']
    root = create_root()
    root.withdraw()
    app = None
    archive_gate = threading.Event()
    archive_started = threading.Event()
    try:
        app = SuiteApp(root, test_mode=True)
        root.report_callback_exception = lambda typ, value, tb: report['callback_errors'].append(
            ''.join(traceback.format_exception(typ, value, tb)))
        strict = verify_sources()
        assert strict and all(item['valid'] for item in strict)
        checks['strict_manifest_resources'] = {'passed': True, 'checks': len(strict)}
        module = app.loader.load('ir_feedback')
        native_attempts = []
        def forbid_native(*args, **kwargs):
            native_attempts.append('native subprocess attempted during test-mode initialization')
            raise AssertionError(native_attempts[-1])
        with patch('subprocess.Popen', side_effect=forbid_native):
            ir = app.mount_tool('ir_feedback', module)
            app.select_tool('ir_feedback')
            root.update()
        assert not native_attempts, native_attempts
        assert ir.controller.thread is None
        assert not ir.searching and ir.discovery_thread is None
        checks['test_mode_initialization_has_no_native_process'] = {'passed': True,
            'native_process_attempts': len(native_attempts), 'auto_discovery_started': False}
        assert ir.algorithm_type.get() == module.BUILTIN_LABEL
        spec = module.builtin_workbook_spec()
        spec.validate()
        expected = {'sheet': '수동 테스트', 'temperature_cell': 'B13',
                    'actual_power_cell': 'C13', 'recommendation_cell': 'D13',
                    'target_cell': 'B5', 'layer_cell': 'A13', 'slice_start_cell': 'BL13'}
        actual = asdict(spec)
        assert all(actual[name] == value for name, value in expected.items()), actual
        workbook = Path(spec.path).resolve()
        assert workbook.is_relative_to(base)
        workbook_bytes = workbook.read_bytes()
        assert hashlib.sha256(workbook_bytes).hexdigest() == WORKBOOK_SHA256 == module.BUILTIN_SHA256
        package = base / 'ir_feedback'
        assert hashlib.sha256((package / 'UiaBridge.dll').read_bytes()).hexdigest() == BRIDGE_SHA256
        for name in ('native_helper.ps1', 'excel_worker.ps1'):
            assert (package / name).is_file() and (package / name).stat().st_size > 100
        settings = list(package.glob('*.json'))
        assert settings, 'IR JSON runtime configuration missing'
        checks['authentic_builtin_workbook_and_worker_assets'] = {'passed': True,
            'workbook_sha256': WORKBOOK_SHA256, 'workbook_bytes': workbook.stat().st_size,
            'bridge_sha256': BRIDGE_SHA256, 'mapping': expected,
            'json_files': [p.name for p in settings]}

        # Validate the genuine formulas using the unchanged bounded production
        # parser on an owned background thread; never freeze Tk for318k formulas.
        prepared = []
        preparation_errors = []
        def prepare():
            try:
                prepared.append(PreparedWorkbook.from_bytes(spec, workbook_bytes))
            except Exception:
                preparation_errors.append(traceback.format_exc())
        preparation = threading.Thread(target=prepare, name='owned-workbook-validation', daemon=True)
        preparation.start()
        _until(root, lambda: not preparation.is_alive(), timeout=180)
        assert not preparation_errors and len(prepared) == 1, preparation_errors
        metadata = prepared[0].metadata
        assert metadata['sha256'] == WORKBOOK_SHA256 and metadata['sheet'] == '수동 테스트'
        assert metadata['first_row'] == 13 and metadata['last_row'] >= 300
        checks['production_parser_accepts_pinned_original'] = {'passed': True,
            'metadata': dict(metadata), 'background_thread': True,
            'excel_calculated': False}

        # The actual image panel handles explicit multi-select inputs. Synthetic
        # images are large enough for a1mm circle at400DPI, white is binder.
        images = []
        for layer, value in ((1, 0), (2, 255), (3, 0)):
            path = work / f'{layer}.png'
            Image.new('L', (64, 64), value).save(path)
            images.append(str(path))
        config = SliceConfig(radius_mm=1)
        dataset = analyze(images, config)
        assert [r.center_coverage_pct for r in dataset.records] == [0., 100., 0.]
        assert dataset.features(3)['previous_center_coverage_pct'] == 100.
        ir.slice_panel.radius.set('1')
        ir.slice_panel.set_paths(tuple(images))
        _until(root, lambda: ir.slice_panel.dataset is not None and not ir.slice_panel.running)
        assert ir.slice_panel.dataset.fingerprint == dataset.fingerprint
        checks['manual_images_and_previous_layer_features'] = {'passed': True,
            'layers': len(dataset.records), 'white_binder': True, 'dpi': [400, 400]}

        # Record through the genuine SessionController/Tk event pump. The only
        # runner invoked here has no adapter or workbook engine.
        target = MachineWindow('M1', 1234, 5678, 'Owned offline BJT fixture', 'owned-fixture.exe')
        received = []
        def offline_runner(options, stop_event, emit):
            received.append(options)
            emit('connected', {})
            for index, temperature in enumerate((65., 62., 64.)):
                snapshot = UiSnapshot(target=target, layer_index=index, t_recoating_c=temperature,
                    observation_key=f'offline-{index}', heater1_setting_percent=60.,
                    heater2_setting_percent=60., machine_state='AUTO', control_available=True,
                    run_key='owned-fixture', observation_is_new=True, observation_fresh=True)
                emit('snapshot', {'snapshot': snapshot, 'applied': {}, 'proposed': {},
                    'effective_mode': 'control', 'reasons': {}, 'status': 'owned offline fixture',
                    'display_target_c': 65.})
            stop_event.wait()
        ir.controller.runner = offline_runner
        ir.targets = [target]
        ir._populate_hosts()
        ir.connect()
        _until(root, lambda: ir.controller.state == 'connected' and len(ir.charts.history.points) == 3)
        assert len(received) == 1 and received[0].workbook_spec is not None
        assert received[0].slice_dataset.fingerprint == dataset.fingerprint
        # Tab changes and resize must not retire the session or reset history.
        app.notebook.select(app.home)
        root.geometry('1120x780+0+0')
        root.update()
        app.select_tool('ir_feedback')
        root.geometry('1660x950+0+0')
        root.update()
        assert ir.controller.state == 'connected' and len(ir.charts.history.points) == 3
        checks['offline_connect_tab_resize_preserves_session'] = {'passed': True,
            'observed_layers': 3, 'save_calls': 0, 'real_adapters_created': 0}

        # Make archive completion observable: suite root must stay alive while
        # the owned writer is still working, then close only after all3 files.
        ir.archive.close()
        def gated_csv(path, points):
            archive_started.set()
            if not archive_gate.wait(15):
                raise RuntimeError('Owned test archive gate was not released')
            _write_csv(path, points)
        ir.archive = PlotArchive(directories=[work / 'records'], writer=gated_csv)
        app.request_close(confirm=False)
        _until(root, archive_started.is_set)
        assert _exists(root), 'Suite destroyed while archive CSV writer was pending'
        assert ir.archive.busy
        archive_gate.set()
        _until(root, lambda: not _exists(root))
        assert not ir.controller.worker_alive
        assert not app.close_errors and not report['callback_errors']
        csv_files = list((work / 'records').glob('*.csv'))
        png_files = list((work / 'records').glob('*.png'))
        assert len(csv_files) == 1 and len(png_files) == 2
        with csv_files[0].open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        assert len(rows) == 4 and [row[0] for row in rows[1:]] == ['1', '2', '3']
        for path in png_files:
            with Image.open(path) as image:
                image.verify()
        checks['asynchronous_close_preserves_csv_and_two_plots'] = {'passed': True,
            'rows': len(rows) - 1, 'files': [p.name for p in csv_files + png_files],
            'writer_pending_keeps_suite_alive': True, 'worker_retired': True}
        checks['callback_errors'] = {'passed': not report['callback_errors']}
        report['passed'] = all(item['passed'] for item in checks.values())
    except Exception:
        report['traceback'] = traceback.format_exc()
    finally:
        archive_gate.set()
        if app is not None and _exists(root):
            try:
                if not app.closing:
                    app.request_close(confirm=False)
                _until(root, lambda: not _exists(root), timeout=10)
            except Exception:
                if _exists(root):
                    root.destroy()
        elif _exists(root):
            root.destroy()
        report['seconds'] = round(time.monotonic() - started, 3)
    return report
