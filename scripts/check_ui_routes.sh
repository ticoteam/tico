#!/usr/bin/env bash
# Every API path the UI's JavaScript calls must be a route the backend serves. The routes come from importing the app
# and walking app.routes (no server, no network); the calls come from scanning ui/**/*.js and ui/**/*.ts, excluding
# ui/vendor/ and ui/assets/. A call that matches no route is printed as file:line METHOD path and counted per file
# against ui/routes-baseline.json, which only shrinks. Calls whose path cannot be read are skipped and counted:
# python3 scripts/check_ui_routes.py --skipped lists them. Pass --base origin/main on a pull request so a baseline
# that grew there fails too. Needs backend/requirements-dev.txt installed. Takes seconds.
#   scripts/check_ui_routes.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/check_ui_routes.py "$@"
echo "ui routes: ok"
