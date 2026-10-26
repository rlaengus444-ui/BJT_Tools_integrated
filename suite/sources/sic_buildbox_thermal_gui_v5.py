
# -*- coding: utf-8 -*-
"""
SiC Binder-Jet Build Box Thermal Simulator
------------------------------------------
- GUI: tkinter/ttk
- Core calculation: NumPy vectorized 3D transient finite-volume model
- Boundary: oven-air heating of side walls, in-plane metal conduction into the bottom plate,
  40-mm under-plate radiation/natural air-layer heat transfer, and wall-to-powder coupling
- Output: time-history + interactive time-dependent 3D powder temperature field
- Designed for Windows + Anaconda/PyCharm use

Important modeling assumptions
1) Powder bed is treated as a homogeneous porous continuum. k_eff can be:
   - Manual constant
   - PSD-informed and temperature-dependent
   - PSD-informed and calibrated to a center thermocouple run.
2) The four side walls and bottom plate have separate temperature fields, thermal mass,
   in-plane metal conduction, and conservative thermal coupling at their shared edges.
3) The bottom plate is 40 mm above the oven floor by default. The under-plate cavity has
   floor-to-plate radiation and horizontal-air-layer natural convection/conduction, but no
   forced convection. Side-wall-to-bottom metal conduction remains active. The oven-floor
   temperature follows the oven profile. A closed top/lid remains resistance-only.
4) Oven air temperature follows the user-entered profile by linear interpolation.
5) Convection coefficient and emissivity are effective values and should ideally be
   calibrated with at least one thermocouple experiment.
6) The PSD model is an engineering estimate, not a universal packed-bed correlation.
   Bed density still carries most of the packing information. D10/D50/D90 refine PSD
   breadth, characteristic pore length and the radiative contribution.
7) Heat generation from binder reaction/evaporation is not included.
8) Moisture / solvent evaporation latent heat is not included.

The purpose of this program is to estimate heat-up lag and center temperature history.
For higher fidelity, calibrate k_eff and h_conv from measured data.

O2 diffusion-only model:
- Optional 3D oxygen diffusion is solved on the same grid and time step as heat transfer.
- Oxygen diffusivity varies with local temperature and packed-bed porosity/tortuosity.
- D10/D50/D90 are used to estimate a characteristic pore size for optional Knudsen correction.
- No oxygen consumption, PVP oxidation, reaction kinetics, gas convection, or pressure-driven flow
  is included in this mode. If initial pore O2 equals ambient O2, the O2 field remains uniform.

GUI persistence:
- The exact inputs used for each valid Run are automatically saved.
- Saved values and the entire oven profile are automatically restored next launch.
- Settings are stored in the user's home folder as
  .sic_buildbox_thermal_gui_settings.json.
"""

import sys
import subprocess
import importlib
import math
import threading
import queue
import csv
import json
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, replace
from typing import List, Tuple, Dict

# -----------------------------------------------------------------------------
# Dependency bootstrap
# -----------------------------------------------------------------------------
def ensure_package(import_name: str, pip_name: str = None):
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pip_name = pip_name or import_name
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
        return importlib.import_module(import_name)

np = ensure_package("numpy")
matplotlib = ensure_package("matplotlib")

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# The standalone source and the frozen/integrated app share the same explicit
# presentation catalogue. No translated value is passed to the solver or CSV.
try:
    from oven_i18n import OvenTranslator, LocalizedWidgets, LocalizedDialogs, MODEL_INFO_EN
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from oven_i18n import OvenTranslator, LocalizedWidgets, LocalizedDialogs, MODEL_INFO_EN

SIGMA = 5.670374419e-8  # W/m²/K⁴


# -----------------------------------------------------------------------------
# Material presets
# -----------------------------------------------------------------------------
BOX_MATERIALS = {
    "SUS304": 16.2,
    "SUS316": 16.3,
    "Carbon steel": 45.0,
    "Al6061": 167.0,
    "Al5052": 138.0,
    "Inconel 625": 9.8,
    "Custom": 16.0,
}

# Starter thermal-mass properties for the build-box metal. Values remain
# editable because alloy grade and temperature can change them appreciably.
BOX_THERMAL_MASS = {
    "SUS304": {"rho": 7930.0, "cp": 500.0},
    "SUS316": {"rho": 7980.0, "cp": 500.0},
    "Carbon steel": {"rho": 7850.0, "cp": 490.0},
    "Al6061": {"rho": 2700.0, "cp": 897.0},
    "Al5052": {"rho": 2680.0, "cp": 900.0},
    "Inconel 625": {"rho": 8440.0, "cp": 410.0},
    "Custom": {"rho": 7930.0, "cp": 500.0},
}

# These values are only starter values and remain fully editable.
# GUI density presets are stored in g/cc.
SIC_PRESETS = {
    "SiC general": {"rho_true_gcc": 3.210, "cp": 750.0, "k_solid": 80.0},
    "α-SiC": {"rho_true_gcc": 3.210, "cp": 750.0, "k_solid": 100.0},
    "β-SiC": {"rho_true_gcc": 3.210, "cp": 750.0, "k_solid": 80.0},
    "Custom": {"rho_true_gcc": 3.210, "cp": 750.0, "k_solid": 80.0},
}


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------
@dataclass
class SimulationInputs:
    # Geometry
    lx_mm: float
    ly_mm: float
    lz_mm: float

    # Wall
    box_k: float
    box_density_kgm3: float
    box_cp_jkgk: float
    side_thickness_mm: float
    bottom_thickness_mm: float
    top_thickness_mm: float
    top_open: bool

    # Powder
    bed_density_kgm3: float
    true_density_kgm3: float
    cp_jkgk: float
    k_eff_wmk: float
    k_solid_wmk: float
    gas_k_wmk: float

    # Effective-conductivity model
    k_mode: str
    d10_um: float
    d50_um: float
    d90_um: float
    solid_k_temp_exponent: float
    contact_blend: float
    calibration_k_scale: float
    calibration_h_wm2k: float

    # Boundary
    h_conv_wm2k: float
    emissivity: float
    bottom_gap_mm: float
    floor_emissivity: float
    bottom_view_factor: float

    # O2 diffusion-only model
    o2_enabled: bool
    o2_ambient_percent: float
    o2_initial_percent: float
    o2_diff_air_25_m2s: float
    o2_diff_temp_exponent: float
    o2_tortuosity_mode: str
    o2_manual_tortuosity: float
    o2_use_knudsen: bool
    o2_top_access: bool
    o2_side_access: bool
    o2_bottom_access: bool

    # Numerical
    nx: int
    ny: int
    nz: int
    max_dt_s: float
    record_interval_s: float
    snapshot_interval_s: float
    initial_temp_c: float

    # Oven profile
    oven_profile_min_c: List[Tuple[float, float]]


@dataclass
class SimulationResult:
    time_min: np.ndarray
    oven_c: np.ndarray
    center_c: np.ndarray
    surface_c: np.ndarray
    avg_c: np.ndarray
    min_c: np.ndarray
    max_c: np.ndarray
    side_wall_avg_c: np.ndarray
    bottom_plate_avg_c: np.ndarray
    bottom_gap_h_wm2k: np.ndarray
    bottom_gap_air_w: np.ndarray
    bottom_gap_radiation_w: np.ndarray
    bottom_gap_mm: float
    floor_emissivity: float
    bottom_view_factor: float
    dt_used_s: float
    porosity: float
    solid_fraction: float
    k_eff: float
    k_mode: str
    k_center_wmk: np.ndarray
    grid_shape: Tuple[int, int, int]
    threshold_hours: Dict[float, float]

    # Time-dependent 3D fields for visualization.
    snapshot_time_min: np.ndarray
    snapshots_c: np.ndarray
    snapshot_interval_used_s: float
    geometry_mm: Tuple[float, float, float]

    # O2 diffusion-only outputs
    o2_enabled: bool
    o2_center_percent: np.ndarray
    o2_top_percent: np.ndarray
    o2_avg_percent: np.ndarray
    o2_min_percent: np.ndarray
    o2_max_percent: np.ndarray
    o2_deff_center_m2s: np.ndarray
    o2_center_dose_air_h: np.ndarray
    o2_snapshots_percent: np.ndarray


# -----------------------------------------------------------------------------
# Physics helpers
# -----------------------------------------------------------------------------
def oven_temp_at(t_s: float, profile: np.ndarray) -> float:
    """Linear interpolation through time-temperature profile."""
    t_min = t_s / 60.0
    return float(np.interp(t_min, profile[:, 0], profile[:, 1]))


def h_radiation(eps: float, ts_c: float, tenv_c: float) -> float:
    """
    Linearized radiative heat transfer coefficient:
        h_rad = eps*sigma*(Ts+Tenv)*(Ts^2+Tenv^2)
    temperatures in Kelvin.
    """
    ts = max(ts_c + 273.15, 1.0)
    te = max(tenv_c + 273.15, 1.0)
    return eps * SIGMA * (ts + te) * (ts * ts + te * te)


def overall_h(h_out: float, wall_t_m: float, wall_k: float) -> float:
    """Outside film + wall conduction in series."""
    h_out = max(h_out, 1e-9)
    r = 1.0 / h_out
    if wall_t_m > 0.0:
        r += wall_t_m / max(wall_k, 1e-9)
    return 1.0 / r


def maxwell_gas_matrix_keff(phi_s: float, k_s: float, k_g: float) -> float:
    """
    Maxwell-Eucken style estimate with gas as continuous phase.
    Useful as a conservative starter, NOT a calibrated packed-bed correlation.
    """
    phi_s = min(max(phi_s, 0.0), 0.999999)
    num = k_s + 2.0 * k_g + 2.0 * phi_s * (k_s - k_g)
    den = k_s + 2.0 * k_g - phi_s * (k_s - k_g)
    return k_g * num / max(den, 1e-12)


def geometric_mean_keff(phi_s: float, k_s: float, k_g: float) -> float:
    """
    Upper-ish heuristic for sensitivity checking.
    Not a substitute for a validated packed-bed correlation.
    """
    phi_s = min(max(phi_s, 0.0), 1.0)
    return (k_s ** phi_s) * (k_g ** (1.0 - phi_s))



def _maxwell_gas_matrix_array(phi_s: float, k_s, k_g):
    """Vectorized Maxwell-Eucken estimate with gas as continuous phase."""
    phi_s = float(np.clip(phi_s, 0.0, 0.999999))
    ks = np.asarray(k_s, dtype=np.float64)
    kg = np.asarray(k_g, dtype=np.float64)
    num = ks + 2.0 * kg + 2.0 * phi_s * (ks - kg)
    den = ks + 2.0 * kg - phi_s * (ks - kg)
    return kg * num / np.maximum(den, 1e-30)


def _geometric_array(phi_s: float, k_s, k_g):
    """Vectorized geometric mean bound/heuristic."""
    phi_s = float(np.clip(phi_s, 0.0, 1.0))
    ks = np.maximum(np.asarray(k_s, dtype=np.float64), 1e-12)
    kg = np.maximum(np.asarray(k_g, dtype=np.float64), 1e-12)
    return np.exp(phi_s * np.log(ks) + (1.0 - phi_s) * np.log(kg))


def psd_statistics(d10_um: float, d50_um: float, d90_um: float):
    """
    Approximate a lognormal PSD from D10/D50/D90.

    sigma_ln is estimated from the 10th and 90th percentiles.
    d32 is a lognormal Sauter-mean estimate M3/M2.
    """
    d10 = max(float(d10_um), 1e-9)
    d50 = max(float(d50_um), 1e-9)
    d90 = max(float(d90_um), 1e-9)
    z90 = 1.2815515655446004
    sigma_ln = max((math.log(d90) - math.log(d10)) / (2.0 * z90), 1e-9)
    d32_um = d50 * math.exp(2.5 * sigma_ln * sigma_ln)
    span = (d90 - d10) / d50
    return {
        "span": span,
        "sigma_ln": sigma_ln,
        "d32_um": d32_um,
    }


def gas_k_temperature(k_ref_25c: float, temp_c):
    """
    Approximate dry-air/gas conductivity scaling near atmospheric pressure.
    k(T) ~ k_ref * (T_K / 298.15)^0.86
    """
    tk = np.maximum(np.asarray(temp_c, dtype=np.float64) + 273.15, 1.0)
    return max(float(k_ref_25c), 1e-9) * (tk / 298.15) ** 0.86


def horizontal_air_gap_natural_h(
    gap_m: float,
    floor_temp_c,
    upper_plate_temp_c,
    k_air_ref_25c: float = 0.026,
):
    """
    Effective heat-transfer coefficient across a horizontal air layer [W/m²K].

    The lower oven floor and upper build-box plate form an air layer heated
    from below when T_floor > T_plate. A Hollands-type horizontal-layer
    correlation is used for buoyancy enhancement. When the layer is stable
    (floor not hotter), Nu=1 and only gas conduction remains. No forced-flow
    term is included.
    """
    gap = max(float(gap_m), 1e-6)
    tf = np.asarray(floor_temp_c, dtype=np.float64)
    tp = np.asarray(upper_plate_temp_c, dtype=np.float64)
    film_k = np.maximum(0.5 * (tf + tp) + 273.15, 150.0)

    k_air = max(float(k_air_ref_25c), 1e-9) * (film_k / 298.15) ** 0.86

    # Dry-air engineering properties at approximately 1 atm.
    mu = 1.716e-5 * (film_k / 273.15) ** 1.5 * (
        (273.15 + 111.0) / (film_k + 111.0)
    )
    rho = 101325.0 / (287.058 * film_k)
    cp_air = 1007.0
    nu = mu / np.maximum(rho, 1e-30)
    alpha = k_air / np.maximum(rho * cp_air, 1e-30)
    beta = 1.0 / film_k

    delta_t_unstable = np.maximum(tf - tp, 0.0)
    ra = (
        9.80665 * beta * delta_t_unstable * gap**3
        / np.maximum(nu * alpha, 1e-30)
    )

    term_laminar = np.maximum(1.0 - 1708.0 / np.maximum(ra, 1e-30), 0.0)
    term_high_ra = np.maximum(
        (ra / 5830.0) ** (1.0 / 3.0) - 1.0,
        0.0,
    )
    nusselt = 1.0 + 1.44 * term_laminar + term_high_ra
    out = nusselt * k_air / gap
    return float(out) if np.asarray(out).ndim == 0 else out


def bottom_cavity_heat_fluxes(inp: SimulationInputs, bottom_plate_temp_c,
                              floor_temp_c: float):
    """
    Under-plate heat fluxes [W/m²] from the oven floor to the bottom plate.

    Includes horizontal air-layer conduction/natural convection and diffuse
    gray radiation. The floor temperature follows the oven profile. Forced
    convection is deliberately absent.
    """
    plate = np.asarray(bottom_plate_temp_c, dtype=np.float64)
    floor_c = float(floor_temp_c)
    gap_m = inp.bottom_gap_mm / 1000.0

    h_air = horizontal_air_gap_natural_h(
        gap_m,
        floor_c,
        plate,
        inp.gas_k_wmk,
    )
    q_air = h_air * (floor_c - plate)

    eps_plate = float(np.clip(inp.emissivity, 0.0, 1.0))
    eps_floor = float(np.clip(inp.floor_emissivity, 0.0, 1.0))
    if eps_plate <= 0.0 or eps_floor <= 0.0:
        eps_eff = 0.0
    else:
        eps_eff = 1.0 / (1.0 / eps_plate + 1.0 / eps_floor - 1.0)

    floor_k = max(floor_c + 273.15, 1.0)
    plate_k = np.maximum(plate + 273.15, 1.0)
    q_rad = (
        float(np.clip(inp.bottom_view_factor, 0.0, 1.0))
        * eps_eff
        * SIGMA
        * (floor_k**4 - plate_k**4)
    )

    return q_air, q_rad, h_air


def solid_k_temperature(k_ref_25c: float, exponent: float, temp_c):
    """
    Engineering temperature scaling for SiC solid conductivity:
    k_s(T) = k_ref * (298.15/T_K)^n
    n is user-editable because actual SiC grade/purity strongly matters.
    """
    tk = np.maximum(np.asarray(temp_c, dtype=np.float64) + 273.15, 1.0)
    n = max(float(exponent), 0.0)
    return max(float(k_ref_25c), 1e-9) * (298.15 / tk) ** n


def effective_h_conv(inp: SimulationInputs) -> float:
    if inp.k_mode == "Calibrated":
        return max(float(inp.calibration_h_wm2k), 1e-9)
    return max(float(inp.h_conv_wm2k), 1e-9)


def effective_k(inp: SimulationInputs, temp_c):
    """
    Effective powder-bed conductivity.

    Manual:
        constant user-entered k_eff.

    PSD estimate:
        temperature-dependent solid/gas terms
        + packing/contact interpolation
        + PSD-informed pore-radiation term.

    Calibrated:
        same PSD(T) model multiplied by a fitted k-scale.

    NOTE:
    This is intentionally labeled an engineering estimate. D10/D50/D90 alone
    cannot uniquely determine particle contact resistance. The calibration mode
    is the preferred route when center thermocouple data exist.
    """
    arr = np.asarray(temp_c, dtype=np.float64)

    if inp.k_mode == "Manual":
        out = np.full_like(arr, max(float(inp.k_eff_wmk), 1e-9), dtype=np.float64)
        return float(out) if out.ndim == 0 else out

    phi = float(np.clip(inp.bed_density_kgm3 / inp.true_density_kgm3, 1e-6, 0.999999))
    por = 1.0 - phi
    stats = psd_statistics(inp.d10_um, inp.d50_um, inp.d90_um)

    kg = gas_k_temperature(inp.gas_k_wmk, arr)
    ks = solid_k_temperature(inp.k_solid_wmk, inp.solid_k_temp_exponent, arr)

    k_low = _maxwell_gas_matrix_array(phi, ks, kg)
    k_geo = _geometric_array(phi, ks, kg)

    # Contact/structure interpolation in logarithmic space.
    # PSD breadth only gives a mild correction because measured bed density
    # already contains most packing information.
    blend = float(np.clip(
        inp.contact_blend + 0.04 * np.clip(stats["span"], 0.0, 2.5),
        0.0, 1.0
    ))
    k_cond = np.exp(
        (1.0 - blend) * np.log(np.maximum(k_low, 1e-12))
        + blend * np.log(np.maximum(k_geo, 1e-12))
    )

    # Characteristic pore chord from solid specific area:
    # L ~ 2*epsilon*d32/(3*phi)
    d32_m = stats["d32_um"] * 1e-6
    pore_chord_m = max(2.0 * por * d32_m / (3.0 * phi), 1e-12)

    tk = np.maximum(arr + 273.15, 1.0)
    eps_rad = float(np.clip(inp.emissivity, 0.0, 1.0))
    k_rad = 4.0 * SIGMA * eps_rad * tk**3 * pore_chord_m

    out = k_cond + k_rad

    if inp.k_mode == "Calibrated":
        out = out * max(float(inp.calibration_k_scale), 1e-9)

    return float(out) if np.asarray(out).ndim == 0 else out


def overall_u_to_cell(h_out: float, wall_t_m: float, wall_k: float,
                      half_cell_m: float, k_bed):
    """
    Oven/environment -> external film -> wall -> half powder cell.
    Returns a scalar or array conductance U [W/m²K].
    """
    kbed = np.maximum(np.asarray(k_bed, dtype=np.float64), 1e-12)
    r = 1.0 / max(float(h_out), 1e-12)
    if wall_t_m > 0.0:
        r = r + wall_t_m / max(float(wall_k), 1e-12)
    r = r + max(float(half_cell_m), 0.0) / kbed
    out = 1.0 / np.maximum(r, 1e-30)
    return float(out) if np.asarray(out).ndim == 0 else out


def external_to_wall_midplane_u(h_out: float, wall_t_m: float, wall_k: float) -> float:
    """Environment film plus conduction through half the metal thickness."""
    r = 1.0 / max(float(h_out), 1e-12)
    r += max(float(wall_t_m), 0.0) / (2.0 * max(float(wall_k), 1e-12))
    return 1.0 / max(r, 1e-30)


def wall_midplane_to_powder_u(wall_t_m: float, wall_k: float,
                              half_cell_m: float, k_bed):
    """Metal mid-plane to powder-cell center conductance [W/m²K]."""
    kbed = np.maximum(np.asarray(k_bed, dtype=np.float64), 1e-12)
    r = max(float(wall_t_m), 0.0) / (2.0 * max(float(wall_k), 1e-12))
    r = r + max(float(half_cell_m), 0.0) / kbed
    out = 1.0 / np.maximum(r, 1e-30)
    return float(out) if np.asarray(out).ndim == 0 else out


