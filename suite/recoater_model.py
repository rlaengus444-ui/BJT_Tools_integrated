"""Cross-sectional model from the offline recoater simulator.

Lengths are in mm, sectional areas in mm², densities in g/cm³, and the angle
of repose in degrees. The cross section runs along the travel direction and
represents a unit width along the roller axes. The bed surface before feeding
is the height datum. Each profile's horizontal origin is its roller axis.

The model assumes each pile stays at loose density with a planar repose slope.
Above a roller center the wall follows the vertical tangent. At contact the
compacting pile is limited to capacity and further excess is counted as diverted.
The spreading pile holds its last section after its roller exits the bed. The
physical pass fixes the rear at world x=0 and the front at x=bed length.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cached_property
import math

from recoater_aor import (
    AorFeed, AorParameters, BedHistory, BedState, _checked_input, _checked_scale, _number,
    event_starts, make_aor_feed, make_bed_history,
)


_ANGLE_ERROR = '안식각은 0°보다 크고 90°보다 작은 값으로 입력하세요.'
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
    aor: AorParameters | None = None


SHEET_EXAMPLE = Parameters(
    layer=0.10, loose_density=1.52, bed_density=1.75, feed_ratio=2,
    angle=45, bed_length=250, compact_diameter=0, spread_on=True,
    spread_diameter=0, spacing=5, clearance=0.18,
)
ROLLER_EXAMPLE = replace(SHEET_EXAMPLE, compact_diameter=20, spread_diameter=20, spacing=46)


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
    compact_started: bool = True
    compact_distance: float = 0.0
    fed_distance: float = 0.0
    feeding: bool = False
    local: BedState | None = None
    flux: float = 0.0
    phase: str = 'compact'
    world_x: float = 0.0
    compact_world_x: float = 0.0
    spread_world_x: float | None = None
    outlet_world_x: float | None = None
    elapsed: float | None = None
    feed_time: float | None = None

    @property
    def progress(self) -> float:
        return self.x


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
    aor: AorFeed | None
    history: BedHistory | None = field(repr=False)
    two_pass: bool
    feed_direction: str
    lead: float | None
    start_x: float
    travel_end: float
    turn_progress: float | None
    feed_end: float
    compact_start: float
    spread_start: float | None
    contact_distance: float

    def compact_wall(self, height: float) -> float:
        return self._compact.wall(height)

    def spread_wall(self, height: float) -> float:
        """Return the reach from the spreading axis, or zero if disabled."""
        return self._spread.wall(height) if self._spread is not None else 0.0

    def feed_world(self, distance: float) -> float:
        """Convert local feeding distance to fixed bed coordinates."""
        if self.two_pass and self.feed_direction == 'front_to_rear':
            return float(self.parameters.bed_length) - distance
        return distance

    def feed_distance(self, world_x: float) -> float:
        return self.feed_world(world_x)

    def return_world(self, distance: float) -> float:
        """Convert a roller's front-to-rear travel distance to world coordinates."""
        return float(self.parameters.bed_length) - distance if self.two_pass else distance

    def return_distance(self, world_x: float) -> float:
        return self.return_world(world_x)

    def layer_height_at(self, world_x: float, key: str) -> float:
        """Read the physical thickness of a deposited, passed, or formed layer."""
        p = self.parameters
        length = float(p.bed_length)
        if key == 'raw':
            distance = min(length, max(0.0, self.feed_distance(world_x)))
            return self.feed if self.aor is None else self.aor.run.height_at(distance)
        if key not in ('passed', 'formed'):
            raise ValueError(f'Unknown bed layer {key!r}')
        if self.history is None:
            return self.base if key == 'passed' else self.deposit
        distance = min(length, max(0.0, self.return_distance(world_x)))
        height = self.history.layer_at(distance, key)
        return height if key == 'passed' else height * p.loose_density / p.bed_density

    def _compact_profile(self, area: float, contacted: bool = False) -> Profile:
        known_height = self.contact_height if contacted or area == self.capacity else None
        return self._compact.profile(area, known_height)

    def _spread_profile_at(self, distance: float) -> Profile:
        area = (self.history.pile_area_at(distance, spreader=True) if self.history is not None else
                self.swept_thickness * distance)
        return self._spread.profile(area)

    def pile_height_at(self, is_spreader: bool, bed_position: float) -> float:
        """Return a pile height at that roller's own local position on the bed."""
        distance = min(float(self.parameters.bed_length), max(0.0, bed_position))
        if is_spreader:
            if self._spread is None:
                raise ValueError('스프래딩롤러가 비활성화되어 있습니다.')
            return self._spread_profile_at(distance).height
        area = (self.history.pile_area_at(distance, spreader=False) if self.history is not None else
                min(distance * self.excess, self.capacity))
        return self._compact_profile(area).height

    def at(self, distance: float) -> State:
        """Evaluate the section at finite progress, clamped to the operation."""
        distance = _number(distance)
        if not math.isfinite(distance):
            raise ValueError('컴팩팅 진행거리에 유한한 수를 입력하세요.')
        length = float(self.parameters.bed_length)
        x = min(max(self.start_x, distance), self.travel_end)
        outbound = self.turn_progress is not None and x < self.turn_progress
        returning = self.turn_progress is not None and not outbound
        compact_position = length if x == self.travel_end else x - self.compact_start
        compact_started = compact_position >= 0 and not outbound
        compact_distance = max(0.0, compact_position) if compact_started else 0.0

        if outbound:
            phase = 'feed'
            outlet_world_x = self.feed_world(x)
            compact_world_x = outlet_world_x + self.lead
            world_x = outlet_world_x
        else:
            phase = 'compact' if returning or self.lead is None else 'combined'
            compact_world_x = self.return_world(compact_position)
            outlet_world_x = None
            if self.lead is not None:
                outlet_position = x - (self.turn_progress or self.start_x)
                outlet_world_x = self.return_world(outlet_position)
            world_x = compact_world_x
        spread_world_x = None
        if self._spread is not None:
            spacing = float(self.parameters.spacing)
            spread_world_x = compact_world_x - spacing if self.two_pass else compact_world_x + spacing

        fed_distance = (min(max(0.0, x - self.start_x), length)
                        if self.lead is not None and not returning else length)
        feeding = self.lead is not None and not returning and x < self.feed_end and self.feed > 0
        elapsed = (x - self.start_x) / self.aor.speed if self.aor is not None else None
        feed_time = fed_distance / self.aor.speed if self.aor is not None else None

        local = self.history.at(compact_distance) if self.history is not None else None
        supplied_area = compact_distance * self.excess
        pile_area = local.compact_pile if local is not None else min(supplied_area, self.capacity)
        contacted = compact_started and x >= self.contact_x and self._spread is not None
        if contacted and local is not None:
            contacted = pile_area >= self.capacity - 1e-10 * max(1.0, self.capacity)
        compact = self._compact_profile(pile_area, contacted and local is None)

        spread_distance = 0.0
        spreader = None
        if self.spread_start is not None and not outbound and x >= self.spread_start:
            spread_distance = length if x >= self.spread_start + length else x - self.spread_start
            spreader = self._spread_profile_at(spread_distance)
        return State(
            height=compact.height, rise=compact.rise, peak_x=compact.peak_x,
            toe_x=compact.toe_x, pile_area=pile_area, x=x, contacted=contacted,
            spreader=spreader, spread_distance=spread_distance,
            diverted_area=local.overflow if local is not None else max(0.0, supplied_area - pile_area),
            compact_started=compact_started, compact_distance=compact_distance, fed_distance=fed_distance,
            feeding=feeding, local=local, flux=self.aor.run.flow_at(feed_time) if feeding and self.aor else 0.0,
            phase=phase, world_x=world_x, compact_world_x=compact_world_x, spread_world_x=spread_world_x,
            outlet_world_x=outlet_world_x, elapsed=elapsed, feed_time=feed_time,
        )

    @cached_property
    def end(self) -> State:
        return self.at(self.travel_end)

    @cached_property
    def max_compact(self) -> Profile:
        return self._compact_profile(self.history.max_compact) if self.history is not None else self.end

    @cached_property
    def max_spread(self) -> Profile | None:
        if self.history is not None and self._spread is not None:
            return self._spread.profile(self.history.max_spread)
        return self.end.spreader


