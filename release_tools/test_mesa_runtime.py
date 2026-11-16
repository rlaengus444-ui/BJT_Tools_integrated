"""Distribution must retain the pinned graphics bytes and their notices."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'suite/배포_빌드'))
import mesa_runtime as mesa


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    pin_file=tmp_path/'vendor/manifest.json';pin_file.parent.mkdir()
    notices=pin_file.parent/'licenses';notices.mkdir();(notices/'NOTICE.txt').write_bytes(b'license')
    raw=b'MZ pinned test DLL';checksum=hashlib.sha256(raw).hexdigest()
    value={'archive_sha256':mesa.ARCHIVE_SHA256,'dll_sha256':checksum,'dll_bytes':len(raw),
           'mesa_version':mesa.MESA_VERSION,'notices':{'NOTICE.txt':hashlib.sha256(b'license').hexdigest()}}
    pin_file.write_text(json.dumps(value))
    monkeypatch.setattr(mesa,'PIN_FILE',pin_file);monkeypatch.setattr(mesa,'DLL_SHA256',checksum)
    monkeypatch.setattr(mesa,'DLL_BYTES',len(raw))
    folder=tmp_path/'runtime';folder.mkdir();(folder/'opengl32.dll').write_bytes(raw)
    return folder,tmp_path/'app',raw


def test_application_local_install_is_exact_and_idempotent(runtime):
    source,target,raw=runtime
    assert mesa.install_runtime(source,target)['passed']
    assert (target/'opengl32.dll').read_bytes()==raw
    assert (target/'_internal/licenses/Mesa-LLVM/NOTICE.txt').read_bytes()==b'license'
    assert mesa.install_runtime(source,target)==mesa.verify_install(target)


def test_install_preserves_existing_different_files(runtime):
    source,target,raw=runtime;target.mkdir();dll=target/'opengl32.dll';dll.write_bytes(b'existing data')
    with pytest.raises(RuntimeError,match='different runtime'):mesa.install_runtime(source,target)
    assert dll.read_bytes()==b'existing data'


@pytest.mark.parametrize('name',['opengl32.dll','graphics-runtime.json','_internal/licenses/Mesa-LLVM/NOTICE.txt'])
def test_modified_graphics_or_notices_are_rejected(runtime,name):
    source,target,raw=runtime;mesa.install_runtime(source,target)
    (target/name).write_bytes(b'{}')
    with pytest.raises(RuntimeError):mesa.verify_install(target)


def test_corrupt_source_is_not_installed(runtime):
    source,target,raw=runtime;(source/'opengl32.dll').write_bytes(b'corrupt')
    with pytest.raises(RuntimeError,match='source changed'):mesa.install_runtime(source,target)
    assert not target.exists()


def test_unreviewed_opengl_copy_inside_bundle_is_rejected(runtime):
    source,target,raw=runtime;mesa.install_runtime(source,target)
    other=target/'_internal/opengl32.dll';other.write_bytes(b'other driver')
    with pytest.raises(RuntimeError,match='hash mismatch'):mesa.verify_install(target)
