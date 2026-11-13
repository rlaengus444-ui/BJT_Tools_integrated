"""Headless packing operations; no Tk reads, display geometry or training."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from powdernest.cancellation import Cancelled, cancellation_scope
from powdernest.export_safety import capture_input_identities, output_targets, protect_destinations
from powdernest.geometry import GeometryError, load_model
from powdernest.optimizer import optimize
from powdernest.rl import default_policy
from powdernest.types import RequestItem, Settings


@dataclass
class PackingSnapshot:
    models: dict
    items: list
    settings: Settings
    result: object
    input_identities: tuple = ()






def input_records(rows):
    """Only serializable input descriptors, never meshes or result state."""
    return [dict(id=row['id'],path=row['path'],unit=row['unit'],quantity=row['quantity'],
                 rotations=list(row['rotations'])) for row in rows]


def load_inputs(records, cancel, progress=None):
    loaded, failures = {}, []
    try:
        with cancellation_scope(cancel):
            for i, record in enumerate(records):
                if progress:
                    progress(f"모델 불러오기 {i+1}/{len(records)} · {Path(record['path']).name}")
                try:
                    model = load_model(record['path'], stl_unit=record['unit'], model_id=record['id'])
                except GeometryError as exc:
                    if Path(record['path']).suffix.lower() != '.stl':
                        failures.append((record['id'], str(exc))); continue
                    try:
                        from powdernest.inspection import preview_stl
                        model = preview_stl(record['path'], stl_unit=record['unit'],
                                            model_id=record['id'], reason=str(exc))
                    except Cancelled:
                        raise
                    except Exception as error:
                        failures.append((record['id'], str(error))); continue
                except (OSError, ValueError) as exc:
                    failures.append((record['id'], str(exc))); continue
                model.allowed_rotations = tuple(record['rotations'])
                loaded[record['id']] = model
    except Cancelled:
        # A cancelled import batch must not commit incomplete geometry.
        return {}, [], True
    return ({}, [], True) if cancel.is_set() else (loaded, failures, False)


def calculate(models, records, settings, cancel, progress=None):
    active = [row for row in records if row['quantity']]
    absent = [row['path'] for row in active if row['id'] not in models]
    if absent:
        raise ValueError('아직 읽지 못한 모델이 있습니다. 파일 다시 읽기를 실행하세요.\n'+'\n'.join(absent))
    if not active:
        raise ValueError('수량이 1개 이상인 모델을 추가하세요.')
    selected = {row['id']: models[row['id']] for row in active}
    blocked = [m.name for m in selected.values() if m.diagnostics.get('inspection_only')]
    if blocked:
        raise ValueError('미리보기 전용 STL은 배치할 수 없습니다. 정상 솔리드가 필요합니다.\n'+'\n'.join(blocked))
    items = [RequestItem(row['id'], row['quantity']) for row in active]
    # No cancellation scope here: optimize must independently validate its
    # best partial result after cancellation. A fresh policy never trains.
    identities=capture_input_identities([row['path'] for row in records]+[m.source_path for m in models.values()])
    result = optimize(selected, items, settings, cancel=cancel, progress=progress,
                      policy=default_policy(), learn=False)
    return PackingSnapshot(selected, items, settings, result, identities)


def save_snapshot(snapshot, destination, kind='standard', progress=None):
    if not snapshot.result.validation.get('valid'):
        raise ValueError('검증을 통과한 결과만 저장할 수 있습니다.')
    identities=snapshot.input_identities or capture_input_identities(model.source_path for model in snapshot.models.values())
    protect_destinations(identities,output_targets(destination,kind))
    if kind == 'chitubox':
        from powdernest.chitubox import export_chitubox
        return export_chitubox(snapshot.models, snapshot.items, snapshot.settings,
                               snapshot.result, destination, progress=progress)
    if kind != 'standard':
        raise ValueError('지원하지 않는 내보내기 형식입니다.')
    from powdernest.io import export_result
    return export_result(snapshot.models, snapshot.items, snapshot.settings, snapshot.result, destination)
