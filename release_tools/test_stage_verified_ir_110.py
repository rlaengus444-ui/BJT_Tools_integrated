"""Offline candidate-transfer preflights and server readbacks; no network calls."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import stage_verified_ir_110 as stage


class CandidateTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'evidence'; self.evidence.mkdir()
        self.assets = self.root / 'assets'; self.assets.mkdir()
        self.archives = self.root / 'archives'; self.archives.mkdir()
        self.facts = json.loads(stage.PINS.read_text())
        self.facts.update(run_attempt=1, release_id=987, release_notes_sha256=hashlib.sha256(b'Candidate only').hexdigest())
        for kind, proof in self.facts['artifacts'].items():
            proof.update(id=100 if kind == 'evidence' else 200, bytes=400, digest='sha256:' + 'a' * 64)
        for name in stage.ASSET_NAMES - {'SHA256SUMS.txt'}:
            (self.assets / name).write_bytes(('candidate ' + name).encode())
        text = ''.join(f'{stage.digest(self.assets / name)}  {name}\n' for name in sorted(stage.ASSET_NAMES - {'SHA256SUMS.txt'}))
        (self.assets / 'SHA256SUMS.txt').write_text(text)
        self.facts['assets'] = {p.name: {'bytes': p.stat().st_size, 'sha256': stage.digest(p)} for p in self.assets.iterdir()}
        source = ET.Element('testsuite')
        skips = []
        for (cls, name), reason in stage.SOURCE_SKIP_PAIRS.items():
            case = ET.SubElement(source, 'testcase', classname=cls, name=name)
            raw = 'unittest.case.SkipTest: ' + reason
            ET.SubElement(case, 'skipped', message=raw)
            skips.append({'test': name, 'reason': raw})
        for index in range(497): ET.SubElement(source, 'testcase', classname='source', name=f'test_{index}')
        ET.ElementTree(source).write(self.evidence / 'source-tests.xml')
        capsule = ET.Element('testsuite')
        for index in range(8): ET.SubElement(capsule, 'testcase', classname='capsule', name=f'test_{index}')
        ET.ElementTree(capsule).write(self.evidence / 'source-capsule-tests.xml')
        self.upstream = dict(passed=True, tests=572, expected_tests=572, failures=0, errors=0, skips=0,
            upstream_test_files=58, source_revision='2372c29602a9538f78593699c5e34c0485373524',
            upstream_snapshots_byte_verified=True, real_equipment_tested=False, excel_com_tested=False,
            skipped=[], failed=[], errored=[], imported_product_modules=self.facts['imported_product_modules'],
            inspected_resources=self.facts['inspected_resources'])
        self.write('ir-upstream-tests', self.upstream)
        self.summary = dict(passed=True, version=stage.VERSION, commit=stage.COMMIT, assets=self.facts['assets'],
            checks={name: {'passed': True} for name in stage.GATES})
        self.summary['checks'].update(full_source_suite={'passed': True, 'skips': skips},
            ir_upstream_regressions=copy.deepcopy(self.upstream),
            ir_build_gates={'passed': True, 'source_capsule_regressions': True, 'capsule_tests': 8})
        self.write('native-summary', self.summary)
        for name in ('diagnose-exe', 'smoke-exe', 'smoke-gui-exe', 'fixtures-exe', 'fixtures-gui-exe',
                     'features-exe', 'oven-playback-exe', 'rust-probe-exe', 'cli-exe'):
            self.write(name, {'passed': True, 'frozen': True})
        self.ir = self.ir_report()
        self.write('ir-feedback-exe', self.ir); self.write('ir-feedback-cli-exe', self.ir)
        self.release = dict(id=987, draft=True, prerelease=True, tag_name='v1.10.0', target_commitish=stage.COMMIT,
            body='Candidate only', assets=[])
        steps = [dict(name=name, conclusion='success', status='completed') for name in
            (stage.BUILD_STEP, 'Preserve verification evidence', 'Preserve complete release files')]
        steps.append(dict(name=stage.PRIVATE_UPLOAD_STEP, conclusion='skipped', status='completed'))
        self.remote = {
            '': dict(id=stage.REPOSITORY_ID, full_name=stage.REPOSITORY, private=False, visibility='public'),
            f'actions/runs/{stage.RUN_ID}': dict(id=stage.RUN_ID, head_sha=stage.COMMIT, head_branch=stage.BRANCH,
                event='workflow_dispatch', status='completed', conclusion='success', workflow_id=stage.WORKFLOW_ID,
                run_attempt=1, path=stage.WORKFLOW_PATH),
            f'actions/runs/{stage.RUN_ID}/attempts/1/jobs?per_page=100': dict(total_count=1, jobs=[dict(
                name='windows-release', status='completed', conclusion='success', head_sha=stage.COMMIT, steps=steps)]),
            'git/ref/tags/v1.10.0': {'object': {'sha': stage.COMMIT, 'type': 'commit'}},
            'releases/987': self.release,
        }
        for proof in self.facts['artifacts'].values():
            self.remote[f'actions/artifacts/{proof["id"]}'] = dict(id=proof['id'], name=proof['name'],
                digest=proof['digest'], size_in_bytes=proof['bytes'], expired=False, workflow_run=dict(
                    id=stage.RUN_ID, head_sha=stage.COMMIT, head_branch=stage.BRANCH,
                    repository_id=stage.REPOSITORY_ID, head_repository_id=stage.REPOSITORY_ID))
        self.mutations = []
        self.addCleanup(patch.stopall)
        patch.object(stage, 'read', side_effect=lambda endpoint: copy.deepcopy(self.remote[endpoint])).start()
        patch.object(stage, 'gh', side_effect=self.transfer).start()
        patch.dict('os.environ', {'GITHUB_REPOSITORY': stage.REPOSITORY, 'GH_TOKEN': 'mock-never-sent',
            'GITHUB_REF': stage.DELIVERY_REF, 'GITHUB_EVENT_NAME': 'workflow_dispatch'}).start()
        self.refresh_archives()

    def write(self, name, value):
        (self.evidence / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def ir_report(self):
        names = ('strict_manifest_resources', 'test_mode_initialization_has_no_native_process',
            'authentic_builtin_workbook_and_worker_assets', 'production_parser_accepts_pinned_original',
            'manual_images_and_previous_layer_features', 'offline_connect_tab_resize_preserves_session',
            'asynchronous_close_preserves_csv_and_two_plots', 'suite_button_fonts_and_single_title',
            'whole_flow_has_no_native_or_com_attempts', 'callback_errors')
        checks = {name: {'passed': True} for name in names}
        sha = 'eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20'
        attempts = dict(native_processes=0, adapter_factories=0, adapter_constructors=0, excel_controllers=0, save_calls=0)
        checks['authentic_builtin_workbook_and_worker_assets'].update(workbook_sha256=sha, workbook_bytes=11523470,
            bridge_sha256='939c65d211ff9d144e956eb7b2bae26c4cfab80437c2f0a6c60efa70f4c1b215',
            mapping=dict(sheet='수동 테스트', temperature_cell='B13', actual_power_cell='C13', recommendation_cell='D13',
                         target_cell='B5', layer_cell='A13', slice_start_cell='BL13'),
            json_files=['input_example_validation.json', 'provenance.json', 'random80_validation.json', 'workbook-replay.settings.json'])
        checks['production_parser_accepts_pinned_original'].update(metadata=dict(sha256=sha, sheet='수동 테스트', first_row=13),
            background_thread=True, excel_calculated=False)
        checks['offline_connect_tab_resize_preserves_session'].update(observed_layers=3, save_calls=0, real_adapters_created=0)
        checks['asynchronous_close_preserves_csv_and_two_plots'].update(rows=3, files=['run.csv', 'one.png', 'two.png'],
            writer_pending_keeps_suite_alive=True, worker_retired=True)
        checks['whole_flow_has_no_native_or_com_attempts']['measured_attempts'] = attempts.copy()
        checks['test_mode_initialization_has_no_native_process'].update(native_process_attempts=0, auto_discovery_started=False)
        checks['manual_images_and_previous_layer_features'].update(layers=3, white_binder=True, dpi=[400, 400])
        return dict(passed=True, frozen=True, excel_com_verified=False, equipment_verified=False,
                    native_operation_attempts=attempts, checks=checks, callback_errors=[])

    def remote_asset(self, name):
        proof = self.facts['assets'][name]
        return dict(name=name, state='uploaded', size=proof['bytes'], digest='sha256:' + proof['sha256'])

    def transfer(self, *args):
        self.assertEqual(args[:3], ('api', '--method', 'POST'))
        self.assertEqual(args[4:7], ('--header', 'Content-Type: application/octet-stream', '--input'))
        self.assertEqual(len(args), 8)
        target = urlsplit(args[3])
        self.assertEqual(target.scheme, 'https')
        self.assertEqual(target.netloc, 'uploads.github.com')
        self.assertEqual(target.path, f'/repos/{stage.REPOSITORY}/releases/987/assets')
        name = Path(args[7]).name
        self.assertEqual(parse_qs(target.query), {'name': [name]})
        self.mutations.append(name)
        self.release['assets'].append(self.remote_asset(name))
        return ''

    def refresh_archives(self):
        for kind, source in (('evidence', self.evidence), ('release', self.assets)):
            proof = self.facts['artifacts'][kind]
            previous = copy.deepcopy(proof)
            path = self.archives / (str(proof['id']) + '.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                for item in sorted(source.rglob('*')):
                    if item.is_file(): archive.write(item, item.relative_to(source).as_posix())
            proof.update(bytes=path.stat().st_size, digest='sha256:' + stage.digest(path))
            remote = self.remote[f'actions/artifacts/{proof["id"]}']
            for key, remote_key in (('bytes', 'size_in_bytes'), ('digest', 'digest')):
                if remote.get(remote_key) == previous[key]: remote[remote_key] = proof[key]

    def run_stage(self, *, refresh=True):
        if refresh: self.refresh_archives()
        return stage.stage(self.evidence, self.assets, self.facts, archives=self.archives)

    def test_template_fails_closed(self):
        incomplete = copy.deepcopy(self.facts)
        incomplete['release_id'] = None
        pinfile = self.root / 'incomplete-facts.json'
        pinfile.write_text(json.dumps(incomplete))
        with self.assertRaises(ValueError): stage.load_pins(pinfile)

    def test_complete_candidate_transfer_preserves_bytes_and_holds(self):
        before = {p.name: p.read_bytes() for p in self.assets.iterdir()}
        report = self.run_stage()
        self.assertEqual(self.mutations, sorted(stage.ASSET_NAMES))
        self.assertFalse(report['candidate_artifact_approved'])
        self.assertTrue(report['release_remains_draft'] and report['server_digest_readback_passed'])
        self.assertTrue(report['outer_archive_bytes_and_digest_verified'] and report['extracted_files_match_outer_archives'])
        self.assertFalse(report['program_bytes_changed'] or report['visibility_changed'] or report['equipment_verified'])
        self.assertEqual(report['equipment_application'], 'HOLD')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.assets.iterdir()})

    def test_resume_skips_only_exact_asset(self):
        name = sorted(stage.ASSET_NAMES)[0]
        self.release['assets'].append(self.remote_asset(name))
        self.run_stage()
        self.assertEqual(self.mutations, sorted(stage.ASSET_NAMES - {name}))

    def test_every_summary_gate_required(self):
        for gate in stage.GATES:
            with self.subTest(gate=gate):
                summary = copy.deepcopy(self.summary); del summary['checks'][gate]
                self.write('native-summary', summary)
                with self.assertRaises(ValueError): self.run_stage()
                self.assertEqual(self.mutations, [])

    def test_upstream_counts_provenance_and_closure_required(self):
        changes = [('tests', 571), ('failures', 1), ('errors', 1), ('skips', 1), ('passed', False),
                   ('source_revision', 'wrong'), ('upstream_snapshots_byte_verified', False), ('excel_com_tested', True),
                   ('imported_product_modules', []), ('inspected_resources', []), ('upstream_test_files', 57)]
        for key, value in changes:
            with self.subTest(key=key):
                upstream = copy.deepcopy(self.upstream); upstream[key] = value
                summary = copy.deepcopy(self.summary); summary['checks']['ir_upstream_regressions'] = upstream
                self.write('ir-upstream-tests', upstream); self.write('native-summary', summary)
                with self.assertRaises(ValueError): self.run_stage()
                self.assertEqual(self.mutations, [])

    def test_raw_summary_divergence_rejected(self):
        upstream = copy.deepcopy(self.upstream); upstream['extra'] = 'diverges'
        self.write('ir-upstream-tests', upstream)
        with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(self.mutations, [])

    def test_each_offline_counter_and_both_raw_executables_required(self):
        for raw_name in ('ir-feedback-exe', 'ir-feedback-cli-exe'):
            for counter in self.ir['native_operation_attempts']:
                with self.subTest(raw=raw_name, counter=counter):
                    raw = copy.deepcopy(self.ir); raw['native_operation_attempts'][counter] = 1
                    self.write(raw_name, raw)
                    with self.assertRaises((ValueError, RuntimeError)): self.run_stage()
                    self.assertEqual(self.mutations, [])
                    self.write(raw_name, self.ir)

    def test_raw_workbook_mapping_inventory_and_archive_required(self):
        for field, value in [('workbook_sha256', 'a' * 64), ('bridge_sha256', 'b' * 64),
                             ('workbook_bytes', 1), ('mapping', {}), ('json_files', [])]:
            with self.subTest(field=field):
                raw = copy.deepcopy(self.ir)
                raw['checks']['authentic_builtin_workbook_and_worker_assets'][field] = value
                self.write('ir-feedback-exe', raw)
                with self.assertRaises((ValueError, RuntimeError)): self.run_stage()
                self.assertEqual(self.mutations, [])
        raw = copy.deepcopy(self.ir)
        raw['checks']['asynchronous_close_preserves_csv_and_two_plots']['files'] = ['../run.csv', 'one.png', 'one.png']
        self.write('ir-feedback-exe', raw)
        with self.assertRaises(ValueError): self.run_stage()

    def test_raw_xml_failures_counts_and_skips(self):
        for filename in ('source-tests.xml', 'source-capsule-tests.xml'):
            path = self.evidence / filename; original = path.read_bytes()
            for failure in ('failure', 'error', 'extra_case', 'skip'):
                with self.subTest(filename=filename, failure=failure):
                    tree = ET.parse(path); case = list(tree.iter('testcase'))[-1]
                    if failure == 'extra_case': ET.SubElement(tree.getroot(), 'testcase', name='extra')
                    else: ET.SubElement(case, 'skipped' if failure == 'skip' else failure)
                    tree.write(path)
                    with self.assertRaises(ValueError): self.run_stage()
                    self.assertEqual(self.mutations, [])
                    path.write_bytes(original)

    def test_bad_remote_preflight_never_mutates(self):
        run = f'actions/runs/{stage.RUN_ID}'
        cases = [('', 'id', 1), ('', 'private', True), ('', 'visibility', 'private'),
            (run, 'head_sha', '0'*40), (run, 'head_branch', 'main'), (run, 'event', 'push'),
            (run, 'status', 'in_progress'), (run, 'conclusion', 'failure'), (run, 'run_attempt', 2),
            (run, 'workflow_id', 1), (run, 'path', 'wrong'),
            ('actions/artifacts/100', 'id', 1), ('actions/artifacts/100', 'name', 'wrong'),
            ('actions/artifacts/100', 'digest', 'sha256:'+'0'*64), ('actions/artifacts/100', 'size_in_bytes', 1),
            ('actions/artifacts/100', 'expired', True), ('releases/987', 'draft', False),
            ('releases/987', 'prerelease', False), ('releases/987', 'body', 'changed'),
            ('releases/987', 'tag_name', 'v0'), ('releases/987', 'target_commitish', '0'*40)]
        for endpoint, key, bad in cases:
            with self.subTest(endpoint=endpoint, key=key):
                original = self.remote[endpoint][key]; self.remote[endpoint][key] = bad
                with self.assertRaises(ValueError): self.run_stage()
                self.assertEqual(self.mutations, [])
                self.remote[endpoint][key] = original

    def test_remote_asset_duplicates_unknown_partial_and_mismatch(self):
        name = sorted(stage.ASSET_NAMES)[0]
        for defect in ('duplicate', 'unknown', 'size', 'digest', 'state'):
            with self.subTest(defect=defect):
                record = self.remote_asset(name); self.release['assets'] = [record]
                if defect == 'duplicate': self.release['assets'].append(copy.deepcopy(record))
                elif defect == 'unknown': record['name'] = 'unknown.exe'
                else: record[defect] = 'wrong'
                with self.assertRaises(ValueError): self.run_stage()
                self.assertEqual(self.mutations, [])

    def test_wrong_cyclic_deep_and_annotated_tags(self):
        endpoint = 'git/ref/tags/v1.10.0'
        self.remote[endpoint] = {'object': {'sha': '0'*40, 'type': 'commit'}}
        with self.assertRaises(ValueError): self.run_stage()
        self.remote[endpoint] = {'object': {'sha': '1'*40, 'type': 'tag'}}
        self.remote['git/tags/'+'1'*40] = self.remote[endpoint]
        with self.assertRaises(ValueError): self.run_stage()
        for index in range(1, 7):
            self.remote['git/tags/'+str(index)*40] = {'object': {'sha': str(index+1)*40, 'type': 'tag'}}
        with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(self.mutations, [])
        self.remote['git/tags/'+'1'*40] = {'object': {'sha': stage.COMMIT, 'type': 'commit'}}
        self.run_stage()

    def test_altered_extra_and_symlink_assets(self):
        name = sorted(stage.ASSET_NAMES)[0]; path = self.assets / name; original = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.run_stage()
        path.write_bytes(original)
        extra = self.assets / 'extra'; extra.write_bytes(b'extra')
        with self.assertRaises(ValueError): self.run_stage()
        extra.unlink(); path.unlink(); path.symlink_to(self.evidence / 'native-summary.json')
        with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(self.mutations, [])

    def test_draft_and_tag_races_block_before_upload(self):
        original = stage.read
        count = 0
        def raced(endpoint):
            nonlocal count
            if endpoint == 'releases/987':
                count += 1
                if count == 2: self.release['draft'] = False
            return original(endpoint)
        with patch.object(stage, 'read', side_effect=raced):
            with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(self.mutations, [])

    def test_upload_digest_readback_stops_after_first_write(self):
        def corrupt(*args):
            self.transfer(*args); self.release['assets'][-1]['digest'] = 'sha256:'+'0'*64
        with patch.object(stage, 'gh', side_effect=corrupt):
            with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(len(self.mutations), 1)

    def test_tag_race_between_files_stops_further_uploads(self):
        def retag(*args):
            self.transfer(*args)
            self.remote['git/ref/tags/v1.10.0']['object']['sha'] = '0' * 40
        with patch.object(stage, 'gh', side_effect=retag):
            with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(len(self.mutations), 1)

    def test_tag_retargets_at_write_but_upload_still_addresses_pinned_release_id(self):
        def concurrent_tag_change(*args):
            self.remote['git/ref/tags/v1.10.0']['object']['sha'] = '0' * 40
            # transfer asserts the full API URL addresses release987 directly.
            # A tag-based writer would now select another release.
            self.transfer(*args)
        with patch.object(stage, 'gh', side_effect=concurrent_tag_change):
            with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(len(self.mutations), 1)

    def test_download_uses_exact_ids_and_verifies_outer_bytes_before_extraction(self):
        calls = []
        def download(artifact_id, target):
            calls.append(artifact_id)
            shutil.copyfile(self.archives / (str(artifact_id) + '.zip'), target)
        dest = self.root / 'downloaded'
        with patch.object(stage, 'download_archive', side_effect=download):
            stage.download_candidate(dest / 'archives', dest / 'evidence', dest / 'assets', self.facts)
        self.assertEqual(calls, [p['id'] for p in self.facts['artifacts'].values()])
        stage.verify_downloaded_trees(dest / 'archives', dest / 'evidence', dest / 'assets', self.facts)
        stage.verify_local(dest / 'evidence', dest / 'assets', self.facts)
        self.assertEqual(self.mutations, [])

    def test_outer_size_and_same_size_corruption_stop_before_any_extraction(self):
        for kind in ('evidence', 'release'):
            for defect in ('size', 'digest'):
                with self.subTest(kind=kind, defect=defect):
                    dest = self.root / ('bad-' + kind + '-' + defect)
                    def download(artifact_id, target):
                        payload = (self.archives / (str(artifact_id) + '.zip')).read_bytes()
                        if artifact_id == self.facts['artifacts'][kind]['id']:
                            payload = payload + b'extra' if defect == 'size' else bytes([payload[0] ^ 1]) + payload[1:]
                        target.write_bytes(payload)
                    with patch.object(stage, 'download_archive', side_effect=download):
                        with self.assertRaises(ValueError):
                            stage.download_candidate(dest / 'archives', dest / 'evidence', dest / 'assets', self.facts)
                    self.assertFalse((dest / 'evidence').exists() or (dest / 'assets').exists())
                    self.assertEqual(self.mutations, [])

    def test_outer_archive_corruption_or_extracted_mutation_stops_staging(self):
        path = self.archives / '100.zip'; original = path.read_bytes()
        path.write_bytes(original + b'extra')
        with self.assertRaises(ValueError): self.run_stage(refresh=False)
        path.write_bytes(original)
        (self.evidence / 'native-summary.json').write_bytes(b'changed after extraction')
        with self.assertRaises(ValueError): self.run_stage(refresh=False)
        self.assertEqual(self.mutations, [])

    def test_unsafe_zip_inventory_is_rejected_even_with_matching_outer_digest(self):
        for defect in ('traversal', 'absolute', 'drive', 'symlink', 'case_collision', 'conflict', 'encrypted'):
            with self.subTest(defect=defect):
                path = self.root / (defect + '.zip')
                with zipfile.ZipFile(path, 'w') as archive:
                    if defect == 'traversal': archive.writestr('../outside', b'bad')
                    elif defect == 'absolute': archive.writestr('/outside', b'bad')
                    elif defect == 'drive': archive.writestr('C:/outside', b'bad')
                    elif defect == 'symlink':
                        link = zipfile.ZipInfo('link'); link.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(link, b'../outside')
                    elif defect == 'case_collision':
                        archive.writestr('same', b'one'); archive.writestr('SAME', b'two')
                    elif defect == 'conflict':
                        archive.writestr('parent', b'one'); archive.writestr('parent/child', b'two')
                    else:
                        archive.writestr('owned', b'one')
                if defect == 'encrypted':
                    # Set matching encryption bits in local and central headers.
                    data = bytearray(path.read_bytes()); data[6] |= 1
                    central = data.index(b'PK\x01\x02'); data[central + 8] |= 1
                    path.write_bytes(data)
                proof = {'bytes': path.stat().st_size, 'digest': 'sha256:' + stage.digest(path)}
                with self.assertRaises(ValueError): stage.verify_archive(path, proof)

    def test_exact_id_api_download_streams_binary_without_name_resolution(self):
        target = self.root / 'binary.zip'
        def binary(command, *, stdout, stderr, check):
            self.assertEqual(command, ['gh', 'api', '--method', 'GET',
                f'repos/{stage.REPOSITORY}/actions/artifacts/100/zip'])
            self.assertFalse(check)
            self.assertEqual(stderr, stage.subprocess.PIPE)
            stdout.write(b'owned binary')
            return SimpleNamespace(returncode=0)
        with patch.object(stage.subprocess, 'run', side_effect=binary):
            stage.download_archive(100, target)
        self.assertEqual(target.read_bytes(), b'owned binary')

    def test_download_error_fails_without_extraction_or_raw_signed_url_error(self):
        target = self.root / 'failed.zip'
        with patch.object(stage.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr=b'signed-secret-url')):
            with self.assertRaisesRegex(ValueError, 'no extraction or retry') as failure:
                stage.download_archive(100, target)
        self.assertNotIn('signed-secret-url', str(failure.exception))
        self.assertEqual(self.mutations, [])

    def test_source_reviews_and_checker_bytes_are_required(self):
        for group, key in [('source_reviews', 'source'), ('source_reviews', 'ci_delta'),
                           ('checker_files', 'suite/배포_빌드/release_exe.py')]:
            with self.subTest(group=group, key=key):
                facts = copy.deepcopy(self.facts)
                if group == 'source_reviews': facts[group][key]['sha256'] = '0' * 64
                else: facts[group][key] = '0' * 64
                with self.assertRaises(ValueError): stage.stage(self.evidence, self.assets, facts, archives=self.archives)
                self.assertEqual(self.mutations, [])

    def test_job_step_and_artifact_provenance_fail_closed(self):
        jobs = self.remote[f'actions/runs/{stage.RUN_ID}/attempts/1/jobs?per_page=100']
        job = jobs['jobs'][0]
        original = copy.deepcopy(job)
        for key, value in [('name', 'other'), ('conclusion', 'failure'), ('head_sha', '0' * 40), ('status', 'queued')]:
            with self.subTest(job=key):
                job[key] = value
                with self.assertRaises(ValueError): self.run_stage()
                job.clear(); job.update(copy.deepcopy(original))
        for step in job['steps']:
            previous = step['conclusion']; step['conclusion'] = 'failure'
            with self.assertRaises(ValueError): self.run_stage()
            step['conclusion'] = previous
        for key, value in [('id', 1), ('head_sha', '0' * 40), ('head_branch', 'main'),
                           ('repository_id', 1), ('head_repository_id', 1)]:
            with self.subTest(artifact=key):
                source = self.remote['actions/artifacts/100']['workflow_run']
                previous = source[key]; source[key] = value
                with self.assertRaises(ValueError): self.run_stage()
                source[key] = previous
        self.assertEqual(self.mutations, [])

    def test_reviewable_facts_schema_rejects_all_unknown_pins(self):
        self.assertEqual(stage.load_pins(), json.loads(stage.PINS.read_text()))
        pinfile = self.root / 'facts.json'
        pinfile.write_text(json.dumps(self.facts))
        self.assertEqual(stage.load_pins(pinfile), self.facts)
        for key in ('run_attempt', 'release_id', 'release_notes_sha256'):
            with self.subTest(key=key):
                facts = copy.deepcopy(self.facts); facts[key] = None
                pinfile.write_text(json.dumps(facts))
                with self.assertRaises(ValueError): stage.load_pins(pinfile)
        for group in ('artifacts', 'assets'):
            for name, proof in self.facts[group].items():
                for key in ('id', 'digest', 'bytes') if group == 'artifacts' else ('bytes', 'sha256'):
                    with self.subTest(group=group, name=name, key=key):
                        facts = copy.deepcopy(self.facts); facts[group][name][key] = None
                        pinfile.write_text(json.dumps(facts))
                        with self.assertRaises(ValueError): stage.load_pins(pinfile)


if __name__ == '__main__':
    unittest.main()
