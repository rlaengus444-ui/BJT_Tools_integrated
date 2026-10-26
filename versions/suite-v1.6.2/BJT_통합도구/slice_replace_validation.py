"""Native format validation for the preserved slicing replacement workflow.

The original replacement function still owns preflight, backup, writes and
rollback. This adapter supplies accurate stored sample depths and all TIFF
pages without changing that transaction's ordering or numbered-file scope.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

import slice_compare_engine as engine


def get_image_signature(path):
    """Decode for validity, returning only structure, never image content."""
    path = Path(path)
    try:
        options = engine.CompareOptions()
        with path.open('rb') as stream:
            head = stream.read(16)
            stream.seek(0)
            actual_format = engine._detect_format(head)
            if actual_format == 'PNG':
                frames, _, _ = engine._decode_png(stream, options, None)
            elif actual_format == 'TIFF':
                frames, _, _ = engine._decode_tiff(stream, options, None)
            elif actual_format == 'JPEG':
                frames, _, _ = engine._decode_jpeg(stream, options, None)
            elif head.startswith(b'BM'):
                # Preserve BMP support of the original replacement tool.
                with Image.open(stream) as image:
                    image.load()
                    actual_format = image.format
                    bits = {'1': 1, 'L': 8, 'P': 8, 'LA': 16, 'RGB': 24,
                            'RGBA': 32, 'CMYK': 32, 'I': 32, 'F': 32}.get(image.mode)
                    if bits is None:
                        raise ValueError('지원하지 않는 BMP 픽셀 모드: ' + image.mode)
                    channels = len(image.getbands())
                    frames = [{'width': image.width, 'height': image.height,
                               'bit_depth': bits // channels, 'channels': channels,
                               'bits_per_pixel': bits, 'mode': image.mode,
                               'dtype': 'uint8' if bits <= 32 else image.mode}]
            else:
                raise ValueError('지원하지 않는 실제 이미지 형식입니다.')
        structures = []
        for frame in frames:
            structures.append({key: frame[key] for key in (
                'width', 'height', 'bit_depth', 'channels', 'bits_per_pixel',
                'mode', 'dtype')})
        first = structures[0]
        mode = first['mode']
        bands = {'L': ('L',), 'P': ('P',), 'LA': ('L', 'A'),
                 'RGB': ('R', 'G', 'B'), 'RGBA': ('R', 'G', 'B', 'A'),
                 'CMYK': ('C', 'M', 'Y', 'K')}.get(mode)
        if bands is None:
            bands = tuple(str(index + 1) for index in range(first['channels']))
        return {'ext': path.suffix.lower(), 'format': actual_format,
                'size': (first['width'], first['height']), 'mode': mode,
                'bands': bands, 'bits': first['bits_per_pixel'],
                'bit_depth': first['bit_depth'], 'frame_count': len(structures),
                'frame_signatures': structures}
    except Exception as exc:
        raise RuntimeError(f'이미지 정보를 읽을 수 없습니다: {path}\n{exc}') from exc


def signature_to_text(signature):
    width, height = signature['size']
    return (f"ext={signature['ext']}, format={signature['format']}, "
            f"size={width}x{height}, mode={signature['mode']}, "
            f"bands={signature['bands']}, bits={signature['bits']} bit/pixel, "
            f"sample_bits={signature['bit_depth']}, "
            f"frames={signature['frame_count']}")


def check_folder_format_consistency(number_map, folder_name, log):
    numbers = sorted(number_map)
    if not numbers:
        raise RuntimeError(f'{folder_name}에 숫자 이미지 파일이 없습니다.')
    base_path = number_map[numbers[0]]
    base = get_image_signature(base_path)
    log(f'[최종검사] {folder_name} 기준 파일: {base_path.name}')
    log(f'[최종검사] 기준 형식: {signature_to_text(base)}')
    log(f'[최종검사] 숫자 슬라이싱 이미지 전체 {len(numbers):,}개 검사 (미리보기·하위 폴더 제외)')
    bad = []
    for index, number in enumerate(numbers, start=1):
        path = number_map[number]
        signature = base if index == 1 else get_image_signature(path)
        if signature != base:
            bad.append((path.name, signature))
        if index % 100 == 0 or index == len(numbers):
            log(f'[최종검사] 형식 검사 완료: {index:,} / {len(numbers):,}')
    if bad:
        messages = [f'{folder_name} 전체 이미지 형식이 일관되지 않습니다.',
                    f'기준 파일: {base_path.name}',
                    f'기준 형식: {signature_to_text(base)}', '', '문제 파일 예시:']
        for name, signature in bad[:30]:
            messages.append(f'  {name}: {signature_to_text(signature)}')
            if signature['frame_signatures'] != base['frame_signatures']:
                for index, (expected, actual) in enumerate(zip(
                        base['frame_signatures'], signature['frame_signatures']), start=1):
                    if expected != actual:
                        messages.append(f'    프레임 {index}: 기준={expected}, 파일={actual}')
                        break
        if len(bad) > 30:
            messages.append(f'  ... 외 {len(bad) - 30}개')
        raise RuntimeError('\n'.join(messages))
    log(f'[최종검사 통과] {len(numbers):,}개 · 번호 {numbers[0]}~{numbers[-1]} · '
        f"{signature_to_text(base)}")


def install(module):
    """Install once in a loaded original module; source stays byte-identical."""
    if getattr(module, '_suite_native_validation', False):
        return
    for name in ('get_image_signature', 'signature_to_text',
                 'check_folder_format_consistency'):
        setattr(module, '_suite_original_' + name, getattr(module, name))
        setattr(module, name, globals()[name])
    module._suite_native_validation = True
