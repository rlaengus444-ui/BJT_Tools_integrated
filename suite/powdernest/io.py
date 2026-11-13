"""Job import and validated, millimetre-based result export."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
import struct
from collections import Counter
from pathlib import Path
import numpy as np
import trimesh

from .rotations import ROTATIONS
from .types import Placement, RequestItem, Settings, matrix_for


def _json_value(obj):
    if isinstance(obj, np.ndarray):
        return _json_value(obj.tolist())
    if isinstance(obj, np.generic):
        return _json_value(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _json_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_value(v) for v in obj]
    return obj


def load_job(path):
    """Load a JSON job, resolving model paths relative to the job file."""
    from .geometry import load_model

    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ValueError('작업 파일에 models 배열이 필요합니다')
    models, items = {}, []
    for entry in data['models']:
        model_id = entry['id']
        if not isinstance(model_id, str) or not model_id or model_id in models:
            raise ValueError('모델 ID는 비어 있지 않고 고유해야 합니다')
        quantity = entry.get('quantity', 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise ValueError('수량은 음수가 아닌 정수여야 합니다')
        source = Path(entry['path'])
        if not source.is_absolute():
            source = path.parent / source
        unit = entry.get('unit', 'mm')
        if unit not in ('mm', 'cm', 'm', 'in'):
            raise ValueError('STL 단위는 mm, cm, m, in 중 하나여야 합니다')
        model = load_model(source, stl_unit=unit, model_id=model_id)
        if 'allowed_rotations' in entry:
            rotations = entry['allowed_rotations']
            if not isinstance(rotations, list) or not rotations or any(isinstance(r, bool) or not isinstance(r, int) or r < 0 or r >= len(ROTATIONS) for r in rotations):
                raise ValueError('allowed_rotations에는 0부터 23까지의 회전 ID가 필요합니다')
            model.allowed_rotations = tuple(dict.fromkeys(rotations))
        models[model_id] = model
        items.append(RequestItem(model_id, quantity))
    return models, items, Settings(**data.get('settings', {}))


def load_placements(path, models):
    """Read exported placements and verify any stored transforms match their fields."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    entries = data.get('placements') if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError('placements 배열이 필요합니다')
    placements = []
    for e in entries:
        copy_index, rotation_id, position = e['copy_index'], e['rotation_id'], e['position']
        if isinstance(copy_index, bool) or not isinstance(copy_index, int) or copy_index < 0:
            raise ValueError('복사본 인덱스는 음수가 아닌 정수여야 합니다')
        if isinstance(rotation_id, bool) or not isinstance(rotation_id, int) or not 0 <= rotation_id < len(ROTATIONS):
            raise ValueError('회전 ID는 0부터 23까지의 정수여야 합니다')
        if not isinstance(position, (list, tuple)) or len(position) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in position):
            raise ValueError('위치에는 유한한 좌표 3개가 필요합니다')
        p = Placement(e['model_id'], copy_index, rotation_id, tuple(position))
        if p.model_id not in models:
            raise ValueError(f'알 수 없는 모델 ID: {p.model_id}')
        if 'rotation_matrix' in e and not np.array_equal(np.asarray(e['rotation_matrix']), ROTATIONS[p.rotation_id]):
            raise ValueError('저장된 회전 행렬과 회전 ID가 일치하지 않습니다')
        if 'matrix' in e and not np.allclose(np.asarray(e['matrix'], dtype=float), matrix_for(models[p.model_id], p), rtol=0, atol=1e-8):
            raise ValueError('저장된 변환 행렬과 배치 정보가 일치하지 않습니다')
        placements.append(p)
    return placements


def result_label(result, validation):
    """Describe verified feasibility without claiming an unproved optimum."""
    if not validation.get('valid', False):
        if (validation.get('interlocking') or {}).get('linked_pairs'):
            return '고리 얽힘 · 분리 불가 확인 · 내보내기 불가'
        return '검증 실패 · 내보내기 불가'
    metadata = result.metadata or {}
    certificate = metadata.get('infeasibility_certificate') or metadata.get('certificate')
    if not validation.get('complete', False) and metadata.get('feasibility') == 'proven_infeasible' and certificate:
        label = '전체 요청 배치 불가능 증명 · 검증 통과'
    elif result.placements:
        scope = '전체 요청 배치' if validation.get('complete', False) else '부분 배치'
        label = f'{scope} · 찾은 최선의 배치 (최적성 미증명) · 검증 통과'
    else:
        label = '배치 없음 · 검증 통과'
    termination = metadata.get('termination', result.status)
    reason = {'time_limit': '시간 제한 종료', 'cancelled': '사용자 취소',
              'height_bound': '높이 하한 도달 (수치 허용오차 이내)'}.get(termination)
    return f'{label} · {reason}' if reason else label


