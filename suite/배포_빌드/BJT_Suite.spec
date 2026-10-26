# -*- mode: python ; coding: utf-8 -*-
import json
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata, collect_delvewheel_libs_directory

HERE = Path(SPECPATH)
APP = HERE / 'app-source'
VERSION = json.loads((APP / 'source_manifest.json').read_text(encoding='utf-8'))['suite_version']
inputs = json.loads((HERE / 'build_inputs.json').read_text(encoding='utf-8'))
datas = []
# Conda and the official python.org installer use different license names.
python_license = next((Path(sys.prefix) / name for name in ('LICENSE_PYTHON.txt', 'LICENSE.txt')
                       if (Path(sys.prefix) / name).is_file()), None)
if python_license is not None:
    datas.append((str(python_license), 'licenses/Python'))
for path in APP.rglob('*'):
    if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.nbc', '.nbi'):
        datas.append((str(path), str(path.parent.relative_to(APP))))
# vtk.py installs the legacy vtk.util alias at runtime. Its real file lives
# under vtkmodules and can therefore be analyzed without a misleading error.
hiddenimports = ['vtkmodules.util.numpy_support' if name == 'vtk.util.numpy_support' else name
                 for name in inputs['hiddenimports']]
binaries = []
# These distributable VC++ DLLs are not part of clean Windows itself. Wine's
# built-in substitutes must never conceal their absence from the release.
vendor_runtime = HERE / 'vendor-runtime'
for path in vendor_runtime.glob('*.dll'):
    binaries.append((str(path), '.'))
for path in (vendor_runtime / 'licenses').glob('*'):
    if path.is_file():
        datas.append((str(path), 'licenses/Microsoft-VC++-2022'))
hiddenimports += ['vtk', 'vtkmodules.all', 'matplotlib.backends.backend_tkagg', 'matplotlib.backends.backend_agg',
                  'PIL.ImageTk', 'PIL.ImageOps', 'PIL.ImageDraw', 'PIL.ImageChops', 'PIL._tkinter_finder',
                  'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.scrolledtext', 'tkinter.font',
                  'numba.typed', 'numba.core.typing', 'scipy.optimize', 'scipy.ndimage',
                  'unittest.mock', 'frozen_fixtures', 'frozen_chitu_gui', 'frozen_large_chitu_gui',
                  'frozen_camera_chitu_gui', 'frozen_suite_features']
hiddenimports += ['manifold3d', 'fcl', 'fcl.fcl', 'plotly', 'plotly.offline',
                  'chitu_overlap', 'chitu_overlap.geometry', 'chitu_overlap.chitubox',
                  'chitu_overlap.inputs', 'chitu_overlap.cli', 'chitu_overlap.viewer', 'chitu_overlap.seams']
datas += collect_data_files('plotly')
binaries += collect_dynamic_libs('manifold3d')
binaries += collect_dynamic_libs('fcl')
datas, binaries = collect_delvewheel_libs_directory('fcl', libdir_name='python_fcl.libs', datas=datas, binaries=binaries)

for package in ('imagecodecs',):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
for package in ('trimesh', 'skimage', 'scipy', 'numba', 'vtkmodules'):
    hiddenimports += collect_submodules(package, filter=lambda name: not any(part in name.split('.') for part in ('tests', 'test', 'testing', 'qt', 'wx', 'cuda')))
    datas += collect_data_files(package)
for package in ('llvmlite', 'shapely', 'rtree'):
    binaries += collect_dynamic_libs(package)
for distribution in ('numpy', 'scipy', 'Pillow', 'scikit-image', 'trimesh', 'shapely', 'numba', 'llvmlite',
                     'rtree', 'opencv-python', 'tkinterdnd2', 'vtk', 'matplotlib', 'tifffile', 'pypng',
                     'imagecodecs', 'networkx', 'ImageIO', 'lazy-loader', 'packaging', 'contourpy', 'cycler',
                     'fonttools', 'kiwisolver', 'pyparsing', 'python-dateutil', 'six',
                     'manifold3d', 'python-fcl', 'plotly', 'narwhals'):
    datas += copy_metadata(distribution)

a = Analysis([str(HERE / 'bjt_frozen_entry.py')], pathex=[str(APP), str(HERE)],
             binaries=binaries, datas=datas, hiddenimports=sorted(set(hiddenimports)),
             hookspath=[], runtime_hooks=[],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython', 'jupyter', 'pytest', 'torch', 'cupy'],
             hooksconfig={'matplotlib': {'backends': ['TkAgg', 'Agg']}},
             module_collection_mode={'numba': 'pyz+py', 'llvmlite': 'pyz+py'}, noarchive=False)
# Windows 10/11 already supplies UCRT. Conda's private copy would load beside
# System32's CRT. Match the working source environment's single system CRT;
# Windows 10/11 supplies these runtime/API-set DLLs. Keep VCRUNTIME/MSVCP DLLs.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != 'ucrtbase.dll'
              and not Path(entry[0]).name.lower().startswith('api-ms-win-crt-')]
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BJT_통합프로그램', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=False,
          version=str(HERE / 'version_info.txt'))
console = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BJT_명령줄', debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=True,
              version=str(HERE / 'version_info.txt'))
coll = COLLECT(gui, console, a.binaries, a.datas, strip=False, upx=False, name=f'BJT_통합프로그램_{VERSION}_EXE')
