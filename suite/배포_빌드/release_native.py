"""Fail-closed provenance checks shared by native CI, frozen QA and packaging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

TARGET = 'x86_64-pc-windows-msvc'
TOOLCHAIN = '1.99.1'
BINARY = 'bjt_mesh_native.dll'
EXPORTS = frozenset((
    'bjt_mesh_abi_version', 'bjt_mesh_edge_row_size', 'bjt_mesh_edge_row_align',
    'bjt_mesh_counts_size', 'bjt_mesh_platform_supported', 'bjt_mesh_sort_count_edges',
    'bjt_mesh_triangle_pack_abi_version', 'bjt_mesh_pack_grid_triangles',
))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def crate_digest(crate):
    paths = [crate / 'Cargo.toml', *sorted((crate / 'src').rglob('*.rs'))]
    if len(paths) < 2:
        raise RuntimeError('Missing Rust crate sources')
    result = hashlib.sha256()
    for path in paths:
        name = path.relative_to(crate).as_posix().encode('utf-8')
        data = path.read_bytes()
        result.update(len(name).to_bytes(8, 'little'))
        result.update(name)
        result.update(len(data).to_bytes(8, 'little'))
        result.update(data)
    return result.hexdigest()


def verify_native(directory, *, expected_source=None):
    """Read actual DLL bytes and exports; metadata alone is never a proof."""
    import pefile
    directory = Path(directory)
    binary = directory / BINARY
    metadata_path = directory / 'bjt_mesh_native.build.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    actual = digest(binary)
    if (metadata.get('schema') != 1 or metadata.get('target') != TARGET
            or metadata.get('binary') != BINARY or metadata.get('profile') != 'release'
            or metadata.get('binary_sha256') != actual
            or metadata.get('binary_bytes') != binary.stat().st_size
            or metadata.get('rustflags') != '-C panic=unwind -C target-cpu=generic'
            or not metadata.get('rustc', '').startswith('rustc ' + TOOLCHAIN + ' ')
            or '\nhost: ' + TARGET + '\n' not in metadata.get('rustc', '')
            or not metadata.get('cargo', '').startswith('cargo ' + TOOLCHAIN + ' ')):
        raise RuntimeError('Native DLL is not the pinned Windows MSVC release build')
    if expected_source is not None and metadata.get('crate_source_sha256') != expected_source:
        raise RuntimeError('Native DLL was built from different Rust sources')
    pe = pefile.PE(str(binary), fast_load=False)
    try:
        exports = {symbol.name.decode('ascii') for symbol in
                   getattr(getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', [])
                   if symbol.name is not None}
        if pe.FILE_HEADER.Machine != 0x8664 or not EXPORTS.issubset(exports):
            raise RuntimeError('Native DLL requires AMD64 PE and all eight ABI exports')
        if any(symbol.forwarder for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols
               if symbol.name and symbol.name.decode('ascii') in EXPORTS):
            raise RuntimeError('Native ABI exports must execute in the bundled DLL')
    finally:
        pe.close()
    return {'sha256': actual, 'bytes': binary.stat().st_size,
            'metadata_sha256': digest(metadata_path), 'machine': 'AMD64',
            'target': TARGET, 'toolchain': TOOLCHAIN, 'exports': sorted(EXPORTS),
            'crate_source_sha256': metadata['crate_source_sha256']}


def verify_chain(source, stage, frozen):
    expected = crate_digest(Path(source) / 'native/bjt_mesh_native')
    proofs = {name: verify_native(Path(root) / 'native', expected_source=expected)
              for name, root in (('source', source), ('stage', stage), ('frozen', frozen))}
    if proofs['source'] != proofs['stage'] or proofs['source'] != proofs['frozen']:
        raise RuntimeError('Source, staged and frozen native DLL provenance differs')
    return proofs


def require_probe(report, proof, *, commit, manifest_sha256, executable_sha256):
    """Bind successful execution evidence to this commit and these frozen bytes."""
    if (report.get('passed') is not True or report.get('frozen') is not True
            or report.get('commit') != commit or report.get('native') != proof
            or report.get('source_manifest_sha256') != manifest_sha256
            or report.get('executable_sha256') != executable_sha256
            or report.get('missing_dll_rejected') is not True
            or report.get('can_pack_grid') is not True
            or report.get('fixture_count') != 6 or report.get('parity') is not True):
        raise RuntimeError('Current frozen Rust execution and parity proof is required')
    for name in ('sort_count', 'pack_grid_triangles'):
        usage = report.get('native_calls', {}).get(name, {})
        if usage.get('success_calls', 0) <= 0 or usage.get('rows', 0) <= 0:
            raise RuntimeError('Frozen probe did not execute nonempty native ' + name)