def make_model(
    parameters: Parameters, continuous_lead: float | None = None, *,
    two_pass: bool = False, feed_direction: str = 'rear_to_front',
) -> Model:
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

    if feed_direction not in ('rear_to_front', 'front_to_rear'):
        raise ValueError('피딩 방향을 확인하세요.')
    aor = make_aor_feed(p.aor, layer, loose_density, feed_ratio, spacing) if p.aor is not None else None
    lead = aor.lead if aor is not None else continuous_lead
    if lead is not None:
        lead = _checked_input(lead, '토출점 선행 거리', allow_zero=True)
    elif two_pass:
        lead = spacing
    if two_pass and lead < spacing:
        raise ValueError('토출점 선행 거리는 선행 롤러의 축 간 거리 이상으로 설정하세요.')

    reverse_feed = two_pass and feed_direction == 'rear_to_front'
    turn_progress = bed_length if reverse_feed else None
    start_x = 0.0 if two_pass or lead is None else -lead
    compact_start = (turn_progress or 0.0) + lead if two_pass else 0.0
    spread_start = compact_start - spacing if p.spread_on else None
    travel_end = _checked_scale(compact_start + bed_length, positive=True)
    feed_end = _checked_scale(start_x + bed_length)
    _checked_scale(travel_end - compact_start, positive=True)
    travel = _checked_scale(travel_end - start_x, positive=True)
    if aor is not None:
        _checked_scale(travel / aor.speed, positive=True)
        feed_duration = _checked_scale(bed_length / aor.speed, positive=True)
        if not aor.run.stopped:
            _checked_scale(feed_duration / aor.run.period)

    angle_rad = angle * math.pi / 180
    slope = _checked_scale(math.tan(angle_rad), positive=True)
    rc = _checked_scale(compact_diameter / 2, positive=compact_diameter > 0)
    rs = _checked_scale(spread_diameter / 2, positive=spread_diameter > 0)
    feed = aor.run.mean if aor is not None else _checked_scale(feed_ratio * layer, positive=feed_ratio > 0)
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
    contact_distance = contact_height = capacity = math.inf
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
        if excess > 0 and aor is None:
            contact_distance = _checked_scale(capacity / excess, positive=capacity > 0)

    history = None
    if aor is not None:
        history = make_bed_history(
            aor.run, length=bed_length, clearance=clearance if p.spread_on else None,
            required=required, capacity=capacity, loose_density=loose_density, bed_density=bed_density,
            release_tau=float(p.aor.release_tau), entry_phase=float(p.aor.entry_phase) / 360,
            reverse_feed=reverse_feed,
        )
        contact_distance = history.contact_x

    model = Model(
        parameters=p, slope=slope, rc=rc, rs=rs, feed=feed, base=base, required=required,
        balance=balance, excess=excess, deposit=deposit, swept_thickness=swept_thickness,
        contact_x=compact_start + contact_distance, contact_height=contact_height, capacity=capacity,
        tangent_x=tangent_x, tangent_z=tangent_z, _compact=compact, _spread=spread,
        aor=aor, history=history, two_pass=two_pass, feed_direction=feed_direction, lead=lead,
        start_x=start_x, travel_end=travel_end, turn_progress=turn_progress, feed_end=feed_end,
        compact_start=compact_start, spread_start=spread_start, contact_distance=contact_distance,
    )
    for profile in (model.end, model.end.spreader, model.max_compact, model.max_spread):
        if profile is not None and (
            not all(math.isfinite(value) for value in (profile.height, profile.toe_x, profile.pile_area)) or
            (profile.pile_area > 0 and profile.rise == 0)
        ):
            raise ValueError(_SECTION_SCALE_ERROR)
    return model


