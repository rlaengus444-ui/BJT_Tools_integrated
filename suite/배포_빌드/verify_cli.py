"""CLI and exit-code checks with no Python interpreter on the child PATH."""
from pathlib import Path
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = Path(__file__).resolve().parent
VERSION = json.loads((HERE / 'app-source/source_manifest.json').read_text(encoding='utf-8'))['suite_version']
FOLDER = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / 'dist' / f'BJT_통합프로그램_{VERSION}_EXE'
EXE = FOLDER / 'BJT_명령줄.exe'
environment = os.environ.copy()
for key in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'TCL_LIBRARY', 'TK_LIBRARY',
            'NUMBA_CACHE_DIR', 'MPLCONFIGDIR'):
    environment.pop(key, None)
windows = Path(environment.get('SystemRoot', 'C:/Windows'))
environment['PATH'] = os.pathsep.join(str(path) for path in (windows / 'System32', windows, windows / 'System32/Wbem'))
environment['LOCALAPPDATA'] = str(HERE / 'test-profile/Local')
environment['APPDATA'] = str(HERE / 'test-profile/Roaming')
environment['PYTHONUTF8'] = '1'
checks = []
def invoke(name, arguments, expected, inspect=None):
    result = subprocess.run([str(EXE), *map(str, arguments)], cwd=HERE, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                            timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    passed = result.returncode == expected
    if passed and inspect:
        passed = bool(inspect(result.stdout))
    checks.append({'name': name, 'passed': passed, 'exit_code': result.returncode,
                   'stdout': result.stdout[:1500] if not passed else '', 'stderr': result.stderr[:1500] if not passed else ''})

def png(path, value):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 0, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(bytes((0, value, value, 0, value, value)))) + chunk(b'IEND', b''))

invoke('list_11_tools', ['--list-tools'], 0, lambda s: len(json.loads(s)) == 11)
invoke('source_and_engine_hashes', ['--verify-sources'], 0, lambda s: all(c['valid'] for c in json.loads(s)))
invoke('bjt_cli_no_original_bootstrap', ['--tool', 'bjt', '--help'], 0)
invoke('alignment_cli_no_original_bootstrap', ['--tool', 'alignment', '--help'], 0)
invoke('compare_help', ['--compare-cli', '--help'], 0)
invoke('chitu_help', ['--tool', 'chitu_checker', '--help'], 0)
with tempfile.TemporaryDirectory(prefix='BJT_EXE_CLI_') as directory:
    root = Path(directory)
    left, right = root / '왼쪽 입력', root / '오른쪽 입력'
    left.mkdir(); right.mkdir()
    png(left / '1.png', 0); png(right / '1.png', 0)
    output_json, output_csv = root / '결과.json', root / '결과.csv'
    invoke('compare_identical_exit_0', ['--compare-cli', left, right, '--json', output_json, '--csv', output_csv], 0,
           lambda s: json.loads(s)['summary']['overall_identical'])
    checks.append({'name': 'compare_json_csv_exports', 'passed': output_json.is_file() and output_csv.is_file()})
    png(right / '1.png', 1)
    invoke('compare_different_exit_1', ['--compare-cli', left, right], 1,
           lambda s: json.loads(s)['summary']['different'] == 1)
    (right / '1.png').write_bytes(b'\x89PNG\r\n\x1a\ncorrupt')
    invoke('compare_error_exit_2', ['--compare-cli', left, right], 2)
report = {'passed': bool(checks) and all(c['passed'] for c in checks), 'executable': str(EXE),
          'python_removed_from_path': True, 'checks': checks}
(HERE / 'cli-exe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'passed': report['passed'], 'checks': len(checks)}))
raise SystemExit(0 if report['passed'] else 2)
