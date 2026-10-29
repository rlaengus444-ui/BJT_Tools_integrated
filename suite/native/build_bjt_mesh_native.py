"""Build the optional BJT native kernel with a caller-supplied Rust toolchain."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CRATE = HERE / 'bjt_mesh_native'


def digest_source():
    paths = [CRATE / 'Cargo.toml', *sorted((CRATE / 'src').rglob('*.rs'))]
    if len(paths) < 2 or not paths[0].is_file():
        raise FileNotFoundError('Expected native crate Cargo.toml and Rust sources')
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(CRATE).as_posix().encode('utf-8')
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'little'))
        digest.update(name)
        digest.update(len(data).to_bytes(8, 'little'))
        digest.update(data)
    return digest.hexdigest()


def atomic_publish(destination, data):
    """Publish complete bytes via a new sibling inode; never mutate a loaded DLL."""
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cargo', default='cargo', help='cargo executable')
    parser.add_argument('--rustc', default='rustc', help='rustc executable')
    parser.add_argument('--target', help='Rust target triple; default is rustc host')
    parser.add_argument('--output-dir', type=Path, default=HERE)
    args = parser.parse_args()
    env = os.environ.copy()
    env['RUSTC'] = args.rustc
    # Do not inherit arbitrary architecture, fast-math or panic settings.
    env.pop('CARGO_ENCODED_RUSTFLAGS', None)
    env['RUSTFLAGS'] = '-C panic=unwind -C target-cpu=generic'
    rustc_version = subprocess.check_output([args.rustc, '-vV'], env=env, text=True).strip()
    host = next(line.split(':', 1)[1].strip() for line in rustc_version.splitlines() if line.startswith('host:'))
    target = args.target or host
    if 'windows' in target:
        name = 'bjt_mesh_native.dll'
    elif 'apple-darwin' in target:
        name = 'libbjt_mesh_native.dylib'
    elif 'linux' in target:
        name = 'libbjt_mesh_native.so'
    else:
        parser.error(f'Unsupported library target: {target}')
    source_sha = digest_source()
    command = [args.cargo, 'build', '--release', '--offline', '--manifest-path', str(CRATE / 'Cargo.toml'), '--target', target]
    subprocess.run(command, env=env, check=True)
    if digest_source() != source_sha:
        raise RuntimeError('Native sources changed during build; binary was not installed')
    # Explicit --target creates target/<triple>/release even for the host.
    target_dir = Path(env.get('CARGO_TARGET_DIR', CRATE / 'target')).resolve()
    binary = target_dir / target / 'release' / name
    data = binary.read_bytes()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / name
    atomic_publish(destination, data)
    metadata = {'schema': 1, 'rustc': rustc_version, 'cargo': subprocess.check_output([args.cargo, '--version'], env=env, text=True).strip(),
                'target': target, 'profile': 'release', 'rustflags': env['RUSTFLAGS'],
                'crate_source_sha256': source_sha, 'binary': name,
                'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data),
                'source_digest_scheme': 'sha256 of sorted Cargo.toml and src/**/*.rs, each prefixed by 8-byte little-endian name length and data length'}
    atomic_publish(args.output_dir / 'bjt_mesh_native.build.json', (json.dumps(metadata, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
