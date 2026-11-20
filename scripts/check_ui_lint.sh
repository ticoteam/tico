#!/usr/bin/env bash
# The old web app (ui/) is gone, so this check of it has nothing left to read. .github/workflows/pr-checks.yml still
# runs it as a step; delete this file with that step. The web app's own checks: npm run typecheck and npm run lint, in web/.
echo "check_ui_lint: ui/ is gone, nothing to check"
