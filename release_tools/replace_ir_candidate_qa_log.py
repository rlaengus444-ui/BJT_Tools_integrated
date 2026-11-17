"""Replace only the approved QA-log-only EXE repack and checksums in a draft.

This one-off route never publishes, changes visibility, rebuilds product code,
renames assets or replaces the other three files. Original CI artifacts and
reviewed staging code remain immutable. Unknown new pins disable execution.
Every write uses an exact existing release/asset ID, with fresh draft checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import zipfile

if __package__:
    from . import stage_verified_ir_110 as original
else:
    import stage_verified_ir_110 as original

ROOT = Path(__file__).resolve().parents[1]
PINS = Path(__file__).with_suffix('.json')
RELEASE_ID = 448714553
EXE = 'BJT_1.10.0_Windows_x64.zip'
SUMS = 'SHA256SUMS.txt'
TARGETS = (EXE, SUMS)
REMOVED = {'path': 'BJT_통합프로그램_1.10.0_EXE/_internal/logs/errors.log', 'bytes': 1626,
           'sha256': 'c06264143b923010383965a790f0561014fa5eecc3908827f81ecea359394df1'}
OLD_IDS = {EXE: 759344713, SUMS: 759345731,
           'BJT_1.10.0_manual.zip': 759345381,
           'BJT_1.10.0_source.zip': 759345454,
           'BJT_1.10.0_standalone.pyw': 759345577}
SENTENCE = '원본 검증 ZIP에서 합성 QA 시험의 오류 로그 한 개만 제외했습니다. 나머지 실행 파일·리소스는 해당 Windows 검증 빌드와 바이트가 동일합니다.'
PHASES = [('old', 'old'), ('absent', 'old'), ('new', 'old'), ('new', 'absent'), ('new', 'new')]


def require(value, message):
    original.require(value, message)


def note_body(facts):
    old_sha = facts['old_assets'][EXE]['sha256']
    line = 'EXE ZIP SHA-256: ' + old_sha
    require(facts['old_notes_body'].count(line) == 1, 'Exact original EXE note line required')
    return (facts['old_notes_body'].replace(line, 'EXE ZIP SHA-256: ' + facts['new_assets'][EXE]['sha256'])
            + '\n' + SENTENCE + '\n원본 Windows CI ZIP SHA-256: ' + old_sha + '\n')


def text_digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def load_pins(path=PINS):
    facts = json.loads(Path(path).read_text(encoding='utf-8'))
    require(facts.get('repository') == original.REPOSITORY
            and facts.get('repository_id') == original.REPOSITORY_ID
            and facts.get('release_id') == RELEASE_ID and facts.get('source_commit') == original.COMMIT
            and facts.get('source_run') == original.RUN_ID, 'Wrong fixed private-draft replacement target')
    require(set(facts.get('old_assets', {})) == original.ASSET_NAMES
            and set(facts.get('new_assets', {})) == original.ASSET_NAMES, 'Five exact old/new asset facts required')
    for name, proof in facts['old_assets'].items():
        require(proof.get('id') == OLD_IDS[name] and original.positive(proof.get('bytes'))
                and original.is_sha(proof.get('sha256')), 'Exact original server asset pins required')
        new = facts['new_assets'][name]
        require(original.positive(new.get('bytes')) and original.is_sha(new.get('sha256')), 'Unknown cleaned asset pins')
        if name in TARGETS:
            require(new['sha256'] != proof['sha256'], 'Only the two approved changed payloads may be replaced')
        else:
            require(new == {key: proof[key] for key in ('bytes', 'sha256')}, 'Preserved asset bytes must remain exact')
    require(isinstance(facts.get('old_notes_body'), str)
            and text_digest(facts['old_notes_body']) == facts.get('old_notes_sha256')
            and original.is_sha(facts.get('new_notes_sha256'))
            and text_digest(note_body(facts)) == facts['new_notes_sha256'], 'Exact old/new notes pins required')
    require(set(facts.get('files', {})) == {'stage_code', 'stage_pins', 'cleanup_code', 'provenance', 'cleaned_facts',
                                         'clean_artifact_review', 'replacement_review'}, 'All immutable proof/code pins required')
    for proof in facts['files'].values():
        require(original.safe_relative(proof.get('path')) and original.is_sha(proof.get('sha256')),
                'Unknown immutable code/review/provenance pin')
    for key, path in {'stage_code': 'release_tools/stage_verified_ir_110.py',
                      'stage_pins': 'release_tools/stage_verified_ir_110.json',
                      'cleanup_code': 'release_tools/clean_ir_candidate.py'}.items():
        require(facts['files'][key]['path'] == path, 'Unexpected code/pins source path')
    require(facts.get('equipment_application') == 'HOLD' and facts.get('final_control_algorithm_approval') == 'HOLD',
            'Equipment and algorithm holds must remain')
    return facts


def verify_pinned_files(facts, root=ROOT):
    for proof in facts['files'].values():
        path = root / proof['path']
        original.regular_file(path)
        require(original.digest(path) == proof['sha256'], 'Pinned code/review/provenance bytes changed: ' + proof['path'])
    for kind in ('clean_artifact_review', 'replacement_review'):
        review = json.loads((root / facts['files'][kind]['path']).read_text(encoding='utf-8'))
        invocation = review.get('invocation', {})
        require(review.get('review_completed') is True and review.get('unresolved_findings') == []
                and invocation.get('requested_model') == 'gpt-6.1-sol'
                and invocation.get('requested_reasoning_effort') == 'max'
                and invocation.get('provider_model_identity_independently_attested') is False,
                'Completed independent review required: ' + kind)
        if kind == 'clean_artifact_review':
            require(review.get('final_cleaned_candidate_review_completed') is True
                    and review.get('candidate_artifact_approved') is True
                    and review.get('cleaned_assets') == facts['new_assets'], 'Approval must cover the exact five cleaned assets')
        else:
            require(review.get('private_draft_replacement_logically_authorized') is True,
                    'The exact private-draft replacement route requires completed review')
    source = original.load_pins(root / facts['files']['stage_pins']['path'])
    require(source['release_id'] == RELEASE_ID and source['commit'] == facts['source_commit']
            and source['run_id'] == facts['source_run'] and source['release_notes_sha256'] == facts['old_notes_sha256']
            and source['assets'] == {name: {key: proof[key] for key in ('bytes', 'sha256')}
                                     for name, proof in facts['old_assets'].items()}, 'Original CI pins must remain unchanged')
    return source


def asset_matches(item, proof):
    return (item.get('state') == 'uploaded' and item.get('size') == proof['bytes']
            and item.get('digest') == 'sha256:' + proof['sha256'])


def verify_release(release, facts):
    require(release.get('id') == RELEASE_ID and release.get('draft') is True
            and release.get('prerelease') is True and release.get('tag_name') == 'v1.10.0'
            and release.get('target_commitish') == facts['source_commit'] and release.get('published_at') is None,
            'Only the existing invisible prerelease draft is authorized')
    assets = release.get('assets', [])
    require(isinstance(assets, list), 'Remote asset list required')
    by_name = {item.get('name'): item for item in assets}
    ids = [item.get('id') for item in assets]
    require(len(by_name) == len(assets) and len(set(ids)) == len(ids)
            and all(original.positive(value) for value in ids)
            and set(by_name) <= original.ASSET_NAMES, 'Unknown or duplicate remote asset identity')
    state = []
    for name in sorted(original.ASSET_NAMES - set(TARGETS)):
        item = by_name.get(name, {})
        proof = facts['old_assets'][name]
        require(item.get('id') == proof['id'] and asset_matches(item, proof), 'Preserved server asset changed: ' + name)
    for name in TARGETS:
        item = by_name.get(name)
        if item is None:
            state.append('absent')
        elif item.get('id') == facts['old_assets'][name]['id']:
            require(asset_matches(item, facts['old_assets'][name]), 'Original target ID has altered bytes')
            state.append('old')
        else:
            require(item.get('id') not in OLD_IDS.values() and asset_matches(item, facts['new_assets'][name]),
                    'Unrecognized, incomplete or altered replacement asset')
            state.append('new')
    state = tuple(state)
    require(state in PHASES, 'Invalid replacement phase ordering')
    body = release.get('body')
    require(isinstance(body, str), 'Release body must be text')
    body_hash = text_digest(body)
    require(body_hash in (facts['old_notes_sha256'], facts['new_notes_sha256']), 'Release notes changed unexpectedly')
    new_notes = body_hash == facts['new_notes_sha256']
    require(not new_notes or state == ('new', 'new'), 'New notes require both verified replacement assets')
    return {'phase': PHASES.index(state), 'state': state, 'new_notes': new_notes, 'assets': by_name}


def fresh_state(facts):
    repo = original.read('')
    require(repo.get('id') == original.REPOSITORY_ID and repo.get('full_name') == original.REPOSITORY
            and repo.get('private') is False and repo.get('visibility') == 'public', 'Selected repository changed')
    original.verify_tag()
    return verify_release(original.read(f'releases/{RELEASE_ID}'), facts)


def verify_local(archives, evidence, old_assets, new_assets, provenance, cleaned_facts, facts, *, root=ROOT):
    source = verify_pinned_files(facts, root)
    original.verify_downloaded_trees(archives, evidence, old_assets, source)
    original.verify_local(evidence, old_assets, source, root=root)
    for path, key in ((provenance, 'provenance'), (cleaned_facts, 'cleaned_facts')):
        original.regular_file(path)
        require(original.digest(path) == facts['files'][key]['sha256'], 'Reproduced cleanup proof differs from reviewed bytes')
    proof = json.loads(provenance.read_text(encoding='utf-8'))
    cleaned = json.loads(cleaned_facts.read_text(encoding='utf-8'))
    require(proof.get('passed') is True and cleaned.get('passed') is True
            and proof.get('original_assets') == source['assets'] and proof.get('cleaned_assets') == facts['new_assets']
            and cleaned.get('original_assets') == source['assets'] and cleaned.get('assets') == facts['new_assets']
            and cleaned.get('provenance_sha256') == facts['files']['provenance']['sha256'], 'Approved log-only cleanup proof required')
    transform = proof.get('archive_transform', {})
    require(proof.get('schema') == 1 and cleaned.get('schema') == 1
            and proof.get('operation') == 'remove_exact_ci_fixture_log_only'
            and proof.get('source_revision') == facts['source_commit']
            and cleaned.get('source_revision') == facts['source_commit']
            and proof.get('original_ci_run') == facts['source_run'] and cleaned.get('original_ci_run') == facts['source_run']
            and proof.get('removed_member') == REMOVED and cleaned.get('removed_member') == REMOVED
            and transform.get('removed_member') == REMOVED and transform.get('original_members') == 3728
            and transform.get('cleaned_members') == transform.get('retained_members') == 3727
            and len(transform.get('retained_inventory', {})) == 3727
            and transform.get('allowed_position_changes') == ['header_offset', 'derived _end_offset']
            and all(transform.get(key) is True for key in ('all_retained_metadata_identical',
                    'all_retained_uncompressed_bytes_identical', 'all_retained_raw_local_records_identical',
                    'all_retained_normalized_central_records_identical', 'all_retained_crc_validated')),
            'Exact one-log removal and all retained ZIP member proofs required')
    execution = proof.get('execution', {})
    require(execution.get('offline_packaging_only') is True
            and execution.get('original_ci_program_files_are_identical') is True
            and all(execution.get(key) is False for key in ('product_rebuilt', 'product_files_changed', 'runtime_reexecuted',
                                                          'excel_com_executed', 'hardware_executed')),
            'Repack must preserve product bytes and cannot claim new runtime validation')
    require(new_assets.is_dir() and not new_assets.is_symlink()
            and {path.name for path in new_assets.iterdir()} == original.ASSET_NAMES, 'Exactly five cleaned local assets required')
    for name, expected in facts['new_assets'].items():
        path = new_assets / name
        original.regular_file(path)
        require(path.stat().st_size == expected['bytes'] and original.digest(path) == expected['sha256'],
                'Cleaned candidate bytes changed: ' + name)
    sums = ''.join(f'{facts["new_assets"][name]["sha256"]}  {name}\n'
                   for name in sorted(original.ASSET_NAMES - {SUMS}))
    require((new_assets / SUMS).read_bytes() == sums.encode('utf-8'), 'Cleaned checksum text must match all four payloads')


def prepare(archives, evidence, old_assets, new_assets, provenance, cleaned_facts, facts, *, root=ROOT):
    original.require_actions()
    source = verify_pinned_files(facts, root)
    verify_release(original.verify_remote(source), facts)
    require(all(not path.exists() for path in (archives, evidence, old_assets, new_assets, provenance, cleaned_facts)),
            'Use fresh reproduction directories and proof files')
    archives.mkdir(parents=True)
    for artifact in source['artifacts'].values():
        path = archives / (str(artifact['id']) + '.zip')
        original.download_archive(artifact['id'], path)
        original.verify_archive(path, artifact)
    for kind, destination in (('evidence', evidence), ('release', old_assets)):
        destination.mkdir(parents=True)
        with zipfile.ZipFile(archives / (str(source['artifacts'][kind]['id']) + '.zip')) as archive:
            for item in original.archive_members(archive):
                target = destination / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    original.verify_downloaded_trees(archives, evidence, old_assets, source)
    original.verify_local(evidence, old_assets, source, root=root)
    subprocess.run([sys.executable, root / facts['files']['cleanup_code']['path'], '--assets', old_assets,
                    '--output-assets', new_assets, '--provenance', provenance, '--facts', cleaned_facts], check=True)
    verify_local(archives, evidence, old_assets, new_assets, provenance, cleaned_facts, facts, root=root)


def replace(facts, new_assets, report, *, archives, evidence, old_assets, provenance, cleaned_facts, root=ROOT):
    original.require_actions()
    verify_local(archives, evidence, old_assets, new_assets, provenance, cleaned_facts, facts, root=root)
    journal = {'passed': False, 'release_id': RELEASE_ID, 'actions': [], 'public_published': False,
               'program_bytes_changed': False, 'equipment_application': 'HOLD', 'final_control_algorithm_approval': 'HOLD'}
    def save():
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    def attempt(label, operation):
        # Log the attempted operation before its request. Never repeat it here.
        journal['actions'].append(label); save()
        operation()
    try:
        state = verify_release(original.verify_remote(original.load_pins()), facts)
        for name in TARGETS:
            state = fresh_state(facts)
            item = state['assets'].get(name)
            if item and item['id'] == facts['old_assets'][name]['id']:
                old_id = item['id']
                attempt({'operation': 'delete_exact_old_asset', 'name': name, 'asset_id': old_id},
                        lambda: original.gh('api', '--method', 'DELETE',
                                            f'repos/{original.REPOSITORY}/releases/assets/{old_id}'))
                state = fresh_state(facts)
                require(name not in state['assets'], 'Deleted target still present; stop without retry')
            state = fresh_state(facts)
            if name not in state['assets']:
                path = new_assets / name
                original.regular_file(path)
                require(path.stat().st_size == facts['new_assets'][name]['bytes']
                        and original.digest(path) == facts['new_assets'][name]['sha256'], 'Replacement bytes changed before POST')
                endpoint = (f'https://uploads.github.com/repos/{original.REPOSITORY}/releases/{RELEASE_ID}/assets'
                            f'?name={quote(name, safe="")}')
                attempt({'operation': 'upload_exact_new_asset', 'name': name, 'release_id': RELEASE_ID},
                        lambda: original.gh('api', '--method', 'POST', endpoint, '--header',
                                            'Content-Type: application/octet-stream', '--input', path))
                state = fresh_state(facts)
                require(name in state['assets'] and asset_matches(state['assets'][name], facts['new_assets'][name]),
                        'New asset not confirmed on server; stop without retry')
        state = fresh_state(facts)
        require(state['state'] == ('new', 'new'), 'Both replacement server digests required before notes')
        if not state['new_notes']:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json') as payload:
                json.dump({'body': note_body(facts)}, payload, ensure_ascii=False); payload.flush()
                attempt({'operation': 'patch_exact_notes_body', 'release_id': RELEASE_ID},
                        lambda: original.gh('api', '--method', 'PATCH', f'repos/{original.REPOSITORY}/releases/{RELEASE_ID}',
                                            '--input', payload.name))
        state = fresh_state(facts)
        require(state['state'] == ('new', 'new') and state['new_notes'] and len(state['assets']) == 5,
                'Final private draft assets/notes readback failed')
        journal.update(passed=True, release_remains_draft=True, prerelease=True,
                       server_digest_readback_passed=True, preserved_three_asset_ids=True,
                       original_ci_assets=facts['old_assets'], replacement_assets=facts['new_assets'],
                       final_asset_ids={name: item['id'] for name, item in state['assets'].items()},
                       old_notes_sha256=facts['old_notes_sha256'], new_notes_sha256=facts['new_notes_sha256'],
                       source_commit=facts['source_commit'], source_run=facts['source_run'],
                       original_ci_zip_sha256=facts['old_assets'][EXE]['sha256'],
                       cleaned_zip_sha256=facts['new_assets'][EXE]['sha256'], removed_member=REMOVED,
                       cleanup_code_sha256=facts['files']['cleanup_code']['sha256'],
                       provenance_sha256=facts['files']['provenance']['sha256'],
                       replacement_commit=os.environ.get('GITHUB_SHA'), replacement_run=os.environ.get('GITHUB_RUN_ID'),
                       repository_visibility='public', visibility_changed=False, runtime_reexecuted=False,
                       excel_com_verified=False, equipment_verified=False)
        save()
        return journal
    except Exception:
        journal['stopped_without_automatic_retry'] = True
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archives', 'evidence', 'old-assets', 'new-assets', 'provenance', 'cleaned-facts', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    facts = load_pins()
    inputs = (args.archives, args.evidence, args.old_assets, args.new_assets, args.provenance, args.cleaned_facts, facts)
    if args.prepare:
        prepare(*inputs)
    if args.replace:
        replace(facts, args.new_assets, args.report, archives=args.archives, evidence=args.evidence,
                old_assets=args.old_assets, provenance=args.provenance, cleaned_facts=args.cleaned_facts)
    else:
        verify_local(*inputs)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'passed': True, 'replaced': False, 'public_published': False}) + '\n')


if __name__ == '__main__':
    main()
