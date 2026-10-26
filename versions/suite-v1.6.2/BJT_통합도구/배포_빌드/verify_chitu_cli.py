"""Verify packaged Chitu CLI against a supplied genuine project without editing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-distance', type=float, default=2.924621820449829)
    args = parser.parse_args()
    exe = args.folder.resolve() / 'BJT_명령줄.exe'
    project = args.project.resolve()
    original_hash = hashlib.sha256(project.read_bytes()).hexdigest()
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                 'TCL_LIBRARY', 'TK_LIBRARY', 'NUMBA_CACHE_DIR', 'MPLCONFIGDIR'):
        environment.pop(name, None)
    windows = Path(environment.get('SystemRoot', 'C:/Windows'))
    environment['PATH'] = os.pathsep.join(str(path) for path in (windows / 'System32', windows, windows / 'System32/Wbem'))
    environment['PYTHONUTF8'] = '1'
    checks = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='BJT_Chitu_EXE_') as directory:
        work = Path(directory)
        for gap, expected in ((1, 0), (3, 1)):
            result_path, html_path = work / f'gap-{gap}.json', work / f'gap-{gap}.html'
            command = [str(exe), '--tool', 'chitu_checker', str(project),
                       '--min-gap-mm', str(gap), '--json', str(result_path), '--html', str(html_path)]
            result = subprocess.run(command, env=environment, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=180)
            report = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
            distances = [pair['distance_mm'] for pair in report.get('pairs', []) if pair.get('distance_mm') is not None]
            actual_distance = min(distances) if distances else None
            checks.append({
                'name': f'genuine_project_gap_{gap}mm', 'exit_code': result.returncode,
                'expected_exit_code': expected,
                'passed': result.returncode == expected and report.get('complete') is True
                          and actual_distance is not None
                          and abs(actual_distance - args.expected_distance) < 1e-8
                          and html_path.is_file() and html_path.stat().st_size > 1000000,
                'object_count': report.get('object_count'), 'status': report.get('status'),
                'minimum_distance_mm': actual_distance, 'html_bytes': html_path.stat().st_size if html_path.is_file() else None,
                'stdout': result.stdout[-3000:], 'stderr': result.stderr[-3000:],
            })
        bad_input, bad_result = work / 'unsupported.chitu', work / 'unsupported.json'
        bad_input.write_bytes(b'unsupported project format')
        result = subprocess.run([str(exe), '--tool', 'chitu_checker', str(bad_input), '--json', str(bad_result)],
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=180)
        report = json.loads(bad_result.read_text(encoding='utf-8')) if bad_result.is_file() else {}
        checks.append({'name': 'unsupported_format_exit_2', 'exit_code': result.returncode,
                       'passed': result.returncode == 2 and report.get('status') == 'inconclusive',
                       'stdout': result.stdout[-3000:], 'stderr': result.stderr[-3000:]})
    checks.append({'name': 'original_project_unchanged',
                   'passed': original_hash == hashlib.sha256(project.read_bytes()).hexdigest()})
    report = {'passed': all(check['passed'] for check in checks), 'frozen_executable': str(exe),
              'source_project': project.name, 'source_sha256': original_hash,
              'python_removed_from_child_path': True, 'seconds': time.monotonic() - started, 'checks': checks}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(checks)}, ensure_ascii=True))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
