"""Tk Canvas views for recoater travel, cross-section and pile heights."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import math

from recoater_charts import (
    AOR, ChartBounds, clear_plot, draw_series, sample_plot_points, set_chart_hover, waveform_samples,
)
from suite_theme import COLORS, FONTS

POWDER = '#d79531'
POWDER_FILL = '#ffd887'
SPREAD = '#267da9'
SPREAD_FILL = '#9ad7ed'
DEPOSIT = '#197d70'
PASS = '#b57832'
FEED = '#93a6b9'
CONTACT = '#ab3b71'
STEEL = '#46566e'
BED = '#dce5f0'
GRID = '#dde6f1'
PLOT = '#f8faff'


def format_number(value, digits=3):
    if not math.isfinite(value):
        return '—'
    if value and (abs(value) >= 1e6 or abs(value) < 10 ** -digits):
        return f'{value:.4g}'
    return f'{value:.{digits}f}'


def _ticks(length, count):
    return (length * (i / count) for i in range(count + 1))


def _label(canvas, x, y, text, *, anchor='center', color=None, font=None, **options):
    canvas.create_text(x, y, text=text, anchor=anchor, fill=color or COLORS['muted'],
                       font=font or FONTS['small'], **options)


def _line(canvas, *coords, color=GRID, **options):
    canvas.create_line(*coords, fill=color, **options)


def _marker(canvas, x, y, color, *, diamond=False, size=4, **options):
    options.update(fill=color, outline=COLORS['surface'], width=1)
    if diamond:
        canvas.create_polygon(x-size, y, x, y-size, x+size, y, x, y+size, **options)
    else:
        canvas.create_oval(x-size, y-size, x+size, y+size, **options)


def empty(canvas, message):
    clear_plot(canvas, forget_pointer=True)
    _label(canvas, canvas.winfo_width() / 2, canvas.winfo_height() / 2, message)


def _draw_pile(canvas, px, py, offset, base, profile, wall, fill, edge, *, direction=1):
    if profile.rise <= 0:
        return
    shape = []
    for i in range(33):
        z = base + profile.rise * (i / 32) ** 2
        shape.extend((px(offset + direction * wall(z)), py(z)))
    toe = (px(offset + direction * profile.toe_x), py(base))
    canvas.create_polygon(*shape, *toe, fill=fill, outline=edge, width=1)


def _draw_roller(canvas, x, py, bottom, radius, scale, blade_top, *, tags=()):
    y = py(bottom + radius)
    r = radius * scale
    if radius == 0:
        ceiling = py(blade_top)
        _line(canvas, x, ceiling, x, py(bottom), color=STEEL, width=2, dash=(4, 3), tags=tags)
    else:
        ceiling = y - r
        canvas.create_oval(x-r, y-r, x+r, y+r, fill='#cbd6e4', outline=STEEL, width=1.4, tags=tags)
        if r > 6:
            inner = .82 * r
            canvas.create_oval(x-inner, y-inner, x+inner, y+inner,
                               fill='#edf3fa', outline='#a5b5c9', tags=tags)
            _marker(canvas, x, y, STEEL, size=min(7, max(2, r*.17)), tags=tags)
    return ceiling


def draw_section(canvas, model, state, follow):
    """Render the same scale on both axes and clip the bed to its true length."""
    canvas.delete('all')
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 95:
        return
    if getattr(model, 'two_pass', False):
        _draw_sequenced_section(canvas, model, state, follow, width, height)
        return
    p = model.parameters
    spread_end = model.end.spreader
    extent = max(model.rc, model.end.toe_x,
                 p.spacing + max(model.rs, spread_end.toe_x) if spread_end else 0)
    top = max(model.end.height, model.feed, model.deposit, p.layer + 2 * model.rc,
              max(spread_end.height, p.clearance + 2 * model.rs) if spread_end else 0, .2)
    # Normalize by the larger extent before converting to pixels. This also
    # keeps distant bed-coordinate labels finite for very long beds.
    assembly_span = extent + model.rc
    domain = assembly_span if follow else p.bed_length + assembly_span
    view_width = max(domain, top * 1.35, 1.)
    if not math.isfinite(view_width) or not math.isfinite(top):
        empty(canvas, '표시할 수 있는 입력 크기를 확인하세요.')
        return
    left, right, floor = 14, width - 14, height - 29
    pixels = min((right-left) / 1.12, (floor-24) * (view_width / top) / 1.14)
    origin = (left + right - domain/view_width*pixels) / 2 + model.rc/view_width*pixels
    def bx(u):
        if not follow:
            return origin + (u / view_width) * pixels
        delta = (u - state.x) / view_width
        # All bed segments are clipped before drawing.
        return min(width + 1., max(-1., origin + delta * pixels))
    def px(offset):
        return origin + (offset / view_width if follow else state.x/view_width + offset/view_width) * pixels
    def py(z):
        return floor - (z / view_width) * pixels

    if follow:
        half_span = ((right-left) / max(pixels, 1.) / 2) * view_width
        span_start = max(0., state.x-half_span)
        span_end = min(p.bed_length, state.x+half_span)
    else:
        span_start, span_end = 0., p.bed_length
    columns = max(4, min(8, width // 80))
    for value in _ticks(span_end-span_start, columns):
        coord = span_start + value
        screen = bx(coord)
        if 57 < screen < width-28:
            _line(canvas, screen, 16, screen, floor+7)
            _label(canvas, screen, floor+17, format_number(coord, 1))
    band_left, band_right = max(0, bx(0)), min(width, bx(p.bed_length))
    if band_left < band_right:
        canvas.create_rectangle(band_left, floor, band_right, floor+8, fill=BED, outline='')
        _line(canvas, band_left, floor, band_right, floor, color=STEEL)
        for x in range(int(band_left)+8, int(band_right), 11):
            _line(canvas, x-5, floor+7, x, floor+2, color='#c1cedf')

    def layer(start, end, thickness, color):
        a, b = max(band_left, start), min(band_right, end)
        if a < b and thickness > 0:
            canvas.create_rectangle(a, py(thickness), b, floor, fill=color, outline='')
            _line(canvas, a, py(thickness), b, py(thickness), color=color)

    layer(band_left, px(0), model.deposit, DEPOSIT)
    if state.spreader is not None:
        layer(px(0), px(p.spacing), model.base, PASS)
        layer(px(p.spacing), band_right, model.feed, FEED)
    else:
        layer(px(0), band_right, model.feed, FEED)

    _draw_pile(canvas, px, py, 0, model.base, state, model.compact_wall, POWDER_FILL, POWDER)
    if state.spreader is not None:
        _draw_pile(canvas, px, py, p.spacing, model.feed, state.spreader, model.spread_wall, SPREAD_FILL, SPREAD)

    def roller(offset, bottom, radius, caption):
        x = px(offset)
        ceiling = _draw_roller(canvas, x, py, bottom, radius, pixels / view_width, top * .75)
        if follow:
            anchor = 'e' if offset == 0 and state.spreader is not None else 'w' if state.spreader is not None else 'center'
            shift = -4 if anchor == 'e' else 4 if anchor == 'w' else 0
            _label(canvas, x+shift, max(11, ceiling-12), caption, anchor=anchor, color=COLORS['ink'])

    roller(0, p.layer, model.rc, '컴팩팅롤러')
    if state.spreader is not None:
        roller(p.spacing, p.clearance, model.rs, '스프래딩롤러')

    def mark_peak(offset, profile, color, diamond=False):
        if profile.rise > 0:
            x, y = px(offset + profile.peak_x), py(profile.height)
            if follow:
                _line(canvas, x, y, x, floor, color=color, dash=(3, 3))
            _marker(canvas, x, y, color, diamond=diamond)

    mark_peak(0, state, POWDER)
    if state.spreader is not None:
        mark_peak(p.spacing, state.spreader, SPREAD, diamond=True)
    if state.contacted:
        _marker(canvas, px(model.tangent_x), py(model.tangent_z), CONTACT, size=5)
    _label(canvas, width-12, 12, '진행 방향 →', anchor='e')
    _label(canvas, 13, floor+17, 'mm', anchor='w')


_STREAM_TRANSIT = .18


def stream_bands(run, feed_time, *, transit=_STREAM_TRANSIT, columns=15, knot_values=None):
    """Schematic fall depth with mass flux evaluated at the release time of each band."""
    if run.stopped or run.mean_mass <= 0:
        return ()
    if feed_time <= 0:
        return ((0., run.flow_at(0.)),)
    start = max(0., feed_time - transit)
    if start == feed_time:
        return ((0., run.flow_at(feed_time)),)
    slot = transit / columns
    latest = feed_time - (feed_time % slot)
    issued_times = {feed_time}
    issued_times.update(issued for i in range(columns + 1)
                        if (issued := latest - i * slot) >= start)
    if run.wave.min != run.wave.max:
        # Include release extrema independently of the clock slots so a narrow
        # pulse remains visible even when it falls between two emissions.
        peaks, _ = waveform_samples(run, run.flow_at, run.period, start, feed_time, columns,
                                    run.mean_mass, knot_values=knot_values, include_grid=False)
        issued_times.update(issued for issued, _ in peaks)
    return tuple((min(1., (feed_time - issued) / transit), run.flow_at(issued))
                 for issued in sorted(issued_times, reverse=True))


def _draw_stream(canvas, x, y, landing, run, feed_time, radius, knot_values):
    if landing <= y + 1:
        return
    for depth, rate in stream_bands(run, feed_time, knot_values=knot_values):
        if rate <= 0:
            continue
        intensity = math.sqrt(min(20., rate / run.mean_mass))
        at_y = y + (landing - y) * depth ** 1.55
        spread = (.72 + intensity * 1.29) * min(1.6, max(.72, radius / 20))
        issued = feed_time - depth * _STREAM_TRANSIT
        grain_phase = (issued % .618) / .618
        center = x + math.sin(grain_phase * math.tau + depth * 3) * min(1., spread / 3)
        _line(canvas, center - spread, at_y, center + spread, at_y,
              color='#c9b4e7', width=min(3.4, .7 + intensity),
              tags=('aor-stream', 'aor-stream-band'))
        for grain in range(min(6, 1 + int(intensity * 1.8))):
            fraction = ((grain_phase + grain * .382) % 1) * 2 - 1
            grain_x = center + fraction * spread
            grain_radius = min(1.4, .50 + intensity * .23)
            canvas.create_oval(grain_x - grain_radius, at_y - grain_radius, grain_x + grain_radius, at_y + grain_radius,
                               outline='', fill=AOR, tags=('aor-stream', 'aor-stream-grain'))


def _draw_feeder(canvas, wx, py, scale, outlet_z, landing_z, model, state, direction, follow, knot_values):
    x, y = wx(state.outlet_world_x), py(outlet_z)
    width = canvas.winfo_width()
    if model.aor is None:
        canvas.create_rectangle(x - 5, y - 7, x + 5, y, fill=FEED, outline=STEEL,
                                tags=('recoater-feeder', 'recoater-outlet'))
        if state.feeding:
            _line(canvas, x, y, x, py(landing_z), color=FEED, width=2, tags='recoater-uniform-feed')
        if follow:
            _label(canvas, min(width-38, max(38, x)), max(12, y-16), '균일 피딩', color=STEEL)
        return

    run = model.aor.run
    radius = model.parameters.aor.feeder_diameter / 2 * scale
    cx, cy = x - direction * .94 * radius, y - .34 * radius
    top, shoulder = cy - 2.6 * radius, cy - 1.85 * radius
    left, right = cx - direction * 1.70 * radius, cx + direction * 1.38 * radius
    lip_x = x + direction * run.outlet * scale
    tags = ('recoater-feeder', 'aor-feeder')
    canvas.create_polygon(left, top, right, top, right, shoulder, lip_x, cy - .34 * radius,
                          cx - direction * .88 * radius, cy - .45 * radius, left, shoulder,
                          fill='#e9ddf5', outline=STEEL, width=1, tags=tags)
    canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                       fill='#cbd6e4', outline=STEEL, width=1.3, tags=tags)
    if run.rpm > 0:
        revolution = 60. / run.rpm
        angle = math.tau * ((state.feed_time % revolution) / revolution) if math.isfinite(revolution) else 0.
        _line(canvas, cx, cy, cx + direction * .72 * radius * math.cos(angle),
              cy + .72 * radius * math.sin(angle), color=STEEL, width=1.2, tags=tags)
    _marker(canvas, x, y, AOR, size=2.5, tags=('recoater-outlet', 'aor-outlet'))
    if state.feeding:
        _draw_stream(canvas, x, y, py(landing_z), run, state.feed_time, radius, knot_values)
    if follow:
        _label(canvas, min(width-42, max(42, cx)), max(25, top-9), 'AOR 피더', color=AOR, tags=tags)


def _sequence_caption(model, state):
    if state.phase == 'feed':
        return '후면 → 전면  ·  피딩'
    stages = []
    if state.phase == 'combined' and state.progress < model.feed_end:
        stages.append('피딩')
    if state.spreader is not None and state.spread_distance < model.parameters.bed_length:
        stages.append('스프래딩')
    if state.compact_started:
        stages.append('컴팩팅')
    return '전면 → 후면  ·  ' + (' · '.join(stages) if stages else '진입')


def _draw_sequenced_section(canvas, model, state, follow, width, height):
    p = model.parameters
    aor = model.aor
    length = p.bed_length
    show_feeder = state.phase in ('feed', 'combined')
    show_rollers = state.phase in ('compact', 'combined')
    feed_direction = 1 if state.phase == 'feed' else -1
    feeder_radius = p.aor.feeder_diameter / 2 if aor is not None else .6
    run = aor.run if aor is not None else None
    feed_high = model.feed * run.wave.max if run is not None else model.feed
    compact_extent = max(model.rc, model.max_compact.toe_x)
    spread_extent = (p.spacing + max(model.rs, model.max_spread.toe_x)) if model.max_spread is not None else 0.
    roller_extent = max(compact_extent, spread_extent)
    roller_top = max(model.max_compact.height, p.layer + 2 * model.rc, feed_high, .2,
                     max(model.max_spread.height, p.clearance + 2 * model.rs) if model.max_spread else 0.)
    outlet_z = roller_top + 1.3
    feature_top = outlet_z + 3 * feeder_radius if show_feeder or not follow else roller_top
    feeder_extent = 3 * feeder_radius + (run.outlet if run is not None else 0.)

    if follow:
        lefts, rights = [], []
        if show_feeder:
            outlet = state.outlet_world_x - state.world_x
            lefts.append(outlet - feeder_extent)
            rights.append(outlet + feeder_extent)
        if show_rollers:
            compact = state.compact_world_x - state.world_x
            lefts.append(compact - roller_extent)
            rights.append(compact + max(model.rc, model.rs))
        left, right, origin = min(lefts), max(rights), state.world_x
    else:
        left = -max(roller_extent, model.lead + feeder_extent)
        right = length + max(model.lead + max(model.rc, model.rs), feeder_extent)
        origin = 0.
    view_span = max(right - left, 1.35 * feature_top, 1.)
    floor = height - 30
    if not math.isfinite(view_span):
        empty(canvas, '표시할 수 있는 입력 크기를 확인하세요.')
        return
    middle = left + (right - left) / 2
    headroom = 35 if show_feeder and follow and height >= 135 else 24
    scale = min((width - 22) / view_span / 1.12, (floor - headroom) / feature_top)

    def wx(x):
        return width / 2 + ((x - origin) - middle) * scale

    def py(z):
        return floor - z * scale

    band_left, band_right = max(0., wx(0.)), min(width, wx(length))
    _line(canvas, 0, floor, width, floor)
    if band_left < band_right:
        canvas.create_rectangle(band_left, floor, band_right, floor + 8, fill=BED, outline='')
        for x in range(int(band_left)+7, int(band_right), 11):
            _line(canvas, x-5, floor+7, x, floor+2, color='#c1cedf')
        _line(canvas, band_left, floor, band_right, floor, color=STEEL)
        for x, edge, tag in ((0., '후면', 'recoater-bed-rear'), (length, '전면', 'recoater-bed-front')):
            screen = wx(x)
            if 2 < screen < width - 2:
                _line(canvas, screen, floor + 1, screen, floor + 9, color=STEEL, tags=tag)
                align = 'w' if x == 0 else 'e'
                _label(canvas, screen, floor + 19, f'{edge} {format_number(x, 1)}', anchor=align, tags=tag)
        divisions = max(3, min(8, int((band_right - band_left) / 64)))
        for i in range(1, divisions):
            screen = band_left + (band_right - band_left) * i / divisions
            world = origin + middle + (screen - width / 2) / scale
            _line(canvas, screen, 20, screen, floor, color=GRID)
            _label(canvas, screen, floor + 19, f'{world:.4g}')
    _label(canvas, width-47, 11, _sequence_caption(model, state), anchor='e',
           color=COLORS['ink'], tags='recoater-stage')
    arrow_start, arrow_end = width - 37, width - 12
    if feed_direction < 0:
        arrow_start, arrow_end = arrow_end, arrow_start
    _line(canvas, arrow_start, 11, arrow_end, 11, color=STEEL, arrow='last',
          arrowshape=(5, 7, 2), tags=('recoater-stage', 'recoater-stage-direction'))
    _label(canvas, 11, 11, '베드 좌표 (mm)', anchor='w')

    knot_values = None
    if run is not None and not run.stopped and run.mean > 0:
        cached = getattr(canvas, '_recoater_scene_wave', None)
        if cached is None or cached[0] is not run:
            cached = run, tuple(run.wave.value(knot) for knot in run.wave.cycle_knots)
            canvas._recoater_scene_wave = cached
        knot_values = cached[1]
    _draw_swept_layers(canvas, model, state, wx, py, width, origin, middle, scale, knot_values)

    def roll(x, bottom, radius, profile, color, fill, wall, caption, tag, diamond=False):
        if profile is not None:
            _draw_pile(canvas, wx, py, x, profile.height - profile.rise, profile, wall, fill, color, direction=-1)
        screen = wx(x)
        ceiling = _draw_roller(canvas, screen, py, bottom, radius, scale, roller_top * .75, tags=tag)
        if follow:
            leading_gap = p.spacing * scale if p.spread_on else 0.
            place_left = screen > width - 97 or (diamond and leading_gap < 97)
            anchor = 'e' if place_left else 'w'
            label_x = min(width-9, max(9, screen + (-5 if place_left else 5)))
            _label(canvas, label_x, max(25, ceiling - 10), caption, anchor=anchor, color=COLORS['ink'], tags=tag)
        if profile is not None and profile.rise > 0:
            _marker(canvas, wx(x - profile.peak_x), py(profile.height), color,
                    diamond=diamond, size=4 if follow else 2.8, tags=tag)

    if show_rollers:
        if p.spread_on:
            roll(state.spread_world_x, p.clearance, model.rs, state.spreader, SPREAD, SPREAD_FILL,
                 model.spread_wall, '스프래딩롤러', 'recoater-spreader', diamond=True)
        roll(state.compact_world_x, p.layer, model.rc, state if state.compact_started else None, POWDER, POWDER_FILL,
             model.compact_wall, '컴팩팅롤러', 'recoater-compactor')
        if state.contacted:
            _marker(canvas, wx(state.compact_world_x - model.tangent_x), py(model.tangent_z),
                    CONTACT, size=5, tags='recoater-contact')
    if show_feeder:
        def pile_surface(x, profile):
            if profile is None:
                return 0.
            ahead = x - state.outlet_world_x
            if profile.peak_x <= ahead <= profile.toe_x:
                return profile.height - model.slope * (ahead - profile.peak_x)
            return 0.

        landing = max(model.layer_height_at(state.outlet_world_x, 'raw'),
                      pile_surface(state.compact_world_x, state if state.compact_started else None),
                      pile_surface(state.spread_world_x, state.spreader)) if state.feeding else 0.
        _draw_feeder(canvas, wx, py, scale, outlet_z, landing, model, state, feed_direction, follow, knot_values)


def _draw_swept_layers(canvas, model, state, wx, py, width, origin, middle, scale, knot_values):
    length = model.parameters.bed_length
    view_center = origin + middle
    view_left = max(0., view_center - width / 2 / scale)
    view_right = min(length, view_center + width / 2 / scale)
    compact_edge = model.return_world(state.compact_distance)
    spread_edge = model.return_world(state.spread_distance) if state.spreader is not None else compact_edge
    bed_inlet = model.feed_world(0.)
    bed_fed = model.feed_world(state.fed_distance)
    raw_left, raw_right = sorted((bed_inlet, bed_fed))
    intervals = ((compact_edge, length, 'formed', DEPOSIT), (spread_edge, compact_edge, 'passed', PASS),
                 (raw_left, min(raw_right, spread_edge), 'raw', FEED))
    for left, right, kind, color in intervals:
        left, right = max(left, view_left), min(right, view_right)
        if left >= right:
            continue
        columns = min(640, max(1, math.ceil((right - left) * scale / 2)))
        dense = False
        if model.aor is None:
            value = model.layer_height_at(left, kind)
            points = ((left, value), (right, value))
        elif kind == 'raw':
            run = model.aor.run
            a, b = sorted((model.feed_distance(left), model.feed_distance(right)))
            if a >= b:
                continue
            samples, dense = waveform_samples(run, run.height_at, run.pitch, a, b,
                                              columns, run.mean, knot_values=knot_values)
            if dense:
                samples = ((a, run.mean), (b, run.mean))
            points = tuple((model.feed_world(x), thickness) for x, thickness in samples)
        else:
            points = _swept_cell_points(model, left, right, kind, columns)
        if dense:
            band = model.aor.run.mean * model.aor.run.wave.max
            canvas.create_rectangle(wx(left), py(band), wx(right), py(0), fill='#e6e0ed',
                                    outline='', tags='aor-layer-raw-envelope')
        if not points or not any(thickness > 0 for _, thickness in points):
            continue
        shape = [coord for x, thickness in points for coord in (wx(x), py(thickness))]
        tags = (f'recoater-layer-{kind}', f'aor-layer-{kind}') if model.aor else (f'recoater-layer-{kind}',)
        canvas.create_polygon(*shape, shape[-2], py(0), shape[0], py(0),
                              fill=color, outline='', tags=tags)
        _line(canvas, *shape, color=color, tags=tags)


def _swept_cell_points(model, left, right, kind, columns):
    a, b = model.return_distance(right), model.return_distance(left)
    if a >= b:
        return ()
    knots = model.history.positions
    first, last = bisect_right(knots, a), bisect_left(knots, b)
    if last - first <= columns * 2:
        edges = (a, *knots[first:last], b)
        return tuple(
            (model.return_world(edge), thickness)
            for start, end in zip(edges, edges[1:])
            for thickness in (model.layer_height_at(model.return_world(start + (end - start) / 2), kind),)
            for edge in (start, end)
        )
    # Average the accumulated section across a screen column when several cells
    # share that column. Zooming in preserves the original cell boundaries.
    origin = a
    previous = getattr(model.history.at(a), kind)
    output = []
    for i in range(1, columns + 1):
        end = b if i == columns else origin + (b - origin) * (i / columns)
        if end <= a:
            continue
        current = getattr(model.history.at(end), kind)
        thickness = max(0., (current - previous) / (end - a))
        if kind == 'formed':
            thickness = model.parameters.layer * (thickness / model.required)
        output.extend(((model.return_world(a), thickness), (model.return_world(end), thickness)))
        a, previous = end, current
    return tuple(output)


def chart_x_bounds(width):
    return 55, max(56, width - 18)


def draw_height_chart(canvas, model, state, curve):
    clear_plot(canvas)
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 70:
        return
    left, right = chart_x_bounds(width)
    sequenced = getattr(model, 'two_pass', False)
    top, bottom = (23, height - 30) if sequenced else (14, height - 22)
    start = model.start_x if sequenced else 0.
    end = model.travel_end if sequenced else model.parameters.bed_length
    compact = model.max_compact if sequenced else model.end
    spread = model.max_spread if sequenced else model.end.spreader
    high = max(compact.height, spread.height if spread else 0., .001)
    axes = ChartBounds(left, top, right, bottom, start, end, 0., high * 1.08 if high <= 1e300 else high)
    xx, yy = axes.screen_x, axes.screen_y
    grid_height = high
    for value in _ticks(grid_height, 3):
        axis_y = yy(value)
        _line(canvas, left, axis_y, right, axis_y)
        _label(canvas, left-7, axis_y, f'{value:.4g}', anchor='e')
    for step in _ticks(end - start, 5 if width > 480 else 4):
        value = start + step
        axis_x = xx(value)
        _line(canvas, axis_x, bottom, axis_x, bottom+4)
        _label(canvas, axis_x, bottom+11, f'{value:.4g}',
               anchor='w' if step == 0 else 'e' if value == end else 'center')
    if sequenced:
        _label(canvas, left, 9, '더미 총높이 (mm)', anchor='w')
        _label(canvas, right, height - 6, '전체 주행거리 (mm)', anchor='e')
        markers = ((model.turn_progress, '방향 전환', 'recoater-turn'),)
        if model.turn_progress is None:
            markers = ((model.feed_end, '피딩 종료', 'recoater-feed-end'),)
        for position, label, tag in markers:
            if position is not None and start < position < end:
                x = xx(position)
                _line(canvas, x, top, x, bottom, color=STEEL, dash=(3, 4), tags=tag)
                _label(canvas, x-4 if x > right-67 else x+4, top+7, label,
                       anchor='e' if x > right-67 else 'w', color=STEEL, tags=tag)
        if start < model.compact_start < end:
            x = xx(model.compact_start)
            _line(canvas, x, top, x, bottom, color=DEPOSIT, dash=(2, 4), tags='recoater-compact-entry')
            _label(canvas, x+4 if x < right-75 else x-4, top+7 if x < left+105 else 9, '컴팩팅 진입',
                   anchor='w' if x < right-75 else 'e', color=DEPOSIT, tags='recoater-compact-entry')

    if start <= model.contact_x <= end:
        x = xx(model.contact_x)
        _line(canvas, x, top, x, bottom, color=CONTACT, dash=(4, 4), tags='recoater-contact')
        text_y = top + 8 if sequenced else top
        if sequenced and abs(xx(model.compact_start) - x) > 85:
            text_y = 9
        _label(canvas, x-4 if x > right-70 else x+4, text_y, '최초 접촉',
               anchor='e' if x > right-70 else 'w', color=CONTACT, tags='recoater-contact')
    cursor = xx(state.x)
    _line(canvas, cursor, top, cursor, bottom, color=COLORS['muted'], dash=(2, 4), tags='recoater-height-cursor')
    pixels = max(1, min(1200, int(right - left)))
    cache = getattr(canvas, '_recoater_height_samples', None)
    if cache is None or cache[0] is not curve or cache[1] != pixels:
        samples = sample_plot_points(curve, pixels, lambda point: (point[0] - start) / (end - start),
                                     tuple.__getitem__, (1, 2))
        cache = curve, pixels, samples
        canvas._recoater_height_samples = cache

    def series(index, current, ink, diamond):
        before, after = [], [(state.x, current)]
        for point in cache[2]:
            distance, value = point[0], point[index]
            if distance < state.x:
                before.append((distance, value))
            elif distance > state.x:
                after.append((distance, value))
        before.append((state.x, current))
        draw_series(canvas, axes, after, ink, width=1.5, dash=(4, 4))
        draw_series(canvas, axes, before, ink, width=2.4)
        if current is not None:
            _marker(canvas, cursor, yy(current), ink, diamond=diamond, tags='recoater-height-cursor')

    use_compact = not sequenced or state.compact_started
    series(1, state.height if use_compact else None, POWDER, False)
    if model.parameters.spread_on:
        series(2, state.spreader.height if state.spreader is not None else None, SPREAD, True)

    def values_at(x):
        hovered = model.at(x)
        values = []
        if not sequenced or hovered.compact_started:
            values.append(('컴팩팅 더미 높이', 'mm', hovered.height, POWDER))
        if hovered.spreader is not None:
            values.append(('스프래딩 더미 높이', 'mm', hovered.spreader.height, SPREAD))
        if sequenced:
            values.append(('베드 좌표 (후면=0)', 'mm', hovered.world_x, None))
        return values

    set_chart_hover(canvas, axes, '전체 주행거리' if sequenced else '컴팩팅 진행거리', 'mm', 'mm', values_at)