def export_result(models, items, settings, result, directory):
    """Validate afresh and write separate transformed shells and reports.

    Partial results are exported when their placements pass validation; the
    reports explicitly identify incomplete requests.
    """
    from .export_safety import protect_model_outputs
    protect_model_outputs(models,directory)
    from .validation import validate

    validation = validate(models, items, settings, result.placements)
    if not validation.get('valid', False):
        raise ValueError('배치 검증에 실패하여 내보낼 수 없습니다: ' + json.dumps(_json_value(validation), ensure_ascii=False))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    from .types import matrices_for
    entries = []
    for p, matrix in zip(result.placements, matrices_for(models, result.placements)):
        model = models[p.model_id]
        entries.append({
            'model_id': p.model_id, 'copy_index': p.copy_index,
            'rotation_id': p.rotation_id, 'rotation_matrix': ROTATIONS[p.rotation_id].tolist(),
            'position': list(p.position), 'matrix': matrix.tolist(),
            'source_path': model.source_path, 'source_unit': model.source_unit,
            'matrix_coordinate_system': 'canonical_mm_to_build_box_mm',
        })
    _write_stl(models, result.placements, entries, directory / 'layout.stl')
    requested = sum(i.quantity for i in items)
    placed = len(entries)
    partial = not validation.get('complete', False)
    counts = Counter(p.model_id for p in result.placements)
    requested_by_model = Counter()
    for item in items:
        requested_by_model[item.model_id] += item.quantity
    unplaced = {model_id: max(0, count - counts[model_id]) for model_id, count in requested_by_model.items()}
    height = max((float(p.position[2] + models[p.model_id].bounds_for(p.rotation_id)[1, 2] - models[p.model_id].bounds_for(p.rotation_id)[0, 2]) for p in result.placements), default=0.0)
    placements_document = {'coordinate_unit': 'mm', 'matrix_convention': '4x4 column-vector transform from canonical input millimetres to build box millimetres', 'placements': entries}
    report = {'status': result.status, 'partial': partial, 'requested_count': requested,
              'placed_count': placed, 'unplaced': unplaced, 'height_mm': height,
              'elapsed_seconds': result.elapsed, 'iterations': result.iterations,
              'settings': asdict(settings), 'validation': validation, 'metadata': result.metadata}
    (directory / 'placements.json').write_text(json.dumps(_json_value(placements_document), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (directory / 'report.json').write_text(json.dumps(_json_value(report), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    text = (f"배치 결과: {result_label(result, validation)}\n"
            f"상태: {result.status}\n요청: {requested}개 / 배치: {placed}개\n"
            f"미배치: {json.dumps(_json_value(unplaced), ensure_ascii=False)}\n"
            f"높이: {height:.3f} mm\n경과 시간: {result.elapsed:.3f} 초\n"
            f"검증: 통과\n설정: {json.dumps(_json_value(asdict(settings)), ensure_ascii=False)}\n"
            f"검증 상세: {json.dumps(_json_value(validation), ensure_ascii=False)}\n"
            f"종료 및 증명 정보: {json.dumps(_json_value(result.metadata), ensure_ascii=False)}\n")
    (directory / 'report.txt').write_text(text, encoding='utf-8')
    return directory


def _write_stl(models, placements, entries, path):
    """Stream separate shells with bounded buffers, no concatenated mesh copy."""
    from trimesh.exchange.stl import _stl_dtype
    total = sum(len(models[p.model_id].mesh.faces) for p in placements)
    if total > 0xFFFFFFFF:
        raise ValueError('STL의 32비트 삼각형 수 한도를 초과했습니다')
    with Path(path).open('wb') as stream:
        header = bytes(80) if total else b'PowderNest empty layout'.ljust(80, b'\0')
        stream.write(header + struct.pack('<I', total))
        for p, entry in zip(placements, entries):
            mesh = models[p.model_id].mesh
            world = trimesh.transform_points(mesh.vertices, np.asarray(entry['matrix']))
            faces = np.asarray(mesh.faces)
            for first in range(0, len(faces), 65536):
                triangles = world[faces[first:first + 65536]]
                packed = np.zeros(len(triangles), dtype=_stl_dtype)
                packed['vertices'] = triangles
                normals, valid = trimesh.triangles.normals(triangles)
                packed['normals'][valid] = normals
                stream.write(packed.tobytes())
