"""Numeric text for presentation; never feed these strings back into a solver.

Integer identifiers/counts stay integers. Floating measurements use two decimal
places, with two-decimal scientific notation when a nonzero value would appear
as zero. Certified lower bounds round down, so their displayed guarantee remains
true. Raw values in inputs, result records and exports are deliberately separate.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import json
import math
import numbers


def format_number(value, *, decimals=2, scientific=False, trim=False):
    """Return human-readable numeric text without changing the supplied value."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if not isinstance(value, (numbers.Number, Decimal)):
        return str(value)
    if isinstance(value, numbers.Integral):
        return str(value)
    if isinstance(value, complex):
        return str(value)
    try:
        finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
        if not finite:
            nan = value.is_nan() if isinstance(value, Decimal) else math.isnan(value)
            return "NaN" if nan else "-∞" if value < 0 else "∞"
        # Formatting does not overwrite, quantize or serialize the real value.
        if scientific:
            return f"{value:.{decimals}e}"
        rendered = f"{value:.{decimals}f}"
        if value != 0 and Decimal(rendered) == 0:
            return f"{value:.{decimals}e}"
        if Decimal(rendered) == 0:
            rendered = f"{0:.{decimals}f}"
        if trim and "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered
    except (TypeError, ValueError, InvalidOperation):
        return str(value)


def format_lower_bound(value, *, decimals=2):
    """Round a finite bound downward, including the actual binary float value.

    Decimal(str(value)) can round upward before flooring at a decimal boundary;
    Decimal.from_float preserves the original float's ordering instead.
    """
    if value is None or isinstance(value, bool):
        return "—"
    try:
        if isinstance(value, Decimal):
            exact = value
        elif isinstance(value, numbers.Integral):
            exact = Decimal(int(value))
        else:
            exact = Decimal.from_float(float(value))
        if not exact.is_finite():
            return format_number(value, decimals=decimals)
        with localcontext() as context:
            context.prec = max(28, len(exact.as_tuple().digits) + abs(exact.adjusted()) + decimals + 4)
            quantum = Decimal(1).scaleb(-decimals)
            rounded = exact.quantize(quantum, rounding=ROUND_FLOOR)
        if rounded == 0:
            rounded = abs(rounded)
        return f"{rounded:.{decimals}f}"
    except (ValueError, TypeError, OverflowError, InvalidOperation):
        return "—"


def format_display_value(value):
    """Render nested inspection values for a text widget, preserving strings.

    This is display text, not a JSON encoder. Export the original object with
    the normal JSON/CSV writer so numeric precision and data types survive.
    """
    def render(item, nested=False):
        if isinstance(item, dict):
            return "{" + ", ".join(json.dumps(str(key), ensure_ascii=False) + ": " + render(part, True)
                                    for key, part in item.items()) + "}"
        if isinstance(item, (list, tuple)):
            return "[" + ", ".join(render(part, True) for part in item) + "]"
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False) if nested else item
        if item is None:
            return "null" if nested else "—"
        if isinstance(item, bool):
            return str(item).lower() if nested else str(item)
        return format_number(item)

    return render(value)
