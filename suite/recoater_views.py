"""Tk Canvas views for the recoater cross-section and powder height plot."""
from __future__ import annotations

import math

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


def _label(canvas, x, y, text, *, anchor='center', color=None, font=None):
    canvas.create_text(x, y, text=text, anchor=anchor, fill=color or COLORS['muted'],
                       font=font or FONTS['small'])


def _line(canvas, *coords, color=GRID, **options):
    canvas.create_line(*coords, fill=color, **options)


def _marker(canvas, x, y, color, *, diamond=False, size=4):
    options = dict(fill=color, outline=COLORS['surface'], width=1)
    if diamond:
        canvas.create_polygon(x-size, y, x, y-size, x+size, y, x, y+size, **options)
    else:
        canvas.create_oval(x-size, y-size, x+size, y+size, **options)


def empty(canvas, message):
    canvas.delete('all')
    _label(canvas, canvas.winfo_width() / 2, canvas.winfo_height() / 2, message)


def draw_section(canvas, model, state, follow):
    """Render the same scale on both axes and clip the bed to its true length."""
    canvas.delete('all')
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 95:
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

    def pile(offset, base, profile, wall, fill, edge):
        if profile.rise <= 0:
            return
        shape = []
        for i in range(33):
            z = base + profile.rise * (i / 32) ** 2
            shape.extend((px(offset + wall(z)), py(z)))
        toe = (px(offset + profile.toe_x), py(base))
        canvas.create_polygon(*shape, *toe, fill=fill, outline=edge, width=1)

    pile(0, model.base, state, model.compact_wall, POWDER_FILL, POWDER)
    if state.spreader is not None:
        pile(p.spacing, model.feed, state.spreader, model.spread_wall, SPREAD_FILL, SPREAD)

    def roller(offset, bottom, radius, caption):
        x, y = px(offset), py(bottom+radius)
        r = radius / view_width * pixels
        if radius == 0:
            ceiling = py(top * .75)
            _line(canvas, x, ceiling, x, py(bottom), color=STEEL, width=2, dash=(4, 3))
        else:
            ceiling = y-r
            canvas.create_oval(x-r, y-r, x+r, y+r, fill='#cbd6e4', outline=STEEL, width=1.4)
            if r > 6:
                inner = .82*r
                canvas.create_oval(x-inner, y-inner, x+inner, y+inner,
                                   fill='#edf3fa', outline='#a5b5c9')
                _marker(canvas, x, y, STEEL, size=min(7, max(2, r*.17)))
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


def chart_x_bounds(width):
    return 55, max(56, width - 18)


def draw_height_chart(canvas, model, state, curve):
    canvas.delete('all')
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 150 or height < 70:
        return
    left, right = chart_x_bounds(width)
    top, bottom = 14, height - 22
    length = model.parameters.bed_length
    high = max(model.end.height, model.end.spreader.height if model.end.spreader else 0., .001)

    def xx(distance):
        return left + (right-left) * (distance / length)

    def yy(y):
        return bottom - (bottom-top) * (y/high) / 1.08

    for value in _ticks(high, 3):
        axis_y = yy(value)
        _line(canvas, left, axis_y, right, axis_y)
        _label(canvas, left-7, axis_y, f'{value:.4g}', anchor='e')
    for value in _ticks(length, 5 if width > 480 else 4):
        axis_x = xx(value)
        _line(canvas, axis_x, bottom, axis_x, bottom+4)
        _label(canvas, axis_x, bottom+12, f'{value:.4g}',
               anchor='w' if value == 0 else 'e' if value == length else 'center')

    if 0 <= model.contact_x <= length:
        x = xx(model.contact_x)
        _line(canvas, x, top, x, bottom, color=CONTACT, dash=(4, 4))
        _label(canvas, x-4 if x > right-70 else x+4, top, '최초 접촉',
               anchor='e' if x > right-70 else 'w', color=CONTACT)
    cursor = xx(state.x)
    _line(canvas, cursor, top, cursor, bottom, color=COLORS['muted'], dash=(2, 4))

    def series(index, current, ink, diamond):
        before, after = [], [cursor, yy(current)]
        for point in curve:
            distance, value = point[0], point[index]
            if distance < state.x:
                before.extend((xx(distance), yy(value)))
            elif distance > state.x:
                after.extend((xx(distance), yy(value)))
        before.extend((cursor, yy(current)))
        if len(after) >= 4:
            _line(canvas, *after, color=ink, width=1.5, dash=(4, 4))
        if len(before) >= 4:
            _line(canvas, *before, color=ink, width=2.4)
        _marker(canvas, cursor, yy(current), ink, diamond=diamond)

    series(1, state.height, POWDER, False)
    if state.spreader is not None:
        series(2, state.spreader.height, SPREAD, True)
