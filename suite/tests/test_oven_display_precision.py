"""Rendered thermal values stay concise without rounding inputs or exports."""
from __future__ import annotations

import csv
from dataclasses import replace
import importlib.util
from pathlib import Path
import re
import sys
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest


@pytest.fixture(scope="module")
def oven():
    path = Path(__file__).resolve().parents[1] / "sources" / "sic_buildbox_thermal_gui_v5.py"
    spec = importlib.util.spec_from_file_location("_oven_display_precision", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app(oven):
    try:
        probe = tk.Tk()
        probe.destroy()
    except tk.TclError as exc:
        pytest.skip("Tk display unavailable: " + str(exc))
    with patch.object(oven.ThermalGUI, "_load_saved_settings", return_value=False), \
            patch.object(oven.ThermalGUI, "_save_settings", return_value=True):
        instance = oven.ThermalGUI()
        instance.withdraw()
        instance.language_var.set("en")
        try:
            yield instance
        finally:
            instance._on_close()


def concise(text):
    assert re.search(r"\d+\.\d{3,}", text) is None, text


def short_result(oven, app):
    inputs = replace(
        app._collect_inputs(), nx=3, ny=3, nz=3,
        oven_profile_min_c=[(0.0, 25.123456789), (0.02, 30.987654321)],
        initial_temp_c=25.123456789, max_dt_s=0.1,
        record_interval_s=0.1, snapshot_interval_s=0.1,
    )
    return oven.simulate(inputs, progress_cb=None, stop_event=None)


def test_language_and_output_refresh_preserve_editable_precision(app):
    app.lx.set("410.123456789")
    app.gas_k.set("0.026123456789")
    app.cal_scale.set("1.123456789")
    app.cal_h.set("12.123456789")
    app.profile_tree.insert("", "end", values=(123.123456789, 57.123456789))
    before = app._collect_inputs()
    settings = app._capture_settings()
    profile = [app.profile_tree.item(i, "values") for i in app.profile_tree.get_children()]
    for language in ("ko", "en"):
        app.language_var.set(language)
        app._update_psd_label()
        app._update_porosity_label()
        concise(app.psd_label.cget("text"))
        concise(app.porosity_label.cget("text"))
        assert app._collect_inputs() == before
        settings["variables"]["language"] = language
        assert app._capture_settings() == settings
        assert [app.profile_tree.item(i, "values") for i in app.profile_tree.get_children()] == profile


def test_fitted_values_keep_input_precision_and_format_separate_outputs(app):
    fit = dict(k_scale=1.234567891, h_conv=12.34567891, rmse_c=0.123456789, nfev=123)
    app.msg_queue.put(("cal_done", fit))
    with patch.object(app._dialogs, "showinfo") as dialog:
        app._poll_queue()
    assert app.cal_scale.get() == "1.234568"
    assert app.cal_h.get() == "12.345679"
    assert fit == dict(k_scale=1.234567891, h_conv=12.34567891, rmse_c=0.123456789, nfev=123)
    concise(app.status.cget("text"))
    concise(app.cal_status.cget("text"))
    concise(dialog.call_args.args[1])
    assert "1.23" in app.cal_status.cget("text")
    assert "nfev=123" in app.cal_status.cget("text")


def test_plots_summaries_colorbars_and_coordinates_only_format_presentation(oven, app):
    result = short_result(oven, app)
    result = replace(result, dt_used_s=0.00000123456789,
                     k_center_wmk=np.full_like(result.k_center_wmk, 0.00000123456789))
    app.result = result
    original_arrays = {name: value.copy() for name, value in vars(result).items()
                       if isinstance(value, np.ndarray)}
    inputs = app._collect_inputs()
    app._show_summary(result)
    app._show_o2_summary(result)
    app._plot_result(result)
    app._plot_o2_result(result)
    app._initialize_3d_field(result)
    app._initialize_o2_3d_field(result)
    assert "1.23e-06" in app.summary.get("1.0", "end")
    for widget in (app.summary, app.o2_summary):
        concise(widget.get("1.0", "end"))
    for widget in (app.field_time_label, app.o2_field_time_label):
        concise(widget.cget("text"))
    for figure in (app.fig, app.o2_fig, app.field_fig, app.o2_field_fig):
        figure.canvas.draw()
        for axes in figure.axes:
            concise(axes.get_title())
            for name in ("x", "y", "z"):
                axis = getattr(axes, name + "axis", None)
                if axis is None:
                    continue
                assert axis.get_major_formatter()(1.23456789, 0) == "1.23"
                assert axis.get_major_formatter()(0.000001, 0) == "1.00e-06"
                assert getattr(axes, "format_" + name + "data")(1.23456789) == "1.23"
                for label in axis.get_ticklabels():
                    concise(label.get_text())
    assert app._collect_inputs() == inputs
    for name, values in original_arrays.items():
        np.testing.assert_array_equal(getattr(result, name), values)


def test_hover_coordinates_and_csv_keep_separate_precision(oven, app, tmp_path):
    result = short_result(oven, app)
    app.result = result
    app._plot_result(result)
    app._plot_o2_result(result)
    for axes, lines, callback, annotation, figure in (
        (app.ax, app.hover_lines, app._on_plot_hover, app.hover_annotation, app.fig),
        (app.o2_ax, app.o2_hover_lines, app._on_o2_plot_hover, app.o2_hover_annotation, app.o2_fig),
    ):
        figure.canvas.draw()
        x = 0.00000123456789
        y = float(np.interp(x, lines[0].get_xdata(), lines[0].get_ydata()))
        px, py = axes.transData.transform((x, y))
        callback(SimpleNamespace(inaxes=axes, xdata=x, ydata=y, x=px, y=py))
        assert annotation.get_visible()
        concise(annotation.get_text())
        assert "1.23e-06" in annotation.get_text()
    destination = tmp_path / "precise.csv"
    with patch.object(app._dialogs, "asksaveasfilename", return_value=str(destination)), \
            patch.object(app._dialogs, "showinfo"):
        app._export_csv()
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert float(rows[0]["center_C"]) == result.center_c[0]
    assert float(rows[0]["center_C"]) != round(float(result.center_c[0]), 2)
    assert float(rows[-1]["o2_center_Deff_m2s"]) == result.o2_deff_center_m2s[-1]
