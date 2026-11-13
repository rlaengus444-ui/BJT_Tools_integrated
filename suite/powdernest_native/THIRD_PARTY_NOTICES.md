# Native library components

The application kernels in `src/lib.rs` are built as the `powdernest-native`
0.1.0 crate. The crate has no third-party Cargo dependencies. Release artifacts
include the Rust standard library and compiler runtime (Rust 1.85.1), distributed
under MIT / Apache-2.0. Source and licenses:

- https://github.com/rust-lang/rust/tree/1.85.1
- https://github.com/rust-lang/rust/blob/1.85.1/LICENSE-MIT
- https://github.com/rust-lang/rust/blob/1.85.1/LICENSE-APACHE

The GNU Windows cross build uses Zig 0.13.0's compiler runtime and mingw-w64 CRT
components. These have their respective LLVM exception, permissive, and public
domain notices. Source and license index:

- https://github.com/ziglang/zig/tree/0.13.0
- https://github.com/ziglang/zig/blob/0.13.0/LICENSE
- https://github.com/ziglang/zig/tree/0.13.0/lib/libc/mingw

The Windows DLL statically links `libgcc_eh.a` from Debian's
`gcc-mingw-w64-x86-64-posix` 12.2.0-14+deb12u1+25.2+b1. This contains the GNU
Windows SEH unwinder. GCC runtime code is GPLv3 with the GCC Runtime Library
Exception 3.1, permitting this independent application under its own terms.
Corresponding GCC runtime source, license and exception:

- https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libgcc;hb=releases/gcc-12.2.0
- https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html
- https://www.gnu.org/licenses/gcc-exception-3.1.html
- https://www.gnu.org/licenses/gpl-3.0.html
- https://deb.debian.org/debian/pool/main/g/gcc-mingw-w64/

No MinGW, Zig, GCC or Rust installation is required on the end-user machine.
The bundled Windows DLL imports Windows/UCRT system libraries only.
