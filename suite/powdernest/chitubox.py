"""Legacy Basic .chitubox projects with one independent object per placement.

This is the native AB231243 binary container, not an STL with a different
extension.  Rotation is baked into each object's triangles.  The build plate's
XY origin is its centre; PowderNest's origin is the lower build-box corner.

The implementation uses the documented older, geometry-only layout.  It does
not store printer profiles, resin settings, generated supports or slice layers.
See docs/chitubox-format.md for provenance and the application-test limitation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import tempfile
import time

import numpy as np
import trimesh

from .types import Model, Placement, RequestItem, matrix_for, matrices_for


MAGIC = 0xAB231243
HEADER_SIZE = 412
OBJECT_SIZE = 680
TRIANGLE_SIZE = 36
NO_SUPPORTS = 0xFFFFFFFF
MAX_FILE_SIZE = 0xFFFFFFFF
FORMAT_NAME = 'CHITUBOX Basic legacy geometry-only project'
APPLICATION_VERIFIED = False
CHITUBOX_EXPORT_TOLERANCE_MM = 0.01


@dataclass(frozen=True)
class ChituboxObject:
    """One decoded object's triangles in native, centred plate coordinates."""

    name: str
    mesh: trimesh.Trimesh
    plate_position: tuple[float, float, float]

    def vertices_in_build_box(self, size) -> np.ndarray:
        """Convert native plate coordinates to PowderNest's build-box frame."""
        dimensions = np.asarray(size, dtype=float)
        if dimensions.shape != (3,) or not np.isfinite(dimensions).all() or np.any(dimensions <= 0):
            raise ValueError('빌드 크기는 유한한 양수 3개여야 합니다')
        return np.asarray(self.mesh.vertices) + np.array([dimensions[0] / 2, dimensions[1] / 2, 0.])


def read_chitubox(path) -> list[ChituboxObject]:
    """Read model meshes from an AB231243 project, following its native table.

    Supports and other optional sections are not imported.  This independent
    table-and-range reader also checks the temporary file before publication.
    A supplied build size is needed to recover PowderNest coordinates because
    legacy CHITUBOX projects do not contain a machine/build-box profile.
    """
    path = Path(path)
    file_size = path.stat().st_size
    if not HEADER_SIZE <= file_size <= MAX_FILE_SIZE:
        raise ValueError('CHITUBOX 프로젝트 크기가 올바르지 않습니다')
    with path.open('rb') as stream:
        header = stream.read(HEADER_SIZE)
        magic, count, table = struct.unpack_from('<III', header)
        if magic != MAGIC:
            raise ValueError('CHITUBOX Basic 프로젝트의 파일 서명이 아닙니다')
        table_end = table + count * OBJECT_SIZE
        if count == 0 or table < HEADER_SIZE or table_end > file_size:
            raise ValueError('CHITUBOX 객체 테이블이 없거나 잘렸습니다')
        objects = []
        for index in range(count):
            stream.seek(table + index * OBJECT_SIZE)
            record = stream.read(OBJECT_SIZE)
            name = record[:256].split(b'\0', 1)[0].decode('utf-8', errors='replace')
            x, y, lift, _, mesh_offset, mesh_size = struct.unpack_from('<fffIII', record, 256)
            if not np.isfinite([x, y, lift]).all():
                raise ValueError('CHITUBOX 객체 위치가 유한하지 않습니다')
            if (mesh_size == 0 or mesh_size % TRIANGLE_SIZE or mesh_offset < table_end or
                    mesh_offset + mesh_size > file_size):
                raise ValueError('CHITUBOX 객체의 삼각형 범위가 올바르지 않습니다')
            stream.seek(mesh_offset)
            vertices = np.frombuffer(stream.read(mesh_size), dtype='<f4').astype(float).reshape((-1, 3))
            if not np.isfinite(vertices).all():
                raise ValueError('CHITUBOX 객체 정점이 유한하지 않습니다')
            vertices += [x, y, lift - vertices[:, 2].min()]
            faces = np.arange(len(vertices)).reshape((-1, 3))
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            objects.append(ChituboxObject(name, mesh, (x, y, lift)))
    return objects


