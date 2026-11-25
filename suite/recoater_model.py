"""Cross-sectional model from the offline recoater simulator.

Lengths are in mm, sectional areas in mm², densities in g/cm³, and the angle
of repose in degrees. The cross section runs along the travel direction and
represents a unit width along the roller axes. The bed surface before feeding
is the height datum. Each profile's horizontal origin is its roller axis.

The model assumes each pile stays at loose density with a planar repose slope.
Above a roller center the wall follows the vertical tangent. At contact the
compacting pile is held at capacity and further excess is counted as diverted.
The spreading pile holds its last section after its roller exits the bed.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cached_property
import math


_ANGLE_ERROR = '안식각은 0°보다 크고 90°보다 작은 값으로 입력하세요.'
_SCALE_ERROR = '입력값의 단위와 크기를 확인하세요.'
_ROLLER_SCALE_ERROR = '롤러와 피딩층의 치수를 확인하세요.'
_SECTION_SCALE_ERROR = '단면의 크기를 계산할 수 없습니다. 길이와 밀도 단위를 확인하세요.'


@dataclass(frozen=True)
class Parameters:
    layer: float
    loose_density: float
    bed_density: float
    feed_ratio: float
    angle: float
    bed_length: float
    compact_diameter: float
    spread_on: bool
    spread_diameter: float
    spacing: float
    clearance: float


SHEET_EXAMPLE = Parameters(
    layer=0.10, loose_density=1.52, bed_density=1.75, feed_ratio=2,
    angle=35, bed_length=250, compact_diameter=0, spread_on=True,
    spread_diameter=0, spacing=5, clearance=0.18,
)
ROLLER_EXAMPLE = replace(SHEET_EXAMPLE, compact_diameter=10, spread_diameter=6, spacing=9)


@dataclass(frozen=True)
class Profile:
    height: float
    rise: float
    peak_x: float
    toe_x: float
    pile_area: float


@dataclass(frozen=True)
class State(Profile):
    x: float
    contacted: bool
    spreader: Profile | None
    spread_distance: float
    diverted_area: float


def _number(value: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except OverflowError:
            pass
    return math.nan


def _checked_input(value: float, label: str, *, allow_zero: bool = False) -> float:
    value = _number(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        requirement = '0 이상의 수' if allow_zero else '양수'
        raise ValueError(f'{label}에 {requirement}를 입력하세요.')
    return value


def _checked_scale(value: float, *, positive: bool = False) -> float:
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(_SCALE_ERROR)
    return value


def _arc_reach(height: float, bottom: float, radius: float) -> float:
    depth = min(max(0.0, height - bottom), radius)
    diameter_minus_depth = 2 * radius - depth
    squared = depth * diameter_minus_depth
    if depth > 0 and (not math.isfinite(squared) or squared == 0):
        return math.sqrt(depth) * math.sqrt(diameter_minus_depth)
    return math.sqrt(squared)


def _arc_primitive(height: float, bottom: float, radius: float) -> float:
    """Integral of the lower forward quadrant wall from the roller bottom."""
    if not radius or height <= bottom:
        return 0.0
    depth = min(height - bottom, radius)
    angle = 2 * math.asin(math.sqrt(depth / (2 * radius)))
    angle_squared = angle * angle
    if angle < 0.01:
        # The series avoids cancellation in a - sin(a) * cos(a) near the bottom.
        segment = angle * angle_squared * (
            2 / 3 - 2 * angle_squared / 15 + 4 * angle_squared * angle_squared / 315
        )
    else:
        segment = angle - math.sin(angle) * math.cos(angle)
    return radius * radius * segment / 2 + radius * max(0.0, height - bottom - radius)


def _inverse_increasing(
    function: Callable[[float], float], target: float, low: float, high: float,
) -> float:
    for _ in range(48):
        midpoint = low + (high - low) / 2
        if function(midpoint) < target:
            low = midpoint
        else:
            high = midpoint
    return low + (high - low) / 2


@dataclass(frozen=True)
class _PileGeometry:
    base: float
    bottom: float
    radius: float
    slope: float
    base_integral: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        integral = _arc_primitive(self.base, self.bottom, self.radius)
        if not math.isfinite(integral):
            raise ValueError(_ROLLER_SCALE_ERROR)
        object.__setattr__(self, 'base_integral', integral)

    def wall(self, height: float) -> float:
        return _arc_reach(height, self.bottom, self.radius)

    def area_at_height(self, height: float) -> float:
        # At each height the pile extends from the wall to the repose slope.
        rise = max(0.0, height - self.base)
        if rise == 0:
            return 0.0
        triangle = rise * rise / (2 * self.slope)
        integral = _arc_primitive(height, self.bottom, self.radius)
        arc_correction = rise * self.wall(height) - (integral - self.base_integral)
        if not math.isfinite(arc_correction):
            raise ValueError(_ROLLER_SCALE_ERROR)
        return triangle + max(0.0, arc_correction)

    def profile(self, pile_area: float, known_height: float | None = None) -> Profile:
        height = self.base if known_height is None else known_height
        if known_height is None and pile_area > 0:
            twice_slope = 2 * self.slope
            squared_rise = twice_slope * pile_area
            if math.isfinite(squared_rise) and squared_rise > 0:
                triangle_rise = math.sqrt(squared_rise)
            else:
                triangle_rise = math.sqrt(twice_slope) * math.sqrt(pile_area)
            triangle_height = self.base + triangle_rise
            if not math.isfinite(triangle_height):
                raise ValueError(_SECTION_SCALE_ERROR)
            height = (triangle_height if self.radius == 0 else
                      _inverse_increasing(self.area_at_height, pile_area, self.base, triangle_height))
        rise = max(0.0, height - self.base)
        peak_x = self.wall(height)
        return Profile(height, rise, peak_x, peak_x + rise / self.slope, pile_area)


@dataclass(frozen=True)
class Model:
    parameters: Parameters
    slope: float
    rc: float
    rs: float
    feed: float
    base: float
    required: float
    balance: float
    excess: float
    deposit: float
    swept_thickness: float
    contact_x: float
    contact_height: float
    capacity: float
    tangent_x: float
    tangent_z: float
    _compact: _PileGeometry = field(repr=False)
    _spread: _PileGeometry | None = field(repr=False)

    def compact_wall(self, height: float) -> float:
        return self._compact.wall(height)

    def spread_wall(self, height: float) -> float:
        """Return the reach from the spreading axis, or zero if disabled."""
        return self._spread.wall(height) if self._spread is not None else 0.0

    def at(self, distance: float) -> State:
        """Evaluate the section at a finite compactor distance, clamped to the bed."""
        distance = _number(distance)
        if not math.isfinite(distance):
            raise ValueError('컴팩팅 진행거리에 유한한 수를 입력하세요.')
        length = float(self.parameters.bed_length)
        x = min(max(0.0, distance), length)
        supplied_area = x * self.excess
        pile_area = min(supplied_area, self.capacity)
        contacted = self._spread is not None and self.excess > 0 and x >= self.contact_x
        compact = self._compact.profile(pile_area, self.contact_height if contacted else None)

        spread_distance = 0.0
        spreader = None
        if self._spread is not None:
            # The lead roller has already swept the distance between the axes
            # at compactor entry, and receives no feed beyond the bed.
            spread_distance = min(length, x + float(self.parameters.spacing))
            spreader = self._spread.profile(self.swept_thickness * spread_distance)
        return State(
            height=compact.height, rise=compact.rise, peak_x=compact.peak_x,
            toe_x=compact.toe_x, pile_area=pile_area, x=x, contacted=contacted,
            spreader=spreader, spread_distance=spread_distance,
            diverted_area=max(0.0, supplied_area - pile_area),
        )

    @cached_property
    def end(self) -> State:
        return self.at(self.parameters.bed_length)


def make_model(parameters: Parameters) -> Model:
    """Build and validate a section model from the supplied process conditions."""
    p = parameters
    layer = _checked_input(p.layer, '레이어 두께')
    loose_density = _checked_input(p.loose_density, '겉보기 밀도')
    bed_density = _checked_input(p.bed_density, '베드밀도')
    feed_ratio = _checked_input(p.feed_ratio, '피딩배수', allow_zero=True)
    bed_length = _checked_input(p.bed_length, '베드 길이')
    compact_diameter = _checked_input(p.compact_diameter, '컴팩팅롤러 지름', allow_zero=True)
    angle = _number(p.angle)
    if not math.isfinite(angle) or not 0 < angle < 90:
        raise ValueError(_ANGLE_ERROR)
    spread_diameter = spacing = clearance = 0.0
    if p.spread_on:
        spread_diameter = _checked_input(p.spread_diameter, '스프래딩롤러 지름', allow_zero=True)
        spacing = _checked_input(p.spacing, '롤러 축 간 수평거리', allow_zero=True)
        clearance = _checked_input(p.clearance, '스프래딩 하단 간극', allow_zero=True)

    angle_rad = angle * math.pi / 180
    slope = _checked_scale(math.tan(angle_rad), positive=True)
    rc = _checked_scale(compact_diameter / 2, positive=compact_diameter > 0)
    rs = _checked_scale(spread_diameter / 2, positive=spread_diameter > 0)
    feed = _checked_scale(feed_ratio * layer, positive=feed_ratio > 0)
    base = min(feed, clearance) if p.spread_on else feed
    swept_thickness = feed - base
    required = _checked_scale(layer * bed_density / loose_density, positive=True)
    balance = base - required
    excess = max(0.0, balance)
    available_deposit = _checked_scale(base * loose_density / bed_density, positive=base > 0)
    deposit = min(layer, available_deposit)
    _checked_scale(excess * bed_length, positive=excess > 0)
    _checked_scale(swept_thickness * bed_length, positive=swept_thickness > 0)

    compact = _PileGeometry(base, layer, rc, slope)
    spread = _PileGeometry(feed, clearance, rs, slope) if p.spread_on else None
    contact_x = contact_height = capacity = math.inf
    tangent_x = tangent_z = math.nan
    if spread is not None:
        axis_distance = math.hypot(spacing, clearance + rs - layer - rc)
        geometry_tolerance = 1e-10 * max(1.0, rc + rs, spacing)
        if rc > 0 and rs > 0 and axis_distance < rc + rs - geometry_tolerance:
            raise ValueError('두 롤러의 외경이 겹칩니다. 지름, 축 간 수평거리, 하단 간극을 확인하세요.')
        tangent_x = spacing - rs * math.sin(angle_rad)
        tangent_z = _checked_scale(clearance + rs * (1 - math.cos(angle_rad)))
        intercept = _checked_scale(tangent_z + slope * tangent_x)

        def peak_intercept(height: float) -> float:
            return height + slope * compact.wall(height)

        if intercept <= peak_intercept(base):
            contact_height = base
        else:
            contact_height = (intercept if rc == 0 else
                              _inverse_increasing(peak_intercept, intercept, base, intercept))
        if tangent_x < compact.wall(contact_height) - geometry_tolerance:
            raise ValueError('스프래딩 접점이 컴팩팅롤러 앞에 오도록 축 간 거리와 지름을 확인하세요.')
        capacity = _checked_scale(compact.area_at_height(contact_height), positive=contact_height > base)
        if excess > 0:
            contact_x = _checked_scale(capacity / excess, positive=capacity > 0)

    model = Model(
        parameters=p, slope=slope, rc=rc, rs=rs, feed=feed, base=base, required=required,
        balance=balance, excess=excess, deposit=deposit, swept_thickness=swept_thickness,
        contact_x=contact_x, contact_height=contact_height, capacity=capacity,
        tangent_x=tangent_x, tangent_z=tangent_z, _compact=compact, _spread=spread,
    )
    end = model.end
    end_profiles = (end,) if end.spreader is None else (end, end.spreader)
    for profile in end_profiles:
        if (not all(math.isfinite(value) for value in (profile.height, profile.toe_x, profile.pile_area)) or
                (profile.pile_area > 0 and profile.rise == 0)):
            raise ValueError(_SECTION_SCALE_ERROR)
    return model


def build_curve(model: Model) -> tuple[tuple[float, float, float | None], ...]:
    """Sample both heights on a uniform grid plus contact and spreader exit."""
    length = float(model.parameters.bed_length)
    intervals = 240
    if math.isfinite(length * intervals):
        knots = {length * index / intervals for index in range(intervals)}
    else:
        knots = {length * (index / intervals) for index in range(intervals)}
    knots.add(length)
    if 0 < model.contact_x < length:
        knots.add(model.contact_x)
    if model.parameters.spread_on:
        knots.add(max(0.0, length - float(model.parameters.spacing)))
    points = []
    for x in sorted(knots):
        state = model.at(x)
        spread_height = state.spreader.height if state.spreader is not None else None
        points.append((x, state.height, spread_height))
    return tuple(points)
