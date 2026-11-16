"""Exact-build publication refuses wrong sources and never overwrites assets."""
import copy
import json
from pathlib import Path

import pytest
import publish_verified_192 as publisher


@pytest.fixture
def publication(tmp_path, monkeypatch):
    assets = tmp_path / 'assets'; assets.mkdir()
    evidence = tmp_path / 'evidence'; evidence.mkdir()
    expected = {}
    for name in publisher.KNOWN_ASSETS:
        path = assets / name; path.write_bytes(('verified ' + name).encode())
        expected[name] = dict(bytes=path.stat().st_size, sha256=publisher.digest(path))
    monkeypatch.setattr(publisher, 'KNOWN_ASSETS', expected)
    summary = dict(passed=True, version=publisher.VERSION, commit=publisher.COMMIT, assets=expected,
                   checks={name: {'passed': True} for name in publisher.REQUIRED_CHECKS})
    (evidence / 'native-summary.json').write_text(json.dumps(summary))
    release = dict(id=publisher.RELEASE_ID, draft=True, tag_name='v1.9.2',
                   target_commitish=publisher.COMMIT, assets=[])
    remote = {
        '': dict(id=publisher.REPOSITORY_ID, full_name=publisher.REPOSITORY, private=False, visibility='public'),
        f'actions/runs/{publisher.RUN_ID}': dict(id=publisher.RUN_ID, head_sha=publisher.COMMIT,
            head_branch='main', event='workflow_dispatch', status='completed', conclusion='failure', workflow_id=397074012),
        f'actions/runs/{publisher.RUN_ID}/jobs': {'jobs': [dict(name='windows-release', steps=[
            dict(name=name, conclusion='success') for name in (publisher.BUILD_STEP,
                'Preserve verification evidence', 'Preserve complete release files')
        ] + [dict(name=publisher.UPLOAD_STEP, conclusion='failure')])]},
        f'releases/{publisher.RELEASE_ID}': release,
    }
    for artifact_id, (name, digest) in publisher.ARTIFACTS.items():
        remote[f'actions/artifacts/{artifact_id}'] = dict(id=artifact_id, name=name, digest=digest,
            expired=False, workflow_run=dict(id=publisher.RUN_ID, head_sha=publisher.COMMIT,
                repository_id=publisher.REPOSITORY_ID, head_repository_id=publisher.REPOSITORY_ID))
    mutations = []
    def asset(name):
        proof = expected[name]
        return dict(name=name, state='uploaded', size=proof['bytes'], digest='sha256:' + proof['sha256'])
    def upload(*arguments):
        assert arguments[:5] == ('release', 'upload', 'v1.9.2', '--repo', publisher.REPOSITORY)
        assert '--clobber' not in arguments
        path = Path(arguments[5]); mutations.append(path.name)
        release['assets'].append(asset(path.name))
        return ''
    monkeypatch.setattr(publisher, 'read', lambda endpoint: copy.deepcopy(remote[endpoint]))
    monkeypatch.setattr(publisher, 'gh', upload)
    monkeypatch.setenv('GITHUB_REPOSITORY', publisher.REPOSITORY)
    monkeypatch.setenv('GH_TOKEN', 'mock-token-never-sent')
    return assets, evidence, remote, release, mutations, asset


def test_upload_preserves_every_byte_and_keeps_draft(publication):
    assets, evidence, remote, release, mutations, asset = publication
    before = {p.name: p.read_bytes() for p in assets.iterdir()}
    report = publisher.upload(evidence, assets)
    assert mutations == sorted(publisher.KNOWN_ASSETS)
    assert report['server_digest_readback_passed'] and report['release_remains_draft']
    assert report['program_bytes_changed'] is False and report['visibility_changed'] is False
    assert {p.name: p.read_bytes() for p in assets.iterdir()} == before
    publisher.verify_release(release, complete=True)


def test_retry_skips_only_matching_uploaded_asset(publication):
    assets, evidence, remote, release, mutations, asset = publication
    name = sorted(publisher.KNOWN_ASSETS)[0]; release['assets'].append(asset(name))
    publisher.upload(evidence, assets)
    assert mutations == sorted(set(publisher.KNOWN_ASSETS) - {name})