def add_neumann_laplacian_2d(rate: np.ndarray, field: np.ndarray,
                             spacing0: float, spacing1: float,
                             diffusivity: float):
    """Add a cell-centered 2D diffusion operator with zero-flux outer edges."""
    rate[1:-1, :] += diffusivity * (
        field[2:, :] - 2.0 * field[1:-1, :] + field[:-2, :]
    ) / spacing0**2
    rate[0, :] += diffusivity * (field[1, :] - field[0, :]) / spacing0**2
    rate[-1, :] += diffusivity * (field[-2, :] - field[-1, :]) / spacing0**2

    rate[:, 1:-1] += diffusivity * (
        field[:, 2:] - 2.0 * field[:, 1:-1] + field[:, :-2]
    ) / spacing1**2
    rate[:, 0] += diffusivity * (field[:, 1] - field[:, 0]) / spacing1**2
    rate[:, -1] += diffusivity * (field[:, -2] - field[:, -1]) / spacing1**2


def harmonic_mean(a, b):
    a = np.maximum(np.asarray(a, dtype=np.float64), 1e-30)
    b = np.maximum(np.asarray(b, dtype=np.float64), 1e-30)
    return 2.0 * a * b / (a + b)



O2_MOLAR_MASS_KG_MOL = 0.0319988
R_GAS = 8.314462618


def oxygen_tortuosity(inp: SimulationInputs) -> float:
    """
    Tortuosity factor used by the diffusion-only engineering model.

    Bruggeman:
        tau = porosity^(-1/2)
        => D_eff = D_free * porosity / tau = D_free * porosity^(3/2)

    Manual:
        user-entered tau >= 1
    """
    por = 1.0 - inp.bed_density_kgm3 / inp.true_density_kgm3
    por = float(np.clip(por, 1e-6, 0.999999))
    if inp.o2_tortuosity_mode == "Bruggeman":
        return por ** -0.5
    return max(float(inp.o2_manual_tortuosity), 1.0)


def oxygen_characteristic_pore_m(inp: SimulationInputs) -> float:
    """
    PSD-informed characteristic pore chord.

    Uses the lognormal D32 estimate and a simple packed-bed specific-area
    relationship:
        L_pore ~ 2 * epsilon * d32 / (3 * solid_fraction)

    This is used only for the optional Knudsen correction. It is not claimed
    to be a directly measured pore-throat diameter.
    """
    phi = inp.bed_density_kgm3 / inp.true_density_kgm3
    phi = float(np.clip(phi, 1e-6, 0.999999))
    por = 1.0 - phi
    stats = psd_statistics(inp.d10_um, inp.d50_um, inp.d90_um)
    d32_m = stats["d32_um"] * 1e-6
    return max(2.0 * por * d32_m / (3.0 * phi), 1e-12)


def oxygen_effective_diffusivity(inp: SimulationInputs, temp_c):
    """
    O2 effective diffusivity in the packed SiC bed [m²/s].

    Molecular diffusion:
        D_m(T) = D_25 * (T_K / 298.15)^n

    Optional Knudsen correction:
        1/D_pore = 1/D_m + 1/D_K

    Packed-bed correction:
        D_eff = D_pore * epsilon / tau

    This is a diffusion-only engineering model at approximately atmospheric
    pressure. It does NOT include oxygen consumption, Darcy flow, natural
    convection inside the bed, thermal transpiration, or pressure changes.
    """
    arr = np.asarray(temp_c, dtype=np.float64)
    tk = np.maximum(arr + 273.15, 1.0)

    d_m = max(float(inp.o2_diff_air_25_m2s), 1e-12) * (
        tk / 298.15
    ) ** max(float(inp.o2_diff_temp_exponent), 0.0)

    d_pore = d_m

    if inp.o2_use_knudsen:
        pore_m = oxygen_characteristic_pore_m(inp)
        thermal_speed = np.sqrt(
            8.0 * R_GAS * tk / (math.pi * O2_MOLAR_MASS_KG_MOL)
        )
        d_k = (pore_m / 3.0) * thermal_speed
        d_pore = 1.0 / (
            1.0 / np.maximum(d_m, 1e-30)
            + 1.0 / np.maximum(d_k, 1e-30)
        )

    por = 1.0 - inp.bed_density_kgm3 / inp.true_density_kgm3
    por = float(np.clip(por, 1e-6, 0.999999))
    tau = oxygen_tortuosity(inp)

    out = d_pore * por / tau
    return float(out) if np.asarray(out).ndim == 0 else out


def oxygen_diffusion_stable_dt(inp: SimulationInputs, max_temp_c: float) -> float:
    """Conservative explicit time-step limit for pore-gas O2 concentration.

    D_eff describes flux per bulk area, while the stored state is pore-gas
    volume fraction. Its accumulation is porosity*c, so a concentration
    gradient evolves with D_eff/porosity. The exactly uniform shortcut needs
    no diffusion update; retain its historical bound to preserve the default
    thermal timestep and temperature histories.
    """
    if not inp.o2_enabled:
        return float("inf")

    lx = inp.lx_mm / 1000.0
    ly = inp.ly_mm / 1000.0
    lz = inp.lz_mm / 1000.0
    dx, dy, dz = lx / inp.nx, ly / inp.ny, lz / inp.nz

    tlo = min(inp.initial_temp_c, 25.0)
    # A cooling run may start hotter than every oven-profile point.
    thi = max(float(max_temp_c), float(inp.initial_temp_c))
    temps = np.linspace(tlo, thi, 20)
    dmax = float(np.max(oxygen_effective_diffusivity(inp, temps)))
    if abs(inp.o2_initial_percent - inp.o2_ambient_percent) >= 1e-12:
        porosity = 1.0 - inp.bed_density_kgm3 / inp.true_density_kgm3
        dmax /= porosity

    invsum = 1.0 / dx**2 + 1.0 / dy**2 + 1.0 / dz**2

    # Dirichlet concentration at an exposed face is located half a cell away,
    # giving a stronger boundary coefficient than an interior face. 0.24 is a
    # deliberately conservative factor for cells on exposed corners.
    factor = 0.24 if (
        inp.o2_top_access or inp.o2_side_access or inp.o2_bottom_access
    ) else 0.45

    return factor / max(dmax * invsum, 1e-30)


def _oxygen_reach_time(time_min, center_percent, ambient_percent, frac):
    """
    First interpolated time at which center reaches frac * ambient concentration.
    Intended for ingress cases where ambient > initial.
    """
    t = np.asarray(time_min, dtype=float)
    y = np.asarray(center_percent, dtype=float)
    target = float(frac) * float(ambient_percent)

    if y.size == 0:
        return None
    if y[0] >= target:
        return 0.0

    idxs = np.where(y >= target)[0]
    if len(idxs) == 0:
        return None

    i = int(idxs[0])
    if i == 0:
        return float(t[0])

    y0, y1 = y[i - 1], y[i]
    t0, t1 = t[i - 1], t[i]
    if abs(y1 - y0) < 1e-30:
        return float(t1)

    f = (target - y0) / (y1 - y0)
    return float(t0 + f * (t1 - t0))


