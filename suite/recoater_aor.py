"""Offline angle-of-repose feeder and powder inventory per unit roller width.

Travel and outlet dimensions are in mm, time is in s, and densities are in
g/cm³. Feed integrals and roller inventories are loose sectional areas in mm².
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
import math


_SCALE_ERROR = '입력값의 단위와 크기를 확인하세요.'
_PERIOD_ERROR = '아발란체 주기를 계산할 수 없습니다. 롤러와 방출 조건을 확인하세요.'


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


@dataclass(frozen=True)
class AorParameters:
    tray_diameter: float = 90
    feed_width: float = 120
    feeder_diameter: float = 16
    active_length: float = 6
    start_angle: float = 40
    stop_angle: float = 34
    baseline_pct: float = 3
    release_tau: float = 0.075
    measured_period: float = 0
    entry_phase: float = 0
    period_variation: float = 0
    test_speed: float = 220
    test_outlet: float = 2
    test_rpm: float = 30
    run_speed: float = 220
    run_outlet: float = 2
    run_rpm: float = 60
    outlet_lead: float = 61


AOR_EXAMPLE = AorParameters()


@dataclass(frozen=True, slots=True)
class ReleaseWave:
    block: int
    starts: tuple[float, ...]
    cycle_knots: tuple[float, ...]
    min: float
    max: float
    _entry: float = field(default=0, repr=False)
    _phase: tuple[float, ...] = field(default=(), repr=False)
    _magnitude: tuple[float, ...] = field(default=(), repr=False)
    _prefix: tuple[float, ...] = field(default=(), repr=False)
    _origin: float = field(init=False, default=0, repr=False)

    def __post_init__(self) -> None:
        if self._phase:
            object.__setattr__(self, '_origin', self._primitive(0))

    @property
    def minimum(self) -> float:
        return self.min

    @property
    def maximum(self) -> float:
        return self.max

    def value(self, cycles: float) -> float:
        if not self._phase:
            return self.min
        t = (cycles + self._entry) / self.block
        f = t - math.floor(t)
        i = bisect_right(self._phase, f, hi=len(self._phase) - 1) - 1
        part = (f - self._phase[i]) / (self._phase[i + 1] - self._phase[i])
        return self._magnitude[i] + part * (self._magnitude[i + 1] - self._magnitude[i])

    def _primitive(self, cycles: float) -> float:
        t = (cycles + self._entry) / self.block
        whole = math.floor(t)
        f = t - whole
        i = bisect_right(self._phase, f, hi=len(self._phase) - 1) - 1
        df = f - self._phase[i]
        left = self._magnitude[i]
        end = left + (self._magnitude[i + 1] - left) * df / (self._phase[i + 1] - self._phase[i])
        return self.block * (whole + self._prefix[i] + df * (left + end) / 2)

    def integral(self, cycles: float) -> float:
        if not self._phase:
            return self.min * cycles if self.min else 0.0
        return self._primitive(cycles) - self._origin


_STOPPED_WAVE = ReleaseWave(1, (0,), (0, 1), 0, 0)


def release_wave(
    period: float, tau: float, background: float, entry_phase: float, variation: float,
) -> ReleaseWave:
    """Normalize a periodic train of finite-rise gamma pulses to unit mean."""
    intervals = (1 + variation, 1 - variation / 2, 1 - variation, 1 + variation / 2) if variation else (1,)
    block = len(intervals)
    starts = [0.0]
    for interval in intervals[:-1]:
        starts.append(starts[-1] + interval)

    a = block * period / tau
    if not math.isfinite(a):
        raise ValueError(_PERIOD_ERROR)
    if a < 0.001 or background == 1:
        return ReleaseWave(block, tuple(starts), (0, block), 1, 1)

    phases = {0.0, 1.0}
    phases.update(i / (192 * block) for i in range(1, 192 * block))
    active_span = min(1, 32 / a)
    for start in starts:
        for i in range(241):
            f = start / block + active_span * i / 240
            phases.add(f - math.floor(f))
    phase = tuple(sorted(phases))

    decay = math.exp(-a)
    ad = a / -math.expm1(-a)
    c1 = ad * decay
    c2 = ad * ad * decay * (1 + decay) if decay else 0

    def pulse(f: float) -> float:
        u = f * a
        if u > 740:
            return 0
        # k(t) = t² exp(-t/τ) / (2τ³), summed over indefinite past releases.
        return ad * 0.5 * math.exp(-u) * (u * u + 2 * c1 * u + c2)

    scale = []
    for f in phase[:-1]:
        rate = 0.0
        for j, start in enumerate(starts):
            offset = f - start / block
            rate += intervals[j - 1] / block * pulse(offset - math.floor(offset))
        scale.append(rate)
    scale.append(scale[0])

    area = 0.0
    for i in range(1, len(phase)):
        area += (phase[i] - phase[i - 1]) * (scale[i] + scale[i - 1]) / 2
    _checked_scale(area, positive=True)
    magnitude = tuple(background + (1 - background) * value / area for value in scale)
    low, high = min(magnitude), _checked_scale(max(magnitude), positive=True)
    prefix = [0.0]
    for i in range(1, len(phase)):
        prefix.append(prefix[-1] + (phase[i] - phase[i - 1]) * (magnitude[i] + magnitude[i - 1]) / 2)

    cycle_knots = {0.0, float(block)}
    for f in phase:
        offset = f * block - entry_phase
        cycle_knots.add(offset - block * math.floor(offset / block))
    return ReleaseWave(
        block, tuple(starts), tuple(sorted(cycle_knots)), low, high,
        entry_phase, phase, magnitude, tuple(prefix),
    )


def event_starts(
    wave: ReleaseWave, entry: float, from_cycles: float, to_cycles: float,
) -> Iterator[float]:
    """Yield all releases in and around a caller-bounded window of mean periods."""
    for k in range(math.floor((from_cycles + entry) / wave.block) - 1,
                   math.ceil((to_cycles + entry) / wave.block) + 1):
        for start in wave.starts:
            yield k * wave.block + start - entry


@dataclass(frozen=True, slots=True)
class DischargeCondition:
    speed: float
    rpm: float
    outlet: float
    stopped: bool
    period: float
    pitch: float
    mean: float
    mean_volume: float
    ideal_volume: float
    mean_mass: float
    gain: float | None
    low: float
    high: float
    contrast: float
    wave: ReleaseWave

    def height_at(self, distance: float) -> float:
        return self.mean * self.wave.value(distance / self.pitch) if self.mean else 0.0

    def area_at(self, distance: float) -> float:
        if not self.mean:
            return 0.0
        if self.wave.min == self.wave.max:
            return self.mean * distance
        return self.mean * self.pitch * self.wave.integral(distance / self.pitch)

    def flow_at(self, time: float) -> float:
        return self.mean_mass * self.wave.value(time / self.period) if self.mean_mass else 0.0


@dataclass(frozen=True, slots=True)
class AorFeed:
    test: DischargeCondition
    run: DischargeCondition
    expected_mass: float
    covered_mean_mass: float
    pass_mass: float
    dwell: float
    covered_area: float
    tray_area: float
    event_volume: float
    lead: float

    @property
    def speed(self) -> float:
        return self.run.speed

    @property
    def rpm(self) -> float:
        return self.run.rpm


def _validated_aor(parameters: AorParameters, leading_offset: float) -> AorParameters:
    positive = (
        ('tray_diameter', '트레이 지름'), ('feed_width', '유효 토출 폭'),
        ('feeder_diameter', '피딩롤러 지름'), ('active_length', '사면의 수평 길이'),
        ('release_tau', '방출 시간상수'), ('test_speed', '시험 이동속도'), ('run_speed', '운전 이동속도'),
    )
    nonnegative = (
        ('test_outlet', '시험 토출 두께'), ('run_outlet', '운전 토출 두께'), ('test_rpm', '시험 RPM'),
        ('run_rpm', '운전 RPM'), ('measured_period', '시험 실측 주기'), ('outlet_lead', '토출점 선행 거리'),
    )
    values = {key: _checked_input(getattr(parameters, key), label) for key, label in positive}
    values.update({key: _checked_input(getattr(parameters, key), label, allow_zero=True)
                   for key, label in nonnegative})
    for key in ('start_angle', 'stop_angle', 'baseline_pct', 'entry_phase', 'period_variation'):
        values[key] = _number(getattr(parameters, key))
    p = replace(parameters, **values)
    if not 0 <= p.stop_angle < p.start_angle < 90:
        raise ValueError('사면 시작각은 정지각보다 크고 90°보다 작아야 합니다. 정지각은 0° 이상입니다.')
    if not 0 < p.baseline_pct <= 100:
        raise ValueError('상시 흐름 비율은 0% 초과 100% 이하로 입력하세요.')
    if not 0 <= p.entry_phase <= 360:
        raise ValueError('베드 진입 위상은 0~360°로 입력하세요.')
    if not 0 <= p.period_variation <= 90:
        raise ValueError('주기 편차는 0~90%로 입력하세요.')
    if p.outlet_lead < leading_offset:
        raise ValueError('AOR 토출점 선행 거리는 선행 롤러의 축 간 거리 이상으로 설정하세요.')
    if p.measured_period > 0 and not (p.test_rpm > 0 and p.test_outlet > 0):
        raise ValueError('시험 실측 주기를 사용하려면 시험 RPM과 토출 두께가 양수여야 합니다.')
    return p


def _discharge_condition(
    p: AorParameters, speed: float, rpm: float, outlet: float,
    mean: float, density: float, event_area: float,
) -> DischargeCondition:
    if rpm == 0 or outlet == 0:
        return DischargeCondition(speed, rpm, outlet, True, math.inf, math.inf, 0, 0, 0, 0,
                                  None, 0, 0, 0, _STOPPED_WAVE)

    sweep = math.pi * p.feeder_diameter * rpm * outlet / 60
    _checked_scale(sweep, positive=True)
    if p.measured_period:
        test_product = _checked_scale(p.test_rpm * p.test_outlet, positive=True)
        run_product = _checked_scale(rpm * outlet, positive=True)
        period = p.measured_period * test_product / run_product
    else:
        period = event_area / sweep
    pitch = period * speed
    if not (math.isfinite(pitch) and pitch > 0 and math.isfinite(period / p.release_tau)):
        raise ValueError(_PERIOD_ERROR)
    wave = release_wave(period, p.release_tau, p.baseline_pct / 100, p.entry_phase / 360,
                        p.period_variation / 100)
    mean_volume = _checked_scale(mean * p.feed_width * speed)
    ideal_volume = _checked_scale(sweep * p.feed_width, positive=True)
    mean_mass = _checked_scale(mean_volume * density / 1000)
    gain = _checked_scale(mean_volume / ideal_volume)
    _checked_scale(mean * wave.max)
    if wave.min != wave.max:
        _checked_scale(mean * pitch)
    low, high = mean_mass * wave.min, _checked_scale(mean_mass * wave.max)
    contrast = (1 - low / high) / (1 + low / high) if high else 0.0
    return DischargeCondition(speed, rpm, outlet, False, period, pitch, mean, mean_volume,
                              ideal_volume, mean_mass, gain, low, high, contrast, wave)


def _tray_pass_volume(
    test: DischargeCondition, diameter: float, radius: float, half_width: float, covered_area: float,
) -> float:
    if test.wave.min == test.wave.max or not test.mean:
        return test.mean * covered_area
    _checked_scale(diameter / test.pitch)
    radius_squared = radius * radius
    transition = math.sqrt(max(0.0, radius_squared - half_width * half_width))

    def arc_area(x: float) -> float:
        return (x * math.sqrt(max(0.0, radius_squared - x * x)) +
                radius_squared * math.asin(min(1.0, max(-1.0, x / radius))))

    middle_area = 2 * half_width * transition
    transition_area = arc_area(transition)

    def chord_primitive(x: float) -> float:
        if abs(x) <= transition:
            return 2 * half_width * x
        return math.copysign(middle_area + arc_area(abs(x)) - transition_area, x)

    count = 1200
    dx = _checked_scale(diameter / count, positive=True)
    prev_area, prev_feed = chord_primitive(-radius), 0.0
    volume = 0.0
    for i in range(1, count + 1):
        x = i * dx
        area = chord_primitive(min(radius, max(-radius, x - radius)))
        delivered = test.area_at(x)
        volume += (area - prev_area) * (delivered - prev_feed) / dx
        prev_area, prev_feed = area, delivered
    return volume


def make_aor_feed(
    parameters: AorParameters, layer: float, density: float, ratio: float, leading_offset: float = 0,
) -> AorFeed:
    """Build test and operating waveforms and infer the test-tray mass."""
    p = _validated_aor(parameters, leading_offset)
    mean = _checked_scale(ratio * layer, positive=ratio > 0)
    start_slope = math.tan(p.start_angle * math.pi / 180)
    stop_slope = math.tan(p.stop_angle * math.pi / 180)
    event_area = _checked_scale(0.5 * (p.active_length * p.active_length) * (start_slope - stop_slope),
                                positive=True)
    test = _discharge_condition(p, p.test_speed, p.test_rpm, p.test_outlet, mean, density, event_area)
    run = _discharge_condition(p, p.run_speed, p.run_rpm, p.run_outlet, mean, density, event_area)

    # Integrate deposition against the circular tray chord clipped to the outlet width.
    radius = _checked_scale(p.tray_diameter / 2, positive=True)
    half_width = min(p.feed_width / 2, radius)
    radius_squared = _checked_scale(radius * radius, positive=True)
    tray_area = _checked_scale(math.pi * radius_squared, positive=True)
    covered_area = _checked_scale(2 * (half_width * math.sqrt(max(0.0, radius_squared - half_width * half_width)) +
                                     radius_squared * math.asin(half_width / radius)), positive=True)
    expected_mass = _checked_scale(mean * tray_area * density / 1000)
    covered_mean_mass = _checked_scale(test.mean * covered_area * density / 1000)
    pass_mass = _checked_scale(_tray_pass_volume(test, p.tray_diameter, radius, half_width, covered_area) *
                               density / 1000)
    dwell = _checked_scale(p.tray_diameter / test.speed, positive=True)
    event_volume = _checked_scale(event_area * p.feed_width, positive=True)
    return AorFeed(test, run, expected_mass, covered_mean_mass, pass_mass, dwell,
                   covered_area, tray_area, event_volume, p.outlet_lead)


@dataclass(frozen=True, slots=True)
class _Inventory:
    raw: float = 0.0
    passed: float = 0.0
    formed: float = 0.0
    spread_pile: float = 0.0
    compact_pile: float = 0.0
    overflow: float = 0.0


@dataclass(frozen=True, slots=True)
class BedState(_Inventory):
    pass_height: float = 0.0
    formed_height: float = 0.0


@dataclass(frozen=True, slots=True)
class BedHistory:
    """Inventory checkpoints and layer cell averages along the roller pass."""

    positions: tuple[float, ...]
    contact_x: float
    max_spread: float
    max_compact: float
    _data: tuple[_Inventory, ...] = field(repr=False)
    _advance: Callable[[_Inventory, float, float], _Inventory] = field(repr=False)
    _loose_density: float = field(repr=False)
    _bed_density: float = field(repr=False)
    _steady_rates: _Inventory | None = field(repr=False)

    def _cell(self, x: float) -> int:
        return bisect_right(self.positions, x, lo=1, hi=len(self.positions) - 1)

    def _inventory_at(self, x: float, i: int) -> _Inventory:
        left, right = self.positions[i - 1], self.positions[i]
        if x <= left:
            return self._data[i - 1]
        if x >= right:
            return self._data[i]
        return self._advance(self._data[i - 1], x - left, x)

    def _rate_at(self, i: int, key: str) -> float:
        if self._steady_rates is not None:
            return getattr(self._steady_rates, key)
        return ((getattr(self._data[i], key) - getattr(self._data[i - 1], key)) /
                (self.positions[i] - self.positions[i - 1]))

    def at(self, x: float) -> BedState:
        i = self._cell(x)
        state = self._inventory_at(x, i)
        return BedState(
            state.raw, state.passed, state.formed, state.spread_pile, state.compact_pile, state.overflow,
            self._rate_at(i, 'passed'), self._rate_at(i, 'formed') * self._loose_density / self._bed_density,
        )

    def layer_at(self, x: float, key: str) -> float:
        """Return raw, passed, or formed average thickness at loose density."""
        if key not in ('raw', 'passed', 'formed'):
            raise ValueError(f'Unknown bed layer {key!r}')
        return self._rate_at(self._cell(x), key)

    def pile_area_at(self, x: float, *, spreader: bool) -> float:
        state = self._inventory_at(x, self._cell(x))
        return state.spread_pile if spreader else state.compact_pile


def make_bed_history(
    run: DischargeCondition, *, length: float, clearance: float | None,
    required: float, capacity: float, loose_density: float, bed_density: float,
    release_tau: float, entry_phase: float, reverse_feed: bool = False,
) -> BedHistory:
    """Route deposited powder through the spreader and compactor at each bed cell.

    If the outlet deposited on the opposing traverse, each roller encounters the
    original fixed field in reverse order. The same local distance denotes the
    same bed cell for both rollers, even when their axes reach it at different times.
    """
    cycles = 0.0 if run.stopped else _checked_scale(length / run.pitch)
    count = math.ceil(min(16384, max(1024, cycles * 80)))
    knots = {length * (i / count) for i in range(count + 1)}
    steady = run.mean == 0 or run.wave.min == run.wave.max
    if not steady and 0 < cycles < 128:
        for event in event_starts(run.wave, entry_phase, 0, cycles):
            for j in range(65):
                x = event * run.pitch + j * release_tau * run.speed / 4
                if 0 < x < length:
                    knots.add(length - x if reverse_feed else x)

    positions = [0.0]
    tolerance = length * 1e-12
    for x in sorted(knots):
        if x - positions[-1] > tolerance and x < length - tolerance:
            positions.append(x)
    positions.append(length)

    steady_rates = None
    contact_x = math.inf
    if steady:
        passed_rate = run.mean if clearance is None else min(run.mean, clearance)
        formed_rate = min(required, passed_rate)
        steady_rates = _Inventory(run.mean, passed_rate, formed_rate,
                                  run.mean - passed_rate, passed_rate - formed_rate)
        if steady_rates.compact_pile > 0 and capacity <= length * steady_rates.compact_pile:
            contact_x = capacity / steady_rates.compact_pile

        def advance(left: _Inventory, dx: float, x: float) -> _Inventory:
            remaining = steady_rates.compact_pile * x
            return _Inventory(run.mean * x, passed_rate * x, formed_rate * x, steady_rates.spread_pile * x,
                              min(capacity, remaining), max(0.0, remaining - capacity))

    else:
        total_raw = _checked_scale(run.area_at(length))

        def advance(left: _Inventory, dx: float, x: float) -> _Inventory:
            raw = total_raw - run.area_at(length - x) if reverse_feed else run.area_at(x)
            incoming = max(0.0, raw - left.raw)
            spread_stock = incoming + left.spread_pile
            out = incoming if clearance is None else min(clearance * dx, spread_stock)
            compact_stock = out + left.compact_pile
            formed = min(required * dx, compact_stock)
            remaining = max(0.0, compact_stock - formed)
            return _Inventory(raw, left.passed + out, left.formed + formed, max(0.0, spread_stock - out),
                              min(capacity, remaining), left.overflow + max(0.0, remaining - capacity))

    data = [_Inventory()]
    for x0, x1 in zip(positions[:-1], positions[1:]):
        previous = data[-1]
        current = advance(previous, x1 - x0, x1)
        data.append(current)
        if steady or math.isfinite(contact_x) or not math.isfinite(capacity):
            continue
        if current.compact_pile >= capacity > 0 or current.overflow > previous.overflow:
            lo, hi = x0, x1
            for _ in range(40):
                mid = (lo + hi) / 2
                near = advance(previous, mid - x0, mid)
                if near.compact_pile >= capacity > 0 or near.overflow > previous.overflow:
                    hi = mid
                else:
                    lo = mid
            contact_x = hi

    max_spread = _checked_scale(max(state.spread_pile for state in data))
    max_compact = _checked_scale(max(state.compact_pile for state in data))
    for x in (data[-1].raw, data[-1].passed, data[-1].formed, data[-1].overflow):
        _checked_scale(x)
    return BedHistory(tuple(positions), contact_x, max_spread, max_compact, tuple(data), advance,
                      loose_density, bed_density, steady_rates)
