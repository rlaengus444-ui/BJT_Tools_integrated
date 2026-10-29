#!/usr/bin/env python3
"""Frozen-source q-to-STL benchmark; unchanged Numba triangulation is excluded."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "suite/tests"))
from bjt_kernel_support import load_definitions, load_embedded_definitions
from benchmark_bjt_rust_edges import OperationRSS, file_sha, CORNERS, FACES


def load_writer(source, backend):
    core = load_embedded_definitions(source, "_RECON_CORE_B64", ["CancelledError", "check_cancel", "BinarySTLWriter"],
                                      {"np": np, "Path": Path, "os": os, "struct": struct})
    names = ["_AIO_FAST_EXACT_RECORD_DTYPE", "_aio_fast_writer_check_limit", "_aio_fast_write_triangle_soup"]
    if backend == "rust":
        names += ["_bjt_native_edge_backend", "_bjt_write_grid_triangle_soup"]
    namespace = load_definitions(source, names, {"np": np, "Path": Path, "__file__": str(source),
                                 "recon_core": SimpleNamespace(check_cancel=core["check_cancel"])})
    if backend == "rust":
        native = namespace["_bjt_native_edge_backend"]("rust")
        if not native.can_pack_grid:
            raise RuntimeError("frozen library lacks triangle packing")
        return core["BinarySTLWriter"], namespace["_bjt_write_grid_triangle_soup"], native
    # Compile the actual pre-Rust class's xyz mapping/write statements, rather
    # than copying a competing numerical implementation into the benchmark.
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_BJT57GridRectWriter")
    close = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "close")
    loop = next(n for n in ast.walk(close) if isinstance(n, ast.While) and any(
        isinstance(x, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "xyz" for t in x.targets) for x in n.body))
    start = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "xyz" for t in n.targets))
    write = next(n.value for n in loop.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Name) and n.value.func.id == "_aio_fast_write_triangle_soup")
    body = []
    for n in loop.body[start:]:
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            body.append(n)
        else:
            break
    body.append(ast.Return(value=write))

    class ReplaceSelf(ast.NodeTransformer):
        def visit_Attribute(self, node):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                return ast.copy_location(ast.Name(id={"coord": "coord", "limit": "max_triangles", "cancel": "cancel_event"}[node.attr], ctx=ast.Load()), node)
            return self.generic_visit(node)

    function = ast.FunctionDef(name="baseline_grid_write", args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg=n) for n in ("writer", "q", "coord", "max_triangles", "cancel_event")],
        kwonlyargs=[], kw_defaults=[], defaults=[ast.Constant(None), ast.Constant(None)]), body=body, decorator_list=[])
    module = ast.fix_missing_locations(ReplaceSelf().visit(ast.Module(body=[function], type_ignores=[])))
    exec(compile(module, str(source), "exec"), namespace)
    return core["BinarySTLWriter"], namespace["baseline_grid_write"], None


def worker(args):
    writer_class, write, native = load_writer(args.source, args.backend)
    q = np.load(args.fixture, allow_pickle=False)
    coord = SimpleNamespace(xsp=.0846, ysp=.0846, zsp=.03, xoff=-150., yoff=-120.)
    with tempfile.TemporaryDirectory(prefix="bjt_pack_worker_") as directory:
        path = Path(directory) / "output.stl"
        if args.scope == "writer":
            writer = writer_class(path, header="BJT exact q packing benchmark")
        else:
            writer = SimpleNamespace(f=open(os.devnull, "wb"), tri_count=0, _flush_buffer=lambda: None)
        with OperationRSS() as rss:
            start = __import__("time").perf_counter()
            try:
                count = write(writer, q, coord)
                if args.scope == "writer":
                    writer.close(True)
                else:
                    writer.f.close()
            finally:
                if args.scope == "writer":
                    writer.close(False)
                elif not writer.f.closed:
                    writer.f.close()
            seconds = __import__("time").perf_counter() - start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        signature = {"triangles": count, "writer_count": writer.tri_count}
        if args.scope == "writer":
            signature.update(bytes=path.stat().st_size, sha256=file_sha(path))
        print(json.dumps({"backend": args.backend, "scope": args.scope, "seconds": seconds,
            "source_sha256": file_sha(args.source), "rss_before_bytes": rss.before,
            "operation_sampled_peak_rss_bytes": rss.peak, "process_lifetime_peak_rss_bytes": int(peak if sys.platform == "darwin" else peak*1024),
            "rss_samples": rss.samples, "rss_sample_interval_seconds": .002,
            "input_q_bytes": q.nbytes, "signature": signature,
            "native": {"path": str(native.path), "sha256": file_sha(native.path)} if native else None}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "suite/sources/260914_BJT_model_to_lattice_STL_all_in_one.py")
    parser.add_argument("--native-wrapper", type=Path, default=ROOT / "suite/bjt_mesh_native.py")
    parser.add_argument("--native-library", type=Path, default=ROOT / "suite/native/libbjt_mesh_native.so")
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--triangles", type=int, default=2_000_000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "verification/bjt-rust/packing.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend", choices=("python", "rust"), help=argparse.SUPPRESS)
    parser.add_argument("--scope", choices=("pack-phase", "writer"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.baseline_source is None or args.triangles < 1 or args.repeat < 1:
        parser.error("select --baseline-source and positive triangles/repeat")
    runs = []
    with tempfile.TemporaryDirectory(prefix="bjt_pack_benchmark_") as directory:
        root = Path(directory)
        snapshot = args.snapshot_dir.resolve() if args.snapshot_dir else root / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=False)
        copies = {"baseline/source.py": args.baseline_source, "candidate/source.py": args.source,
                  "candidate/bjt_mesh_native.py": args.native_wrapper,
                  "candidate/native/" + args.native_library.name: args.native_library}
        metadata = args.native_library.parent / "bjt_mesh_native.build.json"
        if metadata.is_file():
            copies["candidate/native/bjt_mesh_native.build.json"] = metadata
        artifacts = {}
        for relative, original in copies.items():
            data = original.read_bytes()
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            artifacts[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "original": str(original.resolve())}
        fixture = root / "q.npy"
        q = np.lib.format.open_memmap(fixture, mode="w+", dtype=np.int64, shape=(args.triangles, 3, 3))
        triangle = (2 * CORNERS[FACES]).astype(np.int64)
        for begin in range(0, args.triangles, 100_000):
            ids = np.arange(begin, min(args.triangles, begin+100_000), dtype=np.int64)
            cubes = ids // 12
            offsets = np.column_stack(((cubes % 128)*6, ((cubes//128) % 128)*6, (cubes//16384)*6))
            q[begin:begin+len(ids)] = triangle[ids % 12] + offsets[:, None, :]
        q.flush()
        del q
        fixture_signature = {"sha256": file_sha(fixture), "bytes": fixture.stat().st_size, "geometry": "ordered tiled cube triangles in doubled integer grid coordinates"}
        for scope in ("pack-phase", "writer"):
            for repeat in range(1, args.repeat+1):
                choices = ("python", "rust") if repeat % 2 else ("rust", "python")
                for backend in choices:
                    source = snapshot / ("baseline/source.py" if backend == "python" else "candidate/source.py")
                    command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", str(source),
                               "--fixture", str(fixture), "--scope", scope, "--backend", backend]
                    result = subprocess.run(command, text=True, capture_output=True, env={**os.environ, "BJT_MESH_BACKEND": backend})
                    if result.returncode:
                        raise RuntimeError(f"{scope}/{backend} failed:\n{result.stdout}\n{result.stderr}")
                    data = json.loads(result.stdout)
                    data.update(repetition=repeat, sequence=len(runs)+1)
                    runs.append(data)
                    print(f"{scope} {repeat}/{args.repeat} {backend}: {data['seconds']:.3f}s, operation RSS {data['operation_sampled_peak_rss_bytes']/1024**2:.1f} MiB", file=sys.stderr, flush=True)
        summary = {}
        for scope in ("pack-phase", "writer"):
            selected = [run for run in runs if run["scope"] == scope]
            signature = selected[0]["signature"]
            if any(run["signature"] != signature for run in selected):
                raise AssertionError(f"packing parity failure: {scope}")
            summary[scope] = {"parity_passed": True, "signature": signature}
            for backend in ("python", "rust"):
                samples = [run for run in selected if run["backend"] == backend]
                summary[scope][backend] = {key+"_median": statistics.median(run[key] for run in samples)
                    for key in ("seconds", "rss_before_bytes", "operation_sampled_peak_rss_bytes", "process_lifetime_peak_rss_bytes")}
        evidence = {"configuration": {"triangles": args.triangles, "repeat": args.repeat}, "frozen_artifacts": artifacts,
            "snapshot_directory": str(snapshot), "fixture": fixture_signature,
            "measurement": "Fresh alternating workers; all executable source/wrapper/binary inputs copied and hashed before launch. Existing q loaded before operation. No triangulation/JIT is timed. Pack phase writes records to os.devnull; writer adds real BinarySTLWriter close/fsync. RSS samples every 2ms may miss short spikes. Lifetime RSS includes Python/NumPy/Numba imports, source loading and q. This is not whole-app peak.",
            "summary": summary, "runs": runs}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True)+"\n")
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
