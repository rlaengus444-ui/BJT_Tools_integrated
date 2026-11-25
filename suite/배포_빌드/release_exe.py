"""Package an already verified one-folder build without modifying its code."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import numbers
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


def _valid_preview_pixels(value):
    size,bore,wall=(value.get(key) for key in ('size','bore_rgb','wall_rgb')) if isinstance(value,dict) else (None,None,None)
    return (isinstance(size,(list,tuple)) and len(size)==2
            and all(type(v) is int for v in size) and size[0]>=320 and size[1]>=240
            and all(isinstance(rgb,(list,tuple)) and len(rgb)==3
                    and all(type(v) is int and 0<=v<=255 for v in rgb) for rgb in (bore,wall))
            and min(bore)>240 and max(wall)<245)


def require_packing_check(report):
    checks = [phase.get('report', {}).get('checks', {}).get('packing_inference_and_exports', {})
              for phase in report.get('checks', []) if phase.get('phase') == 'save']
    if (len(checks) != 1 or checks[0].get('passed') is not True
            or checks[0].get('step_native_import') is not True
            or checks[0].get('placed') != 2 or checks[0].get('height_mm') != 17
            or checks[0].get('new_training_updates') != 0
            or checks[0].get('preview_and_exports') is not True
            or not _valid_preview_pixels(checks[0].get('depth_buffer_preview'))):
        raise RuntimeError('Native packing STEP/preview/export verification is required')
    exports = checks[0].get('export_buttons')
    size = exports.get('window_size') if isinstance(exports, dict) else None
    if (not isinstance(exports, dict) or exports.get('passed') is not True
            or exports.get('requested_window_size') != [1120, 780]
            or not isinstance(size, list) or len(size) != 2 or any(type(value) is not int for value in size)
            or not (1000 <= size[0] <= 1140 and 700 <= size[1] <= 800)
            or exports.get('calculate_button_invoked') is not True
            or exports.get('save_buttons_invoked') != 2 or exports.get('object_count') != 2
            or exports.get('unicode_output') is not True):
        raise RuntimeError('Compact-window CHITUBOX export button verification is required')
    union = checks[0].get('step_material_union')
    volume = union.get('volume_mm3') if isinstance(union, dict) else None
    if (not isinstance(union, dict) or union.get('passed') is not True or union.get('source_solids') != 6
            or union.get('material_solids') != 1
            or not isinstance(volume, numbers.Real) or isinstance(volume, bool)
            or not math.isfinite(volume) or abs(volume - 512.) > 1e-6
            or union.get('unicode_path') is not True or union.get('gui_import') is not True):
        raise RuntimeError('Native packing multi-body STEP GUI import verification is required')
    from mesa_runtime import DLL_SHA256
    software=checks[0].get('software_opengl') or {}
    if (software.get('passed') is not True or software.get('dll_sha256')!=DLL_SHA256
            or 'llvmpipe' not in '\n'.join(software.get('capabilities',[])).lower()):
        raise RuntimeError('Native packing software OpenGL loaded-DLL verification is required')


def require_ir_check(report):
    """Require the exact workbook and real frozen offline lifecycle evidence."""
    checks = report.get('checks', {})
    required = ('strict_manifest_resources', 'test_mode_initialization_has_no_native_process',
                'authentic_builtin_workbook_and_worker_assets',
                'production_parser_accepts_pinned_original', 'manual_images_and_previous_layer_features',
                'offline_connect_tab_resize_preserves_session',
                'asynchronous_close_preserves_csv_and_two_plots', 'suite_button_fonts_and_single_title',
                'whole_flow_has_no_native_or_com_attempts', 'callback_errors')
    if (report.get('passed') is not True or report.get('frozen') is not True
            or report.get('excel_com_verified') is not False or report.get('equipment_verified') is not False
            or any(checks.get(name, {}).get('passed') is not True for name in required)):
        raise RuntimeError('Complete frozen offline IR integration evidence is required')
    zero_attempts = {'native_processes': 0, 'adapter_factories': 0, 'adapter_constructors': 0,
                     'excel_controllers': 0, 'save_calls': 0}
    if (report.get('native_operation_attempts') != zero_attempts
            or checks['whole_flow_has_no_native_or_com_attempts'].get('measured_attempts') != zero_attempts):
        raise RuntimeError('Measured zero native/COM/Save attempts are required for the entire offline fixture')
    assets = checks['authentic_builtin_workbook_and_worker_assets']
    workbook_sha256 = 'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20'
    mapping = {'sheet': '수동 테스트', 'temperature_cell': 'B13', 'actual_power_cell': 'C13',
               'recommendation_cell': 'D13', 'target_cell': 'B5', 'layer_cell': 'A13',
               'slice_start_cell': 'BL13'}
    if (assets.get('workbook_sha256') != workbook_sha256 or assets.get('workbook_bytes') != 11523470
            or assets.get('bridge_sha256') != '939c65d211ff9d144e956eb7b2bae26c4cfab80437c2f0a6c60efa70f4c1b215'
            or assets.get('mapping') != mapping):
        raise RuntimeError('Authentic recovery workbook, bridge and cell mapping are required')
    metadata = checks['production_parser_accepts_pinned_original'].get('metadata', {})
    if (metadata.get('sha256') != workbook_sha256 or metadata.get('sheet') != '수동 테스트'
            or metadata.get('first_row') != 13 or checks['production_parser_accepts_pinned_original'].get('background_thread') is not True
            or checks['production_parser_accepts_pinned_original'].get('excel_calculated') is not False):
        raise RuntimeError('Production workbook structural parser evidence is required')
    session = checks['offline_connect_tab_resize_preserves_session']
    archive = checks['asynchronous_close_preserves_csv_and_two_plots']
    if (session.get('observed_layers') != 3 or session.get('save_calls') != 0
            or session.get('real_adapters_created') != 0 or archive.get('rows') != 3
            or archive.get('writer_pending_keeps_suite_alive') is not True
            or archive.get('worker_retired') is not True or len(archive.get('files', [])) != 3):
        raise RuntimeError('Owned offline IR session and loss-free archive shutdown are required')


def require_recoater_check(report):
    phases = {phase.get('phase'): phase.get('report', {}).get('checks', {})
              for phase in report.get('checks', [])}
    checks = phases.get('save', {}).get('recoater_simulation', {})
    restored = phases.get('restore', {}).get('fresh_process_all_14_inputs_restored', {})
    if (any(checks.get(key) is not True for key in
            ('passed', 'chart_seek', 'animation_advanced', 'suite_stop', 'tab_pause'))
            or restored.get('passed') is not True or 'recoater' not in restored.get('tools', [])):
        raise RuntimeError('Frozen recoater controls and input restoration evidence is required')


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
                'fixtures-exe', 'fixtures-gui-exe', 'features-exe', 'oven-playback-exe', 'rust-probe-exe',
                'ir-feedback-exe', 'ir-feedback-cli-exe', 'cli-exe')
    reports = {}
    for name in required:
        report = json.loads((HERE / (name + '.json')).read_text(encoding='utf-8'))
        if not report.get('passed') or (name != 'cli-exe' and not report.get('frozen')):
            raise RuntimeError('Frozen verification did not pass: ' + name)
        reports[name] = report
    expected_tools = len(json.loads((source / 'source_manifest.json').read_text(encoding='utf-8'))['tools'])
    if expected_tools != 14 or reports['smoke-exe'].get('tool_count') != expected_tools:
        raise RuntimeError('Expected all 14 registered tools, including the recoater simulator')
    require_recoater_check(reports['features-exe'])
    require_packing_check(reports['features-exe'])
    for name in ('ir-feedback-exe', 'ir-feedback-cli-exe'):
        require_ir_check(reports[name])
    from mesa_runtime import verify_install
    verify_install(build)
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
        'logs', '검증_결과', '통합_검증_보고서.txt', '치투검사_실제파일_검증.md',
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
14개 도구 중 치투 배치 검사에서 겹침·표면 접촉·최소 간격을 검사합니다.
리코터 시뮬레이터에서 두 롤러 앞 파우더의 단면과 높이 그래프를 확인할 수 있습니다.
IR 피드백 제어에는 이미지 회복제어 Excel 계산시트가 포함됩니다.
이미지 회복제어와 사용자 Excel 수식에는 설치된 Microsoft Excel 데스크톱이 필요합니다.
IR 지원 장비는 M1·S3이며 연결 버튼을 눌러야 시작합니다. M2는 지원하지 않습니다.
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
