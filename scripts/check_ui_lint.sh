#!/usr/bin/env bash
# Biome lint of the UI's .js and .ts files (biome.json enables only the correctness and suspicious rules; ui/types/ is
# left to tsc because it holds generated and declaration files), counted per file against ui/baselines/biome/ by
# scripts/ui_baseline.py. Biome is a root devDependency: after `npm ci` it runs from node_modules, otherwise the same
# pinned version is fetched with npx. Pass --base origin/main on a pull request so a baseline that grew fails too.
# Takes seconds.
# Two rules are off in biome.json on purpose. noUnusedVariables: each top-level name in a classic script is shared
# through window with other scripts, which a file-by-file reader cannot see, so tsc's noUnusedLocals checks local
# variables instead (ui/tsconfig.json). noRedundantUseStrict: a classic script needs its 'use strict'.
#   scripts/check_ui_lint.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/ui_baseline.py biome --check "$@"
echo "ui lint: ok"