def _object_name(model, index) -> bytes:
    # A bounded UTF-8 name with an intact, unique copy suffix.  Never serialize
    # embedded NULs or control characters as an early string terminator.
    stem = ''.join(c if c >= ' ' and c != '\x7f' else '_' for c in str(model.name))
    suffix = f'__object_{index + 1}.stl'.encode('utf-8')
    stem = stem.encode('utf-8')[:255 - len(suffix)].decode('utf-8', errors='ignore').encode('utf-8')
    return (stem or b'model') + suffix


def _validated(report, label):
    if not report.get('valid', False):
        errors = '; '.join(report.get('errors', []))
        raise ValueError(f'{label}: {errors}')


def _coordinate_rounding_errors_mm(original, encoded) -> tuple[float, float]:
    """Bound each corresponding vertex's full 3D serialization displacement."""
    difference = np.asarray(encoded, dtype=float) - np.asarray(original, dtype=float)
    coordinate_error = float(np.abs(difference).max())
    displacement = float(np.hypot.reduce(difference, axis=1).max())
    if not np.isfinite(displacement) or displacement > CHITUBOX_EXPORT_TOLERANCE_MM:
        raise ValueError(f'CHITUBOX float32 변환 정점 이동 오차 {displacement:.12g} mm가 '
                         f'내보내기 허용오차 {CHITUBOX_EXPORT_TOLERANCE_MM:g} mm를 초과했습니다')
    return coordinate_error, displacement


