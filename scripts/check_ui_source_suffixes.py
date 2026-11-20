#!/usr/bin/env python3
"""The old web app (ui/) is gone, so this check of it has nothing left to read. .github/workflows/pr-checks.yml still
runs it as a step; delete this file with that step. The web app's own checks: npm run typecheck and npm run lint, in
web/."""

if __name__ == "__main__":
    print("check_ui_source_suffixes: ui/ is gone, nothing to check")
