"""BJT logs format measurements while the geometry keeps its exact inputs."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import trimesh

from bjt_kernel_support import load_definitions, load_embedded_definitions
from display_numbers import format_display_value, format_number


SOURCE = Path(__file__).resolve().parents[1] / "sources" / "260914_BJT_model_to_lattice_STL_all_in_one.py"


class Log:
    def __init__(self):
        self.messages = []

    def write(self, message=""):
        self.messages.append(message)

    def progress(self, _percent, _message=""):
        pass


def concise(text):
    assert re.search(r"\d+\.\d{3,}", text) is None, text


def test_model_dimension_log_does_not_round_mesh_vertices(tmp_path):
    kernel = load_definitions(SOURCE, ["MODEL_EXTS", "UNIT_SCALE", "load_mesh_mm"], {
        "np": np, "trimesh": trimesh, "format_number": format_number,
        "format_display_value": format_display_value,
    })
    path = tmp_path / "model.stl"
    path.touch()
    original = trimesh.creation.box(extents=[1.23456789, 2.34567891, 3.45678912])
    expected = original.vertices.copy()
    log = Log()
    with patch.object(trimesh, "load", return_value=original):
        result = kernel["load_mesh_mm"](path, "mm", log)
    np.testing.assert_array_equal(result.vertices, expected)
    np.testing.assert_array_equal(original.vertices, expected)
    size_line = next(message for message in log.messages if message.startswith("크기:"))
    assert size_line == "크기: X=1.23, Y=2.35, Z=3.46 mm"
    assert not np.array_equal(result.vertices, np.round(result.vertices, 2))


def test_embedded_reconstruction_logs_tiny_pitches_without_rounding_grid(tmp_path):
    seen = {}
    bbox = SimpleNamespace(full_w=100, full_h=100, width=10, height=20,
                           xmin=3, ymin=4, layers=3)

    def reconstruct(params, _items, _bbox, grid, *_args):
        seen.update(params=params, grid=grid)
        return 12, 6

    kernel = load_embedded_definitions(SOURCE, "_RECON_CORE_B64", [
        "ModelingGrid", "dpi_to_mm_per_px", "build_modeling_grid", "validate_params",
        "reconstruct_streaming", "format_bytes", "format_seconds",
    ], {
        "dataclass": dataclass, "np": np, "Path": Path,
        "tempfile": tempfile, "time": time, "format_number": format_number,
        "PreparedInput": lambda *_args: nullcontext(SimpleNamespace(images=[Path("slice.bmp")])),
        "build_layer_items": lambda *_args: [Path("slice.bmp")],
        "resolve_binary_settings": lambda *_args: SimpleNamespace(threshold=128, print_is_white=True),
        "scan_bbox_cached": lambda *_args: (bbox, None),
        "reconstruct_original_surface": reconstruct,
    })
    params = SimpleNamespace(
        input_path=tmp_path, output_stl=tmp_path / "output.stl", recursive=False,
        respect_number_gaps=False, dpi_x=25_400_000.123456789,
        dpi_y=12_700_000.987654321, layer_thickness_mm=0.000123456789,
        threshold=128, crop_margin_px=0, workers=1, center_xy=False,
        surface_mode="원본 충실형", mesh_mode="고속 병합",
    )
    before = vars(params).copy()
    log = Log()
    kernel["reconstruct_streaming"](params, log, threading.Event())
    assert seen["params"] is params
    assert vars(params) == before
    grid = seen["grid"]
    assert grid.x_pitch_mm == 25.4 / params.dpi_x
    assert grid.y_pitch_mm == 25.4 / params.dpi_y
    assert grid.x_offset_mm == bbox.xmin * grid.x_pitch_mm
    assert grid.x_pitch_mm != round(grid.x_pitch_mm, 2)
    for message in log.messages:
        concise(message)
    assert any("1.00e-06" in message and "2.00e-06" in message for message in log.messages)
    assert any("1.23e-04" in message for message in log.messages)


def test_embedded_lattice_warning_preserves_nonzero_subpixel_values():
    kernel = load_embedded_definitions(SOURCE, "_LATTICE_CORE_B64", [
        "dpi_to_um_per_px", "dpi_to_mm_per_px", "pixel_pitch_xy",
        "physical_px_counts", "validate_params",
    ], {"format_number": format_number})
    params = SimpleNamespace(dpi_x=400.123456789, dpi_y=401.234567891,
                             shell_mm=0.00000123456789, lattice_width_mm=0.00000234567891,
                             spacing_mm=0.0000123456789, layer_thickness_mm=0.00000123456789,
                             threshold=128, shell_method="고속 정밀 EDT", axes="xy")
    before = vars(params).copy()
    warnings = kernel["validate_params"](params)
    assert len(warnings) == 3
    assert all("e-" in warning for warning in warnings)
    assert all("0.00px" not in warning for warning in warnings)
    for warning in warnings:
        concise(warning)
    assert vars(params) == before