@pytest.mark.parametrize('failure', [
    'repo_id', 'repo_name', 'private_repo', 'run_commit', 'run_state', 'run_workflow',
    'build_failure', 'extra_job', 'artifact_digest', 'artifact_expired', 'artifact_repo',
    'published', 'release_id', 'release_tag', 'release_commit', 'extra_asset',
    'duplicate_asset', 'remote_digest', 'partial_asset',
])
def test_bad_remote_preflight_causes_no_mutation(publication, failure):
    assets, evidence, remote, release, mutations, asset = publication
    run = remote[f'actions/runs/{publisher.RUN_ID}']
    jobs = remote[f'actions/runs/{publisher.RUN_ID}/jobs']['jobs']
    artifact = remote[f'actions/artifacts/{next(iter(publisher.ARTIFACTS))}']
    if failure == 'repo_id': remote['']['id'] = 1
    elif failure == 'repo_name': remote['']['full_name'] = 'other/repo'
    elif failure == 'private_repo': remote['']['private'] = True
    elif failure == 'run_commit': run['head_sha'] = '0' * 40
    elif failure == 'run_state': run['status'] = 'in_progress'
    elif failure == 'run_workflow': run['workflow_id'] = 1
    elif failure == 'build_failure': jobs[0]['steps'][0]['conclusion'] = 'failure'
    elif failure == 'extra_job': jobs.append(copy.deepcopy(jobs[0]))
    elif failure == 'artifact_digest': artifact['digest'] = 'sha256:wrong'
    elif failure == 'artifact_expired': artifact['expired'] = True
    elif failure == 'artifact_repo': artifact['workflow_run']['head_repository_id'] = 1
    elif failure == 'published': release['draft'] = False
    elif failure == 'release_id': release['id'] = 1
    elif failure == 'release_tag': release['tag_name'] = 'v0.0.0'
    elif failure == 'release_commit': release['target_commitish'] = '0' * 40
    elif failure == 'extra_asset': release['assets'].append(dict(name='unreviewed.exe'))
    else:
        record = asset(next(iter(publisher.KNOWN_ASSETS)))
        release['assets'].append(record)
        if failure == 'duplicate_asset': release['assets'].append(copy.deepcopy(record))
        elif failure == 'remote_digest': record['digest'] = 'sha256:wrong'
        elif failure == 'partial_asset': record['state'] = 'starter'
    with pytest.raises(ValueError): publisher.upload(evidence, assets)
    assert mutations == []


@pytest.mark.parametrize('failure', ['bytes', 'extra_file', 'summary_commit', 'missing_gate'])
def test_bad_local_source_causes_no_mutation(publication, failure):
    assets, evidence, remote, release, mutations, asset = publication
    if failure == 'bytes': (assets / next(iter(publisher.KNOWN_ASSETS))).write_bytes(b'changed')
    elif failure == 'extra_file': (assets / 'private-model.step').write_bytes(b'not for release')
    else:
        path = evidence / 'native-summary.json'; summary = json.loads(path.read_text())
        if failure == 'summary_commit': summary['commit'] = '0' * 40
        else: del summary['checks']['features-exe']
        path.write_text(json.dumps(summary))
    with pytest.raises(ValueError): publisher.upload(evidence, assets)
    assert mutations == []


def test_draft_race_is_rechecked_before_first_upload(publication, monkeypatch):
    assets, evidence, remote, release, mutations, asset = publication
    original = publisher.read; reads = []
    def raced(endpoint):
        if endpoint == f'releases/{publisher.RELEASE_ID}':
            reads.append(endpoint)
            if len(reads) == 2: release['draft'] = False
        return original(endpoint)
    monkeypatch.setattr(publisher, 'read', raced)
    with pytest.raises(ValueError): publisher.upload(evidence, assets)
    assert mutations == []


def test_bad_upload_readback_stops_further_mutations(publication, monkeypatch):
    assets, evidence, remote, release, mutations, asset = publication
    original = publisher.gh
    def corrupt_readback(*args):
        original(*args); release['assets'][-1]['digest'] = 'sha256:wrong'
    monkeypatch.setattr(publisher, 'gh', corrupt_readback)
    with pytest.raises(ValueError): publisher.upload(evidence, assets)
    assert len(mutations) == 1
