"""Clean native-Windows verification/build runner for the private repository.

Build output stays in an explicit scratch directory. Publishing is a separate
opt-in invocation and can update only a draft targeting this exact commit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
REPOSITORY = SOURCE.parent


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def run(command, directory, environment, log):
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(list(map(str, command)), cwd=directory, env=environment,
            stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{log.name} failed with exit {result.returncode}; see retained log')


def current_commit():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPOSITORY,
                                   text=True).strip()


def stage_ascii_assets(raw, output, version):
    """Keep verified archive bytes; give each published asset a unique name."""
    names = {
        'BJT_통합프로그램.pyw': f'BJT_{version}_standalone.pyw',
        f'BJT_통합프로그램_{version}_EXE.zip': f'BJT_{version}_Windows_x64.zip',
        f'BJT_통합프로그램_{version}_전체소스.zip': f'BJT_{version}_source.zip',
        f'BJT_통합프로그램_{version}_사용설명서.zip': f'BJT_{version}_manual.zip',
    }
    if {path.name for path in raw.iterdir()} != set(names):
        raise RuntimeError('Expected exactly the four verified build archives/launcher')
    if output.exists():
        raise FileExistsError('Use a fresh publication asset directory')
    output.mkdir()
    checksums = {}
    for original, renamed in names.items():
        renamed.encode('ascii')
        before = digest(raw / original)
        target = output / renamed
        shutil.copyfile(raw / original, target)
        if digest(target) != before:
            raise RuntimeError('Renaming changed verified bytes: ' + original)
        checksums[renamed] = {'bytes': target.stat().st_size, 'sha256': before}
    (output / 'SHA256SUMS.txt').write_text(''.join(
        f'{checksums[name]["sha256"]}  {name}\n' for name in sorted(checksums)),
        encoding='utf-8', newline='\n')
    return names


def publish_draft(output):
    summary = json.loads((output / 'reports/native-summary.json').read_text(encoding='utf-8'))
    if not summary.get('passed') or summary['commit'] != current_commit():
        raise RuntimeError('Successful verification of this exact commit is required')
    assets = output / 'release-assets'
    if {path.name for path in assets.iterdir()} != set(summary['assets']):
        raise RuntimeError('Unverified files cannot join the publication')
    for name, expected in summary['assets'].items():
        if (not name.isascii() or (assets / name).stat().st_size != expected['bytes']
                or digest(assets / name) != expected['sha256']):
            raise RuntimeError('Verified asset changed: ' + name)
    repository = os.environ.get('GITHUB_REPOSITORY')
    if not repository or not os.environ.get('GH_TOKEN'):
        raise RuntimeError('GitHub Actions repository and job token are required')
    repository_info = json.loads(subprocess.check_output(
        ['gh', 'api', f'repos/{repository}'], text=True, encoding='utf-8'))
    if repository_info.get('private') is not True:
        raise RuntimeError('This release must retain the private repository audience')
    tag = 'v' + summary['version']
    query = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository,
        '--json', 'isDraft,targetCommitish,apiUrl'], capture_output=True, text=True, encoding='utf-8')
    if query.returncode:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repository,
            '--draft', '--target', summary['commit'], '--title', 'BJT 통합프로그램 ' + summary['version'],
            '--notes', 'Windows 64비트용입니다. 전체 ZIP을 압축 해제한 뒤 BJT_통합프로그램.exe를 실행하세요.'],
            check=True)
        query = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository,
            '--json', 'isDraft,targetCommitish,apiUrl'], check=True,
            capture_output=True, text=True, encoding='utf-8')
    release = json.loads(query.stdout)
    if not release.get('isDraft') or release.get('targetCommitish') != summary['commit']:
        raise RuntimeError('Refusing to replace a published release or a draft for another commit')
    api_url = release['apiUrl']
    if not api_url.startswith(f'https://api.github.com/repos/{repository}/releases/'):
        raise RuntimeError('Unexpected release readback endpoint')
    for path in sorted(assets.iterdir()):
        subprocess.run(['gh', 'release', 'upload', tag, '--repo', repository,
                        '--clobber', str(path)], check=True)
    readback = json.loads(subprocess.check_output(['gh', 'api', api_url],
                                                text=True, encoding='utf-8'))
    if (readback.get('draft') is not True or readback.get('tag_name') != tag
            or readback.get('target_commitish') != summary['commit']):
        raise RuntimeError('Release changed during draft publication')
    by_name = {item['name']: item for item in readback.get('assets', [])}
    for name, expected in summary['assets'].items():
        item = by_name.get(name, {})
        if (item.get('state') != 'uploaded' or item.get('size') != expected['bytes']
                or item.get('digest') != 'sha256:' + expected['sha256']):
            raise RuntimeError('GitHub server digest readback failed: ' + name)
    write(output / 'reports/draft-upload.json', {'passed': True, 'tag': tag,
        'commit': summary['commit'], 'draft': True, 'server_digests_verified': True,
        'assets': summary['assets']})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.publish_only:
        publish_draft(output)
        return 0
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('This runner requires native Windows and CPython 3.12')
    import ctypes
    try:
        getattr(ctypes.WinDLL('ntdll'), 'wine_get_version')
    except AttributeError:
        pass
    else:
        raise RuntimeError('Use run_build.py for Wine; this native CI gate requires Windows itself')
    if output.exists():
        raise FileExistsError('Use a fresh output directory')
    reports = output / 'reports'
    reports.mkdir(parents=True)
    build = output / 'build-work'
    build.mkdir()
    for path in HERE.iterdir():
        if path.is_file():
            shutil.copy2(path, build / path.name)
    for name in ('helpers', 'vendor-runtime'):
        shutil.copytree(HERE / name, build / name)
    manifest = json.loads((SOURCE / 'source_manifest.json').read_text(encoding='utf-8'))
    version = manifest['suite_version']
    summary = {'passed': False, 'version': version, 'commit': current_commit(),
               'scope': 'Native GitHub-hosted Windows; synthetic inputs, no printer/oven hardware or private project files',
               'python': sys.version, 'checks': {}}
    environment = os.environ.copy()
    environment.update({'BJT_SOURCE_DIR': str(SOURCE), 'BJT_BUILD_PYTHON': sys.executable,
        'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'BJT_STATE_TEST_REPORT': str(reports / 'all11-input-inventory.json'),
        'NUMBA_CACHE_DIR': str(output / ('n' + version.replace('.', '') + '-src')),
        'MPLCONFIGDIR': str(output / 'matplotlib-src')})
    environment['PATH'] = str(build / 'vendor-runtime') + os.pathsep + environment.get('PATH', '')
    try:
        run([sys.executable, '-m', 'pytest', 'tests', 'chitu_tests', '-p', 'no:cacheprovider',
            '-q', '--junitxml=' + str(reports / 'source-tests.xml')], SOURCE, environment,
            reports / 'source-tests.log')
        xml = ET.parse(reports / 'source-tests.xml')
        skips = [{'test': item.get('name'), 'reason': item.find('skipped').get('message', '')}
                 for item in xml.iter('testcase') if item.find('skipped') is not None]
        if any('Original supplied tool is only present in the integration workspace' not in item['reason'] for item in skips):
            raise RuntimeError('Unexpected native Windows test skip; see source-tests.xml')
        summary['checks']['full_source_suite'] = {'passed': True, 'skips': skips}
        run([sys.executable, build / 'prepare_build.py'], build, environment, reports / 'prepare.log')
        for action in ('build', 'diagnose-exe', 'smoke-exe', 'smoke-gui-exe',
                       'fixtures-exe', 'fixtures-gui-exe', 'features-exe', 'oven-playback-exe'):
            run([sys.executable, build / 'run_build.py', action], build, environment,
                reports / (action + '-runner.log'))
            if action != 'build':
                value = json.loads((build / (action + '.json')).read_text(encoding='utf-8'))
                if not value.get('passed') or not value.get('frozen'):
                    raise RuntimeError('Frozen verification failed: ' + action)
                summary['checks'][action] = {'passed': True}
        run([sys.executable, build / 'verify_cli.py'], build, environment, reports / 'cli-runner.log')
        summary['checks']['cli-exe'] = {'passed': True}
        dist = build / 'dist' / f'BJT_통합프로그램_{version}_EXE'
        parity = []
        for path in sorted((build / 'app-source').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                rel = path.relative_to(build / 'app-source')
                bundled = dist / '_internal' / rel
                if not bundled.is_file() or digest(path) != digest(bundled):
                    raise RuntimeError('Frozen data/source differs: ' + str(rel))
                parity.append(str(rel))
        excluded = {'ucrtbase.dll', 'gdiplus.dll', 'windowscodecs.dll'}
        bad_dlls = [str(path.relative_to(dist)) for path in dist.rglob('*.dll')
                    if path.name.lower() in excluded or path.name.lower().startswith('api-ms-win-crt-')]
        if bad_dlls:
            raise RuntimeError('Operating-system DLLs must not be bundled: ' + repr(bad_dlls))
        for name in ('tcl8.6', 'tk8.6'):
            if not (dist / '_internal/licenses' / name / 'license.terms').is_file():
                raise RuntimeError('Missing Tcl/Tk license: ' + name)
        metadata = {}
        import pefile
        for name in ('BJT_통합프로그램.exe', 'BJT_명령줄.exe'):
            path = dist / name
            pe = pefile.PE(str(path), fast_load=False)
            fixed = pe.VS_FIXEDFILEINFO[0]
            expected = tuple(map(int, version.split('.'))) + (0,)
            actual = (fixed.FileVersionMS >> 16, fixed.FileVersionMS & 65535,
                      fixed.FileVersionLS >> 16, fixed.FileVersionLS & 65535)
            if pe.FILE_HEADER.Machine != 0x8664 or actual != expected:
                raise RuntimeError('Wrong Windows x64 PE version: ' + name)
            metadata[name] = {'sha256': digest(path), 'version': list(actual), 'machine': 'AMD64'}
            pe.close()
        summary['checks']['source_data_and_native_closure'] = {'passed': True,
            'matching_files': len(parity), 'excluded_os_dlls': bad_dlls, 'executables': metadata}
        write(reports / 'frozen-source-parity.json', {'passed': True, 'matching_paths': parity})
        raw_assets = output / 'raw-release-assets'
        raw_assets.mkdir()
        run([sys.executable, build / 'release_exe.py', '--source', SOURCE,
            '--output-root', output / 'packaged-exe'], build, environment, reports / 'package-exe.log')
        packaged = json.loads((build / 'release_result.json').read_text(encoding='utf-8'))
        shutil.copy2(packaged['zip'], raw_assets / Path(packaged['zip']).name)
        release_date = (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()
        run([sys.executable, REPOSITORY / 'release_tools/build_source_release.py', '--app', SOURCE,
            '--output', raw_assets, '--release-date', release_date,
            '--launcher-template', REPOSITORY / 'versions/suite-v1.6.2/BJT_통합프로그램.pyw',
            '--launcher-output', raw_assets / 'BJT_통합프로그램.pyw'], REPOSITORY, environment,
            reports / 'package-source.log')
        shutil.move(raw_assets / 'source-release-verification.json', reports / 'source-release-verification.json')
        assets = output / 'release-assets'
        summary['asset_name_mapping'] = stage_ascii_assets(raw_assets, assets, version)
        summary['assets'] = {path.name: {'bytes': path.stat().st_size, 'sha256': digest(path)}
                             for path in sorted(assets.iterdir()) if path.is_file()}
        summary['passed'] = True
    except Exception:
        summary['traceback'] = traceback.format_exc()
        raise
    finally:
        for path in build.glob('*'):
            if path.is_file() and path.suffix in ('.json', '.log'):
                shutil.copy2(path, reports / path.name)
        write(reports / 'native-summary.json', summary)
    print(json.dumps({'passed': True, 'version': version, 'assets': summary['assets']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
