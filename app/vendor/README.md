# GLib 0.18 security backport

The Linux Tauri 2 / GTK 0.18 dependency graph requires GLib `^0.18`. The published
`glib 0.18.5` crate has an unsound variadic output argument in `VariantStrIter`.
The fixed `0.20` series is outside that requirement. The official `0.18` branch
at `42b9caf98e03ded086362d9653ca58fe94dc8658` still contains the vulnerable code.

`glib/` is the complete original MIT-licensed crate from
[`glib-0.18.5.crate`](https://static.crates.io/crates/glib/glib-0.18.5.crate),
SHA-256 `233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5`,
with exactly the two source-line changes from
[upstream PR 1343](https://github.com/gtk-rs/gtk-rs-core/pull/1343):
commit [`b5a4071e439bef2b5eea76c3aa25e5ae84839e34`](https://github.com/gtk-rs/gtk-rs-core/commit/b5a4071e439bef2b5eea76c3aa25e5ae84839e34),
merged as `05dff0ee696f9bcd8617cd48c4b812d046d440cb`.
The output pointer is mutable and passed as `&mut p` to `g_variant_get_child`.
Original metadata, source, tests and [license](glib/LICENSE) are retained.
`glib-provenance.json` records the original package commit and hashes of every
original file plus the patched source hash.

`app/Cargo.toml` patches crates.io GLib to this local source. The version stays
`0.18.5` and the lockfile records a path package; it does not pretend to be
`0.20`. Version-based scanners may still report
[RUSTSEC-2024-0429 / GHSA-wrw7-89jp-8q8g](https://rustsec.org/advisories/RUSTSEC-2024-0429.html).
The advisory gate accepts it for `app/Cargo.lock` and the regression test's lock only, until a dated review
(`scripts/advisories/allowlist.json`); this does not claim that Dependabot will close the alert. Assess the patched source and these tests alongside the
scanner result.

From the repository root, with Rust, Python 3.11+ and native GLib development
headers installed:

```sh
python app/tests/check_glib_backport.py
cargo metadata --manifest-path app/Cargo.toml --offline --locked --format-version 1 --no-deps
cargo tree --manifest-path app/Cargo.toml --offline --locked --target x86_64-unknown-linux-gnu --invert glib
CARGO_BUILD_JOBS=1 CARGO_TARGET_DIR=app/target/glib-backport cargo test --manifest-path app/tests/glib-backport/Cargo.toml --offline --locked --release --test variant_str_iter
```

The standalone regression uses this same GLib source and real native GLib FFI,
without compiling Tauri or needing a display. Optimization matters: the old
immutable-reference write can be discarded by Rust. The five tests exercise
`next`, `nth`, `last`, `next_back`, `nth_back`, mixed ends, Unicode, empty and
overflowing skips, and borrowed-string lifetime. First populate the public
dependency cache if an offline command reports a missing crate.

When a maintained compatible upstream release/commit contains the fix, replace
the local patch with that reviewed source and regenerate the lockfile. When the
desktop graph supports a fixed GLib series, remove the vendor patch and guard.
Retain the regression until the new dependency has passed the same optimized
checks. Never edit the vendored version to satisfy a scanner or silently update
the integrity manifest with unrelated source changes.
