#!/usr/bin/env bash
# Makes this clone or worktree ready for scripts/check.sh (the pre-push hook) and for one test file at a time:
#   .venv/bin/python -m pytest -q -n 2 backend/tests/test_x.py
# Run it once per clone or worktree, and again after a pull: it does only what is missing or stale, so a run with
# nothing to do takes about a second and prints one line. It needs no root and runs no tests.
#   scripts/dev_setup.sh             the pre-push hook on; .venv with the images' Python, backend/requirements-dev.txt
#                                    and uv; node_modules (npm ci)
#   scripts/dev_setup.sh --rust      also the Rust toolchain check.sh's cargo fmt uses (rustup, in your home); on by
#                                    itself when your branch touches a Rust crate
#   scripts/dev_setup.sh --browser   for the browser scripts: Google Chrome when it is on PATH, else Playwright's Chromium
#                                    (pytest never starts a browser)
# check.sh puts .venv/bin first on its PATH by itself. Anything else: .venv/bin/python, or `source .venv/bin/activate`.
set -euo pipefail
cd "$(dirname "$0")/.."

RUST=0 BROWSER=0
for arg in "$@"; do
  case $arg in
    --rust) RUST=1 ;;
    --browser) BROWSER=1 ;;
    -h | --help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "usage: scripts/dev_setup.sh [--rust] [--browser]" >&2; exit 2 ;;
  esac
done

T0=$SECONDS
done_list=()
say() { echo "dev_setup: $*"; }
fail() { echo "dev_setup: $*" >&2; exit 1; }
# A stamp is the content hash of what a step installed from, so a changed lock file redoes the step.
stamp_of() { printf '%s\n' "$@"; }

# The Python and uv versions the images use (Dockerfile), so a local check sees what CI sees.
PY_WANT=$(sed -n 's/^ARG PYTHON_IMAGE=python:\([0-9]*\.[0-9]*\).*/\1/p' Dockerfile)
UV_WANT=$(sed -n 's|^FROM ghcr.io/astral-sh/uv:\([0-9.]*\)@.*|\1|p' Dockerfile)
[ -n "$PY_WANT" ] && [ -n "$UV_WANT" ] || fail "could not read the Python or uv version from the Dockerfile"

# 1. The pre-push hook (every worktree of a clone shares the setting).
if [ "$(git config --get core.hooksPath 2>/dev/null || true)" != .githooks ]; then
  git config core.hooksPath .githooks
  done_list+=("pre-push hook on")
fi

# 2. .venv: Python $PY_WANT, the dev requirements, and uv (lint.sh and check_types.sh run uvx).
py_is_wanted() { "$1" -c "import sys; sys.exit(0 if '%d.%d' % sys.version_info[:2] == '$PY_WANT' else 1)" 2>/dev/null; }
find_python() {
  local candidate path
  for candidate in "python$PY_WANT" python3; do
    path=$(command -v "$candidate" 2>/dev/null) || continue
    if py_is_wanted "$path"; then echo "$path"; return 0; fi
  done
  # uv can fetch a Python into your home when none is installed.
  if command -v uv >/dev/null 2>&1; then
    uv python find "$PY_WANT" 2>/dev/null && return 0
    uv python install --quiet "$PY_WANT" >&2 && uv python find "$PY_WANT" 2>/dev/null && return 0
  fi
  return 1
}
py_stamp=$(stamp_of "python $PY_WANT" "uv $UV_WANT" "requirements $(git hash-object backend/requirements-dev.txt)")
if ! { [ -f .venv/.dev-setup ] && [ "$(cat .venv/.dev-setup)" = "$py_stamp" ] && py_is_wanted .venv/bin/python; }; then
  if ! py_is_wanted .venv/bin/python; then
    # Missing, or made by another Python (an image update moves the interpreter a venv points at): made again.
    base=$(find_python) || fail "needs Python $PY_WANT on PATH (python$PY_WANT), or uv to fetch it: https://docs.astral.sh/uv/"
    "$base" -m venv --clear .venv
  fi
  if [ "$(.venv/bin/uv --version 2>/dev/null | awk '{print $2}')" != "$UV_WANT" ]; then
    .venv/bin/python -m pip install --quiet --disable-pip-version-check "uv==$UV_WANT"
  fi
  .venv/bin/uv pip install --quiet --python .venv/bin/python -r backend/requirements-dev.txt
  printf '%s\n' "$py_stamp" > .venv/.dev-setup
  done_list+=(".venv ($(.venv/bin/python -c 'import platform; print(platform.python_version())'))")
