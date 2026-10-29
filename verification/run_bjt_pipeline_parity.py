"""Run the six existing BJT pipeline fixtures in isolated baseline/candidate processes."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


FIXTURES = Path(__file__).resolve().parents[1] / "suite" / "배포_빌드" / "helpers" / "bjt_fixtures.py"
CASES = {"reconstruct_crop_center", "hex_line_xyz_bmp", "square_line_xy_png",
         "hex_face_xyz", "shell_only", "model_full"}
DEPENDENCIES = ("numpy", "scipy", "vtk", "Pillow", "psutil", "numba", "trimesh", "shapely", "scikit-image", "rtree")


def load_module(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load source: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def versions():
    result = {"python": sys.version}
    for name in DEPENDENCIES:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def provenance(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def worker(source, fixture, output):
    source_info, fixture_info = provenance(source), provenance(fixture)
    module = load_module("_bjt_parity_source", source)
    fixtures = load_module("_bjt_parity_fixtures", fixture)
    helper = getattr(module, "_bjt_native_edge_backend", None)
    selection = helper() if callable(helper) else None
    calls = {"sort_count": 0, "pack_grid_triangles": 0}
    if selection is not None:
        for name in calls:
            original = getattr(selection, name, None)
            if callable(original):
                def counted(*args, _original=original, _name=name, **kwargs):
                    value = _original(*args, **kwargs)
                    calls[_name] += 1
                    return value
                setattr(selection, name, counted)
    result = fixtures.run(module, output)
    result["dependencies"] = versions()
    result["source"] = source_info
    result["fixture"] = fixture_info
    wrapper = output / "bjt_mesh_native.py"
    library_dir = output / "native"
    libraries = list(library_dir.iterdir()) if library_dir.is_dir() else []
    result["native_wrapper"] = provenance(wrapper) if wrapper.is_file() else None
    result["native_library"] = provenance(libraries[0]) if len(libraries) == 1 else None
    result["backend"] = {"requested": os.environ.get("BJT_MESH_BACKEND", "auto"),
                         "helper_present": callable(helper),
                         "selected": "rust" if selection is not None else "python",
                         "native_success_calls": calls}
    for case in result["cases"]:
        if case["status"] == "passed":
            path = output / (case["case"] + ".stl")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            case["file_sha256"] = digest.hexdigest()
    (output / "parity_worker.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def compare(baseline, candidate):
    differences = []
    sets = []
    for label, result in (("baseline", baseline), ("candidate", candidate)):
        cases = result.get("cases", [])
        by_name = {case.get("case"): case for case in cases}
        if set(by_name) != CASES or len(cases) != len(CASES):
            differences.append({"side": label, "field": "case_names", "actual": sorted(str(x) for x in by_name)})
        sets.append(by_name)
    for name in sorted(CASES):
        left, right = (side.get(name) for side in sets)
        if left is None or right is None:
            continue
        for label, case in (("baseline", left), ("candidate", right)):
            if case.get("status") != "passed":
                differences.append({"case": name, "side": label, "field": "status", "actual": case.get("status"), "error": case.get("error")})
            if case.get("status") == "passed":
                if not case.get("progress_ends_at_100") or case.get("report_status") != "completed":
                    differences.append({"case": name, "side": label, "field": "completion", "progress_ends_at_100": case.get("progress_ends_at_100"), "report_status": case.get("report_status")})
                if case.get("file_sha256") != case.get("stats", {}).get("sha256"):
                    differences.append({"case": name, "side": label, "field": "file_sha256_vs_stats"})
        if left.get("status") == right.get("status") == "passed":
            for field in ("stats", "images", "file_sha256", "previews_count", "progress_ends_at_100", "report_status"):
                if left.get(field) != right.get(field):
                    differences.append({"case": name, "field": field, "baseline": left.get(field), "candidate": right.get(field)})
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--candidate-source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--native-wrapper", type=Path, help="optional wrapper to snapshot beside each source")
    parser.add_argument("--native-library", type=Path, help="optional host native library to snapshot with wrapper")
    parser.add_argument("--worker-source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-fixture", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if args.worker_source:
        if not args.worker_fixture:
            parser.error("--worker-fixture is required for worker")
        worker(args.worker_source.resolve(), args.worker_fixture.resolve(), output)
        return 0
    if not args.baseline_source or not args.candidate_source:
        parser.error("--baseline-source and --candidate-source are required")
    for source in (args.baseline_source, args.candidate_source):
        if not source.is_file():
            parser.error(f"source does not exist: {source}")
    if args.native_library and not args.native_wrapper:
        parser.error("--native-library requires --native-wrapper")
    for path in (args.native_wrapper, args.native_library):
        if path is not None and not path.is_file():
            parser.error(f"native file does not exist: {path}")
    platform_name = {"win32": "bjt_mesh_native.dll", "darwin": "libbjt_mesh_native.dylib"}.get(
        sys.platform, "libbjt_mesh_native.so" if sys.platform.startswith("linux") else None)
    if args.native_library and (platform_name is None or args.native_library.name != platform_name):
        parser.error(f"native library must have the expected host basename: {platform_name}")
    output.mkdir(parents=True, exist_ok=True)
    # Never reuse existing run directories: stale outputs could contaminate parity.
    for side in ("baseline", "candidate"):
        if (output / side).exists():
            parser.error(f"output directory already contains {side}; choose a fresh output directory")
    # Freeze both source versions and fixture before launching either process.
    fixture_bytes = FIXTURES.read_bytes()
    wrapper_bytes = args.native_wrapper.read_bytes() if args.native_wrapper else None
    library_bytes = args.native_library.read_bytes() if args.native_library else None
    for side, source in (("baseline", args.baseline_source), ("candidate", args.candidate_source)):
        work = output / side
        work.mkdir()
        (work / "source.py").write_bytes(source.read_bytes())
        (work / "fixture.py").write_bytes(fixture_bytes)
        if wrapper_bytes is not None:
            (work / "bjt_mesh_native.py").write_bytes(wrapper_bytes)
        if library_bytes is not None:
            (work / "native").mkdir()
            (work / "native" / platform_name).write_bytes(library_bytes)
    results, errors = {}, []
    for side in ("baseline", "candidate"):
        work = output / side
        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(work / "localappdata")
        env["NUMBA_CACHE_DIR"] = str(work / "numba_cache")
        (work / "localappdata").mkdir()
        (work / "numba_cache").mkdir()
        with (work / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-source", str(work / "source.py"), "--worker-fixture", str(work / "fixture.py"), "--output-dir", str(work)], env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        report = work / "parity_worker.json"
        if process.returncode or not report.is_file():
            errors.append({"side": side, "field": "worker", "returncode": process.returncode, "log": f"{side}/worker.log"})
        else:
            results[side] = json.loads(report.read_text(encoding="utf-8"))
    differences = errors + (compare(results["baseline"], results["candidate"]) if len(results) == 2 else [])
    for side, data in results.items():
        backend = data.get("backend", {})
        if (backend.get("helper_present") and str(backend.get("requested", "")).strip().lower() == "rust"
                and not backend.get("native_success_calls", {}).get("sort_count", 0)):
            differences.append({"side": side, "field": "native_success_calls", "error": "forced Rust did not complete any native sort/count call"})
    if len(results) == 2 and results["baseline"].get("fixture") != results["candidate"].get("fixture"):
        differences.append({"field": "fixture_provenance"})
    summary = {"provenance": {side: {"source": data.get("source"), "fixture": data.get("fixture"), "native_wrapper": data.get("native_wrapper"), "native_library": data.get("native_library")} for side, data in results.items()}, "backends": {side: data.get("backend") for side, data in results.items()}, "status": "passed" if not differences else "failed", "dependencies": {side: data.get("dependencies") for side, data in results.items()}, "cases": sorted(CASES), "differences": differences}
    (output / "parity_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
