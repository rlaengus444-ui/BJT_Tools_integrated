"""Frozen-only launcher: no Python discovery, pip or original bootstrap spawn."""
from __future__ import annotations
import multiprocessing
import os
import sys
sys.dont_write_bytecode = True
_windowed_build = sys.stdout is None and sys.stderr is None

# Frozen Python does not honor PYTHONUTF8. Keep redirected CLI JSON and Korean
# status text usable on Windows systems whose ANSI code page is not Korean.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='backslashreplace')

if __name__ == '__main__':
    multiprocessing.freeze_support()

import importlib
import importlib.metadata
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent / 'app-source'))
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
VERSION = json.loads((BASE / 'source_manifest.json').read_text(encoding='utf-8'))['suite_version']
LOCAL_STATE = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()).resolve()
STATE = LOCAL_STATE / 'BJT_통합프로그램' / VERSION
STATE.mkdir(parents=True, exist_ok=True)
# Numba cache filenames include the original (long) function name and an
# atomic-write suffix. Keep our default short, isolate each signed manifest,
# and allow long Windows usernames/redirected profiles without MAX_PATH errors.
build_key = hashlib.sha256((BASE / 'source_manifest.json').read_bytes()).hexdigest()[:10]
cache_root = LOCAL_STATE / 'BJT' / ('n' + VERSION.replace('.', '') + '_' + build_key)
cache_root.mkdir(parents=True, exist_ok=True)
cache_path = str(cache_root)
if os.name == 'nt' and not cache_path.startswith('\\\\?\\'):
    cache_path = ('\\\\?\\UNC\\' + cache_path[2:]) if cache_path.startswith('\\\\') else ('\\\\?\\' + cache_path)
os.environ.setdefault('NUMBA_CACHE_DIR', cache_path)
os.environ.setdefault('MPLCONFIGDIR', str(STATE / 'matplotlib-cache'))
os.environ['PYTHONNOUSERSITE'] = '1'

REQUIRED = ('numpy', 'scipy', 'scipy.optimize', 'scipy.ndimage', 'PIL.ImageTk',
            'skimage.measure', 'trimesh', 'shapely', 'numba', 'numba.typed',
            'llvmlite.binding', 'rtree', 'cv2', 'tkinterdnd2', 'vtk',
            'matplotlib.backends.backend_tkagg', 'tifffile', 'png', 'imagecodecs',
            'manifold3d', 'fcl', 'plotly.offline')
_validated = False

def embedded_runtime_check():
    global _validated
    if _validated:
        return
    failures = {}
    for name in REQUIRED:
        try:
            importlib.import_module(name)
        except Exception:
            failures[name] = traceback.format_exc()
    if failures:
        raise RuntimeError('내장 라이브러리를 불러오지 못했습니다. Python 설치를 시도하지 않습니다.\n' + json.dumps(failures, ensure_ascii=False, indent=2))
    import imagecodecs
    if not imagecodecs.PNG.available:
        raise RuntimeError('내장 PNG 디코더를 사용할 수 없습니다.')
    _validated = True


def _patch_runtime():
    import suite_runtime
    suite_runtime.ensure_runtime = embedded_runtime_check