fi

# 3. node_modules, from package-lock.json (Biome, tsc, esbuild and Playwright's scripts).
command -v node >/dev/null 2>&1 || fail "needs Node 22 or later on PATH"
node_major=$(node -p 'process.versions.node.split(".")[0]')
[ "$node_major" -ge 22 ] || fail "needs Node 22 or later; this is $(node --version)"
node_stamp=$(stamp_of "node $node_major" "lock $(git hash-object package-lock.json)")
if ! { [ -f node_modules/.dev-setup ] && [ "$(cat node_modules/.dev-setup)" = "$node_stamp" ]; }; then
  npm ci --no-audit --no-fund --update-notifier=false --prefer-offline --loglevel=error >/dev/null
  printf '%s\n' "$node_stamp" > node_modules/.dev-setup
  done_list+=("node_modules")
fi

# 4. Rust, for check.sh's cargo fmt: when asked, or when this branch touches a crate scripts/rust_checks.sh formats.
if [ "$RUST" = 0 ]; then
  crates=$(sed -n 's/^CRATES="\(.*\)"$/\1/p' scripts/rust_checks.sh)
  base=""
  if git rev-parse --verify --quiet 'origin/main^{commit}' >/dev/null; then base=$(git merge-base origin/main HEAD 2>/dev/null || true); fi
  if [ -n "$base" ]; then
    changed=$(git diff --name-only "$base" --; git ls-files --others --exclude-standard)
    for crate in $crates; do
      if grep -q "^$crate/" <<< "$changed"; then RUST=1; break; fi
    done
  fi
fi
if [ "$RUST" = 1 ]; then
  # shellcheck disable=SC2016  # the sed pattern matches the literal ${RUST_TOOLCHAIN:-...} in rust_checks.sh
  toolchain=$(sed -n 's/^TOOLCHAIN="${RUST_TOOLCHAIN:-\(.*\)}"$/\1/p' scripts/rust_checks.sh)
  PATH="$PATH:${CARGO_HOME:-$HOME/.cargo}/bin"
  if ! command -v rustup >/dev/null 2>&1 && ! command -v cargo >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || fail "needs curl to install Rust"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
      | sh -s -- -y -q --no-modify-path --profile minimal --default-toolchain "$toolchain" --component rustfmt >/dev/null
    done_list+=("Rust $toolchain (rustup, in ${CARGO_HOME:-$HOME/.cargo})")
  elif command -v rustup >/dev/null 2>&1; then
    if ! rustup component list --installed --toolchain "$toolchain" 2>/dev/null | grep -q '^rustfmt'; then
      rustup toolchain install "$toolchain" --profile minimal --component rustfmt >/dev/null 2>&1 \
        || fail "rustup could not install Rust $toolchain with rustfmt"
      done_list+=("Rust $toolchain")
    fi
  elif ! cargo fmt --version >/dev/null 2>&1; then
    fail "cargo is here without rustfmt and without rustup; install rustup (https://rustup.rs) or rustfmt"
  fi
fi

# 5. The browser scripts run on Google Chrome when it is on PATH (Tico computers have it), so Playwright's Chromium is
# downloaded only when asked and there is none. The system libraries it needs are the image's (or your OS's).
if [ "$BROWSER" = 1 ]; then
  chrome=$(command -v google-chrome || command -v google-chrome-stable || true)
  if [ -n "$chrome" ]; then
    say "browser scripts use Google Chrome ($chrome), Chromium skipped"
  else
    out=$(npx --no-install playwright install chromium 2>&1) || { printf '%s\n' "$out" >&2; fail "Playwright could not install Chromium"; }
    if [ -n "$out" ]; then done_list+=("Chromium"); fi
  fi
fi

if [ "${#done_list[@]}" -eq 0 ]; then
  say "ready, nothing to do ($((SECONDS - T0))s)"
else
  joined=$(printf '%s, ' "${done_list[@]}")
  say "ready: ${joined%, } ($((SECONDS - T0))s)"
fi
