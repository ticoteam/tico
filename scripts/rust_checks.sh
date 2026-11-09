#!/usr/bin/env bash
# Rust gate for the crates CI builds: rustfmt and clippy with -D warnings. app/ (the Tauri desktop shell, with
# vendored GTK dependencies) has its own PR job in pr-checks.yml; recorder/ runs its own in the recorder job.
# The toolchain is pinned here, not in rust-toolchain.toml: installs that run from a checkout build runner-rs with
# whatever cargo they have (runner_support/engine.py build), and a pin there would make each of them download it first.
# 1.92.0 is the release images' version (Dockerfile); RUST_TOOLCHAIN overrides it.
set -euo pipefail
cd "$(dirname "$0")/.."

CRATES="runner-rs relay-rs tools/release-command tools/trust-list-check"
TOOLCHAIN="${RUST_TOOLCHAIN:-1.92.0}"
rustup toolchain install "$TOOLCHAIN" --profile minimal --component clippy,rustfmt >/dev/null
status=0

for crate in $CRATES; do
  if (cd "$crate" && cargo +"$TOOLCHAIN" fmt --check); then
    echo "fmt: $crate: clean"
  else
    echo "fmt: $crate: cargo fmt --check failed (diff above; run cargo fmt in $crate)"; status=1
  fi

  if (cd "$crate" && cargo +"$TOOLCHAIN" clippy --locked --all-targets -- -D warnings); then
    echo "clippy: $crate: clean"
  else
    echo "clippy: $crate: findings"; status=1
  fi
done

exit "$status"
