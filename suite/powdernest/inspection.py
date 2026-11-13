"""Readable STL diagnostics/preview, deliberately separate from valid solids.

This adapter never fixes faces, registers a geometry proof or authorizes
packing/export. The strict load_model/validate APIs retain their rejection.
"""
from pathlib import Path
import numpy as np
import trimesh

from .cache import file_digest
from .geometry import GeometryError, _canonical_mesh, _geometry_epsilon, _UNIT_SCALE
from .types import Model


def preview_stl(path, *, stl_unit='mm', model_id='inspection', reason=''):
    path = Path(path)
    unit = str(stl_unit).lower().strip()
    if path.suffix.lower() != '.stl' or unit not in _UNIT_SCALE:
        raise GeometryError('진단 미리보기는 단위가 지정된 STL에 사용할 수 있습니다.')
    raw = trimesh.load_mesh(path, file_type='stl', process=False)
    mesh = _canonical_mesh(trimesh.Trimesh(np.asarray(raw.vertices) * _UNIT_SCALE[unit], raw.faces, process=False))
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    triangles = vertices[faces]; epsilon = _geometry_epsilon(vertices)
    area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)
    longest = np.linalg.norm(triangles[:, [1, 2, 0]] - triangles, axis=2).max(axis=1)
    degenerate = area <= epsilon * np.maximum(longest, epsilon)
    duplicate_count = len(faces) - len(np.unique(np.sort(faces, axis=1), axis=0))
    edges = np.sort(np.asarray(mesh.edges), axis=1)
    unique_edges, edge_counts = np.unique(edges, axis=0, return_counts=True)
    bad = unique_edges[edge_counts != 2]
    details = {'source_file': path.name, 'source_sha256': file_digest(path), 'unit': unit,
               'working_unit': 'mm', 'valid_for_packing': False, 'inspection_only': True,
               'original_validation_error': str(reason), 'raw_triangles': len(faces), 'unique_vertices': len(vertices),
               'degenerate_triangles': int(degenerate.sum()), 'exact_zero_area_triangles': int((area == 0).sum()),
               'duplicate_triangles': int(duplicate_count), 'watertight': bool(mesh.is_watertight),
               'winding_consistent': bool(mesh.is_winding_consistent),
               'boundary_edges': int((edge_counts == 1).sum()),
               'nonmanifold_edges': int((edge_counts > 2).sum()), 'bounds_mm': mesh.bounds.tolist(),
               'extents_mm': mesh.extents.tolist(), 'geometry_modified': False,
               'message': '원본 STL을 수정 없이 표시합니다. 닫힌 재료 형상으로 검증되지 않아 배치·학습·결과 내보내기에 사용할 수 없습니다.'}
    return Model(model_id, path.name, mesh, source_path=str(path.resolve()), source_unit=unit,
                 diagnostics={'format': 'STL', 'inspection_only': True, 'input_inspection': details,
                              'inspection_bad_edge_indices': bad[:5000].tolist()})


def summary(model):
    report = model.diagnostics.get('input_inspection', {})
    return (f"{model.name}\n미리보기 전용 · 배치 입력 검사 실패\n"
            f"삼각형 {report.get('raw_triangles', 0):,}개 · 중복 {report.get('duplicate_triangles', 0):,}개 · "
            f"면적 0/불안정 {report.get('degenerate_triangles', 0):,}개\n"
            f"열린 모서리 {report.get('boundary_edges', 0):,}개 · 다중 접합 {report.get('nonmanifold_edges', 0):,}개\n"
            f"{report.get('message', '')}\n원인: {report.get('original_validation_error', '')}")
