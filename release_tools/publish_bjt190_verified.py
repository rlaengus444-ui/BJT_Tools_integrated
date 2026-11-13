import hashlib,json,os,pathlib,subprocess
REPO='rlaengus444-ui/BJT_Tools_integrated'
COMMIT='d55617e494aebe270c9a618544638774882596c3'
RUN=43455530159
TAG='v1.9.0'
ROOT=pathlib.Path(os.environ['BJT_ROOT'])
EXPECTED={'BJT_1.9.0_manual.zip': {'bytes': 233930, 'sha256': '6fff0a78ad3eb9af07a3de9d40b226ed7be97cdc565222dd88bb4fd2c541dc41'}, 'BJT_1.9.0_source.zip': {'bytes': 6610350, 'sha256': 'a1c8d5f1cba9b4d23ecc88504fc12ae3cedfef209b1b3f9ee55a30e801ac78ff'}, 'BJT_1.9.0_standalone.pyw': {'bytes': 3693400, 'sha256': '05100e2890657d52793fbfa93f5c417256a595d8f6fcda6ebedf2f834f85b230'}, 'BJT_1.9.0_Windows_x64.zip': {'bytes': 363883451, 'sha256': '12d1a5372b57cff7a84a77afca97b54829bea54c339fc07f3f57b50d7b323808'}, 'SHA256SUMS.txt': {'bytes': 357, 'sha256': '38c1ad8ef04567a3975e1df52c7ad02d579e283b12955c1720f262d3a00bfd67'}}
def gh(*args):
    return subprocess.check_output(['gh',*map(str,args)],text=True,encoding='utf-8')
def api(path): return json.loads(gh('api',path))
def digest(p):
    with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
assert os.environ['GITHUB_REPOSITORY']==REPO
assert api('repos/'+REPO)['private'] is True
run=api(f'repos/{REPO}/actions/runs/{RUN}')
assert run['head_sha']==COMMIT and run['conclusion']=='success'
summary=json.loads((ROOT/'evidence/native-summary.json').read_text(encoding='utf-8'))
assert summary['passed'] is True and summary['commit']==COMMIT and summary['version']=='1.9.0'
assert summary['assets']==EXPECTED and all(v['passed'] is True for v in summary['checks'].values())
assets=ROOT/'assets'
assert {p.name for p in assets.iterdir()}==set(EXPECTED)
for name,e in EXPECTED.items():
    assert (assets/name).stat().st_size==e['bytes'] and digest(assets/name)==e['sha256'],name
# Never overwrite any release or tag. A partial attempt is inspected manually.
releases=json.loads(gh('api','--paginate','--slurp',f'repos/{REPO}/releases?per_page=100'))
assert not any(r['tag_name']==TAG for page in releases for r in page),'Release already exists'
tags=json.loads(gh('api','--paginate','--slurp',f'repos/{REPO}/tags?per_page=100'))
assert not any(t['name']==TAG for page in tags for t in page),'Tag already exists'
gh('release','create',TAG,'--repo',REPO,'--draft','--target',COMMIT,'--title','BJT 통합프로그램 1.9.0','--notes','모델 자동배치 포함 Windows x64 배포판입니다. ZIP 전체를 압축 해제한 뒤 BJT_통합프로그램.exe를 실행하세요. Python 또는 PyCharm 설치가 필요하지 않습니다. Windows CI 및 모델 배치·GUI·CLI 검증을 통과한 동일 파일입니다.')
release=json.loads(gh('release','view',TAG,'--repo',REPO,'--json','apiUrl,isDraft,targetCommitish'))
assert release['isDraft'] is True and release['targetCommitish']==COMMIT
assert release['apiUrl'].startswith(f'https://api.github.com/repos/{REPO}/releases/')
rid=api(release['apiUrl'])['id']
for name in sorted(EXPECTED): gh('release','upload',TAG,str(assets/name),'--repo',REPO)
r=api(f'repos/{REPO}/releases/{rid}')
assert r['draft'] is True and r['target_commitish']==COMMIT and r['tag_name']==TAG
assert {a['name'] for a in r['assets']}==set(EXPECTED)
for a in r['assets']:
    e=EXPECTED[a['name']]
    assert a['state']=='uploaded' and a['size']==e['bytes'] and a['digest']=='sha256:'+e['sha256']
readback=ROOT/'readback';readback.mkdir()
gh('release','download',TAG,'--repo',REPO,'--dir',readback)
for name,e in EXPECTED.items():assert (readback/name).stat().st_size==e['bytes'] and digest(readback/name)==e['sha256'],name
tags=json.loads(gh('api','--paginate','--slurp',f'repos/{REPO}/tags?per_page=100'))
assert not any(t['name']==TAG for page in tags for t in page),'Tag appeared before publication; inspect draft manually'
gh('release','edit',TAG,'--repo',REPO,'--draft=false')
r=api(f'repos/{REPO}/releases/{rid}')
assert r['draft'] is False and r['target_commitish']==COMMIT and r['tag_name']==TAG
assert {a['name'] for a in r['assets']}==set(EXPECTED)
for a in r['assets']:
    e=EXPECTED[a['name']]
    assert a['state']=='uploaded' and a['size']==e['bytes'] and a['digest']=='sha256:'+e['sha256']
obj=api(f'repos/{REPO}/git/ref/tags/{TAG}')['object']
for _ in range(10):
    if obj['type'] != 'tag':break
    obj=api(f'repos/{REPO}/git/tags/{obj["sha"]}')['object']
assert obj['type']=='commit' and obj['sha']==COMMIT,'Published tag commit differs'
report={'passed':True,'release_url':r['html_url'],'release_id':rid,'verified_commit':COMMIT,'download_sha256_verified':True,'assets':EXPECTED}
(ROOT/'publication-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
