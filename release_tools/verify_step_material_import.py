"""Verify a privately supplied STEP through the integrated import/export path.

CAD and generated meshes stay in the caller's output directory. Only the
geometry-free JSON report is suitable for repository verification evidence.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'suite'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from packing_service import load_inputs, calculate, save_snapshot
    from powdernest.geometry import load_model, _step_material_shape
    from powdernest.types import Settings
    from powdernest.chitubox import read_chitubox
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    import numpy as np
    args.output.mkdir(parents=True, exist_ok=True)
    before = sha256(args.input.read_bytes()).hexdigest()
    rows = [dict(id='private-step', path=str(args.input.resolve()), unit='mm',
                 quantity=2, rotations=list(range(24)))]
    started = time.perf_counter()
    models, errors, cancelled = load_inputs(rows, threading.Event())
    assert not errors and not cancelled, errors
    model = models['private-step']
    assert model.diagnostics['step_material_union_applied']
    material, unused = _step_material_shape(model.original_shape)
    properties = GProp_GProps(); BRepGProp.VolumeProperties_s(material, properties)
    report = dict(passed=False, source_sha256=before, source_bytes=args.input.stat().st_size,
                  platform=platform.platform(), python=sys.version,
                  import_seconds=time.perf_counter()-started,
                  source_brep_valid=BRepCheck_Analyzer(model.original_shape).IsValid(),
                  union_cad_volume_mm3=properties.Mass(), mesh_volume_mm3=model.mesh.volume,
                  bounds_mm=model.mesh.bounds.tolist(), diagnostics=model.diagnostics)
    # Full strict checks, not an inspection-only preview or an AABB placement.
    settings = Settings(size=(50, 50, 50), gap=2, wall_gap=1,
                        iterations=0, time_limit=10, xy_balance='off')
    snapshot = calculate(models, rows, settings, threading.Event())
    assert snapshot.result.validation['valid'] and snapshot.result.validation['complete']
    assert len(snapshot.result.placements) == 2
    save_snapshot(snapshot, args.output / 'standard')
    save_snapshot(snapshot, args.output / 'layout.chitubox', 'chitubox')
    encoded = json.loads((args.output / 'layout.chitubox.json').read_text(encoding='utf-8'))
    assert encoded['encoded_geometry_validation']['valid']
    assert len(read_chitubox(args.output / 'layout.chitubox')) == 2
    # Re-import the actual STL serialization under the strict raw-mesh checks.
    reloaded = load_model(args.output / 'standard/layout.stl', use_cache=False)
    assert reloaded.mesh.is_watertight and reloaded.mesh.is_winding_consistent
    np.testing.assert_allclose(reloaded.mesh.volume, model.mesh.volume * 2, rtol=1e-6)
    assert sha256(args.input.read_bytes()).hexdigest() == before
    report.update(passed=True, source_unchanged=True, placed=2,
                  height_mm=snapshot.result.height, validation=snapshot.result.validation,
                  stl_roundtrip=True, chitubox_roundtrip=True,
                  encoded_geometry_validation=encoded['encoded_geometry_validation'],
                  maximum_vertex_displacement_mm=encoded['maximum_vertex_displacement_mm'],
                  total_seconds=time.perf_counter()-started)
    (args.output / 'verification.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('passed', 'source_sha256', 'placed',
          'bounds_mm', 'mesh_volume_mm3', 'total_seconds')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
