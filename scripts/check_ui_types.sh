#!/usr/bin/env bash
# Checks that the generated API types match backend/openapi_v2.py and that the type-checked UI files compile.
# The UI has no build step; tsc runs with noEmit, so this only reads files.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/gen_ui_types.py --check
npx --yes -p typescript@5.9.3 tsc -p ui/tsconfig.json
echo "ui types: ok"
