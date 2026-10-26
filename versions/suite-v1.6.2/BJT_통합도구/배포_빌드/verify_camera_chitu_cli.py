"""Verify the packaged 49-object camera fixture without altering its input."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    work = args.output.parent / 'camera-cli-cases'
    work.mkdir(exist_ok=True)
    exe = args.folder.resolve() / 'BJT_명령줄.exe'
    project = args.project.resolve()
    input_hash, exe_hash = digest(project), digest(exe)
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                 'TCL_LIBRARY', 'TK_LIBRARY', 'NUMBA_CACHE_DIR', 'MPLCONFIGDIR'):
        environment.pop(name, None)
    windows = Path(environment.get('SystemRoot', 'C:/Windows'))
    environment['PATH'] = os.pathsep.join(str(p) for p in
        (windows / 'System32', windows, windows / 'System32/Wbem'))
    checks, cases = {}, []
    report = {'passed': False, 'checks': checks, 'cases': cases,
              'executable': str(exe), 'executable_sha256': exe_hash,
              'project': str(project), 'project_sha256': input_hash,
              'python_removed_from_child_path': True}
    expected_by_gap = {0: [], 1: [], 1.5: [(5, 6)],
                       2: [(1, 4), (2, 4), (5, 6), (5, 7)]}
    started = time.monotonic()
    try:
        checks['genuine_camera_project'] = input_hash == 'c22dc0cfc58b42a0f6528e512ae9b1947222c6bbfc8fad67747b807cad2270ad'
        for gap, expected_pairs in expected_by_gap.items():
            name = f'gap-{gap:g}'
            result_path, html_path = work / (name + '.json'), work / (name + '.html')
            command = [str(exe), '--tool', 'chitu_checker', str(project),
                       '--min-gap-mm', str(gap), '--json', str(result_path)]
            if gap == 2:
                command += ['--html', str(html_path)]
            sample_started = time.monotonic()
            child = subprocess.run(command, env=environment, capture_output=True,
                                   encoding='utf-8', errors='replace', timeout=180)
            elapsed = time.monotonic() - sample_started
            (work / (name + '.stdout.txt')).write_text(child.stdout, encoding='utf-8')
            (work / (name + '.stderr.txt')).write_text(child.stderr, encoding='utf-8')
            result = json.loads(result_path.read_text(encoding='utf-8'))
            pairs = sorted((p['index_a'], p['index_b']) for p in result['violations'])
            normalized = [o for o in result['objects'] if o.get('normalization')]
            distances = [p['distance_mm'] for p in result['pairs'] if p.get('distance_mm') is not None]
            proof_ok = [o['index'] for o in normalized] == [4, 5] and all(
                o['normalization']['method'] == 'exact-collinear-seam-retriangulation'
                and o['normalization']['zero_area_seams_removed'] == 2
                and all(o['normalization'][key] is True for key in
                    ('coordinates_unchanged', 'exact_surface_union_preserved',
                     'exact_oriented_surface_chain_preserved')) for o in normalized)
            expected_counts = {'clear': 1176 - len(expected_pairs)}
            if expected_pairs:
                expected_counts['too_close'] = len(expected_pairs)
            passed = (child.returncode == bool(expected_pairs)
                      and result['status'] == ('violation' if expected_pairs else 'clear')
                      and result['complete'] is True and result['processing_completed'] is True
                      and result['object_count'] == 49 and len(result['pairs']) == 1176
                      and dict(Counter(p['status'] for p in result['pairs'])) == expected_counts
                      and all(o['valid'] for o in result['objects']) and pairs == expected_pairs
                      and proof_ok and min(distances) == 1.4999923706054688
                      and '연결면 정리: 2개' in child.stdout
                      and not child.stderr.strip())
            checks[f'{name}_decisions_normalization_and_notice'] = passed
            cases.append({'gap_mm': gap, 'passed': passed, 'exit_code': child.returncode,
                          'seconds': elapsed, 'status': result['status'], 'complete': result['complete'],
                          'processing_completed': result['processing_completed'],
                          'object_count': result['object_count'], 'pair_count': len(result['pairs']),
                          'pair_status_counts': dict(Counter(p['status'] for p in result['pairs'])),
                          'violating_pairs': pairs, 'normalized_indices': [o['index'] for o in normalized],
                          'minimum_distance_mm': min(distances), 'result_file': str(result_path)})
            if gap == 2:
                page = html_path.read_text(encoding='utf-8')
                match = re.search(r'<script id="viewer-data" type="application/json">(.*?)</script>', page, re.S)
                payload = json.loads(match.group(1)) if match else {}
                checks['standalone_html_preserves_notices_and_failed_pairs'] = (
                    'Plotly.newPlot' in page and '<script src=' not in page
                    and '원본 형상을 유지한 검사용 연결면 정리 완료' in page
                    and [o['index'] for o in payload.get('report', {}).get('objects', [])
                         if o.get('normalization')] == [4, 5]
                    and sorted((p['index_a'], p['index_b']) for p in
                               payload.get('report', {}).get('violations', [])) == expected_pairs)
                report['html_file'], report['html_bytes'] = str(html_path), html_path.stat().st_size
    except Exception:
        report['traceback'] = traceback.format_exc()
    finally:
        checks['source_file_unchanged'] = digest(project) == input_hash
        checks['executable_unchanged'] = digest(exe) == exe_hash
        report['seconds'] = time.monotonic() - started
        report['passed'] = len(cases) == 4 and all(checks.values()) and 'traceback' not in report
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(checks)}, ensure_ascii=False))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
