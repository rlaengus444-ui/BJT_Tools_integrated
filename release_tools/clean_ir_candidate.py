"""Remove one pinned CI fixture log from a fresh copy of the reviewed ZIP.

The program/runtime/member bytes are not rebuilt or recompressed. Every retained
local record, normalized central record, metadata field and uncompressed hash
must match the original. This is an offline packaging operation only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import unicodedata
import zipfile


REVISION = '0a48712576b9b7cbb147c03becd957eb559af4c7'
CI_RUN = 44096041775
EXE_NAME = 'BJT_1.10.0_Windows_x64.zip'
LOG_NAME = 'BJT_통합프로그램_1.10.0_EXE/_internal/logs/errors.log'
LOG_PIN = {'bytes': 1626, 'sha256': 'c06264143b923010383965a790f0561014fa5eecc3908827f81ecea359394df1'}
ORIGINAL_ASSETS = {
    'BJT_1.10.0_manual.zip': {'bytes': 241422, 'sha256': '2e7cec3aa47df43130ba2cd1202da5a9f16a2e298487fa15f3d36aecb445993b'},
    'BJT_1.10.0_source.zip': {'bytes': 26398492, 'sha256': 'c324e834521f6efff320a71d662ca3fbfef4a414ff259754b23064e3015d8ba7'},
    'BJT_1.10.0_standalone.pyw': {'bytes': 16101255, 'sha256': 'be921bc2625d5adb24aae61f9c7e7ebe52a127280c9e081e7e0560cb5e9e9d7a'},
    EXE_NAME: {'bytes': 418199737, 'sha256': '7a2bcc5e23ea7ae4ab5f34d72bf901ca9d3892ffe73ba6a9e9456133336a5243'},
    'SHA256SUMS.txt': {'bytes': 361, 'sha256': 'bbb7ea599fd00dcd5a2236fac9ca32de749b00dc5e34024ea6962b05c9883f7d'},
}
# These are all serialized ZipInfo metadata fields exposed by Python. Absolute
# header_offset and derived _end_offset are layout positions, not metadata.
METADATA_FIELDS = ('filename', 'orig_filename', 'date_time', 'compress_type',
    'comment', 'extra', 'create_system', 'create_version', 'extract_version',
    'reserved', 'flag_bits', 'volume', 'internal_attr', 'external_attr',
    'CRC', 'compress_size', 'file_size', '_raw_time')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def facts(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Expected regular file: ' + str(path))
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': path.stat().st_size, 'sha256': checksum}


def checked_file(path, expected):
    actual = facts(path)
    require(actual == expected, 'Pinned file differs: ' + str(path))
    return actual


def safe_name(name):
    require(isinstance(name, str) and name and '\x00' not in name and '\\' not in name and not name.startswith('/'),
            'Unsafe archive path: ' + repr(name))
    parts = name.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    require(all(p not in ('', '.', '..') for p in parts), 'Non-normalized archive path: ' + name)
    for part in parts:
        require(not any(c in part for c in ':<>"|?*') and all(ord(c) >= 32 for c in part)
                and part == part.rstrip(' .') and part.split('.')[0].upper() not in reserved,
                'Windows-unsafe archive path: ' + name)
    return unicodedata.normalize('NFC', name).casefold()


def metadata(info):
    values = {}
    for name in METADATA_FIELDS:
        value = getattr(info, name)
        values[name] = value.hex() if isinstance(value, bytes) else list(value) if isinstance(value, tuple) else value
    return values


def range_hash(stream, begin, end):
    require(0 <= begin <= end, 'Invalid raw ZIP record range')
    stream.seek(begin)
    remaining, result = end - begin, hashlib.sha256()
    while remaining:
        value = stream.read(min(1024 * 1024, remaining))
        require(value, 'Truncated raw ZIP record')
        result.update(value)
        remaining -= len(value)
    return result.hexdigest()


def archive_inventory(path):
    rows, aliases = {}, set()
    with zipfile.ZipFile(path) as archive, Path(path).open('rb') as raw:
        infos = archive.infolist()
        require(0 < len(infos) <= 50000, 'Unexpected archive count')
        total = 0
        for info in infos:
            require(info.orig_filename == info.filename and not info.is_dir(), 'Unexpected directory/NUL path')
            alias = safe_name(info.filename)
            require(info.filename not in rows and alias not in aliases, 'Duplicate/case/Unicode ZIP path: ' + info.filename)
            mode = info.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG) and not info.flag_bits & 1,
                    'Special/encrypted ZIP member: ' + info.filename)
            total += info.file_size
            require(0 <= info.file_size <= 2 * 1024**3 and total <= 20 * 1024**3, 'Unexpected ZIP expansion')
            checksum, size = hashlib.sha256(), 0
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    checksum.update(chunk)
                    size += len(chunk)
            require(size == info.file_size, 'ZIP member length differs')
            rows[info.filename] = {'bytes': size, 'sha256': checksum.hexdigest(), 'metadata': metadata(info)}
            aliases.add(alias)
        for name in rows:
            parts = name.split('/')
            require(not any(safe_name('/'.join(parts[:i])) in aliases for i in range(1, len(parts))),
                    'ZIP file/parent collision')
        ordered = sorted(infos, key=lambda item: item.header_offset)
        require(ordered[0].header_offset == 0 and len({info.header_offset for info in ordered}) == len(ordered),
                'Unexpected ZIP prefix/overlapping local offsets')
        for index, info in enumerate(ordered):
            end = ordered[index + 1].header_offset if index + 1 < len(ordered) else archive.start_dir
            rows[info.filename]['raw_local_record_bytes'] = end - info.header_offset
            rows[info.filename]['raw_local_record_sha256'] = range_hash(raw, info.header_offset, end)
        # A central record's only allowed byte difference is the local-header
        # offset at bytes42:46, because deleting an earlier record moves it.
        raw.seek(archive.start_dir)
        for info in infos:
            header = raw.read(46)
            require(len(header) == 46 and header[:4] == b'PK\x01\x02', 'Bad central directory record')
            name_size, extra_size, comment_size = struct.unpack_from('<HHH', header, 28)
            tail = raw.read(name_size + extra_size + comment_size)
            require(len(tail) == name_size + extra_size + comment_size, 'Truncated central directory')
            offset = struct.unpack_from('<I', header, 42)[0]
            require(offset == info.header_offset and offset != 0xffffffff, 'Unsupported ZIP64/central local-header offset')
            encoded_name = tail[:name_size].decode('utf-8' if info.flag_bits & 0x800 else 'cp437')
            require(encoded_name == info.filename, 'Central directory member name differs')
            normalized = header[:42] + b'\0' * 4 + header[46:] + tail
            rows[info.filename]['central_record_bytes'] = len(normalized)
            rows[info.filename]['normalized_central_record_sha256'] = hashlib.sha256(normalized).hexdigest()
        end = raw.read(22)
        require(len(end) == 22 and end[:4] == b'PK\x05\x06', 'Unsupported ZIP64 or extra central directory record')
        disk, start_disk, on_disk, total_records, directory_size, directory_offset, comment_size = struct.unpack_from('<HHHHIIH', end, 4)
        require(disk == 0 and start_disk == 0 and on_disk == total_records == len(infos)
                and directory_offset == archive.start_dir, 'Invalid ZIP end directory')
        comment = raw.read(comment_size)
        require(comment == archive.comment and not raw.read(1), 'Unexpected ZIP comment/trailing bytes')
        return {'members': rows, 'archive_comment_hex': archive.comment.hex(), 'member_order': [info.filename for info in infos]}


def verify_transform(original, cleaned, original_pin, removed_name, removed_pin, expected_remaining=None):
    checked_file(original, original_pin)
    before, after = archive_inventory(original), archive_inventory(cleaned)
    require(removed_name in before['members'], 'Pinned log member is absent')
    log = before['members'][removed_name]
    require({key: log[key] for key in ('bytes', 'sha256')} == removed_pin, 'Pinned log bytes differ')
    expected_names = set(before['members']) - {removed_name}
    require(set(after['members']) == expected_names and removed_name not in after['members'], 'Repack changed more than the pinned log')
    require(after['member_order'] == [name for name in before['member_order'] if name != removed_name], 'Retained member order changed')
    require(after['archive_comment_hex'] == before['archive_comment_hex'], 'Archive comment changed')
    if expected_remaining is not None:
        require(len(expected_names) == expected_remaining, 'Unexpected retained file count')
    for name in sorted(expected_names):
        require(before['members'][name] == after['members'][name], 'Retained member bytes/metadata/raw record changed: ' + name)
    # Re-read original outer bytes after the complete comparison as well.
    checked_file(original, original_pin)
    return {'original_members': len(before['members']), 'cleaned_members': len(after['members']),
            'removed_member': {'path': removed_name, **removed_pin}, 'retained_members': len(expected_names),
            'metadata_fields': list(METADATA_FIELDS), 'allowed_position_changes': ['header_offset', 'derived _end_offset'],
            'all_retained_metadata_identical': True, 'all_retained_uncompressed_bytes_identical': True,
            'all_retained_raw_local_records_identical': True, 'all_retained_normalized_central_records_identical': True,
            'all_retained_crc_validated': True, 'retained_inventory': after['members']}


def write_json(path, value):
    path = Path(path)
    require(not path.exists(), 'Refusing to overwrite report: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def clean_assets(original, output, provenance_path, facts_path):
    original, output = Path(original).resolve(), Path(output).resolve()
    provenance_path, facts_path = Path(provenance_path).resolve(), Path(facts_path).resolve()
    require(original.is_dir() and {p.name for p in original.iterdir()} == set(ORIGINAL_ASSETS), 'Expected original five-asset directory')
    require(not output.exists() and output != original and not output.is_relative_to(original), 'Fresh independent output directory required')
    require(provenance_path != facts_path and not provenance_path.is_relative_to(output) and not facts_path.is_relative_to(output)
            and not provenance_path.exists() and not facts_path.exists(), 'Fresh reports must stay outside asset output')
    for name, pin in ORIGINAL_ASSETS.items(): checked_file(original / name, pin)
    # Verify the pinned log before allocating or mutating any output files.
    before = archive_inventory(original / EXE_NAME)
    log = before['members'].get(LOG_NAME, {})
    require(len(before['members']) == 3728 and {key: log.get(key) for key in ('bytes', 'sha256')} == LOG_PIN,
            'Original archive/log does not match the approved cleanup policy')
    executable = shutil.which('zip')
    require(executable is not None, 'Installed zip utility is required; no dependency installation attempted')
    output.mkdir(parents=True)
    for name in ORIGINAL_ASSETS:
        if name != 'SHA256SUMS.txt': shutil.copyfile(original / name, output / name)
    checked_file(output / EXE_NAME, ORIGINAL_ASSETS[EXE_NAME])
    command = [executable, '-d', str(output / EXE_NAME), LOG_NAME]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    require(result.returncode == 0, 'zip -d did not complete: ' + result.stderr[-2000:])
    proof = verify_transform(original / EXE_NAME, output / EXE_NAME, ORIGINAL_ASSETS[EXE_NAME], LOG_NAME, LOG_PIN, 3727)
    cleaned_exe = facts(output / EXE_NAME)
    expected_old_text = ''.join(f'{ORIGINAL_ASSETS[name]["sha256"]}  {name}\n' for name in sorted(ORIGINAL_ASSETS) if name != 'SHA256SUMS.txt')
    original_text = (original / 'SHA256SUMS.txt').read_bytes()
    require(original_text == expected_old_text.encode('utf-8'), 'Original checksums text differs')
    old_line = f'{ORIGINAL_ASSETS[EXE_NAME]["sha256"]}  {EXE_NAME}\n'.encode()
    new_line = f'{cleaned_exe["sha256"]}  {EXE_NAME}\n'.encode()
    require(original_text.count(old_line) == 1, 'Expected exactly one original EXE checksum field')
    (output / 'SHA256SUMS.txt').write_bytes(original_text.replace(old_line, new_line))
    cleaned = {name: facts(output / name) for name in sorted(ORIGINAL_ASSETS)}
    for name in set(ORIGINAL_ASSETS) - {EXE_NAME, 'SHA256SUMS.txt'}:
        require(cleaned[name] == ORIGINAL_ASSETS[name], 'Non-EXE asset changed: ' + name)
    for name, pin in ORIGINAL_ASSETS.items(): checked_file(original / name, pin)
    provenance = {'schema': 1, 'passed': True, 'operation': 'remove_exact_ci_fixture_log_only',
        'source_revision': REVISION, 'original_ci_run': CI_RUN, 'original_assets': ORIGINAL_ASSETS,
        'cleaned_assets': cleaned, 'removed_member': {'path': LOG_NAME, **LOG_PIN}, 'archive_transform': proof,
        'checksum_edit': 'Only the original EXE SHA256SUMS field was replaced; every other byte retained.',
        'zip_command': ['zip', '-d', EXE_NAME, LOG_NAME], 'zip_exit_code': result.returncode,
        'execution': {'offline_packaging_only': True, 'product_rebuilt': False, 'product_files_changed': False,
            'runtime_reexecuted': False, 'excel_com_executed': False, 'hardware_executed': False,
            'original_ci_program_files_are_identical': True}}
    write_json(provenance_path, provenance)
    fact_record = {'schema': 1, 'passed': True, 'source_revision': REVISION, 'original_ci_run': CI_RUN,
        'assets': cleaned, 'original_assets': ORIGINAL_ASSETS, 'removed_member': provenance['removed_member'],
        'provenance_sha256': facts(provenance_path)['sha256']}
    write_json(facts_path, fact_record)
    print(json.dumps({'passed': True, 'assets': cleaned, 'provenance_sha256': fact_record['provenance_sha256']}, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--output-assets', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--facts', type=Path, required=True)
    args = parser.parse_args()
    return clean_assets(args.assets, args.output_assets, args.provenance, args.facts)


if __name__ == '__main__':
    raise SystemExit(main())
