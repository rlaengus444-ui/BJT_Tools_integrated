"""Attach checked Windows CI outputs to a prepared evaluation prerelease draft."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


REPOSITORY = 'rlaengus444-ui/BJT_Tools_integrated'
RELEASE_REFS = {'refs/heads/feature/recoater-simulator-1.11.0': '1.11.0'}


def api(endpoint, *args):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint, *args], encoding='utf-8'))


def stage(output, release_id):
    version = RELEASE_REFS.get(os.environ.get('GITHUB_REF'))
    if (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or version is None or release_id <= 0
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or not os.environ.get('GH_TOKEN')):
        raise RuntimeError('An approved release branch, explicit draft, and job token are required')
    tag_name = f'v{version}'
    assets = {f'BJT_{version}_{name}' for name in
              ('Windows_x64.zip', 'source.zip', 'manual.zip', 'standalone.pyw')} | {'SHA256SUMS.txt'}
    summary = json.loads((output / 'reports/native-summary.json').read_text(encoding='utf-8'))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if (summary.get('passed') is not True or summary['version'] != version
            or commit != summary['commit'] or commit != os.environ.get('GITHUB_SHA')
            or summary['checks']['features-exe']['passed'] is not True):
        raise RuntimeError('A successful Windows build of this exact revision is required')
    folder = output / 'release-assets'
    expected = summary['assets']
    if set(expected) != assets or {path.name for path in folder.iterdir()} != assets:
        raise RuntimeError('Expected the five verified release assets')
    for name, proof in expected.items():
        path = folder / name
        with path.open('rb') as stream:
            sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != proof['bytes'] or sha256 != proof['sha256']:
            raise RuntimeError('Build asset changed: ' + name)

    release_api = f'repos/{REPOSITORY}/releases/{release_id}'
    tag = api(f'repos/{REPOSITORY}/git/ref/tags/{tag_name}')
    if tag['object']['type'] != 'commit' or tag['object']['sha'] != commit:
        raise RuntimeError('The release tag must point to the exact build revision')

    def read_draft():
        release = api(release_api)
        if (release['id'] != release_id or release['draft'] is not True
                or release['prerelease'] is not True or release['tag_name'] != tag_name
                or release['target_commitish'] != commit):
            raise RuntimeError('The prepared prerelease must still be an unpublished draft for this build')
        present = {}
        for asset in release['assets']:
            name = asset['name']
            proof = expected.get(name)
            if (proof is None or name in present or asset['state'] != 'uploaded'
                    or asset['size'] != proof['bytes'] or asset['digest'] != 'sha256:' + proof['sha256']):
                raise RuntimeError('Unexpected asset in the prepared draft: ' + name)
            present[name] = asset['id']
        return present

    for name in sorted(expected):
        if name not in read_draft():
            endpoint = f'https://uploads.github.com/{release_api}/assets?name={name}'
            api(endpoint, '--method', 'POST', '--header', 'Content-Type: application/octet-stream',
                '--input', str(folder / name))
            if name not in read_draft():
                raise RuntimeError('Draft upload was not confirmed: ' + name)
    uploaded = read_draft()
    if set(uploaded) != assets:
        raise RuntimeError('The draft asset set is incomplete')
    print(json.dumps({'release_id': release_id, 'tag': tag_name, 'commit': commit,
                      'draft': True, 'asset_ids': uploaded, 'assets': expected}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release-id', required=True, type=int)
    args = parser.parse_args()
    stage(args.output, args.release_id)
