#!/usr/bin/env python3
"""Compare optional Rust edges against the optimized Python/NumPy/Numba source.

Fixtures are streamed before launching fresh worker processes. Both real Numba
kernels are warmed before operation timing. The phase includes sorting AND
counting; the full verifier includes STL reading, edge extraction, disk buckets,
sorting/counting and cleanup. Native row order among equal keys is irrelevant.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import psutil
from numba import njit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "suite/tests"))
from bjt_kernel_support import load_definitions

NAMES = ["CancelledError", "check_cancel", "_AIO_FAST_EXACT_RECORD_DTYPE", "_BJT_EDGE_DTYPE",
         "_BJT_EDGE_FIELDS", "_bjt_edges_and_buckets", "_bjt_edge_counts", "_bjt_verify_edges"]
METRICS = ("boundary_edges", "nonmanifold_edges", "inconsistent_winding_edges", "unique_edges", "voxel_contact_edges")
CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], np.float32)
FACES = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]])
RECORD = np.dtype([("normal", "<f4", (3,)), ("a", "<f4", (3,)), ("b", "<f4", (3,)),
                   ("c", "<f4", (3,)), ("attr", "<u2")])


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def fixture(path, cubes, geometry):
    with path.open("wb") as stream:
        stream.write(f"BJT tiled {geometry} cubes".encode().ljust(80, b" "))
        stream.write(struct.pack("<I", cubes * 12))
        for begin in range(0, cubes, 4096):
            ids = np.arange(begin, min(cubes, begin + 4096))
            groups = ids // 2 if geometry == "edge-touching" else ids
            offsets = np.column_stack(((groups % 128) * 3, ((groups // 128) % 128) * 3,
                                       (groups // 16384) * 3)).astype(np.float32)
            if geometry == "edge-touching":
                offsets[:, :2] += (ids % 2)[:, None]
            triangles = (CORNERS[FACES][None, :, :, :] + offsets[:, None, None, :]).reshape(-1, 3, 3)
            records = np.zeros(len(triangles), RECORD)
            for index, name in enumerate(("a", "b", "c")):
                records[name] = triangles[:, index]
            records.tofile(stream)
    return {"cubes": cubes, "triangles": cubes * 12, "bytes": path.stat().st_size, "sha256": file_sha(path)}


def kernels(source):
    text = source.read_text(encoding="utf-8")
    names = NAMES + (["_bjt_native_edge_backend"] if "def _bjt_native_edge_backend(" in text else [])
    namespace = load_definitions(source, names, {"np": np, "Path": Path, "tempfile": tempfile,
        "shutil": shutil, "format_bytes": str, "__file__": str(source)})
    for name in ("_bjt_edges_and_buckets", "_bjt_edge_counts"):
        namespace[name] = njit(cache=False, nogil=True)(namespace[name])
    # Compile the same record signatures as actual work, before timing/sampling.
    bits = np.ascontiguousarray(CORNERS[FACES]).view("<u4")
    rows = np.empty(len(bits) * 3, namespace["_BJT_EDGE_DTYPE"])
    namespace["_bjt_edges_and_buckets"](bits, rows, np.empty(len(rows), np.uint16), 3)
    words = rows.view("<u8").reshape(-1, 4)
    namespace["_bjt_edge_counts"](rows[np.lexsort((words[:, 2], words[:, 1], words[:, 0]))])
    return namespace


def read_edges(path, count, namespace):
    rows = np.empty(count * 3, namespace["_BJT_EDGE_DTYPE"])
    with path.open("rb") as stream:
        stream.seek(84)
        for begin in range(0, count, 100_000):
            take = min(count - begin, 100_000)
            records = np.fromfile(stream, namespace["_AIO_FAST_EXACT_RECORD_DTYPE"], count=take)
            triangles = np.stack((records["a"], records["b"], records["c"]), axis=1)
            triangles[triangles == 0] = 0.0
            bits = np.ascontiguousarray(triangles, dtype="<f4").view("<u4")
            namespace["_bjt_edges_and_buckets"](bits, rows[begin*3:(begin+take)*3], np.empty(take*3, np.uint16), 0)
    return rows


class OperationRSS:
    """Sample current process RSS; short unsampled spikes remain possible."""
    def __init__(self):
        self.process = psutil.Process()
        self.before = self.process.memory_info().rss
        self.peak = self.before
        self.samples = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.stop.is_set():
            self.peak = max(self.peak, self.process.memory_info().rss)
            self.samples += 1
            self.stop.wait(.002)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.peak = max(self.peak, self.process.memory_info().rss)
        self.stop.set()
        self.thread.join()


def key_sha(rows):
    digest = hashlib.sha256()
    words = rows.view("<u8").reshape(-1, 4)[:, :3]
    for begin in range(0, len(rows), 32_768):
        digest.update(np.ascontiguousarray(words[begin:begin+32_768]).data)
    return digest.hexdigest()


def worker(args):
    namespace = kernels(args.source)
    native = None
    if args.backend == "rust":
        native = namespace["_bjt_native_edge_backend"]("rust")
    if args.scope == "phase":
        rows = read_edges(args.fixture, args.cubes * 12, namespace)
    with OperationRSS() as rss:
        start = time.perf_counter()
        if args.scope == "phase":
            if native is None:
                words = rows.view("<u8").reshape(-1, 4)[:, :3]
                order = np.lexsort((words[:, 2], words[:, 1], words[:, 0]))
                rows = rows[order]
                del words, order
                counts = tuple(map(int, namespace["_bjt_edge_counts"](rows)))
            else:
                counts = native.sort_count(rows)
            metrics = dict(zip(METRICS, counts))
        else:
            kwargs = {"backend": args.backend} if "_bjt_native_edge_backend" in namespace else {}
            metrics = namespace["_bjt_verify_edges"](args.fixture, args.cubes * 12, threading.Event(), args.budget, **kwargs)
        seconds = time.perf_counter() - start
    raw_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    signature = {"metrics": metrics}
    if args.scope == "phase":
        signature["sorted_coordinate_keys_sha256"] = key_sha(rows)
    result = {"scope": args.scope, "geometry": args.geometry, "backend": args.backend,
              "source_sha256": file_sha(args.source), "seconds": seconds,
              "rss_before_bytes": rss.before, "operation_sampled_peak_rss_bytes": rss.peak,
              "rss_sample_interval_seconds": .002, "rss_samples": rss.samples,
              "process_lifetime_peak_rss_bytes": int(raw_peak if sys.platform == "darwin" else raw_peak * 1024),
              "signature": signature, "jit_warmup": "Both actual Numba edge kernels, matching record signatures, before operation timing/RSS sampling",
              "native": {"path": str(native.path), "sha256": file_sha(native.path)} if native else None}
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "suite/sources/260914_BJT_model_to_lattice_STL_all_in_one.py")
    parser.add_argument("--cubes", type=int, default=166_666)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--budget", type=int, default=256 * 1024**2)
    parser.add_argument("--geometries", nargs="+", choices=("separated", "edge-touching"), default=["separated", "edge-touching"])
    parser.add_argument("--scopes", nargs="+", choices=("phase", "full"), default=["phase", "full"])
    parser.add_argument("--output", type=Path, default=ROOT / "verification/bjt-rust/benchmark.json")
    parser.add_argument("--native-wrapper", type=Path, default=ROOT / "suite/bjt_mesh_native.py")
    parser.add_argument("--native-library", type=Path, default=ROOT / "suite/native/libbjt_mesh_native.so")
    parser.add_argument("--snapshot-dir", type=Path, help="retain a fresh immutable-by-convention source/binary snapshot")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend", choices=("python", "rust"), help=argparse.SUPPRESS)
    parser.add_argument("--scope", choices=("phase", "full"), help=argparse.SUPPRESS)
    parser.add_argument("--geometry", choices=("separated", "edge-touching"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cubes < 2 or args.cubes % 2 or args.cubes * 12 > 0xFFFFFFFF or args.repeat < 1 or args.budget < 1:
        parser.error("cubes must be positive/even with a uint32 STL count; repeat/budget must be positive")
    args.source = args.source.resolve()
    if args.worker:
        worker(args)
        return
    if args.baseline_source is None:
        parser.error("--baseline-source must identify the current optimized pre-Rust source")
    runs, fixtures = [], {}
    with tempfile.TemporaryDirectory(prefix="bjt_rust_benchmark_", dir=args.work_dir) as directory:
        snapshot = args.snapshot_dir.resolve() if args.snapshot_dir else Path(directory) / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=False)
        original_sources = {"baseline": str(args.baseline_source.resolve()), "candidate": str(args.source)}
        artifacts = {}
        copies = {"baseline/source.py": args.baseline_source, "candidate/source.py": args.source,
                  "candidate/bjt_mesh_native.py": args.native_wrapper,
                  "candidate/native/" + args.native_library.name: args.native_library}
        metadata = args.native_library.parent / "bjt_mesh_native.build.json"
        if metadata.is_file():
            copies["candidate/native/bjt_mesh_native.build.json"] = metadata
        # Read and copy every executable input before launching ANY worker.
        # Later repository edits/rebuilds cannot change these loaded copies.
        for relative, original in copies.items():
            data = original.read_bytes()
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            artifacts[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "original": str(original.resolve())}
        baseline_source = snapshot / "baseline/source.py"
        candidate_source = snapshot / "candidate/source.py"
        for geometry in args.geometries:
            path = Path(directory) / f"{geometry}.stl"
            fixtures[geometry] = fixture(path, args.cubes, geometry)
            for scope in args.scopes:
                for repeat in range(1, args.repeat + 1):
                    choices = ("python", "rust") if repeat % 2 else ("rust", "python")
                    for backend in choices:
                        source = baseline_source if backend == "python" else candidate_source
                        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", str(source),
                                   "--fixture", str(path), "--cubes", str(args.cubes), "--geometry", geometry,
                                   "--scope", scope, "--backend", backend, "--budget", str(args.budget)]
                        completed = subprocess.run(command, text=True, capture_output=True, env={**os.environ, "BJT_MESH_BACKEND": backend})
                        if completed.returncode:
                            raise RuntimeError(f"{geometry}/{scope}/{backend} failed:\n{completed.stdout}\n{completed.stderr}")
                        result = json.loads(completed.stdout)
                        result.update(repetition=repeat, sequence=len(runs)+1)
                        runs.append(result)
                        print(f"{geometry}/{scope} {repeat}/{args.repeat} {backend}: {result['seconds']:.3f}s, operation RSS {result['operation_sampled_peak_rss_bytes']/1024**2:.1f} MiB", file=sys.stderr, flush=True)
    summary = {}
    for geometry in args.geometries:
        for scope in args.scopes:
            selected = [run for run in runs if run["geometry"] == geometry and run["scope"] == scope]
            expected = selected[0]["signature"]
            if any(run["signature"] != expected for run in selected):
                raise AssertionError(f"baseline/native parity failure: {geometry}/{scope}")
            per_backend = {}
            for backend in ("python", "rust"):
                samples = [run for run in selected if run["backend"] == backend]
                per_backend[backend] = {key + "_median": statistics.median(run[key] for run in samples)
                    for key in ("seconds", "rss_before_bytes", "operation_sampled_peak_rss_bytes", "process_lifetime_peak_rss_bytes")}
            summary[f"{geometry}/{scope}"] = {"parity_passed": True, "signature": expected, **per_backend}
    evidence = {"configuration": {"cubes": args.cubes, "triangles": args.cubes*12, "repeat": args.repeat,
                   "budget_bytes": args.budget, "baseline_source": original_sources["baseline"], "source": original_sources["candidate"]},
                "frozen_artifacts": artifacts, "snapshot_directory": str(snapshot),
                "environment": {"python": sys.version, **{name: importlib.metadata.version(name) for name in ("numpy", "numba", "llvmlite", "psutil")}},
                "measurement": "Fresh sequential workers with alternating backend order. Fixtures built before launch, actual Numba kernels warmed outside timer. Lifetime RSS includes setup/JIT. Operation RSS is sampled every 2ms and may miss short spikes. Phase includes sort+count; full includes extraction/disk buckets/sort/count/cleanup. Neither measures GUI/whole application peak.",
                "fixtures": fixtures, "summary": summary, "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
