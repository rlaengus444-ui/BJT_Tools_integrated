"""Meaningful physics parity checks for the integrated thermal source.

Usage: python thermal_parity.py --candidate PATH [--report PATH]
The source module is imported under separate names. No GUI is opened and no
user settings are read or changed. All numeric outputs compare bit for bit.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np

ORIGINAL = Path(r"C:\Users\Kain\PycharmProjects\build oven profile\sic_buildbox_thermal_gui_v5.py")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def inputs(mod, **overrides):
    values = dict(
        lx_mm=400.0, ly_mm=500.0, lz_mm=350.0,
        box_k=16.2, box_density_kgm3=7930.0, box_cp_jkgk=500.0,
        side_thickness_mm=3.0, bottom_thickness_mm=5.0,
        top_thickness_mm=3.0, top_open=True,
        bed_density_kgm3=1700.0, true_density_kgm3=3210.0,
        cp_jkgk=750.0, k_eff_wmk=0.6, k_solid_wmk=80.0,
        gas_k_wmk=0.026, k_mode="PSD estimate",
        d10_um=40.6, d50_um=65.4, d90_um=105.0,
        solid_k_temp_exponent=0.75, contact_blend=0.5,
        calibration_k_scale=1.0, calibration_h_wm2k=12.0,
        h_conv_wm2k=12.0, emissivity=0.75, bottom_gap_mm=40.0,
        floor_emissivity=0.8, bottom_view_factor=0.95,
        o2_enabled=True, o2_ambient_percent=20.95, o2_initial_percent=20.95,
        o2_diff_air_25_m2s=2.05e-5, o2_diff_temp_exponent=1.75,
        o2_tortuosity_mode="Bruggeman", o2_manual_tortuosity=1.46,
        o2_use_knudsen=True, o2_top_access=True, o2_side_access=False,
        o2_bottom_access=False, nx=4, ny=5, nz=4, max_dt_s=5.0,
        record_interval_s=15.0, snapshot_interval_s=20.0,
        initial_temp_c=25.0,
        oven_profile_min_c=[(0.0, 25.0), (0.8, 200.0), (2.0, 200.0), (3.0, 50.0)],
    )
    values.update(overrides)
    return mod.SimulationInputs(**values)


class Checker:
    def __init__(self):
        self.count = 0
        self.cases = []

    def equal(self, left, right, path):
        self.count += 1
        if isinstance(left, np.ndarray):
            np.testing.assert_array_equal(left, right, err_msg=path, strict=True)
        elif isinstance(left, dict):
            assert left.keys() == right.keys(), path
            for key in left:
                self.equal(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, (tuple, list)):
            assert len(left) == len(right), path
            for i, (a, b) in enumerate(zip(left, right)):
                self.equal(a, b, f"{path}[{i}]")
        elif isinstance(left, float) and np.isnan(left):
            assert np.isnan(right), path
        else:
            assert left == right, f"{path}: {left!r} != {right!r}"


def run(original, candidate):
    checker = Checker()
    scenarios = [
        ("psd_uniform_o2", {}),
        ("manual_heat_only_closed_lid", dict(k_mode="Manual", o2_enabled=False, top_open=False)),
        ("calibrated_gradient_o2_side_access", dict(k_mode="Calibrated", o2_initial_percent=1.0, o2_side_access=True, calibration_k_scale=1.8, calibration_h_wm2k=20.0)),
        ("manual_tortuosity_no_knudsen", dict(o2_initial_percent=0.0, o2_tortuosity_mode="Manual", o2_manual_tortuosity=2.0, o2_use_knudsen=False)),
        ("sealed_o2_and_cooling", dict(o2_initial_percent=5.0, o2_top_access=False, o2_side_access=False, initial_temp_c=200.0, oven_profile_min_c=[(0.0, 200.0), (1.0, 30.0), (3.0, 30.0)])),
        ("no_radiation_no_snapshots", dict(emissivity=0.0, floor_emissivity=0.0, bottom_view_factor=0.0)),
    ]
    for name, overrides in scenarios:
        callbacks = [[], []]
        results = []
        for idx, mod in enumerate((original, candidate)):
            result = mod.simulate(inputs(mod, **overrides), progress_cb=callbacks[idx].append, store_snapshots=name != "no_radiation_no_snapshots")
            results.append(result)
        checker.equal([f.name for f in dataclasses.fields(results[0])], [f.name for f in dataclasses.fields(results[1])], f"{name}.fields")
        for field in dataclasses.fields(results[0]):
            checker.equal(getattr(results[0], field.name), getattr(results[1], field.name), f"{name}.{field.name}")
        checker.equal(callbacks[0], callbacks[1], f"{name}.progress")
        assert results[0].snapshots_c.shape[-3:] == (4, 5, 4)
        if name == "psd_uniform_o2":
            expected_uniform = np.asarray(20.95, dtype=results[0].o2_snapshots_percent.dtype)
            np.testing.assert_array_equal(results[0].o2_snapshots_percent, np.full_like(results[0].o2_snapshots_percent, expected_uniform))
        if name == "calibrated_gradient_o2_side_access":
            assert results[0].o2_center_percent[-1] > results[0].o2_center_percent[0]
        checker.cases.append(name)

    temps = np.array([-20.0, 25.0, 100.0, 300.0, 600.0])
    for mode in ("Manual", "PSD estimate", "Calibrated"):
        for helper in ("effective_k", "oxygen_effective_diffusivity", "oxygen_tortuosity", "oxygen_characteristic_pore_m", "effective_h_conv"):
            values = []
            for mod in (original, candidate):
                inp = inputs(mod, k_mode=mode)
                fn = getattr(mod, helper)
                values.append(fn(inp, temps) if helper in ("effective_k", "oxygen_effective_diffusivity") else fn(inp))
            checker.equal(*values, f"helper.{mode}.{helper}")

    invalid_cases = [
        dict(lx_mm=0), dict(nx=2), dict(nx=101), dict(emissivity=1.01),
        dict(bed_density_kgm3=3210), dict(k_mode="unknown"),
        dict(d10_um=200.0), dict(contact_blend=1.1),
        dict(calibration_k_scale=0, k_mode="Calibrated"),
        dict(o2_initial_percent=-0.1), dict(o2_ambient_percent=0),
        dict(o2_manual_tortuosity=0.5, o2_tortuosity_mode="Manual"),
        dict(o2_bottom_access=True), dict(o2_tortuosity_mode="invalid"),
        dict(oven_profile_min_c=[(1, 25), (2, 30)]),
        dict(oven_profile_min_c=[(0, 25), (0, 30)]),
    ]
    for index, overrides in enumerate(invalid_cases):
        errors = []
        for mod in (original, candidate):
            try:
                mod.validate_inputs(inputs(mod, **overrides))
            except ValueError as exc:
                errors.append(str(exc))
            else:
                raise AssertionError(f"invalid input {index} accepted")
        checker.equal(*errors, f"validation[{index}]")
    checker.cases.append(f"{len(invalid_cases)} validation failures")

    errors = []
    for mod in (original, candidate):
        stop = threading.Event()
        stop.set()
        try:
            mod.simulate(inputs(mod), stop_event=stop)
        except RuntimeError as exc:
            errors.append(str(exc))
        else:
            raise AssertionError("cancellation did not stop the solver")
    checker.equal(*errors, "cancellation")
    checker.cases.append("cancellation")

    # Exact synthetic TC data at the configured starting parameters makes this
    # fast while exercising SciPy fitting, callback, final solve and result keys.
    truth = original.simulate(inputs(original, k_mode="Calibrated", o2_enabled=False), store_snapshots=False)
    fits, events = [], [[], []]
    for index, mod in enumerate((original, candidate)):
        fits.append(mod.fit_thermal_calibration(inputs(mod), truth.time_min, truth.center_c, status_cb=lambda *event, i=index: events[i].append(event)))
    checker.equal(*fits, "calibration")
    checker.equal(*events, "calibration.progress")
    checker.cases.append("synthetic thermocouple calibration")
    return checker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, default=ORIGINAL)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    checker = run(load(args.original, "thermal_original"), load(args.candidate, "thermal_candidate"))
    report = dict(passed=True, assertions=checker.count, cases=checker.cases,
                  elapsed_seconds=round(time.perf_counter() - started, 3),
                  original=str(args.original.resolve()), candidate=str(args.candidate.resolve()),
                  comparison="bit-for-bit equality, equal-NaN for all SimulationResult arrays and scalar/dict fields")
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
