"""Prepare bounded Drive upload ZIPs and a Windows-native restore command."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


CHUNK_BYTES = 99_000_000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='1.6')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    version = args.version
    folder = f'BJT_통합프로그램_{version}_EXE'
    base_name = f'BJT_{version}_EXE'
    expected_exe = folder + '/BJT_통합프로그램.exe'
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        if expected_exe not in names or not any(name.startswith(folder + '/_internal/') for name in names):
            raise ValueError('The input must be the complete BJT Windows distribution ZIP for this version.')
        if archive.testzip() is not None:
            raise ValueError('The distribution ZIP is corrupt.')
    archive_hash = hashlib.file_digest(args.archive.open('rb'), 'sha256').hexdigest()
    count = (args.archive.stat().st_size + CHUNK_BYTES - 1) // CHUNK_BYTES
    part_names = [f'{base_name}.zip.{index:03d}' for index in range(1, count + 1)]
    targets = [args.output / f'{folder}_100MB_{index:02d}.zip' for index in range(1, count + 1)]
    if any(target.resolve() == args.archive.resolve() for target in targets):
        raise ValueError('An output part would overwrite the input ZIP.')
    quoted = '+'.join('"' + name + '"' for name in part_names)
    if len(quoted) > 7800:
        raise ValueError('Too many parts for the Windows CMD command length limit.')
    checks = '\r\n'.join(f'if not exist "{name}" (echo Missing: {name}& pause& exit /b 2)' for name in part_names)
    command = f'''@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto failed
{checks}
echo Verifying and restoring BJT {version}...
copy /b /y {quoted} "{base_name}.zip.tmp" >nul
if errorlevel 1 goto failed
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if ((Get-FileHash -LiteralPath '{base_name}.zip.tmp' -Algorithm SHA256).Hash -ne '{archive_hash}') {{ exit 2 }}"
if errorlevel 1 goto failed
move /y "{base_name}.zip.tmp" "{base_name}.zip" >nul
if errorlevel 1 goto failed
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '{base_name}.zip' -DestinationPath '.' -Force"
if errorlevel 1 goto failed
if not exist ".\\{folder}\\BJT_통합프로그램.exe" goto failed
if not exist ".\\{folder}\\_internal" goto failed
start "" ".\\{folder}\\BJT_통합프로그램.exe"
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo Restore failed. Check that all parts were downloaded and extracted into this folder.
popd
pause
exit /b 2
'''.replace('\r\n', '\n').replace('\n', '\r\n')
    readme = f'''BJT 통합프로그램 {version} EXE 분할 배포

이 배포본은 {count}개 ZIP을 모두 받은 뒤 복원 명령으로 실행합니다.
1. {folder}_100MB_01.zip ~ _{count:02d}.zip을 모두 다운로드합니다.
2. 모든 ZIP을 같은 폴더에 압축 해제합니다. 안의 .001, .002 파일 이름은 바꾸지 않습니다.
3. 첫 ZIP에 들어 있는 BJT_{version}_복원후실행.cmd를 실행합니다.
4. SHA256 확인 후 전체 EXE ZIP을 복원하고 압축을 풀어 프로그램을 실행합니다.
5. 이후에는 {folder} 폴더의 BJT_통합프로그램.exe를 실행합니다.

Python, Excel, .NET 설치는 필요 없습니다. Windows 10/11의 기본 PowerShell을 복원에 사용합니다.
EXE와 _internal 폴더를 함께 유지하세요. 원본 치투 프로젝트는 배포본에 포함하지 않습니다.
전체 ZIP SHA256: {archive_hash}
'''
    records = []
    reconstructed = hashlib.sha256()
    with args.archive.open('rb') as original:
        for index, name in enumerate(part_names, 1):
            chunk = original.read(CHUNK_BYTES)
            reconstructed.update(chunk)
            target = targets[index - 1]
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as package:
                package.writestr(name, chunk)
                if index == 1:
                    package.writestr(f'BJT_{version}_복원후실행.cmd', command.encode('utf-8'))
                    package.writestr('먼저_읽어주세요.txt', readme.encode('utf-8-sig'))
            assert target.stat().st_size < 100 * 1024 * 1024
            with zipfile.ZipFile(target) as package:
                assert package.testzip() is None
                assert package.read(name) == chunk
            records.append({'name': target.name, 'bytes': target.stat().st_size,
                            'sha256': hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()})
    assert reconstructed.hexdigest() == archive_hash
    record = {'archive': args.archive.name, 'archive_bytes': args.archive.stat().st_size,
              'archive_sha256': archive_hash, 'part_count': count,
              'restored_bytes_match': True, 'parts': records}
    (args.output / '분할배포_검증.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
