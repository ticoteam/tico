#!/usr/bin/env bash
# Rust gate for the crates CI builds: rustfmt and clippy with -D warnings. app/ (the Tauri desktop shell, with
# vendored GTK dependencies) is left out and has its own job.
# The toolchain is pinned here, not in rust-toolchain.toml: installs that run from a checkout build runner-rs with
# whatever cargo they have (runner_support/engine.py build), and a pin there would make each of them download it first.
# 1.92.0 is the release images' version (Dockerfile); RUST_TOOLCHAIN overrides it.
# rustc's dead_code is allowed for clippy only (baseline: test-only helpers), so ordinary builds still warn.
# Crates listed in scripts/rust-fmt-pending.txt are reported but do not fail until they are formatted, and the
# list only shrinks: a crate that is clean must leave it.
set -euo pipefail
cd "$(dirname "$0")/.."

CRATES="runner-rs relay-rs tools/release-command tools/trust-list-check"
TOOLCHAIN="${RUST_TOOLCHAIN:-1.92.0}"
rustup toolchain install "$TOOLCHAIN" --profile minimal --component clippy,rustfmt >/dev/null
PENDING=scripts/rust-fmt-pending.txt
log=$(mktemp)
trap 'rm -f "$log"' EXIT
status=0

for crate in $CRATES; do
  rc=0
  (cd "$crate" && cargo +"$TOOLCHAIN" fmt --check) >"$log" 2>&1 || rc=$?
  files=$({ grep '^Diff in' "$log" || true; } | sed -E 's/^Diff in (.*):[0-9]+:$/\1/' | sort -u | wc -l | tr -d ' ')
  if [ "$rc" -ne 0 ] && [ "$files" = 0 ]; then
    echo "fmt: $crate: cargo +$TOOLCHAIN fmt --check failed"; cat "$log"; status=1
  elif grep -qx "$crate" "$PENDING"; then
    if [ "$files" = 0 ]; then
      echo "fmt: $crate is clean; remove it from $PENDING"; status=1
    else
      echo "fmt: $crate: $files file(s) pending (listed in $PENDING)"
    fi
  elif [ "$files" != 0 ]; then
    echo "fmt: $crate: $files file(s) need cargo fmt"; status=1
  else
    echo "fmt: $crate: clean"
  fi

  if (cd "$crate" && cargo +"$TOOLCHAIN" clippy --locked --all-targets -- -D warnings -A dead_code); then
    echo "clippy: $crate: clean"
  else
    echo "clippy: $crate: findings"; status=1
  fi
done

exit "$status"
