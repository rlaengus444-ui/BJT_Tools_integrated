"""Transfer the exact 1.10.0 CI candidate to an existing, invisible draft.

This does not approve the candidate artifacts: they still require independent
artifact review after download. Source reviews are separate evidence. There is
no rebuild, overwrite, tag creation, publication or repository visibility write.
Unknown immutable facts in stage_verified_ir_110.json fail closed before writes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PINS = Path(__file__).with_suffix('.json')
REPOSITORY = 'rlaengus444-ui/BJT_Tools_integrated'
REPOSITORY_ID = 1433912494
VERSION = '1.10.0'
COMMIT = '0a48712576b9b7cbb147c03becd957eb559af4c7'
BRANCH = 'feature/ir-image-workbook-1.10.0'
RUN_ID = 44096041775
WORKFLOW_ID = 397074012
WORKFLOW_PATH = '.github/workflows/suite-release.yml'
DELIVERY_REF = 'refs/heads/deliver/ir-suite-1.10.0'
GATES = (
    'software_opengl_setup', 'software_opengl_source', 'native_rust_build', 'full_source_suite',
    'software_opengl_packaged', 'diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe',
    'fixtures-gui-exe', 'features-exe', 'oven-playback-exe', 'rust-probe-exe', 'cli-exe',
    'native_rust_provenance', 'source_data_and_native_closure', 'ir_upstream_regressions',
    'ir_build_gates', 'ir-feedback-exe', 'ir-feedback-cli-exe',
)
ASSET_NAMES = {
    'BJT_1.10.0_manual.zip', 'BJT_1.10.0_source.zip', 'BJT_1.10.0_standalone.pyw',
    'BJT_1.10.0_Windows_x64.zip', 'SHA256SUMS.txt',
}
BUILD_STEP = 'Verify all tools, build genuine EXEs and package checked assets'
PRIVATE_UPLOAD_STEP = 'Upload verified assets to a private draft release'
SOURCE_SKIP_PAIRS = {
    ('test_number_tools.NumberToolTests', 'test_original_valid_conflict_policies_match_file_bytes_and_metadata'):
        'Original supplied tool is only present in the integration workspace',
    ('test_bjt_build_publish.AtomicPublishTests', 'test_open_old_file_survives_replacement'):
        'open-file replacement semantics vary by platform',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def gh(*arguments):
    return subprocess.check_output(['gh', *map(str, arguments)], text=True, encoding='utf-8')


def read(endpoint):
    return json.loads(gh('api', f'repos/{REPOSITORY}/{endpoint}'.rstrip('/')))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def is_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def positive(value):
    return type(value) is int and value > 0


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in ('', '.', '..') for part in value.split('/'))


def regular_file(path):
    require(not path.is_symlink() and path.is_file(), 'Regular file required: ' + str(path))


def require_actions():
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and os.environ.get('GH_TOKEN')
            and os.environ.get('GITHUB_REF') == DELIVERY_REF and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch',
            'Use only the authorized delivery-ref Actions token')


def download_archive(artifact_id, destination):
    """Use the normal Actions token/API; never print its redirected signed URL."""
    with destination.open('xb') as stream:
        result = subprocess.run(['gh', 'api', '--method', 'GET',
                                 f'repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip'],
                                stdout=stream, stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, 'Exact-ID artifact download failed; no extraction or retry performed')


def archive_members(archive):
    """Validate the complete ZIP inventory before writing any extracted bytes."""
    files, names = [], set()
    for item in archive.infolist():
        name = item.filename[:-1] if item.is_dir() else item.filename
        require(item.orig_filename == item.filename and safe_relative(name) and name.casefold() not in names
                and not item.flag_bits & 1, 'Unsafe, duplicate or encrypted artifact member')
        names.add(name.casefold())
        kind = stat.S_IFMT(item.external_attr >> 16)
        require(kind in (0, stat.S_IFREG, stat.S_IFDIR)
                and (kind != stat.S_IFDIR or item.is_dir())
                and (kind != stat.S_IFREG or not item.is_dir()), 'Artifact links/special files are forbidden')
        if not item.is_dir():
            files.append(item)
    require(files, 'Empty CI artifact archive')
    file_names = {item.filename.casefold() for item in files}
    require(all(not any(parent.as_posix().casefold() in file_names for parent in PurePosixPath(item.filename).parents
                        if parent.as_posix() != '.') for item in archive.infolist()),
            'Artifact file/directory path conflict')
    return files


def verify_archive(path, proof):
    regular_file(path)
    require(path.stat().st_size == proof['bytes'] and 'sha256:' + digest(path) == proof['digest'],
            'Actual downloaded artifact archive bytes/digest differ from immutable pins')
    with zipfile.ZipFile(path) as archive:
        archive_members(archive)


def download_candidate(archives, evidence, assets, facts):
    require_actions()
    verify_release(verify_remote(facts), facts)
    require(not archives.exists() and not evidence.exists() and not assets.exists(),
            'Use fresh download and extraction directories')
    archives.mkdir(parents=True)
    # Both exact outer ZIPs must pass SHA256/size/inventory before extraction.
    for proof in facts['artifacts'].values():
        path = archives / (str(proof['id']) + '.zip')
        download_archive(proof['id'], path)
        verify_archive(path, proof)
    for kind, destination in (('evidence', evidence), ('release', assets)):
        destination.mkdir(parents=True)
        proof = facts['artifacts'][kind]
        with zipfile.ZipFile(archives / (str(proof['id']) + '.zip')) as archive:
            for item in archive_members(archive):
                path = destination / item.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as incoming, path.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def verify_downloaded_trees(archives, evidence, assets, facts):
    require(archives.is_dir() and not archives.is_symlink()
            and {p.name for p in archives.iterdir()} == {str(p['id']) + '.zip' for p in facts['artifacts'].values()},
            'Exactly the two pinned outer archives are required')
    for kind, destination in (('evidence', evidence), ('release', assets)):
        proof = facts['artifacts'][kind]
        path = archives / (str(proof['id']) + '.zip')
        verify_archive(path, proof)
        require(destination.is_dir() and not destination.is_symlink(), 'Regular extracted artifact directory required')
        for node in destination.rglob('*'):
            require(not node.is_symlink() and (node.is_dir() or node.is_file()), 'Extracted links/special files forbidden')
        with zipfile.ZipFile(path) as archive:
            members = archive_members(archive)
            require({p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
                    == {item.filename for item in members}, 'Extracted artifact inventory differs from pinned outer ZIP')
            for item in members:
                target = destination / item.filename
                regular_file(target)
                with archive.open(item) as incoming:
                    expected = hashlib.file_digest(incoming, 'sha256').hexdigest()
                require(target.stat().st_size == item.file_size and digest(target) == expected,
                        'Extracted artifact bytes differ from pinned outer ZIP')


def load_pins(path=PINS):
    facts = json.loads(Path(path).read_text(encoding='utf-8'))
    require(facts.get('repository') == REPOSITORY and facts.get('repository_id') == REPOSITORY_ID
            and facts.get('version') == VERSION and facts.get('commit') == COMMIT
            and facts.get('branch') == BRANCH and facts.get('run_id') == RUN_ID
            and facts.get('workflow_id') == WORKFLOW_ID and facts.get('workflow_path') == WORKFLOW_PATH,
            'Wrong fixed candidate source')
    require(positive(facts.get('run_attempt')) and positive(facts.get('release_id'))
            and is_sha(facts.get('release_notes_sha256')), 'Complete run/release/notes pins required')
    artifacts = facts.get('artifacts', {})
    require(set(artifacts) == {'evidence', 'release'}, 'Exactly two pinned artifacts required')
    for kind, proof in artifacts.items():
        require(positive(proof.get('id')) and positive(proof.get('bytes'))
                and proof.get('name') == f'BJT-native-Windows-{kind}-{RUN_ID}'
                and isinstance(proof.get('digest'), str) and proof['digest'].startswith('sha256:')
                and is_sha(proof['digest'][7:]), 'Missing immutable artifact pins: ' + kind)
    require(artifacts['evidence']['id'] != artifacts['release']['id'], 'Artifact IDs must differ')
    assets = facts.get('assets', {})
    require(set(assets) == ASSET_NAMES, 'Five fixed candidate asset names required')
    for name, proof in assets.items():
        require(positive(proof.get('bytes')) and is_sha(proof.get('sha256')), 'Missing asset pins: ' + name)
    reviews = facts.get('source_reviews', {})
    require(set(reviews) == {'source', 'ci_delta'}, 'Both completed source reviews required')
    for proof in reviews.values():
        require(safe_relative(proof.get('path')) and is_sha(proof.get('sha256'))
                and isinstance(proof.get('status'), str), 'Missing source review byte pins')
    for key in ('imported_product_modules', 'inspected_resources'):
        require(isinstance(facts.get(key), list) and bool(facts[key]), 'Source closure pins required')
    require(set(facts.get('checker_files', {})) == {'suite/배포_빌드/release_exe.py', 'suite/배포_빌드/release_native.py'}
            and all(is_sha(value) for value in facts['checker_files'].values()), 'Exact source checker pins required')
    require(facts.get('candidate_artifact_approved') is False and facts.get('equipment_application') == 'HOLD'
            and facts.get('final_control_algorithm_approval') == 'HOLD', 'Candidate review and equipment holds required')
    return facts


def verify_source_reviews(facts, root):
    for kind, proof in facts['source_reviews'].items():
        path = root / proof['path']
        regular_file(path)
        require(digest(path) == proof['sha256'], 'Completed source review bytes changed')
        review = json.loads(path.read_text(encoding='utf-8'))
        invocation = review.get('invocation', {})
        require(review.get('status') == proof['status']
                and review.get('scope', {}).get('reviewed_revision') == proof['reviewed_revision']
                and invocation.get('requested_model') == 'gpt-6.1-sol'
                and invocation.get('requested_reasoning_effort') == 'max'
                and invocation.get('provider_model_identity_independently_attested') is False
                and not review.get('unresolved_findings'), 'Completed independent source review required')
        if kind == 'source':
            require(review.get('review_completed') is True, 'Source review must be completed')
        else:
            require(review.get('scope', {}).get('base_revision') == facts['source_reviews']['source']['reviewed_revision']
                    and proof['reviewed_revision'] == COMMIT and not review.get('findings'),
                    'CI-only review must extend the exact source review')


def verify_xml(path, count, *, source=False):
    regular_file(path)
    tree = ET.parse(path)
    cases = list(tree.iter('testcase'))
    require(len(cases) == count and not list(tree.iter('failure')) and not list(tree.iter('error')),
            'Wrong test count or failed raw XML: ' + path.name)
    skipped = []
    allowed = set()
    for case in cases:
        nodes = case.findall('skipped')
        require(len(nodes) <= 1, 'Duplicate skipped result')
        if not nodes:
            continue
        raw = nodes[0].get('message', '')
        reason = raw.removeprefix('unittest.case.SkipTest: ')
        matches = [pair for pair, expected in SOURCE_SKIP_PAIRS.items()
                   if case.get('name') == pair[1] and reason == expected
                   and (case.get('classname') == pair[0] or case.get('classname', '').endswith('.' + pair[0]))]
        require(source and len(matches) == 1 and matches[0] not in allowed, 'Unexpected native test skip')
        allowed.add(matches[0])
        skipped.append({'test': case.get('name'), 'reason': raw})
    require((source and allowed == set(SOURCE_SKIP_PAIRS)) or (not source and not skipped),
            'Exact native skip policy required')
    return skipped


def verify_rows(actual, expected, *, module=False):
    require(isinstance(actual, list) and len(actual) == len(expected), 'Wrong source closure row count')
    by_path = {}
    for row in actual:
        require(isinstance(row, dict) and safe_relative(row.get('path'))
                and row['path'] not in by_path and is_sha(row.get('sha256')), 'Invalid source closure path/hash')
        if module:
            require(row.get('module') == 'ir_feedback.' + PurePosixPath(row['path']).stem,
                    'Wrong imported module identity')
        by_path[row['path']] = row
    require(by_path == {row['path']: row for row in expected}, 'Reviewed source closure differs')


def require_ir_check(report, root):
    # Reuse the exact reviewed release checker; this loads helpers, never an app.
    build = root / 'suite' / '배포_빌드'
    sys.path.insert(0, str(build))
    try:
        spec = importlib.util.spec_from_file_location('_candidate_release_exe', build / 'release_exe.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.require_ir_check(report)
    finally:
        sys.path.pop(0)


def verify_local(evidence, assets, facts, *, root=ROOT):
    verify_source_reviews(facts, root)
    for name, expected in facts['checker_files'].items():
        path = root / name
        regular_file(path)
        require(digest(path) == expected, 'Reviewed source checker changed')
    for folder in (evidence, assets):
        require(folder.is_dir() and not folder.is_symlink(), 'Regular candidate directories required')
    def report(name):
        path = evidence / (name + '.json')
        regular_file(path)
        return json.loads(path.read_text(encoding='utf-8'))
    summary = report('native-summary')
    checks = summary.get('checks', {})
    require(summary.get('passed') is True and summary.get('version') == VERSION
            and summary.get('commit') == COMMIT and summary.get('assets') == facts['assets']
            and all(checks.get(name, {}).get('passed') is True for name in GATES),
            'Exact successful native summary and all twenty gates required')
    require(checks['full_source_suite'].get('skips') == verify_xml(evidence / 'source-tests.xml', 499, source=True),
            'Raw source-suite skips differ from summary')
    verify_xml(evidence / 'source-capsule-tests.xml', 8)
    require(checks['ir_build_gates'] == {'passed': True, 'source_capsule_regressions': True, 'capsule_tests': 8},
            'All eight source-capsule regressions required')
    upstream = report('ir-upstream-tests')
    require(upstream == checks['ir_upstream_regressions'], 'Raw upstream report and summary diverge')
    for key, expected in {'passed': True, 'tests': 572, 'expected_tests': 572, 'failures': 0,
                          'errors': 0, 'skips': 0, 'upstream_test_files': 58,
                          'source_revision': '2372c29602a9538f78593699c5e34c0485373524',
                          'upstream_snapshots_byte_verified': True, 'real_equipment_tested': False,
                          'excel_com_tested': False, 'skipped': [], 'failed': [], 'errored': []}.items():
        require(type(upstream.get(key)) is type(expected) and upstream.get(key) == expected,
                'Unexpected raw upstream result: ' + key)
    require(not any(key in upstream for key in ('runner_error', 'count_error')), 'Upstream runner error')
    verify_rows(upstream.get('imported_product_modules'), facts['imported_product_modules'], module=True)
    verify_rows(upstream.get('inspected_resources'), facts['inspected_resources'])
    for row in facts['inspected_resources']:
        path = root / 'suite/ir_feedback' / row['path']
        regular_file(path)
        require(digest(path) == row['sha256'], 'Reviewed local product resource changed')
    for name in ('diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe', 'fixtures-gui-exe',
                 'features-exe', 'oven-playback-exe', 'rust-probe-exe', 'ir-feedback-exe',
                 'ir-feedback-cli-exe', 'cli-exe'):
        raw = report(name)
        require(raw.get('passed') is True and (name == 'cli-exe' or raw.get('frozen') is True),
                'Raw frozen gate failed: ' + name)
        if name.startswith('ir-feedback'):
            require_ir_check(raw, root)
            require(raw.get('callback_errors') == [], 'Raw IR callback errors must be empty')
            init = raw['checks']['test_mode_initialization_has_no_native_process']
            images = raw['checks']['manual_images_and_previous_layer_features']
            require(init.get('native_process_attempts') == 0 and init.get('auto_discovery_started') is False
                    and images.get('layers') == 3 and images.get('white_binder') is True
                    and images.get('dpi') == [400, 400], 'Measured offline initialization and manual image features required')
            ir_assets = raw['checks']['authentic_builtin_workbook_and_worker_assets']
            require(sorted(ir_assets.get('json_files', [])) == sorted(
                    PurePosixPath(row['path']).name for row in facts['inspected_resources']
                    if '/' not in row['path'] and row['path'].endswith('.json')), 'IR JSON resource inventory differs')
            files = raw['checks']['asynchronous_close_preserves_csv_and_two_plots']['files']
            require(len(set(files)) == 3 and all(safe_relative(name) and '/' not in name for name in files)
                    and sorted(PurePosixPath(name).suffix.lower() for name in files) == ['.csv', '.png', '.png'],
                    'IR archived CSV and both PNG filenames required')
    require({p.name for p in assets.iterdir()} == ASSET_NAMES, 'Only five candidate files permitted')
    for name, proof in facts['assets'].items():
        path = assets / name
        regular_file(path)
        require(path.stat().st_size == proof['bytes'] and digest(path) == proof['sha256'], 'Candidate bytes changed: ' + name)
    checksums = ''.join(f'{facts["assets"][name]["sha256"]}  {name}\n' for name in sorted(ASSET_NAMES - {'SHA256SUMS.txt'}))
    require((assets / 'SHA256SUMS.txt').read_bytes() == checksums.encode('utf-8'), 'Exact checksum manifest required')


def verify_release(release, facts, *, complete=False):
    require(release.get('id') == facts['release_id'] and release.get('draft') is True
            and release.get('prerelease') is True and release.get('tag_name') == 'v' + VERSION
            and release.get('target_commitish') == COMMIT and isinstance(release.get('body'), str)
            and hashlib.sha256(release['body'].encode('utf-8')).hexdigest() == facts['release_notes_sha256'],
            'Refusing a published, altered or unrelated draft')
    assets = release.get('assets', [])
    require(isinstance(assets, list), 'Release assets must be a list')
    by_name = {item.get('name'): item for item in assets}
    require(len(by_name) == len(assets) and set(by_name) <= ASSET_NAMES, 'Unknown or duplicate remote asset')
    for name, item in by_name.items():
        proof = facts['assets'][name]
        require(item.get('state') == 'uploaded' and item.get('size') == proof['bytes']
                and item.get('digest') == 'sha256:' + proof['sha256'], 'Remote asset digest/size mismatch: ' + name)
    require(not complete or set(by_name) == ASSET_NAMES, 'Final draft requires exactly five candidate assets')
    return by_name


def verify_tag():
    item = read('git/ref/tags/v' + VERSION).get('object', {})
    seen = set()
    for _ in range(5):
        sha, kind = item.get('sha'), item.get('type')
        require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha) and sha not in seen,
                'Invalid or cyclic precreated tag')
        seen.add(sha)
        if kind == 'commit':
            require(sha == COMMIT, 'Precreated tag targets another source commit')
            return
        require(kind == 'tag', 'Unexpected tag object type')
        item = read('git/tags/' + sha).get('object', {})
    raise ValueError('Annotated tag chain exceeds bound')


def verify_remote(facts):
    repository = read('')
    require(repository.get('id') == REPOSITORY_ID and repository.get('full_name') == REPOSITORY
            and repository.get('private') is False and repository.get('visibility') == 'public', 'Wrong public repository')
    run = read(f'actions/runs/{RUN_ID}')
    require(run.get('id') == RUN_ID and run.get('head_sha') == COMMIT and run.get('head_branch') == BRANCH
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('workflow_id') == WORKFLOW_ID
            and run.get('run_attempt') == facts['run_attempt'] and run.get('path') == WORKFLOW_PATH,
            'Exact successful source workflow run required')
    jobs = read(f'actions/runs/{RUN_ID}/attempts/{facts["run_attempt"]}/jobs?per_page=100')
    require(jobs.get('total_count') == 1 and len(jobs.get('jobs', [])) == 1, 'Exactly one source job required')
    job = jobs['jobs'][0]
    require(job.get('name') == 'windows-release' and job.get('status') == 'completed'
            and job.get('conclusion') == 'success' and job.get('head_sha') == COMMIT, 'Wrong source job')
    steps = job.get('steps', [])
    by_name = {step.get('name'): step for step in steps}
    require(len(by_name) == len(steps) and all(step.get('status') == 'completed'
            and step.get('conclusion') in ('success', 'skipped') for step in steps)
            and all(by_name.get(name, {}).get('conclusion') == 'success' for name in
                    (BUILD_STEP, 'Preserve verification evidence', 'Preserve complete release files'))
            and by_name.get(PRIVATE_UPLOAD_STEP, {}).get('conclusion') == 'skipped',
            'Build/evidence/assets must succeed and private upload must be skipped')
    for proof in facts['artifacts'].values():
        item = read(f'actions/artifacts/{proof["id"]}')
        source = item.get('workflow_run', {})
        require(item.get('id') == proof['id'] and item.get('name') == proof['name']
                and item.get('expired') is False and item.get('digest') == proof['digest']
                and item.get('size_in_bytes') == proof['bytes'] and source.get('id') == RUN_ID
                and source.get('head_sha') == COMMIT and source.get('head_branch') == BRANCH
                and source.get('repository_id') == REPOSITORY_ID and source.get('head_repository_id') == REPOSITORY_ID,
                'Wrong, expired or changed CI artifact')
    verify_tag()
    return read(f'releases/{facts["release_id"]}')


def stage(evidence, assets, facts, *, archives, root=ROOT):
    require_actions()
    verify_downloaded_trees(archives, evidence, assets, facts)
    verify_local(evidence, assets, facts, root=root)
    verify_release(verify_remote(facts), facts)
    # Every preflight above is read-only. Each missing asset is uploaded once.
    for name in sorted(ASSET_NAMES):
        verify_tag()
        existing = verify_release(read(f'releases/{facts["release_id"]}'), facts)
        if name in existing:
            continue
        path = assets / name
        regular_file(path)
        require(path.stat().st_size == facts['assets'][name]['bytes']
                and digest(path) == facts['assets'][name]['sha256'], 'Candidate bytes changed before transfer')
        # Address the immutable release ID directly. A concurrent tag change
        # must never redirect this write to another release selected by tag.
        endpoint = (f'https://uploads.github.com/repos/{REPOSITORY}/releases/'
                    f'{facts["release_id"]}/assets?name={quote(name, safe="")}')
        gh('api', '--method', 'POST', endpoint, '--header',
           'Content-Type: application/octet-stream', '--input', path)
        require(name in verify_release(read(f'releases/{facts["release_id"]}'), facts), 'Uploaded asset absent on server')
    verify_tag()
    verify_release(read(f'releases/{facts["release_id"]}'), facts, complete=True)
    return dict(passed=True, candidate_staged=True, candidate_artifact_approved=False,
                independent_artifact_review='required_after_download', release_id=facts['release_id'],
                release_remains_draft=True, verified_source_commit=COMMIT, source_run=RUN_ID,
                source_run_attempt=facts['run_attempt'], source_artifacts=facts['artifacts'], assets=facts['assets'],
                repository_visibility='public', visibility_changed=False, program_bytes_changed=False,
                server_digest_readback_passed=True, outer_archive_bytes_and_digest_verified=True,
                extracted_files_match_outer_archives=True, publisher_commit=os.environ.get('GITHUB_SHA'),
                publisher_run=os.environ.get('GITHUB_RUN_ID'), excel_com_verified=False,
                equipment_verified=False, equipment_application='HOLD', final_control_algorithm_approval='HOLD')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage', action='store_true')
    mode.add_argument('--download', action='store_true')
    args = parser.parse_args()
    facts = load_pins()
    if args.download:
        download_candidate(args.archives, args.evidence, args.assets, facts)
    if args.stage:
        result = stage(args.evidence, args.assets, facts, archives=args.archives)
    else:
        verify_downloaded_trees(args.archives, args.evidence, args.assets, facts)
        verify_local(args.evidence, args.assets, facts)
        result = {'passed': True, 'candidate_staged': False, 'candidate_artifact_approved': False,
                  'outer_archive_bytes_and_digest_verified': True, 'extracted_files_match_outer_archives': True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