def center_mean(T: np.ndarray) -> float:
    """Average cell(s) surrounding geometric center for even/odd grid sizes."""
    nz, ny, nx = T.shape

    def idxs(n):
        return [n // 2] if n % 2 == 1 else [n // 2 - 1, n // 2]

    iz = idxs(nz)
    iy = idxs(ny)
    ix = idxs(nx)
    vals = T[np.ix_(iz, iy, ix)]
    return float(vals.mean())


def surface_mean(T: np.ndarray) -> float:
    """
    Area-weighting is omitted here because face dimensions are not passed.
    This is a simple mean of six face means, used as a diagnostic trace.
    """
    faces = [
        T[:, :, 0].mean(),
        T[:, :, -1].mean(),
        T[:, 0, :].mean(),
        T[:, -1, :].mean(),
        T[0, :, :].mean(),
        T[-1, :, :].mean(),
    ]
    return float(np.mean(faces))


def estimate_stable_dt(inp: SimulationInputs, oven_max_c: float) -> float:
    """
    Conservative explicit stability estimate using the highest k_eff expected
    over the modeled temperature range.
    """
    lx = inp.lx_mm / 1000.0
    ly = inp.ly_mm / 1000.0
    lz = inp.lz_mm / 1000.0
    dx, dy, dz = lx / inp.nx, ly / inp.ny, lz / inp.nz

    rho_cp = inp.bed_density_kgm3 * inp.cp_jkgk

    tlo = min(inp.initial_temp_c, min(t for _, t in inp.oven_profile_min_c))
    thi = max(inp.initial_temp_c, oven_max_c, max(t for _, t in inp.oven_profile_min_c))
    tsamp = np.linspace(tlo, thi, 24)
    kmax = float(np.max(effective_k(inp, tsamp)))
    alpha_max = kmax / rho_cp

    invsum = (1.0 / dx**2) + (1.0 / dy**2) + (1.0 / dz**2)
    dt_diff = 0.40 / max(alpha_max * invsum, 1e-30)

    # Conservative coupled powder/wall boundary estimate.
    hrad = h_radiation(inp.emissivity, thi, thi)
    hout = effective_h_conv(inp) + hrad

    side_t = inp.side_thickness_mm / 1000.0
    bottom_t = inp.bottom_thickness_mm / 1000.0
    top_t = 0.0 if inp.top_open else inp.top_thickness_mm / 1000.0

    u_side_x = wall_midplane_to_powder_u(side_t, inp.box_k, dx / 2.0, kmax)
    u_side_y = wall_midplane_to_powder_u(side_t, inp.box_k, dy / 2.0, kmax)
    u_bottom = wall_midplane_to_powder_u(bottom_t, inp.box_k, dz / 2.0, kmax)
    u_top = overall_u_to_cell(hout, top_t, inp.box_k, dz / 2.0, kmax)

    lam_powder_bc = (
        u_side_x / dx + u_side_y / dy + max(u_bottom, u_top) / dz
    ) / rho_cp
    dt_bc = 0.35 / max(lam_powder_bc, 1e-30)

    # Side-wall and bottom-plate thermal fields are explicit 2D metal meshes.
    wall_rho_cp = inp.box_density_kgm3 * inp.box_cp_jkgk
    alpha_wall = inp.box_k / wall_rho_cp
    u_ext = external_to_wall_midplane_u(hout, side_t, inp.box_k)

    joint_t = min(side_t, bottom_t)
    gx = inp.box_k * joint_t * dy / max(0.5 * (dz + dx), 1e-30)
    gy = inp.box_k * joint_t * dx / max(0.5 * (dz + dy), 1e-30)

    c_side_x = wall_rho_cp * side_t * dz * dy
    c_side_y = wall_rho_cp * side_t * dz * dx
    c_bottom = wall_rho_cp * bottom_t * dx * dy

    h_gap_max = horizontal_air_gap_natural_h(
        inp.bottom_gap_mm / 1000.0,
        thi,
        tlo,
        inp.gas_k_wmk,
    )
    if inp.emissivity > 0.0 and inp.floor_emissivity > 0.0:
        eps_gap = 1.0 / (
            1.0 / inp.emissivity + 1.0 / inp.floor_emissivity - 1.0
        )
    else:
        eps_gap = 0.0
    h_gap_rad_max = h_radiation(
        inp.bottom_view_factor * eps_gap,
        thi,
        thi,
    )

    lam_xwall = (
        alpha_wall * (1.0 / dz**2 + 1.0 / dy**2)
        + (u_ext + u_side_x) / (wall_rho_cp * side_t)
        + gx / max(c_side_x, 1e-30)
    )
    lam_ywall = (
        alpha_wall * (1.0 / dz**2 + 1.0 / dx**2)
        + (u_ext + u_side_y) / (wall_rho_cp * side_t)
        + gy / max(c_side_y, 1e-30)
    )
    lam_bottom = (
        alpha_wall * (1.0 / dy**2 + 1.0 / dx**2)
        + (u_bottom + h_gap_max + h_gap_rad_max) / (wall_rho_cp * bottom_t)
        + gx / max(c_bottom, 1e-30)
        + gy / max(c_bottom, 1e-30)
    )
    dt_wall = 0.20 / max(lam_xwall, lam_ywall, lam_bottom, 1e-30)

    dt_o2 = oxygen_diffusion_stable_dt(inp, thi)

    # Never enlarge a calculated stability limit to a fixed minimum step.
    dt = min(inp.max_dt_s, dt_diff, dt_bc, dt_wall, dt_o2)
    if not math.isfinite(dt) or dt <= 0.0:
        raise ValueError("Calculated stable time step must be finite and > 0.")
    return dt

def validate_inputs(inp: SimulationInputs):
    positives = [
        ("Lx", inp.lx_mm), ("Ly", inp.ly_mm), ("Lz", inp.lz_mm),
        ("box k", inp.box_k), ("box density", inp.box_density_kgm3),
        ("box cp", inp.box_cp_jkgk),
        ("side thickness", inp.side_thickness_mm),
        ("bottom thickness", inp.bottom_thickness_mm),
        ("bottom gap", inp.bottom_gap_mm),
        ("bed density", inp.bed_density_kgm3),
        ("true density", inp.true_density_kgm3), ("cp", inp.cp_jkgk),
        ("k_eff", inp.k_eff_wmk), ("h_conv", inp.h_conv_wm2k),
        ("max_dt", inp.max_dt_s), ("record interval", inp.record_interval_s),
        ("3D snapshot interval", inp.snapshot_interval_s),
    ]
    finite_values = positives + [
        ("top thickness", inp.top_thickness_mm),
        ("solid SiC k", inp.k_solid_wmk), ("pore gas k", inp.gas_k_wmk),
        ("D10", inp.d10_um), ("D50", inp.d50_um), ("D90", inp.d90_um),
        ("solid-k temperature exponent", inp.solid_k_temp_exponent),
        ("contact blend", inp.contact_blend),
        ("calibration k scale", inp.calibration_k_scale),
        ("calibrated h", inp.calibration_h_wm2k),
        ("emissivity", inp.emissivity),
        ("floor emissivity", inp.floor_emissivity),
        ("bottom view factor", inp.bottom_view_factor),
        ("initial temperature", inp.initial_temp_c),
        ("ambient O2", inp.o2_ambient_percent),
        ("initial pore O2", inp.o2_initial_percent),
        ("O2 diffusivity", inp.o2_diff_air_25_m2s),
        ("O2 temperature exponent", inp.o2_diff_temp_exponent),
        ("O2 tortuosity", inp.o2_manual_tortuosity),
        ("nx", inp.nx), ("ny", inp.ny), ("nz", inp.nz),
    ]
    for name, val in finite_values:
        try:
            finite = math.isfinite(val)
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise ValueError(f"{name} must be a finite number.")

    for name, val in positives:
        if val <= 0:
            raise ValueError(f"{name} must be > 0.")

    if inp.initial_temp_c <= -273.15:
        raise ValueError("Initial temperature must be above absolute zero (-273.15°C).")

    if inp.top_thickness_mm < 0.0:
        raise ValueError("Top/lid thickness must be >= 0.")

    if any(isinstance(n, (bool, np.bool_)) or not isinstance(n, (int, np.integer))
           for n in (inp.nx, inp.ny, inp.nz)):
        raise ValueError("Grid dimensions must be integers.")

    if not (0.0 <= inp.emissivity <= 1.0):
        raise ValueError("Emissivity must be between 0 and 1.")

    if not (0.0 <= inp.floor_emissivity <= 1.0):
        raise ValueError("Oven-floor emissivity must be between 0 and 1.")

    if not (0.0 <= inp.bottom_view_factor <= 1.0):
        raise ValueError("Bottom radiation view factor must be between 0 and 1.")

    if inp.k_mode not in ("Manual", "PSD estimate", "Calibrated"):
        raise ValueError("Unknown k_eff mode.")

    if inp.k_mode != "Manual":
        if not (0 < inp.d10_um <= inp.d50_um <= inp.d90_um):
            raise ValueError("PSD must satisfy 0 < D10 <= D50 <= D90.")
        if not (0.0 <= inp.contact_blend <= 1.0):
            raise ValueError("Contact blend must be between 0 and 1.")
        if inp.solid_k_temp_exponent < 0:
            raise ValueError("Solid-k temperature exponent must be >= 0.")

    if inp.k_mode == "Calibrated":
        if inp.calibration_k_scale <= 0 or inp.calibration_h_wm2k <= 0:
            raise ValueError("Calibrated k scale and h must both be > 0.")

    if inp.o2_enabled:
        if not (0.0 < inp.o2_ambient_percent <= 100.0):
            raise ValueError("Ambient O2 must be > 0 and <= 100 vol%.")
        if not (0.0 <= inp.o2_initial_percent <= 100.0):
            raise ValueError("Initial pore O2 must be between 0 and 100 vol%.")
        if inp.o2_diff_air_25_m2s <= 0:
            raise ValueError("O2 diffusion coefficient at 25°C must be > 0.")
        if inp.o2_diff_temp_exponent < 0:
            raise ValueError("O2 diffusion temperature exponent must be >= 0.")
        if inp.o2_tortuosity_mode not in ("Bruggeman", "Manual"):
            raise ValueError("O2 tortuosity mode must be Bruggeman or Manual.")
        if inp.o2_tortuosity_mode == "Manual" and inp.o2_manual_tortuosity < 1.0:
            raise ValueError("Manual O2 tortuosity must be >= 1.")
        if not (0 < inp.d10_um <= inp.d50_um <= inp.d90_um):
            raise ValueError(
                "O2 PSD model requires 0 < D10 <= D50 <= D90."
            )
        if inp.o2_bottom_access:
            raise ValueError(
                "Bottom O2 access must be off because the connected metal "
                "bottom plate is treated as sealed."
            )

    if min(inp.nx, inp.ny, inp.nz) < 3:
        raise ValueError("nx, ny, nz must each be at least 3.")

    if max(inp.nx, inp.ny, inp.nz) > 100:
        raise ValueError("For desktop use, keep each grid dimension <= 100.")

    if inp.bed_density_kgm3 >= inp.true_density_kgm3:
        raise ValueError("Bed density must be lower than SiC true density.")

    if len(inp.oven_profile_min_c) < 2:
        raise ValueError("Oven profile needs at least two time-temperature points.")

    for point in inp.oven_profile_min_c:
        try:
            finite = len(point) == 2 and all(math.isfinite(value) for value in point)
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise ValueError("Oven profile points must contain finite time and temperature values.")
        if point[1] <= -273.15:
            raise ValueError("Oven profile temperatures must be above absolute zero (-273.15°C).")

    times = [p[0] for p in inp.oven_profile_min_c]
    if times[0] != 0:
        raise ValueError("The first oven profile time must be 0 min.")
    if any(b <= a for a, b in zip(times[:-1], times[1:])):
        raise ValueError("Oven profile times must be strictly increasing.")


def simulate(
    inp: SimulationInputs,
    progress_cb=None,
    stop_event: threading.Event = None,
    store_snapshots: bool = True,
) -> SimulationResult:
    validate_inputs(inp)

    profile = np.asarray(inp.oven_profile_min_c, dtype=np.float64)
    total_s = float(profile[-1, 0] * 60.0)
    oven_max_c = float(profile[:, 1].max())

    dt = estimate_stable_dt(inp, oven_max_c)
    nsub = max(1, int(math.ceil(inp.record_interval_s / dt)))
    dt = min(dt, inp.record_interval_s / nsub)

    lx = inp.lx_mm / 1000.0
    ly = inp.ly_mm / 1000.0
    lz = inp.lz_mm / 1000.0
    dx, dy, dz = lx / inp.nx, ly / inp.ny, lz / inp.nz

    rho_cp = inp.bed_density_kgm3 * inp.cp_jkgk
    pore_volume_fraction = 1.0 - inp.bed_density_kgm3 / inp.true_density_kgm3

    side_t = inp.side_thickness_mm / 1000.0
    bottom_t = inp.bottom_thickness_mm / 1000.0
    top_t = 0.0 if inp.top_open else inp.top_thickness_mm / 1000.0
    wall_rho_cp = inp.box_density_kgm3 * inp.box_cp_jkgk
    alpha_wall = inp.box_k / wall_rho_cp

    # Thermal field
    T = np.full((inp.nz, inp.ny, inp.nx), inp.initial_temp_c, dtype=np.float64)
    dT = np.empty_like(T)

    # Connected build-box shell fields. Each side wall is a 2D mid-thickness
    # mesh aligned with the adjacent powder cells. The bottom plate is a 2D
    # field connected conservatively to all four side-wall bottom edges.
    wall_xm = np.full((inp.nz, inp.ny), inp.initial_temp_c, dtype=np.float64)
    wall_xp = wall_xm.copy()
    wall_ym = np.full((inp.nz, inp.nx), inp.initial_temp_c, dtype=np.float64)
    wall_yp = wall_ym.copy()
    wall_bottom = np.full((inp.ny, inp.nx), inp.initial_temp_c, dtype=np.float64)
    d_wall_xm = np.empty_like(wall_xm)
    d_wall_xp = np.empty_like(wall_xp)
    d_wall_ym = np.empty_like(wall_ym)
    d_wall_yp = np.empty_like(wall_yp)
    d_wall_bottom = np.empty_like(wall_bottom)

    # O2 field uses absolute vol fraction, e.g. 20.95% -> 0.2095.
    if inp.o2_enabled:
        O2 = np.full(
            T.shape, inp.o2_initial_percent / 100.0, dtype=np.float64
        )
        dO2 = np.empty_like(O2)
        o2_ambient = inp.o2_ambient_percent / 100.0
    else:
        O2 = None
        dO2 = None
        o2_ambient = 0.0

    times, ovens, centers, surfaces, avgs, mins, maxs = [], [], [], [], [], [], []
    side_wall_avgs, bottom_plate_avgs = [], []
    bottom_gap_hs, bottom_gap_air_ws, bottom_gap_rad_ws = [], [], []
    kcenters = []

    o2_centers = []
    o2_tops = []
    o2_avgs = []
    o2_mins = []
    o2_maxs = []
    o2_deff_centers = []
    o2_dose_hist = []
    o2_dose_air_s = 0.0

    next_record = 0.0

    MAX_3D_FRAMES = 300
    requested_snapshot_s = inp.snapshot_interval_s

    # Memory-aware snapshot cap. This changes visualization cadence only,
    # never the numerical solver grid or timestep.
    n_cells = inp.nx * inp.ny * inp.nz
    n_fields = 2 if inp.o2_enabled else 1
    max_snapshot_bytes = 384 * 1024 * 1024
    frames_by_memory = max(
        2,
        int(max_snapshot_bytes / max(n_cells * 4 * n_fields, 1))
    )
    max_frames = min(MAX_3D_FRAMES, frames_by_memory)

    if total_s > 0:
        min_interval_for_cap = total_s / max(max_frames - 1, 1)
        snapshot_interval_used_s = max(
            requested_snapshot_s, min_interval_for_cap
        )
    else:
        snapshot_interval_used_s = requested_snapshot_s

    snapshot_times = []
    snapshots = []
    o2_snapshots = []
    next_snapshot = 0.0

    step = 0
    nsteps = int(math.ceil(total_s / dt))

    thresholds = [150.0, 165.0, 180.0, 190.0, 195.0]
    exposure_s = {th: 0.0 for th in thresholds}
    prev_center = center_mean(T)

    manual_mode = inp.k_mode == "Manual"
    if manual_mode:
        k_manual = float(inp.k_eff_wmk)
        alpha_manual = k_manual / rho_cp

    # Diffusion-only shortcut: if the whole pore space starts at exactly the
    # ambient concentration, no concentration gradient can form in this model.
    o2_uniform_trivial = (
        inp.o2_enabled
        and abs(inp.o2_initial_percent - inp.o2_ambient_percent) < 1e-12
    )

    while step <= nsteps:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Simulation stopped by user.")

        t = min(step * dt, total_s)
        toven = oven_temp_at(t, profile)

        # Local temperature-dependent thermal conductivity.
        if manual_mode:
            K = None
            k_center_now = k_manual
        else:
            K = effective_k(inp, T)
            k_center_now = center_mean(K)

        # Local temperature-dependent O2 diffusivity.
        if inp.o2_enabled:
            D_O2 = oxygen_effective_diffusivity(inp, T)
            d_o2_center_now = center_mean(D_O2)
        else:
            D_O2 = None
            d_o2_center_now = 0.0

        # ------------------------------------------------------------------
        # Record histories
        # ------------------------------------------------------------------
        if t + 1e-9 >= next_record or step == 0 or t >= total_s:
            times.append(t / 60.0)
            ovens.append(toven)
            centers.append(center_mean(T))
            surfaces.append(surface_mean(T))
            avgs.append(float(T.mean()))
            mins.append(float(T.min()))
            maxs.append(float(T.max()))
            kcenters.append(float(k_center_now))
            side_area_total = 2.0 * lz * (lx + ly)
            side_wall_avgs.append(float(
                (
                    (wall_xm.mean() + wall_xp.mean()) * lz * ly
                    + (wall_ym.mean() + wall_yp.mean()) * lz * lx
                ) / max(side_area_total, 1e-30)
            ))
            bottom_plate_avgs.append(float(wall_bottom.mean()))
            q_gap_air, q_gap_rad, h_gap = bottom_cavity_heat_fluxes(
                inp, wall_bottom, toven
            )
            bottom_area = lx * ly
            bottom_gap_hs.append(float(np.mean(h_gap)))
            bottom_gap_air_ws.append(float(np.mean(q_gap_air) * bottom_area))
            bottom_gap_rad_ws.append(float(np.mean(q_gap_rad) * bottom_area))

            if inp.o2_enabled:
                o2_centers.append(center_mean(O2) * 100.0)
                o2_tops.append(float(O2[-1, :, :].mean()) * 100.0)
                o2_avgs.append(float(O2.mean()) * 100.0)
                o2_mins.append(float(O2.min()) * 100.0)
                o2_maxs.append(float(O2.max()) * 100.0)
                o2_deff_centers.append(float(d_o2_center_now))
                o2_dose_hist.append(o2_dose_air_s / 3600.0)
            else:
                o2_centers.append(np.nan)
                o2_tops.append(np.nan)
                o2_avgs.append(np.nan)
                o2_mins.append(np.nan)
                o2_maxs.append(np.nan)
                o2_deff_centers.append(np.nan)
                o2_dose_hist.append(np.nan)

            next_record += inp.record_interval_s

        if store_snapshots and (
            t + 1e-9 >= next_snapshot or step == 0 or t >= total_s
        ):
            snapshot_times.append(t / 60.0)
            snapshots.append(T.astype(np.float32, copy=True))

            if inp.o2_enabled:
                o2_snapshots.append(
                    (O2 * 100.0).astype(np.float32, copy=True)
                )

            next_snapshot += snapshot_interval_used_s

        if t >= total_s:
            break

        # ==================================================================
        # HEAT TRANSFER
        # ==================================================================
        dT.fill(0.0)

        if manual_mode:
            dT[:, :, 1:-1] += alpha_manual * (
                T[:, :, 2:] - 2.0 * T[:, :, 1:-1] + T[:, :, :-2]
            ) / dx**2
            dT[:, :, 0] += alpha_manual * (
                T[:, :, 1] - T[:, :, 0]
            ) / dx**2
            dT[:, :, -1] += alpha_manual * (
                T[:, :, -2] - T[:, :, -1]
            ) / dx**2

            dT[:, 1:-1, :] += alpha_manual * (
                T[:, 2:, :] - 2.0 * T[:, 1:-1, :] + T[:, :-2, :]
            ) / dy**2
            dT[:, 0, :] += alpha_manual * (
                T[:, 1, :] - T[:, 0, :]
            ) / dy**2
            dT[:, -1, :] += alpha_manual * (
                T[:, -2, :] - T[:, -1, :]
            ) / dy**2

            dT[1:-1, :, :] += alpha_manual * (
                T[2:, :, :] - 2.0 * T[1:-1, :, :] + T[:-2, :, :]
            ) / dz**2
            dT[0, :, :] += alpha_manual * (
                T[1, :, :] - T[0, :, :]
            ) / dz**2
            dT[-1, :, :] += alpha_manual * (
                T[-2, :, :] - T[-1, :, :]
            ) / dz**2
        else:
            # Conservative variable-k finite-volume fluxes.
            kf = harmonic_mean(K[:, :, :-1], K[:, :, 1:])
            q = kf * (
                T[:, :, 1:] - T[:, :, :-1]
            ) / (rho_cp * dx**2)
            dT[:, :, :-1] += q
            dT[:, :, 1:] -= q

            kf = harmonic_mean(K[:, :-1, :], K[:, 1:, :])
            q = kf * (
                T[:, 1:, :] - T[:, :-1, :]
            ) / (rho_cp * dy**2)
            dT[:, :-1, :] += q
            dT[:, 1:, :] -= q

            kf = harmonic_mean(K[:-1, :, :], K[1:, :, :])
            q = kf * (
                T[1:, :, :] - T[:-1, :, :]
            ) / (rho_cp * dz**2)
            dT[:-1, :, :] += q
            dT[1:, :, :] -= q

        hconv = effective_h_conv(inp)

        # --------------------------------------------------------------
        # CONNECTED SIDE-WALL / BOTTOM-PLATE THERMAL MODEL
        # --------------------------------------------------------------
        # In-plane metal conduction on the four side walls and bottom plate.
        for rate, field, ds0, ds1 in (
            (d_wall_xm, wall_xm, dz, dy),
            (d_wall_xp, wall_xp, dz, dy),
            (d_wall_ym, wall_ym, dz, dx),
            (d_wall_yp, wall_yp, dz, dx),
            (d_wall_bottom, wall_bottom, dy, dx),
        ):
            rate.fill(0.0)
            add_neumann_laplacian_2d(rate, field, ds0, ds1, alpha_wall)

        # Oven air heats the outer side-wall surfaces. Each side wall then
        # exchanges heat with its adjacent powder boundary. The wall fields
        # include metal thermal mass, so they do not jump instantly to oven T.
        side_specs = (
            ("xm", wall_xm, d_wall_xm, T[:, :, 0], K[:, :, 0] if not manual_mode else k_manual, dx),
            ("xp", wall_xp, d_wall_xp, T[:, :, -1], K[:, :, -1] if not manual_mode else k_manual, dx),
            ("ym", wall_ym, d_wall_ym, T[:, 0, :], K[:, 0, :] if not manual_mode else k_manual, dy),
            ("yp", wall_yp, d_wall_yp, T[:, -1, :], K[:, -1, :] if not manual_mode else k_manual, dy),
        )

        for face_name, wall, d_wall, powder_face, k_face, cell_width in side_specs:
            hout_wall = hconv + h_radiation(
                inp.emissivity, float(wall.mean()), toven
            )
            u_ext = external_to_wall_midplane_u(hout_wall, side_t, inp.box_k)
            u_in = wall_midplane_to_powder_u(
                side_t, inp.box_k, cell_width / 2.0, k_face
            )

            d_wall += u_ext * (toven - wall) / (wall_rho_cp * side_t)
            flux_to_powder = u_in * (wall - powder_face)
            d_wall -= flux_to_powder / (wall_rho_cp * side_t)

            if face_name == "xm":
                dT[:, :, 0] += flux_to_powder / (rho_cp * dx)
            elif face_name == "xp":
                dT[:, :, -1] += flux_to_powder / (rho_cp * dx)
            elif face_name == "ym":
                dT[:, 0, :] += flux_to_powder / (rho_cp * dy)
            else:
                dT[:, -1, :] += flux_to_powder / (rho_cp * dy)

        # The bottom plate exchanges heat with the lower powder face and is
        # heated through its connected edges by the four side walls.
        k_bottom = k_manual if manual_mode else K[0, :, :]
        u_bottom = wall_midplane_to_powder_u(
            bottom_t, inp.box_k, dz / 2.0, k_bottom
        )
        flux_bottom_to_powder = u_bottom * (wall_bottom - T[0, :, :])
        dT[0, :, :] += flux_bottom_to_powder / (rho_cp * dz)
        d_wall_bottom -= flux_bottom_to_powder / (wall_rho_cp * bottom_t)

        # 40-mm under-plate cavity by default: floor-to-plate radiation plus
        # horizontal air-layer conduction/natural convection. The floor
        # temperature follows the oven profile; no forced-convection term.
        q_gap_air, q_gap_rad, _h_gap = bottom_cavity_heat_fluxes(
            inp, wall_bottom, toven
        )
        d_wall_bottom += (
            q_gap_air + q_gap_rad
        ) / (wall_rho_cp * bottom_t)

        # Conservative metal-junction conductances at the four shared edges.
        # Heat added to a side-wall edge is subtracted from the matching
        # bottom-plate edge, and vice versa.
        joint_t = min(side_t, bottom_t)
        gx = inp.box_k * joint_t * dy / max(0.5 * (dz + dx), 1e-30)
        gy = inp.box_k * joint_t * dx / max(0.5 * (dz + dy), 1e-30)
        c_side_x = wall_rho_cp * side_t * dz * dy
        c_side_y = wall_rho_cp * side_t * dz * dx
        c_bottom = wall_rho_cp * bottom_t * dx * dy

        q = gx * (wall_bottom[:, 0] - wall_xm[0, :])
        d_wall_xm[0, :] += q / c_side_x
        d_wall_bottom[:, 0] -= q / c_bottom

        q = gx * (wall_bottom[:, -1] - wall_xp[0, :])
        d_wall_xp[0, :] += q / c_side_x
        d_wall_bottom[:, -1] -= q / c_bottom

        q = gy * (wall_bottom[0, :] - wall_ym[0, :])
        d_wall_ym[0, :] += q / c_side_y
        d_wall_bottom[0, :] -= q / c_bottom

        q = gy * (wall_bottom[-1, :] - wall_yp[0, :])
        d_wall_yp[0, :] += q / c_side_y
        d_wall_bottom[-1, :] -= q / c_bottom

        # top
        ts = float(T[-1, :, :].mean())
        hout = hconv + h_radiation(inp.emissivity, ts, toven)
        kb = k_manual if manual_mode else K[-1, :, :]
        u = overall_u_to_cell(
            hout, top_t, inp.box_k, dz / 2.0, kb
        )
        dT[-1, :, :] += (
            u * (toven - T[-1, :, :]) / (rho_cp * dz)
        )

        # ==================================================================
        # O2 DIFFUSION ONLY
        # ==================================================================
        if inp.o2_enabled and not o2_uniform_trivial:
            dO2.fill(0.0)

            # Internal conservative diffusion fluxes with harmonic face D.
            df = harmonic_mean(D_O2[:, :, :-1], D_O2[:, :, 1:])
            q = df * (
                O2[:, :, 1:] - O2[:, :, :-1]
            ) / dx**2
            dO2[:, :, :-1] += q
            dO2[:, :, 1:] -= q

            df = harmonic_mean(D_O2[:, :-1, :], D_O2[:, 1:, :])
            q = df * (
                O2[:, 1:, :] - O2[:, :-1, :]
            ) / dy**2
            dO2[:, :-1, :] += q
            dO2[:, 1:, :] -= q

            df = harmonic_mean(D_O2[:-1, :, :], D_O2[1:, :, :])
            q = df * (
                O2[1:, :, :] - O2[:-1, :, :]
            ) / dz**2
            dO2[:-1, :, :] += q
            dO2[1:, :, :] -= q

            # Exposed faces are fixed to ambient O2 at the geometric boundary.
            # Half-cell distance => factor 2*D/delta².
            if inp.o2_side_access:
                dO2[:, :, 0] += (
                    2.0 * D_O2[:, :, 0]
                    * (o2_ambient - O2[:, :, 0]) / dx**2
                )
                dO2[:, :, -1] += (
                    2.0 * D_O2[:, :, -1]
                    * (o2_ambient - O2[:, :, -1]) / dx**2
                )
                dO2[:, 0, :] += (
                    2.0 * D_O2[:, 0, :]
                    * (o2_ambient - O2[:, 0, :]) / dy**2
                )
                dO2[:, -1, :] += (
                    2.0 * D_O2[:, -1, :]
                    * (o2_ambient - O2[:, -1, :]) / dy**2
                )

            if inp.o2_bottom_access:
                dO2[0, :, :] += (
                    2.0 * D_O2[0, :, :]
                    * (o2_ambient - O2[0, :, :]) / dz**2
                )

            if inp.o2_top_access:
                dO2[-1, :, :] += (
                    2.0 * D_O2[-1, :, :]
                    * (o2_ambient - O2[-1, :, :]) / dz**2
                )

        actual_dt = min(dt, total_s - t)

        # Center O2 dose, normalized to ambient concentration:
        # 1 hour at ambient O2 = 1 "air-equivalent hour".
        if inp.o2_enabled and o2_ambient > 0:
            center_before = center_mean(O2)
        else:
            center_before = 0.0

        T += actual_dt * dT
        wall_xm += actual_dt * d_wall_xm
        wall_xp += actual_dt * d_wall_xp
        wall_ym += actual_dt * d_wall_ym
        wall_yp += actual_dt * d_wall_yp
        wall_bottom += actual_dt * d_wall_bottom

        if inp.o2_enabled and not o2_uniform_trivial:
            # dO2 is divergence of bulk-area flux. Pore-gas concentration c
            # stores epsilon*c per bulk volume, hence epsilon*dc/dt = dO2.
            O2 += (actual_dt / pore_volume_fraction) * dO2
            # Stability should prevent overshoot; clipping is only a final
            # physical guard against floating-point excursions.
            O2[:] = np.clip(O2, 0.0, 1.0)

        if inp.o2_enabled and o2_ambient > 0:
            center_after = center_mean(O2)
            o2_dose_air_s += (
                0.5 * (center_before + center_after)
                / o2_ambient
                * actual_dt
            )

        new_center = center_mean(T)
        mid_center = 0.5 * (prev_center + new_center)
        for th in thresholds:
            if mid_center >= th:
                exposure_s[th] += actual_dt
        prev_center = new_center

        step += 1
        if progress_cb is not None and (
            step % 20 == 0 or step == nsteps
        ):
            progress_cb(
                min(t / total_s, 1.0) if total_s > 0 else 1.0
            )

    phi_s = inp.bed_density_kgm3 / inp.true_density_kgm3
    porosity = 1.0 - phi_s

    snap_arr = (
        np.asarray(snapshots, dtype=np.float32)
        if snapshots
        else np.empty(
            (0, inp.nz, inp.ny, inp.nx), dtype=np.float32
        )
    )

    o2_snap_arr = (
        np.asarray(o2_snapshots, dtype=np.float32)
        if o2_snapshots
        else np.empty(
            (0, inp.nz, inp.ny, inp.nx), dtype=np.float32
        )
    )

    return SimulationResult(
        time_min=np.asarray(times),
        oven_c=np.asarray(ovens),
        center_c=np.asarray(centers),
        surface_c=np.asarray(surfaces),
        avg_c=np.asarray(avgs),
        min_c=np.asarray(mins),
        max_c=np.asarray(maxs),
        side_wall_avg_c=np.asarray(side_wall_avgs, dtype=np.float64),
        bottom_plate_avg_c=np.asarray(bottom_plate_avgs, dtype=np.float64),
        bottom_gap_h_wm2k=np.asarray(bottom_gap_hs, dtype=np.float64),
        bottom_gap_air_w=np.asarray(bottom_gap_air_ws, dtype=np.float64),
        bottom_gap_radiation_w=np.asarray(bottom_gap_rad_ws, dtype=np.float64),
        bottom_gap_mm=float(inp.bottom_gap_mm),
        floor_emissivity=float(inp.floor_emissivity),
        bottom_view_factor=float(inp.bottom_view_factor),
        dt_used_s=dt,
        porosity=porosity,
        solid_fraction=phi_s,
        k_eff=float(kcenters[0]) if kcenters else float(inp.k_eff_wmk),
        k_mode=inp.k_mode,
        k_center_wmk=np.asarray(kcenters, dtype=np.float64),
        grid_shape=(inp.nx, inp.ny, inp.nz),
        threshold_hours={
            th: sec / 3600.0 for th, sec in exposure_s.items()
        },
        snapshot_time_min=np.asarray(
            snapshot_times, dtype=np.float64
        ),
        snapshots_c=snap_arr,
        snapshot_interval_used_s=float(
            snapshot_interval_used_s
        ),
        geometry_mm=(inp.lx_mm, inp.ly_mm, inp.lz_mm),

        o2_enabled=bool(inp.o2_enabled),
        o2_center_percent=np.asarray(o2_centers, dtype=np.float64),
        o2_top_percent=np.asarray(o2_tops, dtype=np.float64),
        o2_avg_percent=np.asarray(o2_avgs, dtype=np.float64),
        o2_min_percent=np.asarray(o2_mins, dtype=np.float64),
        o2_max_percent=np.asarray(o2_maxs, dtype=np.float64),
        o2_deff_center_m2s=np.asarray(
            o2_deff_centers, dtype=np.float64
        ),
        o2_center_dose_air_h=np.asarray(
            o2_dose_hist, dtype=np.float64
        ),
        o2_snapshots_percent=o2_snap_arr,
    )


def fit_thermal_calibration(
    inp: SimulationInputs,
    measured_time_min,
    measured_center_c,
    status_cb=None,
    stop_event: threading.Event = None,
):
    """
    Fit two physically important uncertain parameters:
      1) multiplicative scale on PSD-based k_eff(T)
      2) oven convection coefficient h

    Uses the SAME 3D solver/grid as the final simulation. No grid reduction or
    approximate thermal solver is used for calibration.

    scipy is imported lazily. If unavailable, the existing dependency bootstrap
    attempts to install it.
    """
    t_meas = np.asarray(measured_time_min, dtype=np.float64)
    y_meas = np.asarray(measured_center_c, dtype=np.float64)

    mask = np.isfinite(t_meas) & np.isfinite(y_meas)
    t_meas = t_meas[mask]
    y_meas = y_meas[mask]

    if len(t_meas) < 5:
        raise ValueError("At least 5 valid thermocouple points are required.")

    order = np.argsort(t_meas)
    t_meas = t_meas[order]
    y_meas = y_meas[order]

    tmax = inp.oven_profile_min_c[-1][0]
    keep = (t_meas >= 0.0) & (t_meas <= tmax)
    t_meas = t_meas[keep]
    y_meas = y_meas[keep]
    if len(t_meas) < 5:
        raise ValueError("At least 5 thermocouple points must lie inside the oven profile time range.")

    try:
        from scipy.optimize import least_squares
    except Exception:
        ensure_package("scipy", "scipy")
        from scipy.optimize import least_squares

    # Residual interpolation does not need dense 3D snapshots.
    cal_inp = replace(
        inp,
        k_mode="Calibrated",
        o2_enabled=False,
        record_interval_s=min(inp.record_interval_s, 60.0),
    )

    eval_count = {"n": 0}
    best = {"rmse": float("inf"), "scale": None, "h": None}

    def residual(log_params):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Calibration stopped by user.")

        k_scale = float(math.exp(log_params[0]))
        h_fit = float(math.exp(log_params[1]))

        test_inp = replace(
            cal_inp,
            calibration_k_scale=k_scale,
            calibration_h_wm2k=h_fit,
        )
        res = simulate(
            test_inp,
            progress_cb=None,
            stop_event=stop_event,
            store_snapshots=False,
        )
        pred = np.interp(t_meas, res.time_min, res.center_c)
        r = pred - y_meas
        rmse = float(np.sqrt(np.mean(r * r)))

        eval_count["n"] += 1
        if rmse < best["rmse"]:
            best.update(rmse=rmse, scale=k_scale, h=h_fit)

        if status_cb is not None:
            status_cb(eval_count["n"], rmse, k_scale, h_fit)

        return r

    x0 = np.log([
        max(inp.calibration_k_scale, 1.0e-3),
        max(inp.calibration_h_wm2k, 1.0e-3),
    ])

    result = least_squares(
        residual,
        x0,
        bounds=(
            np.log([0.10, 1.0]),
            np.log([10.0, 100.0]),
        ),
        method="trf",
        xtol=1e-3,
        ftol=1e-3,
        gtol=1e-3,
        max_nfev=30,
    )

    k_scale = float(math.exp(result.x[0]))
    h_fit = float(math.exp(result.x[1]))

    final_inp = replace(
        cal_inp,
        calibration_k_scale=k_scale,
        calibration_h_wm2k=h_fit,
    )
    final_res = simulate(
        final_inp,
        progress_cb=None,
        stop_event=stop_event,
        store_snapshots=False,
    )
    final_pred = np.interp(t_meas, final_res.time_min, final_res.center_c)
    final_rmse = float(np.sqrt(np.mean((final_pred - y_meas) ** 2)))

    return {
        "k_scale": k_scale,
        "h_conv": h_fit,
        "rmse_c": final_rmse,
        "nfev": int(result.nfev),
        "success": bool(result.success),
        "message": str(result.message),
    }



# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------
class ThermalGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.language_var = tk.StringVar(master=self, value="ko")
        self._i18n = OvenTranslator(self.language_var.get)
        self._tr = self._i18n
        self._widgets = LocalizedWidgets(self._i18n)
        self._dialogs = LocalizedDialogs(self._i18n)
        self.title(self._tr("Oven thermal diffusion simulator"))
        self.geometry("1680x940")
        self.minsize(1240, 760)

        self.result = None
        self.worker = None
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()

        # Optional center thermocouple calibration data
        self.tc_time_min = None
        self.tc_center_c = None
        self.tc_path = None

        # 3D temperature-field animation state
        self.field_playing = False
        self.field_after_id = None
        self.field_colorbar = None
        self.field_norm = None
        self.field_cmap = None
        self.field_display_stride = 1

        # 3D O2-field animation state
        self.o2_field_playing = False
        self.o2_field_after_id = None
        self.o2_field_colorbar = None
        self.o2_field_norm = None
        self.o2_field_cmap = None
        self.o2_field_display_stride = 1

        self.style = ttk.Style(self)
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")

        # Persist last-run GUI settings across program launches.
        # Using the user's home folder keeps the settings even when the .py file
        # is copied/renamed or launched from a different PyCharm project folder.
        self.settings_path = Path.home() / ".sic_buildbox_thermal_gui_settings.json"

        self._build_gui()
        self._load_defaults()
        self._load_saved_settings(silent=True)
        self.language_var.trace_add("write", lambda *_: self._apply_language())
        self._apply_language()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)

    def _build_gui(self):
        root = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)
        self.main_pane = root

        # Give the parameter panel enough room for long labels and editable values.
        left = ttk.Frame(root, padding=(8, 8, 4, 8), width=600)
        right = ttk.Frame(root, padding=(4, 8, 8, 8))
        root.add(left, weight=0)
        root.add(right, weight=1)

        # Fixed header: remains visible while the parameter form is scrolled.
        left_header = ttk.Frame(left)
        left_header.pack(fill=tk.X, pady=(0, 6))

        self.language_combo = self._widgets.Combobox(
            left_header, values=("ko", "en"), state="readonly", width=9,
            model_variable=self.language_var,
        )
        self.language_combo.pack(side=tk.RIGHT, padx=(8, 2))
        self._widgets.Label(left_header, text="Language").pack(side=tk.RIGHT, padx=(8, 2))

        self._widgets.Label(
            left_header,
            text="Simulation inputs",
            font=("TkDefaultFont", 10, "bold")
        ).pack(side=tk.LEFT, padx=(2, 8))

        self._widgets.Button(
            left_header,
            text="Input guide (Korean)",
            command=self._show_input_guide
        ).pack(side=tk.RIGHT, padx=2)

        # Scrollable left panel
        canvas = tk.Canvas(
            left,
            width=585,
            highlightthickness=0,
            borderwidth=0
        )
        self.left_canvas = canvas

        scrollbar = ttk.Scrollbar(
            left, orient="vertical", command=canvas.yview
        )

        self.form = ttk.Frame(canvas)
        self.form.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )

        self.form_window_id = canvas.create_window(
            (0, 0), window=self.form, anchor="nw"
        )

        # Critical UI fix: the embedded form follows the actual canvas width,
        # so entries expand instead of being clipped off-screen.
        def _resize_form_to_canvas(event):
            usable = max(int(event.width), 420)
            canvas.itemconfigure(
                self.form_window_id, width=usable
            )

        canvas.bind("<Configure>", _resize_form_to_canvas)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Scroll with mouse wheel while the pointer is over the input panel.
        def _wheel_on(event):
            canvas.bind_all(
                "<MouseWheel>",
                lambda ev: canvas.yview_scroll(
                    int(-1 * (ev.delta / 120)), "units"
                )
            )

        def _wheel_off(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _wheel_on)
        canvas.bind("<Leave>", _wheel_off)

        # Initial sash position; user can still drag the splitter afterward.
        self.after_idle(
            lambda: self._set_main_sash_position(600)
        )

        # ---- Geometry
        sec = self._widgets.LabelFrame(self.form, text="1) Powder-filled region", padding=8)
        sec.pack(fill=tk.X, pady=4)
        self.lx = self._entry_row(sec, 0, "X [mm]")
        self.ly = self._entry_row(sec, 1, "Y [mm]")
        self.lz = self._entry_row(sec, 2, "Z [mm]")

        # ---- Box
        sec = self._widgets.LabelFrame(self.form, text="2) Build box", padding=8)
        sec.pack(fill=tk.X, pady=4)

        self._widgets.Label(sec, text="Material").grid(row=0, column=0, sticky="w", padx=3, pady=2)
        self.box_mat = self._widgets.Combobox(sec, values=list(BOX_MATERIALS.keys()), state="readonly", width=25)
        self.box_mat.grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        self.box_mat.bind("<<ComboboxSelected>>", self._box_material_changed)

        self.box_k = self._entry_row(sec, 1, "Wall k [W/mK]")
        self.box_density = self._entry_row(sec, 2, "Wall density [kg/m³]")
        self.box_cp = self._entry_row(sec, 3, "Wall cp [J/kgK]")
        self.side_t = self._entry_row(sec, 4, "Side thickness [mm]")
        self.bottom_t = self._entry_row(sec, 5, "Bottom thickness [mm]")
        self.top_t = self._entry_row(sec, 6, "Top/lid thickness [mm]")
        self.bottom_gap = self._entry_row(sec, 7, "Under-plate gap [mm]")
        self.floor_emissivity = self._entry_row(sec, 8, "Oven-floor emissivity")
        self.bottom_view_factor = self._entry_row(sec, 9, "Floor view factor [0–1]")

        self.top_open_var = tk.BooleanVar(value=True)
        self._widgets.Checkbutton(
            sec, text="Top is open to oven air", variable=self.top_open_var
        ).grid(row=10, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        self._widgets.Label(
            sec,
            text=(
                "Bottom: side-wall metal conduction + floor radiation + natural air-layer "
                "convection/conduction. Forced convection is excluded."
            ),
            foreground="#555",
            wraplength=410,
        ).grid(row=11, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        # ---- Powder
        sec = self._widgets.LabelFrame(self.form, text="3) SiC powder bed / k_eff model", padding=8)
        sec.pack(fill=tk.X, pady=4)

        self._widgets.Label(sec, text="SiC preset").grid(row=0, column=0, sticky="w", padx=3, pady=2)
        self.sic_type = self._widgets.Combobox(sec, values=list(SIC_PRESETS.keys()), state="readonly", width=25)
        self.sic_type.grid(row=0, column=1, sticky="ew", padx=3, pady=2)
        self.sic_type.bind("<<ComboboxSelected>>", self._sic_preset_changed)

        self.bed_rho = self._entry_row(sec, 1, "Bed density [g/cc]")
        self.true_rho = self._entry_row(sec, 2, "True density [g/cc]")
        self.cp = self._entry_row(sec, 3, "cp [J/kgK]")
        self.k_solid = self._entry_row(sec, 4, "Solid SiC k @25°C [W/mK]")
        self.gas_k = self._entry_row(sec, 5, "Pore-gas k @25°C [W/mK]")

        self._widgets.Label(sec, text="k_eff mode").grid(row=6, column=0, sticky="w", padx=3, pady=2)
        self.k_mode = self._widgets.Combobox(
            sec,
            values=["Manual", "PSD estimate", "Calibrated"],
            state="readonly",
            width=20
        )
        self.k_mode.grid(row=6, column=1, sticky="ew", padx=3, pady=2)
        self.k_mode.bind("<<ComboboxSelected>>", self._k_mode_changed)

        self.d10 = self._entry_row(sec, 7, "D10 [µm]")
        self.d50 = self._entry_row(sec, 8, "D50 [µm]")
        self.d90 = self._entry_row(sec, 9, "D90 [µm]")
        self.solid_k_exp = self._entry_row(sec, 10, "Solid-k T exponent n")
        self.contact_blend = self._entry_row(sec, 11, "Contact blend [0–1]")
        self.k_eff = self._entry_row(sec, 12, "Manual k_eff [W/mK]")

        btns = ttk.Frame(sec)
        btns.grid(row=13, column=0, columnspan=2, sticky="ew", pady=4)
        self._widgets.Button(btns, text="Preview k_eff(T)", command=self._estimate_keff).pack(side=tk.LEFT, padx=2)
        self._widgets.Button(btns, text="Model info", command=self._show_keff_info).pack(side=tk.LEFT, padx=2)

        self.porosity_label = self._widgets.Label(sec, text="Porosity: -")
        self.porosity_label.grid(row=14, column=0, columnspan=2, sticky="w", padx=3)

        self.psd_label = self._widgets.Label(sec, text="PSD: -", foreground="#555")
        self.psd_label.grid(row=15, column=0, columnspan=2, sticky="w", padx=3, pady=(2, 0))

        self._widgets.Label(
            sec,
            text=(
                "PSD mode uses D10/D50/D90 to estimate breadth and characteristic pore length.\n"
                "Bed density still carries most packing information; calibration is preferred."
            ),
            foreground="#555"
        ).grid(row=16, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        # ---- Boundary
        sec = self._widgets.LabelFrame(self.form, text="4) Oven heat transfer", padding=8)
        sec.pack(fill=tk.X, pady=4)
        self.hconv = self._entry_row(sec, 0, "Convection h [W/m²K]")
        self.emissivity = self._entry_row(sec, 1, "Effective emissivity")
        self.initial_temp = self._entry_row(sec, 2, "Initial bed temp [°C]")

        self._widgets.Label(
            sec,
            text="Tip: h and k_eff are the two parameters most worth calibrating\nwith a thermocouple experiment.",
            foreground="#555"
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=3, pady=4)

        # ---- O2 diffusion only
        sec = self._widgets.LabelFrame(self.form, text="5) O₂ diffusion only", padding=8)
        sec.pack(fill=tk.X, pady=4)

        self.o2_enabled_var = tk.BooleanVar(value=True)
        self._widgets.Checkbutton(
            sec,
            text="Enable O₂ diffusion-only simulation",
            variable=self.o2_enabled_var
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        self.o2_ambient = self._entry_row(sec, 1, "Ambient O₂ [vol%]")
        self.o2_initial = self._entry_row(sec, 2, "Initial pore O₂ [vol%]")
        self.o2_d25 = self._entry_row(sec, 3, "O₂ D_air @25°C [m²/s]")
        self.o2_temp_exp = self._entry_row(sec, 4, "D temperature exponent")

        self._widgets.Label(sec, text="Tortuosity model").grid(
            row=5, column=0, sticky="w", padx=3, pady=2
        )
        self.o2_tau_mode = self._widgets.Combobox(
            sec,
            values=["Bruggeman", "Manual"],
            state="readonly",
            width=20
        )
        self.o2_tau_mode.grid(
            row=5, column=1, sticky="ew", padx=3, pady=2
        )

        self.o2_tau = self._entry_row(sec, 6, "Manual tortuosity τ")

        self.o2_knudsen_var = tk.BooleanVar(value=True)
        self._widgets.Checkbutton(
            sec,
            text="Include PSD-based Knudsen correction",
            variable=self.o2_knudsen_var
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=3, pady=2)

        self._widgets.Label(sec, text="O₂ access boundaries").grid(
            row=8, column=0, sticky="w", padx=3, pady=2
        )
        access = ttk.Frame(sec)
        access.grid(row=8, column=1, sticky="w", padx=3, pady=2)

        self.o2_top_access_var = tk.BooleanVar(value=True)
        self.o2_side_access_var = tk.BooleanVar(value=False)
        self.o2_bottom_access_var = tk.BooleanVar(value=False)

        self._widgets.Checkbutton(
            access, text="Top", variable=self.o2_top_access_var
        ).pack(side=tk.LEFT, padx=1)
        self._widgets.Checkbutton(
            access, text="Sides", variable=self.o2_side_access_var
        ).pack(side=tk.LEFT, padx=1)
        self._widgets.Checkbutton(
            access,
            text="Bottom (sealed metal plate)",
            variable=self.o2_bottom_access_var,
            state=tk.DISABLED,
        ).pack(side=tk.LEFT, padx=1)

        self._widgets.Button(
            sec, text="Preview O₂ diffusivity",
            command=self._preview_o2_diffusivity
        ).grid(row=9, column=0, columnspan=2, sticky="ew", padx=3, pady=4)

        self.o2_info_label = self._widgets.Label(
            sec,
            text=(
                "Diffusion only: no O₂ consumption/reaction. "
                "If Initial O₂ = Ambient O₂, the field stays uniform."
            ),
            foreground="#555",
            wraplength=410
        )
        self.o2_info_label.grid(
            row=10, column=0, columnspan=2, sticky="w", padx=3, pady=3
        )

        # ---- Thermocouple calibration
        sec = self._widgets.LabelFrame(self.form, text="6) Center thermocouple calibration (optional)", padding=8)
        sec.pack(fill=tk.X, pady=4)

        cal_btns = ttk.Frame(sec)
        cal_btns.grid(row=0, column=0, columnspan=2, sticky="ew", pady=2)

        self._widgets.Button(
            cal_btns, text="Load TC CSV", command=self._load_tc_csv
        ).pack(side=tk.LEFT, padx=2)

        self.fit_btn = self._widgets.Button(
            cal_btns, text="Fit k scale + h", command=self._fit_calibration
        )
        self.fit_btn.pack(side=tk.LEFT, padx=2)

        self.cal_scale = self._entry_row(sec, 1, "Calibrated k scale")
        self.cal_h = self._entry_row(sec, 2, "Calibrated h [W/m²K]")

        self.tc_status = self._widgets.Label(
            sec,
            text="No thermocouple CSV loaded.",
            foreground="#555"
        )
        self.tc_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=3, pady=2)

        self.cal_status = self._widgets.Label(
            sec,
            text="CSV: time_min, center_C (header recommended).",
            foreground="#555"
        )
        self.cal_status.grid(row=4, column=0, columnspan=2, sticky="w", padx=3, pady=2)

        self._widgets.Label(
            sec,
            text=(
                "Calibration uses the same 3D grid/solver as the final run; it does not reduce "
                "numerical precision. Fitting can therefore take time."
            ),
            foreground="#555"
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        # ---- Numerical
        sec = self._widgets.LabelFrame(self.form, text="7) Numerical settings", padding=8)
        sec.pack(fill=tk.X, pady=4)
        self.nx = self._entry_row(sec, 0, "Grid nx")
        self.ny = self._entry_row(sec, 1, "Grid ny")
        self.nz = self._entry_row(sec, 2, "Grid nz")
        self.max_dt = self._entry_row(sec, 3, "Max Δt [s]")
        self.record_interval = self._entry_row(sec, 4, "Output interval [s]")
        self.snapshot_interval = self._entry_row(sec, 5, "3D snapshot interval [min]")

        self._widgets.Label(
            sec,
            text="3D snapshot interval changes saved visualization frames only; solver precision is unchanged.",
            foreground="#555"
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=3, pady=3)

        # ---- Oven profile
        sec = self._widgets.LabelFrame(self.form, text="8) Oven profile (absolute elapsed time)", padding=8)
        sec.pack(fill=tk.X, pady=4)

        self.profile_tree = self._widgets.Treeview(
            sec, columns=("time", "temp"), show="headings", height=8
        )
        self.profile_tree.heading("time", text="Time [min]")
        self.profile_tree.heading("temp", text="Oven [°C]")
        self.profile_tree.column("time", width=110, anchor="center")
        self.profile_tree.column("temp", width=110, anchor="center")
        self.profile_tree.grid(row=0, column=0, columnspan=3, sticky="ew")

        self._widgets.Button(sec, text="Add point", command=self._add_profile_point).grid(row=1, column=0, sticky="ew", padx=2, pady=4)
        self._widgets.Button(sec, text="Delete", command=self._delete_profile_point).grid(row=1, column=1, sticky="ew", padx=2, pady=4)
        self._widgets.Button(sec, text="Edit", command=self._edit_profile_point).grid(row=1, column=2, sticky="ew", padx=2, pady=4)

        self._widgets.Label(
            sec,
            text="Hold example: 120 min / 200°C → 840 min / 200°C",
            foreground="#555"
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=3)

        # ---- Run controls
        sec = ttk.Frame(self.form)
        sec.pack(fill=tk.X, pady=8)

        self.run_btn = self._widgets.Button(sec, text="Run simulation", command=self._run)
        self.run_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.stop_btn = self._widgets.Button(sec, text="Stop", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)

        self._widgets.Button(sec, text="Export CSV", command=self._export_csv).pack(side=tk.LEFT, padx=2)

        settings_row = ttk.Frame(self.form)
        settings_row.pack(fill=tk.X, pady=(0, 6))
        self._widgets.Button(
            settings_row, text="Save inputs", command=lambda: self._save_settings(silent=False)
        ).pack(side=tk.LEFT, padx=2)
        self._widgets.Button(
            settings_row, text="Load saved", command=lambda: self._load_saved_settings(silent=False)
        ).pack(side=tk.LEFT, padx=2)
        self._widgets.Button(
            settings_row, text="Reset defaults", command=self._reset_defaults
        ).pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(self.form, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 5))

        self.status = self._widgets.Label(self.form, text="Ready")
        self.status.pack(fill=tk.X, pady=(0, 5))

        # ---- Right panel: time-history + interactive 3D field
        self.right_notebook = self._widgets.Notebook(right)
        self.right_notebook.pack(fill=tk.BOTH, expand=True)

        history_tab = ttk.Frame(self.right_notebook, padding=4)
        field_tab = ttk.Frame(self.right_notebook, padding=4)
        o2_history_tab = ttk.Frame(self.right_notebook, padding=4)
        o2_field_tab = ttk.Frame(self.right_notebook, padding=4)

        self.right_notebook.add(history_tab, text="Temperature history")
        self.right_notebook.add(field_tab, text="3D temperature field")
        self.right_notebook.add(o2_history_tab, text="O₂ history")
        self.right_notebook.add(o2_field_tab, text="3D O₂ field")

        # Time-history graph
        self.fig = Figure(figsize=(8.2, 5.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel(self._tr("Elapsed time [h]"))
        self.ax.set_ylabel(self._tr("Temperature [°C]"))
        self.ax.grid(True, alpha=0.25)

        self.canvas_plot = FigureCanvasTkAgg(self.fig, master=history_tab)
        self.canvas_plot.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.hover_lines = []
        self.hover_annotation = None
        self.canvas_plot.mpl_connect("motion_notify_event", self._on_plot_hover)
        self.canvas_plot.mpl_connect("axes_leave_event", self._on_plot_leave)

        self.summary = self._widgets.Text(history_tab, height=12, wrap="word")
        self.summary.pack(fill=tk.X, pady=(8, 0))
        self.summary.configure(state=tk.DISABLED)

        # Rotatable 3D temperature field
        self.field_fig = Figure(figsize=(8.2, 6.2), dpi=100)
        self.field_ax = self.field_fig.add_subplot(111, projection="3d")
        self.field_canvas = FigureCanvasTkAgg(self.field_fig, master=field_tab)
        self.field_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        field_controls = ttk.Frame(field_tab)
        field_controls.pack(fill=tk.X, pady=(6, 2))

        self.field_prev_btn = self._widgets.Button(
            field_controls, text="◀", width=4,
            command=lambda: self._step_3d_frame(-1), state=tk.DISABLED
        )
        self.field_prev_btn.pack(side=tk.LEFT, padx=2)

        self.field_play_btn = self._widgets.Button(
            field_controls, text="Play", width=7,
            command=self._toggle_3d_play, state=tk.DISABLED
        )
        self.field_play_btn.pack(side=tk.LEFT, padx=2)

        self.field_next_btn = self._widgets.Button(
            field_controls, text="▶", width=4,
            command=lambda: self._step_3d_frame(1), state=tk.DISABLED
        )
        self.field_next_btn.pack(side=tk.LEFT, padx=2)

        self.field_slider_var = tk.DoubleVar(value=0)
        self.field_slider = ttk.Scale(
            field_controls, from_=0, to=0, orient=tk.HORIZONTAL,
            variable=self.field_slider_var, command=self._on_3d_slider
        )
        self.field_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self.field_time_label = self._widgets.Label(
            field_controls, text="Run a simulation to view the 3D field."
        )
        self.field_time_label.pack(side=tk.RIGHT, padx=4)

        self._widgets.Label(
            field_tab,
            text=(
                "Drag the plot with the mouse to rotate it. Large grids are decimated only for "
                "3D display speed; the thermal calculation keeps the full requested grid."
            ),
            foreground="#555"
        ).pack(fill=tk.X, pady=(2, 0))

        # O2 time-history graph
        self.o2_fig = Figure(figsize=(8.2, 5.5), dpi=100)
        self.o2_ax = self.o2_fig.add_subplot(111)
        self.o2_ax.set_xlabel(self._tr("Elapsed time [h]"))
        self.o2_ax.set_ylabel(self._tr("O₂ [vol%]"))
        self.o2_ax.grid(True, alpha=0.25)

        self.o2_canvas_plot = FigureCanvasTkAgg(
            self.o2_fig, master=o2_history_tab
        )
        self.o2_canvas_plot.get_tk_widget().pack(
            fill=tk.BOTH, expand=True
        )

        self.o2_hover_lines = []
        self.o2_hover_annotation = None
        self.o2_canvas_plot.mpl_connect(
            "motion_notify_event", self._on_o2_plot_hover
        )
        self.o2_canvas_plot.mpl_connect(
            "axes_leave_event", self._on_o2_plot_leave
        )

        self.o2_summary = self._widgets.Text(
            o2_history_tab, height=10, wrap="word"
        )
        self.o2_summary.pack(fill=tk.X, pady=(8, 0))
        self.o2_summary.configure(state=tk.DISABLED)

        # Rotatable 3D O2 field
        self.o2_field_fig = Figure(figsize=(8.2, 6.2), dpi=100)
        self.o2_field_ax = self.o2_field_fig.add_subplot(
            111, projection="3d"
        )
        self.o2_field_canvas = FigureCanvasTkAgg(
            self.o2_field_fig, master=o2_field_tab
        )
        self.o2_field_canvas.get_tk_widget().pack(
            fill=tk.BOTH, expand=True
        )

        o2_controls = ttk.Frame(o2_field_tab)
        o2_controls.pack(fill=tk.X, pady=(6, 2))

        self.o2_field_prev_btn = self._widgets.Button(
            o2_controls, text="◀", width=4,
            command=lambda: self._step_o2_3d_frame(-1),
            state=tk.DISABLED
        )
        self.o2_field_prev_btn.pack(side=tk.LEFT, padx=2)

        self.o2_field_play_btn = self._widgets.Button(
            o2_controls, text="Play", width=7,
            command=self._toggle_o2_3d_play,
            state=tk.DISABLED
        )
        self.o2_field_play_btn.pack(side=tk.LEFT, padx=2)

        self.o2_field_next_btn = self._widgets.Button(
            o2_controls, text="▶", width=4,
            command=lambda: self._step_o2_3d_frame(1),
            state=tk.DISABLED
        )
        self.o2_field_next_btn.pack(side=tk.LEFT, padx=2)

        self.o2_field_slider_var = tk.DoubleVar(value=0)
        self.o2_field_slider = ttk.Scale(
            o2_controls, from_=0, to=0,
            orient=tk.HORIZONTAL,
            variable=self.o2_field_slider_var,
            command=self._on_o2_3d_slider
        )
        self.o2_field_slider.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8
        )

        self.o2_field_time_label = self._widgets.Label(
            o2_controls,
            text="Run an O₂-enabled simulation to view the field."
        )
        self.o2_field_time_label.pack(side=tk.RIGHT, padx=4)

        self._widgets.Label(
            o2_field_tab,
            text=(
                "O₂ is shown as actual gas-phase-equivalent vol%. "
                "This mode includes diffusion only; no binder reaction or O₂ consumption."
            ),
            foreground="#555"
        ).pack(fill=tk.X, pady=(2, 0))

    def _set_main_sash_position(self, px=600):
        """Set a comfortable initial parameter-panel width."""
        try:
            total = max(self.winfo_width(), 1)
            # Leave a useful plotting area even on smaller displays.
            target = int(min(max(px, 500), max(total - 600, 500)))
            self.main_pane.sashpos(0, target)
        except Exception:
            pass

    def _apply_language(self):
        """Refresh presentation in place; input values and solver state stay intact."""
        if self.language_var.get() not in ("ko", "en"):
            self.language_var.set("ko")
            return
        self.title(self._tr("Oven thermal diffusion simulator"))
        self._i18n.refresh()
        # Matplotlib text is presentation-only. Reuse the original English
        # strings retained by the catalogue, rather than translating CSV keys.
        for name in ("fig", "field_fig", "o2_fig", "o2_field_fig"):
            figure = getattr(self, name, None)
            if figure is None:
                continue
            self._format_plot_text(figure)
            figure.canvas.draw_idle()
        result = getattr(self, "result", None)
        if result is not None:
            self._show_summary(result)
            self._show_o2_summary(result)

    @staticmethod
    @lru_cache(maxsize=1)
    def _korean_plot_font():
        """Use the installed Korean font without changing other tools' defaults."""
        from matplotlib import font_manager
        for name in ("Malgun Gothic", "Noto Sans CJK KR", "NanumGothic", "AppleGothic"):
            try:
                font_manager.findfont(name, fallback_to_default=False)
                return name
            except ValueError:
                pass
        return "DejaVu Sans"

    def _format_plot_text(self, figure):
        from matplotlib.text import Text
        korean = self.language_var.get() == "ko"
        font = self._korean_plot_font() if korean else None
        for artist in figure.findobj(match=Text):
            original = self._i18n.canonical(artist.get_text())
            artist.set_text(self._tr(original))
            if korean:
                # The Korean font may omit scientific glyphs such as O₂.
                artist.set_fontfamily([font, "DejaVu Sans"])
        for axes in figure.axes:
            for artist in [*axes.get_lines(), *axes.collections]:
                label = artist.get_label()
                if label and not label.startswith("_"):
                    artist.set_label(self._tr(self._i18n.canonical(label)))

    def _draw_canvas(self, canvas):
        self._format_plot_text(canvas.figure)
        canvas.draw_idle()

    def _show_input_guide(self):
        """
        Open a detailed Korean guide for every major GUI input.
        The guide is intentionally model-specific so the names match this program.
        """
        win = tk.Toplevel(self)
        win.title(self._tr("SiC Build Box Simulator — 입력값 상세 설명서"))
        win.geometry("1080x780")
        win.minsize(820, 580)
        win.transient(self)

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 8))

        self._widgets.Label(
            header,
            text="입력값 상세 설명서",
            font=("TkDefaultFont", 13, "bold")
        ).pack(side=tk.LEFT)

        self._widgets.Label(
            header,
            text="현재 코드의 계산식·단위·권장 사용법 기준",
            foreground="#555"
        ).pack(side=tk.LEFT, padx=12)

        notebook = self._widgets.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)

        sections = [
            (
                "1. Powder region",
                """[Powder-filled region]

X [mm], Y [mm], Z [mm]
  • 실제 SiC 파우더가 채워져 있는 영역의 크기입니다.
  • 빌드박스의 외형치수가 아니라 '실제로 파우더가 존재하는 계산영역'을 입력합니다.
  • X, Y, Z가 커질수록 중심까지의 열전달 및 산소 확산 거리가 길어집니다.
  • 코드에서는 이 영역을 nx × ny × nz 개의 3D cell로 나누어 계산합니다.

중요
  • 열전대 Calibration을 할 경우, 실험 때의 파우더 충진높이와 여기의 Z가 반드시 같아야 합니다.
  • 빌드박스 내부에 빈 상부 공간이 있다면 그 공간은 현재 powder-domain에 포함시키지 않습니다.
  • 예: 내부가 450 × 550 mm이고 파우더 높이가 180 mm라면
       X=450, Y=550, Z=180 으로 입력합니다.
"""
            ),
            (
                "2. Build box",
                """[Build box]

Material
  • 빌드박스 금속 재질 프리셋입니다.
  • 선택하면 Wall k가 해당 프리셋 값으로 바뀝니다.
  • 실제 합금/온도에서의 열전도율을 알고 있다면 Wall k를 직접 수정하는 편이 더 좋습니다.

Wall k [W/mK]
  • 빌드박스 벽 소재의 열전도율입니다.
  • 측벽과 하부판 내부의 금속 전도 및 벽-파우더 열전달 계산에 사용됩니다.

Wall density [kg/m³]
  • 빌드박스 금속의 밀도입니다.
  • Wall cp와 함께 금속벽 및 하부판의 열용량을 결정합니다.

Wall cp [J/kgK]
  • 빌드박스 금속의 비열입니다.
  • 밀도·두께와 함께 측벽이 오븐 온도를 따라가는 시간지연을 결정합니다.

Side thickness [mm]
  • X/Y 방향 측벽 두께입니다.
  • 측벽의 열용량, 면내 전도 및 하부판 연결부 전도에 사용됩니다.

Bottom thickness [mm]
  • 연결된 금속 하부판 두께입니다.
  • 하부판의 열용량, 면내 전도 및 하부 파우더와의 열전달에 사용됩니다.

Under-plate gap [mm]
  • 하부판 외측과 오븐 바닥 사이의 수직 공기층 높이입니다.
  • 기본값 40 mm이며 자연대류 Rayleigh 수와 기체 전도 열저항에 사용됩니다.

Oven-floor emissivity
  • 하부판을 바라보는 오븐 바닥의 방사율입니다.
  • 일반적인 비연마·산화 금속 표면을 가정한 기본값은 0.80입니다.

Floor view factor [0–1]
  • 하부판이 오븐 바닥을 얼마나 직접 바라보는지 나타내는 복사 형상계수입니다.
  • 오븐 바닥이 하부판보다 넓고 간격이 40 mm인 일반적인 구조의 기본값은 0.95입니다.

Top/lid thickness [mm]
  • 상부에 금속 뚜껑이 있는 경우 그 두께입니다.
  • 'Top is open to oven air'가 켜져 있으면 열해석상 상부 금속벽 두께는 사용하지 않습니다.

Top is open to oven air
  • 체크: 파우더 상부가 오븐 분위기에 직접 노출된 것으로 취급합니다.
  • 해제: 상부에도 금속벽이 존재하는 것으로 취급합니다.

주의
  • 네 측벽은 각각 2D 금속 온도장으로 계산되고, 하부판도 별도 2D 금속 온도장으로 계산됩니다.
  • 측벽 하단과 하부판의 네 가장자리는 보존적인 금속 전도 열유량으로 연결됩니다.
  • 하부 빈 공간에는 오븐 바닥-하부판 복사와 수평 공기층의 기체전도·자연대류를 적용합니다.
  • 강제대류 항은 적용하지 않습니다.
  • 오븐 바닥온도는 입력한 Oven profile과 같다고 가정합니다.
  • 하부판은 측벽 금속전도와 하부 빈 공간 열전달을 동시에 받은 뒤 하부 파우더에 전달합니다.
  • 상부가 닫힌 경우 상부판은 아직 열저항만 반영하며, 상부판 열용량은 별도 mesh하지 않습니다.
"""
            ),
            (
                "3. SiC / PSD / k_eff",
                """[SiC powder bed / k_eff model]

SiC preset
  • α-SiC, β-SiC 등의 기본 진밀도·비열·고체 열전도율 프리셋입니다.
  • 실제 사용 분말의 제조사 물성이 있으면 직접 입력값을 우선하십시오.

Bed density [g/cc]
  • 실제 리코팅된 powder bed의 벌크밀도입니다.
  • 공극률 계산의 핵심 입력입니다.
  • 코드 내부에서는 자동으로 kg/m³로 변환됩니다.

True density [g/cc]
  • SiC 입자 고체 자체의 진밀도입니다.
  • Solid fraction = Bed density / True density
  • Porosity = 1 - Solid fraction

cp [J/kgK]
  • 파우더 고체의 비열입니다.
  • rho × cp가 커질수록 동일 열유입에 대한 승온이 느려집니다.

Solid SiC k @25°C [W/mK]
  • 치밀한 SiC 고체의 기준 열전도율입니다.
  • 이것을 powder bed의 k_eff로 직접 사용하지 않습니다.

Pore-gas k @25°C [W/mK]
  • 파우더 공극을 채우는 기체의 열전도율 기준값입니다.
  • 일반 공기를 가정한 초기값을 사용할 수 있습니다.

k_eff mode
  1) Manual
     - Manual k_eff 한 값을 모든 위치·온도에서 일정하게 사용합니다.

  2) PSD estimate
     - Bed density + D10/D50/D90 + 고체/기체 열전도율 + 온도 + 복사를 사용해
       각 cell의 k_eff(T)를 계속 계산합니다.

  3) Calibrated
     - PSD estimate로 계산된 k_eff(T)에 'Calibrated k scale'을 곱하고,
       별도로 fitting된 h를 사용합니다.
     - 실제 중심 열전대 데이터가 있을 때 가장 권장되는 모드입니다.

D10 / D50 / D90 [µm]
  • 누적 입도분포에서 각각 10%, 50%, 90% 지점의 입경입니다.
  • 코드에서는 PSD Span 및 lognormal 폭, 추정 D32를 계산합니다.
  • O₂ 모델에서는 characteristic pore length 추정에도 사용합니다.

Solid-k T exponent n
  • SiC 고체 열전도율의 온도 변화 근사식에 사용됩니다.
      k_s(T) = k_25 × (298.15 / T_K)^n
  • 실제 SiC grade의 고온 열전도율 자료가 있으면 n을 조정할 수 있습니다.

Contact blend [0–1]
  • 입자 접촉 및 packing 구조가 k_eff에 미치는 불확실성을 나타내는 모델 파라미터입니다.
  • 0에 가까울수록 보수적인 Maxwell gas-matrix 쪽,
    1에 가까울수록 geometric-mean 쪽으로 이동합니다.
  • 실측 없이 임의로 크게 조정하기보다 Calibration을 권장합니다.

Manual k_eff [W/mK]
  • k_eff mode가 Manual일 때 사용하는 상수입니다.

Preview k_eff(T)
  • 25/100/165/180/200°C에서 현재 입력조건의 k_eff 예측값을 미리 보여줍니다.

Model info
  • 세 가지 k_eff mode의 차이를 간단히 보여줍니다.
"""
            ),
            (
                "4. Oven heat",
                """[Oven heat transfer]

Convection h [W/m²K]
  • 오븐 분위기와 빌드박스 외부 사이의 유효 대류 열전달계수입니다.
  • 값이 클수록 빌드박스/파우더가 오븐 온도를 빠르게 따라갑니다.
  • 강제대류 오븐의 실제 h는 장비·풍속·적재상태에 따라 달라집니다.

Effective emissivity
  • 복사 열전달 계산에 사용하는 유효 방사율입니다.
  • 0~1 사이 값이어야 합니다.
  • 150~200°C에서도 복사는 무시되지 않으므로 코드에서 대류와 함께 계산합니다.

Initial bed temp [°C]
  • 시뮬레이션 시작 순간 전체 powder domain의 초기온도입니다.
  • 실제 오븐 투입 직전 파우더 온도와 맞추는 것이 좋습니다.

중요
  • 실제 중심온도 예측 정확도에는 k_eff와 h가 가장 큰 불확실성으로 작용하기 쉽습니다.
  • 가능하면 중심 열전대 실험으로 두 값을 Calibration하십시오.
"""
            ),
            (
                "5. O₂ diffusion",
                """[O₂ diffusion only]

Enable O₂ diffusion-only simulation
  • 체크하면 열해석과 같은 3D grid/time-step에서 산소 확산 PDE도 함께 계산합니다.
  • 현재는 '확산만' 계산하며 산소 소비·PVP 산화반응은 없습니다.

Ambient O₂ [vol%]
  • 외부 오븐 분위기의 산소 농도입니다.
  • 일반 공기를 단순화하면 약 20.95 vol%를 사용할 수 있습니다.

Initial pore O₂ [vol%]
  • 계산 시작 순간 powder 공극 내부에 이미 존재하는 산소 농도입니다.

매우 중요
  • 공기 중에서 준비한 파우더라면 보통 Initial O₂와 Ambient O₂가 거의 같습니다.
  • 이 두 값이 모두 20.95%이고 '반응이 없는 diffusion-only'라면,
    처음부터 농도구배가 없기 때문에 O₂가 20.95%로 균일하게 유지되는 것이 정상입니다.
  • 순수하게 '무산소 상태에서 외부 산소가 얼마나 빨리 침투하는가'를 보고 싶을 때만
    Initial O₂를 0% 등으로 낮춰 사용하십시오.

O₂ D_air @25°C [m²/s]
  • 25°C에서 자유 기체 중 O₂의 기준 분자확산계수입니다.

D temperature exponent
  • 온도에 따른 분자확산계수 변화식에 사용됩니다.
      D(T) = D_25 × (T_K / 298.15)^n

Tortuosity model
  • Bruggeman:
      porosity로부터 tortuosity를 자동 추정합니다.
  • Manual:
      사용자가 직접 τ를 입력합니다.

Manual tortuosity τ
  • Manual mode일 때만 의미가 있습니다.
  • 1 이상이어야 하며, 커질수록 실제 확산 경로가 굴곡져 D_eff가 감소합니다.

Include PSD-based Knudsen correction
  • D10/D50/D90로 추정한 characteristic pore length를 사용하여
    molecular diffusion과 Knudsen diffusion을 Bosanquet 형태로 결합합니다.
  • 현재 입도 수준/대기압에서는 보정이 작을 수도 있습니다.

O₂ access boundaries
  • Top: 상부에서 외부 O₂가 유입될 수 있음
  • Sides: 네 측면에서 O₂ 유입 가능
  • Bottom: 연결된 금속 하부판으로 밀폐되어 있다고 가정하므로 선택할 수 없음
  • 금속으로 완전히 밀폐된 면은 체크하지 않는 것이 맞습니다.

Preview O₂ diffusivity
  • 현재 공극률/PSD/온도에서 D_eff 값을 미리 표시합니다.

현재 모델에 없는 것
  • PVP/유기물에 의한 O₂ 소비
  • 산화반응 속도
  • 압력차에 의한 Darcy flow
  • powder 내부 자연대류
  • 산화에 따른 생성가스/조성 변화
"""
            ),
            (
                "6. Calibration",
                """[Center thermocouple calibration]

목적
  • PSD만으로 정확히 알기 어려운 powder-bed k_eff 크기와
    오븐의 유효 대류계수 h를 실제 중심온도 이력에 맞춰 역추정합니다.

Load TC CSV
  • 중심 열전대 시간-온도 데이터를 불러옵니다.
  • 권장 형식:
      time_min,center_C
      0,25.1
      1,25.2
      2,25.4
      ...

Fit k scale + h
  • 같은 3D thermal solver를 반복 실행하면서 다음 두 값을 동시에 최적화합니다.
      Calibrated k scale
      Calibrated h
  • fitting 동안 O₂ 계산은 열결과에 영향을 주지 않으므로 꺼서 계산량을 줄입니다.

Calibrated k scale
  • PSD 기반 k_eff(T)에 곱해지는 보정계수입니다.
      k_eff,cal(T) = scale × k_eff,PSD(T)

Calibrated h [W/m²K]
  • 열전대 데이터와 가장 잘 맞도록 fitting된 유효 대류계수입니다.

RMSE
  • 계산 중심온도와 실측 중심온도 차이의 Root Mean Square Error입니다.
  • 낮을수록 해당 실험에 대한 일치도가 좋습니다.

좋은 Calibration 실험 조건
  • 실제 생산과 동일한 powder X/Y/Z
  • 동일한 bed density 및 powder
  • 동일한 build box
  • 동일한 oven 위치/적재조건
  • 열전대를 가능한 한 기하학적 중심에 배치
  • 승온구간부터 유지구간까지 데이터 포함

검증 권장
  • 한 프로파일로 calibration 후,
    다른 oven profile을 같은 scale/h로 예측해 별도 검증하는 것이 가장 좋습니다.
"""
            ),
            (
                "7. Numerical",
                """[Numerical settings]

Grid nx / ny / nz
  • X/Y/Z 방향 계산 cell 개수입니다.
  • 실제 cell size는
      dx = X / nx
      dy = Y / ny
      dz = Z / nz
    입니다.
  • grid 수를 늘리면 공간분해능은 좋아지지만 계산시간과 메모리가 증가합니다.

Max Δt [s]
  • 사용자가 허용하는 최대 timestep입니다.
  • 실제 timestep은 이것보다 작아질 수 있습니다.
  • 코드가 열확산 안정조건과 O₂ 확산 안정조건을 검사하여 자동으로 더 작은 Δt를 선택합니다.

Output interval [s]
  • 온도/O₂ history를 결과 배열과 CSV에 기록하는 시간 간격입니다.
  • solver timestep 자체를 의미하지 않습니다.

3D snapshot interval [min]
  • 3D 온도장/O₂장을 저장하는 간격입니다.
  • 수치계산 정밀도에는 영향을 주지 않고 visualization 저장량에만 영향을 줍니다.
  • 메모리 폭증을 막기 위해 코드가 최대 frame 수를 자동 제한할 수 있습니다.

정밀도
  • grid를 너무 거칠게 하면 중심/경계 온도 및 침투 front가 둔화되어 보일 수 있습니다.
  • 최종 검증 시에는 grid refinement test를 권장합니다.
"""
            ),
            (
                "8. Oven profile",
                """[Oven profile — absolute elapsed time]

표의 Time [min]
  • 각 행은 '해당 단계의 지속시간'이 아니라 실험 시작부터의 누적 절대시간입니다.

표의 Oven [°C]
  • 해당 누적시간에서의 오븐 환경온도입니다.
  • 두 입력점 사이의 온도는 선형보간됩니다.

예시
  0 min      25°C
  60 min     80°C
  120 min    80°C
  330 min    200°C
  1050 min   200°C

  → 60~120분은 80°C 유지
  → 330~1050분은 200°C에서 720분 = 12시간 유지

Add point
  • 시간/온도 점을 추가합니다.

Edit
  • 선택한 점을 수정합니다.

Delete
  • 선택한 점을 삭제합니다.

권장
  • 가능하면 오븐 controller의 설정 profile보다 실제 chamber air thermocouple 로그를 사용하면
    중심온도 예측이 더 정확해집니다.
"""
            ),
            (
                "9. Results",
                """[결과 탭 해석]

Temperature history
  • Oven air: 입력한 오븐 프로파일
  • Side-wall avg.: 네 측벽 금속의 면적가중 평균온도
  • Bottom-plate avg.: 측벽 전도와 하부 빈 공간 열전달을 받는 하부판 금속 평균온도
  • Powder surface avg.: 6개 면의 표면 cell 평균 진단값
  • Powder volume avg.: 전체 powder 평균온도
  • Powder center: 기하학적 중심 cell(짝수 grid는 중앙 여러 cell 평균)
  • Bed min-max: 전체 계산영역의 온도 범위

3D temperature field
  • 저장된 각 시간 snapshot의 3D 온도분포입니다.
  • Slider, ◀/▶, Play를 이용해 시간에 따른 열침투를 볼 수 있습니다.
  • 큰 grid에서는 표시 속도를 위해 '표시만' stride sampling할 수 있으며,
    실제 thermal solver grid는 줄이지 않습니다.

O₂ history
  • O₂ center: 중심 산소농도
  • O₂ top-layer avg.: 상부 cell layer 평균
  • O₂ volume avg.: 전체 공극 평균
  • Bed O₂ min-max: 영역 내 최소~최대
  • Ambient O₂: 외부 경계 농도

Center cumulative O₂ dose [air-equivalent h]
  • 중심 O₂ 농도를 ambient O₂로 정규화하여 시간적분한 값입니다.
  • 예: ambient의 50% O₂에 2시간 노출 → 약 1 air-equivalent hour.

3D O₂ field
  • 시간에 따른 공간별 O₂ vol%를 보여줍니다.

CSV Export
  • 시간별 파우더·측벽·하부판 온도, 하부 공기층 h, 하부 대류·복사 열량,
    k_eff, O₂ 농도, D_eff, O₂ dose를 CSV로 저장합니다.

주의
  • 현재 O₂ 모델은 diffusion-only이므로 실제 PVP 산화 정도를 직접 의미하지 않습니다.
  • PVP 산화/가교 가능성을 다루려면 향후 O₂ 소비 및 Arrhenius reaction term이 추가되어야 합니다.
"""
            ),
        ]

        def add_text_tab(title, body):
            frame = ttk.Frame(notebook, padding=8)
            notebook.add(frame, text=title)

            text_frame = ttk.Frame(frame)
            text_frame.pack(fill=tk.BOTH, expand=True)

            yscroll = ttk.Scrollbar(text_frame, orient="vertical")
            yscroll.pack(side=tk.RIGHT, fill=tk.Y)

            txt = self._widgets.Text(
                text_frame,
                wrap="word",
                padx=14,
                pady=12,
                yscrollcommand=yscroll.set,
                font=("TkDefaultFont", 10)
            )
            txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            yscroll.configure(command=txt.yview)

            txt.insert("1.0", body.strip())
            txt.configure(state=tk.DISABLED)

        for title, body in sections:
            add_text_tab(title, body)

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(8, 0))

        self._widgets.Label(
            footer,
            text=(
                "※ 설명서는 현재 v7 계산모델 기준입니다. "
                "실측 물성이 있으면 프리셋보다 실측값을 우선하십시오."
            ),
            foreground="#555"
        ).pack(side=tk.LEFT)

        self._widgets.Button(
            footer,
            text="닫기",
            command=win.destroy
        ).pack(side=tk.RIGHT)

    def _entry_row(self, parent, row, label):
        # Long labels are allowed to wrap instead of being truncated.
        lbl = self._widgets.Label(
            parent,
            text=label,
            anchor="w",
            justify=tk.LEFT,
            wraplength=255
        )
        lbl.grid(
            row=row, column=0,
            sticky="w", padx=(4, 10), pady=3
        )

        var = tk.StringVar()
        ent = ttk.Entry(parent, textvariable=var, width=25)
        ent.grid(
            row=row, column=1,
            sticky="ew", padx=(2, 5), pady=3
        )

        # Keep a useful label/value ratio, while allowing the right column
        # to absorb any extra width when the user enlarges the left pane.
        parent.columnconfigure(0, weight=0, minsize=245)
        parent.columnconfigure(1, weight=1, minsize=190)
        return var

    def _load_defaults(self):
        # Clear the profile first so repeated Reset Defaults does not duplicate rows.
        for iid in self.profile_tree.get_children():
            self.profile_tree.delete(iid)

        self.lx.set("400")
        self.ly.set("500")
        self.lz.set("350")

        self.box_mat.set("SUS304")
        self.box_k.set(str(BOX_MATERIALS["SUS304"]))
        self.box_density.set(str(BOX_THERMAL_MASS["SUS304"]["rho"]))
        self.box_cp.set(str(BOX_THERMAL_MASS["SUS304"]["cp"]))
        self.side_t.set("3")
        self.bottom_t.set("5")
        self.top_t.set("3")
        self.bottom_gap.set("40")
        self.floor_emissivity.set("0.80")
        self.bottom_view_factor.set("0.95")
        self.top_open_var.set(True)

        self.sic_type.set("SiC general")
        self.bed_rho.set("1.700")
        self.true_rho.set("3.210")
        self.cp.set("750")
        self.k_solid.set("80")
        self.gas_k.set("0.026")
        self.k_mode.set("PSD estimate")
        self.d10.set("40.6")
        self.d50.set("65.4")
        self.d90.set("105.0")
        self.solid_k_exp.set("0.75")
        self.contact_blend.set("0.50")
        self.k_eff.set("0.60")
        self.cal_scale.set("1.00")
        self.cal_h.set("12.0")

        self.hconv.set("12")
        self.emissivity.set("0.75")
        self.initial_temp.set("25")

        # O2 diffusion-only defaults.
        # 20.95 -> 20.95 is the physically appropriate default for a powder
        # bed initially equilibrated with ordinary air.
        self.o2_enabled_var.set(True)
        self.o2_ambient.set("20.95")
        self.o2_initial.set("20.95")
        self.o2_d25.set("2.05e-5")
        self.o2_temp_exp.set("1.75")
        self.o2_tau_mode.set("Bruggeman")
        self.o2_tau.set("1.46")
        self.o2_knudsen_var.set(True)
        self.o2_top_access_var.set(True)
        self.o2_side_access_var.set(False)
        self.o2_bottom_access_var.set(False)

        self.nx.set("20")
        self.ny.set("24")
        self.nz.set("18")
        self.max_dt.set("10")
        self.record_interval.set("60")
        self.snapshot_interval.set("10")

        default_profile = [
            (0, 25),
            (60, 80),
            (120, 80),
            (180, 120),
            (240, 120),
            (330, 200),
            (1050, 200),   # 12 h hold from 330 to 1050 min
            (1170, 80),
        ]
        for t, temp in default_profile:
            self.profile_tree.insert("", "end", values=(t, temp))

        self._update_porosity_label()
        self._update_psd_label()

    def _settings_variables(self):
        """Map persistent setting keys to tkinter variables."""
        return {
            "language": self.language_var,
            "lx": self.lx,
            "ly": self.ly,
            "lz": self.lz,
            "box_mat": self.box_mat,
            "box_k": self.box_k,
            "box_density": self.box_density,
            "box_cp": self.box_cp,
            "side_t": self.side_t,
            "bottom_t": self.bottom_t,
            "top_t": self.top_t,
            "bottom_gap": self.bottom_gap,
            "floor_emissivity": self.floor_emissivity,
            "bottom_view_factor": self.bottom_view_factor,
            "sic_type": self.sic_type,
            "bed_rho": self.bed_rho,
            "true_rho": self.true_rho,
            "cp": self.cp,
            "k_solid": self.k_solid,
            "gas_k": self.gas_k,
            "k_mode": self.k_mode,
            "d10": self.d10,
            "d50": self.d50,
            "d90": self.d90,
            "solid_k_exp": self.solid_k_exp,
            "contact_blend": self.contact_blend,
            "k_eff": self.k_eff,
            "cal_scale": self.cal_scale,
            "cal_h": self.cal_h,
            "hconv": self.hconv,
            "emissivity": self.emissivity,
            "initial_temp": self.initial_temp,
            "o2_ambient": self.o2_ambient,
            "o2_initial": self.o2_initial,
            "o2_d25": self.o2_d25,
            "o2_temp_exp": self.o2_temp_exp,
            "o2_tau_mode": self.o2_tau_mode,
            "o2_tau": self.o2_tau,
            "nx": self.nx,
            "ny": self.ny,
            "nz": self.nz,
            "max_dt": self.max_dt,
            "record_interval": self.record_interval,
            "snapshot_interval": self.snapshot_interval,
        }

    def _capture_settings(self):
        """
        Capture ALL user-entered thermal inputs plus the full oven profile.
        Values are stored as strings so reloading reproduces the exact GUI text.
        """
        data = {
            "settings_version": 6,
            "top_open": bool(self.top_open_var.get()),
            "o2_enabled": bool(self.o2_enabled_var.get()),
            "o2_knudsen": bool(self.o2_knudsen_var.get()),
            "o2_top_access": bool(self.o2_top_access_var.get()),
            "o2_side_access": bool(self.o2_side_access_var.get()),
            "o2_bottom_access": False,
            "variables": {
                key: str(var.get())
                for key, var in self._settings_variables().items()
            },
            "oven_profile": [
                [float(t), float(temp)]
                for t, temp in self._profile_values()
            ],
        }

        # Save the last selected TC path only as convenience metadata.
        # The thermocouple data themselves are NOT embedded in the settings file.
        if self.tc_path:
            data["last_tc_path"] = str(self.tc_path)

        return data

    def _save_settings(self, silent=True):
        """
        Save the current GUI values atomically.

        The file is written in the user's home folder:
            ~/.sic_buildbox_thermal_gui_settings.json
        """
        try:
            data = self._capture_settings()
            path = self.settings_path
            path.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_path.replace(path)

            if not silent:
                self._dialogs.showinfo(
                    "Inputs saved",
                    f"Current inputs and oven profile were saved.\n\n{path}"
                )
            return True
        except Exception as e:
            if not silent:
                self._dialogs.showerror("Save error", str(e))
            return False

    def _apply_saved_settings_dict(self, data):
        """Apply a previously captured settings dictionary to the GUI."""
        if not isinstance(data, dict):
            raise ValueError("Saved settings are not a valid object.")

        variables = data.get("variables", {})
        if not isinstance(variables, dict):
            raise ValueError("Saved variables section is invalid.")

        mapping = self._settings_variables()
        for key, value in variables.items():
            var = mapping.get(key)
            if var is not None:
                var.set(str(value))

        if "top_open" in data:
            self.top_open_var.set(bool(data["top_open"]))

        if "o2_enabled" in data:
            self.o2_enabled_var.set(bool(data["o2_enabled"]))
        if "o2_knudsen" in data:
            self.o2_knudsen_var.set(bool(data["o2_knudsen"]))
        if "o2_top_access" in data:
            self.o2_top_access_var.set(bool(data["o2_top_access"]))
        if "o2_side_access" in data:
            self.o2_side_access_var.set(bool(data["o2_side_access"]))
        # The connected metal bottom plate is sealed. Ignore an older saved
        # setting that may have exposed the bottom to ambient O2.
        self.o2_bottom_access_var.set(False)

        profile = data.get("oven_profile")
        if isinstance(profile, list) and len(profile) >= 2:
            parsed_profile = []
            for row in profile:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                parsed_profile.append((float(row[0]), float(row[1])))

            if len(parsed_profile) >= 2:
                parsed_profile.sort(key=lambda x: x[0])
                for iid in self.profile_tree.get_children():
                    self.profile_tree.delete(iid)
                for t, temp in parsed_profile:
                    self.profile_tree.insert("", "end", values=(t, temp))

        self.tc_path = data.get("last_tc_path") or self.tc_path

        self._update_porosity_label()
        self._update_psd_label()
        self._k_mode_changed()

    def _load_saved_settings(self, silent=True):
        """
        Load the last settings automatically at startup, or manually from the button.
        Invalid/corrupted settings never replace the hard-coded defaults.
        """
        try:
            path = self.settings_path
            if not path.exists():
                if not silent:
                    self._dialogs.showinfo(
                        "Load saved",
                        "No previously saved input file exists yet.\n"
                        "Run a simulation once or press Save inputs first."
                    )
                return False

            data = json.loads(path.read_text(encoding="utf-8"))
            self._apply_saved_settings_dict(data)

            if not silent:
                self._dialogs.showinfo(
                    "Inputs loaded",
                    f"Restored the last saved inputs and oven profile.\n\n{path}"
                )
            else:
                self.status.config(text="Ready — last saved inputs restored")
            return True
        except Exception as e:
            # Startup must remain usable even if the JSON was manually edited/corrupted.
            if not silent:
                self._dialogs.showerror("Load error", str(e))
            return False

    def _reset_defaults(self):
        if self._dialogs.askyesno(
            "Reset defaults",
            "Reset all inputs and the oven profile to the built-in defaults?"
        ):
            self._load_defaults()
            self.status.config(text="Built-in defaults restored")

    def _on_close(self):
        # Save whatever is currently visible, even if the user edited values
        # after the most recent simulation.
        try:
            self._save_settings(silent=True)
        finally:
            self._stop_3d_play()
            self._stop_o2_3d_play()
            self.destroy()

    def _box_material_changed(self, _evt=None):
        name = self.box_mat.get()
        if name in BOX_MATERIALS:
            self.box_k.set(str(BOX_MATERIALS[name]))
        thermal_mass = BOX_THERMAL_MASS.get(name)
        if thermal_mass:
            self.box_density.set(str(thermal_mass["rho"]))
            self.box_cp.set(str(thermal_mass["cp"]))

    def _sic_preset_changed(self, _evt=None):
        name = self.sic_type.get()
        p = SIC_PRESETS.get(name)
        if p:
            self.true_rho.set(str(p["rho_true_gcc"]))
            self.cp.set(str(p["cp"]))
            self.k_solid.set(str(p["k_solid"]))
        self._update_porosity_label()
        self._update_psd_label()

    def _k_mode_changed(self, _evt=None):
        mode = self.k_mode.get()
        if mode == "Manual":
            self.cal_status.config(text="Manual mode: the constant Manual k_eff value is used.")
        elif mode == "PSD estimate":
            self.cal_status.config(text="PSD estimate mode: k_eff varies with local temperature.")
        else:
            self.cal_status.config(
                text="Calibrated mode: PSD k_eff(T) × fitted scale, with calibrated h."
            )

    def _update_psd_label(self):
        try:
            st = psd_statistics(
                float(self.d10.get()), float(self.d50.get()), float(self.d90.get())
            )
            self.psd_label.config(
                text=(
                    f"PSD span={(st['span']):.3f}   |   "
                    f"lognormal σ={st['sigma_ln']:.3f}   |   "
                    f"estimated D32={st['d32_um']:.1f} µm"
                )
            )
        except Exception:
            self.psd_label.config(text="PSD: -")

    def _update_porosity_label(self):
        try:
            rho_b = float(self.bed_rho.get())
            rho_s = float(self.true_rho.get())
            phi = rho_b / rho_s
            eps = 1.0 - phi
            self.porosity_label.config(
                text=f"Solid fraction: {phi:.3f}   |   Porosity: {eps:.3f}"
            )
        except Exception:
            self.porosity_label.config(text="Porosity: -")

    def _estimate_keff(self):
        try:
            inp = self._collect_inputs()
            self._update_porosity_label()
            self._update_psd_label()

            temps = np.asarray([25.0, 100.0, 165.0, 180.0, 200.0])
            kvals = effective_k(inp, temps)
            stats = psd_statistics(inp.d10_um, inp.d50_um, inp.d90_um)

            lines = [
                f"Mode: {inp.k_mode}",
                f"Bed density: {inp.bed_density_kgm3/1000.0:.4f} g/cc",
                f"Porosity: {1.0 - inp.bed_density_kgm3/inp.true_density_kgm3:.4f}",
                f"D10 / D50 / D90: {inp.d10_um:g} / {inp.d50_um:g} / {inp.d90_um:g} µm",
                f"PSD span: {stats['span']:.4f}",
                f"Estimated D32: {stats['d32_um']:.2f} µm",
                "",
                "Predicted k_eff:",
            ]
            for t, k in zip(temps, np.atleast_1d(kvals)):
                lines.append(f"  {t:6.1f} °C : {float(k):.4f} W/mK")

            if inp.k_mode == "Calibrated":
                lines += [
                    "",
                    f"Calibration k scale: {inp.calibration_k_scale:.4f}",
                    f"Calibrated h: {inp.calibration_h_wm2k:.3f} W/m²K",
                ]

            lines += [
                "",
                "Important: PSD mode is an engineering estimate. Particle-contact thermal",
                "resistance cannot be uniquely inferred from D10/D50/D90 alone.",
                "Use center-thermocouple calibration when available."
            ]

            self._dialogs.showinfo("k_eff(T) preview", "\n".join(lines))
        except Exception as e:
            self._dialogs.showerror("Input error", str(e))

    def _show_keff_info(self):
        self._dialogs.showinfo(
            "About the k_eff models",
            MODEL_INFO_EN
        )

    def _load_tc_csv(self):
        path = self._dialogs.askopenfilename(
            title="Load center thermocouple CSV",
            filetypes=[("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return

        rows = None
        last_error = None
        for enc in ("utf-8-sig", "cp949", "utf-8"):
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    rows = list(csv.reader(f))
                break
            except Exception as e:
                last_error = e

        if rows is None or len(rows) < 2:
            self._dialogs.showerror("CSV error", f"Could not read CSV.\\n{last_error}")
            return

        try:
            def norm(x):
                return str(x).strip().lower().replace(" ", "").replace("_", "").replace("-", "")

            first = rows[0]
            header_norm = [norm(x) for x in first]

            time_idx = None
            temp_idx = None
            time_in_hours = False

            time_min_alias = {"timemin", "elapsedmin", "minutes", "min", "time(minutes)"}
            time_h_alias = {"timeh", "elapsedh", "hours", "hour", "hr"}
            temp_alias = {
                "centerc", "centerdegc", "centertempc", "centertemperaturec",
                "tempc", "temperaturec", "temperature", "temp"
            }

            for i, h in enumerate(header_norm):
                if h in time_min_alias and time_idx is None:
                    time_idx = i
                if h in time_h_alias and time_idx is None:
                    time_idx = i
                    time_in_hours = True
                if h in temp_alias and temp_idx is None:
                    temp_idx = i

            # Detect whether first row is numeric. If yes, treat first two cols as data.
            numeric_first = False
            try:
                float(first[0]); float(first[1]); numeric_first = True
            except Exception:
                pass

            if numeric_first:
                data_rows = rows
                time_idx, temp_idx = 0, 1
            else:
                data_rows = rows[1:]
                if time_idx is None or temp_idx is None:
                    # Header fallback: first two columns.
                    time_idx, temp_idx = 0, 1

            tt, yy = [], []
            for row in data_rows:
                if len(row) <= max(time_idx, temp_idx):
                    continue
                try:
                    t = float(str(row[time_idx]).strip())
                    y = float(str(row[temp_idx]).strip())
                except Exception:
                    continue
                if time_in_hours:
                    t *= 60.0
                if math.isfinite(t) and math.isfinite(y):
                    tt.append(t)
                    yy.append(y)

            if len(tt) < 5:
                raise ValueError("Fewer than 5 numeric time/temperature rows were found.")

            arr_t = np.asarray(tt, dtype=np.float64)
            arr_y = np.asarray(yy, dtype=np.float64)
            order = np.argsort(arr_t)
            arr_t = arr_t[order]
            arr_y = arr_y[order]

            self.tc_time_min = arr_t
            self.tc_center_c = arr_y
            self.tc_path = path

            self.tc_status.config(
                text=(
                    f"Loaded {len(arr_t)} points | "
                    f"{arr_t[0]:.1f}–{arr_t[-1]:.1f} min | "
                    f"{Path(path).name}"
                )
            )
            self.cal_status.config(
                text="Thermocouple data loaded. Fit uses the currently entered oven profile."
            )

            if self.result is not None:
                self._plot_result(self.result)

        except Exception as e:
            self._dialogs.showerror("CSV error", str(e))

    def _fit_calibration(self):
        if self.worker and self.worker.is_alive():
            return
        if self.tc_time_min is None or self.tc_center_c is None:
            self._dialogs.showinfo("Calibration", "Load a center thermocouple CSV first.")
            return

        try:
            inp = self._collect_inputs()
            # Fitting is always based on PSD(T), regardless of the currently selected mode.
            inp = replace(inp, k_mode="Calibrated")
            validate_inputs(inp)
        except Exception as e:
            self._dialogs.showerror("Input error", str(e))
            return

        self.stop_event.clear()
        self.run_btn.configure(state=tk.DISABLED)
        self.fit_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress["value"] = 0
        self.status.config(text="Calibrating k scale + h...")

        def fit_status(n, rmse, scale, h):
            self.msg_queue.put(("cal_progress", (n, rmse, scale, h)))

        def work():
            try:
                fit = fit_thermal_calibration(
                    inp,
                    self.tc_time_min,
                    self.tc_center_c,
                    status_cb=fit_status,
                    stop_event=self.stop_event,
                )
                self.msg_queue.put(("cal_done", fit))
            except Exception as e:
                self.msg_queue.put(("error", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _preview_o2_diffusivity(self):
        try:
            inp = self._collect_inputs()
            validate_inputs(inp)

            por = 1.0 - inp.bed_density_kgm3 / inp.true_density_kgm3
            tau = oxygen_tortuosity(inp)
            pore_um = oxygen_characteristic_pore_m(inp) * 1e6
            temps = np.asarray([25.0, 100.0, 165.0, 180.0, 200.0])
            ds = np.atleast_1d(
                oxygen_effective_diffusivity(inp, temps)
            )

            lines = [
                "=== O₂ diffusion-only model preview ===",
                f"Porosity: {por:.4f}",
                f"Tortuosity τ: {tau:.4f} ({inp.o2_tortuosity_mode})",
                f"PSD-based characteristic pore length: {pore_um:.2f} µm",
                f"Knudsen correction: {'ON' if inp.o2_use_knudsen else 'OFF'}",
                "",
                "Effective O₂ diffusivity:",
            ]
            for temp, d in zip(temps, ds):
                lines.append(
                    f"  {temp:6.1f} °C : {float(d):.4e} m²/s"
                )

            if abs(
                inp.o2_initial_percent - inp.o2_ambient_percent
            ) < 1e-12:
                lines += [
                    "",
                    "Initial pore O₂ equals ambient O₂.",
                    "With diffusion only and no reaction, no O₂ gradient will form."
                ]

            self._dialogs.showinfo(
                "O₂ diffusivity preview", "\n".join(lines)
            )
        except Exception as e:
            self._dialogs.showerror("Input error", str(e))

    def _profile_values(self):
        vals = []
        for iid in self.profile_tree.get_children():
            row = self.profile_tree.item(iid, "values")
            vals.append((float(row[0]), float(row[1])))
        vals.sort(key=lambda x: x[0])
        return vals

    def _add_profile_point(self):
        self._profile_dialog(None)

    def _edit_profile_point(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._dialogs.showinfo("Edit", "Select one profile point.")
            return
        iid = sel[0]
        old = self.profile_tree.item(iid, "values")
        self._profile_dialog(iid, old)

    def _profile_dialog(self, iid=None, old=None):
        win = tk.Toplevel(self)
        win.title(self._tr("Oven profile point"))
        win.transient(self)
        win.grab_set()

        tvar = tk.StringVar(value=str(old[0]) if old else "")
        tempvar = tk.StringVar(value=str(old[1]) if old else "")

        self._widgets.Label(win, text="Elapsed time [min]").grid(row=0, column=0, padx=8, pady=6)
        ttk.Entry(win, textvariable=tvar).grid(row=0, column=1, padx=8, pady=6)
        self._widgets.Label(win, text="Oven temperature [°C]").grid(row=1, column=0, padx=8, pady=6)
        ttk.Entry(win, textvariable=tempvar).grid(row=1, column=1, padx=8, pady=6)

        def ok():
            try:
                t = float(tvar.get())
                temp = float(tempvar.get())
                if t < 0:
                    raise ValueError("Time must be >= 0.")
                if iid is None:
                    self.profile_tree.insert("", "end", values=(t, temp))
                else:
                    self.profile_tree.item(iid, values=(t, temp))
                self._sort_profile_tree()
                win.destroy()
            except Exception as e:
                self._dialogs.showerror("Input error", str(e), parent=win)

        self._widgets.Button(win, text="OK", command=ok).grid(row=2, column=0, columnspan=2, pady=8)

    def _sort_profile_tree(self):
        vals = self._profile_values()
        for iid in self.profile_tree.get_children():
            self.profile_tree.delete(iid)
        for row in vals:
            self.profile_tree.insert("", "end", values=row)

    def _delete_profile_point(self):
        for iid in self.profile_tree.selection():
            self.profile_tree.delete(iid)

    def _collect_inputs(self) -> SimulationInputs:
        self._update_porosity_label()
        return SimulationInputs(
            lx_mm=float(self.lx.get()),
            ly_mm=float(self.ly.get()),
            lz_mm=float(self.lz.get()),

            box_k=float(self.box_k.get()),
            box_density_kgm3=float(self.box_density.get()),
            box_cp_jkgk=float(self.box_cp.get()),
            side_thickness_mm=float(self.side_t.get()),
            bottom_thickness_mm=float(self.bottom_t.get()),
            top_thickness_mm=float(self.top_t.get()),
            top_open=self.top_open_var.get(),

            bed_density_kgm3=float(self.bed_rho.get()) * 1000.0,  # GUI g/cc -> kg/m³
            true_density_kgm3=float(self.true_rho.get()) * 1000.0,  # GUI g/cc -> kg/m³
            cp_jkgk=float(self.cp.get()),
            k_eff_wmk=float(self.k_eff.get()),
            k_solid_wmk=float(self.k_solid.get()),
            gas_k_wmk=float(self.gas_k.get()),

            k_mode=self.k_mode.get(),
            d10_um=float(self.d10.get()),
            d50_um=float(self.d50.get()),
            d90_um=float(self.d90.get()),
            solid_k_temp_exponent=float(self.solid_k_exp.get()),
            contact_blend=float(self.contact_blend.get()),
            calibration_k_scale=float(self.cal_scale.get()),
            calibration_h_wm2k=float(self.cal_h.get()),

            h_conv_wm2k=float(self.hconv.get()),
            emissivity=float(self.emissivity.get()),
            bottom_gap_mm=float(self.bottom_gap.get()),
            floor_emissivity=float(self.floor_emissivity.get()),
            bottom_view_factor=float(self.bottom_view_factor.get()),

            o2_enabled=bool(self.o2_enabled_var.get()),
            o2_ambient_percent=float(self.o2_ambient.get()),
            o2_initial_percent=float(self.o2_initial.get()),
            o2_diff_air_25_m2s=float(self.o2_d25.get()),
            o2_diff_temp_exponent=float(self.o2_temp_exp.get()),
            o2_tortuosity_mode=self.o2_tau_mode.get(),
            o2_manual_tortuosity=float(self.o2_tau.get()),
            o2_use_knudsen=bool(self.o2_knudsen_var.get()),
            o2_top_access=bool(self.o2_top_access_var.get()),
            o2_side_access=bool(self.o2_side_access_var.get()),
            o2_bottom_access=False,

            nx=int(self.nx.get()),
            ny=int(self.ny.get()),
            nz=int(self.nz.get()),
            max_dt_s=float(self.max_dt.get()),
            record_interval_s=float(self.record_interval.get()),
            snapshot_interval_s=float(self.snapshot_interval.get()) * 60.0,
            initial_temp_c=float(self.initial_temp.get()),

            oven_profile_min_c=self._profile_values(),
        )

    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            inp = self._collect_inputs()
            validate_inputs(inp)
        except Exception as e:
            self._dialogs.showerror("Input error", str(e))
            return

        # Persist exactly the inputs used for this run.
        self._save_settings(silent=True)

        self.stop_event.clear()
        self.run_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress["value"] = 0
        self.status.config(text="Running...")

        def progress(frac):
            self.msg_queue.put(("progress", frac))

        def work():
            try:
                res = simulate(inp, progress_cb=progress, stop_event=self.stop_event)
                self.msg_queue.put(("done", res))
            except Exception as e:
                self.msg_queue.put(("error", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.status.config(text="Stopping...")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "progress":
                    self.progress["value"] = float(payload) * 100.0
                elif kind == "cal_progress":
                    n, rmse, scale, hfit = payload
                    # No artificial percentage because least_squares may terminate early.
                    self.progress["value"] = min(95.0, n * 3.0)
                    self.status.config(
                        text=(
                            f"Calibration eval {n}: RMSE={rmse:.3f}°C, "
                            f"k-scale={scale:.3f}, h={hfit:.2f}"
                        )
                    )
                elif kind == "cal_done":
                    fit = payload
                    self.cal_scale.set(f"{fit['k_scale']:.6f}")
                    self.cal_h.set(f"{fit['h_conv']:.6f}")
                    self.k_mode.set("Calibrated")
                    self._save_settings(silent=True)
                    self.progress["value"] = 100
                    self.status.config(
                        text=f"Calibration completed: RMSE={fit['rmse_c']:.3f} °C"
                    )
                    self.cal_status.config(
                        text=(
                            f"Fit: k scale={fit['k_scale']:.4f}, "
                            f"h={fit['h_conv']:.3f} W/m²K, "
                            f"RMSE={fit['rmse_c']:.3f}°C, nfev={fit['nfev']}"
                        )
                    )
                    self.run_btn.configure(state=tk.NORMAL)
                    self.fit_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self._dialogs.showinfo(
                        "Calibration complete",
                        (
                            f"k_eff scale = {fit['k_scale']:.5f}\n"
                            f"h = {fit['h_conv']:.4f} W/m²K\n"
                            f"Center-temperature RMSE = {fit['rmse_c']:.4f} °C\n\n"
                            "Mode has been switched to Calibrated. "
                            "Press Run simulation to generate the final 3D result."
                        )
                    )
                elif kind == "done":
                    self.result = payload
                    self.progress["value"] = 100
                    self.status.config(text="Completed")
                    self.run_btn.configure(state=tk.NORMAL)
                    self.fit_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self._plot_result(payload)
                    self._show_summary(payload)
                    self._initialize_3d_field(payload)
                    self._plot_o2_result(payload)
                    self._show_o2_summary(payload)
                    self._initialize_o2_3d_field(payload)
                elif kind == "error":
                    self.status.config(text="Stopped / error")
                    self.run_btn.configure(state=tk.NORMAL)
                    self.fit_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    if "stopped by user" not in payload.lower() and "calibration stopped" not in payload.lower():
                        self._dialogs.showerror("Simulation error", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _plot_result(self, r: SimulationResult):
        self.ax.clear()
        h = r.time_min / 60.0

        line_oven, = self.ax.plot(h, r.oven_c, label=self._tr("Oven air"))
        line_surface, = self.ax.plot(h, r.surface_c, label=self._tr("Powder surface avg."))
        line_avg, = self.ax.plot(h, r.avg_c, label=self._tr("Powder volume avg."))
        line_center, = self.ax.plot(h, r.center_c, label=self._tr("Powder center"))
        line_side_wall, = self.ax.plot(
            h, r.side_wall_avg_c, "--", label=self._tr("Side-wall avg.")
        )
        line_bottom_plate, = self.ax.plot(
            h, r.bottom_plate_avg_c, "--", label=self._tr("Bottom-plate avg.")
        )
        self.hover_lines = [
            line_oven, line_surface, line_avg, line_center,
            line_side_wall, line_bottom_plate,
        ]

        self.ax.fill_between(h, r.min_c, r.max_c, alpha=0.12, label=self._tr("Bed min-max"))

        if self.tc_time_min is not None and self.tc_center_c is not None:
            self.ax.scatter(
                self.tc_time_min / 60.0,
                self.tc_center_c,
                marker="x",
                s=24,
                label=self._tr("Measured center TC")
            )

        self.ax.set_xlabel(self._tr("Elapsed time [h]"))
        self.ax.set_ylabel(self._tr("Temperature [°C]"))
        self.ax.grid(True, alpha=0.25)
        self.ax.legend()

        # Re-create the annotation after ax.clear().
        self.hover_annotation = self.ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="0.45", alpha=0.95),
            arrowprops=dict(arrowstyle="->", color="0.35"),
            fontsize=9,
        )
        self.hover_annotation.set_visible(False)

        self._format_plot_text(self.fig)
        self.fig.tight_layout()
        self._draw_canvas(self.canvas_plot)

    def _hide_hover_annotation(self):
        if self.hover_annotation is not None and self.hover_annotation.get_visible():
            self.hover_annotation.set_visible(False)
            self._draw_canvas(self.canvas_plot)

    def _on_plot_leave(self, _event):
        self._hide_hover_annotation()

    def _on_plot_hover(self, event):
        """Show interpolated x/y values on the nearest plotted curve."""
        if (
            self.result is None
            or event.inaxes is not self.ax
            or event.xdata is None
            or event.ydata is None
            or not self.hover_lines
            or self.hover_annotation is None
        ):
            self._hide_hover_annotation()
            return

        x_cursor = float(event.xdata)
        best = None

        for line in self.hover_lines:
            x = np.asarray(line.get_xdata(), dtype=float)
            y = np.asarray(line.get_ydata(), dtype=float)
            if x.size < 2 or x_cursor < x[0] or x_cursor > x[-1]:
                continue

            # Linear interpolation gives the value exactly at the cursor's
            # x-position, rather than only at stored output points.
            y_curve = float(np.interp(x_cursor, x, y))
            px, py = self.ax.transData.transform((x_cursor, y_curve))
            dist_px = math.hypot(float(event.x) - px, float(event.y) - py)

            if best is None or dist_px < best[0]:
                best = (dist_px, line, y_curve)

        # Display only when the pointer is reasonably close to a curve.
        if best is None or best[0] > 18.0:
            self._hide_hover_annotation()
            return

        _, line, y_curve = best
        self.hover_annotation.xy = (x_cursor, y_curve)
        self.hover_annotation.set_text(
            self._tr(f"{line.get_label()}\n"
            f"X: {x_cursor:.3f} h\n"
            f"Y: {y_curve:.2f} °C")
        )
        self.hover_annotation.set_visible(True)
        self._draw_canvas(self.canvas_plot)


    def _plot_o2_result(self, r: SimulationResult):
        self.o2_ax.clear()
        self.o2_hover_lines = []

        if not r.o2_enabled:
            self.o2_ax.text(
                0.5, 0.5,
                "O₂ diffusion was disabled for this run.",
                transform=self.o2_ax.transAxes,
                ha="center", va="center"
            )
            self.o2_ax.set_xlabel(self._tr("Elapsed time [h]"))
            self.o2_ax.set_ylabel(self._tr("O₂ [vol%]"))
            self.o2_ax.grid(True, alpha=0.25)
            self._format_plot_text(self.o2_fig)
            self.o2_fig.tight_layout()
            self._draw_canvas(self.o2_canvas_plot)
            return

        h = r.time_min / 60.0

        l1, = self.o2_ax.plot(
            h, r.o2_center_percent, label=self._tr("O₂ center")
        )
        l2, = self.o2_ax.plot(
            h, r.o2_top_percent, label=self._tr("O₂ top-layer avg.")
        )
        l3, = self.o2_ax.plot(
            h, r.o2_avg_percent, label=self._tr("O₂ volume avg.")
        )
        self.o2_hover_lines = [l1, l2, l3]

        try:
            ambient = float(self.o2_ambient.get())
            self.o2_ax.axhline(
                ambient, linestyle="--", linewidth=1.0,
                label=self._tr("Ambient O₂")
            )
        except Exception:
            pass

        self.o2_ax.fill_between(
            h,
            r.o2_min_percent,
            r.o2_max_percent,
            alpha=0.12,
            label=self._tr("Bed O₂ min-max")
        )

        self.o2_ax.set_xlabel(self._tr("Elapsed time [h]"))
        self.o2_ax.set_ylabel(self._tr("O₂ [vol%]"))
        self.o2_ax.grid(True, alpha=0.25)
        self.o2_ax.legend()

        self.o2_hover_annotation = self.o2_ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(
                boxstyle="round,pad=0.35",
                fc="white", ec="0.45", alpha=0.95
            ),
            arrowprops=dict(arrowstyle="->", color="0.35"),
            fontsize=9,
        )
        self.o2_hover_annotation.set_visible(False)

        self._format_plot_text(self.o2_fig)
        self.o2_fig.tight_layout()
        self._draw_canvas(self.o2_canvas_plot)

    def _hide_o2_hover_annotation(self):
        if (
            self.o2_hover_annotation is not None
            and self.o2_hover_annotation.get_visible()
        ):
            self.o2_hover_annotation.set_visible(False)
            self._draw_canvas(self.o2_canvas_plot)

    def _on_o2_plot_leave(self, _event):
        self._hide_o2_hover_annotation()

    def _on_o2_plot_hover(self, event):
        if (
            self.result is None
            or event.inaxes is not self.o2_ax
            or event.xdata is None
            or event.ydata is None
            or not self.o2_hover_lines
            or self.o2_hover_annotation is None
        ):
            self._hide_o2_hover_annotation()
            return

        x_cursor = float(event.xdata)
        best = None

        for line in self.o2_hover_lines:
            x = np.asarray(line.get_xdata(), dtype=float)
            y = np.asarray(line.get_ydata(), dtype=float)
            if (
                x.size < 2
                or x_cursor < x[0]
                or x_cursor > x[-1]
            ):
                continue

            y_curve = float(np.interp(x_cursor, x, y))
            px, py = self.o2_ax.transData.transform(
                (x_cursor, y_curve)
            )
            dist_px = math.hypot(
                float(event.x) - px, float(event.y) - py
            )

            if best is None or dist_px < best[0]:
                best = (dist_px, line, y_curve)

        if best is None or best[0] > 18.0:
            self._hide_o2_hover_annotation()
            return

        _, line, y_curve = best
        self.o2_hover_annotation.xy = (
            x_cursor, y_curve
        )
        self.o2_hover_annotation.set_text(
            self._tr(f"{line.get_label()}\n"
            f"X: {x_cursor:.3f} h\n"
            f"Y: {y_curve:.3f} vol%")
        )
        self.o2_hover_annotation.set_visible(True)
        self._draw_canvas(self.o2_canvas_plot)

    def _show_o2_summary(self, r: SimulationResult):
        lines = ["=== O₂ diffusion-only summary ==="]

        if not r.o2_enabled:
            lines.append("O₂ diffusion was disabled for this run.")
        else:
            ambient = float(self.o2_ambient.get())
            initial = float(self.o2_initial.get())

            lines += [
                f"Ambient O₂: {ambient:.4f} vol%",
                f"Initial pore O₂: {initial:.4f} vol%",
                f"Final center O₂: {r.o2_center_percent[-1]:.4f} vol%",
                f"Final volume-average O₂: {r.o2_avg_percent[-1]:.4f} vol%",
                f"Center D_eff at start: {r.o2_deff_center_m2s[0]:.4e} m²/s",
                f"Center D_eff at end: {r.o2_deff_center_m2s[-1]:.4e} m²/s",
                f"Center cumulative O₂ dose: {r.o2_center_dose_air_h[-1]:.4f} air-equivalent h",
            ]

            if ambient > initial + 1e-12:
                lines += ["", "Center ingress times:"]
                for frac in (0.50, 0.90, 0.95, 0.99):
                    tm = _oxygen_reach_time(
                        r.time_min,
                        r.o2_center_percent,
                        ambient,
                        frac
                    )
                    if tm is None:
                        lines.append(
                            f"  {frac*100:.0f}% of ambient: not reached"
                        )
                    else:
                        lines.append(
                            f"  {frac*100:.0f}% of ambient: "
                            f"{tm:.2f} min ({tm/60.0:.3f} h)"
                        )

            if abs(initial - ambient) < 1e-12:
                lines += [
                    "",
                    "Initial O₂ = ambient O₂.",
                    "Because this mode has no O₂-consuming reaction, "
                    "a concentration gradient cannot develop.",
                    "Set Initial pore O₂ below ambient (e.g. 0%) only when "
                    "you want to study pure ingress from an O₂-depleted initial state."
                ]

            lines += [
                "",
                "Model limits:",
                "- diffusion only; O₂ consumption = 0",
                "- no PVP oxidation/crosslinking kinetics",
                "- no pressure-driven gas flow or natural convection through the bed",
                "- atmospheric-pressure gas-diffusion assumption",
                "- PSD pore size is an estimate used mainly for Knudsen correction",
            ]

        self.o2_summary.configure(state=tk.NORMAL)
        self.o2_summary.delete("1.0", tk.END)
        self.o2_summary.insert("1.0", "\n".join(lines))
        self.o2_summary.configure(state=tk.DISABLED)

    def _initialize_o2_3d_field(self, r: SimulationResult):
        self._stop_o2_3d_play()

        nframes = int(len(r.o2_snapshots_percent))
        if not r.o2_enabled or nframes == 0:
            self.o2_field_time_label.config(
                text="No O₂ snapshots available for this run."
            )
            self.o2_field_prev_btn.configure(state=tk.DISABLED)
            self.o2_field_play_btn.configure(state=tk.DISABLED)
            self.o2_field_next_btn.configure(state=tk.DISABLED)
            return

        self.o2_field_slider.configure(
            from_=0, to=max(nframes - 1, 0)
        )
        self.o2_field_slider_var.set(0)

        state = tk.NORMAL if nframes > 1 else tk.DISABLED
        self.o2_field_prev_btn.configure(state=state)
        self.o2_field_play_btn.configure(state=state)
        self.o2_field_next_btn.configure(state=state)

        try:
            ambient = float(self.o2_ambient.get())
            initial = float(self.o2_initial.get())
        except Exception:
            ambient = float(np.nanmax(r.o2_snapshots_percent))
            initial = float(np.nanmin(r.o2_snapshots_percent))

        vmax = max(
            ambient,
            initial,
            float(np.nanmax(r.o2_snapshots_percent)),
            1e-6
        )
        vmin = min(
            0.0,
            initial,
            float(np.nanmin(r.o2_snapshots_percent))
        )

        if abs(vmax - vmin) < 1e-12:
            vmax = vmin + 1.0

        self.o2_field_norm = matplotlib.colors.Normalize(
            vmin=vmin, vmax=vmax
        )
        self.o2_field_cmap = matplotlib.colormaps.get_cmap(
            matplotlib.rcParams["image.cmap"]
        )

        nx, ny, nz = r.grid_shape
        total_points = nx * ny * nz
        target_points = 9000
        self.o2_field_display_stride = max(
            1,
            int(math.ceil(
                (total_points / target_points) ** (1.0 / 3.0)
            ))
        )

        if self.o2_field_colorbar is not None:
            try:
                self.o2_field_colorbar.remove()
            except Exception:
                pass
            self.o2_field_colorbar = None

        sm = matplotlib.cm.ScalarMappable(
            norm=self.o2_field_norm,
            cmap=self.o2_field_cmap
        )
        sm.set_array([])
        self.o2_field_colorbar = self.o2_field_fig.colorbar(
            sm,
            ax=self.o2_field_ax,
            shrink=0.72,
            pad=0.08
        )
        self.o2_field_colorbar.set_label(self._tr("O₂ [vol%]"))

        self._draw_o2_3d_frame(0)

    def _on_o2_3d_slider(self, value):
        if (
            self.result is None
            or len(self.result.o2_snapshots_percent) == 0
        ):
            return
        idx = int(round(float(value)))
        idx = max(
            0,
            min(
                idx,
                len(self.result.o2_snapshots_percent) - 1
            )
        )
        self._draw_o2_3d_frame(idx)

    def _step_o2_3d_frame(self, direction):
        if (
            self.result is None
            or len(self.result.o2_snapshots_percent) == 0
        ):
            return
        idx = (
            int(round(self.o2_field_slider_var.get()))
            + int(direction)
        )
        idx = max(
            0,
            min(
                idx,
                len(self.result.o2_snapshots_percent) - 1
            )
        )
        self.o2_field_slider_var.set(idx)
        self._draw_o2_3d_frame(idx)

    def _toggle_o2_3d_play(self):
        if self.o2_field_playing:
            self._stop_o2_3d_play()
            return

        if (
            self.result is None
            or len(self.result.o2_snapshots_percent) < 2
        ):
            return

        self.o2_field_playing = True
        self.o2_field_play_btn.configure(text="Pause")
        self._advance_o2_3d_animation()

    def _stop_o2_3d_play(self):
        self.o2_field_playing = False
        try:
            self.o2_field_play_btn.configure(text="Play")
        except Exception:
            pass

        if self.o2_field_after_id is not None:
            try:
                self.after_cancel(self.o2_field_after_id)
            except Exception:
                pass
            self.o2_field_after_id = None

    def _advance_o2_3d_animation(self):
        if (
            not self.o2_field_playing
            or self.result is None
        ):
            return

        n = len(self.result.o2_snapshots_percent)
        if n < 2:
            self._stop_o2_3d_play()
            return

        idx = int(
            round(self.o2_field_slider_var.get())
        ) + 1
        if idx >= n:
            idx = 0

        self.o2_field_slider_var.set(idx)
        self._draw_o2_3d_frame(idx)
        self.o2_field_after_id = self.after(
            180, self._advance_o2_3d_animation
        )

    def _draw_o2_3d_frame(self, idx):
        r = self.result
        if (
            r is None
            or len(r.o2_snapshots_percent) == 0
        ):
            return

        idx = max(
            0,
            min(
                int(idx),
                len(r.o2_snapshots_percent) - 1
            )
        )
        Cfull = r.o2_snapshots_percent[idx]
        stride = max(
            1, int(self.o2_field_display_stride)
        )
        C = Cfull[
            ::stride, ::stride, ::stride
        ]

        nx, ny, nz = r.grid_shape
        lx, ly, lz = r.geometry_mm

        x_full = (
            np.arange(nx, dtype=float) + 0.5
        ) * (lx / nx)
        y_full = (
            np.arange(ny, dtype=float) + 0.5
        ) * (ly / ny)
        z_full = (
            np.arange(nz, dtype=float) + 0.5
        ) * (lz / nz)

        x = x_full[::stride]
        y = y_full[::stride]
        z = z_full[::stride]

        Z, Y, X = np.meshgrid(
            z, y, x, indexing="ij"
        )

        elev = getattr(
            self.o2_field_ax, "elev", 25
        )
        azim = getattr(
            self.o2_field_ax, "azim", -60
        )

        self.o2_field_ax.clear()
        self.o2_field_ax.scatter(
            X.ravel(),
            Y.ravel(),
            Z.ravel(),
            c=C.ravel(),
            cmap=self.o2_field_cmap,
            norm=self.o2_field_norm,
            s=12,
            alpha=0.88,
            depthshade=False,
        )

        self.o2_field_ax.set_xlabel(self._tr("X [mm]"))
        self.o2_field_ax.set_ylabel(self._tr("Y [mm]"))
        self.o2_field_ax.set_zlabel(self._tr("Z [mm]"))
        self.o2_field_ax.set_xlim(0, lx)
        self.o2_field_ax.set_ylim(0, ly)
        self.o2_field_ax.set_zlim(0, lz)

        try:
            self.o2_field_ax.set_box_aspect(
                (lx, ly, lz)
            )
        except Exception:
            pass

        self.o2_field_ax.view_init(
            elev=elev, azim=azim
        )

        time_min = float(
            r.snapshot_time_min[idx]
        )
        center_o2 = center_mean(Cfull)
        cmin = float(np.min(Cfull))
        cmax = float(np.max(Cfull))

        self.o2_field_ax.set_title(
            self._tr(f"3D O₂ diffusion field — "
            f"{time_min/60.0:.3f} h")
        )

        shown = C.size
        full = Cfull.size
        display_note = (
            f"display {shown:,}/{full:,} cells"
            if shown != full
            else f"{full:,} cells"
        )

        self.o2_field_time_label.config(
            text=(
                f"{idx+1}/{len(r.o2_snapshots_percent)}  "
                f"t={time_min/60.0:.3f} h  "
                f"Center={center_o2:.3f} vol%  "
                f"Range={cmin:.3f}–{cmax:.3f} vol%  |  "
                f"{display_note}"
            )
        )

        self._draw_canvas(self.o2_field_canvas)

    def _initialize_3d_field(self, r: SimulationResult):
        """Prepare the time-dependent 3D field viewer."""
        self._stop_3d_play()

        nframes = int(len(r.snapshot_time_min))
        if nframes == 0:
            self.field_time_label.config(text="No 3D snapshots available.")
            self.field_prev_btn.configure(state=tk.DISABLED)
            self.field_play_btn.configure(state=tk.DISABLED)
            self.field_next_btn.configure(state=tk.DISABLED)
            return

        self.field_slider.configure(from_=0, to=max(nframes - 1, 0))
        self.field_slider_var.set(0)

        state = tk.NORMAL if nframes > 1 else tk.DISABLED
        self.field_prev_btn.configure(state=state)
        self.field_play_btn.configure(state=state)
        self.field_next_btn.configure(state=state)

        # One fixed scale for the complete run, so the same color means the
        # same temperature at every animation frame.
        tmin = float(np.min(r.snapshots_c))
        tmax = float(np.max(r.snapshots_c))
        if abs(tmax - tmin) < 1e-9:
            tmax = tmin + 1.0

        self.field_norm = matplotlib.colors.Normalize(vmin=tmin, vmax=tmax)
        self.field_cmap = matplotlib.colormaps.get_cmap(
            matplotlib.rcParams["image.cmap"]
        )

        nx, ny, nz = r.grid_shape
        total_points = nx * ny * nz
        target_points = 9000
        self.field_display_stride = max(
            1, int(math.ceil((total_points / target_points) ** (1.0 / 3.0)))
        )

        if self.field_colorbar is not None:
            try:
                self.field_colorbar.remove()
            except Exception:
                pass
            self.field_colorbar = None

        sm = matplotlib.cm.ScalarMappable(
            norm=self.field_norm, cmap=self.field_cmap
        )
        sm.set_array([])
        self.field_colorbar = self.field_fig.colorbar(
            sm, ax=self.field_ax, shrink=0.72, pad=0.08
        )
        self.field_colorbar.set_label(self._tr("Temperature [°C]"))

        self._draw_3d_frame(0)

    def _on_3d_slider(self, value):
        if self.result is None or len(self.result.snapshot_time_min) == 0:
            return
        idx = int(round(float(value)))
        idx = max(0, min(idx, len(self.result.snapshot_time_min) - 1))
        self._draw_3d_frame(idx)

    def _step_3d_frame(self, direction):
        if self.result is None or len(self.result.snapshot_time_min) == 0:
            return
        idx = int(round(self.field_slider_var.get())) + int(direction)
        idx = max(0, min(idx, len(self.result.snapshot_time_min) - 1))
        self.field_slider_var.set(idx)
        self._draw_3d_frame(idx)

    def _toggle_3d_play(self):
        if self.field_playing:
            self._stop_3d_play()
            return

        if self.result is None or len(self.result.snapshot_time_min) < 2:
            return

        self.field_playing = True
        self.field_play_btn.configure(text="Pause")
        self._advance_3d_animation()

    def _stop_3d_play(self):
        self.field_playing = False
        try:
            self.field_play_btn.configure(text="Play")
        except Exception:
            pass

        if self.field_after_id is not None:
            try:
                self.after_cancel(self.field_after_id)
            except Exception:
                pass
            self.field_after_id = None

    def _advance_3d_animation(self):
        if not self.field_playing or self.result is None:
            return

        n = len(self.result.snapshot_time_min)
        if n < 2:
            self._stop_3d_play()
            return

        idx = int(round(self.field_slider_var.get())) + 1
        if idx >= n:
            idx = 0

        self.field_slider_var.set(idx)
        self._draw_3d_frame(idx)
        self.field_after_id = self.after(180, self._advance_3d_animation)

    def _draw_3d_frame(self, idx):
        """Render one rotatable 3D powder-temperature field."""
        r = self.result
        if r is None or len(r.snapshot_time_min) == 0:
            return

        idx = max(0, min(int(idx), len(r.snapshot_time_min) - 1))
        Tfull = r.snapshots_c[idx]
        stride = max(1, int(self.field_display_stride))
        T = Tfull[::stride, ::stride, ::stride]

        nx, ny, nz = r.grid_shape
        lx, ly, lz = r.geometry_mm

        x_full = (np.arange(nx, dtype=float) + 0.5) * (lx / nx)
        y_full = (np.arange(ny, dtype=float) + 0.5) * (ly / ny)
        z_full = (np.arange(nz, dtype=float) + 0.5) * (lz / nz)

        x = x_full[::stride]
        y = y_full[::stride]
        z = z_full[::stride]

        Z, Y, X = np.meshgrid(z, y, x, indexing="ij")

        # Keep the viewing angle selected by the user while time changes.
        elev = getattr(self.field_ax, "elev", 25)
        azim = getattr(self.field_ax, "azim", -60)

        self.field_ax.clear()
        self.field_ax.scatter(
            X.ravel(), Y.ravel(), Z.ravel(),
            c=T.ravel(),
            cmap=self.field_cmap,
            norm=self.field_norm,
            s=12,
            alpha=0.88,
            depthshade=False,
        )

        self.field_ax.set_xlabel(self._tr("X [mm]"))
        self.field_ax.set_ylabel(self._tr("Y [mm]"))
        self.field_ax.set_zlabel(self._tr("Z [mm]"))
        self.field_ax.set_xlim(0, lx)
        self.field_ax.set_ylim(0, ly)
        self.field_ax.set_zlim(0, lz)
        try:
            self.field_ax.set_box_aspect((lx, ly, lz))
        except Exception:
            pass
        self.field_ax.view_init(elev=elev, azim=azim)

        time_min = float(r.snapshot_time_min[idx])
        oven_c = float(np.interp(time_min, r.time_min, r.oven_c))
        center_c = float(center_mean(Tfull))
        field_min = float(np.min(Tfull))
        field_max = float(np.max(Tfull))

        self.field_ax.set_title(
            self._tr(f"3D powder temperature field — {time_min/60.0:.3f} h")
        )

        shown = T.size
        full = Tfull.size
        display_note = (
            f"display {shown:,}/{full:,} cells"
            if shown != full else f"{full:,} cells"
        )

        self.field_time_label.config(
            text=(
                f"{idx+1}/{len(r.snapshot_time_min)}  "
                f"t={time_min/60.0:.3f} h  "
                f"Oven={oven_c:.1f}°C  Center={center_c:.1f}°C  "
                f"Range={field_min:.1f}–{field_max:.1f}°C  |  {display_note}"
            )
        )

        self._draw_canvas(self.field_canvas)

    def _show_summary(self, r: SimulationResult):
        center_peak = float(np.max(r.center_c))
        final_center = float(r.center_c[-1])
        lag_peak = float(np.max(r.oven_c - r.center_c))
        idx = int(np.argmax(r.oven_c - r.center_c))
        lag_time_h = float(r.time_min[idx] / 60.0)

        lines = [
            "=== Simulation summary ===",
            f"Grid: nx×ny×nz = {r.grid_shape[0]}×{r.grid_shape[1]}×{r.grid_shape[2]}",
            f"Stable Δt actually used: {r.dt_used_s:.4f} s",
            f"Powder solid fraction: {r.solid_fraction:.4f}",
            f"Powder porosity: {r.porosity:.4f}",
            f"k_eff mode: {r.k_mode}",
            f"Center k_eff at start: {r.k_center_wmk[0]:.4f} W/mK",
            f"Center k_eff at end: {r.k_center_wmk[-1]:.4f} W/mK",
            f"3D snapshots stored: {len(r.snapshot_time_min)} frames",
            f"3D snapshot interval used: {r.snapshot_interval_used_s/60.0:.3f} min",
            f"O₂ diffusion-only enabled: {r.o2_enabled}",
            "",
            f"Peak center temperature: {center_peak:.2f} °C",
            f"Final center temperature: {final_center:.2f} °C",
            f"Final side-wall average: {r.side_wall_avg_c[-1]:.2f} °C",
            f"Final bottom-plate average: {r.bottom_plate_avg_c[-1]:.2f} °C",
            f"Final under-plate air-layer h: {r.bottom_gap_h_wm2k[-1]:.3f} W/m²K",
            f"Final under-plate air-layer heat: {r.bottom_gap_air_w[-1]:.2f} W",
            f"Final under-plate radiation heat: {r.bottom_gap_radiation_w[-1]:.2f} W",
            f"Maximum oven-center ΔT: {lag_peak:.2f} °C at {lag_time_h:.2f} h",
            "",
            "Center-temperature cumulative time above thresholds:",
        ]
        for th, hr in r.threshold_hours.items():
            lines.append(f"  ≥ {th:5.1f} °C : {hr:.3f} h")

        if self.tc_time_min is not None and self.tc_center_c is not None:
            valid = (
                (self.tc_time_min >= r.time_min[0])
                & (self.tc_time_min <= r.time_min[-1])
            )
            if np.any(valid):
                pred = np.interp(
                    self.tc_time_min[valid], r.time_min, r.center_c
                )
                rmse = float(np.sqrt(np.mean(
                    (pred - self.tc_center_c[valid]) ** 2
                )))
                lines += ["", f"Center TC comparison RMSE: {rmse:.3f} °C"]

        lines += [
            "",
            "Interpretation caution:",
            "- Bottom input includes floor radiation and horizontal air-layer natural convection/conduction.",
            f"- No forced convection is applied in the {r.bottom_gap_mm:.1f}-mm under-plate gap.",
            "- Oven-floor temperature is assumed to follow the oven profile.",
            "- Side-wall metal conduction into the connected bottom plate remains active.",
            "- k_eff/contact physics and h_conv still dominate uncertainty without calibration.",
            "- PSD k_eff(T) includes temperature dependence, but it remains an engineering model.",
            "- Side/bottom metal thermal mass is resolved; a closed lid remains resistance-only.",
            "- Solvent latent heat, binder reaction heat and detailed oven flow are not resolved.",
            "- For production decisions, calibrate against at least one thermocouple run."
        ]

        self.summary.configure(state=tk.NORMAL)
        self.summary.delete("1.0", tk.END)
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state=tk.DISABLED)

    def _export_csv(self):
        if self.result is None:
            self._dialogs.showinfo("Export", "Run a simulation first.")
            return

        path = self._dialogs.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="sic_buildbox_thermal_result.csv"
        )
        if not path:
            return

        r = self.result
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([
                "time_min", "time_h", "oven_C", "surface_avg_C",
                "volume_avg_C", "center_C", "min_C", "max_C",
                "side_wall_avg_C", "bottom_plate_avg_C",
                "bottom_gap_h_Wm2K", "bottom_gap_air_W",
                "bottom_gap_radiation_W",
                "center_k_eff_WmK", "k_eff_mode",
                "o2_center_volpct", "o2_top_volpct",
                "o2_avg_volpct", "o2_min_volpct", "o2_max_volpct",
                "o2_center_Deff_m2s", "o2_center_dose_air_h"
            ])
            for i in range(len(r.time_min)):
                w.writerow([
                    r.time_min[i], r.time_min[i] / 60.0, r.oven_c[i],
                    r.surface_c[i], r.avg_c[i], r.center_c[i],
                    r.min_c[i], r.max_c[i],
                    r.side_wall_avg_c[i], r.bottom_plate_avg_c[i],
                    r.bottom_gap_h_wm2k[i], r.bottom_gap_air_w[i],
                    r.bottom_gap_radiation_w[i],
                    r.k_center_wmk[i], r.k_mode,
                    r.o2_center_percent[i],
                    r.o2_top_percent[i],
                    r.o2_avg_percent[i],
                    r.o2_min_percent[i],
                    r.o2_max_percent[i],
                    r.o2_deff_center_m2s[i],
                    r.o2_center_dose_air_h[i]
                ])
        self._dialogs.showinfo("Export", f"Saved:\n{path}")


if __name__ == "__main__":
    app = ThermalGUI()
    app.mainloop()
