"""Publish the already-verified 1.7.0 native Windows build, without rebuilding.

The exact source run, commit, artifact identities and file digests are fixed for
this release. Only the existing private draft may be updated. GitHub sanitizes
non-ASCII asset names, so publication uses distinct ASCII names and new sums.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REPOSITORY = 'rlaengus444-ui/BJT_Tools_integrated'
VERSION = '1.7.0'
COMMIT = 'fe8d9ea21770edd54174d7a37213af355d3e65ec'
RUN_ID = 40330519907
RELEASE_ID = 424290858
ARTIFACTS = {
    12655093162: 'sha256:324124e77c243ed42de3eda528e60e9d7612291ab28314fd26677171eb98446f',
    12654878218: 'sha256:45eeb7dc0ffb4889ce9d1c25f253184655b847dfc0f096e600622a7df54f8408',
}
KNOWN_ASSETS = {
    'BJT_통합프로그램.pyw': {'bytes': 2537262, 'sha256': '70fa4fd7499b8a73992bded9abb464554f2f82670f6b5dcc7422bfda3ac232e1'},
    'BJT_통합프로그램_1.7.0_EXE.zip': {'bytes': 309325775, 'sha256': 'f66736807208c49c9cefefb3b64152d722d5ab659782e40c6f7519d238b5a196'},
    'BJT_통합프로그램_1.7.0_사용설명서.zip': {'bytes': 229677, 'sha256': '5268f2c972e8b037124560ec91fcad414d086c57adad2b4b958c6fb1c51ad659'},
    'BJT_통합프로그램_1.7.0_전체소스.zip': {'bytes': 4037439, 'sha256': '698f2315898b3d696440592e9913e07e08d5073a9647b066a3d395b57f254b96'},
    'SHA256SUMS.txt': {'bytes': 427, 'sha256': '0767c6475da5b3453b629f5b8679b41d76db128d9e7c87fff9cfd4ec4b41b6f9'},
}
ASCII_NAMES = {
    'BJT_통합프로그램.pyw': 'BJT_1.7.0_standalone.pyw',
    'BJT_통합프로그램_1.7.0_EXE.zip': 'BJT_1.7.0_Windows_x64.zip',
    'BJT_통합프로그램_1.7.0_사용설명서.zip': 'BJT_1.7.0_manual.zip',
    'BJT_통합프로그램_1.7.0_전체소스.zip': 'BJT_1.7.0_source.zip',
}
REQUIRED_CHECKS = ('full_source_suite', 'diagnose-exe', 'smoke-exe', 'smoke-gui-exe',
                   'fixtures-exe', 'fixtures-gui-exe', 'features-exe', 'cli-exe',
                   'source_data_and_native_closure')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_summary(summary):
    if (summary.get('passed') is not True or summary.get('version') != VERSION
            or summary.get('commit') != COMMIT):
        raise ValueError('Expected a successful native build for the exact verified commit')
    if any(summary.get('checks', {}).get(name, {}).get('passed') is not True
           for name in REQUIRED_CHECKS):
        raise ValueError('Every native verification gate must have passed')
    if summary.get('assets') != KNOWN_ASSETS:
        raise ValueError('Native asset manifest differs from the independently recorded digests')


def prepare(evidence, assets, output):
    summary = json.loads((evidence / 'native-summary.json').read_text(encoding='utf-8'))
    verify_summary(summary)
    if {path.name for path in assets.iterdir()} != set(KNOWN_ASSETS):
        raise ValueError('The artifact must contain exactly the five verified original assets')
    for name, expected in KNOWN_ASSETS.items():
        path = assets / name
        if not path.is_file() or path.stat().st_size != expected['bytes'] or digest(path) != expected['sha256']:
            raise ValueError('Original asset failed size/SHA-256 verification: ' + name)
    if output.exists():
        raise FileExistsError('Use a fresh publication directory')
    output.mkdir(parents=True)
    published = {}
    for original, renamed in ASCII_NAMES.items():
        renamed.encode('ascii')
        target = output / renamed
        shutil.copyfile(assets / original, target)
        expected = KNOWN_ASSETS[original]
        if digest(target) != expected['sha256']:
            raise ValueError('Renamed copy changed the verified bytes')
        published[renamed] = dict(expected)
    (output / 'SHA256SUMS.txt').write_text(''.join(
        f'{published[name]["sha256"]}  {name}\n' for name in sorted(published)),
        encoding='utf-8', newline='\n')
    sums = output / 'SHA256SUMS.txt'
    published[sums.name] = {'bytes': sums.stat().st_size, 'sha256': digest(sums)}
    return {'passed': True, 'version': VERSION, 'verified_commit': COMMIT,
            'verified_run': RUN_ID, 'artifact_ids': sorted(ARTIFACTS),
            'original_assets': KNOWN_ASSETS, 'ascii_assets': published,
            'program_bytes_changed': False, 'rebuilt': False}


def gh(*arguments):
    result = subprocess.run(['gh', *map(str, arguments)], check=True,
                            capture_output=True, text=True, encoding='utf-8')
    return result.stdout


def validate_release(release):
    if (release.get('id') != RELEASE_ID or release.get('draft') is not True
            or release.get('tag_name') != 'v' + VERSION
            or release.get('target_commitish') != COMMIT):
        raise ValueError('Refusing a published or unrelated release')


def verify_remote_assets(remote, expected):
    by_name = {item['name']: item for item in remote}
    for name, record in expected.items():
        asset = by_name.get(name, {})
        if (asset.get('state') != 'uploaded' or asset.get('size') != record['bytes']
                or asset.get('digest') != 'sha256:' + record['sha256']):
            raise ValueError('GitHub readback size/digest failed: ' + name)


def upload(output, report):
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or not os.environ.get('GH_TOKEN'):
        raise ValueError('Use the repository-scoped GitHub Actions job token')
    metadata = json.loads(gh('api', f'repos/{REPOSITORY}'))
    if metadata.get('private') is not True:
        raise ValueError('This publication must remain in the private repository')
    for artifact_id, expected_digest in ARTIFACTS.items():
        item = json.loads(gh('api', f'repos/{REPOSITORY}/actions/artifacts/{artifact_id}'))
        if (item.get('expired') or item.get('digest') != expected_digest
                or item.get('workflow_run', {}).get('id') != RUN_ID
                or item.get('workflow_run', {}).get('head_sha') != COMMIT):
            raise ValueError('Wrong or expired verified source artifact')
    release = json.loads(gh('api', f'repos/{REPOSITORY}/releases/{RELEASE_ID}'))
    validate_release(release)
    # gh upload sends several file arguments concurrently. One invocation per
    # unique ASCII name prevents sanitized-name collisions and upload races.
    for name in sorted(report['ascii_assets']):
        path = output / name
        if digest(path) != report['ascii_assets'][name]['sha256']:
            raise ValueError('Publication file changed before upload')
        gh('release', 'upload', 'v' + VERSION, '--repo', REPOSITORY, '--clobber', path)
    release = json.loads(gh('api', f'repos/{REPOSITORY}/releases/{RELEASE_ID}'))
    validate_release(release)
    verify_remote_assets(release.get('assets', []), report['ascii_assets'])
    # Remove only the two known sanitized partial uploads after every new asset
    # has been read back with the correct server-computed SHA-256 digest.
    old = {
        'BJT_._1.7.0_.zip': {KNOWN_ASSETS['BJT_통합프로그램_1.7.0_사용설명서.zip']['sha256'],
                            KNOWN_ASSETS['BJT_통합프로그램_1.7.0_전체소스.zip']['sha256']},
        'BJT_.pyw': {KNOWN_ASSETS['BJT_통합프로그램.pyw']['sha256']},
    }
    for item in release.get('assets', []):
        if item['name'] in old:
            if item.get('digest', '').removeprefix('sha256:') not in old[item['name']]:
                raise ValueError('Unknown content under a legacy sanitized asset name')
            gh('api', '--method', 'DELETE', f'repos/{REPOSITORY}/releases/assets/{item["id"]}')
    release = json.loads(gh('api', f'repos/{REPOSITORY}/releases/{RELEASE_ID}'))
    validate_release(release)
    verify_remote_assets(release.get('assets', []), report['ascii_assets'])
    report['uploaded'] = True
    report['server_digest_readback_passed'] = True
    report['release_remains_draft'] = True
    report['release_id'] = RELEASE_ID
    report['release_url'] = release['html_url']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--upload', action='store_true')
    args = parser.parse_args()
    report = prepare(args.evidence.resolve(), args.assets.resolve(), args.output.resolve())
    if args.upload:
        upload(args.output.resolve(), report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'uploaded': report.get('uploaded', False),
                      'assets': sorted(report['ascii_assets'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
