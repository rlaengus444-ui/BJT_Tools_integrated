# Optional BJT native kernels

The std-only Rust crate provides optional edge sort/count (ABI 1) and optional grid-triangle record packing (packing ABI 1). Existing Python code creates the triangles; Rust does not triangulate or change their order. No third-party Rust crates are required.

Build locally from the repository root with an installed Rust toolchain:

```sh
python suite/native/build_bjt_mesh_native.py --cargo /path/to/cargo --rustc /path/to/rustc
```

The script uses an offline release build, `panic=unwind`, generic CPU, and writes the host library plus a build metadata JSON beside this README. Use `--target` for an installed target and `--output-dir` to choose a destination. `RUSTUP_HOME` and `CARGO_HOME` may point to an isolated toolchain. The build output, generated libraries and metadata are ignored by Git; the crate sources remain tracked.

`BJT_MESH_BACKEND=auto` selects the optional library when its platform and ABI are supported, otherwise the Python path. `python` forces the Python path; `rust` requires native edges and, when grid triangles are emitted, native packing. In auto mode, missing optional packing support retains the Python packing path. Native execution errors are surfaced rather than silently retried. The wrapper borrows arrays during calls: callers must prevent concurrent access or resizing through any alias while ctypes releases the GIL.

The build recipe stages the optional library matching the build host when it is present; absent libraries preserve Python fallback. A Windows DLL requires its own native runtime and EXE validation; no Windows execution or EXE build is claimed here.

A Windows x86-64 GNU DLL has been cross-compiled from the same Rust crate and statically inspected. It was not executed on Windows or tested in an EXE; see [build evidence](../../verification/bjt-rust/build-windows.json).
