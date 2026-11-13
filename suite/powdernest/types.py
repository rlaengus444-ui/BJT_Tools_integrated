"""Shared millimetre-based domain records; mesh coordinates retain the input origin."""
from dataclasses import dataclass, field
from numbers import Integral, Real
from typing import Any
import numpy as np
from .rotations import ROTATIONS


def _rotation_index(value: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or not 0 <= value < len(ROTATIONS):
        raise ValueError('회전 ID는 0부터 23까지의 정수여야 합니다')
    return int(value)


def _finite_number(value, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{label}에는 유한한 수가 필요합니다')
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f'{label}에는 유한한 수가 필요합니다') from exc
    if not np.isfinite(number):
        raise ValueError(f'{label}에는 유한한 수가 필요합니다')
    return number


@dataclass
class Model:
    model_id: str
    name: str
    mesh: Any
    source_path: str = ''
    source_unit: str = 'mm'
    tessellation_error: float = 0.0
    original_shape: Any = None
    diagnostics: dict = field(default_factory=dict)
    allowed_rotations: tuple[int, ...] = tuple(range(24))
    _bounds: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError('모델 ID는 비어 있지 않은 문자열이어야 합니다')
        self.tessellation_error = _finite_number(self.tessellation_error, '삼각화 오차')
        if self.tessellation_error < 0:
            raise ValueError('삼각화 오차는 음수가 아니어야 합니다')
        try:
            rotations = tuple(dict.fromkeys(_rotation_index(r) for r in self.allowed_rotations))
        except TypeError as exc:
            raise ValueError('허용 회전 목록이 필요합니다') from exc
        if not rotations:
            raise ValueError('허용 회전이 하나 이상 필요합니다')
        self.allowed_rotations = rotations

    def bounds_for(self, rotation_id: int) -> np.ndarray:
        rotation_id = _rotation_index(rotation_id)
        if rotation_id not in self.allowed_rotations:
            raise ValueError('허용하지 않은 회전입니다')
        # Mesh records are public and mutable.  Neither an old cache entry nor
        # Trimesh's own cached bounds may determine a transform at export time.
        # A signed permutation rotates an AABB exactly, without rotating every
        # vertex; fresh vertex extrema therefore suffice for all 24 rotations.
        vertices = np.asarray(self.mesh.vertices, dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all():
            raise ValueError('메시에 유한한 3차원 정점이 필요합니다')
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        rotation = ROTATIONS[rotation_id]
        positive, negative = np.maximum(rotation, 0), np.minimum(rotation, 0)
        bounds = np.array([positive @ low + negative @ high,
                           positive @ high + negative @ low])
        bounds.setflags(write=False)
        self._bounds[rotation_id] = bounds
        return bounds

@dataclass(frozen=True)
class Placement:
    model_id: str
    copy_index: int
    rotation_id: int
    position: tuple[float, float, float]

@dataclass(frozen=True)
class RequestItem:
    model_id: str
    quantity: int

@dataclass
class Settings:
    size: tuple[float, float, float] = (100.,100.,100.)
    gap: float = 1.
    wall_gap: float = 0.
    time_limit: float = 10.
    seed: int = 42
    tolerance: float = 1e-6
    search_step: float = 5.
    mode: str = 'mesh'
    iterations: int | None = None
    learning: bool = False
    precision: bool = False
    precision_time: float = 10.
    xy_balance: str = 'off'
    xy_balance_time: float = 3.
    early_stop: bool = True
    check_interlocking: bool = True

    def boundary_margins(self, tessellation_error=0.) -> np.ndarray:
        """XY side-wall gap only; the CAD envelope allowance remains XYZ."""
        error = _finite_number(tessellation_error, '삼각화 오차')
        if error < 0:
            raise ValueError('삼각화 오차는 음수가 아니어야 합니다')
        return np.array([self.wall_gap + error, self.wall_gap + error, error])

    def __post_init__(self):
        try:
            if isinstance(self.size, (str, bytes)) or len(self.size) != 3:
                raise ValueError('빌드 크기는 유한한 치수 3개여야 합니다')
            self.size = tuple(_finite_number(x, '빌드 크기') for x in self.size)
        except (TypeError, OverflowError) as exc:
            raise ValueError('빌드 크기는 유한한 치수 3개여야 합니다') from exc
        for name in ('gap', 'wall_gap', 'time_limit', 'tolerance', 'search_step', 'precision_time', 'xy_balance_time'):
            setattr(self, name, _finite_number(getattr(self, name), name))
        if min(self.size)<=0 or self.gap<0 or self.wall_gap<0 or self.time_limit<0 or self.precision_time<0 or self.xy_balance_time<0 or self.tolerance<=0 or self.search_step<=0:
            raise ValueError('양수 치수/허용오차/탐색 간격 및 음수가 아닌 간격/시간이 필요합니다')
        if isinstance(self.seed, (bool, np.bool_)) or not isinstance(self.seed, Integral) or self.seed < 0:
            raise ValueError('시드는 음수가 아닌 정수여야 합니다')
        self.seed = int(self.seed)
        if self.mode not in ('mesh','aabb'):
            raise ValueError('mode must be mesh or aabb')
        if type(self.learning) is not bool:
            raise ValueError('learning must be a boolean')
        if type(self.precision) is not bool:
            raise ValueError('precision must be a boolean')
        if type(self.early_stop) is not bool:
            raise ValueError('early_stop must be a boolean')
        if type(self.check_interlocking) is not bool:
            raise ValueError('check_interlocking must be a boolean')
        if self.xy_balance not in ('off', 'center', 'symmetric'):
            raise ValueError('xy_balance must be off, center or symmetric')
        if self.iterations is not None:
            if isinstance(self.iterations, (bool, np.bool_)) or not isinstance(self.iterations, Integral) or self.iterations < 0:
                raise ValueError('iterations must be a nonnegative integer')
            self.iterations = int(self.iterations)

@dataclass
class Result:
    placements: list[Placement] = field(default_factory=list)
    unplaced: dict[str,int] = field(default_factory=dict)
    status: str = 'not_found'
    elapsed: float = 0.
    height: float = 0.
    seed: int = 42
    iterations: int = 0
    initial_score: tuple = ()
    validation: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


def _matrix_values(model, p):
    if p.model_id != model.model_id:
        raise ValueError('배치와 모델 ID가 일치하지 않습니다')
    rotation_id = _rotation_index(p.rotation_id)
    try:
        if isinstance(p.position, (str, bytes)) or len(p.position) != 3:
            raise ValueError('위치에는 유한한 좌표 3개가 필요합니다')
        position = np.array([_finite_number(x, '위치') for x in p.position])
    except TypeError as exc:
        raise ValueError('위치에는 유한한 좌표 3개가 필요합니다') from exc
    return rotation_id, position


def _matrix_from_bounds(rotation_id, position, bounds):
    result = np.eye(4)
    result[:3,:3] = ROTATIONS[rotation_id]
    result[:3,3] = position - bounds[0]
    return result


def matrix_for(model: Model, p: Placement) -> np.ndarray:
    rotation_id, position = _matrix_values(model, p)
    return _matrix_from_bounds(rotation_id, position, model.bounds_for(rotation_id))


def matrices_for(models, placements):
    """Operation-local bounds cache for an unchanged set of input models."""
    bounds = {}
    for p in placements:
        model = models[p.model_id]
        rotation_id, position = _matrix_values(model, p)
        key = (p.model_id, rotation_id)
        if key not in bounds:
            bounds[key] = model.bounds_for(rotation_id)
        if rotation_id not in model.allowed_rotations:
            raise ValueError('허용하지 않은 회전입니다')
        yield _matrix_from_bounds(rotation_id, position, bounds[key])
