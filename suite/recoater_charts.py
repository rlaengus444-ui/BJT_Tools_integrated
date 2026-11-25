"""Quantitative chart data and Tk Canvas plotting for the recoater."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
import math
from collections.abc import Callable

from suite_theme import COLORS, FONTS


AOR = '#654795'
GRID = '#dde6f1'
_HOVER_TAG = 'recoater-hover'
_COMPACT_HOVER_LABELS = {
    '컴팩팅 더미 높이': '컴팩팅',
    '스프래딩 더미 높이': '스프래딩',
    '베드 좌표 (후면=0)': '베드 x',
    '토출 질량유량': '토출',
    '피딩 직후 두께': '두께',
    '주기 평균': '평균',
    '연속피딩 기준': '기준',
    '연속피딩': '연속',
}


def _finite(value):
    return value is not None and math.isfinite(value)


def _number(value, precision=5):
    return f'{value:.{precision}g}' if _finite(value) else '—'


def _quantity(value, unit):
    return f'{_number(value)} {unit}' if _finite(value) else '—'


def _fraction(value, low, high):
    span = high - low
    if math.isfinite(span):
        return (value - low) / span
    scale = max(abs(low), abs(high))
    return (value / scale - low / scale) / (high / scale - low / scale)


def _interpolate(low, high, part):
    return low * (1 - part) + high * part


@dataclass(frozen=True)
class ChartBounds:
    left: float
    top: float
    right: float
    bottom: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def screen_x(self, value):
        return self.left + (self.right - self.left) * _fraction(value, self.x_min, self.x_max)

    def screen_y(self, value):
        return self.bottom - (self.bottom - self.top) * _fraction(value, self.y_min, self.y_max)

    def x_at(self, pixel):
        return _interpolate(self.x_min, self.x_max, (pixel - self.left) / (self.right - self.left))

    def y_at(self, pixel):
        return _interpolate(self.y_min, self.y_max, (self.bottom - pixel) / (self.bottom - self.top))

    def contains(self, x, y):
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True)
class _HoverPlot:
    bounds: ChartBounds
    x_label: str
    x_unit: str
    y_unit: str
    values_at: Callable


def bind_chart_hover(canvas):
    """Add hover interactions without replacing existing seek or scroll bindings."""
    if getattr(canvas, '_recoater_hover_bound', False):
        return
    canvas._recoater_hover_bound = True

    def move(event):
        canvas._recoater_pointer = (event.x, event.y)
        _draw_hover(canvas)

    def leave(_event):
        canvas._recoater_pointer = None
        canvas.delete(_HOVER_TAG)

    canvas.bind('<Motion>', move, add='+')
    canvas.bind('<Leave>', leave, add='+')


def clear_plot(canvas, *, forget_pointer=False):
    canvas.delete('all')
    canvas._recoater_hover_plot = None
    if forget_pointer:
        canvas._recoater_pointer = None


def set_chart_hover(canvas, bounds, x_label, x_unit, y_unit, values_at):
    """Each series returned by values_at is (label, unit, value, color)."""
    canvas._recoater_hover_plot = _HoverPlot(bounds, x_label, x_unit, y_unit, values_at)
    _draw_hover(canvas)


def _text(canvas, x, y, label, *, anchor='center', color=None, **kwargs):
    return canvas.create_text(x, y, text=label, anchor=anchor, fill=color or COLORS['muted'],
                              font=FONTS['small'], **kwargs)


def _draw_hover(canvas):
    canvas.delete(_HOVER_TAG)
    plot = getattr(canvas, '_recoater_hover_plot', None)
    pointer = getattr(canvas, '_recoater_pointer', None)
    if plot is None or pointer is None or not plot.bounds.contains(*pointer):
        return
    x, y = pointer
    axes = plot.bounds
    position = axes.x_at(x)
    pointer_x = _quantity(position, plot.x_unit)
    pointer_y = _quantity(axes.y_at(y), plot.y_unit)
    rows = [f'{plot.x_label}  {pointer_x}', f'커서 y  {pointer_y}']
    canvas.create_line(x, axes.top, x, axes.bottom, fill=COLORS['muted'],
                       dash=(2, 3), tags=_HOVER_TAG)
    canvas.create_line(axes.left, y, axes.right, y, fill=COLORS['muted'],
                       dash=(2, 3), tags=_HOVER_TAG)
    series = tuple(plot.values_at(position))
    for label, unit, value, color in series:
        rows.append(f'{label}  {_quantity(value, unit)}')
        if color and _finite(value) and axes.y_min <= value <= axes.y_max:
            _dot(canvas, x, axes.screen_y(value), color, size=3, tags=_HOVER_TAG)

    width, height = canvas.winfo_width(), canvas.winfo_height()
    label = _text(canvas, 0, 0, '\n'.join(rows), anchor='nw', color=COLORS['ink'],
                  width=width - 18, tags=_HOVER_TAG)
    a, b, c, d = canvas.bbox(label)
    if d - b + 12 > height:
        short_rows = [f'x  {pointer_x}  ·  y  {pointer_y}']
        short_rows.extend(f'{_COMPACT_HOVER_LABELS.get(name, name)}  {_quantity(value, unit)}'
                          for name, unit, value, _ in series)
        canvas.itemconfigure(label, text='\n'.join(short_rows))
        a, b, c, d = canvas.bbox(label)
        # The native family can resolve to a fixed bitmap font. Measure the text
        # again with a scalable family before positioning a very short tooltip.
        for pixels in (10, 9, 8, 7, 6):
            if d - b + 12 <= height:
                break
            canvas.itemconfigure(label, font=('Arial', -pixels))
            a, b, c, d = canvas.bbox(label)
    box_w, box_h = min(width - 4, c - a + 12), min(height - 4, d - b + 8)
    left = x + 11 if x + 11 + box_w < width else x - box_w - 10
    top = y - box_h - 8 if y - box_h - 8 > 0 else y + 10
    left = max(2, min(left, width - box_w - 2))
    top = max(2, min(top, height - box_h - 2))
    canvas.coords(label, left + 6, top + 4)
    background = canvas.create_rectangle(left, top, left + box_w, top + box_h,
                                         fill=COLORS['surface'], outline=COLORS['border'],
                                         tags=_HOVER_TAG)
    canvas.tag_lower(background, label)


def _dot(canvas, x, y, color, *, size=4, **options):
    canvas.create_oval(x - size, y - size, x + size, y + size,
                       fill=color, outline=COLORS['surface'], **options)


def _axes(canvas, x_min, x_max, y_min, y_max, x_caption, y_caption):
    width, height = canvas.winfo_width(), canvas.winfo_height()
    axes = ChartBounds(55, 21, width - 18, height - 31, x_min, x_max, y_min, y_max)
    _text(canvas, axes.left, 9, y_caption, anchor='w')
    for i in range(5):
        value = _interpolate(y_min, y_max, i / 4)
        y = axes.screen_y(value)
        canvas.create_line(axes.left, y, axes.right, y, fill=GRID)
        _text(canvas, axes.left - 6, y, _number(value, 3), anchor='e')
    columns = 4 if width >= 440 else 3
    for i in range(columns + 1):
        value = _interpolate(x_min, x_max, i / columns)
        x = axes.screen_x(value)
        canvas.create_line(x, axes.bottom, x, axes.bottom + 4, fill=GRID)
        anchor = 'w' if i == 0 else 'e' if i == columns else 'center'
        _text(canvas, x, axes.bottom + 11, _number(value, 4), anchor=anchor)
    _text(canvas, axes.right, height - 7, x_caption, anchor='e')
    return axes


def draw_series(canvas, axes, points, color, *, width=2, **options):
    """Draw only defined segments, preserving gaps before a roller enters."""
    segment = []
    for x, value in points:
        if _finite(value):
            segment.extend((axes.screen_x(x), axes.screen_y(value)))
        elif segment:
            _stroke(canvas, segment, color, width, options)
            segment = []
    if segment:
        _stroke(canvas, segment, color, width, options)


def _stroke(canvas, coords, color, width, options):
    if len(coords) >= 4:
        canvas.create_line(*coords, fill=color, width=width, **options)
    else:
        _dot(canvas, *coords, color, size=width / 2)


@dataclass(frozen=True)
class ComparisonPoint:
    x: float
    actual: float
    reference: float
    difference: float
    percent: float | None


@dataclass(frozen=True)
class DeviationSummary:
    minimum: float | None
    maximum: float | None
    rms: float | None


@dataclass(frozen=True)
class ComparisonData:
    points: tuple[ComparisonPoint, ...]
    span: float
    axis: str
    axis_unit: str
    interval: str
    unit: str
    difference: DeviationSummary
    percent: DeviationSummary
    value_max: float
    _value_at: Callable
    _current: Callable

    def value_at(self, x):
        return self._value_at(x)

    def at_state(self, state):
        return self._current(state)


def deviation_point(x, actual, reference):
    delta = actual - reference
    if abs(delta) <= 32 * math.ulp(1.) * max(abs(actual), abs(reference)):
        delta = 0.
    percent = 100 * (delta / reference) if reference > 0 else None
    return ComparisonPoint(x, actual, reference, delta, percent if _finite(percent) else None)


def _deviation_summary(points, key, span):
    values = tuple(getattr(point, key) for point in points)
    defined = [value for value in values if _finite(value)]
    if not defined:
        return DeviationSummary(None, None, None)
    lo, hi = min(defined), max(defined)
    scale = max(abs(lo), abs(hi)) or 1.
    weights, squares = [], []
    for i in range(1, len(points)):
        left, right = values[i - 1], values[i]
        if not (_finite(left) and _finite(right)):
            continue
        weight = (points[i].x - points[i - 1].x) / span
        a, b = left / scale, right / scale
        # Integral within a linear section divided by that section's length.
        weights.append(weight)
        squares.append(weight * (a * a + a * b + b * b) / 3)
    support = math.fsum(weights)
    rms = scale * math.sqrt(min(1., math.fsum(squares) / support)) if support else None
    return DeviationSummary(lo, hi, rms)


def _feeding_position(model, state):
    if state.phase in ('feed', 'combined') and 0 <= state.fed_distance < model.parameters.bed_length:
        return state.fed_distance
    return None


def prepare_comparison(model, reference, metric):
    """Compare identical sequences against an ideal feeder at the entered rate."""
    p, run = model.parameters, model.aor.run
    length = p.bed_length
    flow, feed = metric == 'flow', metric == 'feed'
    formed, spread = metric == 'formed', metric == 'spread'
    if metric not in ('flow', 'feed', 'spread', 'compact', 'formed'):
        raise ValueError(f'Unknown comparison metric {metric!r}')
    same_feed = run.mean == reference.feed and (run.mean == 0 or run.wave.min == run.wave.max == 1)

    if flow or feed:
        axis = '피딩 시간' if flow else '토출점 이동거리'
        axis_unit = 's' if flow else 'mm'
        period = run.period if flow else run.pitch
        repeat = period * run.wave.block
        periodic = not run.stopped and 0 < repeat < math.inf
        span = repeat if periodic else length / run.speed if flow else length
        interval = ('방출 1주기' if run.wave.block == 1 else '방출 4회의 반복 구간') if periodic else '베드 통과 구간'
        reference_value = (reference.feed * p.aor.feed_width * run.speed * p.loose_density / 1000
                           if flow else reference.feed)
        if not math.isfinite(reference_value):
            raise ValueError('연속피딩 기준을 계산할 수 없습니다. 입력 단위와 크기를 확인하세요.')
        trace = run.flow_at if flow else run.height_at

        def value_at(x):
            return deviation_point(x, reference_value if same_feed else trace(x), reference_value)

        def current_at(state):
            pos = _feeding_position(model, state)
            if pos is None:
                return None
            pos = pos / run.speed if flow else pos
            point = value_at(pos)
            return replace(point, x=pos % span) if periodic else point

        knots = sorted({min(span, vertex * period) for vertex in run.wave.cycle_knots}) if periodic else (0., span)
        points = tuple(value_at(x) for x in knots)
    else:
        span, axis_unit, interval = length, 'mm', '베드 전체'
        axis = '스프래딩롤러 진행거리' if spread else '컴팩팅롤러 진행거리'
        if formed:
            def value_at(x):
                actual = model.history.layer_at(x, 'formed') * p.loose_density / p.bed_density
                return deviation_point(x, reference.deposit if same_feed else actual, reference.deposit)

            # Each solver cell has one layer thickness. Reproduce the step at its edges.
            positions = model.history.positions
            points = tuple(
                deviation_point(edge, point.actual, point.reference)
                for left, right in zip(positions, positions[1:])
                for point in (value_at(left + (right - left) / 2),)
                for edge in (left, right)
            )
        else:
            def value_at(x):
                reference_value = reference.pile_height_at(spread, x)
                actual = reference_value if same_feed else model.pile_height_at(spread, x)
                return deviation_point(x, actual, reference_value)

            positions = set(model.history.positions)
            if not spread:
                positions.update(x for x in (model.contact_distance, reference.contact_distance) if 0 < x < length)
            points = tuple(value_at(x) for x in sorted(positions))

        def current_at(state):
            if spread:
                return value_at(state.spread_distance) if state.spreader is not None else None
            return value_at(state.compact_distance) if state.compact_started else None

    return ComparisonData(
        points, span, axis, axis_unit, interval, 'g/s' if flow else 'mm',
        _deviation_summary(points, 'difference', span), _deviation_summary(points, 'percent', span),
        max(max(point.actual, point.reference) for point in points), value_at, current_at,
    )


def sample_plot_points(points, width, fraction_at, value_at, series):
    # Retain the order, extrema and one undefined sample in each screen column.
    if len(points) <= 2 * width:
        return points

    def column(i):
        return max(0, min(width - 1, int(fraction_at(points[i]) * width)))

    result = []
    first = 0
    while first < len(points):
        bucket = column(first)
        end = first + 1
        while end < len(points) and column(end) == bucket:
            end += 1
        selected = {first, end - 1}
        for key in series:
            def get(i):
                return value_at(points[i], key)

            defined = [i for i in range(first, end) if _finite(get(i))]
            if defined:
                selected.update((min(defined, key=get), max(defined, key=get)))
            if len(defined) < end - first:
                selected.add(next(i for i in range(first, end) if not _finite(get(i))))
        result.extend(points[i] for i in sorted(selected))
        first = end
    return result


def draw_comparison_chart(canvas, data, current, view):
    clear_plot(canvas)
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 75:
        return
    overlay, percent = view == 'overlay', view == 'percent'
    summary = data.percent if percent else data.difference
    if percent and summary.minimum is None:
        _text(canvas, width / 2, height / 2, '연속피딩 기준이 0인 구간의 편차율은 표시할 수 없습니다.',
              width=max(1, width - 24))
        return
    unit = '%' if percent else data.unit
    lo = 0. if overlay else min(0., summary.minimum)
    hi = data.value_max if overlay else max(0., summary.maximum)
    if lo == hi:
        hi = 1. if percent else .01
        lo = 0. if overlay else -hi
    elif max(abs(lo), abs(hi)) <= 1e300:
        pad = (hi - lo) * .04
        lo = lo - pad if lo < 0 else lo
        hi = hi + pad if hi > 0 else hi
    direction = ' · 전면→후면' if data.interval == '베드 전체' else ''
    axes = _axes(canvas, 0., data.span, lo, hi, f'{data.axis} ({data.axis_unit}){direction}',
                 f'{"계산값" if overlay else "AOR − 연속피딩"} ({unit})')
    keys = ('actual', 'reference') if overlay else (view,)
    pixels = max(1, min(1200, int(axes.right - axes.left)))
    cache = getattr(canvas, '_recoater_comparison_samples', None)
    if cache is None or cache[0] is not data or cache[1:3] != (pixels, keys):
        points = sample_plot_points(data.points, pixels, lambda point: point.x / data.span, getattr, keys)
        cache = data, pixels, keys, points
        canvas._recoater_comparison_samples = cache
    samples = cache[3]
    if not overlay:
        _baseline(canvas, axes, 0.)
    draw_series(canvas, axes, ((point.x, getattr(point, keys[0])) for point in samples), AOR, width=2.2)
    if overlay:
        draw_series(canvas, axes, ((point.x, point.reference) for point in samples),
                    COLORS['muted'], width=1.5, dash=(6, 4))
    if current is not None and _finite(value := getattr(current, keys[0])):
        x = axes.screen_x(current.x)
        tag = 'recoater-comparison-current'
        canvas.create_line(x, axes.top, x, axes.bottom, fill=COLORS['muted'], dash=(2, 4), tags=tag)
        _dot(canvas, x, axes.screen_y(value), AOR, tags=tag)

    def values_at(x):
        point = data.value_at(x)
        if overlay:
            return (('AOR', unit, point.actual, AOR), ('연속피딩', unit, point.reference, COLORS['muted']))
        return (('편차율' if percent else '편차', unit, getattr(point, view), AOR),
                ('연속피딩 기준', unit, 0. if _finite(getattr(point, view)) else None, COLORS['muted']))

    set_chart_hover(canvas, axes, data.axis, data.axis_unit, unit, values_at)


def discharge_readings(model, view):
    """Return trough, mean and peak in the selected unit, then contrast as a fraction."""
    run = model.aor.run
    if view == 'distance':
        return run.mean * run.wave.min, run.mean, run.mean * run.wave.max, run.contrast
    return run.low, run.mean_mass, run.high, run.contrast


def _baseline(canvas, axes, value):
    y = axes.screen_y(value)
    canvas.create_line(axes.left, y, axes.right, y, fill=COLORS['muted'], width=1.3, dash=(5, 4))


def waveform_samples(run, trace, period, origin, end, columns, mean, *, knot_values=None, include_grid=True):
    """Select line vertices and indicate when every column covers a full repeat."""
    wave = run.wave
    if run.stopped or mean == 0 or wave.min == wave.max:
        return ((origin, trace(origin)), (end, trace(end))), False
    span = end - origin
    if (span / columns) / period >= wave.block:
        # Each pixel covers a whole motif, so every column has the same exact range.
        return (), True
    knot_cycles = wave.cycle_knots
    if knot_values is None:
        knot_values = tuple(wave.value(knot) for knot in knot_cycles)
    results = [(origin, trace(origin))] if include_grid else []
    a = origin
    for i in range(1, columns + 1):
        b = _interpolate(origin, end, i / columns)
        lo, hi = a / period, b / period
        phase = lo % wave.block
        remaining = min(wave.block, hi - lo)
        candidates = []
        for shift in (0., wave.block):
            if phase + remaining <= shift:
                break
            u0, u1 = phase - shift, phase + remaining - shift
            first, last = bisect_right(knot_cycles, u0), bisect_left(knot_cycles, u1)
            if first < last:
                values = knot_values[first:last]
                for value in {min(values), max(values)}:
                    j = first + values.index(value)
                    elapsed = (knot_cycles[j] + shift - phase) * period
                    candidates.append((min(b, a + elapsed), value * mean))
        if candidates:
            low = min(candidates, key=lambda item: item[1])
            high = max(candidates, key=lambda item: item[1])
            results.extend(sorted({low, high}))
        if include_grid:
            results.append((b, trace(b)))
        a = b
    return tuple(results), False


def draw_discharge_chart(canvas, model, state, view):
    clear_plot(canvas)
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 75:
        return
    run = model.aor.run
    spatial = view == 'distance'
    if spatial:
        x_label, x_unit, label, unit = '토출점 이동거리', 'mm', '피딩 직후 두께', 'mm'
        extent, cursor, typical_span = model.parameters.bed_length, state.fed_distance, 4 * run.speed
        period, trace = run.pitch, run.height_at
    else:
        x_label, x_unit, label, unit = '피딩 시간', 's', '토출 질량유량', 'g/s'
        extent, cursor, typical_span = model.parameters.bed_length / run.speed, state.fed_distance / run.speed, 4.
        period, trace = run.period, run.flow_at
    span = min(extent, max(typical_span, 16 * math.ulp(cursor)))
    origin = min(cursor - (cursor % span), extent - span)
    end = min(extent, origin + span)
    low, mean, peak, _ = discharge_readings(model, view)
    maximum = max(.001, peak)
    if maximum <= 1e300:
        maximum *= 1.06
    axes = _axes(canvas, origin, end, 0., maximum, f'{x_label} ({x_unit})', f'{label} ({unit})')
    columns = max(1, min(1200, int(axes.right - axes.left)))
    cache = getattr(canvas, '_recoater_discharge_samples', None)
    key = view, origin, end, columns
    if cache is None or cache[0] is not model or cache[1] != key:
        cache = model, key, waveform_samples(run, trace, period, origin, end, columns, mean)
        canvas._recoater_discharge_samples = cache
    samples, unresolved = cache[2]
    if unresolved:
        canvas.create_rectangle(axes.left, axes.screen_y(peak), axes.right, axes.screen_y(low),
                                fill='#eee8f7', outline=AOR, width=1)
        _text(canvas, axes.right, 9, '화면 간격별 골~마루', anchor='e', color=AOR)
    else:
        draw_series(canvas, axes, samples, AOR, width=2.2)
    _baseline(canvas, axes, mean)
    if state.feeding:
        _dot(canvas, axes.screen_x(cursor), axes.screen_y(trace(cursor)), AOR, tags='recoater-discharge-current')

    def values_at(x):
        values = ((label, unit, trace(x), AOR), ('주기 평균', unit, mean, COLORS['muted']))
        if not spatial:
            return values
        return (*values, ('베드 좌표 (후면=0)', 'mm', model.feed_world(x), None))

    set_chart_hover(canvas, axes, x_label, x_unit, unit, values_at)
