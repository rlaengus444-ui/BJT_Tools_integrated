"""Package an already verified one-folder build without modifying its code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

from release_native import verify_chain, require_probe

HERE = Path(__file__).resolve().parent


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=Path(os.environ.get('BJT_SOURCE_DIR', str(HERE.parent))))
    parser.add_argument('--output-root', type=Path, default=HERE / 'releases')
    args = parser.parse_args()
    source = args.source.resolve()
    version = json.loads((source / 'source_manifest.json').read_text(encoding='utf-8'))['suite_version']
    NAME = f'BJT_통합프로그램_{version}_EXE'
    build = HERE / 'dist' / NAME
    output_root = args.output_root.resolve()
    output = output_root / NAME
    archive_path = output_root / (NAME + '.zip')
    checksums_path = output_root / (NAME + '_SHA256SUMS.txt')
    required = ('diagnose-exe', 'smoke-exe', 'smoke-gui-exe',
                'fixtures-exe', 'fixtures-gui-exe', 'features-exe', 'oven-playback-exe', 'rust-probe-exe', 'cli-exe')
    reports = {}
    for name in required:
        report = json.loads((HERE / (name + '.json')).read_text(encoding='utf-8'))
        if not report.get('passed') or (name != 'cli-exe' and not report.get('frozen')):
            raise RuntimeError('Frozen verification did not pass: ' + name)
        reports[name] = report
    if reports['smoke-exe'].get('tool_count') != 11:
        raise RuntimeError('Expected all 11 registered tools')
    latest_exe = max((build / name).stat().st_mtime for name in
                     ('BJT_통합프로그램.exe', 'BJT_명령줄.exe'))
    additional = ('chitu-cli-exe', 'chitu-gui-exe', 'camera-cli-exe', 'camera-gui-exe')
    for name in required + additional:
        path = HERE / (name + '.json')
        if not path.is_file():
            continue
        if path.stat().st_mtime < latest_exe:
            raise RuntimeError('Verification predates the current EXE: ' + name)
        report = json.loads(path.read_text(encoding='utf-8'))
        if not report.get('passed'):
            raise RuntimeError('Additional verification failed: ' + name)
    manifest = json.loads((build / '_internal/source_manifest.json').read_text(encoding='utf-8'))
    production_manifest = json.loads((source / 'source_manifest.json').read_text(encoding='utf-8'))
    if manifest != production_manifest:
        raise RuntimeError('Frozen manifest differs from the supplied production source')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source.parent,
                                     text=True).strip()
    provenance = json.loads((HERE / 'build-provenance.json').read_text(encoding='utf-8'))
    chain = verify_chain(source, HERE / 'app-source', build / '_internal')
    if (provenance.get('commit') != commit or provenance.get('native') != chain['source']
            or provenance.get('source_manifest_sha256') != digest(source / 'source_manifest.json')):
        raise RuntimeError('Current commit, source manifest and source DLL build provenance are required')
    require_probe(reports['rust-probe-exe'], chain['source'], commit=commit,
        manifest_sha256=digest(source / 'source_manifest.json'),
        executable_sha256=digest(build / 'BJT_명령줄.exe'))
    rust_notice = source / 'native/licenses/COPYRIGHT-library.html'
    for root in (HERE / 'app-source', build / '_internal'):
        bundled_notice = root / 'licenses/BJT_Rust/COPYRIGHT-library.html'
        if not bundled_notice.is_file() or digest(bundled_notice) != digest(rust_notice):
            raise RuntimeError('Bundled Rust standard-library redistribution notices are required')
    core_hashes = []
    for item in [*(dict(path=tool['source'], sha256=tool['sha256']) for tool in manifest['tools']),
                 *manifest.get('dependencies', [])]:
        actual = digest(build / '_internal' / item['path'])
        if actual != item['sha256']:
            raise RuntimeError('Bundled core changed: ' + item['path'])
        core_hashes.append({'path': item['path'], 'expected_sha256': item['sha256'],
                            'actual_sha256': actual, 'valid': True})
    if output.exists() or archive_path.exists() or checksums_path.exists():
        raise FileExistsError('Release folder, ZIP or checksums already exist; choose another output root')
    output_root.mkdir(parents=True, exist_ok=True)
    cache_ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.nbi', '*.nbc')
    qa_report_names = {
        '검증_결과', '통합_검증_보고서.txt', '치투검사_실제파일_검증.md',
        '1.6_최종_배포_검증.txt', '1.6.1_최종_배포_검증.txt', '1.6.2_최종_배포_검증.txt',
        f'{version}_최종_배포_검증.txt',
    }
    product_roots = {build.resolve(), (build / '_internal').resolve()}
    def product_ignore(directory, names):
        ignored = set(cache_ignore(directory, names))
        if Path(directory).resolve() in product_roots:
            ignored.update(set(names).intersection(qa_report_names))
        return ignored
    # Verification remains a release gate above, but its reports are not runtime assets.
    # Filter reused build trees as well as fresh stages; never remove vendor data/fixtures.
    shutil.copytree(build, output, ignore=product_ignore)
    (output / '사용_안내.txt').write_text(f'''BJT 통합프로그램 {version} · Windows 10/11 64비트

ZIP을 모두 풀고 BJT_통합프로그램.exe를 실행하세요.
Python과 .NET 설치가 필요하지 않습니다. _internal 폴더를 함께 보관하세요.
11개 도구 중 치투 배치 검사에서 겹침·표면 접촉·최소 간격을 검사합니다.
기본 간격은 0mm이며 표면 접촉은 항상 FAIL입니다. 입력 파일은 변경하지 않습니다.
검사된 원본 형상을 그대로 유지하는 연결면 정리 결과는 화면·JSON·HTML에 표시합니다.
명령줄은 BJT_명령줄.exe --tool chitu_checker 작업.chitubox --min-gap-mm 1 --json 결과.json --html 결과.html
도구별 사용설명서는 프로그램 안에서 열 수 있습니다.
라이선스는 _internal/licenses에 있습니다.
입력값은 종료 시 저장되며 다음 실행에서 복원됩니다.
설정: LocalAppData/BJT_Tools/settings.json
로그: LocalAppData/BJT_통합프로그램/{version}
계산 캐시: LocalAppData/BJT/n{version.replace('.', '')}_<빌드해시>
''', encoding='utf-8-sig')
    entries = [(p.relative_to(output).as_posix(), p.stat().st_size, digest(p))
               for p in sorted(output.rglob('*')) if p.is_file()]
    # Checksums are release-management evidence, outside the executable folder.
    checksums_path.write_text(
        ''.join(f'{sha}  {name}\n' for name, size, sha in entries), encoding='utf-8')
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                archive.write(path, NAME + '/' + path.relative_to(output).as_posix())
    result = {'folder': str(output), 'zip': str(archive_path), 'files': len(entries),
              'folder_bytes': sum(size for _, size, sha in entries),
              'verification_checksums': str(checksums_path),
              'zip_bytes': archive_path.stat().st_size, 'zip_sha256': digest(archive_path)}
    (HERE / 'release_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
