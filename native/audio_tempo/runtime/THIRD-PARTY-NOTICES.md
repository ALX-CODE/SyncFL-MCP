# Rust standard-library notices

`syncfl-tempo.exe` is built from original SyncFL Rust source with Rust 1.96.0
for `x86_64-pc-windows-msvc`. The Cargo dependency graph contains no third-party
crates. The executable includes code from the Rust standard library.

This directory preserves the toolchain's unmodified
[standard-library copyright inventory](third_party/rust/COPYRIGHT-library.html)
and its [license texts](third_party/rust/licenses). The inventory covers the
standard library's source and build dependencies across supported targets; it
does not assert that every listed component is linked into this executable.

The Rust standard library is generally dual-licensed under MIT or Apache-2.0,
with the exceptions and notices recorded in that inventory. These notices do
not change the MIT license or authorship of SyncFL's original analysis code.

Source: the Rust 1.96.0 toolchain's `share/doc/rust` directory. Retain this file,
the inventory and license texts when redistributing the bundled executable.
