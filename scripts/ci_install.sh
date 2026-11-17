#!/usr/bin/env bash
# Each attempt has a five-minute bound; kill only the process group timeout starts.
set -euo pipefail
if [ "${1:-}" != --once ]; then
  for attempt in 1 2; do
    echo "Install attempt $attempt/2 (maximum 300 seconds)"
    if timeout --kill-after=10s 300s bash "$0" --once "$@"; then exit 0; fi
  done
  exit 1
fi
shift
suite=${1:?suite required}
# A browser suite's system packages install in the background while pip, npm and the UI build run (about 25 seconds of
# dpkg that used to run after them); the job waits for them before it installs the browsers.
system_pid=''
case $suite in
  browser*)
    cache="$RUNNER_TEMP/playwright-system"
    system_log="$RUNNER_TEMP/playwright-system.log"
    mkdir -p "$cache/archives/partial" "$cache/lists/partial"
    # The checked-in list comes from the locked Playwright package. A version change
    # must regenerate it before this job can install a different dependency set.
    mapfile -t packages < <(python3 - <<'PACKAGES'
import json
from pathlib import Path
manifest = json.loads(Path('scripts/playwright-system-packages.json').read_text())
lock = json.loads(Path('package-lock.json').read_text())
assert manifest['playwright'] == lock['packages']['node_modules/playwright-core']['version']
assert manifest['platform'] == 'ubuntu24.04-x64'
assert all(name and all(c.isalnum() or c in '.+-' for c in name) for name in manifest['packages'])
print('\n'.join(manifest['packages']))
PACKAGES
)
    [ "${#packages[@]}" -gt 0 ]
    # --force-unsafe-io: dpkg skips its per-file fsync; the runner is thrown away after the job.
    apt_options=(-o "Dir::Cache::archives=$cache/archives" -o "Dir::State::lists=$cache/lists"
                 -o APT::Keep-Downloaded-Packages=true -o Acquire::Retries=1
                 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
                 -o Dpkg::Options::=--force-unsafe-io)
    (
      set -euo pipefail
      if [ ! -f "$cache/complete" ]; then
        # Only a cold cache contacts apt; exact keys include the runner image and package lock.
        sudo apt-get "${apt_options[@]}" update
        sudo apt-get "${apt_options[@]}" --download-only install -y --no-install-recommends "${packages[@]}"
        touch "$cache/complete"
      fi
      # Cached indexes and signed debs resolve the exact same versions without any downloads.
      sudo apt-get "${apt_options[@]}" --no-download install -y --no-install-recommends "${packages[@]}"
    ) > "$system_log" 2>&1 &
    system_pid=$!
    # A failed attempt waits for dpkg to finish, so the retry never meets a held dpkg lock.
    trap 'wait "$system_pid" 2>/dev/null || true' EXIT
    ;;
esac
# A venv restored from the cache (pr-checks.yml, keyed on the exact Python, the arch, the lock and this script) is kept;
# one that cannot start is rebuilt. pip still installs the lock either way: on a whole restored venv every pin is already
# satisfied (a few seconds, no downloads), and a missing or wrong package is installed, so a broken lock still fails.
"$RUNNER_TEMP/venv/bin/python" -c '' 2>/dev/null || python3 -m venv --clear "$RUNNER_TEMP/venv"
"$RUNNER_TEMP/venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 -r backend/requirements-dev.txt
# Remote commands can run the same pinned Ruff and basedpyright checks as the PR job.
if [ "${GITHUB_WORKFLOW:-}" = "Remote run" ]; then
  "$RUNNER_TEMP/venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 uv==0.13.0
  case ${SETUP:-} in
    rust | all) rustup toolchain install 1.92 --profile minimal --component rustfmt ;;
  esac
fi
# The types job runs basedpyright on the venv alone.
[ "$suite" != types ] || exit 0
# Every suite serves the UI as scripts/build-ui.cjs builds it to build/ui with the lock's esbuild (backend/ui_build.py
# refuses a missing or stale build).
npm ci --fetch-retries=1 --no-audit --no-fund --prefer-offline
npm run --silent build:ui
# Python tests do not use Playwright. The browser VM also needs Python for its synthetic server.
[ -n "$system_pid" ] || exit 0
if ! wait "$system_pid"; then
  cat "$system_log"
  exit 1
fi
cat "$system_log"
sudo chown -R "$(id -u):$(id -g)" "$cache"
npx --no-install playwright install chromium webkit