def build_curve(model: Model) -> tuple[tuple[float, float | None, float | None], ...]:
    """Sample profile heights at regular progress points and process boundaries."""
    length = float(model.parameters.bed_length)
    intervals = 240
    if math.isfinite(length * intervals):
        knots = {model.compact_start + length * index / intervals for index in range(intervals)}
    else:
        knots = {model.compact_start + length * (index / intervals) for index in range(intervals)}
    knots.update((model.start_x, model.travel_end, model.feed_end, model.contact_x))
    if model.turn_progress is not None:
        knots.add(model.turn_progress)
    if model.spread_start is not None:
        knots.add(max(model.start_x, model.spread_start + length))
        if model.spread_start >= model.start_x:
            spacing = model.compact_start - model.spread_start
            knots.update(model.spread_start + spacing * index / 40 for index in range(40))

    if model.aor is not None and not model.aor.run.stopped:
        run, p = model.aor.run, model.parameters.aor
        cycles = length / run.pitch
        if cycles < 140 and run.mean and run.wave.min != run.wave.max:
            for event in event_starts(run.wave, float(p.entry_phase) / 360, 0, cycles):
                for n in (0, 1, 2, 3, 5, 8, 14):
                    x = event * run.pitch + n * float(p.release_tau) * run.speed
                    if 0 <= x <= length:
                        local = model.return_distance(model.feed_world(x))
                        knots.add(model.compact_start + local)
                        if model.spread_start is not None:
                            knots.add(model.spread_start + local)

    points = []
    for x in sorted(knots):
        if not model.start_x <= x <= model.travel_end:
            continue
        state = model.at(x)
        compact_height = state.height if state.compact_started else None
        spread_height = state.spreader.height if state.spreader is not None else None
        points.append((x, compact_height, spread_height))
    return tuple(points)
