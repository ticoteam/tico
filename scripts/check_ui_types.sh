#!/usr/bin/env bash
# Writes the API types (ui/types/api.d.ts, generated from backend/openapi_v2.py and not committed), then checks that tsc
# (checkJs, ui/tsconfig.json) finds nothing in the UI's .js and .ts files beyond their baselines in ui/baselines/tsc/.
# tsc only type-checks (noEmit): esbuild compiles the .ts files (npm run build:ui, scripts/build-ui.cjs) without
# checking types, so this script is the type gate.
# ui/observability-vendors.js is left out: it is the bundler entry for the npm packages posthog-js and @sentry/browser,
# so its findings would depend on whether node_modules is installed, and CI runs this before npm ci. Pass --base
# origin/main on a pull request so a baseline that grew fails too (scripts/ui_baseline.py).
#   scripts/check_ui_types.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/gen_ui_types.py
python3 scripts/ui_baseline.py tsc --check "$@"
echo "ui types: ok"
