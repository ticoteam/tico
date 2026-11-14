#!/usr/bin/env bash
# Writes the API types (ui/types/api.d.ts, generated from backend/openapi_v2.py and not committed), then checks that tsc
# (checkJs, ui/tsconfig.json) finds nothing in the UI's .js files beyond their baselines in ui/baselines/tsc/ (.ts files are strict: check_ui_strict.py).
# tsc only type-checks (noEmit): esbuild compiles the .ts files (npm run build:ui, scripts/build-ui.cjs) without
# checking types, so this script is the type gate.
# ui/observability-vendors.ts is left out: it is the bundler entry for the npm packages posthog-js and @sentry/browser,
# so its findings would depend on whether node_modules is installed, and CI runs this before npm ci. Pass --base
# origin/main on a pull request so a baseline that grew fails too (scripts/ui_baseline.py).
# ui/sw.ts, the service worker, is left out of ui/tsconfig.json too: it is its own program (ui/tsconfig.worker.json, the
# WebWorker lib), which must report nothing (check_ui_strict.py --worker-only, run here as well).
# The page parts (ui/parts.json) are checked with the same TypeScript: no code outside a part uses its names other than
# through its entries (scripts/check_ui_parts.cjs).
#   scripts/check_ui_types.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/gen_ui_types.py
python3 scripts/ui_baseline.py tsc --check "$@"
python3 scripts/check_ui_strict.py --worker-only
npx --yes -p typescript@5.9.3 -c 'node scripts/check_ui_parts.cjs'
echo "ui types: ok"
