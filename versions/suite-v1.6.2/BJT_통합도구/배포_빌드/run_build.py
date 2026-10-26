"""Isolated build/test runner; all mutable build output stays under this folder."""
from pathlib import Path
import json
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PYTHON = Path(os.environ.get('BJT_BUILD_PYTHON', str(HERE / 'build-python' / 'python.exe'))).expanduser().resolve()
sys.path.insert(0, str(HERE / 'app-source'))
from suite_runtime import process_environment
environment = process_environment(PYTHON)
# The source ZIP carries the genuine redistributable runtime used by FCL/Numba.
# Build/source checks may resolve it here; EXE checks below replace PATH with
# clean system directories and must use the packaged copy.
if (HERE / 'vendor-runtime').is_dir():
    environment['PATH'] = str(HERE / 'vendor-runtime') + os.pathsep + environment.get('PATH', '')
environment['PYTHONUTF8'] = '1'
action = sys.argv[1] if len(sys.argv) > 1 else 'build'
dist = HERE / 'dist'
assert dist.is_relative_to(HERE) and dist != HERE
if action == 'build':
    command = [str(PYTHON), '-m', 'PyInstaller', str(HERE / 'BJT_Suite.spec'),
               '--noconfirm', '--distpath', str(dist), '--workpath', str(HERE / 'build')]
elif action in ('diagnose-source', 'fixtures-source'):
    flag = '--frozen-diagnose' if action == 'diagnose-source' else '--frozen-fixtures'
    command = [str(PYTHON), str(HERE / 'bjt_frozen_entry.py'), flag, str(HERE / (action + '.json'))]
elif action in ('diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe', 'fixtures-gui-exe', 'product-fixtures-exe', 'product-fixtures-gui-exe'):
    # Reproduce a machine without Python/Conda on PATH. A bundled DLL must not
    # silently resolve from either development environment during verification.
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'TCL_LIBRARY', 'TK_LIBRARY',
                'NUMBA_CACHE_DIR', 'MPLCONFIGDIR'):
        environment.pop(key, None)
    windows = Path(environment.get('SystemRoot', 'C:/Windows'))
    environment['PATH'] = os.pathsep.join(str(path) for path in (windows / 'System32', windows, windows / 'System32' / 'Wbem'))
    environment['PYTHONUTF8'] = '1'
    # Clean writable profile proves the embedded-core/JIT path works on first
    # launch, without borrowing an earlier desktop-tool cache.
    profile_root = Path(os.environ.get('BJT_TEST_PROFILE', str(HERE / 'test-profile'))).resolve()
    environment['LOCALAPPDATA'] = str(profile_root / 'Local')
    environment['APPDATA'] = str(profile_root / 'Roaming')
    executable = dist / 'BJT_통합프로그램_1.6.2_EXE' / ('BJT_통합프로그램.exe' if action in ('smoke-gui-exe', 'fixtures-gui-exe', 'product-fixtures-gui-exe') else 'BJT_명령줄.exe')
    flag = {'diagnose-exe': '--frozen-diagnose', 'smoke-exe': '--frozen-smoke', 'smoke-gui-exe': '--frozen-smoke', 'fixtures-exe': '--frozen-fixtures', 'fixtures-gui-exe': '--frozen-fixtures', 'product-fixtures-exe': '--frozen-product-fixtures', 'product-fixtures-gui-exe': '--frozen-product-fixtures'}[action]
    command = [str(executable), flag, str(HERE / (action + '.json'))]
else:
    raise SystemExit('Unknown action: ' + action)
log_path = HERE / (action + '.log')
with log_path.open('w', encoding='utf-8') as log:
    result = subprocess.run(command, cwd=HERE, env=environment, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
exit_code = result.returncode
if action in ('diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe', 'fixtures-gui-exe', 'product-fixtures-exe', 'product-fixtures-gui-exe'):
    report_path = HERE / (action + '.json')
    if not report_path.is_file() or not json.loads(report_path.read_text(encoding='utf-8')).get('passed'):
        exit_code = exit_code or 2
print(action, 'exit', exit_code, 'log', log_path)
raise SystemExit(exit_code)