def export_chitubox(models, items, settings, result, path, *, progress=None) -> Path:
    """Validate, stage, then replace a native legacy Basic project and manifest.

    The original layout must pass the user's unchanged validation settings.
    Encoded float32 solids use a separate 0.01 mm numerical export tolerance,
    and no corresponding vertex may move more than 0.01 mm in 3D.  Geometry,
    surface intersection, containment and tessellation checks remain required.
    Empty placements are rejected before writing.

    ``<path>.json`` records the machine dimensions, coordinate conversion,
    source placement transforms, quantization error and both validation reports.
    The .chitubox file opens independently; the manifest is for traceability.
    Each replacement is atomic; an I/O failure between replacements can leave
    the newly written project with an older or missing companion manifest.
    """
    from .export_safety import protect_model_outputs
    protect_model_outputs(models,path,'chitubox')
    from .validation import validate
    from .geometry import _geometry_epsilon

    started = time.perf_counter()
    def notify(message):
        if progress is not None:
            try:
                progress(message)
            except Exception:
                pass

    path = Path(path)
    if path.suffix.lower() != '.chitubox':
        raise ValueError('프로젝트 파일 확장자는 .chitubox여야 합니다')
    notify('원본 배치의 형상·충돌·간격·경계를 검증하고 있습니다…')
    original_validation = validate(models, items, settings, result.placements)
    original_validation_seconds = time.perf_counter() - started
    _validated(original_validation, '원본 배치 검증에 실패하여 CHITUBOX로 내보낼 수 없습니다')
    if not result.placements:
        raise ValueError('CHITUBOX 프로젝트에 저장할 배치가 없습니다')
    export_settings = replace(settings, tolerance=CHITUBOX_EXPORT_TOLERANCE_MM)

    table_size = len(result.placements) * OBJECT_SIZE
    cursor = HEADER_SIZE + table_size
    if cursor > MAX_FILE_SIZE:
        raise ValueError('레거시 CHITUBOX 프로젝트는 4 GiB 미만이어야 합니다')
    table = bytearray(table_size)
    prepared, entries = [], []
    rounded_models, rounded_items, rounded_placements = {}, [], []
    geometry_groups, group_counts = {}, {}
    max_error, max_displacement = 0., 0.
    plate_origin = np.array([settings.size[0] / 2, settings.size[1] / 2, 0.])

    for index, (placement, matrix) in enumerate(zip(result.placements, matrices_for(models, result.placements))):
        notify(f'CHITUBOX 좌표 변환 {index + 1} / {len(result.placements)}개')
        model = models[placement.model_id]
        vertices = np.asarray(model.mesh.vertices, dtype=float)
        faces = np.asarray(model.mesh.faces)
        world = vertices @ matrix[:3, :3].T + matrix[:3, 3]
        low, high = world.min(axis=0), world.max(axis=0)
        centre = low + (high - low) / 2
        position = np.asarray([centre[0] - plate_origin[0], centre[1] - plate_origin[1], low[2]], dtype='<f4')
        # Use the already encoded header centre for XY.  Otherwise its rounding
        # and the local-vertex rounding accumulate.  A sub-ULP residual in the
        # local XY centre is intentional: readers translate these raw vertices.
        local_origin = np.array([float(position[0]) + plate_origin[0],
                                 float(position[1]) + plate_origin[1], centre[2]])
        local = np.asarray(world - local_origin, dtype='<f4')
        if not np.isfinite(local).all() or not np.isfinite(position).all():
            raise ValueError('CHITUBOX float32 좌표 범위를 초과했습니다')
        mesh_size = len(faces) * TRIANGLE_SIZE
        if cursor + mesh_size > MAX_FILE_SIZE:
            raise ValueError('레거시 CHITUBOX 프로젝트는 4 GiB 미만이어야 합니다')
        name = _object_name(model, index)
        start = index * OBJECT_SIZE
        table[start:start + len(name)] = name
        struct.pack_into('<fffIIII', table, start + 256, *position, NO_SUPPORTS, cursor, mesh_size, 0xFF4EAEEE)
        cursor += mesh_size
        prepared.append((local, faces))

        # Reconstruct what a native reader gets, including rounded header
        # coordinates and its Z-min seating rule.  Validate these actual solids.
        rounded = local.astype(float)
        rounded += [float(position[0]), float(position[1]), float(position[2]) - float(local[:, 2].min())]
        rounded += plate_origin
        error, displacement = _coordinate_rounding_errors_mm(world, rounded)
        max_error = max(max_error, error)
        max_displacement = max(max_displacement, displacement)
        # Share only byte-identical translated geometry, not merely a source
        # ID, bounding box or tolerance match. A quantisation change creates a
        # different group. Keep the old world-coordinate route if translation
        # cannot reconstruct every encoded vertex exactly, or would lower the
        # geometry predicate's numerical epsilon.
        anchor = rounded.min(axis=0)
        normalized = rounded - anchor
        shareable = (np.array_equal(normalized + anchor, rounded)
                     and _geometry_epsilon(normalized) == _geometry_epsilon(rounded))
        validation_vertices = normalized if shareable else rounded
        rounded_id = None
        if shareable:
            fingerprint = (sha256(normalized.tobytes()).digest(), sha256(faces.tobytes()).digest(),
                           float(model.tessellation_error))
            for candidate_id in geometry_groups.get(fingerprint, []):
                candidate = rounded_models[candidate_id]
                if (np.array_equal(candidate.mesh.vertices, normalized)
                        and np.array_equal(candidate.mesh.faces, faces)):
                    rounded_id = candidate_id
                    break
        if rounded_id is None:
            rounded_id = f'chitubox_object_{index}'
            rounded_mesh = trimesh.Trimesh(vertices=validation_vertices, faces=faces.copy(), process=False)
            rounded_models[rounded_id] = Model(rounded_id, name.decode('utf-8'), rounded_mesh,
                                             tessellation_error=model.tessellation_error, allowed_rotations=(0,))
            group_counts[rounded_id] = 0
            if shareable:
                geometry_groups.setdefault(fingerprint, []).append(rounded_id)
        copy_index = group_counts[rounded_id]
        group_counts[rounded_id] += 1
        rounded_placements.append(Placement(rounded_id, copy_index, 0, tuple(map(float, anchor))))
        entries.append({'object_index': index, 'name': name.decode('utf-8'), 'model_id': placement.model_id,
                        'copy_index': int(placement.copy_index), 'rotation_id': int(placement.rotation_id),
                        'matrix_canonical_mm_to_build_box_mm': matrix.tolist(),
                        'native_xy_centre_and_z_lift_mm': position.astype(float).tolist(),
                        'maximum_coordinate_rounding_mm': error,
                        'maximum_vertex_displacement_mm': displacement,
                        'validation_model_id': rounded_id, 'validation_copy_index': copy_index,
                        'original_tolerance_mm': settings.tolerance,
                        'export_tolerance_mm': CHITUBOX_EXPORT_TOLERANCE_MM})

    rounded_items = [RequestItem(key, count) for key, count in group_counts.items()]
    notify(f'저장 좌표의 고유 형상 {len(rounded_models)}종과 전체 부품 쌍의 간격·경계를 검증하고 있습니다…')
    encoded_started = time.perf_counter()
    encoded_validation = validate(rounded_models, rounded_items, export_settings, rounded_placements)
    encoded_validation_seconds = time.perf_counter() - encoded_started
    _validated(encoded_validation, 'CHITUBOX float32 변환 후 간격/경계/형상 검증 실패 (원본 배치는 변경하지 않았습니다)')

    header = bytearray(HEADER_SIZE)
    struct.pack_into('<III', header, 0, MAGIC, len(prepared), HEADER_SIZE)
    manifest = {'format': FORMAT_NAME, 'signature_hex': 'AB231243', 'coordinate_unit': 'mm',
                'application_version_requested': '1.1.0', 'application_version_verified': APPLICATION_VERIFIED,
                'build_box_mm': list(settings.size),
                'native_origin_in_build_box_mm': plate_origin.tolist(),
                'required_machine_setup': 'Set the same build-box dimensions in CHITUBOX; legacy projects do not store a printer profile.',
                'rotation_storage': 'baked into each independent object mesh',
                'maximum_coordinate_rounding_mm': max_error,
                'maximum_vertex_displacement_mm': max_displacement,
                'original_tolerance_mm': settings.tolerance,
                'export_tolerance_mm': CHITUBOX_EXPORT_TOLERANCE_MM,
                'encoded_numeric_rules': {
                    'vertex_rule': 'Each corresponding vertex moves at most 0.01 mm in Euclidean distance, including native header rounding.',
                    'pair_rule': 'distance >= gap + allowance_A + allowance_B - 0.01 mm; surface intersections, contact and solid containment are rejected.',
                    'wall_rule': 'XY inside [wall_gap + allowance - 0.01 mm, size_xy - wall_gap - allowance + 0.01 mm]; Z inside [allowance - 0.01 mm, max_z - allowance + 0.01 mm].'},
                'requested_count': sum(int(item.quantity) for item in items), 'placed_count': len(entries),
                'partial': not original_validation.get('complete', False),
                'objects': entries, 'original_validation': original_validation,
                'encoded_geometry_validation': encoded_validation}
    manifest['export_performance'] = {
        'objects': len(entries), 'unique_encoded_geometries': len(rounded_models),
        'shared_encoded_copies': len(entries) - len(rounded_models),
        'original_validation_seconds': original_validation_seconds,
        'encoded_validation_seconds': encoded_validation_seconds,
        'prewrite_seconds': time.perf_counter() - started,
        'sharing_rule': 'exact normalized vertex/face equality, exact world reconstruction, identical geometry epsilon and CAD allowance',
        'all_encoded_pairs_checked': encoded_validation['checked_pairs'] == len(entries) * (len(entries) - 1) // 2}
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n'

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        notify('프로젝트 파일을 기록하고 다시 읽어 좌표를 확인하고 있습니다…')
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.powdernest-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            staged.append(temporary)
            stream.write(header)
            stream.write(table)
            for local, faces in prepared:
                for first in range(0, len(faces), 65536):
                    stream.write(local[faces[first:first + 65536]].tobytes(order='C'))
        decoded = read_chitubox(temporary)
        if len(decoded) != len(prepared):
            raise ValueError('CHITUBOX 저장 후 객체 수 검증에 실패했습니다')
        for index, obj in enumerate(decoded):
            local, faces = prepared[index]
            position = entries[index]['native_xy_centre_and_z_lift_mm']
            expected = local.astype(float)
            expected += [position[0], position[1], position[2] - float(local[:, 2].min())]
            expected += plate_origin
            expected = expected[faces].reshape((-1, 3))
            if not np.array_equal(obj.vertices_in_build_box(settings.size), expected):
                raise ValueError('CHITUBOX 저장 후 좌표 검증에 실패했습니다')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.powdernest-', suffix='.json.tmp', delete=False) as stream:
            manifest_temporary = Path(stream.name)
            staged.append(manifest_temporary)
            stream.write(manifest_text)
        os.replace(temporary, path)
        os.replace(manifest_temporary, Path(str(path) + '.json'))
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
    return path