def _write(path, data):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def diagnose():
    started = time.monotonic()
    embedded_runtime_check()
    import numpy as np
    import imagecodecs
    import cv2
    import vtk
    import scipy.optimize
    import scipy.ndimage
    import trimesh
    from shapely.geometry import box
    from rtree import index
    from suite_loader import ToolLoader, verify_sources, tool_specs
    checks = {}
    checks['asset_hashes'] = verify_sources()
    if not all(item['valid'] for item in checks['asset_hashes']):
        raise RuntimeError('Bundled source hash mismatch')
    samples = np.array([[0, 1, 255, 256, 65535]], np.uint16)
    checks['png16_native'] = bool(np.array_equal(samples, imagecodecs.png_decode(imagecodecs.png_encode(samples))))
    checks['cv2_native'] = cv2.connectedComponents(np.array([[0, 0], [0, 255]], np.uint8))[0] == 2
    checks['shapely_geos'] = bool(box(0, 0, 1, 1).buffer(.1).is_valid)
    spatial = index.Index()
    spatial.insert(7, (0, 0, 1, 1))
    checks['rtree_spatialindex'] = list(spatial.intersection((0, 0, 1, 1))) == [7]
    spatial.close()
    sphere = vtk.vtkSphereSource()
    sphere.Update()
    checks['vtk_native'] = sphere.GetOutput().GetNumberOfPoints() > 0
    fit = scipy.optimize.least_squares(lambda x: x - 3, [0.])
    checks['scipy_optimize'] = bool(abs(fit.x[0] - 3) < 1e-6)
    checks['scipy_ndimage'] = int(scipy.ndimage.binary_erosion(np.ones((3, 3), bool)).sum()) == 1
    checks['trimesh'] = bool(trimesh.creation.box().is_watertight)
    from chitu_overlap.geometry import MeshObject, check_overlaps
    from plotly.offline import get_plotlyjs
    first = trimesh.creation.box()
    second = trimesh.creation.box()
    second.apply_translation((1, 0, 0))
    contact = check_overlaps([MeshObject('a', first), MeshObject('b', second)])
    checks['chitu_manifold_fcl_contact'] = contact['complete'] and contact['pairs'][0]['status'] == 'touching'
    checks['plotly_offline_bundle'] = len(get_plotlyjs()) > 1000000
    loader = ToolLoader()
    modules = {key: loader.load(key) for key in tool_specs()}
    bjt = modules['bjt']
    rectangles = bjt._bjt_rectangles(np.ones((3, 4), np.bool_))
    checks['bjt_numba_jit'] = len(rectangles) > 0
    checks['dynamic_tools_loaded'] = sorted(modules)
    checks['distribution_metadata'] = {
        name: importlib.metadata.version(name) for name in
        ('numpy', 'Pillow', 'scipy', 'scikit-image', 'trimesh', 'shapely', 'numba',
         'llvmlite', 'rtree', 'opencv-python', 'tkinterdnd2', 'vtk', 'matplotlib',
         'tifffile', 'pypng', 'imagecodecs', 'manifold3d', 'python-fcl', 'plotly')}
    checks['native_module_paths'] = {name: str(getattr(importlib.import_module(name), '__file__', ''))
                                    for name in ('numpy', 'scipy', 'PIL', 'numba', 'llvmlite', 'vtk', 'cv2', 'imagecodecs')}
    if os.name == 'nt':
        import ctypes as C
        from ctypes import wintypes as W
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = W.HANDLE
        kernel.K32EnumProcessModules.argtypes = [W.HANDLE, C.POINTER(C.c_void_p), W.DWORD, C.POINTER(W.DWORD)]
        kernel.K32EnumProcessModules.restype = W.BOOL
        kernel.GetModuleFileNameW.argtypes = [C.c_void_p, W.LPWSTR, W.DWORD]
        kernel.GetModuleFileNameW.restype = W.DWORD
        modules_buffer = (C.c_void_p * 4096)()
        needed = W.DWORD()
        if not kernel.K32EnumProcessModules(kernel.GetCurrentProcess(), modules_buffer, C.sizeof(modules_buffer), C.byref(needed)):
            raise C.WinError(C.get_last_error())
        runtime_paths = []
        for handle in modules_buffer[:needed.value // C.sizeof(C.c_void_p)]:
            path_buffer = C.create_unicode_buffer(32768)
            if kernel.GetModuleFileNameW(handle, path_buffer, len(path_buffer)) and Path(path_buffer.value).name.lower() == 'ucrtbase.dll':
                runtime_paths.append(path_buffer.value)
        checks['ucrt_module_paths'] = runtime_paths
        system_runtime = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/ucrtbase.dll')
        checks['one_system_ucrt'] = len(runtime_paths) == 1 and os.path.normcase(runtime_paths[0]) == os.path.normcase(system_runtime)
    return {'passed': all(value is True for value in checks.values() if isinstance(value, bool)),
            'frozen': bool(getattr(sys, 'frozen', False)), 'python': sys.version,
            'asset_root': str(BASE), 'state_root': str(STATE),
            'numba_cache_root': os.environ['NUMBA_CACHE_DIR'],
            'seconds': round(time.monotonic() - started, 3), 'checks': checks}


def gui_smoke():
    embedded_runtime_check()
    import tkinter as tk
    from suite_ui import SuiteApp, create_root
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    root = create_root()
    root.withdraw()
    errors = []
    root.tk.eval('proc bgerror {msg} {global bjtFrozenBgErrors; lappend bjtFrozenBgErrors $msg}')
    root.tk.eval('set bjtFrozenBgErrors {}')
    app = SuiteApp(root, test_mode=True)
    root.report_callback_exception = lambda typ, value, tb: errors.append(''.join(traceback.format_exception(typ, value, tb)))
    loaded = []
    checks = {'tkdnd_available': bool(getattr(root, '_suite_dnd_available', False))}
    try:
        for key in app.specs:
            module = app.loader.load(key)
            app.mount_tool(key, module)
            root.update()
            loaded.append(key)
        figure = Figure(figsize=(2, 1), dpi=80)
        figure.add_subplot().plot([0, 1], [0, 1])
        plot = FigureCanvasTkAgg(figure, master=root)
        plot.draw()
        plot.get_tk_widget().destroy()
        checks['matplotlib_tkagg_render'] = True
        checks['tkdnd_tcl_version'] = str(root.tk.call('package', 'require', 'tkdnd'))
        checks['tools_loaded'] = loaded
        checks['python_errors'] = errors
        checks['load_failures'] = app.failures
        app.request_close(confirm=False)
        for _ in range(20):
            try:
                root.update()
            except tk.TclError:
                break
            time.sleep(.01)
        checks['close_errors'] = app.close_errors
        checks['tcl_bgerrors'] = list(root.tk.splitlist(root.tk.eval('set bjtFrozenBgErrors')))
        return {'passed': checks['tkdnd_available'] and not errors and not app.failures and not app.close_errors and not checks['tcl_bgerrors'],
                'frozen': bool(getattr(sys, 'frozen', False)), 'tool_count': len(loaded), 'checks': checks}
    finally:
        try:
            if root.winfo_exists():
                app.request_close(confirm=False)
        except tk.TclError:
            pass


def tool_dispatch(arguments):
    # Never run BJT/alignment source as __main__: their standalone bootstraps
    # interpret sys.executable as Python, which a frozen bootloader is not.
    from suite_loader import ToolLoader, tool_specs
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--tool', required=True, choices=list(tool_specs()))
    parsed, extra = parser.parse_known_args(arguments)
    if extra[:1] == ['--']:
        extra = extra[1:]
    if extra and parsed.tool == 'chitu_checker':
        from chitu_overlap.cli import main as chitu_main
        return chitu_main(extra)
    if extra and parsed.tool in ('bjt', 'alignment'):
        module = ToolLoader().load(parsed.tool)
        sys.argv = [str(BASE / tool_specs()[parsed.tool]['source']), *extra]
        result = module.main() if parsed.tool == 'bjt' else module.main(extra)
        return result if isinstance(result, int) else 0
    from suite_ui import SuiteApp, create_root
    root = create_root()
    app = SuiteApp(root)
    app.select_tool(parsed.tool)
    root.mainloop()
    return 0


def main():
    _patch_runtime()
    arguments = sys.argv[1:]
    if arguments[:1] == ['--frozen-oven-playback']:
        path = arguments[1] if len(arguments) > 1 else str(STATE / 'oven-playback.json')
        try:
            embedded_runtime_check()
            from frozen_oven_playback import run as run_oven_playback
            report = run_oven_playback(path)
            _write(path, report)
            return 0 if report['passed'] else 2
        except Exception:
            _write(path, {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)),
                          'traceback': traceback.format_exc()})
            return 2
    if arguments[:1] == ['--frozen-suite-features']:
        path = arguments[3] if len(arguments) > 3 else str(STATE / 'suite-features.json')
        try:
            embedded_runtime_check()
            if len(arguments) != 4:
                raise ValueError('Expected verification phase, isolated directory and report path')
            from frozen_suite_features import run as run_suite_features
            report = run_suite_features(arguments[1], arguments[2])
            _write(path, report)
            return 0 if report['passed'] else 2
        except Exception:
            _write(path, {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)),
                          'traceback': traceback.format_exc()})
            return 2
    if arguments[:1] in (['--frozen-chitu-gui'], ['--frozen-large-gui'], ['--frozen-camera-gui']):
        path = arguments[2] if len(arguments) > 2 else str(STATE / 'chitu-gui.json')
        try:
            embedded_runtime_check()
            if arguments[0] == '--frozen-large-gui':
                from frozen_large_chitu_gui import main as audit_chitu_gui
            elif arguments[0] == '--frozen-camera-gui':
                from frozen_camera_chitu_gui import main as audit_chitu_gui
            else:
                from frozen_chitu_gui import main as audit_chitu_gui
            sys.argv = [sys.argv[0], '--app', str(BASE), '--project', arguments[1],
                        '--out', path, '--screenshot', str(Path(path).with_suffix('.png'))]
            exit_code = audit_chitu_gui()
            report = json.loads(Path(path).read_text(encoding='utf-8'))
            report['frozen'] = bool(getattr(sys, 'frozen', False))
            _write(path, report)
            return exit_code
        except Exception:
            _write(path, {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)), 'traceback': traceback.format_exc()})
            return 2
    if arguments[:1] in (['--frozen-diagnose'], ['--frozen-smoke'], ['--frozen-fixtures'], ['--frozen-product-fixtures']):
        path = arguments[1] if len(arguments) > 1 else str(STATE / 'diagnostic.json')
        try:
            if arguments[0] in ('--frozen-fixtures', '--frozen-product-fixtures'):
                embedded_runtime_check()
                from frozen_fixtures import run
                report = run(BASE, input_zip_only=arguments[0] == '--frozen-product-fixtures')
            else:
                report = diagnose() if arguments[0] == '--frozen-diagnose' else gui_smoke()
            _write(path, report)
            return 0 if report['passed'] else 2
        except Exception:
            _write(path, {'passed': False, 'frozen': bool(getattr(sys, 'frozen', False)), 'traceback': traceback.format_exc()})
            return 2
    if '--tool' in arguments:
        embedded_runtime_check()
        return tool_dispatch(arguments)
    import run_suite
    return run_suite.start() if _windowed_build else run_suite.main(arguments)


if __name__ == '__main__':
    if sys.stdout is None or sys.stderr is None:
        log = (STATE / 'application.log').open('a', encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        trace = traceback.format_exc()
        (STATE / 'startup-error.log').write_text(trace, encoding='utf-8')
        if _windowed_build:
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror('BJT 통합프로그램 · 실행 오류', trace[-3500:], parent=root)
                root.destroy()
            except Exception:
                pass
        else:
            print(trace, file=sys.stderr)
        raise SystemExit(2)
