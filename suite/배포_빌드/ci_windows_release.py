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

from release_native import TARGET, TOOLCHAIN, crate_digest, verify_chain, verify_native, require_probe
from mesa_runtime import prepare_runtime, install_runtime

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
REPOSITORY = SOURCE.parent


# Native Rust is mandatory in release mode. Retain the prototype allowance only
# for explicitly non-release callers; CI always passes rust_required=True.
_ALLOWED_SKIPS = {
    ('test_number_tools.NumberToolTests', 'test_original_valid_conflict_policies_match_file_bytes_and_metadata'): 'Original supplied tool is only present in the integration workspace',
    ('test_bjt_build_publish.AtomicPublishTests', 'test_open_old_file_survives_replacement'): 'open-file replacement semantics vary by platform',
    ('test_bjt_rust_backend.NativeDiscoveryTests', 'test_real_library_metadata_if_built'): 'optional host native library not built',
    **{('test_bjt_rust_kernel.RustEdgeKernelTests', name): 'optional Rust library is absent' for name in (
        'test_random_groups_keys_and_all_rows_preserved', 'test_contact_masks_signs_duplicates_and_balances',
        'test_invalid_metadata_fails_before_mutation', 'test_ffi_rejects_invalid_ranges_without_dereferencing',
        'test_complete_verifier_topologies_and_disk_paths')},
    **{('test_bjt_rust_pack.RustGridPackingTests', name): 'optional Rust packing feature is absent' for name in (
        'test_random_extreme_scaling_and_signed_zero_bytes', 'test_chunk_boundaries_scalar_buffers_and_readonly_input',
        'test_invalid_transform_and_capacity_do_not_mutate_output', 'test_limits_and_cancellation_keep_atomic_writer_contract')},
}


def allowed_source_skip(item, *, rust_required=True):
    classname = item.get('classname', '')
    reason = item.find('skipped').get('message', '')
    prefix = 'unittest.case.SkipTest: '
    if reason.startswith(prefix):
        reason = reason[len(prefix):]
    if rust_required and any(part.startswith('test_bjt_rust_') for part in classname.split('.')):
        return False
    return any((classname == cls or classname.endswith('.' + cls)) and item.get('name') == name and reason == expected
               for (cls, name), expected in _ALLOWED_SKIPS.items())


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


