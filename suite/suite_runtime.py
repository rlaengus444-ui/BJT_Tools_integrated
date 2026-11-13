"""Reuse the original BJT's exact private Python environment."""
from __future__ import annotations

import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

BASE_DIR = Path(__file__).resolve().parent
# Pins apply only to absent packages. Existing working versions are preserved.
EXTRA_REQUIREMENTS = ('matplotlib==3.11.2', 'pypng==0.20220715.0',
                      'tifffile==2026.10.15', 'imagecodecs==2026.10.10',
                      'manifold3d==3.5.4', 'python-fcl==0.7.0.11',
                      'plotly==7.1.0', 'narwhals==2.27.1', 'cadquery-ocp==7.8.1.1.post1')
EXTRA_MODULES = ('matplotlib', 'png', 'tifffile', 'imagecodecs',
                 'manifold3d', 'fcl', 'plotly', 'narwhals', 'OCP')


def original_bootstrap():
    manifest = json.loads((BASE_DIR / 'source_manifest.json').read_text(encoding='utf-8'))
    tool = next(item for item in manifest['tools'] if item['id'] == 'bjt')
    path = BASE_DIR / tool['source']
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != tool['sha256']:
        raise RuntimeError('BJT 원본 모듈의 무결성 검사가 실패했습니다.')
    tree = ast.parse(data, filename=str(path))
    # The standard-library bootstrap precedes the first __main__ guard.
    prefix = []
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == '__name__':
            break
        prefix.append(node)
    namespace = {'__file__': str(path), '__name__': '_suite_original_bootstrap'}
    exec(compile(ast.Module(body=prefix, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def runtime_candidates():
    ns = original_bootstrap()
    requirements, modules = ns['_BJT_BOOT_REQUIREMENTS'], ns['_BJT_BOOT_MODULES']
    signature = hashlib.sha256(repr((requirements, modules)).encode()).hexdigest()[:16]
    parent = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.cache')) / 'BJT' / 'python-envs'
    environment = parent / ('cp312-' + signature)
    candidates = [environment / 'python.exe', environment / 'Scripts' / 'python.exe', environment / 'bin' / 'python', Path(sys.executable)]
    return ns, candidates


def process_environment(executable):
    return original_bootstrap()['_bjt_boot_process_env'](str(executable))


def probe(executable, ns, *, include_extra=True):
    if not Path(executable).is_file():
        return False
    program = ("import sys,struct,importlib,importlib.metadata as m; "
               "assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8; "
               f"reqs={ns['_BJT_BOOT_REQUIREMENTS']!r}; "
               "assert all(m.version(r.split('==')[0])==r.split('==')[1] for r in reqs); "
               f"[importlib.import_module(n) for n in {(ns['_BJT_BOOT_MODULES'] + (EXTRA_MODULES if include_extra else ()))!r}]")
    try:
        result = subprocess.run([str(executable), '-c', program], env=ns['_bjt_boot_process_env'](str(executable)),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_runtime(*, include_extra=False):
    ns, candidates = runtime_candidates()
    seen = set()
    for path in candidates:
        normalized = os.path.normcase(str(path.resolve()))
        if normalized not in seen:
            seen.add(normalized)
            if probe(path, ns, include_extra=include_extra):
                return path
    return None


def _extra_status(executable, ns):
    program = '\n'.join([
        'import importlib, importlib.metadata as m, json',
        f'items={tuple(zip(EXTRA_REQUIREMENTS, EXTRA_MODULES))!r}',
        'rows=[]',
        'for requirement,module_name in items:',
        '    version=None',
        '    try: version=m.version(requirement.split("==")[0])',
        '    except m.PackageNotFoundError: pass',
        '    error=None',
        '    try:',
        '        module=importlib.import_module(module_name)',
        '        if module_name=="imagecodecs" and not module.PNG.available: raise ImportError("PNG decoder unavailable")',
        '    except Exception as exc: error=type(exc).__name__+": "+str(exc)',
        '    rows.append({"requirement":requirement,"module":module_name,"version":version,"error":error})',
        'print(json.dumps(rows,ensure_ascii=True))',
    ])
    result = subprocess.run([str(executable), '-c', program], env=ns['_bjt_boot_process_env'](str(executable)),
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('추가 패키지 확인 실패: ' + result.stderr[-1500:])
    return json.loads(result.stdout)


def _ensure_extras(executable, ns):
    status = _extra_status(executable, ns)
    broken = [row for row in status if row['version'] is not None and row['error']]
    if broken:
        detail = '; '.join(f"{row['module']} {row['version']}: {row['error']}" for row in broken)
        raise RuntimeError('기존 추가 패키지를 불러오지 못했습니다. 설치된 버전은 변경하지 않았습니다. ' + detail)
    missing = [row['requirement'] for row in status if row['version'] is None and row['error']]
    if not missing:
        return status
    _, candidates = runtime_candidates()
    private = candidates[0].parent
    actual = Path(executable).parent
    if actual.name in {'Scripts', 'bin'}:
        actual = actual.parent
    if os.path.normcase(str(actual.resolve())) != os.path.normcase(str(private.resolve())):
        raise RuntimeError('추가 패키지는 원본 BJT 전용 환경에만 설치할 수 있습니다.')
    log_dir = BASE_DIR / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / 'runtime_extras_install.log'
    try:
        with log_path.open('a', encoding='utf-8') as log:
            log.write('\n' + datetime.datetime.now().isoformat(timespec='seconds') + '\nRuntime: ' + str(executable) + '\nMissing only: ' + ', '.join(missing) + '\n')
            log.flush()
            result = subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check',
                                     '--no-input', '--no-deps', '--only-binary=:all:', *missing],
                                    env=ns['_bjt_boot_process_env'](str(executable)), stdout=log, stderr=subprocess.STDOUT,
                                    timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('pip 종료 코드 ' + str(result.returncode))
        after = _extra_status(executable, ns)
        if any(row['error'] for row in after) or not probe(executable, ns, include_extra=False):
            raise RuntimeError('추가 패키지 또는 원본 고정 버전 확인 실패')
        (log_dir / 'runtime_extras.json').write_text(json.dumps({
            'runtime': str(executable), 'installed_missing': missing, 'packages': after,
            'original_pins': list(ns['_BJT_BOOT_REQUIREMENTS']),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        return after
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        raise RuntimeError(f'비교 추가 패키지 준비에 실패했습니다. 기존 패키지 버전은 변경하지 않았습니다. 로그: {log_path}\n{exc}') from exc


def _extras_ticket(prefix):
    signature = hashlib.sha256(repr((EXTRA_REQUIREMENTS, EXTRA_MODULES)).encode()).hexdigest()[:16]
    return signature + '|' + os.path.normcase(str(Path(prefix).resolve()))


def ensure_runtime():
    ready = os.environ.pop('BJT_SUITE_RUNTIME_READY', '')
    extras_ready = os.environ.pop('BJT_SUITE_EXTRAS_READY', '')
    if ready == os.path.normcase(str(Path(sys.prefix).resolve())) and extras_ready == _extras_ticket(sys.prefix):
        return
    # A prior suite's readiness marker cannot skip newly required modules.
    runtime = find_runtime(include_extra=False)
    if runtime is None:
        # Reuse the original automatic, isolated setup without modifying its
        # bootstrap. --diagnose never starts a model conversion or a Tk window.
        ns = original_bootstrap()
        target = Path(ns['__file__'])
        log_dir = BASE_DIR / 'logs'
        log_dir.mkdir(exist_ok=True)
        diagnostic = log_dir / 'runtime_diagnostic.json'
        result = subprocess.run([sys.executable, str(target), '--diagnose', str(diagnostic)],
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('원본 BJT 전용 실행 환경 준비에 실패했습니다. logs 폴더를 확인하세요.')
        runtime = find_runtime(include_extra=False)
        if runtime is None:
            raise RuntimeError('BJT 원본 고정 버전 환경을 찾지 못했습니다. logs 폴더를 확인하세요.')
    _ensure_extras(runtime, original_bootstrap())
    if os.path.normcase(str(runtime.resolve())) == os.path.normcase(str(Path(sys.executable).resolve())):
        return
    env = process_environment(runtime)
    # sys.prefix is the environment directory, for both Conda and venv layouts.
    prefix = runtime.parent.parent if runtime.parent.name in {'Scripts', 'bin'} else runtime.parent
    env['BJT_SUITE_RUNTIME_READY'] = os.path.normcase(str(prefix.resolve()))
    env['BJT_SUITE_EXTRAS_READY'] = _extras_ticket(prefix)
    windowless = sys.stdout is None and sys.stderr is None
    entry = BASE_DIR / ('BJT_Tools_통합.pyw' if windowless else 'run_suite.py')
    if not entry.is_file():
        entry = BASE_DIR / 'run_suite.py'
    child = subprocess.Popen([str(runtime), str(entry), *sys.argv[1:]], env=env,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if entry.suffix == '.pyw' else 0)
    raise SystemExit(child.wait())
