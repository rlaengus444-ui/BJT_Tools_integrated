"""Independent analytical/energy checks of the oven's numerical model.

These checks do not use solver outputs as an oracle.  The O2 slab solution is
the continuum solution for fixed concentration at the top and no flux below.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
import os
from pathlib import Path
import sys

import numpy as np
import pytest


@pytest.fixture(scope="module")
def oven():
    source = Path(os.environ.get(
        "OVEN_REVIEW_SOURCE",
        str(Path(__file__).resolve().parents[1] / "sources" / "sic_buildbox_thermal_gui_v5.py"),
    ))
    spec = importlib.util.spec_from_file_location("_oven_physics_review", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(oven):
    return oven.SimulationInputs(
        lx_mm=100., ly_mm=100., lz_mm=30.,
        box_k=16.2, box_density_kgm3=7930., box_cp_jkgk=500.,
        side_thickness_mm=3., bottom_thickness_mm=5.,
        top_thickness_mm=3., top_open=True,
        bed_density_kgm3=1600., true_density_kgm3=3200., cp_jkgk=750.,
        k_eff_wmk=.6, k_solid_wmk=80., gas_k_wmk=.026,
        k_mode="Manual", d10_um=40.6, d50_um=65.4, d90_um=105.,
        solid_k_temp_exponent=.75, contact_blend=.5,
        calibration_k_scale=1., calibration_h_wm2k=12.,
        h_conv_wm2k=12., emissivity=.75, bottom_gap_mm=40.,
        floor_emissivity=.8, bottom_view_factor=.95,
        o2_enabled=True, o2_ambient_percent=20., o2_initial_percent=0.,
        o2_diff_air_25_m2s=2e-5, o2_diff_temp_exponent=1.75,
        o2_tortuosity_mode="Manual", o2_manual_tortuosity=2.,
        o2_use_knudsen=False, o2_top_access=True, o2_side_access=False,
        o2_bottom_access=False, nx=3, ny=3, nz=20, max_dt_s=1.,
        record_interval_s=.36, snapshot_interval_s=7.2,
        initial_temp_c=25., oven_profile_min_c=[(0., 25.), (.12, 25.)],
    )


def _slab_fraction(z, length, diffusivity, time_s):
    """Concentration/ambient; c(L,t)=ambient, dc/dz(0,t)=0, c(z,0)=0."""
    n = np.arange(200)
    mu = (n + .5) * np.pi
    deficiency = np.sum(
        2 * (-1.)**n / mu * np.cos(mu * z / length)
        * np.exp(-mu**2 * diffusivity * time_s / length**2)
    )
    return 1. - deficiency


def _analytic_center(inp, result):
    length = inp.lz_mm / 1000.
    # The GUI's center metric averages the two central cells for even nz.
    zs = length * np.array([inp.nz / 2 - .5, inp.nz / 2 + .5]) / inp.nz
    # Storage is porosity*c and bulk flux is (porosity/tau)*D_pore*grad(c).
    # For uniform porosity the porosity cancels in the concentration PDE.
    transient_d = inp.o2_diff_air_25_m2s / inp.o2_manual_tortuosity
    seconds = result.time_min[-1] * 60.
    return inp.o2_ambient_percent * np.mean([
        _slab_fraction(z, length, transient_d, seconds) for z in zs
    ])


def test_radiative_secant_is_exact_stefan_boltzmann_flux(oven):
    for surface, ambient in [(25., 200.), (200., 25.), (200., 200.)]:
        eps = .73
        expected = eps * 5.670374419e-8 * (
            (ambient + 273.15)**4 - (surface + 273.15)**4
        )
        actual = oven.h_radiation(eps, surface, ambient) * (ambient - surface)
        assert actual == pytest.approx(expected, rel=2e-15, abs=1e-12)


def test_wall_powder_conductances_are_series_thermal_resistances(oven):
    external = oven.external_to_wall_midplane_u(12., .003, 16.2)
    interior = oven.wall_midplane_to_powder_u(.003, 16.2, .0015, .6)
    combined = 1. / (1. / external + 1. / interior)
    direct = 1. / (1. / 12. + .003 / 16.2 + .0015 / .6)
    assert combined == pytest.approx(direct, rel=2e-15)
    assert oven.overall_u_to_cell(12., .003, 16.2, .0015, .6) == pytest.approx(direct)


def test_stable_horizontal_gap_is_air_conduction_only(oven):
    gap, lower_c, upper_c = .04, 25., 100.
    conductivity = .026 * (((lower_c + upper_c) / 2 + 273.15) / 298.15)**.86
    assert oven.horizontal_air_gap_natural_h(gap, lower_c, upper_c) == pytest.approx(conductivity / gap)
    assert oven.horizontal_air_gap_natural_h(gap, upper_c, lower_c) >= conductivity / gap


def test_bottom_gray_surface_exchange_and_flux_units(oven, inputs):
    plate_c, floor_c = 25., 200.
    air, radiation, h_air = oven.bottom_cavity_heat_fluxes(inputs, plate_c, floor_c)
    gray_resistance = 1 / inputs.emissivity + 1 / inputs.floor_emissivity - 1
    expected = inputs.bottom_view_factor * 5.670374419e-8 / gray_resistance * (
        (floor_c + 273.15)**4 - (plate_c + 273.15)**4
    )
    assert radiation == pytest.approx(expected, rel=2e-15)
    assert air == pytest.approx(h_air * (floor_c - plate_c))
    _, no_rad, _ = oven.bottom_cavity_heat_fluxes(replace(inputs, floor_emissivity=0.), plate_c, floor_c)
    assert no_rad == 0.


def test_zero_flux_metal_laplacian_conserves_energy(oven):
    field = np.arange(35, dtype=float).reshape(5, 7)**2 + 10.
    rate = np.zeros_like(field)
    oven.add_neumann_laplacian_2d(rate, field, .013, .017, 4.1e-6)
    assert np.sum(rate) == pytest.approx(0., abs=1e-12)
    uniform_rate = np.zeros_like(field)
    oven.add_neumann_laplacian_2d(uniform_rate, np.full_like(field, 20.), .013, .017, 4.1e-6)
    assert np.count_nonzero(uniform_rate) == 0


def test_harmonic_face_coefficient_matches_two_half_cell_resistances(oven):
    a, b = 0.1, 3.7
    expected = 1. / (.5 / a + .5 / b)
    assert oven.harmonic_mean(a, b) == pytest.approx(expected)


def test_nonuniform_closed_powder_and_metal_system_conserves_total_heat(oven, inputs, monkeypatch):
    inp = replace(inputs, o2_enabled=False, nx=4, ny=5, nz=6,
                  max_dt_s=.001, record_interval_s=.01,
                  oven_profile_min_c=[(0., 25.), (.01 / 60., 25.)])
    original_full = np.full
    initial_powder = np.linspace(5., 95., 4 * 5 * 6).reshape(6, 5, 4)

    def initial_field(shape, value, *args, **kwargs):
        if shape == (6, 5, 4):
            return initial_powder.copy()
        return original_full(shape, value, *args, **kwargs)

    monkeypatch.setattr(oven.np, "full", initial_field)
    monkeypatch.setattr(oven, "external_to_wall_midplane_u", lambda *args: 0.)
    monkeypatch.setattr(oven, "overall_u_to_cell", lambda *args: 0.)
    monkeypatch.setattr(oven, "bottom_cavity_heat_fluxes", lambda inp, plate, floor: (np.zeros_like(plate), np.zeros_like(plate), np.zeros_like(plate)))
    result = oven.simulate(inp, store_snapshots=False)
    lx, ly, lz = np.array([inp.lx_mm, inp.ly_mm, inp.lz_mm]) / 1000.
    powder_capacity = inp.bed_density_kgm3 * inp.cp_jkgk * lx * ly * lz
    metal_capacity_per_m3 = inp.box_density_kgm3 * inp.box_cp_jkgk
    side_capacity = metal_capacity_per_m3 * inp.side_thickness_mm / 1000. * 2 * lz * (lx + ly)
    bottom_capacity = metal_capacity_per_m3 * inp.bottom_thickness_mm / 1000. * lx * ly
    energy = (powder_capacity * result.avg_c + side_capacity * result.side_wall_avg_c
              + bottom_capacity * result.bottom_plate_avg_c)
    assert energy[-1] == pytest.approx(energy[0], rel=2e-14, abs=1e-8)
    assert result.avg_c[-1] != result.avg_c[0]  # Nontrivial interfacial transfer.


def test_one_step_total_heat_matches_external_boundary_power(oven, inputs):
    seconds = .001
    inp = replace(inputs, o2_enabled=False, nx=3, ny=3, nz=3,
                  max_dt_s=seconds, record_interval_s=seconds,
                  oven_profile_min_c=[(0., 100.), (seconds / 60., 100.)])
    result = oven.simulate(inp, store_snapshots=False)
    assert len(result.time_min) == 2
    lx, ly, lz = np.array([inp.lx_mm, inp.ly_mm, inp.lz_mm]) / 1000.
    rho_cp = inp.bed_density_kgm3 * inp.cp_jkgk
    metal_rho_cp = inp.box_density_kgm3 * inp.box_cp_jkgk
    side_area = 2 * lz * (lx + ly)
    energy_change = (
        rho_cp * lx * ly * lz * (result.avg_c[-1] - inp.initial_temp_c)
        + metal_rho_cp * inp.side_thickness_mm / 1000. * side_area
        * (result.side_wall_avg_c[-1] - inp.initial_temp_c)
        + metal_rho_cp * inp.bottom_thickness_mm / 1000. * lx * ly
        * (result.bottom_plate_avg_c[-1] - inp.initial_temp_c)
    )
    external_h = inp.h_conv_wm2k + oven.h_radiation(inp.emissivity, inp.initial_temp_c, 100.)
    side_u = 1 / (1 / external_h + inp.side_thickness_mm / 2000. / inp.box_k)
    top_u = 1 / (1 / external_h + (lz / inp.nz / 2) / inp.k_eff_wmk)
    air, radiation, _ = oven.bottom_cavity_heat_fluxes(inp, inp.initial_temp_c, 100.)
    expected_power = (side_u * side_area + top_u * lx * ly) * (100. - inp.initial_temp_c) + (air + radiation) * lx * ly
    assert energy_change == pytest.approx(expected_power * seconds, rel=1e-8, abs=1e-8)


def test_thermal_and_oxygen_equilibrium_are_exactly_preserved(oven, inputs):
    inp = replace(inputs, initial_temp_c=200., o2_initial_percent=20.,
                  oven_profile_min_c=[(0., 200.), (.01, 200.)])
    result = oven.simulate(inp)
    np.testing.assert_array_equal(result.avg_c, 200.)
    # The area-weighted wall mean introduces one rounding operation.
    np.testing.assert_allclose(result.side_wall_avg_c, 200., rtol=0., atol=5e-14)
    np.testing.assert_array_equal(result.bottom_plate_avg_c, 200.)
    np.testing.assert_array_equal(result.o2_center_percent, 20.)
    assert result.o2_center_dose_air_h[-1] == pytest.approx(.01 / 60.)


def test_oxygen_effective_coefficient_retains_bulk_area_flux_definition(oven, inputs):
    porosity = 1 - inputs.bed_density_kgm3 / inputs.true_density_kgm3
    expected_bulk_coefficient = inputs.o2_diff_air_25_m2s * porosity / inputs.o2_manual_tortuosity
    assert oven.oxygen_effective_diffusivity(inputs, 25.) == pytest.approx(expected_bulk_coefficient)


@pytest.mark.parametrize("porosity", [.25, .5, .75])
def test_pore_oxygen_ingress_matches_analytic_diffusion_with_porosity_storage(oven, inputs, porosity):
    inp = replace(inputs, bed_density_kgm3=inputs.true_density_kgm3 * (1 - porosity))
    result = oven.simulate(inp)
    assert result.o2_center_percent[-1] == pytest.approx(_analytic_center(inp, result), abs=.004)
    np.testing.assert_array_equal(result.avg_c, 25.)


def test_oxygen_slab_error_decreases_with_grid_refinement(oven, inputs):
    errors = []
    for nz in [10, 20, 40]:
        inp = replace(inputs, nz=nz)
        result = oven.simulate(inp)
        errors.append(abs(result.o2_center_percent[-1] - _analytic_center(inp, result)))
    assert errors[1] < errors[0] / 3
    assert errors[2] < errors[1] / 3
    assert errors[2] < .001


def test_oxygen_stability_bound_includes_pore_storage_factor(oven, inputs):
    dx, dy, dz = np.array([inputs.lx_mm / inputs.nx, inputs.ly_mm / inputs.ny, inputs.lz_mm / inputs.nz]) / 1000.
    transient_d = inputs.o2_diff_air_25_m2s / inputs.o2_manual_tortuosity
    conservative_exposed_face_bound = .25 / (transient_d * (1 / dx**2 + 1 / dy**2 + 1 / dz**2))
    assert oven.oxygen_diffusion_stable_dt(inputs, 25.) <= conservative_exposed_face_bound


def test_small_cells_never_raise_dt_above_explicit_heat_stability_bound(oven, inputs):
    inp = replace(inputs, o2_enabled=False, lx_mm=.01, ly_mm=.01, lz_mm=.01, nx=3, ny=3, nz=3)
    dx = inp.lx_mm / 1000. / inp.nx
    alpha = inp.k_eff_wmk / (inp.bed_density_kgm3 * inp.cp_jkgk)
    diffusion_bound = .5 / (alpha * 3 / dx**2)
    assert oven.estimate_stable_dt(inp, 200.) <= diffusion_bound


def test_oxygen_stability_includes_a_hotter_initial_bed(oven, inputs):
    inp = replace(inputs, initial_temp_c=600.)
    dx, dy, dz = np.array([inp.lx_mm / inp.nx, inp.ly_mm / inp.ny, inp.lz_mm / inp.nz]) / 1000.
    porosity = 1 - inp.bed_density_kgm3 / inp.true_density_kgm3
    hot_transient_d = oven.oxygen_effective_diffusivity(inp, 600.) / porosity
    conservative_bound = .25 / (hot_transient_d * (1 / dx**2 + 1 / dy**2 + 1 / dz**2))
    assert oven.oxygen_diffusion_stable_dt(inp, 25.) <= conservative_bound


def test_heat_stability_includes_a_hotter_initial_bed(oven, inputs):
    inp = replace(inputs, o2_enabled=False, initial_temp_c=600., nx=3, ny=3, nz=3,
                  lx_mm=400., ly_mm=500., lz_mm=350., max_dt_s=1000.)
    assert oven.estimate_stable_dt(inp, 25.) == oven.estimate_stable_dt(inp, 600.)


@pytest.mark.parametrize("changes", [
    {"h_conv_wm2k": math.nan},
    {"box_k": math.inf},
    {"max_dt_s": math.nan},
    {"initial_temp_c": -300.},
    {"oven_profile_min_c": [(0., 25.), (.12, math.nan)]},
    {"oven_profile_min_c": [(0., 25.), (.12, -300.)]},
    {"top_open": False, "top_thickness_mm": -1.},
    {"nx": 3.5},
])
def test_nonphysical_or_nonfinite_inputs_are_rejected_before_solving(oven, inputs, changes):
    with pytest.raises(ValueError):
        oven.validate_inputs(replace(inputs, **changes))