def build_native(output, reports, environment):
    """Compile/test before any source tests import the optional native wrapper."""
    target_directory = output / 'cargo-target'
    if target_directory.is_relative_to(SOURCE):
        raise RuntimeError('Cargo build output must stay outside source archives')
    environment.update({'CARGO_TARGET_DIR': str(target_directory),
                        'RUSTUP_TOOLCHAIN': TOOLCHAIN,
                        'RUSTFLAGS': '-C panic=unwind -C target-cpu=generic'})
    environment.pop('CARGO_ENCODED_RUSTFLAGS', None)
    run(['cargo', 'test', '--offline', '--locked', '--manifest-path',
         SOURCE / 'native/bjt_mesh_native/Cargo.toml', '--target', TARGET],
        SOURCE, environment, reports / 'rust-cargo-test.log')
    if 'test result: ok. 5 passed; 0 failed; 0 ignored;' not in (reports / 'rust-cargo-test.log').read_text(encoding='utf-8'):
        raise RuntimeError('Expected all five native Rust unit tests without skips')
    run([sys.executable, SOURCE / 'native/build_bjt_mesh_native.py', '--target', TARGET],
        SOURCE, environment, reports / 'rust-native-build.log')
    proof = verify_native(SOURCE / 'native',
                          expected_source=crate_digest(SOURCE / 'native/bjt_mesh_native'))
    sysroot = Path(subprocess.check_output(['rustc', '--print', 'sysroot'],
                  env=environment, text=True).strip())
    copyright_file = sysroot / 'share/doc/rust/COPYRIGHT-library.html'
    if not copyright_file.is_file():
        raise RuntimeError('Rust standard-library redistribution notices are required')
    licenses = SOURCE / 'native/licenses'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(copyright_file, licenses / copyright_file.name)
    for name in ('LICENSE-APACHE', 'LICENSE-MIT'):
        notice = sysroot / 'share/doc/rust' / name
        if notice.is_file():
            shutil.copy2(notice, licenses / name)
    preflight = reports / 'rust-source-preflight.json'
    run([sys.executable, '-c',
         'import json, pathlib, bjt_mesh_native as m; '
         's=m.backend_status(); '
         'assert s["available"] and s["can_pack_grid"], s; '
         'assert m.get_backend("rust").path == pathlib.Path(s["path"]).resolve(); '
         'pathlib.Path(__import__("sys").argv[1]).write_text(json.dumps(s), encoding="utf-8")',
         preflight], SOURCE, environment, reports / 'rust-source-preflight.log')
    status = json.loads(preflight.read_text(encoding='utf-8'))
    if Path(status['path']).resolve() != (SOURCE / 'native/bjt_mesh_native.dll').resolve():
        raise RuntimeError('Source preflight loaded a side-path native DLL')
    return {'passed': True, 'tests': 5, 'native': proof, 'preflight': status}


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
    if output == SOURCE or output.is_relative_to(SOURCE):
        raise RuntimeError('Release scratch output must stay outside the source tree')
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
        'BJT_RELEASE_COMMIT': summary['commit'],
        'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'BJT_STATE_TEST_REPORT': str(reports / 'all11-input-inventory.json'),
        'NUMBA_CACHE_DIR': str(output / ('n' + version.replace('.', '') + '-src')),
        'MPLCONFIGDIR': str(output / 'matplotlib-src')})
    # Missing-library unit cases deliberately test auto fallback. Do not force
    # Rust globally across those tests; enforce availability through preflight,
    # forbid Rust-absence skips, then run a separate forced-Rust frozen proof.
    environment.pop('BJT_MESH_BACKEND', None)
    environment['PATH'] = str(build / 'vendor-runtime') + os.pathsep + environment.get('PATH', '')
    try:
        graphics=prepare_runtime(output/'mesa-runtime')
        summary['checks']['software_opengl_setup']=graphics
        run([sys.executable,HERE/'mesa_runtime.py','--probe',reports/'source-opengl.json'],
            SOURCE,environment,reports/'source-opengl.log')
        summary['checks']['software_opengl_source']=json.loads((reports/'source-opengl.json').read_text(encoding='utf-8'))
        summary['checks']['native_rust_build'] = build_native(output, reports, environment)
        run([sys.executable, '-m', 'pytest', 'tests', 'chitu_tests', '-p', 'no:cacheprovider',
            '-q', '--junitxml=' + str(reports / 'source-tests.xml')], SOURCE, environment,
            reports / 'source-tests.log')
        xml = ET.parse(reports / 'source-tests.xml')
        skips = [{'test': item.get('name'), 'reason': item.find('skipped').get('message', '')}
                 for item in xml.iter('testcase') if item.find('skipped') is not None]
        if any(not allowed_source_skip(item) for item in xml.iter('testcase') if item.find('skipped') is not None):
            raise RuntimeError('Unexpected native Windows test skip; see source-tests.xml')
        summary['checks']['full_source_suite'] = {'passed': True, 'skips': skips}
        run([sys.executable, build / 'prepare_build.py'], build, environment, reports / 'prepare.log')
        write(build / 'build-provenance.json', {'commit': summary['commit'],
            'source_manifest_sha256': digest(SOURCE / 'source_manifest.json'),
            'native': summary['checks']['native_rust_build']['native']})
        for action in ('build', 'diagnose-exe', 'smoke-exe', 'smoke-gui-exe',
                       'fixtures-exe', 'fixtures-gui-exe', 'features-exe', 'oven-playback-exe', 'rust-probe-exe'):
            run([sys.executable, build / 'run_build.py', action], build, environment,
                reports / (action + '-runner.log'))
            if action=='build':
                dist=build/'dist'/f'BJT_통합프로그램_{version}_EXE'
                summary['checks']['software_opengl_packaged']=install_runtime(graphics['runtime_directory'],dist)
            if action != 'build':
                value = json.loads((build / (action + '.json')).read_text(encoding='utf-8'))
                if not value.get('passed') or not value.get('frozen'):
                    raise RuntimeError('Frozen verification failed: ' + action)
                summary['checks'][action] = {'passed': True}
        run([sys.executable, build / 'verify_cli.py'], build, environment, reports / 'cli-runner.log')
        summary['checks']['cli-exe'] = {'passed': True}
        dist = build / 'dist' / f'BJT_통합프로그램_{version}_EXE'
        chain = verify_chain(SOURCE, build / 'app-source', dist / '_internal')
        probe = json.loads((build / 'rust-probe-exe.json').read_text(encoding='utf-8'))
        require_probe(probe, chain['source'], commit=summary['commit'],
            manifest_sha256=digest(SOURCE / 'source_manifest.json'),
            executable_sha256=digest(dist / 'BJT_명령줄.exe'))
        summary['checks']['native_rust_provenance'] = {'passed': True, 'chain': chain,
            'native_calls': probe['native_calls'], 'fixture_count': probe['fixture_count']}
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
