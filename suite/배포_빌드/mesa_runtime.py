"""Pinned application-local Windows software OpenGL; never changes System32."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
PIN_FILE = HERE / 'vendor-runtime/mesa/manifest.json'
DLL_SHA256 = '35cad2622a8e1e4bcdddf03ef91d87fe6fa35f2c166bb6a8c9c8fc67131629f2'
DLL_BYTES = 58363904
ARCHIVE_SHA256 = '11dc079e356f33ce2ff84aa399c975da1a7bdd4c470f6eef4cf359ad8b9ca80f'
MESA_VERSION = '26.3.0'


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def pin():
    value=json.loads(PIN_FILE.read_text(encoding='utf-8'))
    if (value['archive_sha256']!=ARCHIVE_SHA256 or value['dll_sha256']!=DLL_SHA256
            or value['dll_bytes']!=DLL_BYTES or value['mesa_version']!=MESA_VERSION):
        raise RuntimeError('Mesa pin differs from the reviewed runtime')
    return value


def verify_dll(path):
    path=Path(path)
    if path.stat().st_size!=DLL_BYTES or digest(path)!=DLL_SHA256:
        raise RuntimeError('Mesa OpenGL DLL hash mismatch: '+str(path))


def checked_copy(source,target,expected):
    source,target=Path(source),Path(target)
    if digest(source)!=expected:raise RuntimeError('Runtime source changed: '+str(source))
    if target.is_symlink():raise RuntimeError('Refusing a symlinked runtime target')
    if target.exists():
        if digest(target)!=expected:raise RuntimeError('Refusing to replace a different runtime file: '+str(target))
        return
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    if digest(target)!=expected:raise RuntimeError('Runtime copy changed: '+str(target))


def verify_install(directory):
    directory=Path(directory);value=pin();verify_dll(directory/'opengl32.dll')
    for path in directory.rglob('*.dll'):
        if path.name.lower()=='opengl32.dll':verify_dll(path)
    if json.loads((directory/'graphics-runtime.json').read_text(encoding='utf-8'))!=value:
        raise RuntimeError('Mesa runtime provenance differs')
    notices=directory/'_internal/licenses/Mesa-LLVM'
    for name,expected in value['notices'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise RuntimeError('Invalid notice path')
        if digest(notices/name)!=expected:raise RuntimeError('Mesa notice changed: '+name)
    return {'passed':True,'dll_sha256':DLL_SHA256,'archive_sha256':ARCHIVE_SHA256,
            'mesa_version':MESA_VERSION,'notices':len(value['notices'])}


def install_runtime(runtime,directory):
    runtime,directory=Path(runtime),Path(directory);value=pin()
    checked_copy(runtime/'opengl32.dll',directory/'opengl32.dll',DLL_SHA256)
    checked_copy(PIN_FILE,directory/'graphics-runtime.json',digest(PIN_FILE))
    for name,expected in value['notices'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise RuntimeError('Invalid notice path')
        checked_copy(PIN_FILE.parent/'licenses'/name,directory/'_internal/licenses/Mesa-LLVM'/name,expected)
    return verify_install(directory)


def prepare_runtime(directory):
    """Download only the pinned archive, extract one WGL DLL, install per app."""
    if os.name!='nt':raise RuntimeError('Mesa preparation requires native Windows')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False);value=pin()
    archive=directory/'mesa-llvmpipe-x64.7z'
    with urllib.request.urlopen(value['archive_url'],timeout=120) as response,archive.open('wb') as stream:
        shutil.copyfileobj(response,stream)
    if digest(archive)!=ARCHIVE_SHA256:raise RuntimeError('Mesa archive hash mismatch')
    executable=shutil.which('7z') or r'C:\Program Files\7-Zip\7z.exe'
    subprocess.run([executable,'e',str(archive),'opengl32.dll','-o'+str(directory),'-y'],check=True,
                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    verify_dll(directory/'opengl32.dll')
    installed=install_runtime(directory,Path(sys.executable).resolve().parent)
    return dict(installed,runtime_directory=str(directory))


def graphics_proof(window):
    """Check the DLL already loaded by VTK, not merely a file on disk."""
    if os.name!='nt':raise RuntimeError('Native Windows graphics evidence is required')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p];kernel.GetModuleHandleW.restype=ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint]
    kernel.GetModuleFileNameW.restype=ctypes.c_uint
    handle=kernel.GetModuleHandleW('opengl32.dll');buffer=ctypes.create_unicode_buffer(32768)
    length=kernel.GetModuleFileNameW(handle,buffer,len(buffer)) if handle else 0
    if not length or length>=len(buffer):raise RuntimeError('Cannot identify the loaded OpenGL DLL')
    actual=Path(buffer.value);expected=Path(sys.executable).resolve().parent/'opengl32.dll'
    if not actual.samefile(expected):raise RuntimeError('VTK loaded OpenGL outside the application directory: '+str(actual))
    verify_dll(actual)
    lines=[line.strip() for line in window.ReportCapabilities().splitlines()
           if any(key in line.lower() for key in ('opengl vendor','opengl renderer','opengl version'))]
    capabilities='\n'.join(lines)
    if 'llvmpipe' not in capabilities.lower() or 'Mesa '+MESA_VERSION not in capabilities:
        raise RuntimeError('The reviewed llvmpipe renderer is not active: '+capabilities)
    return {'passed':True,'loaded_path':str(actual),'dll_sha256':DLL_SHA256,'capabilities':lines}


def probe(output):
    import vtkmodules.vtkRenderingOpenGL2
    from vtkmodules.vtkRenderingCore import vtkRenderer,vtkRenderWindow,vtkActor,vtkPolyDataMapper,vtkWindowToImageFilter
    from vtkmodules.vtkFiltersSources import vtkSphereSource
    renderer=vtkRenderer();renderer.SetBackground(1,1,1)
    source=vtkSphereSource();mapper=vtkPolyDataMapper();mapper.SetInputConnection(source.GetOutputPort())
    actor=vtkActor();actor.SetMapper(mapper);actor.GetProperty().SetColor(.3,.4,.6);renderer.AddActor(actor)
    window=vtkRenderWindow();window.SetOffScreenRendering(1);window.SetMultiSamples(0)
    window.SetSize(640,480);window.AddRenderer(renderer);renderer.ResetCamera()
    try:
        window.Render();result=graphics_proof(window)
        capture=vtkWindowToImageFilter();capture.SetInput(window);capture.ReadFrontBufferOff();capture.ShouldRerenderOff();capture.Update()
        image=capture.GetOutput();rgb=[int(image.GetScalarComponentAsDouble(320,240,0,c)) for c in range(3)]
        if max(rgb)>=245:raise RuntimeError('Software OpenGL did not render the opaque sphere')
        result['opaque_pixel_rgb']=rgb
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:window.Finalize()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--probe',required=True)
    probe(parser.parse_args().probe)
