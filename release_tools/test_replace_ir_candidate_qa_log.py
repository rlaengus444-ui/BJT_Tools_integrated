"""Offline proof of narrowly pinned, resumable private-draft replacements."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import replace_ir_candidate_qa_log as replacement


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.new_assets = self.root / 'new'; self.new_assets.mkdir()
        self.report = self.root / 'report.json'
        self.inputs = {name: self.root / name for name in
                       ('archives', 'evidence', 'old_assets', 'provenance', 'cleaned_facts')}
        self.facts = dict(repository=replacement.original.REPOSITORY,
            repository_id=replacement.original.REPOSITORY_ID, release_id=replacement.RELEASE_ID,
            source_commit=replacement.original.COMMIT, source_run=replacement.original.RUN_ID,
            old_assets={}, new_assets={}, equipment_application='HOLD', final_control_algorithm_approval='HOLD')
        for name in replacement.original.ASSET_NAMES:
            old = ('original ' + name).encode()
            new = ('cleaned ' + name).encode() if name in replacement.TARGETS else old
            self.facts['old_assets'][name] = dict(id=replacement.OLD_IDS[name], bytes=len(old),
                                                sha256=hashlib.sha256(old).hexdigest())
            (self.new_assets / name).write_bytes(new)
            self.facts['new_assets'][name] = dict(bytes=len(new), sha256=hashlib.sha256(new).hexdigest())
        self.facts['old_notes_body'] = ('Original evaluated candidate\nEXE ZIP SHA-256: '
                                       + self.facts['old_assets'][replacement.EXE]['sha256'] + '\n')
        self.facts['old_notes_sha256'] = replacement.text_digest(self.facts['old_notes_body'])
        self.facts['new_notes_sha256'] = replacement.text_digest(replacement.note_body(self.facts))
        paths = dict(stage_code='release_tools/stage_verified_ir_110.py',
            stage_pins='release_tools/stage_verified_ir_110.json', cleanup_code='release_tools/clean_ir_candidate.py',
            provenance='verification/provenance.json', cleaned_facts='verification/cleaned-facts.json',
            clean_artifact_review='verification/clean-artifact-review.json',
            replacement_review='verification/replacement-review.json')
        self.facts['files'] = {key: dict(path=path, sha256='a' * 64) for key, path in paths.items()}
        self.source = dict(commit=replacement.original.COMMIT, run_id=replacement.original.RUN_ID)
        self.repo = dict(id=replacement.original.REPOSITORY_ID, full_name=replacement.original.REPOSITORY,
                         private=False, visibility='public')
        self.release = dict(id=replacement.RELEASE_ID, draft=True, prerelease=True, tag_name='v1.10.0',
            target_commitish=replacement.original.COMMIT, published_at=None,
            body=self.facts['old_notes_body'], assets=[])
        self.operations = []
        self.local_passed = False
        self.authorized = True
        self.tag_correct = True
        self.before_read = None
        self.release_reads = 0
        self.set_phase(('old', 'old'))
        self.initial_release = copy.deepcopy(self.release)
        self.addCleanup(patch.stopall)
        self.local = patch.object(replacement, 'verify_local', side_effect=self.local_proof).start()
        self.remote = patch.object(replacement.original, 'verify_remote', side_effect=self.source_remote).start()
        self.load_source = patch.object(replacement.original, 'load_pins', side_effect=lambda *args: copy.deepcopy(self.source)).start()
        patch.object(replacement.original, 'read', side_effect=self.read).start()
        self.tag = patch.object(replacement.original, 'verify_tag', side_effect=self.check_tag).start()
        self.auth = patch.object(replacement.original, 'require_actions', side_effect=self.check_auth).start()
        self.gh = patch.object(replacement.original, 'gh', side_effect=self.api).start()

    def local_proof(self, *args, **kwargs):
        self.local_passed = True

    def source_remote(self, source):
        if (source.get('commit') != replacement.original.COMMIT
                or source.get('run_id') != replacement.original.RUN_ID):
            raise ValueError('Exact original source CI provenance required')
        return copy.deepcopy(self.release)

    def check_tag(self):
        if not self.tag_correct:
            raise ValueError('Precreated tag changed')

    def check_auth(self):
        if not self.authorized:
            raise ValueError('Wrong Actions authorization')

    def read(self, endpoint):
        if self.before_read is not None:
            self.before_read(endpoint)
        if endpoint == '':
            return copy.deepcopy(self.repo)
        self.assertEqual(endpoint, f'releases/{replacement.RELEASE_ID}')
        self.release_reads += 1
        return copy.deepcopy(self.release)

    def asset(self, name, *, new=False):
        proof = self.facts['new_assets' if new else 'old_assets'][name]
        asset_id = (900000001 + replacement.TARGETS.index(name)) if new else proof['id']
        return dict(id=asset_id, name=name, state='uploaded', size=proof['bytes'], digest='sha256:' + proof['sha256'])

    def set_phase(self, state, *, new_notes=False):
        self.release['assets'] = [self.asset(name) for name in sorted(
            replacement.original.ASSET_NAMES - set(replacement.TARGETS))]
        for name, value in zip(replacement.TARGETS, state):
            if value != 'absent':
                self.release['assets'].append(self.asset(name, new=value == 'new'))
        self.release['body'] = replacement.note_body(self.facts) if new_notes else self.facts['old_notes_body']

    def api(self, *args):
        self.assertTrue(self.local_passed, 'All local proof must complete before any write')
        self.assertEqual(args[:2], ('api', '--method'))
        method, endpoint = args[2:4]
        if method == 'DELETE':
            self.assertEqual(len(args), 4)
            old_id = int(endpoint.rsplit('/', 1)[1])
            self.assertIn(old_id, [replacement.OLD_IDS[name] for name in replacement.TARGETS])
            self.assertEqual(endpoint, f'repos/{replacement.original.REPOSITORY}/releases/assets/{old_id}')
            records = [asset for asset in self.release['assets'] if asset['id'] == old_id]
            self.assertEqual(len(records), 1)
            name = records[0]['name']
            self.assertTrue(replacement.asset_matches(records[0], self.facts['old_assets'][name]))
            self.release['assets'].remove(records[0])
            self.operations.append(('DELETE', name, old_id))
        elif method == 'POST':
            self.assertEqual(args[4:7], ('--header', 'Content-Type: application/octet-stream', '--input'))
            self.assertEqual(len(args), 8)
            url = urlsplit(endpoint)
            self.assertEqual((url.scheme, url.netloc), ('https', 'uploads.github.com'))
            self.assertEqual(url.path, f'/repos/{replacement.original.REPOSITORY}/releases/{replacement.RELEASE_ID}/assets')
            name = Path(args[7]).name
            self.assertIn(name, replacement.TARGETS)
            self.assertEqual(parse_qs(url.query), {'name': [name]})
            self.assertNotIn(name, [asset['name'] for asset in self.release['assets']])
            path = Path(args[7]); proof = self.facts['new_assets'][name]
            self.assertEqual(path.stat().st_size, proof['bytes'])
            self.assertEqual(replacement.original.digest(path), proof['sha256'])
            self.release['assets'].append(self.asset(name, new=True))
            self.operations.append(('POST', name, replacement.RELEASE_ID))
        elif method == 'PATCH':
            self.assertEqual(args[4], '--input')
            self.assertEqual(len(args), 6)
            self.assertEqual(endpoint, f'repos/{replacement.original.REPOSITORY}/releases/{replacement.RELEASE_ID}')
            self.assertEqual(replacement.verify_release(self.release, self.facts)['state'], ('new', 'new'))
            payload = json.loads(Path(args[5]).read_text())
            self.assertEqual(payload, {'body': replacement.note_body(self.facts)})
            self.release['body'] = payload['body']
            self.operations.append(('PATCH', 'body', replacement.RELEASE_ID))
        else:
            self.fail('Unexpected write method: ' + method)
        return ''

    def run_replace(self):
        return replacement.replace(self.facts, self.new_assets, self.report, **self.inputs, root=self.root)

    def test_success_writes_exact_two_old_ids_new_payloads_and_body_only(self):
        report = self.run_replace()
        self.assertEqual(self.operations, [
            ('DELETE', replacement.EXE, replacement.OLD_IDS[replacement.EXE]),
            ('POST', replacement.EXE, replacement.RELEASE_ID),
            ('DELETE', replacement.SUMS, replacement.OLD_IDS[replacement.SUMS]),
            ('POST', replacement.SUMS, replacement.RELEASE_ID), ('PATCH', 'body', replacement.RELEASE_ID)])
        self.assertTrue(report['passed'] and report['release_remains_draft'] and report['server_digest_readback_passed'])
        self.assertFalse(report['public_published'] or report['program_bytes_changed'])
        self.assertEqual(report['equipment_application'], 'HOLD')
        self.assertEqual(report['final_control_algorithm_approval'], 'HOLD')
        self.assertEqual(json.loads(self.report.read_text()), report)
        self.local.assert_called_once()
        self.remote.assert_called_once_with(self.source)
        self.assert_preserved()

    def assert_preserved(self):
        by_name = {asset['name']: asset for asset in self.release['assets']}
        for name in replacement.original.ASSET_NAMES - set(replacement.TARGETS):
            self.assertEqual(by_name[name], self.asset(name))

    def test_every_allowed_resume_phase_preserves_the_other_three_ids(self):
        expected = [5, 4, 3, 2, 1]
        for phase, state in enumerate(replacement.PHASES):
            with self.subTest(state=state):
                self.set_phase(state); self.operations.clear()
                result = self.run_replace()
                self.assertTrue(result['passed'])
                self.assertEqual(len(self.operations), expected[phase])
                self.assert_preserved()
                for method, _, identity in self.operations:
                    if method == 'DELETE':
                        self.assertIn(identity, [replacement.OLD_IDS[name] for name in replacement.TARGETS])

    def test_completed_new_assets_and_notes_are_idempotent_without_writes(self):
        self.set_phase(('new', 'new'), new_notes=True)
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual(self.operations, [])
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual(self.operations, [])

    def test_invalid_phase_ordering_and_new_notes_before_assets_fail(self):
        for state in [('old', 'absent'), ('old', 'new'), ('absent', 'absent'), ('absent', 'new')]:
            with self.subTest(state=state):
                self.set_phase(state)
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])
        for state in replacement.PHASES[:-1]:
            with self.subTest(new_notes_with=state):
                self.set_phase(state, new_notes=True)
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])

    def test_wrong_release_identity_metadata_or_notes_cannot_mutate(self):
        for key, bad in [('id', 1), ('draft', False), ('prerelease', False), ('tag_name', 'v9'),
                         ('target_commitish', '0' * 40), ('published_at', '2026-11-17T00:00:00Z'),
                         ('body', 'unexpected notes')]:
            with self.subTest(key=key):
                self.release = copy.deepcopy(self.initial_release); self.release[key] = bad
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])

    def test_wrong_unknown_duplicate_or_missing_asset_never_mutates(self):
        cases = ('unknown', 'duplicate_name', 'duplicate_id', 'missing_preserved', 'preserved_id',
                 'preserved_digest', 'preserved_size', 'old_id_digest', 'old_id_size', 'starter_old')
        for defect in cases:
            with self.subTest(defect=defect):
                self.release = copy.deepcopy(self.initial_release)
                assets = self.release['assets']
                old = next(asset for asset in assets if asset['name'] == replacement.EXE)
                kept = next(asset for asset in assets if asset['name'] not in replacement.TARGETS)
                if defect == 'unknown': assets.append(dict(id=999, name='unknown.exe'))
                elif defect == 'duplicate_name': assets.append(dict(old, id=999))
                elif defect == 'duplicate_id': kept['id'] = old['id']
                elif defect == 'missing_preserved': assets.remove(kept)
                elif defect == 'preserved_id': kept['id'] = 999
                elif defect == 'preserved_digest': kept['digest'] = 'sha256:' + '0' * 64
                elif defect == 'preserved_size': kept['size'] += 1
                elif defect == 'old_id_digest': old['digest'] = 'sha256:' + '0' * 64
                elif defect == 'old_id_size': old['size'] += 1
                else: old['state'] = 'starter'
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])

    def test_unrecognized_new_asset_identity_bytes_and_state_fail_closed(self):
        for key, value in [('id', replacement.OLD_IDS[replacement.SUMS]), ('id', 0),
                           ('digest', 'sha256:' + '0' * 64), ('size', 1), ('state', 'starter')]:
            with self.subTest(key=key, value=value):
                self.set_phase(('new', 'old'))
                next(asset for asset in self.release['assets'] if asset['name'] == replacement.EXE)[key] = value
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])

    def test_repository_visibility_identity_tag_auth_and_source_guards(self):
        for key, bad in [('id', 1), ('full_name', 'other/repo'), ('private', True), ('visibility', 'private')]:
            with self.subTest(repo=key):
                previous = self.repo[key]; self.repo[key] = bad
                with self.assertRaises(ValueError): self.run_replace()
                self.repo[key] = previous
                self.assertEqual(self.operations, [])
        self.tag_correct = False
        with self.assertRaises(ValueError): self.run_replace()
        self.tag_correct = True; self.authorized = False
        with self.assertRaises(ValueError): self.run_replace()
        self.authorized = True
        for key, bad in [('commit', '0' * 40), ('run_id', 1)]:
            with self.subTest(source=key):
                previous = self.source[key]; self.source[key] = bad
                with self.assertRaises(ValueError): self.run_replace()
                self.source[key] = previous
        self.assertEqual(self.operations, [])

    def test_local_proof_failure_happens_before_any_remote_write(self):
        self.local.side_effect = ValueError('Cleanup proof missing')
        with self.assertRaisesRegex(ValueError, 'Cleanup proof missing'): self.run_replace()
        self.assertEqual(self.operations, [])
        self.remote.assert_not_called()

    def test_observed_draft_or_notes_race_before_first_delete_stops(self):
        for key, value in [('draft', False), ('prerelease', False), ('body', 'changed notes'),
                           ('target_commitish', '0' * 40)]:
            with self.subTest(key=key):
                self.release = copy.deepcopy(self.initial_release)
                def race(endpoint):
                    if endpoint == f'releases/{replacement.RELEASE_ID}': self.release[key] = value
                self.before_read = race
                with self.assertRaises(ValueError): self.run_replace()
                self.assertEqual(self.operations, [])
        self.before_read = None

    def test_publication_detected_after_first_delete_stops_before_post(self):
        def race(endpoint):
            if endpoint == f'releases/{replacement.RELEASE_ID}' and self.operations:
                self.release['draft'] = False
        self.before_read = race
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])

    def test_notes_race_after_replacements_blocks_patch(self):
        def race(endpoint):
            names = {asset['name']: asset for asset in self.release['assets']}
            if (endpoint == f'releases/{replacement.RELEASE_ID}' and all(name in names and names[name]['id']
                    != replacement.OLD_IDS[name] for name in replacement.TARGETS)):
                self.release['body'] = 'unexpected concurrent edit'
        self.before_read = race
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE', 'POST', 'DELETE', 'POST'])

    def test_tag_retarget_at_post_cannot_redirect_pinned_release_endpoint(self):
        def retag(*args):
            result = self.api(*args)
            if args[2] == 'POST': self.tag_correct = False
            return result
        self.gh.side_effect = retag
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE', 'POST'])
        self.assertEqual(self.operations[-1][-1], replacement.RELEASE_ID)

    def test_ambiguous_post_without_commit_stops_once_then_fresh_run_resumes_absent(self):
        post_attempts = []
        def lost_post(*args):
            if args[2] == 'POST':
                post_attempts.append(args)
                raise subprocess.CalledProcessError(1, ['gh', 'api'])
            return self.api(*args)
        self.gh.side_effect = lost_post
        with self.assertRaises(subprocess.CalledProcessError): self.run_replace()
        self.assertEqual(len(post_attempts), 1)
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])
        journal = json.loads(self.report.read_text())
        self.assertTrue(journal['stopped_without_automatic_retry'])
        self.assertEqual([action['operation'] for action in journal['actions']],
                         ['delete_exact_old_asset', 'upload_exact_new_asset'])
        self.gh.side_effect = self.api
        self.operations.clear()
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual([op[0] for op in self.operations], ['POST', 'DELETE', 'POST', 'PATCH'])

    def test_ambiguous_post_after_commit_stops_then_fresh_run_skips_matching_new(self):
        post_attempts = []
        def lost_response(*args):
            result = self.api(*args)
            if args[2] == 'POST':
                post_attempts.append(args)
                raise subprocess.CalledProcessError(1, ['gh', 'api'])
            return result
        self.gh.side_effect = lost_response
        with self.assertRaises(subprocess.CalledProcessError): self.run_replace()
        self.assertEqual(len(post_attempts), 1)
        self.assertEqual([op[0] for op in self.operations], ['DELETE', 'POST'])
        self.gh.side_effect = self.api
        self.operations.clear()
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual(self.operations, [('DELETE', replacement.SUMS, replacement.OLD_IDS[replacement.SUMS]),
            ('POST', replacement.SUMS, replacement.RELEASE_ID), ('PATCH', 'body', replacement.RELEASE_ID)])

    def test_partial_uploaded_asset_stops_and_fresh_run_never_deletes_it(self):
        def partial(*args):
            result = self.api(*args)
            if args[2] == 'POST':
                self.release['assets'][-1].update(state='starter', digest=None)
            return result
        self.gh.side_effect = partial
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE', 'POST'])
        self.gh.side_effect = self.api; self.operations.clear()
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual(self.operations, [])

    def test_wrong_post_digest_readback_stops_before_other_asset_deletion(self):
        def corrupt(*args):
            result = self.api(*args)
            if args[2] == 'POST': self.release['assets'][-1]['digest'] = 'sha256:' + '0' * 64
            return result
        self.gh.side_effect = corrupt
        with self.assertRaises(ValueError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE', 'POST'])

    def test_post_returns_success_but_missing_asset_stops_without_retry(self):
        post_attempts = []
        def missing(*args):
            if args[2] == 'POST':
                post_attempts.append(args)
                return ''
            return self.api(*args)
        self.gh.side_effect = missing
        with self.assertRaisesRegex(ValueError, 'New asset not confirmed on server'): self.run_replace()
        self.assertEqual(len(post_attempts), 1)
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])

    def test_preserved_asset_id_race_stops_before_any_further_write(self):
        def race(endpoint):
            if endpoint == f'releases/{replacement.RELEASE_ID}' and self.operations:
                kept = next(asset for asset in self.release['assets'] if asset['name'] not in replacement.TARGETS)
                kept['id'] = 999
        self.before_read = race
        with self.assertRaisesRegex(ValueError, 'Preserved server asset changed'): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])

    def test_failed_delete_does_not_retry_or_upload(self):
        attempts = []
        def fail(*args):
            attempts.append(args)
            raise subprocess.CalledProcessError(1, ['gh', 'api'])
        self.gh.side_effect = fail
        with self.assertRaises(subprocess.CalledProcessError): self.run_replace()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.operations, [])

    def test_delete_committed_but_response_lost_resumes_from_absent(self):
        def lost(*args):
            self.api(*args)
            raise subprocess.CalledProcessError(1, ['gh', 'api'])
        self.gh.side_effect = lost
        with self.assertRaises(subprocess.CalledProcessError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])
        self.gh.side_effect = self.api; self.operations.clear()
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual([op[0] for op in self.operations], ['POST', 'DELETE', 'POST', 'PATCH'])

    def test_delete_not_effective_stops_without_repeating(self):
        self.gh.side_effect = lambda *args: ''
        with self.assertRaisesRegex(ValueError, 'Deleted target still present'): self.run_replace()
        self.assertEqual(self.gh.call_count, 1)
        self.assertEqual(self.operations, [])

    def test_changed_local_bytes_after_proof_never_post(self):
        def changed(*args, **kwargs):
            self.local_passed = True
            (self.new_assets / replacement.EXE).write_bytes(b'changed after proof')
        self.local.side_effect = changed
        with self.assertRaisesRegex(ValueError, 'Replacement bytes changed'): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['DELETE'])

    def test_ambiguous_notes_patch_committed_is_zero_write_on_fresh_run(self):
        self.set_phase(('new', 'new'))
        def lost(*args):
            self.api(*args)
            raise subprocess.CalledProcessError(1, ['gh', 'api'])
        self.gh.side_effect = lost
        with self.assertRaises(subprocess.CalledProcessError): self.run_replace()
        self.assertEqual([op[0] for op in self.operations], ['PATCH'])
        self.gh.side_effect = self.api; self.operations.clear()
        self.assertTrue(self.run_replace()['passed'])
        self.assertEqual(self.operations, [])

    def test_notes_patch_not_effective_fails_final_readback(self):
        self.set_phase(('new', 'new'))
        self.gh.side_effect = lambda *args: ''
        with self.assertRaisesRegex(ValueError, 'Final private draft assets/notes readback failed'): self.run_replace()
        self.assertEqual(self.gh.call_count, 1)

    def test_load_pins_accepts_complete_facts_and_fails_explicit_unknown(self):
        path = self.root / 'pins.json'; path.write_text(json.dumps(self.facts))
        self.assertEqual(replacement.load_pins(path), self.facts)
        incomplete = copy.deepcopy(self.facts)
        incomplete['new_assets'][replacement.EXE]['sha256'] = None
        path.write_text(json.dumps(incomplete))
        with self.assertRaises(ValueError): replacement.load_pins(path)

    def test_load_pins_rejects_wrong_source_assets_notes_proof_paths_and_holds(self):
        path = self.root / 'pins.json'
        mutations = [
            lambda f: f.update(repository='other/repo'), lambda f: f.update(repository_id=1),
            lambda f: f.update(release_id=1), lambda f: f.update(source_commit='0' * 40),
            lambda f: f.update(source_run=1), lambda f: f.update(equipment_application='APPROVED'),
            lambda f: f.update(final_control_algorithm_approval='APPROVED'),
            lambda f: f['old_assets'][replacement.EXE].update(id=1),
            lambda f: f['new_assets'][replacement.EXE].update(sha256=f['old_assets'][replacement.EXE]['sha256']),
            lambda f: f['new_assets']['BJT_1.10.0_manual.zip'].update(sha256='b' * 64),
            lambda f: f.update(old_notes_sha256='b' * 64), lambda f: f.update(new_notes_sha256='b' * 64),
            lambda f: f['files']['stage_code'].update(path='release_tools/other.py'),
            lambda f: f['files']['replacement_review'].update(sha256=None),
            lambda f: f['files'].pop('clean_artifact_review'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                facts = copy.deepcopy(self.facts); mutate(facts)
                path.write_text(json.dumps(facts))
                with self.assertRaises(ValueError): replacement.load_pins(path)
        self.assertEqual(self.operations, [])

    def test_review_bytes_must_also_approve_exact_cleaned_assets_and_private_route(self):
        invocation = dict(requested_model='gpt-6.1-sol', requested_reasoning_effort='max',
                          provider_model_identity_independently_attested=False)
        clean = dict(review_completed=True, unresolved_findings=[], invocation=invocation,
                     final_cleaned_candidate_review_completed=True, candidate_artifact_approved=True,
                     cleaned_assets=self.facts['new_assets'])
        route = dict(review_completed=True, unresolved_findings=[], invocation=invocation,
                     private_draft_replacement_logically_authorized=True)
        def write_record(key, value):
            descriptor = self.facts['files'][key]
            path = self.root / descriptor['path']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            descriptor['sha256'] = replacement.original.digest(path)
        for key in self.facts['files']:
            write_record(key, {'pinned': key})
        write_record('clean_artifact_review', clean); write_record('replacement_review', route)
        self.source.update(release_id=replacement.RELEASE_ID, release_notes_sha256=self.facts['old_notes_sha256'],
            assets={name: {key: proof[key] for key in ('bytes', 'sha256')} for name, proof in self.facts['old_assets'].items()})
        self.assertEqual(replacement.verify_pinned_files(self.facts, self.root), self.source)
        defects = [('clean_artifact_review', 'review_completed', False),
                   ('clean_artifact_review', 'unresolved_findings', ['blocker']),
                   ('clean_artifact_review', 'candidate_artifact_approved', False),
                   ('clean_artifact_review', 'final_cleaned_candidate_review_completed', False),
                   ('clean_artifact_review', 'cleaned_assets', {}),
                   ('replacement_review', 'private_draft_replacement_logically_authorized', False)]
        for kind, field, value in defects:
            with self.subTest(kind=kind, field=field):
                baseline = clean if kind == 'clean_artifact_review' else route
                record = copy.deepcopy(baseline); record[field] = value
                write_record(kind, record)
                with self.assertRaises(ValueError): replacement.verify_pinned_files(self.facts, self.root)
                write_record(kind, baseline)
        record = copy.deepcopy(clean); record['invocation']['requested_reasoning_effort'] = 'medium'
        write_record('clean_artifact_review', record)
        with self.assertRaises(ValueError): replacement.verify_pinned_files(self.facts, self.root)
        self.assertEqual(self.operations, [])


if __name__ == '__main__':
    unittest.main()
