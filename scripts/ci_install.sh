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
# A browser suite's system packages install in the background while pip, npm and the UI build run; the job waits for
# them before it installs the browsers.
system_pid=''
case $suite in
  browser*)
    # The cache (pr-checks.yml and pr-cache.yml; its key names the runner image, the lock, this script and the package
    # list) holds installed.tar: every file those packages installed, the files their install triggers wrote (the
    # library, font, GLib, MIME and icon caches, alternatives links) and dpkg's records of them. Unpacking it takes about a second, where
    # dpkg took 25 to 45 (measured on hosted ubuntu-24.04 runners, Nov 17; Chromium and WebKit launch on the result).
    cache="$RUNNER_TEMP/playwright-system"
    snapshot="$cache/installed.tar"
    work="$RUNNER_TEMP/playwright-system-work"
    system_log="$RUNNER_TEMP/playwright-system.log"
    mkdir -p "$cache" "$work"
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
    (
      set -euo pipefail
      if [ -f "$snapshot" ]; then
        sudo tar -xpf "$snapshot" -C /
        sudo ldconfig
        exit 0
      fi
      # A cold cache: apt installs them (dpkg without its per-file fsync; the runner is thrown away after the job), and
      # the snapshot is made from what changed.
      dpkg-query -W -f='${Package}:${Architecture} ${Version}\n' | sort > "$work/before"
      sudo apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update
      touch "$work/marker"
      sleep 1
      sudo apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
        -o Dpkg::Options::=--force-unsafe-io install -y --no-install-recommends "${packages[@]}"
      dpkg-query -W -f='${Package}:${Architecture} ${Version}\n' | sort > "$work/after"
      # The packages apt added or upgraded, by their own file lists. Triggers write outside those lists, into the
      # folders below; dpkg keeps each packaged file's mtime, so what changed here is found by ctime. A scan of all of
      # /usr finds the same files but takes about three minutes on a hosted runner.
      {
        comm -13 "$work/before" "$work/after" | cut -d' ' -f1 | xargs -r dpkg -L
        sudo find /etc /var/lib/dpkg /var/lib/ucf /var/cache/fontconfig /usr/lib/x86_64-linux-gnu /usr/share/glib-2.0 \
          /usr/share/mime /usr/share/icons /usr/share/applications /usr/share/info /usr/lib/locale /usr/share/fonts \
          -xdev -cnewer "$work/marker" \( -type f -o -type l -o -type d \) -print 2>/dev/null || true
        # update-alternatives links (a default font, a sound font) can sit anywhere; each changed record names them.
        sudo find /var/lib/dpkg/alternatives -cnewer "$work/marker" -type f -exec awk 'NF == 0 {exit} /^\// {print}' {} \; \
          2>/dev/null || true
      } | sort -u | grep -v -E '^/var/lib/dpkg/(lock|triggers/Lock)' > "$work/candidates" || true
      while IFS= read -r path; do
        if [ -e "$path" ] || [ -L "$path" ]; then printf '%s\n' "$path"; fi
      done < "$work/candidates" > "$work/files"
      sudo tar --no-recursion -cf "$work/installed.tar" -T "$work/files"
      sudo mv "$work/installed.tar" "$snapshot"
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
  "$RUNNER_TEMP/venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 uv==0.13.9
  case ${SETUP:-} in
    rust | all) rustup toolchain install 1.99 --profile minimal --component rustfmt ;;
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
