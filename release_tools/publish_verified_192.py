"""Upload only the reviewed 1.9.2 Windows artifacts to their existing draft.

This one-off route uses the repository-scoped Actions token. It preserves the
repository's current public visibility and the historical private publishers.
It never rebuilds, overwrites assets, publishes a release or changes settings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPOSITORY = 'rlaengus444-ui/BJT_Tools_integrated'
REPOSITORY_ID = 1433912494
VERSION = '1.9.2'
COMMIT = '16f2f5fa8c631d60222eeff178fab35d876913a1'
RUN_ID = 43852970248
RELEASE_ID = 446930711
ARTIFACTS = {
    14219312727: ('BJT-native-Windows-evidence-43852970248', 'sha256:e01ee9b74c3507155fed5d0098f2e660eca36af0c6877bc9ea9d98b296e32c89'),
    14219512451: ('BJT-native-Windows-release-43852970248', 'sha256:2246fb935a4fba5b399f0b29a9f1988db394c9c6746feaef06940a4cf933fd3f'),
}
KNOWN_ASSETS = {
    'BJT_1.9.2_manual.zip': {'bytes': 234668, 'sha256': '1a92eff45d65366a969a675ece1da720dc3077565c6e9f82aecdb99e4dc23409'},
    'BJT_1.9.2_source.zip': {'bytes': 7015791, 'sha256': '12ed811bf4a575fce9f6503103b1ed10e01f55844c54b1ef8b2bd657461b302c'},
    'BJT_1.9.2_standalone.pyw': {'bytes': 3949866, 'sha256': '0861fcc334a661d80238d2ffe5162ee7a0b418f9b8d8b4926cf7a205b419993c'},
    'BJT_1.9.2_Windows_x64.zip': {'bytes': 408152034, 'sha256': 'd444bd338ffc1c13a1ab9f845342d1ede025b7608e6e1904b35a154af243a747'},
    'SHA256SUMS.txt': {'bytes': 357, 'sha256': '411e0ee772c706b6f24f9414e44f856fc20d45a1ef9fe442f4ab59b18efa3437'},
}
REQUIRED_CHECKS = (
    'software_opengl_setup', 'software_opengl_source', 'native_rust_build', 'full_source_suite',
    'software_opengl_packaged', 'diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe',
    'fixtures-gui-exe', 'features-exe', 'oven-playback-exe', 'rust-probe-exe', 'cli-exe',
    'native_rust_provenance', 'source_data_and_native_closure',
)
BUILD_STEP = 'Verify all tools, build genuine EXEs and package checked assets'
UPLOAD_STEP = 'Upload verified assets to a private draft release'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def gh(*arguments):
    return subprocess.check_output(['gh', *map(str, arguments)], text=True, encoding='utf-8')


def read(endpoint):
    return json.loads(gh('api', f'repos/{REPOSITORY}/{endpoint}'.rstrip('/')))


def verify_local(evidence, assets):
    summary = json.loads((evidence / 'native-summary.json').read_text(encoding='utf-8'))
    if (summary.get('passed') is not True or summary.get('version') != VERSION
            or summary.get('commit') != COMMIT or summary.get('assets') != KNOWN_ASSETS
            or any(summary.get('checks', {}).get(name, {}).get('passed') is not True
                   for name in REQUIRED_CHECKS)):
        raise ValueError('The exact reviewed native build and all sixteen gates are required')
    if {p.name for p in assets.iterdir()} != set(KNOWN_ASSETS):
        raise ValueError('Only the five verified files may be uploaded')
    for name, proof in KNOWN_ASSETS.items():
        path = assets / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != proof['bytes']
                or digest(path) != proof['sha256']):
            raise ValueError('Verified asset bytes changed: ' + name)


def verify_release(release, *, complete=False):
    if (release.get('id') != RELEASE_ID or release.get('draft') is not True
            or release.get('tag_name') != 'v' + VERSION or release.get('target_commitish') != COMMIT):
        raise ValueError('Refusing a published or unrelated release')
    assets = release.get('assets', [])
    by_name = {a['name']: a for a in assets}
    if len(assets) != len(by_name) or not set(by_name) <= set(KNOWN_ASSETS):
        raise ValueError('Unexpected or duplicate remote asset')
    for name, asset in by_name.items():
        proof = KNOWN_ASSETS[name]
        if (asset.get('state') != 'uploaded' or asset.get('size') != proof['bytes']
                or asset.get('digest') != 'sha256:' + proof['sha256']):
            raise ValueError('Mismatched or incomplete remote asset: ' + name)
    if complete and set(by_name) != set(KNOWN_ASSETS):
        raise ValueError('Final draft must contain exactly the five verified assets')
    return by_name


def verify_remote():
    repository = read('')
    if (repository.get('id') != REPOSITORY_ID or repository.get('full_name') != REPOSITORY
            or repository.get('private') is not False or repository.get('visibility') != 'public'):
        raise ValueError('Expected the user-selected public repository without visibility changes')
    run = read(f'actions/runs/{RUN_ID}')
    if (run.get('id') != RUN_ID or run.get('head_sha') != COMMIT or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch' or run.get('status') != 'completed'
            or run.get('conclusion') != 'failure' or run.get('workflow_id') != 397074012):
        raise ValueError('Wrong source workflow run')
    jobs = read(f'actions/runs/{RUN_ID}/jobs')['jobs']
    if len(jobs) != 1 or jobs[0].get('name') != 'windows-release':
        raise ValueError('Unexpected source jobs')
    steps = jobs[0]['steps']
    failed = [s['name'] for s in steps if s.get('conclusion') == 'failure']
    successful = {s['name'] for s in steps if s.get('conclusion') == 'success'}
    if failed != [UPLOAD_STEP] or not {BUILD_STEP, 'Preserve verification evidence', 'Preserve complete release files'} <= successful:
        raise ValueError('Source run failed outside its known automatic-publication step')
    for artifact_id, (name, expected_digest) in ARTIFACTS.items():
        item = read(f'actions/artifacts/{artifact_id}')
        source = item.get('workflow_run', {})
        if (item.get('id') != artifact_id or item.get('name') != name or item.get('expired') is not False
                or item.get('digest') != expected_digest or source.get('id') != RUN_ID
                or source.get('head_sha') != COMMIT or source.get('repository_id') != REPOSITORY_ID
                or source.get('head_repository_id') != REPOSITORY_ID):
            raise ValueError('Wrong or expired verified artifact')
    return read(f'releases/{RELEASE_ID}')


def upload(evidence, assets):
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or not os.environ.get('GH_TOKEN'):
        raise ValueError('Use the repository-scoped GitHub Actions token')
    verify_local(evidence, assets)
    verify_release(verify_remote())
    # No mutation occurs until every local and remote preflight has passed.
    for name in sorted(KNOWN_ASSETS):
        existing = verify_release(read(f'releases/{RELEASE_ID}'))
        if name in existing:
            continue
        path = assets / name
        if path.stat().st_size != KNOWN_ASSETS[name]['bytes'] or digest(path) != KNOWN_ASSETS[name]['sha256']:
            raise ValueError('Publication bytes changed before upload: ' + name)
        gh('release', 'upload', 'v' + VERSION, '--repo', REPOSITORY, path)
        readback = verify_release(read(f'releases/{RELEASE_ID}'))
        if name not in readback:
            raise ValueError('Uploaded file missing from server readback')
    release = read(f'releases/{RELEASE_ID}')
    verify_release(release, complete=True)
    return dict(passed=True, release_id=RELEASE_ID, release_remains_draft=True,
                verified_app_commit=COMMIT, verified_run=RUN_ID, assets=KNOWN_ASSETS,
                repository_visibility='public', visibility_changed=False,
                publisher_commit=os.environ.get('GITHUB_SHA'), publisher_run=os.environ.get('GITHUB_RUN_ID'),
                program_bytes_changed=False, server_digest_readback_passed=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--upload', action='store_true')
    args = parser.parse_args()
    verify_local(args.evidence, args.assets)
    report = upload(args.evidence, args.assets) if args.upload else {'passed': True, 'uploaded': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'release_remains_draft': True, 'uploaded': args.upload}))


if __name__ == '__main__':
    main()
