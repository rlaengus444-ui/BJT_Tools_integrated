Measured on Linux, Python 3.12.14 and NumPy 2.3.5, using 2,000,000 triangles,
120,000-triangle writer chunks and three repetitions. Each sample runs in a
fresh process; baseline/candidate execution order alternates. The upstream baseline is commit `8a8f057c6d192de082a6a14f12397e1303653ee0`; the local snapshot commit `e16f48fff76b43b68a43d19ea237031d953c2405` carries that source. The baseline source SHA-256 is `893f656d9dfa7bc3dcc4d1c29f21191a4921e1f77d42f404b948e6a733b95e1f`; the final candidate is `727b6db3b46c1f0d13696d93f7ba9ef039facd87c1722fee1613b5fe32a08d72` (535,217 bytes). Exact inputs, output hashes, environment and samples for the final source are in [benchmark-final-2m.json](benchmark-final-2m.json). The earlier [benchmark-2m.json](benchmark-2m.json) is supplementary and used a preliminary candidate.

| Measured operation | Baseline median seconds | Candidate median seconds | Baseline process peak MiB | Candidate process peak MiB |
| --- | ---: | ---: | ---: | ---: |
| Triangle-soup write, contiguous float64 input | 0.2563375 | 0.2366746 | 275.238 | 198.121 |
| Triangle-soup write, strided float64 input | 0.2711739 | 0.2632006 | 275.238 | 198.125 |
| Binary STL stats and hash | 0.5111838 | 0.5719368 | 116.352 | 83.941 |
| Edge keys, lexsort and row gather | 3.7992830 | 3.7519246 | 630.117 | 498.887 |

All baseline/candidate signatures match exactly: both writer STL hashes, the complete stats dictionary excluding the temporary path, and the ordered edge-row hash including direction/contact metadata. Stats were about 11.9% slower while using less peak memory; edge peak memory fell about 20.8% with similar timing. These measurements do not guarantee a speed improvement across workloads; filesystem and cache variation affect samples.

Timers exclude fixture construction and result verification. Writer timing
includes the real embedded `BinarySTLWriter.close(True)` and its `fsync`. The
strided input reverses the vertex axis of the same-sized float64 array, so its
backing storage is 144,000,000 bytes, as for the contiguous input. The stats STL
fixture is generated in bounded chunks before timing. Edge sorting uses the
actual statements extracted from each source's `_bjt_verify_edges`; its
6,000,000 input rows include deterministic random keys and duplicates. This phase
excludes edge extraction, disk bucketing, counting and JIT compilation.

Peak RSS is the process lifetime high-water mark reported by `resource`, including
Python/NumPy, source loading and fixture construction. It is neither a direct
scratch-allocation measurement nor a whole-application peak. The removed full
float32 writer conversion is 36 bytes per triangle when conversion is required;
the removed copied edge keys are 24 bytes per edge row. Other array temporaries,
lexsort workspace, retained input, allocator reuse and disk-bucket skew still
affect the peak. The existing oversized-bucket error remains in effect.

Reproduce from the repository root with a baseline source file or a pre-change
revision:

```sh
python verification/benchmark_bjt_mesh_memory.py \
  --baseline-source /path/to/pre-change-integrated-source.py \
  --source suite/sources/260914_BJT_model_to_lattice_STL_all_in_one.py \
  --triangles 2000000 --repeat 3 \
  --output verification/bjt-memory/benchmark-local.json
```

`--baseline-ref <revision>` can replace `--baseline-source`; its default is
`HEAD`, so select a pre-change revision after committing these changes. Reduce
`--triangles` for a smaller run. No GUI, release build or Windows executable was
measured by this harness.

The focused Python-body test command and its complete result are preserved in
[unit-python.log](unit-python.log):

```sh
python -m unittest discover -s suite/tests -p 'test_bjt_*.py' -v
```

The mesh tests use the real decoded writer class and selected production
definitions. They check exact bytes and scalar-buffer order, contiguous/strided
arrays and Python sequences, chunk normalization, triangle limits, cancellation
and partial-file cleanup. Edge fixtures cover closed/open/reversed/duplicated
faces, three incident faces, signed zero, negative coordinates, nearby distinct
coordinates and voxel edge contacts, with disk/multiple-chunk/error paths.
The final source also passes all 23 focused tests with the edge kernels compiled
by Numba 0.68.1. The complete result is in
[unit-native-numba.log](unit-native-numba.log); actual CPU dispatchers and compiled
nopython signatures are recorded in [native-jit.json](native-jit.json). The helper
loads the actual production bodies and reapplies `njit(cache=False, nogil=True)`;
this validates JIT execution without testing production cache reuse.

```sh
BJT_TEST_NATIVE_NUMBA=1 /workspace/scratch/bjt-verify-venv/bin/python \
  -m unittest discover -s suite/tests -p 'test_bjt_*.py' -v
```

The final provenance-recorded full-pipeline comparison passed all six cases with zero differences: see [parity summary](pipeline/parity_summary.json), [baseline worker report](pipeline/baseline/parity_worker.json), [candidate worker report](pipeline/candidate/parity_worker.json), and their [baseline](pipeline/baseline/worker.log) and [candidate](pipeline/candidate/worker.log) logs. It compares image pixel hashes, STL statistics, file SHA-256, preview callback counts, final progress reaching 100, and report status. It does not compare callback payloads, all progress events, or the full report dictionary. All 11 manifest source hashes and sizes passed [integrity verification](manifest-integrity.json).

Reproduce the pipeline comparison using a fresh output directory and the existing `suite/배포_빌드/helpers/bjt_fixtures.py` generator:

```sh
/path/to/venv/bin/python verification/run_bjt_pipeline_parity.py \
  --baseline-source /path/to/baseline-source.py \
  --candidate-source suite/sources/260914_BJT_model_to_lattice_STL_all_in_one.py \
  --output-dir /path/to/fresh-output-directory
```

The full-pipeline run does not establish GUI or Windows executable behavior. Independent Sol Max review is complete with no blocking or unresolved code findings; see [REVIEW.md](REVIEW.md).
