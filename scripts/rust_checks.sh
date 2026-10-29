#!/usr/bin/env bash
# Rust gate for the crates CI builds: rustfmt and clippy with -D warnings. app/ (the Tauri desktop shell, with
# vendored GTK dependencies) is left out and has its own job.
# Each crate's rust-toolchain.toml pins 1.92.0 (the release images' version); the rustfmt and clippy components
# are added to that toolchain first.
# Crates listed in scripts/rust-fmt-pending.txt are reported but do not fail until they are formatted, and the
# list only shrinks: a crate that is clean must leave it.
set -euo pipefail
cd "$(dirname "$0")/.."

CRATES="runner-rs relay-rs tools/release-command tools/trust-list-check"
PENDING=scripts/rust-fmt-pending.txt
log=$(mktemp)
trap 'rm -f "$log"' EXIT
status=0

for crate in $CRATES; do
  (cd "$crate" && rustup component add clippy rustfmt >/dev/null 2>&1)

  rc=0
  (cd "$crate" && cargo fmt --check) >"$log" 2>&1 || rc=$?
  files=$(grep '^Diff in' "$log" | sed -E 's/^Diff in (.*):[0-9]+:$/\1/' | sort -u | wc -l | tr -d ' ')
  if [ "$rc" -ne 0 ] && [ "$files" = 0 ]; then
    echo "fmt: $crate: cargo fmt --check failed"; cat "$log"; status=1
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

  if (cd "$crate" && cargo clippy --locked --all-targets -- -D warnings); then
    echo "clippy: $crate: clean"
  else
    echo "clippy: $crate: findings"; status=1
  fi
done

exit "$status"
