#!/usr/bin/env python3
"""Compare real source kernels in fresh, alternating worker processes.

The default baseline is the source at git HEAD; use --baseline-source or
--baseline-ref to select an explicit pre-change revision. Fixture construction
and result hashing are outside operation timers. Peak RSS is the worker process
high-water mark, including Python/NumPy, source loading and fixture construction;
it is not an allocation-only or whole-GUI-pipeline memory measurement.

Example:
  python verification/benchmark_bjt_mesh_memory.py --baseline-ref HEAD \
      --triangles 2000000 --repeat 3 --output verification/bjt-memory/benchmark.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RELATIVE_SOURCE = Path("suite/sources/260914_BJT_model_to_lattice_STL_all_in_one.py")
sys.path.insert(0, str(ROOT / "suite/tests"))
from bjt_kernel_support import load_definitions, load_embedded_definitions

SCENARIOS = ("writer-contiguous", "writer-strided", "stats", "edge-sort")
SEED = 20261028


def process_memory():
    """Return current RSS and the process lifetime peak, with explicit units."""
    current = None
    try:
        import psutil
        current = int(psutil.Process().memory_info().rss)
    except ImportError:
        if sys.platform.startswith("linux"):
            current = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    try:
        import resource
        raw_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = int(raw_peak if sys.platform == "darwin" else raw_peak * 1024)
        method = "resource.ru_maxrss (process lifetime)"
    except ImportError:
        try:
            import psutil
            memory = psutil.Process().memory_info()
            peak = getattr(memory, "peak_wset", None)
            method = "psutil.peak_wset" if peak is not None else "unavailable"
        except ImportError:
            peak, method = None, "unavailable"
    return {"current_rss_bytes": current, "process_peak_rss_bytes": peak, "peak_rss_method": method}


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha(array):
    digest = hashlib.sha256()
    raw = memoryview(array).cast("B")
    for start in range(0, len(raw), 4 * 1024**2):
        digest.update(raw[start:start + 4 * 1024**2])
    return digest.hexdigest()


def load_writer(source):
    core = load_embedded_definitions(source, "_RECON_CORE_B64", ["CancelledError", "check_cancel", "BinarySTLWriter"], {
        "np": np, "Path": Path, "struct": struct, "os": os,
    })
    kernels = load_definitions(source, ["_AIO_FAST_EXACT_RECORD_DTYPE", "_aio_fast_writer_check_limit", "_aio_fast_write_triangle_soup"], {
        "np": np, "recon_core": SimpleNamespace(check_cancel=core["check_cancel"]),
    })
    return core["BinarySTLWriter"], kernels["_aio_fast_write_triangle_soup"]


def load_sort_phase(source):
    """Compile the actual verifier's key creation/sort/gather statements only."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    verifier = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_bjt_verify_edges")
    bucket = next(node for node in ast.walk(verifier) if isinstance(node, ast.For)
                  and isinstance(node.target, ast.Name) and node.target.id == "bucket")
    start = next(i for i, node in enumerate(bucket.body) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "words" for target in node.targets))
    stop = next(i for i, node in enumerate(bucket.body) if isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name) and node.target.id == "totals")
    function = ast.FunctionDef(
        name="edge_sort_phase", args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="rows")],
                                                   kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[*bucket.body[start:stop], ast.Return(value=ast.Name(id="rows", ctx=ast.Load()))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = load_definitions(source, ["_BJT_EDGE_DTYPE", "_BJT_EDGE_FIELDS"], {"np": np})
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["_BJT_EDGE_DTYPE"], namespace["edge_sort_phase"]


def generate_stl(path, triangles, chunk_size):
    """Build the stats input in bounded chunks, outside the measured operation."""
    dtype = np.dtype([("normal", "<f4", (3,)), ("a", "<f4", (3,)),
                      ("b", "<f4", (3,)), ("c", "<f4", (3,)), ("attr", "<u2")])
    rng = np.random.default_rng(SEED)
    with open(path, "wb") as stream:
        stream.write(b"BJT bounded stats benchmark".ljust(80, b" "))
        stream.write(struct.pack("<I", triangles))
        for begin in range(0, triangles, chunk_size):
            take = min(chunk_size, triangles - begin)
            vertices = rng.random((take, 3, 3), dtype=np.float64).astype(np.float32)
            records = np.zeros(take, dtype=dtype)
            records["normal"] = (0, 0, 1)
            for index, name in enumerate(("a", "b", "c")):
                records[name] = vertices[:, index]
            records.tofile(stream)
            del vertices, records


def run_worker(args):
    source = args.source.resolve()
    setup_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bjt_memory_worker_", dir=args.temp_dir) as temp:
        path = Path(temp) / "benchmark.stl"
        if args.scenario.startswith("writer-"):
            writer_class, write_soup = load_writer(source)
            triangles = np.random.default_rng(SEED).random((args.triangles, 3, 3), dtype=np.float64)
            if args.scenario == "writer-strided":
                triangles = triangles[:, ::-1, :]
            writer = writer_class(path, header="BJT triangle soup benchmark")
            setup_seconds = time.perf_counter() - setup_start
            before = process_memory()
            start = time.perf_counter()
            try:
                count = write_soup(writer, triangles, chunk_size=args.chunk_size)
                writer.close(True)
            finally:
                writer.close(False)
            elapsed = time.perf_counter() - start
            after = process_memory()
            signature = {"triangles": count, "bytes": path.stat().st_size, "sha256": file_sha(path)}
            scope = "real triangle-soup writer plus BinarySTLWriter.close(True)/fsync; float64 input already constructed"
            details = {"input_dtype": str(triangles.dtype), "input_shape": list(triangles.shape),
                       "input_strides_bytes": list(triangles.strides), "input_storage_bytes": triangles.nbytes,
                       "chunk_size": args.chunk_size}
        elif args.scenario == "stats":
            namespace = load_definitions(source, ["CancelledError", "check_cancel", "binary_stl_stats"], {
                "np": np, "hashlib": hashlib, "struct": struct,
            })
            generate_stl(path, args.triangles, args.chunk_size)
            setup_seconds = time.perf_counter() - setup_start
            before = process_memory()
            start = time.perf_counter()
            signature = namespace["binary_stl_stats"](path)
            elapsed = time.perf_counter() - start
            after = process_memory()
            signature.pop("path", None)
            scope = "real binary_stl_stats, including its file hash; generated STL fixture already on disk"
            details = {"input_stl_bytes": path.stat().st_size, "fixture_generation_chunk": args.chunk_size}
        else:
            dtype, sort_phase = load_sort_phase(source)
            rows = np.empty(args.triangles * 3, dtype=dtype)
            rng = np.random.default_rng(SEED)
            for name in (f"p{i}" for i in range(6)):
                rows[name] = rng.integers(0, 2**32, len(rows), dtype=np.uint32)
            rows["direction"] = 1
            rows[1::2]["direction"] = -1
            rows["contact"] = 0
            rows[1::7] = rows[::7][:len(rows[1::7])]
            setup_seconds = time.perf_counter() - setup_start
            before = process_memory()
            start = time.perf_counter()
            rows = sort_phase(rows)
            elapsed = time.perf_counter() - start
            after = process_memory()
            signature = {"edge_rows": len(rows), "sorted_rows_sha256": array_sha(rows)}
            scope = "actual verifier key creation, NumPy lexsort and row gather only; excludes edge extraction/bucketing/counting/Numba"
            details = {"edge_rows": len(rows), "row_bytes": dtype.itemsize, "input_row_storage_bytes": rows.nbytes,
                       "duplicates": "every seventh row has a duplicate; direction/contact metadata retained"}
        result = {
            "scenario": args.scenario, "source_sha256": file_sha(source), "triangles": args.triangles,
            "setup_seconds": setup_seconds, "elapsed_seconds": elapsed,
            "rss_before_operation_bytes": before["current_rss_bytes"],
            "process_peak_rss_bytes": after["process_peak_rss_bytes"],
            "peak_rss_method": after["peak_rss_method"], "scope": scope, "details": details,
            "signature": signature,
        }
    print(json.dumps(result, sort_keys=True, allow_nan=False))


def summarize(results):
    summary = {}
    for scenario in dict.fromkeys(result["scenario"] for result in results):
        group = [result for result in results if result["scenario"] == scenario]
        signatures = [result["signature"] for result in group]
        if any(signature != signatures[0] for signature in signatures):
            raise RuntimeError(f"Baseline/candidate output mismatch in {scenario}")
        scenario_summary = {"parity_passed": True, "signature": signatures[0]}
        for label in ("baseline", "candidate"):
            samples = [result for result in group if result["label"] == label]
            peaks = [result["process_peak_rss_bytes"] for result in samples if result["process_peak_rss_bytes"] is not None]
            scenario_summary[label] = {
                "elapsed_seconds_median": statistics.median(result["elapsed_seconds"] for result in samples),
                "process_peak_rss_bytes_median": statistics.median(peaks) if peaks else None,
                "samples": len(samples),
            }
        summary[scenario] = scenario_summary
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=ROOT / RELATIVE_SOURCE)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--baseline-ref", default="HEAD", help="git revision used only when --baseline-source is absent")
    parser.add_argument("--triangles", type=int, default=2_000_000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=120_000)
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    parser.add_argument("--output", type=Path, default=ROOT / "verification/bjt-memory/benchmark.json")
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=SCENARIOS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0 < args.triangles <= 0xFFFFFFFF or args.repeat < 1 or args.chunk_size < 1:
        parser.error("triangles must be 1..uint32 max; repeat and chunk-size must be positive")
    if args.worker:
        if not args.scenario:
            parser.error("--scenario is required for --worker")
        run_worker(args)
        return
    with tempfile.TemporaryDirectory(prefix="bjt_memory_baseline_", dir=args.temp_dir) as temp:
        baseline = args.baseline_source
        if baseline is None:
            baseline = Path(temp) / "baseline.py"
            baseline.write_bytes(subprocess.check_output(["git", "show", f"{args.baseline_ref}:{RELATIVE_SOURCE.as_posix()}"], cwd=ROOT))
        results = []
        for scenario in args.scenarios:
            for repetition in range(1, args.repeat + 1):
                labels = ("baseline", "candidate") if repetition % 2 else ("candidate", "baseline")
                for label in labels:
                    source = baseline if label == "baseline" else args.source
                    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", str(source.resolve()),
                               "--scenario", scenario, "--triangles", str(args.triangles), "--chunk-size", str(args.chunk_size)]
                    if args.temp_dir:
                        command += ["--temp-dir", str(args.temp_dir.resolve())]
                    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                    if completed.returncode:
                        raise RuntimeError(f"Worker failed ({label}/{scenario}):\n{completed.stdout}\n{completed.stderr}")
                    result = json.loads(completed.stdout)
                    result.update(label=label, repetition=repetition, sequence=len(results) + 1)
                    results.append(result)
                    peak = result["process_peak_rss_bytes"]
                    print(f"{scenario} {repetition}/{args.repeat} {label}: {result['elapsed_seconds']:.3f}s, "
                          f"process peak {peak / 1024**2:.1f} MiB" if peak is not None else f"{scenario}: peak unavailable", file=sys.stderr)
        evidence = {
            "environment": {"python": sys.version, "numpy": np.__version__, "platform": platform.platform(),
                            "executable": sys.executable},
            "configuration": {"triangles": args.triangles, "repeat": args.repeat, "chunk_size": args.chunk_size,
                              "baseline_source": str(args.baseline_source.resolve()) if args.baseline_source else None,
                              "baseline_ref": args.baseline_ref if args.baseline_source is None else None,
                              "candidate_source": str(args.source.resolve()), "seed": SEED},
            "measurement": "Fresh sequential worker processes, alternating order per repetition. Timers exclude fixture construction and output verification. RSS is the process lifetime high-water mark, including setup, not scratch allocation or whole-app peak.",
            "summary": summarize(results), "runs": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps(evidence["summary"], indent=2, sort_keys=True))
        print(f"Evidence: {args.output}")


if __name__ == "__main__":
    main()
