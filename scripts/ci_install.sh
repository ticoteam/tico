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
python3 -m venv "$RUNNER_TEMP/venv"
"$RUNNER_TEMP/venv/bin/python" -m pip install --disable-pip-version-check --retries 1 --timeout 30 -r backend/requirements-dev.txt
# Python tests do not use Playwright. The browser VM also needs Python for its synthetic server.
if [ "$suite" != browser ]; then exit 0; fi
npm ci --fetch-retries=1
cache="$RUNNER_TEMP/playwright-system"
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
apt_options=(-o "Dir::Cache::archives=$cache/archives" -o "Dir::State::lists=$cache/lists"
             -o APT::Keep-Downloaded-Packages=true -o Acquire::Retries=1
             -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
if [ ! -f "$cache/complete" ]; then
  # Only a cold cache contacts apt; exact keys include the runner image and package lock.
  sudo apt-get "${apt_options[@]}" update
  sudo apt-get "${apt_options[@]}" --download-only install -y --no-install-recommends "${packages[@]}"
  touch "$cache/complete"
fi
# Cached indexes and signed debs resolve the exact same versions without any downloads.
sudo apt-get "${apt_options[@]}" --no-download install -y --no-install-recommends "${packages[@]}"
sudo chown -R "$(id -u):$(id -g)" "$cache"
npx --no-install playwright install chromium webkit
