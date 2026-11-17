"""Rebuild the single .pyw and source/manual archives from one app tree."""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import textwrap
import zipfile


EXCLUDED = {
    '__pycache__', '.pytest_cache', '.venv', 'logs', 'app-source', 'build',
    'dist', 'build-python', 'test-profile',
}


def app_files(app):
    for path in sorted(app.rglob('*')):
        relative = path.relative_to(app)
        if (relative.parts[:3] != ('native', 'bjt_mesh_native', 'target')
                and path.is_file() and not EXCLUDED.intersection(relative.parts)
                and path.suffix not in {'.pyc', '.nbc', '.nbi', '.log'}):
            yield path, relative.as_posix()


def make_zip(items, timestamp=(2026, 10, 24, 0, 0, 0)):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in items:
            entry = zipfile.ZipInfo(name, timestamp)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--launcher-template', type=Path,
                        help='Existing verified single-file launcher template')
    parser.add_argument('--launcher-output', type=Path,
                        help='Write the generated single-file launcher outside a repository checkout')
    parser.add_argument('--release-date', type=date.fromisoformat,
                        help='User-facing release date, YYYY-MM-DD; deterministic archive timestamps')
    args = parser.parse_args()
    app = args.app.resolve()
    version = json.loads((app / 'source_manifest.json').read_text(encoding='utf-8'))['suite_version']
    args.output.mkdir(parents=True, exist_ok=True)
    timestamp = ((args.release_date.year, args.release_date.month, args.release_date.day, 0, 0, 0)
                 if args.release_date else (2026, 10, 24, 0, 0, 0))
    items = list(app_files(app))
    payload = make_zip(items, timestamp)
    checksum = hashlib.sha256(payload).hexdigest()
    launcher = args.launcher_output or (app.parent / 'BJT_통합프로그램.pyw')
    original = (args.launcher_template or launcher).read_text(encoding='utf-8')
    original = re.sub(r"PAYLOAD_SHA256 = '[^']*'", f"PAYLOAD_SHA256 = '{checksum}'", original)
    start = original.index('PAYLOAD = b"""')
    end = original.index('"""', start + len('PAYLOAD = b"""')) + 3
    encoded = '\n'.join(textwrap.wrap(base64.b85encode(payload).decode('ascii'), 110))
    original = original[:start] + 'PAYLOAD = b"""' + encoded + '"""' + original[end:]
    tool_count = len(json.loads((app / 'source_manifest.json').read_text(encoding='utf-8'))['tools'])
    original = re.sub(r'workbench \d+\.\d+(?:\.\d+)?: (?:ten|eleven|twelve|thirteen|\d+) tools',
                      f'workbench {version}: {tool_count} tools', original)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(original, encoding='utf-8')
    tree = ast.parse(original)
    values = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {'PAYLOAD', 'PAYLOAD_SHA256'}
    }
    decoded = base64.b85decode(b''.join(values['PAYLOAD'].split()))
    assert hashlib.sha256(decoded).hexdigest() == values['PAYLOAD_SHA256']
    with zipfile.ZipFile(io.BytesIO(decoded)) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(items)
        assert all(archive.read(name) == path.read_bytes() for path, name in items)
    source_name = f'BJT_통합프로그램_{version}_전체소스.zip'
    standalone = app.parent / 'standalone'
    standalone_items = [(p, 'standalone/' + p.name) for p in sorted(standalone.iterdir()) if p.is_file()] if standalone.is_dir() else []
    source_items = [(launcher, launcher.name)] + [(path, f'BJT_통합도구/{name}') for path, name in items] + standalone_items
    (args.output / source_name).write_bytes(make_zip(source_items, timestamp))
    manual_items = [(path, path.name) for path in sorted((app / 'manuals').iterdir()) if path.is_file()]
    manual_name = f'BJT_통합프로그램_{version}_사용설명서.zip'
    (args.output / manual_name).write_bytes(make_zip(manual_items, timestamp))
    record = {'version': version, 'embedded_file_count': len(items), 'payload_sha256': checksum,
              'embedded_bytes_match': True, 'archives': {}}
    for name in (source_name, manual_name):
        path = args.output / name
        record['archives'][name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (args.output / 'source-release-verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
