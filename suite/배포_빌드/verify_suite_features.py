"""Run new-feature gates through genuine EXEs with two fresh processes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    folder = args.folder.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                'TCL_LIBRARY', 'TK_LIBRARY', 'NUMBA_CACHE_DIR', 'MPLCONFIGDIR'):
        environment.pop(key, None)
    windows = Path(environment.get('SystemRoot', 'C:/Windows'))
    environment['PATH'] = os.pathsep.join(str(path) for path in
        (windows / 'System32', windows, windows / 'System32/Wbem'))
    checks = []
    with tempfile.TemporaryDirectory(prefix='BJT_EXE_170_features_') as directory:
        work = Path(directory)
        environment['LOCALAPPDATA'] = str(work / 'Local')
        environment['APPDATA'] = str(work / 'Roaming')
        for phase, executable in (('save', 'BJT_통합프로그램.exe'), ('restore', 'BJT_명령줄.exe')):
            report_path = output.with_name(output.stem + '-' + phase + '.json')
            started = time.monotonic()
            child = subprocess.run([str(folder / executable), '--frozen-suite-features',
                phase, str(work), str(report_path)], env=environment,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
            passed = child.returncode == 0 and report.get('passed') and report.get('frozen')
            checks.append({'phase': phase, 'passed': bool(passed), 'executable': executable,
                'exit_code': child.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3),
                'report': report, 'stdout': child.stdout[-4000:] if not passed else '',
                'stderr': child.stderr[-4000:] if not passed else ''})
            if not passed:
                break
    report = {'passed': len(checks) == 2 and all(item['passed'] for item in checks),
        'frozen': True, 'python_removed_from_child_path': True,
        'scope': 'GUI EXE verifies packing and saves real controller inputs; separate CLI EXE process restores all 13 tool inputs',
        'checks': checks}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'phases': len(checks)}))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
